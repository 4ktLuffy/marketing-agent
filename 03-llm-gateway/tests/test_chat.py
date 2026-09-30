"""POST /v1/chat/completions and GET /v1/models: the OpenAI-compatible passthrough the n8n chat
agent (24) uses. Body and answer pass through unchanged (tool_calls, SSE); key by X-API-Key or
Bearer; allowlist; upstream errors by kind; activity metadata only."""
import json
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main
from app.activity import ActivityLog
from app.prompts import PromptStore

FIXTURES = Path(__file__).parent / "fixtures" / "prompts"
OLLAMA = "http://ollama.test"
HOSTED = "https://api.example-llm.test/openai/v1"
SENTINEL = "zq-SENTINEL-9182-chat"
KEY = "k-internal-123"


@pytest.fixture(autouse=True)
def configure(monkeypatch):
    monkeypatch.setattr(main, "store", PromptStore(str(FIXTURES)))
    monkeypatch.setattr(main, "OLLAMA_URL", OLLAMA)
    monkeypatch.setattr(main, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(main, "MODEL", "mkt-writer")
    monkeypatch.setattr(main, "ALLOWED_MODELS", {"mkt-agent"})
    monkeypatch.setattr(main, "activity", ActivityLog(20))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)


client = TestClient(main.app)
BEARER = {"Authorization": f"Bearer {KEY}"}

TOOL_CALL_REPLY = (
    b'{"id":"chatcmpl-1","object":"chat.completion","created":1,"model":"mkt-agent",'
    b'"choices":[{"index":0,"message":{"role":"assistant","content":"","tool_calls":[{"id":"call_ab12",'
    b'"index":0,"type":"function","function":{"name":"write_social_posts","arguments":"{\\"topic\\":\\"decaf\\",'
    b'\\"channels\\":\\"x, linkedin\\"}"}}]},"finish_reason":"tool_calls"}],'
    b'"usage":{"prompt_tokens":2100,"completion_tokens":41,"total_tokens":2141}}')


def body(**kw):
    b = {"model": "mkt-agent", "temperature": 0.2,
         "messages": [{"role": "system", "content": "You are the agent."},
                      {"role": "user", "content": f"write posts about decaf {SENTINEL}"}],
         "tools": [{"type": "function", "function": {"name": "write_social_posts", "description": "posts",
                                                      "parameters": {"type": "object", "properties": {}}}}]}
    b.update(kw)
    return b


def post(b, headers=None):
    return client.post("/v1/chat/completions", json=b,
                       headers={**BEARER, "X-Caller": "24 Chat agent"} if headers is None else headers)


def rows():
    return client.get("/v1/activity", headers={"X-API-Key": KEY}).json()["recent"]


@respx.mock
def test_non_stream_passthrough_keeps_tool_calls_byte_for_byte():
    route = respx.post(f"{OLLAMA}/v1/chat/completions").respond(
        200, content=TOOL_CALL_REPLY, headers={"content-type": "application/json"})
    r = post(body())
    assert r.status_code == 200
    assert r.content == TOOL_CALL_REPLY
    sent = json.loads(route.calls[0].request.content)
    assert sent == body()                           # forwarded unchanged: tools, messages, temperature
    assert "authorization" not in route.calls[0].request.headers   # our key never goes to Ollama
    (row,) = rows()
    assert row["prompt"] == "chat" and row["caller"] == "24 Chat agent" and row["model"] == "mkt-agent"
    assert row["provider"] == "ollama" and row["ok"] is True
    assert row["tokens_in"] == 2100 and row["tokens_out"] == 41


SSE = (b'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant",'
       b'"tool_calls":[{"index":0,"id":"call_x","type":"function","function":{"name":"write_blog_post",'
       b'"arguments":""}}]},"finish_reason":null}]}\n\n'
       b'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":'
       b'[{"index":0,"function":{"arguments":"{\\"topic\\":\\"' + SENTINEL.encode() + b'\\"}"}}]},'
       b'"finish_reason":null}]}\n\n'
       b'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},'
       b'"finish_reason":"tool_calls"}]}\n\n'
       b'data: {"id":"c1","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":900,'
       b'"completion_tokens":17,"total_tokens":917}}\n\n'
       b'data: [DONE]\n\n')


