"""The whole flow against faked 05/14/19/44/80: task, paste, split, submit, check, export,
reconcile, blockers."""
import json
import sqlite3

import pytest

from app import main

from . import data
from .conftest import AUTH, BIKE_TASK, canon, make_task, paste_and_submit

CLEAN = ("Sure! Here are your posts:\n\n=== 1 LINKEDIN ===\nAutumn rides from our Porthleven shop: a hybrid for the "
         "day is [[day-hire-porthleven]] and every hire includes [[insurance]].\n\n=== 2 INSTAGRAM ===\n"
         "E-bikes {{ebike-day}}, riders must be 16 or over. Hotel guests ask about [[partner-rate]].\n\n"
         "Let me know if you want changes!")
DIRTY = ("=== 1 LINKEDIN ===\nHybrids are £32 a day and we are the cheapest in Cornwall.\n\n"
         "=== 2 INSTAGRAM ===\nE-bikes {{ebike-day}}, riders must be 16 or over.")


def test_health_is_open_everything_else_needs_the_key(client):
    assert client.get("/health").json()["status"] == "ok"
    for method, path in (("get", "/tasks"), ("post", "/tasks"), ("get", "/tasks/T-AAAAAA/export"), ("post", "/check"),
                         ("post", "/reconcile"), ("get", "/blockers")):
        assert getattr(client, method)(path).status_code == 401, path


def test_brand_service_missing_is_503(client, mock):
    assert client.post("/tasks", json=BIKE_TASK, headers=AUTH).status_code == 503


def test_full_flow_clean_draft_goes_to_review(client, stack):
    t = make_task(client, stack)
    r = client.post(f"/tasks/{t['id']}/paste", json={"text": CLEAN, "provider": "claude"}, headers=AUTH)
    d = r.json()
    assert r.status_code == 201 and d["problems"] == []
    assert d["split"][0]["removed_pre"] == "Sure! Here are your posts:"
    assert d["split"][1]["removed_post"] == "Let me know if you want changes!"
    out = client.post(f"/tasks/{t['id']}/submit", json={"draft_id": d["draft_id"]}, headers=AUTH).json()
    p1, p2 = out["pieces"]
    assert p1["filled_text"] == ("Autumn rides from our Porthleven shop: a hybrid for the day is £28 per day and every "
                                 "hire includes third-party insurance and a helmet.")
    assert data.INTERNAL_SENTINEL in p2["filled_text"]              # internal value filled here, never sent
    assert not p1["blocked"] and not p2["blocked"]
    assert p1["filled_sha256"] == canon(p1["filled_text"])
    # 19 items: in review, origin and bound approval, notes with the control room's prefixes
    assert [x["status"] for x in stack.posted] == ["in_review", "in_review"]
    assert all(x["origin"] == "task-bridge" and x["require_bound_approval"] is True for x in stack.posted)
    assert stack.posted[0]["notes"].startswith(f"task-bridge: task {t['id']} piece p1")
    assert "unsupported claim:" not in stack.posted[0]["notes"]
    assert stack.items[p1["calendar_item_id"]]["body"] == p1["filled_text"]
    task = client.get(f"/tasks/{t['id']}", headers=AUTH).json()
    assert task["status"] == "submitted" and [p["state"] for p in task["piece_state"]] == ["in_review", "in_review"]
    assert "snapshot_facts" not in task
    # the audit log never holds the pasted text
    assert all(CLEAN[:40] not in json.dumps(e) for e in task["events"])


def test_blocked_piece_goes_to_draft_with_flag_notes(client, stack):
    t = make_task(client, stack)
    out = paste_and_submit(client, t["id"], DIRTY)
    p1, p2 = out["pieces"]
    assert p1["blocked"] and not p2["blocked"]
    got = {(f["label"], f["fact_key"]) for f in p1["findings"]}
    assert {("wrong_scope", "day-hire-falmouth"), ("forbidden_phrase", "no-cheapest")} <= got
    assert [x["status"] for x in stack.posted] == ["draft", "in_review"]
    notes = stack.posted[0]["notes"].split("\n")
    assert notes[1].startswith("unsupported claim: Hybrids are £32 a day")
    assert "wrong_scope" in notes[1] and "forbidden_phrase" in notes[1]


