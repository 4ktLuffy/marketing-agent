"""Tasks pages (88 task bridge, contract §4): new task -> pack with the data-sharing preview first
-> paste -> split -> submit -> evidence per sentence -> export only when 88 says ready; blockers
page and the queue header; task-bridge queue cards. 88 is mocked with respx."""
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import views

from .conftest import CAL, KEYS, PASSWORD, URLS, csrf_of, item, login

TASKS = "http://tasks.internal:8000"
BRAND = URLS["BRAND_URL"]
TID = "T-7K2M9Q"
SENT = ["Midweek Escape costs £180 per room per night, 2 sharing, Sun–Thu. [[weekday-rate]]",
        "Breakfast is included in every stay. [[breakfast]]"]
PREVIEW = {"sent": SENT, "slotted": ["weekday-rate", "breakfast", "staff-discount"],
           "withheld": [{"key": "owner-margin", "reason": "restricted"}, {"key": "leeds-rate", "reason": "out_of_scope"}],
           "chars": 2310}
PIECES = [{"key": "p1", "channel": "linkedin", "kind": "post", "max_chars": None},
          {"key": "p2", "channel": "instagram", "kind": "caption", "max_chars": 300}]
TASK = {"id": TID, "goal": "Fill midweek rooms in October", "pieces": PIECES, "scope": {"sites": ["Quayside"]},
        "publish_on": "2026-10-06", "status": "open", "fact_set_version": "fs-4-a1b2c3d4",
        "pack": "Task T-7K2M9Q · facts fs-4-a1b2c3d4 · publish 2026-10-06\n=== 1 LINKEDIN ===", "pack_sha256": "0" * 64,
        "share_preview": PREVIEW}
ALL_LABELS = ["match", "review", "conflict_or_expired", "wrong_scope", "no_source", "missing_disclosure",
              "forbidden_phrase", "slot_blocked"]
FINDINGS = [
    {"sentence": "Stay midweek for £180 a night.", "label": "match", "fact_key": "weekday-rate",
     "quote": "£180 per room per night", "blocking": False, "detail": "the price matches"},
    {"sentence": "The best rooms in town.", "label": "review", "fact_key": None, "quote": None, "blocking": False,
     "detail": "a comparison: your call"},
    {"sentence": "Offer ends 1 September.", "label": "conflict_or_expired", "fact_key": "summer-offer",
     "quote": "Summer offer", "blocking": True, "detail": "expired on 2026-09-01"},
    {"sentence": "Also at our Leeds branch.", "label": "wrong_scope", "fact_key": "leeds-rate", "quote": None,
     "blocking": True, "detail": "the task is for Quayside"},
    {"sentence": "Award-winning spa.", "label": "no_source", "fact_key": None, "quote": None, "blocking": True,
     "detail": "no fact says so"},
    {"sentence": None, "label": "missing_disclosure", "fact_key": "weekday-rate", "quote": None, "blocking": True,
     "detail": "say: per room per night, 2 sharing"},
    {"sentence": "Guaranteed sunshine.", "label": "forbidden_phrase", "fact_key": None, "quote": None,
     "blocking": True, "detail": "'guaranteed' is on your never-say list"},
    {"sentence": "Staff pay [[staff-discount]].", "label": "slot_blocked", "fact_key": "staff-discount",
     "quote": None, "blocking": True, "detail": "internal and not for this task"},
]


@pytest.fixture
def bridge(monkeypatch, mock):
    """A logged-in client on an app with TASKS_URL set; 88 and 05 mocked. Returns (client, csrf, mock)."""
    monkeypatch.setenv("TASKS_URL", TASKS)
    from app.main import create_app
    mock.get(f"{BRAND}/facts/v2").respond(json={"facts": [
        {"key": "a", "scope": {"sites": ["Quayside", "Leeds"], "channels": ["email"], "segments": ["couples"]}},
        {"key": "b", "scope": {"sites": ["Quayside"], "plan_tiers": ["Pro+"]}}]})
    mock.get(f"{TASKS}/occasions").respond(json={"country": None, "occasions": []})
    with TestClient(create_app(), follow_redirects=False) as c:
        assert login(c).status_code == 303
        yield c, csrf_of(c.get("/more").text), mock


