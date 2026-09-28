"""GET /v1/activity: calls in flight and done, error kinds only, no content, caller label,
ring size, key, per-model totals."""
import json
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main
from app.activity import ActivityLog, clean_caller, provider_label
from app.prompts import PromptStore

FIXTURES = Path(__file__).parent / "fixtures" / "prompts"
OLLAMA = "http://ollama.test"
SENTINEL = "zq-SENTINEL-4471-content"


@pytest.fixture(autouse=True)
def configure(monkeypatch):
    monkeypatch.setattr(main, "store", PromptStore(str(FIXTURES)))
    monkeypatch.setattr(main, "OLLAMA_URL", OLLAMA)
    monkeypatch.setattr(main, "BRAND_URL", "")
    monkeypatch.setattr(main, "LEARNING_URL", "")
    monkeypatch.setattr(main, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(main, "_policy_cache", {"at": None, "policy": None})
    monkeypatch.setattr(main, "activity", ActivityLog(5))
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)


client = TestClient(main.app)


def reply(content: str, pin=0, pout=0) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content},
                                     "prompt_eval_count": pin, "eval_count": pout})


def run(caller=None, topic="coffee"):
    return client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": topic}},
                       headers={"X-Caller": caller} if caller is not None else {})


@respx.mock
def test_in_flight_call_appears_then_moves_to_recent():
    seen = {}

    def answer(request):
        seen["during"] = main.activity.snapshot()
        return reply('{"headlines": ["A", "B"]}', 120, 30)
    respx.post(f"{OLLAMA}/api/chat").mock(side_effect=answer)
    assert run("26 Social writer").status_code == 200
    during = seen["during"]
    assert [c["caller"] for c in during["running"]] == ["26 Social writer"]
    assert during["running"][0]["prompt"] == "headline" and during["running"][0]["provider"] == "ollama"
    assert during["recent"] == []
    after = client.get("/v1/activity").json()
    assert after["running"] == []
    (row,) = after["recent"]
    assert row["ok"] is True and row["error"] is None and row["retries"] == 0
    assert row["tokens_in"] == 120 and row["tokens_out"] == 30
    assert row["caller"] == "26 Social writer" and row["model"] == main.MODEL
    assert row["started_at"].endswith("Z") and row["duration_ms"] >= 0
    (m,) = after["models"]
    assert m["calls_today"] == 1 and m["failures_today"] == 0 and m["tokens_in"] == 120
    assert m["avg_ms"] is not None and m["p95_ms"] is not None and len(m["recent_ms"]) == 1


@respx.mock
def test_errors_recorded_by_kind_only():
    respx.post(f"{OLLAMA}/api/chat").mock(side_effect=[
        httpx.ReadTimeout("slow"), httpx.ConnectError("refused"),
        httpx.Response(503, text=f"overloaded {SENTINEL}"),
        reply("not json"), reply("not json"), reply("not json"),
        reply('{"headlines": ["x"]}'), reply('{"headlines": ["x"]}'), reply('{"headlines": ["A", "B"]}'),
    ])
    for _ in range(4):
        assert run().status_code == 502
    assert run().status_code == 200
    rows = client.get("/v1/activity").json()["recent"]
    assert [r["error"] for r in rows] == [None, "invalid_json", "upstream_5xx", "unreachable", "timeout"]
    assert rows[1]["retries"] == 2 and rows[0]["retries"] == 2
    m = client.get("/v1/activity").json()["models"][0]
    assert m["calls_today"] == 5 and m["failures_today"] == 4


@respx.mock
def test_no_prompt_text_vars_or_output_in_the_log():
    respx.post(f"{OLLAMA}/api/chat").mock(side_effect=[
        reply(json.dumps({"headlines": [SENTINEL, "B"]})), httpx.Response(500, text=SENTINEL),
        reply(SENTINEL), reply(SENTINEL), reply(SENTINEL)])
    assert run(topic=SENTINEL).status_code == 200
    assert run(topic=SENTINEL).status_code == 502
    assert run(topic=SENTINEL).status_code == 502
    body = client.get("/v1/activity").text
    assert SENTINEL not in body and "coffee" not in body
    assert "Write headlines" not in body