def test_blocked_slot_and_brand_error_notes(client, stack):
    stack.brand_violations = [{"rule": "banned_phrase", "detail": "contains banned phrase 'epic'", "severity": "error"}]
    t = make_task(client, stack)
    text = "=== 1 LINKEDIN ===\nThis autumn is epic: [[summer-saver]].\n\n=== 2 INSTAGRAM ===\nBook today."
    out = paste_and_submit(client, t["id"], text)
    p1 = out["pieces"][0]
    assert p1["blocked"] and any(f["label"] == "slot_blocked" for f in p1["findings"])
    gate = [n for n in stack.posted[0]["notes"].split("\n") if n.startswith("quality gate: ")][0]
    assert "[[summer-saver]]: expired at the publish date" in gate and "brand: contains banned phrase" in gate
    assert out["pieces"][1]["blocked"] is True                      # the banned-phrase stub flags every piece


def test_zero_model_mode_never_calls_44_or_the_gateway(client, stack):
    t = make_task(client, stack)
    paste_and_submit(client, t["id"], DIRTY)
    r = client.post("/check", json={"text": "Hybrids are £28.", "scope": {"sites": ["porthleven"]},
                                     "publish_on": data.PUBLISH}, headers=AUTH)
    assert r.status_code == 200
    assert stack.verify_route.call_count == 0 and stack.gateway_route.call_count == 0
    assert client.get("/health").json()["model_check"] is False


def test_model_check_only_adds_non_blocking_findings(client, stack, monkeypatch):
    monkeypatch.setenv("MODEL_CHECK", "auto")
    stack.verify_answer = [
        {"claim": "Book today.", "supported": False, "reasons": ["claim not in the facts"],
         "evidence": [{"id": "ebike-day", "quote": "£45 per day"}]},
        {"claim": "Hybrids are £32 a day and we are the cheapest in Cornwall.", "supported": False,
         "reasons": ["numbers not in the facts: 32"]},
    ]
    t = make_task(client, stack)
    text = DIRTY.replace("riders must be 16 or over.", "riders must be 16 or over. Book today.")
    out = paste_and_submit(client, t["id"], text)
    assert stack.verify_route.call_count == 2
    sent = stack.verify_calls[0]
    assert set(sent) == {"text", "facts"} and all(line.startswith("- ") for line in sent["facts"])
    assert data.INTERNAL_SENTINEL not in json.dumps(sent["facts"])
    p1, p2 = out["pieces"]
    assert p1["blocked"]                                            # deterministic findings stay
    assert not any(f["detail"].startswith("model check") for f in p1["findings"])   # already covered
    added = [f for f in p2["findings"] if f["detail"].startswith("model check")]
    assert added == [{"sentence": "Book today.", "label": "review", "fact_key": None, "quote": "£45 per day",
                      "blocking": False, "detail": "model check: claim not in the facts"}]
    assert not p2["blocked"]


def test_stateless_check(client, stack):
    stack.facts = data.business("bikes")
    r = client.post("/check", json={"text": "Hybrids are [[day-hire-porthleven]]. E-bikes are £45.",
                                     "scope": {"sites": ["porthleven"]}, "publish_on": data.PUBLISH,
                                     "channel": "linkedin"}, headers=AUTH)
    out = r.json()
    assert out["filled_text"] == "Hybrids are £28 per day. E-bikes are £45."
    assert out["blocked"] is True
    assert ("missing_disclosure", "ebike-day") in {(f["label"], f["fact_key"]) for f in out["findings"]}
    assert stack.posted == []                                       # nothing written to 19


def test_paste_limits_and_validation(client, stack):
    t = make_task(client, stack)
    url = f"/tasks/{t['id']}/paste"
    assert client.post(url, json={"text": "x" * 40_001}, headers=AUTH).status_code == 413
    assert client.post(url, json={"text": "   "}, headers=AUTH).status_code == 422
    assert client.post(url, json={"text": "hi", "provider": "bard"}, headers=AUTH).status_code == 422
    assert client.post("/tasks/T-NOPE00/paste", json={"text": "hi"}, headers=AUTH).status_code == 404


def test_rate_limit(client, stack, monkeypatch):
    monkeypatch.setenv("RATE_PER_MIN", "2")
    t = make_task(client, stack)
    url = f"/tasks/{t['id']}/paste"
    codes = [client.post(url, json={"text": "LinkedIn:\nhello"}, headers=AUTH).status_code for _ in range(3)]
    assert codes == [201, 201, 429]