def test_not_installed_when_tasks_url_empty(authed, mock):
    c, token = authed
    for p in ("/tasks", "/tasks/new", f"/tasks/{TID}/pack", f"/tasks/{TID}", "/blockers"):
        r = c.get(p)
        assert r.status_code == 200 and "task bridge (88) isn't installed" in r.text, p
    r = c.post(f"/tasks/{TID}/paste", data={"csrf": token, "text": "x", "provider": "claude"})
    assert "isn't installed" in r.text
    mock.get(f"{CAL}/items").respond(json=[])
    assert "blockers-line" not in c.get("/").text   # no call to 88 at all (respx would refuse it)


def test_login_required(client, mock):
    for p in ("/tasks", "/tasks/new", f"/tasks/{TID}/pack", f"/tasks/{TID}", "/blockers", f"/tasks/{TID}/export"):
        r = client.get(p)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), p
    for p in (f"/tasks/{TID}/paste", f"/tasks/{TID}/submit", f"/tasks/{TID}/split", "/tasks/new"):
        assert client.post(p, data={}).status_code == 401, p


def test_new_task_form_offers_scope_values_from_facts(bridge):
    c, _, _ = bridge
    h = c.get("/tasks/new").text
    for v in ("Quayside", "Leeds", "couples", "Pro+"):
        assert f'value="{v}"' in h, v
    assert 'name="scope_channels" value="email"' in h
    assert '<option value="email"' in h   # a channel seen in facts is offered for pieces too


def test_add_and_remove_pieces_without_js(bridge):
    c, token, mock = bridge
    create = mock.post(f"{TASKS}/tasks").respond(json=TASK)
    base = {"csrf": token, "goal": "Fill rooms", "publish_on": "2026-10-06"}
    r = c.post("/tasks/new", data={**base, "action": "add", "p_channel": ["linkedin"], "p_kind": ["post"], "p_max": [""]})
    assert r.status_code == 200 and r.text.count('name="p_channel"') == 2
    r = c.post("/tasks/new", data={**base, "remove": "0", "p_channel": ["linkedin", "x"], "p_kind": ["post", "post"],
                                   "p_max": ["", "280"]})
    assert r.status_code == 200 and r.text.count('name="p_channel"') == 1 and 'value="280"' in r.text
    assert not create.called


def test_create_task_validates_then_posts_to_88(bridge):
    c, token, mock = bridge
    create = mock.post(f"{TASKS}/tasks").respond(json=TASK)
    bad = c.post("/tasks/new", data={"csrf": token, "action": "create", "goal": "", "publish_on": "2026-02-30",
                                     "p_channel": ["myspace"], "p_kind": ["post"], "p_max": ["5"]})
    assert bad.status_code == 422 and not create.called
    for msg in ("say in a sentence what the copy is for", "pick the day it goes out", "Piece 1: pick a channel",
                "Piece 1: the length limit"):
        assert msg in bad.text, msg
    assert c.post("/tasks/new", data={"goal": "x"}).status_code == 403
    r = c.post("/tasks/new", data={"csrf": token, "action": "create", "goal": "Fill midweek rooms",
                                   "publish_on": "2026-10-06", "audience": "Couples",
                                   "p_channel": ["linkedin", "instagram"], "p_kind": ["post", "caption"],
                                   "p_max": ["", "300"], "scope_sites": ["Quayside"], "scope_segments_other": "families, couples"})
    assert r.status_code == 303 and r.headers["location"] == f"/tasks/{TID}/pack"
    body = json.loads(create.calls[0].request.content)
    assert body["pieces"] == [{"key": "p1", "channel": "linkedin", "kind": "post", "max_chars": None},
                              {"key": "p2", "channel": "instagram", "kind": "caption", "max_chars": 300}]
    assert body["scope"]["sites"] == ["Quayside"] and body["scope"]["segments"] == ["families", "couples"]
    assert body["scope"]["regions"] == [] and body["publish_on"] == "2026-10-06" and body["audience"] == "Couples"
    assert create.calls[0].request.headers["x-api-key"] == KEYS["INTERNAL_API_KEY"]


