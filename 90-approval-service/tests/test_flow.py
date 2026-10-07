"""End to end against the real content calendar (19, in-process): what 72 sends, what 19 records."""
import hashlib
import logging

import httpx
import pytest

from tests.conftest import APPROVER, CONTROL_H, KEY, SECRETS

REVIEWER = "Sam Jones Tesfaye"


def sha(text):
    return hashlib.sha256(text.replace("\r\n", "\n").strip().encode()).hexdigest()


def decide(client, *decisions, reviewer=REVIEWER):
    r = client.post("/decisions", json={"reviewer": reviewer, "decisions": list(decisions)}, headers=CONTROL_H)
    assert r.status_code == 200, r.text
    return r.json()


def test_approve_is_bound_and_audited_with_the_full_name(client, stack):
    it = stack.create(body="Autumn hires are open.", require_bound_approval=True, scheduled_at="2026-10-05T08:00:00Z")
    out = decide(client, {"id": it["id"], "decision": "approve", "seen_sha256": it["body_sha256"]})
    assert out == {"ok": True, "summary": [f"#{it['id']}: approved for 2026-10-05 08:00 UTC"], "not_in_review": [],
                   "stale": [], "failed": [], "message": f"#{it['id']}: approved for 2026-10-05 08:00 UTC"}
    now = stack.item(it["id"])
    assert now["status"] == "approved" and now["scheduled_at"] == "2026-10-05T08:00:00Z"
    last = stack.audit(it["id"])[-1]
    assert last["actor"] == REVIEWER and last["to_status"] == "approved"
    assert last["detail"].startswith(f"bound to {it['body_sha256']}")
    assert f"approved by {REVIEWER}" in now["notes"]
    # The calendar got all three headers on every write; the list only the internal key.
    writes = [r for r in stack.router.calendar_calls if r.method != "GET"]
    assert [r.method for r in writes] == ["PATCH", "POST"]
    for r in writes:
        assert (r.headers["x-api-key"], r.headers["x-approver-key"], r.headers["x-actor"]) == (KEY, APPROVER, REVIEWER)
    listing = next(r for r in stack.router.calendar_calls if r.method == "GET")
    assert listing.url.params["status"] == "in_review" and "x-approver-key" not in listing.headers
    # Learning event (46): the internal key only; the approver key never leaves for another service.
    [ev] = stack.learning.calls
    assert ev["path"] == "/events" and ev["body"] == {
        "item_id": it["id"], "channel": "linkedin", "campaign_id": None, "decision": "approved",
        "draft": "Autumn hires are open.", "final": "Autumn hires are open.", "reason": None, "reviewer": REVIEWER}
    assert ev["headers"]["x-api-key"] == KEY and "x-approver-key" not in ev["headers"]


def test_approve_without_a_seen_hash_uses_the_fetched_one(client, stack):
    it = stack.create(require_bound_approval=True)
    out = decide(client, {"id": it["id"], "decision": "approve"})
    assert out["ok"] and stack.item(it["id"])["status"] == "approved"
    assert stack.audit(it["id"])[-1]["detail"].startswith("bound to")
    assert stack.item(it["id"])["scheduled_at"]  # the next full hour


def test_edit_saves_and_approves_the_edited_text(client, stack):
    it = stack.create(body="Old words.")
    out = decide(client, {"id": it["id"], "decision": "edit", "text": "New words.\r\n", "reason": "tighter",
                          "seen_sha256": it["body_sha256"], "publish_at": "2026-10-07T10:30:00+02:00"})
    assert out["ok"] and out["summary"] == [f"#{it['id']}: edited and approved for 2026-10-07 08:30 UTC"]
    now = stack.item(it["id"])
    assert (now["status"], now["body"], now["scheduled_at"]) == ("approved", "New words.", "2026-10-07T08:30:00Z")
    assert stack.audit(it["id"])[-1]["detail"].startswith(f"bound to {sha('New words.')}")
    assert f"edited and approved by {REVIEWER}: tighter" in now["notes"]
    assert stack.learning.calls[0]["body"]["decision"] == "edited"
    assert stack.learning.calls[0]["body"]["final"] == "New words."