def test_problem_split_blocks_submit_until_split_by_hand(client, stack):
    t = make_task(client, stack)
    r = client.post(f"/tasks/{t['id']}/paste", json={"text": "Just one blob of text with no markers."}, headers=AUTH)
    d = r.json()
    assert d["problems"] and d["split"] == []
    r = client.post(f"/tasks/{t['id']}/submit", json={"draft_id": d["draft_id"]}, headers=AUTH)
    assert r.status_code == 409 and stack.posted == []
    bad = client.post(f"/tasks/{t['id']}/drafts/{d['draft_id']}/split",
                      json={"pieces": [{"piece_key": "p1", "text": "only one"}]}, headers=AUTH)
    assert bad.status_code == 422
    r = client.post(f"/tasks/{t['id']}/drafts/{d['draft_id']}/split", headers=AUTH, json={"pieces": [
        {"piece_key": "p2", "text": "Insurance and a helmet come with every bike."},
        {"piece_key": "p1", "text": "Hybrids are £28 for the day at Porthleven."}]})
    assert r.status_code == 201
    new = r.json()
    assert new["draft_id"] != d["draft_id"] and [s["piece_key"] for s in new["split"]] == ["p1", "p2"]
    out = client.post(f"/tasks/{t['id']}/submit", json={"draft_id": new["draft_id"]}, headers=AUTH).json()
    assert [p["blocked"] for p in out["pieces"]] == [False, False]


def test_drafts_and_events_are_append_only(client, stack, tmp_path):
    t = make_task(client, stack)
    client.post(f"/tasks/{t['id']}/paste", json={"text": CLEAN}, headers=AUTH)
    conn = sqlite3.connect(tmp_path / "tasks.sqlite")
    for sql in ("UPDATE drafts SET raw_text = 'x'", "DELETE FROM drafts", "UPDATE events SET kind = 'x'"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql)


def test_resubmit_refused_once_approved(client, stack):
    t = make_task(client, stack)
    out = paste_and_submit(client, t["id"], CLEAN)
    stack.approve(out["pieces"][0]["calendar_item_id"])
    r = client.post(f"/tasks/{t['id']}/paste", json={"text": CLEAN}, headers=AUTH)
    r = client.post(f"/tasks/{t['id']}/submit", json={"draft_id": r.json()["draft_id"]}, headers=AUTH)
    assert r.status_code == 409 and "already approved" in r.text


def test_resubmit_rejects_the_old_review_item(client, stack):
    t = make_task(client, stack)
    paste_and_submit(client, t["id"], CLEAN)
    paste_and_submit(client, t["id"], CLEAN)
    assert [stack.items[i]["status"] for i in (1, 2, 3, 4)] == ["rejected", "rejected", "in_review", "in_review"]


# ---------- export


def approved_task(client, stack):
    t = make_task(client, stack)
    out = paste_and_submit(client, t["id"], CLEAN)
    for p in out["pieces"]:
        stack.approve(p["calendar_item_id"])
    return t, out