def test_pack_page_share_preview_first_and_exact(bridge):
    c, _, mock = bridge
    mock.get(f"{TASKS}/tasks/{TID}").respond(json=TASK)
    h = c.get(f"/tasks/{TID}/pack").text
    assert "This leaves your business: 2 fact lines." in h
    lines = re.findall(r'<li class="sent-line">(.*?)</li>', h, re.S)
    assert [x.replace("&#39;", "'") for x in lines] == SENT     # exactly 88's share_preview.sent
    assert "[[staff-discount]]" in h and "Kept as placeholders (value never sent)" in h
    assert "owner-margin</code>: restricted: it never leaves your business" in h
    assert "leeds-rate</code>: for another branch" in h
    assert h.index("What leaves your business") < h.index('id="pack"') < h.index('action="/tasks/T-7K2M9Q/paste"')
    assert re.search(r'<textarea id="pack" class="pack" rows="14" readonly>', h)
    assert '<script src="/static/tasks.js" defer></script>' in h
    for label in ("ChatGPT", "Claude", "Gemini", "Another AI"):
        assert label in h


def test_pack_falls_back_to_pack_endpoint(bridge):
    c, _, mock = bridge
    mock.get(f"{TASKS}/tasks/{TID}").respond(json={k: v for k, v in TASK.items() if k not in ("pack", "share_preview")})
    mock.get(f"{TASKS}/tasks/{TID}/pack").respond(json={"pack": "PACK TEXT", "share_preview": PREVIEW})
    h = c.get(f"/tasks/{TID}/pack").text
    assert "PACK TEXT" in h and "2 fact lines" in h


def test_bad_task_id_is_404(bridge):
    c, token, _ = bridge
    assert c.get("/tasks/../etc/pack").status_code == 404
    assert c.get("/tasks/T-x/pack").status_code == 404
    assert c.post("/tasks/nope/paste", data={"csrf": token}).status_code == 404


SPLIT = {"draft_id": 12, "split": [
    {"piece_key": "p1", "text": "Stay midweek for [[weekday-rate]].", "removed_pre": "Sure! Here are your posts:",
     "removed_post": ""},
    {"piece_key": "p2", "text": "Midweek calm.", "removed_pre": "", "removed_post": "Let me know if you want changes!"}],
    "problems": []}


def test_paste_csrf_validation_and_split_preview(bridge):
    c, token, mock = bridge
    mock.get(f"{TASKS}/tasks/{TID}").respond(json=TASK)
    paste = mock.post(f"{TASKS}/tasks/{TID}/paste").respond(json=SPLIT)
    assert c.post(f"/tasks/{TID}/paste", data={"text": "x", "provider": "claude"}).status_code == 403
    r = c.post(f"/tasks/{TID}/paste", data={"csrf": token, "text": "  ", "provider": "claude"})
    assert r.status_code == 422 and "Paste the AI" in r.text
    r = c.post(f"/tasks/{TID}/paste", data={"csrf": token, "text": "answer", "provider": "bing"})
    assert r.status_code == 422 and "Say which AI you used." in r.text and "answer</textarea>" in r.text
    r = c.post(f"/tasks/{TID}/paste", data={"csrf": token, "text": "x" * 40001, "provider": "claude"})
    assert r.status_code == 422 and not paste.called
    r = c.post(f"/tasks/{TID}/paste", data={"csrf": token, "text": "=== 1 LINKEDIN ===\nStay", "provider": "chatgpt"})
    assert r.status_code == 200
    assert json.loads(paste.calls[0].request.content) == {"text": "=== 1 LINKEDIN ===\nStay", "provider": "chatgpt"}
    h = r.text
    assert "Removed before it" in h and "Sure! Here are your posts:" in h
    assert "Removed after it" in h and "Let me know if you want changes!" in h
    assert 'name="draft_id" value="12"' in h and f'action="/tasks/{TID}/submit"' in h
    assert "<details >" in h or "<details>" in h   # reassign form closed when there are no problems