@respx.mock
def test_stream_passthrough_unchanged_with_usage_from_final_chunk():
    parts = [SSE[:37], SSE[37:300], SSE[300:]]      # split mid-line: the usage parse must not care
    route = respx.post(f"{OLLAMA}/v1/chat/completions").respond(
        200, stream=httpx.ByteStream(b"".join(parts)), headers={"content-type": "text/event-stream"})
    with client.stream("POST", "/v1/chat/completions", json=body(stream=True,
                       stream_options={"include_usage": True}), headers={**BEARER, "X-Caller": "24 Chat agent"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        got = b"".join(r.iter_bytes())
    assert got == SSE
    assert json.loads(route.calls[0].request.content)["stream"] is True
    (row,) = rows()
    assert row["ok"] is True and row["tokens_in"] == 900 and row["tokens_out"] == 17
    assert row["caller"] == "24 Chat agent"


@respx.mock
def test_stream_without_usage_still_logged():
    respx.post(f"{OLLAMA}/v1/chat/completions").respond(
        200, content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n',
        headers={"content-type": "text/event-stream"})
    r = post(body(stream=True))
    assert r.status_code == 200 and r.content.endswith(b"data: [DONE]\n\n")
    (row,) = rows()
    assert row["ok"] is True and row["tokens_in"] is None


@respx.mock
def test_auth_bearer_or_x_api_key_else_401():
    respx.post(f"{OLLAMA}/v1/chat/completions").respond(200, content=TOOL_CALL_REPLY)
    assert post(body(), headers={"Authorization": f"Bearer {KEY}"}).status_code == 200
    assert post(body(), headers={"authorization": f"bearer {KEY}"}).status_code == 200
    assert post(body(), headers={"X-API-Key": KEY}).status_code == 200
    assert post(body(), headers={}).status_code == 401
    assert post(body(), headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert post(body(), headers={"Authorization": KEY}).status_code == 401        # no "Bearer "
    assert post(body(), headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/v1/models").status_code == 401
    assert len(rows()) == 3                                                       # refusals not logged


@respx.mock
def test_model_allowlist_refuses_and_default_model_is_mapped():
    route = respx.post(f"{OLLAMA}/v1/chat/completions").respond(200, content=TOOL_CALL_REPLY)
    r = post(body(model="gpt-4o"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "model_not_allowed"
    assert not route.called and rows() == []
    b = body()
    del b["model"]
    assert post(b).status_code == 200
    assert json.loads(route.calls[0].request.content)["model"] == "mkt-writer"   # MODEL, like /v1/run


def test_bad_bodies_and_size_cap(monkeypatch):
    assert post({"model": "mkt-agent"}).status_code == 400
    assert post({"model": "mkt-agent", "messages": []}).status_code == 400
    assert client.post("/v1/chat/completions", content=b"{nope", headers=BEARER).status_code == 400
    monkeypatch.setattr(main, "MAX_CHAT_BYTES", 500)
    assert post(body(messages=[{"role": "user", "content": "x" * 600}])).status_code == 413
    assert rows() == []


@respx.mock
def test_upstream_errors_status_and_kind_only_in_log():
    respx.post(f"{OLLAMA}/v1/chat/completions").mock(side_effect=[
        httpx.Response(500, text=f"boom {SENTINEL}"),
        httpx.Response(400, json={"error": {"message": f"bad {SENTINEL}"}}),
        httpx.Response(429, text="slow down", headers={"retry-after": "7"}),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("refused"),
    ])
    r = post(body())
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_5xx"
    r = post(body())
    assert r.status_code == 400 and SENTINEL in r.text        # the caller sees the upstream reason
    r = post(body())
    assert r.status_code == 429 and r.headers["retry-after"] == "7"
    assert post(body()).status_code == 504
    assert post(body()).status_code == 502
    assert [x["error"] for x in rows()] == ["unreachable", "timeout", "rate_limited", "upstream_4xx", "upstream_5xx"]
    assert SENTINEL not in client.get("/v1/activity", headers={"X-API-Key": KEY}).text


@respx.mock
def test_no_message_or_output_content_in_activity():
    respx.post(f"{OLLAMA}/v1/chat/completions").mock(side_effect=[
        httpx.Response(200, content=TOOL_CALL_REPLY.replace(b"decaf", SENTINEL.encode())),
        httpx.Response(200, content=SSE, headers={"content-type": "text/event-stream"})])
    assert post(body()).status_code == 200
    assert post(body(stream=True)).status_code == 200
    text = client.get("/v1/activity", headers={"X-API-Key": KEY}).text
    assert SENTINEL not in text and "decaf" not in text and "You are the agent" not in text
    assert "write_social_posts" not in text and "write_blog_post" not in text
    assert [r["prompt"] for r in json.loads(text)["recent"]] == ["chat", "chat"]


@respx.mock
def test_hosted_provider_gets_its_own_key_and_host_only_in_log(monkeypatch):
    monkeypatch.setattr(main, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(main, "OPENAI_BASE_URL", HOSTED)
    monkeypatch.setattr(main, "OPENAI_API_KEY", "sk-hosted-0000")
    route = respx.post(f"{HOSTED}/chat/completions").respond(200, content=TOOL_CALL_REPLY)
    assert post(body()).status_code == 200
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-hosted-0000"   # not our key
    text = client.get("/v1/activity", headers={"X-API-Key": KEY}).text
    assert json.loads(text)["recent"][0]["provider"] == "api.example-llm.test"
    assert "sk-hosted" not in text and KEY not in text


def test_hosted_provider_not_configured(monkeypatch):
    monkeypatch.setattr(main, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(main, "OPENAI_BASE_URL", "")
    r = post(body())
    assert r.status_code == 502 and r.json()["error"]["code"] == "not_configured"
    assert rows()[0]["error"] == "not_configured"


@respx.mock
def test_no_caller_header_is_unknown():
    respx.post(f"{OLLAMA}/v1/chat/completions").respond(200, content=TOOL_CALL_REPLY)
    assert post(body(), headers=BEARER).status_code == 200
    assert rows()[0]["caller"] == "unknown"


def test_models_lists_the_allowlist_for_n8n_credential_test():
    r = client.get("/v1/models", headers=BEARER)
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    ids = [m["id"] for m in data["data"]]
    assert "mkt-agent" in ids and "mkt-writer" in ids and ids == sorted(ids)
