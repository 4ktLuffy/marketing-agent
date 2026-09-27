import json
from pathlib import Path

import httpx
import pytest

from .conftest import CAL, ENGINE, HEX, KEYS, PASSWORD, URLS, item

CAMP, LEARN, RULES, STATUS = URLS["CAMPAIGNS_URL"], URLS["LEARNING_URL"], URLS["RULES_URL"], URLS["STATUS_URL"]


def mock_all(mock):
    """Every backend answers, so every page renders with data."""
    mock.get(f"{CAL}/items", params={"status": "in_review"}).respond(json=[
        item(1, image_url=f"http://localhost:8117/cards/{HEX}.png", notes="engine pillar #4 slot #1"),
        item(2, channel="video", video_url=f"http://localhost:8171/videos/{HEX}.mp4")])
    mock.get(f"{CAL}/items").respond(json=[item(1), item(3, status="approved"), item(4, status="published"),
                                           item(5, scheduled_at=None)])
    mock.get(f"{CAL}/items/1").respond(json=item(1, notes="[2026-09-27T10:00:00Z] draft -> in_review: e2e"))
    mock.get(f"{LEARN}/items/1/attempts").respond(json={"item_id": 1, "rejections": 2})
    mock.get(f"{RULES}/rules").respond(json={"channels": {"x": {"limit": 280, "url_length": 23},
                                                          "linkedin": {"limit": 3000}, "instagram": {"limit": 2200, "max_hashtags": 30}}})
    mock.get(f"{CAMP}/insights").respond(json={"by_channel": [{"channel": "linkedin", "posts": 3, "clicks": 40, "avg_clicks": 13.3}],
                                               "top_posts": [{"item_id": 1, "title": "Post 1", "channel": "linkedin", "clicks": 30}], "errors": []})
    mock.get(f"{CAMP}/insights/hooks").respond(json={"recommended": ["question", "story"], "explored": None, "styles": [
        {"hook_style": "question", "posts": 2, "clicks": 30, "clicks_per_post": 15, "posterior_mean": 10, "sample": 9, "recommended": True},
        {"hook_style": "fact_led", "posts": 1, "clicks": 2, "clicks_per_post": 2, "posterior_mean": 1, "sample": 1, "recommended": False}],
        "unlabeled_posts": 1, "method": "thompson", "errors": []})
    mock.get(f"{CAMP}/campaigns").respond(json=[{"id": 1, "name": "Team Box", "status": "active", "start_date": "2026-10-01",
                                                 "end_date": "2026-10-31", "channels": ["linkedin"], "goal_text": "20 sign-ups"}])
    mock.get(f"{CAMP}/campaigns/1/scorecard").respond(json={"elapsed_pct": 40.0, "kpis": [
        {"metric": "clicks", "target_value": 500, "actual_value": 210, "progress_pct": 42.0, "state": "on_track"}],
        "assets": {"approved": 2, "draft": 1}, "errors": []})
    mock.get(f"{CAMP}/report/unmeasured").respond(json=[{"name": "Summer", "unmeasured": ["signups"]}])
    mock.get(f"{ENGINE}/pillars").respond(json=[
        {"id": 4, "title": "Cold brew month", "month": "2026-10", "status": "paused", "channels": ["x"], "atoms": 20,
         "atoms_verified": 14, "slots": {"planned": 5, "drafted": 3, "dropped": 1}}])
    mock.get(f"{ENGINE}/pillars/4/health").respond(json={"pillar_id": 4, "decided": 10, "approved": 4, "edited": 2, "rejected": 4,
        "approved_clean_rate": 0.4, "rejected_rate": 0.4, "min_decisions": 10, "paused": True, "reason": "rejected_rate 0.40 > 0.25"})
    mock.get(f"{STATUS}/status").respond(json={"checked_at": "2026-09-27T10:00:00Z", "all_ok": False, "services": [
        {"name": "content-calendar", "url": "http://content-calendar:8000", "ok": True, "latency_ms": 3, "error": None},
        {"name": "claim-checker", "url": "http://claim-checker:8000", "ok": False, "latency_ms": None, "error": "timeout"}]})


PAGES = ["/", "/?tab=flagged", "/calendar", "/performance", "/performance?days=7", "/engine", "/campaigns",
         "/chat", "/status", "/more", "/items/1", "/items/1/edit", "/results"]


def test_every_page_renders(authed, mock):
    c, _ = authed
    mock_all(mock)
    for p in PAGES:
        r = c.get(p)
        assert r.status_code == 200, (p, r.text[:300])


def test_no_secret_or_internal_url_in_any_response(authed, mock):
    """Keys, the password and internal service URLs never reach the browser: pages, fragments,
    JSON and every static file."""
    c, token = authed
    mock_all(mock)
    mock.post(f"{URLS['N8N_BASE_URL']}/webhook/mkt-apply-decisions").respond(json={"ok": True, "summary": ["#1: approved"]})
    bodies = [c.get(p).text for p in PAGES]
    bodies.append(c.get("/calendar/events", params={"start": "2026-09-28T00:00:00Z", "end": "2026-11-09T00:00:00Z"}).text)
    bodies.append(c.post("/decide", data={"id": 1, "decision": "approve"}, headers={"HX-Request": "1", "X-CSRF-Token": token}).text)
    bodies.append(c.get("/login").text)
    static = Path(__file__).parent.parent / "app" / "static"
    for f in static.rglob("*"):
        if f.is_file() and f.suffix in (".js", ".css", ".webmanifest", ".svg", ".html"):
            bodies.append(c.get("/static/" + f.relative_to(static).as_posix()).text)
    everything = "\n".join(bodies)
    for secret in [*KEYS.values(), PASSWORD]:
        assert secret not in everything
    for url in URLS.values():
        assert url.split("//")[1].split(":")[0] not in everything, url
    assert "X-Control-Key" not in everything and "X-API-Key" not in everything


