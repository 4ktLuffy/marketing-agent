"""Activity page: login, merged and sorted sources, an unreachable gateway, no key or internal URL,
abilities installed / not installed, caller -> ability, filters, and the JS helpers (node)."""
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app import activity as act
from app import config

from .conftest import CAL, KEYS, PASSWORD, URLS, item

GW = URLS["GATEWAY_URL"]
VERIFIER = "http://verifier.internal:8000"
ASSISTANT = "http://assistant.internal:8000"


def ts(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds").replace("+00:00", "Z")


def row(id, caller, minutes_ago, ok=True, model="mkt-writer", provider="ollama", prompt="social_posts", **kw):
    return {"id": id, "started_at": ts(minutes_ago + 0.2), "finished_at": ts(minutes_ago), "duration_ms": 14200,
            "prompt": prompt, "caller": caller, "model": model, "provider": provider, "ok": ok,
            "error": None if ok else "timeout", "retries": 0, "tokens_in": 800, "tokens_out": 120, **kw}


MAIN = {"running": [{"id": 9, "started_at": ts(0.1), "elapsed_ms": 6100, "prompt": "blog_post",
                     "caller": "25 Blog writer", "model": "mkt-writer", "provider": "ollama"}],
        "recent": [row(8, "26 Social post writer", 1), row(7, "44 claim checker", 30, ok=False, prompt="claim_details")],
        "models": [{"model": "mkt-writer", "provider": "ollama", "calls_today": 2, "failures_today": 1, "avg_ms": 10000,
                    "p95_ms": 14200, "tokens_in": 1600, "tokens_out": 240, "last_at": ts(1), "recent_ms": [6000, 14200]}]}
HOSTED = {"running": [], "recent": [row(3, "79 site assistant", 10, model="openai/gpt-oss-20b", provider="api.groq.com",
                                        prompt="site_answer")],
          "models": [{"model": "openai/gpt-oss-20b", "provider": "api.groq.com", "calls_today": 1, "failures_today": 0,
                      "avg_ms": 900, "p95_ms": 900, "tokens_in": 500, "tokens_out": 60, "last_at": ts(10),
                      "recent_ms": [900]},
                     {"model": "mkt-writer", "provider": "ollama", "calls_today": 2, "failures_today": 0, "avg_ms": 4000,
                      "p95_ms": 20000, "tokens_in": 10, "tokens_out": 5, "last_at": ts(40), "recent_ms": [4000, 4000]}]}


@pytest.fixture
def gateways(monkeypatch):
    monkeypatch.setenv("ACTIVITY_GATEWAYS", f"verifier={VERIFIER},assistant={ASSISTANT},empty=")


def calendar_items():
    return [
        item(1, title="Five brew tips", status="draft", created_at=ts(5), notes=None),
        item(2, title="How we roast our decaf", status="approved", created_at=ts(60 * 24 * 30),
             notes=f"[{ts(60 * 24 * 30)}] draft -> in_review: gate ok\n[{ts(20)}] in_review -> approved: approved by alex"),
        item(3, title="Old post", status="published", created_at=ts(60 * 24 * 40),
             notes=f"[{ts(60 * 24 * 40)}] approved -> published: x"),
    ]


def mock_sources(mock, verifier=None, assistant=None):
    mock.get(f"{GW}/v1/activity").respond(json=MAIN)
    mock.get(f"{VERIFIER}/v1/activity").mock(side_effect=verifier or [httpx.Response(200, json=HOSTED)] * 50)
    mock.get(f"{ASSISTANT}/v1/activity").mock(side_effect=assistant or httpx.ConnectError("refused"))
    mock.get(f"{CAL}/items").respond(json=calendar_items())


def test_needs_login(client):
    r = client.get("/activity")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    r = client.get("/activity/data")
    assert r.status_code == 401 and r.json() == {"detail": "log in first"}


def test_data_merges_sources_and_sorts(gateways, authed, mock):
    c, _ = authed
    mock_sources(mock)
    d = c.get("/activity/data").json()
    texts = [e["text"] for e in d["timeline"]]
    assert texts[0] == "Social post writer asked mkt-writer (local) for social posts — 14.2 s, OK"
    assert "Draft ‘Five brew tips’ created for LinkedIn" in texts
    assert "You approved ‘How we roast our decaf’" in texts
    assert "Site assistant asked openai/gpt-oss-20b (hosted) for site answer — 14.2 s, OK" in texts
    assert "Claim checker asked mkt-writer (local) for claim details — 14.2 s, failed (timeout)" in texts
    assert not any("Old post" in t for t in texts)                 # older than ACTIVITY_DAYS
    assert not any("Sent for review" in t for t in texts)           # 30 days ago
    ats = [datetime.fromisoformat(e["at"].replace("Z", "+00:00")) for e in d["timeline"]]
    assert ats == sorted(ats, reverse=True)
    links = {e["text"]: e["link"] for e in d["timeline"]}
    assert links["You approved ‘How we roast our decaf’"] == "/items/2"
    # Now: the main gateway's running call, with its ability name and a local badge
    (now,) = d["now"]
    assert now["who"] == "Blog writer" and now["ability"] == 25 and now["local"] is True and now["elapsed_ms"] == 6100
    # Models: one card per (model, provider) across gateways
    models = {(m["model"], m["provider"]): m for m in d["models"]}
    w = models[("mkt-writer", "ollama")]
    assert w["calls_today"] == 4 and w["failures_today"] == 1 and w["avg_ms"] == 7000 and w["p95_ms"] == 20000
    assert w["tokens_in"] == 1610 and w["local"] is True and w["points"]
    assert models[("openai/gpt-oss-20b", "api.groq.com")]["local"] is False
    # Sources: the assistant gateway is down; the empty one is not listed at all
    assert d["sources"] == [{"name": "main gateway", "ok": True, "detail": "ok"},
                            {"name": "verifier gateway", "ok": True, "detail": "ok"},
                            {"name": "assistant gateway", "ok": False, "detail": "unreachable"},
                            {"name": "calendar", "ok": True, "detail": "ok"}]


def test_gateway_key_is_sent(gateways, authed, mock):
    c, _ = authed
    mock_sources(mock)
    c.get("/activity/data")
    req = next(call.request for call in mock.calls if call.request.url.path == "/v1/activity")
    assert req.headers["X-API-Key"] == KEYS["INTERNAL_API_KEY"]


def test_failing_sources_page_still_renders(gateways, authed, mock):
    c, _ = authed
    mock.get(f"{GW}/v1/activity").mock(side_effect=httpx.ConnectError("refused"))
    mock.get(f"{VERIFIER}/v1/activity").respond(500, text="boom http://verifier.internal:8000/x")
    mock.get(f"{ASSISTANT}/v1/activity").respond(200, text="not json")
    mock.get(f"{CAL}/items").mock(side_effect=httpx.ReadTimeout("slow"))
    r = c.get("/activity")
    assert r.status_code == 200
    assert "main gateway: unreachable" in r.text and "calendar: unreachable" in r.text
    assert "assistant gateway: answer is not JSON" in r.text
    assert "Idle — nothing running." in r.text and "No model calls yet today." in r.text
    d = c.get("/activity/data").json()
    assert [s["ok"] for s in d["sources"]] == [False, False, False, False]
    assert "verifier.internal" not in r.text + str(d)


def test_page_renders_all_sections(gateways, authed, mock):
    c, _ = authed
    mock_sources(mock)
    h = c.get("/activity").text
    assert 'aria-live="polite"' in h and "Blog writer" in h
    assert "Social post writer asked mkt-writer (local)" in h
    assert 'class="badge hosted"' in h and 'class="badge local"' in h
    assert "<polyline points=" in h
    assert 'href="/activity?ability=26#timeline"' in h
    assert 'src="/static/activity.js"' in h and 'id="act-data"' in h
    assert 'href="/activity" aria-current="page"' in h        # nav
    assert 'href="/activity"' in c.get("/more").text


def test_no_secret_or_internal_url_in_page_or_json(gateways, authed, mock):
    c, _ = authed
    mock_sources(mock)
    everything = c.get("/activity").text + c.get("/activity/data").text + c.get("/static/activity.js").text
    for secret in [*KEYS.values(), PASSWORD]:
        assert secret not in everything
    for url in [*URLS.values(), VERIFIER, ASSISTANT]:
        assert url.split("//")[1].split(":")[0] not in everything, url
    assert "X-API-Key" not in everything


def test_filters_server_side(gateways, authed, mock):
    c, _ = authed
    mock_sources(mock)
    get = lambda **p: c.get("/activity/data", params=p).json()["timeline"]  # noqa: E731
    assert {e["kind"] for e in get(filter="ai")} == {"ai"}
    assert {e["kind"] for e in get(filter="content")} == {"content"}
    errors = get(filter="errors")
    assert len(errors) == 1 and "failed (timeout)" in errors[0]["text"]
    only = get(ability="26")
    assert len(only) == 1 and only[0]["ability"] == 26
    assert len(get(filter="nonsense", ability="999")) == len(get())     # unknown values = no filter
    h = c.get("/activity", params={"ability": "44"}).text
    assert "Only <b id=\"act-ability-name\">Claim checker</b>" in h


def test_caller_to_ability():
    assert act.ability_for_caller("26 Social post writer") == (26, "Social post writer")
    assert act.ability_for_caller("44 claim checker") == (44, "Claim checker")
    assert act.ability_for_caller("72 voice interview") == (72, "Voice interview")
    assert act.ability_for_caller("unknown") == (None, "Unknown caller")
    assert act.ability_for_caller("") == (None, "Unknown caller")
    assert act.ability_for_caller("99 Something new") == (None, "Something new")
    assert act.ability_for_caller("eval run") == (None, "eval run")


def test_every_workflow_caller_maps_to_an_ability():
    """The X-Caller of every generated workflow that calls the gateway has a tile."""
    import json
    root = Path(__file__).resolve().parents[2]
    callers = set()
    for f in list(root.glob("[0-9][0-9]-wf-*/workflow.json")) + list(root.glob("[0-9][0-9]-*/n8n/workflow.json")):
        for n in json.loads(f.read_text())["nodes"]:
            for h in (n["parameters"].get("headerParameters") or {}).get("parameters", []):
                if h["name"] == "X-Caller":
                    callers.add(h["value"])
    assert len(callers) >= 20
    assert all(act.ability_for_caller(c)[0] is not None for c in callers), callers


def test_local_or_hosted():
    for p in ("ollama", "localhost", "host.docker.internal", "vllm", "10.0.0.5", "127.0.0.1", "box.local"):
        assert act.is_local(p), p
    for p in ("api.groq.com", "openrouter.ai", "8.8.8.8"):
        assert not act.is_local(p), p


def test_abilities_installed_by_profile_and_urls(monkeypatch):
    for k in ("ENGINE_URL", "CLIPS_URL", "VIDEO_URL", "ADS_URL", "AD_LIBRARY_URL", "FEED_URL"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("INSTALL_PROFILE", "core")
    s = config.load()
    tiles = {a["num"]: a for a in act.abilities_view(s, [row(1, "26 Social post writer", 3),
                                                          row(2, "26 Social post writer", 9, ok=False)])}
    assert tiles[26]["installed"] and tiles[26]["last_ok"] is True and tiles[26]["last_text"] == "OK"
    assert tiles[25]["installed"] and tiles[25]["last_at"] is None
    for n in (64, 65, 60, 71, 77, 78, 79, 83, 84, 87):
        assert not tiles[n]["installed"], n
    monkeypatch.setenv("INSTALL_PROFILE", "growth")
    monkeypatch.setenv("ENGINE_URL", "http://content-engine:8000")
    s = config.load()
    tiles = {a["num"]: a for a in act.abilities_view(s, [])}
    assert tiles[64]["installed"] and tiles[60]["installed"] and not tiles[71]["installed"] and not tiles[83]["installed"]
    monkeypatch.delenv("INSTALL_PROFILE")
    assert config.load().install_profile == "full"
    monkeypatch.setenv("INSTALL_PROFILE", "growth,full")
    assert config.load().install_profile == "full"


def test_activity_gateways_env():
    assert config._gateways("") == []
    assert config._gateways("verifier=http://a:8000/, http://b:8000,assistant=,junk") == [
        ("verifier", "http://a:8000"), ("gateway 3", "http://b:8000")]


def test_content_events_sentences():
    since = datetime.now(timezone.utc) - timedelta(days=7)
    ev = act.content_events([
        item(5, title="Launch day", channel="x", status="published", created_at=ts(90),
             notes=f"[{ts(80)}] draft -> in_review: ok\n[{ts(70)}] in_review -> rejected: too long\n"
                   f"[{ts(60)}] approved -> published: posted\n[{ts(50)}] a free note"),
    ], since)
    assert [e["text"] for e in ev] == ["Draft ‘Launch day’ created for X", "Sent for review: ‘Launch day’",
                                       "You rejected ‘Launch day’", "Published ‘Launch day’ on X"]
    assert [e["ok"] for e in ev] == [True, True, False, True]


def test_js_helpers_in_node():
    node = os.environ.get("NODE") or shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    here = Path(__file__).parent
    r = subprocess.run([node, str(here / "activity_js_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_status_without_a_note_still_shows():
    """A status set outside the control room (API, n8n) leaves no transition note: the current
    approved / published / rejected status still appears once, at the item's last update."""
    now = datetime.now(timezone.utc)
    at = (now - timedelta(minutes=5)).isoformat(timespec="seconds").replace("+00:00", "Z")
    items = [item(1, title="Decaf", status="approved", created_at=at, updated_at=at, notes=None),
             item(2, title="Tips", status="in_review", created_at=at, updated_at=at, notes=None)]
    texts = [e["text"] for e in act.content_events(items, now - timedelta(days=1))]
    assert "You approved ‘Decaf’" in texts
    assert not any("Tips" in t and "approved" in t for t in texts)
    noted = item(3, title="Noted", status="approved", created_at=at, updated_at=at,
                 notes=f"[{at}] in_review -> approved")
    once = [e for e in act.content_events([noted], now - timedelta(days=1)) if "approved" in e["text"]]
    assert len(once) == 1   # the note already says it: not twice


def test_chat_agent_call_through_the_gateway_reads_as_a_chat_step():
    e = act.ai_event(row(12, "24 Chat agent", 1, model="mkt-agent", prompt="chat"), "main")
    assert e["ability"] == 24
    assert e["text"] == "Chat agent asked mkt-agent (local) for a chat step — 14.2 s, OK"
    (card,) = act.now_cards([("main", {"running": [{"id": 13, "caller": "24 Chat agent", "prompt": "chat",
                                                       "model": "mkt-agent", "provider": "ollama", "elapsed_ms": 900}]})])
    assert card["ability"] == 24 and card["prompt"] == "a chat step"
    assert act.prompt_words("social_posts") == "social posts"