def test_split_problems_open_the_reassign_form_and_manual_split(bridge):
    c, token, mock = bridge
    mock.get(f"{TASKS}/tasks/{TID}").respond(json=TASK)
    mock.post(f"{TASKS}/tasks/{TID}/paste").respond(json={**SPLIT, "problems": ["found 1 piece, expected 2"],
                                                          "split": SPLIT["split"][:1]})
    manual = mock.post(f"{TASKS}/tasks/{TID}/drafts/12/split").respond(json=SPLIT)
    h = c.post(f"/tasks/{TID}/paste", data={"csrf": token, "text": "a", "provider": "gemini"}).text
    assert "found 1 piece, expected 2" in h and "<details open>" in h
    assert h.count('name="piece_text" rows=') == 2   # the editable boxes of the reassign form
    assert c.post(f"/tasks/{TID}/split", data={"draft_id": "12", "piece_key": ["p1"], "piece_text": ["a"]}).status_code == 403
    r = c.post(f"/tasks/{TID}/split", data={"csrf": token, "draft_id": "12", "piece_key": ["p1", "p2"],
                                            "piece_text": ["one", "two"]})
    assert r.status_code == 200
    assert json.loads(manual.calls[0].request.content) == {"pieces": [{"piece_key": "p1", "text": "one"},
                                                                     {"piece_key": "p2", "text": "two"}]}
    assert c.post(f"/tasks/{TID}/split", data={"csrf": token, "draft_id": "x"}).status_code == 422


def test_submit_renders_evidence_chips_for_every_label(bridge):
    c, token, mock = bridge
    mock.get(f"{TASKS}/tasks/{TID}").respond(json=TASK)
    submit = mock.post(f"{TASKS}/tasks/{TID}/submit").respond(json={"pieces": [
        {"piece_key": "p1", "filled_text": "Stay midweek for £180 a night.", "filled_sha256": "a" * 64,
         "blocked": True, "findings": FINDINGS, "calendar_item_id": 41},
        {"piece_key": "p2", "filled_text": "Midweek calm.", "filled_sha256": "b" * 64, "blocked": False,
         "findings": [], "calendar_item_id": 42}]})
    assert c.post(f"/tasks/{TID}/submit", data={"draft_id": "12"}).status_code == 403
    r = c.post(f"/tasks/{TID}/submit", data={"csrf": token, "draft_id": "12"})
    assert r.status_code == 200 and json.loads(submit.calls[0].request.content) == {"draft_id": 12}
    h = r.text
    for label in ALL_LABELS:
        words = views.EVIDENCE[label][0]
        assert f'data-label="{label}">{words}</span>' in h, label
    assert "<q class=\"small\">£180 per room per night</q>" in h and "<code class=\"small\">weekday-rate</code>" in h
    assert "expired on 2026-09-01" in h and "The whole piece" in h
    assert "blocked: back to draft" in h
    assert 'href="/items/41"' in h and 'href="/items/42"' in h
    assert "/export?format=" not in h and "Not ready to export yet." in h


def test_export_button_only_when_88_says_ready(bridge):
    c, _, mock = bridge
    route = mock.get(f"{TASKS}/tasks/{TID}")
    pieces = [{**p, "piece_key": p["key"], "filled_text": "x", "findings": [], "state": "in_review", "item_id": 41}
              for p in PIECES]
    route.respond(json={**TASK, "pieces": pieces, "export_ready": False,
                        "export_missing": ["p1 (linkedin) is waiting for approval"]})
    h = c.get(f"/tasks/{TID}").text
    assert "/export?format=" not in h and "p1 (linkedin) is waiting for approval" in h
    route.respond(json={**TASK, "pieces": [{**p, "state": "approved"} for p in pieces], "export": {"ready": True}})
    h = c.get(f"/tasks/{TID}").text
    assert f'href="/tasks/{TID}/export?format=md"' in h and f'href="/tasks/{TID}/export?format=txt"' in h
    # all pieces approved but 88 did not say ready: no button
    route.respond(json={**TASK, "pieces": [{**p, "state": "approved"} for p in pieces]})
    assert "/export?format=" not in c.get(f"/tasks/{TID}").text