def test_calendar_feed(authed, mock):
    c, _ = authed
    route = mock.get(f"{CAL}/items").respond(json=[item(1), item(3, status="approved"), item(4, status="published"),
                                                   item(5, scheduled_at=None)])
    evs = c.get("/calendar/events", params={"start": "2026-09-28T00:00:00Z", "end": "2026-11-09T00:00:00Z"}).json()
    q = route.calls.last.request.url.params
    assert q["from"] == "2026-09-28T00:00:00Z" and q["to"] == "2026-11-09T00:00:00Z" and "status" not in q
    assert [e["id"] for e in evs] == ["1", "3", "4"]                     # unscheduled left out
    assert evs[0] == {"id": "1", "title": "linkedin · Post 1", "start": "2026-10-01T09:00:00Z", "url": "/items/1",
                      "color": "#c98a00", "editable": True, "extendedProps": {"status": "in_review", "channel": "linkedin"}}
    assert evs[1]["color"] == "#1f8a4c" and evs[2]["color"] == "#2f6fd6" and evs[2]["editable"] is False
    assert c.get("/calendar/events", params={"start": "drop table"}).status_code == 422
    route.side_effect = httpx.ConnectError("down")
    r = c.get("/calendar/events")
    assert r.status_code == 502 and "calendar.internal" not in r.text


def test_reschedule_patches_time_with_internal_key_only(authed, mock):
    c, token = authed
    mock.get(f"{CAL}/items/3").respond(json=item(3, status="approved"))
    patch = mock.patch(f"{CAL}/items/3").respond(json=item(3, status="approved", scheduled_at="2026-10-05T09:00:00Z"))
    r = c.post("/calendar/reschedule", json={"id": 3, "start": "2026-10-05T09:00:00Z"}, headers={"X-CSRF-Token": token})
    assert r.status_code == 200 and r.json()["start"] == "2026-10-05T09:00:00Z"
    req = patch.calls.last.request
    assert json.loads(req.content) == {"scheduled_at": "2026-10-05T09:00:00Z"}
    assert req.headers["X-API-Key"] == KEYS["INTERNAL_API_KEY"] and "X-Approver-Key" not in req.headers
    mock.get(f"{CAL}/items/4").respond(json=item(4, status="published"))
    assert c.post("/calendar/reschedule", json={"id": 4, "start": "2026-10-05T09:00:00Z"},
                  headers={"X-CSRF-Token": token}).status_code == 409
    assert c.post("/calendar/reschedule", json={"id": 3, "start": "soon"}, headers={"X-CSRF-Token": token}).status_code == 422


def test_resume_pillar_calls_engine_with_key(authed, mock):
    c, token = authed
    mock_all(mock)
    html = c.get("/engine").text
    assert "Cold brew month" in html and "Resume this pillar" in html and "rejected_rate 0.40" in html and "40 %" in html
    res = mock.post(f"{ENGINE}/pillars/4/resume").respond(json={"paused": False})
    r = c.post("/engine/4/resume", data={"csrf": token})
    assert r.status_code == 303 and r.headers["location"] == "/engine"
    assert res.calls.last.request.headers["X-API-Key"] == KEYS["INTERNAL_API_KEY"]
    mock.post(f"{ENGINE}/pillars/9/resume").respond(404, json={"detail": "no pillar 9"})
    assert c.post("/engine/9/resume", data={"csrf": token}).status_code == 404


def test_read_calls_carry_no_keys(authed, mock):
    c, _ = authed
    mock_all(mock)
    for p in PAGES:
        c.get(p)
    for call in mock.calls:
        h = call.request.headers
        assert "x-approver-key" not in h, call.request.url
        if call.request.method == "GET":
            assert "x-api-key" not in h and "x-control-key" not in h, call.request.url
    # the control room never changes a status itself: approvals go through n8n only
    assert not any("/status" in str(call.request.url) and call.request.method == "POST" for call in mock.calls)


def test_performance_campaigns_status_chat_content(authed, mock):
    c, _ = authed
    mock_all(mock)
    perf = c.get("/performance").text
    assert 'id="perf-data"' in perf and '"clicks_per_post": 15' in perf and "Winners now" in perf and "#1 Post 1" in perf
    camp = c.get("/campaigns").text
    assert "Team Box" in camp and "210" in camp and "on track" in camp and "Summer" in camp
    st = c.get("/status").text
    assert "1 of 2 services are down" in st and "claim-checker" in st
    chat = c.get("/chat").text
    assert "https://n8n.example.test/webhook/mkt-marketing-chat/chat" in chat


def test_detail_and_edit_pages(authed, mock):
    c, _ = authed
    mock_all(mock)
    d = c.get("/items/1").text
    assert "draft → in_review" in d and "Rejections so far" in d and ">2<" in d
    e = c.get("/items/1/edit").text
    assert 'data-limit="3000"' in e and 'name="decision" value="edit"' in e and 'data-fold="210"' in e
    mock.get(f"{CAL}/items/9").respond(json=item(9, status="approved"))
    assert "Only items in review can be approved" in c.get("/items/9/edit").text
    mock.get(f"{CAL}/items/10").respond(404, json={"detail": "no item 10"})
    assert c.get("/items/10").status_code == 404


@pytest.mark.parametrize("path", ["/performance", "/campaigns", "/engine", "/status"])
def test_dashboards_survive_backends_down(authed, mock, path):
    c, _ = authed
    for base in (CAMP, ENGINE, STATUS):
        mock.route(url__startswith=base).mock(side_effect=httpx.ConnectError("down"))
    r = c.get(path)
    assert r.status_code == 200 and "unreachable" in r.text