def test_changed_since_you_looked_is_stale_and_nothing_is_approved(client, stack):
    it = stack.create(body="What the card showed.")
    seen = it["body_sha256"]
    stack.cal.patch(f"/items/{it['id']}", json={"body": "Someone changed it."}, headers={"X-API-Key": KEY})
    out = decide(client, {"id": it["id"], "decision": "approve", "seen_sha256": seen})
    assert out["ok"] is False and out["stale"] == [it["id"]] and out["failed"] == []
    assert out["summary"] == [f"#{it['id']}: NOT approved: changed since you looked — reopen the card"]
    assert stack.item(it["id"])["status"] == "in_review"
    assert stack.learning.calls == []  # nothing approved, nothing learned
    # Stage 1 was refused, so stage 2 was not tried.
    assert [r.method for r in stack.router.calendar_calls if r.method != "GET"] == ["PATCH"]


def test_an_edit_on_changed_text_is_stale_too(client, stack):
    it = stack.create(body="v1")
    stack.cal.patch(f"/items/{it['id']}", json={"body": "v2"}, headers={"X-API-Key": KEY})
    out = decide(client, {"id": it["id"], "decision": "edit", "text": "my edit of v1", "seen_sha256": it["body_sha256"]})
    assert out["stale"] == [it["id"]] and stack.item(it["id"])["body"] == "v2"


def test_items_not_in_review_are_reported_and_left_alone(client, stack):
    draft = stack.create(status="draft")
    out = decide(client, {"id": draft["id"], "decision": "approve"}, {"id": 999, "decision": "reject_drop"})
    assert out == {"ok": True, "summary": [], "not_in_review": [draft["id"], 999], "stale": [], "failed": [],
                   "message": "No decisions made."}
    assert stack.item(draft["id"])["status"] == "draft"
    assert [r.method for r in stack.router.calendar_calls] == ["GET"]


def test_some_in_review_some_not(client, stack):
    a, b = stack.create(), stack.create(status="draft")
    out = decide(client, {"id": a["id"], "decision": "reject_drop"}, {"id": b["id"], "decision": "approve"})
    assert out["ok"] and out["not_in_review"] == [b["id"]] and out["summary"] == [f"#{a['id']}: rejected"]


def test_reject_rewrite_goes_back_to_draft_for_a_person(client, stack):
    a, b = stack.create(), stack.create()
    out = decide(client, {"id": a["id"], "decision": "reject_rewrite", "reason": "too salesy"},
                 {"id": b["id"], "decision": "reject_rewrite"})
    assert out["ok"]
    assert stack.item(a["id"])["status"] == "draft"
    assert "sent back to rewrite by hand: too salesy" in stack.item(a["id"])["notes"]
    assert stack.item(b["id"])["status"] == "rejected"
    assert out["summary"][0].endswith("automatic rewrites run only through n8n")
    assert [c["body"]["decision"] for c in stack.learning.calls] == ["rejected", "rejected"]
    assert [x["actor"] for x in stack.audit(a["id"])[-2:]] == [REVIEWER, REVIEWER]


def test_back_to_draft_and_skip(client, stack):
    a, b = stack.create(), stack.create()
    out = decide(client, {"id": a["id"], "decision": "back_to_draft", "reason": "not this week"},
                 {"id": b["id"], "decision": "skip"})
    assert out["summary"] == [f"#{a['id']}: back to draft"]
    assert stack.item(a["id"])["status"] == "draft" and stack.item(b["id"])["status"] == "in_review"
    assert f"sent back to draft by {REVIEWER}: not this week" in stack.item(a["id"])["notes"]
    assert stack.learning.calls == []


def test_edited_video_goes_to_draft_without_the_old_video(client, stack):
    video = "http://video.test/videos/" + "a" * 32 + ".mp4"
    it = stack.create(channel="video", body="Hook: Old", video_url=video, image_url=video[:-4] + ".jpg")
    out = decide(client, {"id": it["id"], "decision": "edit", "text": "Hook: New"})
    assert out["ok"] and "needs a new render" in out["summary"][0]
    now = stack.item(it["id"])
    assert (now["status"], now["body"], now["video_url"], now["image_url"]) == ("draft", "Hook: New", None, None)
    assert "edited video needs a new render (not approved)" in now["notes"]


def test_engine_outcomes_for_engine_items(client, stack):
    it = stack.create(notes="engine pillar #4 slot #2")
    plain = stack.create()
    decide(client, {"id": it["id"], "decision": "approve"}, {"id": plain["id"], "decision": "reject_drop"})
    assert [(c["path"], c["body"]) for c in stack.engine.calls] == [
        ("/pillars/4/outcomes", {"item_id": it["id"], "decision": "approved"})]
    assert "x-approver-key" not in stack.engine.calls[0]["headers"]