def real_88_task(state: str, ready: bool, missing: list[str]) -> dict:
    """GET /tasks/{id} exactly as 88 answers it: `pieces` = the requested pieces, `piece_state` =
    their results (88 field names), `export` = {"ready", "missing"} from 88's export rules."""
    def result(n, p, text, item):
        return {"piece_key": p["key"], "n": n, "channel": p["channel"], "state": state, "draft_id": 3,
                "filled_text": text, "filled_sha256": str(n) * 64, "blocked": False,
                "findings": [{"sentence": text, "label": "match", "fact_key": "weekday-rate",
                              "quote": "£180 per room per night", "blocking": False, "detail": "£180 = weekday-rate"}],
                "checks": {"brand": [], "platform": [], "skipped": []}, "used_facts": ["weekday-rate"],
                "calendar_item_id": item, "stale_facts": []}
    return {**TASK, "status": "submitted", "snapshot": [{"key": "weekday-rate", "version": 2, "slot": "weekday-rate",
                                                         "sensitivity": "public"}],
            "piece_state": [result(1, PIECES[0], "Midweek from £180 per room per night, 2 sharing.", 41),
                            result(2, PIECES[1], "Quiet lake, £180 a night, 2 sharing.", 42)],
            "drafts": [{"draft_id": 3, "kind": "paste", "provider": "chatgpt", "chars": 120, "problems": [],
                        "parent_id": None, "created_at": "2026-09-29T10:00:00Z"}],
            "exports": [], "events": [], "export": {"ready": ready, "missing": missing}}


def test_review_page_with_88s_real_task_shape(bridge):
    c, _, mock = bridge
    route = mock.get(f"{TASKS}/tasks/{TID}")
    route.respond(json=real_88_task("in_review", False, ["piece p2 is in_review, not approved (item 42)"]))
    h = c.get(f"/tasks/{TID}").text
    # results come from piece_state, with 88's field names
    assert "Midweek from £180 per room per night, 2 sharing." in h and "Quiet lake, £180 a night, 2 sharing." in h
    assert 'href="/items/41"' in h and 'href="/items/42"' in h
    assert "<code class=\"small\">weekday-rate</code>" in h and "in review" in h
    # not ready: 88's own reasons, no button
    assert "/export?format=" not in h and "piece p2 is in_review, not approved (item 42)" in h
    # ready: the button, and no "missing" list
    route.respond(json=real_88_task("in_review", True, []))
    h = c.get(f"/tasks/{TID}").text
    assert f'href="/tasks/{TID}/export?format=md"' in h and f'href="/tasks/{TID}/export?format=txt"' in h
    assert "Not ready to export yet." not in h
    # 88 not ready even though every piece looks fine locally: 88 decides
    route.respond(json=real_88_task("in_review", False, ["fact weekday-rate is no longer valid at 2026-10-06 (expired)"]))
    h = c.get(f"/tasks/{TID}").text
    assert "/export?format=" not in h and "fact weekday-rate is no longer valid" in h


def test_export_download_and_refusal(bridge):
    c, _, mock = bridge
    route = mock.get(f"{TASKS}/tasks/{TID}/export")
    route.respond(content=b"# Fill midweek rooms\n", headers={"content-type": "text/markdown"})
    r = c.get(f"/tasks/{TID}/export?format=md")
    assert r.status_code == 200 and r.content == b"# Fill midweek rooms\n"
    assert r.headers["content-disposition"] == f'attachment; filename="{TID}.md"'
    assert route.calls[0].request.headers["x-api-key"] == KEYS["INTERNAL_API_KEY"]
    route.respond(409, json={"detail": "p1 is not approved"})
    r = c.get(f"/tasks/{TID}/export?format=txt")
    assert r.status_code == 409 and "Not ready to export: HTTP 409: p1 is not approved" in r.text
    assert c.get(f"/tasks/{TID}/export?format=pdf").status_code == 422


BLOCKERS = [{"kind": "price_confirmation", "count": 2, "text": "2 posts need a price confirmation", "link": f"/tasks/{TID}"},
            {"kind": "expired_in_use", "count": 1, "text": "1 fact expired in use", "link": "https://evil.example/x"},
            {"kind": "leads", "count": 3, "text": "3 enquiries waiting", "link": None},
            {"kind": "missing_fact", "count": 0, "text": "0 missing facts", "link": None}]