def test_caller_is_sanitised():
    assert clean_caller(None) == "unknown" and clean_caller("   ") == "unknown"
    assert clean_caller("26 Social writer") == "26 Social writer"
    assert clean_caller("<script>alert(1)</script>") == "scriptalert(1)/script"
    assert clean_caller("a\nb\tc\x00d") == "a b cd"
    assert len(clean_caller("x" * 500)) == 80
    assert clean_caller("79 site assistant · chat") == "79 site assistant · chat"


@respx.mock
def test_caller_header_absent_is_unknown_and_long_one_is_cut():
    respx.post(f"{OLLAMA}/api/chat").mock(return_value=reply('{"headlines": ["A", "B"]}'))
    run()
    run("y" * 300 + '"<>')
    rows = client.get("/v1/activity").json()["recent"]
    assert rows[1]["caller"] == "unknown"
    assert rows[0]["caller"] == "y" * 80


@respx.mock
def test_ring_size_respected_and_since_filters():
    respx.post(f"{OLLAMA}/api/chat").mock(return_value=reply('{"headlines": ["A", "B"]}'))
    for i in range(8):
        run(f"caller {i}")
    data = client.get("/v1/activity").json()
    assert len(data["recent"]) == 5 and data["size"] == 5
    assert [r["caller"] for r in data["recent"]] == [f"caller {i}" for i in (7, 6, 5, 4, 3)]
    assert data["models"][0]["calls_today"] == 8          # today's totals are not limited by the ring
    last = data["last_id"]
    assert client.get("/v1/activity", params={"since": last}).json()["recent"] == []
    assert len(client.get("/v1/activity", params={"since": last - 2}).json()["recent"]) == 2
    assert len(client.get("/v1/activity", params={"limit": 1}).json()["recent"]) == 1


def test_key_required(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "k1")
    assert client.get("/v1/activity").status_code == 401
    assert client.get("/v1/activity", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/v1/activity", headers={"X-API-Key": "k1"}).status_code == 200


def test_refused_requests_are_not_logged():
    assert client.post("/v1/run", json={"prompt": "nope"}).status_code == 404
    assert client.post("/v1/run", json={"prompt": "headline", "vars": {}}).status_code == 422
    assert client.get("/v1/activity").json()["recent"] == []


@respx.mock
def test_hosted_provider_is_the_host_only(monkeypatch):
    monkeypatch.setattr(main, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(main, "OPENAI_BASE_URL", "https://api.example-llm.test/openai/v1")
    monkeypatch.setattr(main, "OPENAI_API_KEY", "sk-secret-0000")
    respx.post("https://api.example-llm.test/openai/v1/chat/completions").respond(json={
        "choices": [{"message": {"content": '{"headlines": ["A", "B"]}'}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 9}})
    assert run("44 claim checker").status_code == 200
    body = client.get("/v1/activity").text
    row = json.loads(body)["recent"][0]
    assert row["provider"] == "api.example-llm.test" and row["tokens_in"] == 50 and row["tokens_out"] == 9
    assert "sk-secret" not in body and "/openai/v1" not in body
    assert provider_label("ollama", "https://x.test") == "ollama"


def test_today_totals_reset_on_a_new_utc_day(monkeypatch):
    log = ActivityLog(10)
    cid = log.start("headline", "a", "m", "ollama")
    log.finish(cid, ok=False, error="timeout")
    assert log.snapshot()["models"][0]["failures_today"] == 1
    log._day = "2000-01-01"   # yesterday's totals
    snap = log.snapshot()
    assert snap["models"][0]["calls_today"] == 0 and snap["models"][0]["recent_ms"]   # sparkline kept
