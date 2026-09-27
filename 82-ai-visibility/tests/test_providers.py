"""Response formats are copied from each provider's API reference (checked 2026-09)."""
import json

import httpx
import pytest
import respx

from app import providers as pv

OPENAI_BODY = {
    "id": "resp_1", "model": "gpt-5-mini-2025-08-07",
    "output": [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "web_search_call", "id": "ws_1", "status": "completed",
         "action": {"type": "search", "query": "best coffee subscription remote team",
                    "sources": [{"type": "url", "url": "https://consulted.example.org/"}]}},
        {"type": "message", "id": "msg_1", "role": "assistant", "content": [{
            "type": "output_text", "text": "1. Trade Coffee\n2. Atlas Coffee Club",
            "annotations": [
                {"type": "url_citation", "start_index": 3, "end_index": 15, "url": "https://www.drinktrade.com/?utm_source=openai",
                 "title": "Trade Coffee"},
                {"type": "url_citation", "start_index": 19, "end_index": 36, "url": "https://atlascoffeeclub.com/",
                 "title": "Atlas"}]}]}],
    "usage": {"input_tokens": 812, "output_tokens": 420, "total_tokens": 1232},
}
PPLX_AGENT_BODY = {
    "id": "a1", "model": "perplexity/sonar", "status": "completed",
    "output": [
        {"type": "search_results", "queries": ["coffee subscription"],
         "results": [{"id": 1, "title": "Best subscriptions", "url": "https://www.wired.com/coffee", "snippet": "…",
                      "source": "web"}]},
        {"type": "message", "content": [{"type": "output_text", "text": "Atlas Coffee Club is popular [1].",
                                         "annotations": [{"type": "url_citation", "url": "https://atlascoffeeclub.com/plans",
                                                          "title": "Plans"}]}]}],
    "usage": {"input_tokens": 4718, "output_tokens": 450, "total_tokens": 5168,
              "cost": {"currency": "USD", "input_cost": 0.00118, "output_cost": 0.001125, "tool_calls_cost": 0.0025,
                       "total_cost": 0.004805}},
}
PPLX_LEGACY_BODY = {
    "model": "sonar", "choices": [{"message": {"role": "assistant", "content": "Trade Coffee [1]"}}],
    "citations": ["https://drinktrade.com/"],
    "search_results": [{"title": "Trade", "url": "https://drinktrade.com/", "date": "2026-01-01"}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "cost": {"total_cost": 0.00503}},
}
GEMINI_BODY = {
    "candidates": [{
        "content": {"role": "model", "parts": [{"text": "Consider Atlas Coffee Club "}, {"text": "or Trade."}]},
        "groundingMetadata": {
            "webSearchQueries": ["best coffee subscription"],
            "searchEntryPoint": {"renderedContent": "<div>…</div>"},
            "groundingChunks": [
                {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQ1", "title": "atlascoffeeclub.com"}},
                {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQ2", "title": "nytimes.com"}}],
            "groundingSupports": [{"segment": {"startIndex": 0, "endIndex": 26, "text": "Consider Atlas Coffee Club"},
                                   "groundingChunkIndices": [0]}]}}],
    "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 120, "thoughtsTokenCount": 300,
                      "toolUsePromptTokenCount": 500, "totalTokenCount": 929},
    "modelVersion": "gemini-2.5-flash",
}
GROQ_BODY = {
    "id": "chatcmpl-1", "model": "openai/gpt-oss-120b",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Try Atlas Coffee Club (https://atlascoffeeclub.com)."}}],
    "usage": {"prompt_tokens": 80, "completion_tokens": 310, "total_tokens": 390},
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for p in pv.PROVIDERS.values():
        monkeypatch.delenv(p["key_env"], raising=False)
    for k in ("VIS_GEMINI_TERMS_ACCEPTED", "VIS_COUNTRY", "VIS_GROQ_DAILY_CALLS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(pv.time, "sleep", lambda s: None)


def test_parse_openai_url_citations():
    a = pv.parse_openai(OPENAI_BODY)
    assert a.text == "1. Trade Coffee\n2. Atlas Coffee Club"
    assert [c["domain"] for c in a.citations] == ["drinktrade.com", "atlascoffeeclub.com"]
    assert (a.input_tokens, a.output_tokens, a.searches) == (812, 420, 1)
    assert "consulted.example.org" not in json.dumps(a.citations)   # consulted, not cited


def test_parse_perplexity_agent_and_legacy():
    a = pv.parse_perplexity(PPLX_AGENT_BODY)
    assert a.text == "Atlas Coffee Club is popular [1]."
    assert [c["url"] for c in a.citations] == ["https://www.wired.com/coffee", "https://atlascoffeeclub.com/plans"]
    assert a.reported_cost == 0.004805 and a.input_tokens == 4718 and a.searches == 1
    assert pv.cost("perplexity", a) == 0.004805     # the API's own cost wins
    b = pv.parse_perplexity(PPLX_LEGACY_BODY)
    assert b.text == "Trade Coffee [1]" and [c["url"] for c in b.citations] == ["https://drinktrade.com/"]
    assert (b.input_tokens, b.output_tokens) == (10, 20)


def test_parse_gemini_grounding_uses_title_domain():
    a = pv.parse_gemini(GEMINI_BODY)
    assert a.text == "Consider Atlas Coffee Club or Trade."
    assert [c["domain"] for c in a.citations] == ["atlascoffeeclub.com", "nytimes.com"]
    assert a.output_tokens == 420 and a.searches == 1
    assert pv.parse_gemini({"candidates": [{"content": {"parts": [{"text": "x"}]}}]}).searches == 0


def test_parse_groq_has_no_citations():
    a = pv.parse_groq(GROQ_BODY)
    assert a.citations == [] and a.searches == 0
    assert a.text.startswith("Try Atlas") and (a.input_tokens, a.output_tokens) == (80, 310)
    assert pv.cost("groq_knowledge", a) == round(80 / 1e6 * 0.15 + 310 / 1e6 * 0.60, 6)


def test_enabled_only_with_key_and_gemini_needs_terms_flag(monkeypatch):
    assert pv.enabled() == []
    monkeypatch.setenv("VIS_GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("VIS_GEMINI_API_KEY", "AIza-test")
    assert pv.enabled() == ["groq_knowledge"]
    monkeypatch.setenv("VIS_GEMINI_TERMS_ACCEPTED", "true")
    assert pv.enabled() == ["gemini_grounded", "groq_knowledge"]
    view = json.dumps(pv.describe())
    assert "gsk-test" not in view and "AIza-test" not in view


def test_requests_put_keys_in_headers_only(monkeypatch):
    monkeypatch.setenv("VIS_GEMINI_API_KEY", "AIza-secret")
    monkeypatch.setenv("VIS_OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("VIS_PERPLEXITY_API_KEY", "pplx-secret")
    monkeypatch.setenv("VIS_GROQ_API_KEY", "gsk-secret")
    monkeypatch.setenv("VIS_COUNTRY", "us")
    url, h, body = pv.request_for("gemini_grounded", "q?")
    assert "AIza-secret" not in url and h == {"x-goog-api-key": "AIza-secret"}
    assert body["tools"] == [{"google_search": {}}]
    url, h, body = pv.request_for("openai_search", "q?")
    assert url.endswith("/responses") and body["tools"][0] == {"type": "web_search",
                                                                "user_location": {"type": "approximate", "country": "US"}}
    url, h, body = pv.request_for("perplexity", "q?")
    assert url == "https://api.perplexity.ai/v1/agent" and body["tools"] == [{"type": "web_search"}]
    url, h, body = pv.request_for("groq_knowledge", "q?")
    assert body["reasoning_effort"] == "low" and "tools" not in body


@respx.mock
def test_ask_each_provider(monkeypatch):
    for n, p in pv.PROVIDERS.items():
        monkeypatch.setenv(p["key_env"], f"key-{n}")
    monkeypatch.setenv("VIS_GEMINI_TERMS_ACCEPTED", "true")
    respx.post("https://api.openai.com/v1/responses").respond(200, json=OPENAI_BODY)
    respx.post("https://api.perplexity.ai/v1/agent").respond(200, json=PPLX_AGENT_BODY)
    respx.post("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent").respond(200, json=GEMINI_BODY)
    groq = respx.post("https://api.groq.com/openai/v1/chat/completions").respond(200, json=GROQ_BODY)
    for n in pv.NAMES:
        assert pv.ask(n, "best coffee subscription?").text
    assert groq.calls.last.request.headers["authorization"] == "Bearer key-groq_knowledge"


@respx.mock
def test_ask_errors_never_carry_the_body(monkeypatch):
    monkeypatch.setenv("VIS_GROQ_API_KEY", "gsk-SECRET-123456")
    route = respx.post("https://api.groq.com/openai/v1/chat/completions")
    route.side_effect = [httpx.Response(429, headers={"retry-after": "1"}),
                         httpx.Response(401, json={"error": {"message": "Invalid API Key gsk-SECRET-123456"}})]
    with pytest.raises(pv.ProviderError) as e:
        pv.ask("groq_knowledge", "q?")
    assert str(e.value) == "HTTP 401" and route.call_count == 2


def test_ask_without_key_refuses():
    with pytest.raises(pv.ProviderError):
        pv.ask("openai_search", "q?")