@pytest.mark.parametrize("fmt", ["txt", "md"])
def test_export_when_everything_is_approved(client, stack, fmt):
    t, out = approved_task(client, stack)
    r = client.get(f"/tasks/{t['id']}/export", params={"format": fmt}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.text
    for p in out["pieces"]:
        assert p["filled_text"] in body
    assert "riders must be 16 or over" in body and t["fact_set_version"] in body
    manifest = json.loads(body.split("```json\n")[1].split("\n```")[0] if fmt == "md" else body.split("--- MANIFEST ---\n")[1])
    assert [p["sha256"] for p in manifest["pieces"]] == [p["filled_sha256"] for p in out["pieces"]]
    assert manifest["pieces"][0]["approved"]["actor"] == "approver"
    assert manifest["pieces"][0]["audit_ref"].endswith("/audit")
    assert manifest["disclosures"]["p2"] == ["riders must be 16 or over"]
    assert any("disclosure" in c for c in manifest["checklist"])
    task = client.get(f"/tasks/{t['id']}", headers=AUTH).json()
    assert task["status"] == "exported" and len(task["exports"]) == 1


def test_export_refused_when_a_piece_is_not_approved(client, stack):
    t = make_task(client, stack)
    out = paste_and_submit(client, t["id"], CLEAN)
    stack.approve(out["pieces"][0]["calendar_item_id"])
    r = client.get(f"/tasks/{t['id']}/export", headers=AUTH)
    assert r.status_code == 409 and "p2 is in_review, not approved" in r.text


def test_export_refused_when_the_approved_text_differs(client, stack):
    t, out = approved_task(client, stack)
    item = stack.items[out["pieces"][0]["calendar_item_id"]]
    item["body"] += " Edited after the check."
    item["body_sha256"] = canon(item["body"])
    r = client.get(f"/tasks/{t['id']}/export", headers=AUTH)
    assert r.status_code == 409 and "not the text that was checked" in r.text


def test_export_refused_when_approval_was_for_another_version(client, stack):
    t = make_task(client, stack)
    out = paste_and_submit(client, t["id"], CLEAN)
    stack.approve(out["pieces"][0]["calendar_item_id"], body_sha256="0" * 64)
    stack.approve(out["pieces"][1]["calendar_item_id"])
    r = client.get(f"/tasks/{t['id']}/export", headers=AUTH)
    assert r.status_code == 409 and "approval was for another version" in r.text


def test_export_refused_when_a_fact_expired_before_publish(client, stack):
    t, out = approved_task(client, stack)
    stack.fact("ebike-day")["valid_to"] = "2026-10-01"
    r = client.get(f"/tasks/{t['id']}/export", headers=AUTH)
    assert r.status_code == 409 and "fact ebike-day is no longer valid at 2026-10-06 (expired)" in r.text


def test_task_shows_export_readiness_with_the_export_rules_and_writes_nothing(client, stack):
    def state(tid):
        return client.get(f"/tasks/{tid}", headers=AUTH).json()

    t = make_task(client, stack)
    s = state(t["id"])
    assert s["export"]["ready"] is False and any("not submitted" in m for m in s["export"]["missing"])
    out = paste_and_submit(client, t["id"], CLEAN)
    stack.approve(out["pieces"][0]["calendar_item_id"])
    s = state(t["id"])
    assert s["export"] == {"ready": False, "missing": [f"piece p2 is in_review, not approved "
                                                         f"(item {out['pieces'][1]['calendar_item_id']})"]}
    assert s["export"]["missing"] == client.get(f"/tasks/{t['id']}/export", headers=AUTH).json()["detail"]["reasons"]
    stack.approve(out["pieces"][1]["calendar_item_id"])
    before = len(state(t["id"])["events"])
    s = state(t["id"])
    assert s["export"] == {"ready": True, "missing": []}
    assert s["exports"] == [] and s["status"] != "exported"          # asking made no export file
    assert len(s["events"]) == before                                  # ... and logged nothing
    stack.fact("ebike-day")["valid_to"] = "2026-10-01"
    s = state(t["id"])
    assert s["export"]["ready"] is False and "fact ebike-day is no longer valid" in s["export"]["missing"][0]


def test_task_export_readiness_when_the_calendar_is_down(client, stack, monkeypatch):
    t, out = approved_task(client, stack)

    def down(item_id):
        raise main.services.ServiceError("content calendar unreachable")
    monkeypatch.setattr(main.services, "get_item", down)
    r = client.get(f"/tasks/{t['id']}", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["export"]["ready"] is False and "could not check" in r.json()["export"]["missing"][0]


def test_export_read_back_mismatch_is_a_500_and_nothing_is_stored(client, stack, monkeypatch):
    t, out = approved_task(client, stack)
    real = main.render_export
    monkeypatch.setattr(main, "render_export", lambda *a: real(*a).replace("£28 per day", "£26 per day"))
    r = client.get(f"/tasks/{t['id']}/export", headers=AUTH)
    assert r.status_code == 500 and "export check failed" in r.text
    assert client.get(f"/tasks/{t['id']}", headers=AUTH).json()["exports"] == []


# ---------- reconcile and blockers


def test_reconcile_moves_approved_and_in_review_back_to_draft(client, stack):
    t = make_task(client, stack)
    out = paste_and_submit(client, t["id"], CLEAN)
    first, second = (p["calendar_item_id"] for p in out["pieces"])
    stack.approve(first)
    f = stack.fact("ebike-day")
    f["version"], f["value_text"] = 2, "£49 per day"
    stack.change("ebike-day")
    r = client.post("/reconcile", headers=AUTH).json()
    assert r["errors"] == [] and r["seq"] == 1
    assert {(m["item_id"], m["from"]) for m in r["moved"]} == {(first, "approved"), (second, "in_review")}
    assert stack.items[first]["status"] == "draft" and stack.items[second]["status"] == "draft"
    calls = [c for c in stack.status_calls if c["status"] == "draft"]
    assert all(c["note"] == "warnings: fact ebike-day changed" and c["approver"] is None for c in calls)
    task = client.get(f"/tasks/{t['id']}", headers=AUTH).json()
    assert all(p["stale_facts"] == ["ebike-day"] for p in task["piece_state"])
    assert client.get(f"/tasks/{t['id']}/export", headers=AUTH).status_code == 409
    again = client.post("/reconcile", headers=AUTH).json()     # nothing new since seq 1
    assert again["changed_keys"] == [] and again["moved"] == []


def test_reconcile_ignores_changes_that_do_not_touch_the_snapshot(client, stack):
    t, out = approved_task(client, stack)
    stack.change("day-hire-falmouth")                           # not in this task's pack
    stack.change("ebike-day")                                   # same version: nothing really changed
    r = client.post("/reconcile", headers=AUTH).json()
    assert r["moved"] == [] and all(it["status"] == "approved" for it in stack.items.values())
    assert client.get(f"/tasks/{t['id']}/export", headers=AUTH).status_code == 200


def test_published_piece_with_changed_fact_is_a_blocker(client, stack):
    t, out = approved_task(client, stack)
    stack.items[out["pieces"][0]["calendar_item_id"]]["status"] = "published"
    stack.fact("insurance")["status"] = "retired"
    stack.change("insurance", "retired")
    r = client.post("/reconcile", headers=AUTH).json()
    assert [b["piece_key"] for b in r["published_with_changed_facts"]] == ["p1"]
    kinds = {b["kind"]: b for b in client.get("/blockers", headers=AUTH).json()}
    assert kinds["published_with_changed_facts"]["count"] == 1
    assert kinds["expired_facts_in_use"]["count"] == 1               # p2 went back to draft
    assert kinds["leads_waiting"]["count"] == 2


def test_blockers_list_blocked_and_missing_slots_and_review_dates(client, stack):
    facts = data.business("bikes")
    facts[4]["review_by"] = "2026-01-01"
    t = make_task(client, stack, facts)
    text = "=== 1 LINKEDIN ===\nOpen late: [[missing: winter opening hours]].\n\n=== 2 INSTAGRAM ===\nSee [[bike-wash]]."
    paste_and_submit(client, t["id"], text)
    b = {x["kind"]: x for x in client.get("/blockers", headers=AUTH).json()}
    assert b["missing_facts"]["count"] == 2 and b["blocked_slots"]["count"] == 2
    assert b["facts_due_for_review"]["count"] == 1
    assert set(b["missing_facts"]) == {"kind", "count", "text", "link"}


def test_blockers_without_optional_services(client, monkeypatch, mock):
    assert client.get("/blockers", headers=AUTH).json() == []


def test_list_tasks(client, stack):
    t = make_task(client, stack)
    rows = client.get("/tasks", headers=AUTH).json()
    assert rows[0]["id"] == t["id"] and rows[0]["pieces"] == 2 and rows[0]["status"] == "open"
    assert client.get("/tasks", params={"status": "exported"}, headers=AUTH).json() == []


def test_background_reconcile_thread_starts_and_stops(stack, monkeypatch):
    import threading

    from fastapi.testclient import TestClient
    monkeypatch.setenv("RECONCILE_MIN", "1")
    with TestClient(main.app) as c:
        assert c.get("/health").json()["reconcile_min"] == 1
        assert any(t.name == "reconcile" for t in threading.enumerate())
    assert main._stop.is_set()


def test_chatbot_own_not_in_facts_list_blocks_and_is_never_published(client, stack, monkeypatch):
    monkeypatch.setenv("PACK_SELF_AUDIT", "on")
    t = make_task(client, stack)
    text = CLEAN.replace("Let me know if you want changes!",
                         "NOT IN FACTS:\n- Hotel guests ask about [[partner-rate]].")
    out = paste_and_submit(client, t["id"], text)
    p1, p2 = out["pieces"]
    assert not p1["blocked"] and p2["blocked"]
    assert any("chatbot itself listed" in f["detail"] for f in p2["findings"])
    assert all("NOT IN FACTS" not in x["body"] for x in stack.items.values())
    out = paste_and_submit(client, t["id"], CLEAN.replace("Let me know if you want changes!", "NOT IN FACTS: none"))
    assert not any(p["blocked"] for p in out["pieces"])


def test_not_in_facts_list_is_cut_but_does_not_block_when_off(client, stack):
    t = make_task(client, stack)
    out = paste_and_submit(client, t["id"], CLEAN.replace("Let me know if you want changes!",
                                                          "NOT IN FACTS:\n- Hotel guests ask about [[partner-rate]]."))
    assert not any(p["blocked"] for p in out["pieces"])
    assert all("NOT IN FACTS" not in x["body"] for x in stack.items.values())