def test_blockers_page_and_safe_links(bridge):
    c, _, mock = bridge
    mock.get(f"{TASKS}/blockers").respond(json=BLOCKERS)
    h = c.get("/blockers").text
    assert "2 posts need a price confirmation" in h and f'href="/tasks/{TID}"' in h
    assert "evil.example" not in h and 'href="/facts?show=expired"' in h
    assert "3 enquiries waiting" in h


def test_queue_header_shows_compact_blockers(bridge):
    c, _, mock = bridge
    mock.get(f"{CAL}/items").respond(json=[])
    mock.get(f"{TASKS}/blockers").respond(json=BLOCKERS)
    h = c.get("/").text
    line = re.search(r'<p class="blockers-line small".*?</p>', h, re.S).group(0)
    assert "2 posts need a price confirmation" in line and "1 fact expired in use" in line
    assert "3 enquiries waiting" in line and "0 missing facts" not in line and 'href="/blockers"' in line


def test_queue_survives_blockers_down(bridge):
    c, _, mock = bridge
    mock.get(f"{CAL}/items").respond(json=[item(1)])
    mock.get(f"{TASKS}/blockers").mock(side_effect=httpx.ConnectError("refused"))
    r = c.get("/")
    assert r.status_code == 200 and "blockers-line" not in r.text and "Post 1" in r.text


def test_queue_card_from_task_bridge_shows_evidence_and_task_link(authed, mock):
    c, _ = authed
    notes = ("[2026-09-29T10:00:00Z] draft -> in_review: task T-7K2M9Q piece p1\n"
             "warnings: needs your judgement: 'the best rooms in town'\n"
             "quality gate: wrong scope: Leeds rate used for Quayside; missing disclosure: per room per night")
    mock.get(f"{CAL}/items", params={"status": "in_review"}).respond(json=[
        item(5, origin="task-bridge", notes=notes, campaign=TID), item(6, notes="warnings: long")])
    h = c.get("/").text
    card5 = h[h.index('id="card-5"'):h.index('id="card-6"')]
    assert f'href="/tasks/{TID}">from task {TID}</a>' in card5
    assert 'data-label="review">needs your judgement' in card5
    assert 'data-label="wrong_scope">wrong scope' in card5 and 'data-label="missing_disclosure"' in card5
    card6 = h[h.index('id="card-6"'):]
    assert "from task" not in card6 and "data-label" not in card6 and 'class="flag warn"' in card6


def test_no_secret_in_any_tasks_response(bridge):
    c, token, mock = bridge
    mock.get(f"{TASKS}/tasks").respond(json={"tasks": [TASK]})
    mock.get(f"{TASKS}/tasks/{TID}").respond(json=TASK)
    mock.post(f"{TASKS}/tasks/{TID}/paste").respond(json=SPLIT)
    mock.post(f"{TASKS}/tasks/{TID}/submit").respond(json=[{"piece_key": "p1", "filled_text": "x", "findings": FINDINGS}])
    mock.get(f"{TASKS}/blockers").respond(json=BLOCKERS)
    mock.get(f"{CAL}/items").respond(json=[])
    bodies = [c.get(p).text for p in ("/tasks", "/tasks/new", f"/tasks/{TID}/pack", f"/tasks/{TID}", "/blockers", "/")]
    bodies.append(c.post(f"/tasks/{TID}/paste", data={"csrf": token, "text": "a", "provider": "claude"}).text)
    bodies.append(c.post(f"/tasks/{TID}/submit", data={"csrf": token, "draft_id": "12"}).text)
    bodies.append(c.get("/static/tasks.js").text)
    everything = "\n".join(bodies)
    for secret in [*KEYS.values(), PASSWORD, TASKS, "tasks.internal", "brand.internal", "X-API-Key", "X-Owner-Key"]:
        assert secret not in everything, secret
    assert "Midweek" in everything


TEMPLATES = Path(__file__).parent.parent / "app" / "templates"


def test_templates_have_no_inline_script_or_event_handlers():
    """CSP script-src 'self': every <script> has a src (or is a JSON data block); no on*= attribute."""
    for f in TEMPLATES.glob("*.html"):
        text = f.read_text(encoding="utf-8")
        for tag in re.findall(r"<script\b[^>]*>", text, re.I):
            assert "src=" in tag or 'type="application/json"' in tag, (f.name, tag)
        assert not re.search(r"<[^>]*\son[a-z]+\s*=", text, re.I), f.name
        assert "javascript:" not in text.lower(), f.name