def test_without_engine_url_no_outcomes(client, stack, monkeypatch):
    monkeypatch.delenv("ENGINE_URL")
    it = stack.create(notes="engine pillar #4")
    assert decide(client, {"id": it["id"], "decision": "approve"})["ok"]
    assert stack.engine.calls == []


def test_learning_or_engine_down_still_ok_with_a_note(client, stack):
    stack.learning.status = None
    stack.engine.status = 500
    it = stack.create(notes="engine pillar #4")
    out = decide(client, {"id": it["id"], "decision": "approve"})
    assert out["ok"] is True and stack.item(it["id"])["status"] == "approved"
    assert out["summary"][1:] == [
        "Note: 1 of 1 decision events were not recorded for learning (learning service unreachable (ConnectError)); "
        "the decisions themselves are saved.",
        "Note: 1 of 1 content-engine outcomes were not recorded (HTTP 500); the decisions themselves are saved."]


def test_other_calendar_errors_are_failed_and_stop_that_item(client, stack):
    a, b = stack.create(), stack.create()
    stack.router.override[("PATCH", f"/items/{a['id']}")] = httpx.Response(500, json={"detail": "disk full"})
    out = decide(client, {"id": a["id"], "decision": "approve"}, {"id": b["id"], "decision": "approve"})
    assert out["ok"] is False and out["stale"] == []
    assert out["failed"] == [{"item_id": a["id"], "status": 500, "detail": "disk full"}]
    assert out["message"].endswith('Some updates failed:\n"disk full"')
    assert stack.item(a["id"])["status"] == "in_review"  # not approved after its PATCH failed
    assert stack.item(b["id"])["status"] == "approved"
    assert [c["body"]["item_id"] for c in stack.learning.calls] == [b["id"]]


def test_calendar_down_mid_batch_is_failed(client, stack):
    it = stack.create()

    orig = stack.router.handle_async_request

    async def down_after_list(request):
        if request.method != "GET":
            raise httpx.ConnectError("down", request=request)
        return await orig(request)
    stack.router.handle_async_request = down_after_list
    out = decide(client, {"id": it["id"], "decision": "approve"})
    assert out["failed"] == [{"item_id": it["id"], "status": None, "detail": "calendar unreachable (ConnectError)"}]


def test_a_name_outside_latin1_falls_back_to_the_note(client, stack):
    it = stack.create()
    decide(client, {"id": it["id"], "decision": "approve"}, reviewer="Σοφία Sam")
    assert stack.audit(it["id"])[-1]["actor"] == "Σοφία"
    assert "approved by Σοφία Sam" in stack.item(it["id"])["notes"]


def test_a_latin1_name_is_kept_whole(client, stack):
    it = stack.create()
    decide(client, {"id": it["id"], "decision": "approve"}, reviewer="José Müller")
    assert stack.audit(it["id"])[-1]["actor"] == "José Müller"


def test_fifty_decisions_in_one_batch(client, stack):
    ids = [stack.create(body=f"Post {i}")["id"] for i in range(50)]
    out = decide(client, *[{"id": i, "decision": "approve"} for i in ids])
    assert out["ok"] and len(out["summary"]) == 50
    assert all(stack.item(i)["status"] == "approved" for i in ids)


def test_no_key_ever_in_responses_or_logs(client, stack, caplog):
    caplog.set_level(logging.DEBUG)
    it = stack.create()
    stack.router.override[("POST", f"/items/{it['id']}/status")] = httpx.Response(403, json={"detail": "nope"})
    texts = [client.post("/decisions", json={"reviewer": "Sam", "decisions": [{"id": it["id"], "decision": d}]},
                         headers=CONTROL_H).text for d in ("approve", "reject_drop")]
    texts.append(client.get("/health").text)
    texts.append(client.post("/decisions", json={"decisions": []}, headers=CONTROL_H).text)
    texts.append(client.post("/decisions", json={"decisions": []}).text)
    blob = "\n".join(texts) + "\n" + caplog.text
    assert "Some updates failed" in blob and "decisions by Sam" in caplog.text
    for s in SECRETS:
        assert s not in blob


@pytest.mark.parametrize("decision", ["approve", "edit", "reject_rewrite", "reject_drop", "back_to_draft"])
def test_every_decision_only_touches_its_item(client, stack, decision):
    a, other = stack.create(), stack.create()
    decide(client, {"id": a["id"], "decision": decision, "text": "Changed text", "reason": "why"})
    assert stack.item(other["id"])["status"] == "in_review"
    assert all(f"/items/{other['id']}" not in r.url.path for r in stack.router.calendar_calls)