def test_tasks_js_is_clipboard_only():
    js = (Path(__file__).parent.parent / "app" / "static" / "tasks.js").read_text()
    assert "clipboard" in js and "fetch(" not in js and "XMLHttpRequest" not in js and "innerHTML" not in js


def test_blocked_piece_shows_brand_and_channel_reasons(client, mock, monkeypatch):
    """A piece blocked by a brand rule (e.g. an emoji the brand doesn't use) says why."""
    from app import tasks as t
    p = {"piece_key": "p1", "channel": "instagram", "filled_text": "Hi 🌄", "blocked": True, "findings": [],
         "checks": {"brand": [{"rule": "emoji_not_allowed", "detail": "emoji 🌄 is not in the brand's emoji set",
                               "severity": "error"}], "platform": [{"rule": "above_recommended", "detail": "long",
                                                                    "severity": "warn"}], "skipped": []}}
    r = t.piece_result(p, {})
    assert [q["where"] for q in r["rule_problems"]] == ["Brand rules", "Channel rules"]
    assert r["rule_problems"][0]["blocking"] is True and r["rule_problems"][1]["blocking"] is False
    assert t.piece_result({"piece_key": "p1"}, {})["rule_problems"] == []


def test_looks_right_confirms_a_split_with_notes_before_submitting(bridge):
    """88 refuses to submit a split with notes until a person accepts it: the button sends the
    pieces as shown, 72 saves that split, then submits the new draft."""
    import json as _j
    import httpx
    c, token, mock = bridge
    split = mock.post(f"{TASKS}/tasks/{TID}/drafts/7/split").mock(
        return_value=httpx.Response(201, json={"draft_id": 8, "split": [], "problems": []}))
    submit = mock.post(f"{TASKS}/tasks/{TID}/submit").mock(return_value=httpx.Response(200, json={"pieces": []}))
    mock.get(f"{TASKS}/tasks/{TID}").mock(return_value=httpx.Response(200, json={
        "id": TID, "goal": "g", "pieces": [], "piece_state": [], "export": {"ready": False, "missing": []}}))
    r = c.post(f"/tasks/{TID}/submit", data={"csrf": token, "draft_id": "7",
                                             "piece_key": ["p1", "p2"], "piece_text": ["one", "two"]})
    assert r.status_code == 200, r.text[:300]
    assert _j.loads(split.calls.last.request.content)["pieces"] == [
        {"piece_key": "p1", "text": "one"}, {"piece_key": "p2", "text": "two"}]
    assert _j.loads(submit.calls.last.request.content)["draft_id"] == 8
    # Without pieces (a clean split) 72 submits the draft directly, no split call.
    n = len(split.calls)
    c.post(f"/tasks/{TID}/submit", data={"csrf": token, "draft_id": "7"})
    assert len(split.calls) == n and _j.loads(submit.calls.last.request.content)["draft_id"] == 7


def blocked_md_task(accepted=None):
    t = real_88_task("draft", False, ["piece p1 is draft, not approved (item 41)"])
    p = t["piece_state"][0]
    f = {"sentence": None, "label": "missing_disclosure", "fact_key": "weekday-rate", "quote": None,
         "blocking": accepted is None, "detail": "add: \"per room per night, 2 sharing\""}
    if accepted:
        f["accepted"] = accepted
    p.update(blocked=accepted is None, findings=[p["findings"][0], f,
                                                {"sentence": "Offer ends soon.", "label": "conflict_or_expired",
                                                 "fact_key": "summer-offer", "quote": None, "blocking": True,
                                                 "detail": "expired"}])
    return t


def test_accept_button_only_on_missing_disclosure(bridge):
    c, _, mock = bridge
    mock.get(f"{TASKS}/tasks/{TID}").respond(json=blocked_md_task())
    h = c.get(f"/tasks/{TID}").text
    assert h.count("It’s there, in other words") == 1
    assert h.count(f'action="/tasks/{TID}/accept"') == 1
    assert 'name="finding" value="1"' in h and f'name="sha" value="{"1" * 64}"' in h
    assert 'name="note" required' in h


def test_accept_posts_to_88_with_who_and_why(bridge):
    c, token, mock = bridge
    mock.get(f"{TASKS}/tasks/{TID}").respond(json=blocked_md_task())
    route = mock.post(f"{TASKS}/tasks/{TID}/pieces/p1/accept").mock(return_value=httpx.Response(200, json={
        "blocked": False, "status": "in_review"}))
    form = {"csrf": token, "piece_key": "p1", "finding": "1", "sha": "1" * 64, "note": "  said as 'per room,  two sharing' "}
    r = c.post(f"/tasks/{TID}/accept", data=form)
    assert r.status_code == 200, r.text[:300]
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"finding": 1, "expected_sha256": "1" * 64, "by": sent["by"], "note": "said as 'per room, two sharing'"}
    assert sent["by"] and route.calls.last.request.headers.get("x-api-key")
    assert "Finding accepted and recorded" in r.text


def test_accept_needs_csrf_and_a_note(bridge):
    c, token, mock = bridge
    mock.get(f"{TASKS}/tasks/{TID}").respond(json=blocked_md_task())
    route = mock.post(f"{TASKS}/tasks/{TID}/pieces/p1/accept").respond(json={})
    form = {"piece_key": "p1", "finding": "1", "sha": "1" * 64, "note": "said as per room"}
    assert c.post(f"/tasks/{TID}/accept", data=form).status_code == 403
    r = c.post(f"/tasks/{TID}/accept", data={**form, "csrf": token, "note": " "})
    assert r.status_code == 422 and "Say where the disclosure is" in r.text
    for bad in ({"sha": "x"}, {"finding": "a"}, {"piece_key": "../x"}):
        assert c.post(f"/tasks/{TID}/accept", data={**form, "csrf": token, **bad}).status_code == 422
    assert not route.calls


def test_accept_refused_by_88_says_reload(bridge):
    c, token, mock = bridge
    mock.get(f"{TASKS}/tasks/{TID}").respond(json=blocked_md_task())
    mock.post(f"{TASKS}/tasks/{TID}/pieces/p1/accept").respond(409, json={"detail": {"message": "changed since you looked"}})
    r = c.post(f"/tasks/{TID}/accept", data={"csrf": token, "piece_key": "p1", "finding": "1", "sha": "1" * 64,
                                              "note": "said as per room"})
    assert r.status_code == 409 and "Reload and check again" in r.text


def test_accepted_finding_shows_who_and_no_button(bridge):
    c, _, mock = bridge
    mock.get(f"{TASKS}/tasks/{TID}").respond(json=blocked_md_task(
        accepted={"by": "Alex", "note": "said as per room, two sharing", "at": "2026-09-30T02:00:00Z"}))
    h = c.get(f"/tasks/{TID}").text
    assert "accepted</span> by Alex" in h and "said as per room, two sharing" in h
    assert "It’s there, in other words" not in h


def test_new_task_page_shows_occasions_when_88_has_them(bridge):
    c, _, mock = bridge
    mock.get(f"{TASKS}/occasions").respond(json={"calendar": "demoland", "on_local": "21 Fourthmonth 2019 AC",
        "occasions": [{"name": "Spring Festival (Local Spring Festival)", "date": "2027-01-07", "local_date": "29 Sixthmonth 2019 AC",
                       "verified": True, "notes": "No alcohol sponsorship of holidays."},
                      {"name": "Eid al-Fitr", "date": "2027-03-10", "verified": False}],
        "note": "Local clock time runs 6 hours off"})
    h = c.get("/tasks/new").text
    assert "Coming up" in h and "21 Fourthmonth 2019 AC" in h and "Spring Festival (Local Spring Festival)" in h
    assert "date not confirmed" in h and "No alcohol sponsorship" in h


def test_new_task_page_without_occasions(bridge):
    c, _, mock = bridge
    mock.get(f"{TASKS}/occasions").respond(json={"country": None, "occasions": []})
    assert "Coming up" not in c.get("/tasks/new").text
    mock.get(f"{TASKS}/occasions").respond(500)
    r = c.get("/tasks/new")
    assert r.status_code == 200 and "Coming up" not in r.text
