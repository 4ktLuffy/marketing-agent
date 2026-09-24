import json
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main
from app.prompts import PromptStore

FIXTURES = Path(__file__).parent / "fixtures" / "prompts"
OLLAMA = "http://ollama.test"


@pytest.fixture(autouse=True)
def configure(monkeypatch):
    monkeypatch.setattr(main, "store", PromptStore(str(FIXTURES)))
    monkeypatch.setattr(main, "OLLAMA_URL", OLLAMA)
    monkeypatch.setattr(main, "BRAND_URL", "")
    monkeypatch.setattr(main, "LEARNING_URL", "")


client = TestClient(main.app)


def reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


def test_lists_prompts():
    names = {p["name"]: p for p in client.get("/v1/prompts").json()}
    assert names["headline"]["required_vars"] == ["topic"]
    assert names["headline"]["optional_vars"] == ["tone"]


def test_unknown_prompt_404():
    assert client.post("/v1/run", json={"prompt": "nope"}).status_code == 404


def test_missing_var_422():
    r = client.post("/v1/run", json={"prompt": "headline", "vars": {}})
    assert r.status_code == 422
    assert "topic" in r.json()["detail"]


@respx.mock
def test_json_output_passes_schema_on_first_try():
    route = respx.post(f"{OLLAMA}/api/chat").mock(
        return_value=reply('{"headlines": ["A", "B"]}')
    )
    r = client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee", "tone": "dry"}})
    assert r.status_code == 200
    assert r.json()["output"] == {"headlines": ["A", "B"]}
    assert r.json()["attempts"] == 1
    sent = json.loads(route.calls[0].request.content)
    assert sent["format"]["required"] == ["headlines"]
    assert "in a dry tone" in sent["messages"][-1]["content"]


@respx.mock
def test_invalid_json_is_retried_with_feedback():
    route = respx.post(f"{OLLAMA}/api/chat").mock(
        side_effect=[reply('{"headlines": ["only one"]}'), reply('```json\n{"headlines": ["A", "B"]}\n```')]
    )
    r = client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    assert r.status_code == 200
    assert r.json()["attempts"] == 2
    retry_msgs = json.loads(route.calls[1].request.content)["messages"]
    assert "rejected" in retry_msgs[-1]["content"]
    assert retry_msgs[-2]["role"] == "assistant"


@respx.mock
def test_gives_up_after_max_attempts_with_502():
    respx.post(f"{OLLAMA}/api/chat").mock(return_value=reply("not json at all"))
    r = client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    assert r.status_code == 502
    assert "not valid JSON" in r.json()["detail"]


@respx.mock
def test_text_max_chars_enforced_and_think_tags_stripped():
    respx.post(f"{OLLAMA}/api/chat").mock(
        side_effect=[reply("x" * 41), reply("<think>hmm</think>Wake up better.")]
    )
    r = client.post("/v1/run", json={"prompt": "tagline", "vars": {"product": "beans"}})
    assert r.status_code == 200
    assert r.json()["output"] == "Wake up better."
    assert r.json()["attempts"] == 2


@respx.mock
def test_ollama_down_is_502():
    respx.post(f"{OLLAMA}/api/chat").mock(side_effect=httpx.ConnectError("refused"))
    r = client.post("/v1/run", json={"prompt": "tagline", "vars": {"product": "beans"}})
    assert r.status_code == 502
    assert "unreachable" in r.json()["detail"]


@respx.mock
def test_brand_summary_injected(monkeypatch):
    monkeypatch.setattr(main, "BRAND_URL", "http://brand.test")
    main._brand_cache.update(at=None, summary="")
    respx.get("http://brand.test/profile/summary").mock(
        return_value=httpx.Response(200, json={"summary": "Northwind Roasters"})
    )
    route = respx.post(f"{OLLAMA}/api/chat").mock(return_value=reply('{"headlines": ["A", "B"]}'))
    client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    system = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert system == "Brand: Northwind Roasters"


def test_template_cannot_reach_python_internals(tmp_path):
    (tmp_path / "evil.yaml").write_text(
        "name: evil\nvars: {x: required}\ntemplate: \"{{ x.__class__.__mro__ }}\"\n"
    )
    prompt = PromptStore(str(tmp_path)).all()["evil"]
    with pytest.raises(Exception):
        prompt.render({"x": "a"})


@respx.mock
def test_text_output_unwraps_whole_answer_code_fence():
    respx.post(f"{OLLAMA}/api/chat").mock(return_value=reply("```markdown\nWake up.\n```"))
    r = client.post("/v1/run", json={"prompt": "tagline", "vars": {"product": "beans"}})
    assert r.json()["output"] == "Wake up."


@respx.mock
def test_text_output_keeps_inner_code_blocks():
    body = "Intro\n```bash\ncurl x\n```\nOutro"
    respx.post(f"{OLLAMA}/api/chat").mock(return_value=reply(body))
    r = client.post("/v1/run", json={"prompt": "tagline", "vars": {"product": "beans"}})
    assert r.json()["output"] == body


# ---------- learned rules (deploy 46) appended to the brand text

LEARNING = "http://learning.test"


def brand_and_learning(monkeypatch, learning_response):
    monkeypatch.setattr(main, "BRAND_URL", "http://brand.test")
    monkeypatch.setattr(main, "LEARNING_URL", LEARNING)
    main._brand_cache.update(at=None, summary="")
    main._learning_cache.update(at=None, summary="")
    respx.get("http://brand.test/profile/summary").mock(
        return_value=httpx.Response(200, json={"summary": "Northwind Roasters"})
    )
    learning = respx.get(f"{LEARNING}/rules/summary").mock(**learning_response)
    chat = respx.post(f"{OLLAMA}/api/chat").mock(return_value=reply('{"headlines": ["A", "B"]}'))
    return learning, chat


def system_of(route, n=0):
    return json.loads(route.calls[n].request.content)["messages"][0]["content"]


@respx.mock
def test_learned_rules_appended_after_brand(monkeypatch):
    summary = "Rules learned from your edits:\n- No exclamation marks.\n- [linkedin] Max 3 hashtags."
    learning, chat = brand_and_learning(monkeypatch, {
        "return_value": httpx.Response(200, json={"summary": summary, "count": 2})})
    r = client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    assert r.status_code == 200
    assert system_of(chat) == f"Brand: Northwind Roasters\n\n{summary}"
    # cached like the brand summary: a second call does not hit the learning service
    client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    assert learning.call_count == 1


@respx.mock
def test_learning_down_leaves_brand_unchanged(monkeypatch):
    _, chat = brand_and_learning(monkeypatch, {"side_effect": httpx.ConnectError("refused")})
    r = client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    assert r.status_code == 200
    assert system_of(chat) == "Brand: Northwind Roasters"


@respx.mock
def test_learning_error_status_keeps_last_good_summary(monkeypatch):
    _, chat = brand_and_learning(monkeypatch, {"side_effect": [
        httpx.Response(200, json={"summary": "Rules learned from your edits:\n- Be brief."}),
        httpx.Response(500),
    ]})
    client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    main._learning_cache["at"] = 0.0  # expire the cache; the refresh fails
    r = client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    assert r.status_code == 200
    assert system_of(chat, 1).endswith("\n\nRules learned from your edits:\n- Be brief.")


@respx.mock
def test_empty_learning_summary_adds_no_blank_lines(monkeypatch):
    _, chat = brand_and_learning(monkeypatch, {
        "return_value": httpx.Response(200, json={"summary": "", "count": 0})})
    client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    assert system_of(chat) == "Brand: Northwind Roasters"


@respx.mock
def test_caller_brand_is_not_touched_by_learning(monkeypatch):
    learning, chat = brand_and_learning(monkeypatch, {
        "return_value": httpx.Response(200, json={"summary": "Rules learned from your edits:\n- x"})})
    client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee", "brand": "Mine"}})
    assert system_of(chat) == "Brand: Mine"
    assert learning.call_count == 0


@respx.mock
def test_learning_without_brand_service(monkeypatch):
    monkeypatch.setattr(main, "LEARNING_URL", LEARNING)
    main._learning_cache.update(at=None, summary="")
    respx.get(f"{LEARNING}/rules/summary").mock(
        return_value=httpx.Response(200, json={"summary": "Rules learned from your edits:\n- x"}))
    chat = respx.post(f"{OLLAMA}/api/chat").mock(return_value=reply('{"headlines": ["A", "B"]}'))
    client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    assert system_of(chat) == "Brand: Rules learned from your edits:\n- x"


@respx.mock
def test_brand_is_fetched_right_after_boot(monkeypatch):
    # regression: on a machine up for < 60 s, monotonic() - 0.0 < TTL made the empty cache
    # look fresh and the brand profile was skipped
    import time
    monkeypatch.setattr(time, "monotonic", lambda: 5.0)
    monkeypatch.setattr(main, "BRAND_URL", "http://brand.test")
    main._brand_cache.update(at=None, summary="")
    respx.get("http://brand.test/profile/summary").mock(
        return_value=httpx.Response(200, json={"summary": "Northwind Roasters"}))
    route = respx.post(f"{OLLAMA}/api/chat").mock(return_value=reply('{"headlines": ["A", "B"]}'))
    client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    assert json.loads(route.calls[0].request.content)["messages"][0]["content"] == "Brand: Northwind Roasters"


# ---------------------------------------------------------------- OpenAI-compatible provider

API = "http://llm-api.test/v1"
SECRET = "gsk_test_secret_value_1234567890"


@pytest.fixture
def openai_provider(monkeypatch):
    monkeypatch.setattr(main, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(main, "OPENAI_BASE_URL", API)
    monkeypatch.setattr(main, "OPENAI_API_KEY", SECRET)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)


def completion(content, prompt_tokens=10, completion_tokens=5):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}],
                                     "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}})


@respx.mock
def test_openai_json_schema_request_and_usage(openai_provider):
    route = respx.post(f"{API}/chat/completions").mock(return_value=completion('{"headlines": ["A", "B"]}'))
    r = client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    assert r.status_code == 200 and r.json()["output"] == {"headlines": ["A", "B"]}
    assert r.json()["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}
    sent = route.calls[0].request
    assert sent.headers["authorization"] == f"Bearer {SECRET}"
    body = json.loads(sent.content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["required"] == ["headlines"]


@respx.mock
def test_openai_falls_back_to_json_object_when_schema_unsupported(openai_provider):
    route = respx.post(f"{API}/chat/completions").mock(side_effect=[
        httpx.Response(400, json={"error": {"message": "response_format json_schema is not supported"}}),
        completion('{"headlines": ["A", "B"]}')])
    r = client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    assert r.status_code == 200
    second = json.loads(route.calls[1].request.content)
    assert second["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in second["messages"][0]["content"]


@respx.mock
def test_openai_waits_out_rate_limit(openai_provider, monkeypatch):
    waited = []
    monkeypatch.setattr(main.time, "sleep", lambda s: waited.append(s))
    respx.post(f"{API}/chat/completions").mock(side_effect=[
        httpx.Response(429, headers={"x-ratelimit-reset-tokens": "1m2.5s"}, json={"error": {"message": "tokens per minute"}}),
        completion("Wake up.")])
    r = client.post("/v1/run", json={"prompt": "tagline", "vars": {"product": "beans"}})
    assert r.status_code == 200 and r.json()["output"] == "Wake up."
    assert waited == [60.0]            # 62.5 s asked, capped at 60


@respx.mock
def test_openai_daily_limit_stops_with_clear_error(openai_provider):
    respx.post(f"{API}/chat/completions").mock(return_value=httpx.Response(
        429, json={"error": {"message": "Rate limit reached on tokens per day (TPD): Limit 200000"}}))
    r = client.post("/v1/run", json={"prompt": "tagline", "vars": {"product": "beans"}})
    assert r.status_code == 502 and "daily limit" in r.json()["detail"]


@respx.mock
def test_openai_errors_never_contain_the_key(openai_provider):
    respx.post(f"{API}/chat/completions").mock(side_effect=httpx.ConnectError(f"boom {SECRET}"))
    r = client.post("/v1/run", json={"prompt": "tagline", "vars": {"product": "beans"}})
    assert r.status_code == 502 and SECRET not in r.text


def test_openai_health_does_not_expose_the_key(openai_provider):
    body = client.get("/health").json()
    assert body["provider"] == "openai" and body["key_set"] is True and SECRET not in json.dumps(body)


def test_wait_seconds_parses_reset_headers():
    assert main.wait_seconds(httpx.Response(429, headers={"retry-after": "3"})) == 3.0
    assert main.wait_seconds(httpx.Response(429, headers={"x-ratelimit-reset-requests": "250ms"})) == 0.25
    assert main.wait_seconds(httpx.Response(429)) == 5.0


@respx.mock
def test_openai_server_side_schema_failure_is_retried_with_feedback(openai_provider):
    # Groq returns 400 json_validate_failed with the model's attempt in failed_generation
    route = respx.post(f"{API}/chat/completions").mock(side_effect=[
        httpx.Response(400, json={"error": {"message": "Failed to validate JSON.", "code": "json_validate_failed",
                                            "failed_generation": '{"headlines": ["only one"]}'}}),
        completion('{"headlines": ["A", "B"]}')])
    r = client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    assert r.status_code == 200 and r.json()["attempts"] == 2
    retry = json.loads(route.calls[1].request.content)["messages"]
    assert "rejected" in retry[-1]["content"] and retry[-2]["content"] == '{"headlines": ["only one"]}'


@respx.mock
def test_reasoning_effort_is_sent_only_when_set(openai_provider, monkeypatch):
    route = respx.post(f"{API}/chat/completions").mock(return_value=completion("Wake up."))
    client.post("/v1/run", json={"prompt": "tagline", "vars": {"product": "beans"}})
    assert "reasoning_effort" not in json.loads(route.calls[0].request.content)
    monkeypatch.setattr(main, "REASONING_EFFORT", "low")
    client.post("/v1/run", json={"prompt": "tagline", "vars": {"product": "beans"}})
    assert json.loads(route.calls[1].request.content)["reasoning_effort"] == "low"


# ---------------------------------------------------------------- approved facts (05 /facts)

FACTS = {"facts": [{"id": "f1", "text": "Desk Blend is a medium roast."},
                   {"id": "f2", "text": "Unopened bags can be returned within 30 days."}]}


def facts_service(monkeypatch, **response):
    monkeypatch.setattr(main, "BRAND_URL", "http://brand.test")
    main._brand_cache.update(at=None, summary="")
    main._facts_cache.update(at=None, text="")
    respx.get("http://brand.test/profile/summary").mock(
        return_value=httpx.Response(200, json={"summary": "Northwind Roasters"}))
    facts = respx.get("http://brand.test/facts").mock(**response)
    chat = respx.post(f"{OLLAMA}/api/chat").mock(return_value=reply("A post."))
    return facts, chat


def user_of(route, n=0):
    return json.loads(route.calls[n].request.content)["messages"][-1]["content"]


@respx.mock
def test_facts_injected_as_numbered_lines(monkeypatch):
    _, chat = facts_service(monkeypatch, return_value=httpx.Response(200, json=FACTS))
    r = client.post("/v1/run", json={"prompt": "fact_post", "vars": {"topic": "decaf"}})
    assert r.status_code == 200
    assert "Facts:\n[f1] Desk Blend is a medium roast.\n[f2] Unopened bags can be returned within 30 days." \
        in user_of(chat)


@respx.mock
def test_facts_are_cached(monkeypatch):
    facts, chat = facts_service(monkeypatch, return_value=httpx.Response(200, json=FACTS))
    client.post("/v1/run", json={"prompt": "fact_post", "vars": {"topic": "decaf"}})
    client.post("/v1/run", json={"prompt": "fact_post", "vars": {"topic": "decaf"}})
    assert facts.call_count == 1
    assert "[f1]" in user_of(chat, 1)


@respx.mock
def test_facts_fetched_right_after_boot(monkeypatch):
    # same regression as the brand cache: "at" must start as None, not 0.0
    import time
    monkeypatch.setattr(time, "monotonic", lambda: 5.0)
    _, chat = facts_service(monkeypatch, return_value=httpx.Response(200, json=FACTS))
    client.post("/v1/run", json={"prompt": "fact_post", "vars": {"topic": "decaf"}})
    assert "[f1]" in user_of(chat)


@respx.mock
def test_facts_service_down_renders_without_facts(monkeypatch):
    _, chat = facts_service(monkeypatch, side_effect=httpx.ConnectError("refused"))
    r = client.post("/v1/run", json={"prompt": "fact_post", "vars": {"topic": "decaf"}})
    assert r.status_code == 200
    assert "Facts:" not in user_of(chat)


@respx.mock
def test_facts_bad_payload_renders_without_facts(monkeypatch):
    _, chat = facts_service(monkeypatch, return_value=httpx.Response(200, json={"facts": "oops"}))
    r = client.post("/v1/run", json={"prompt": "fact_post", "vars": {"topic": "decaf"}})
    assert r.status_code == 200
    assert "Facts:" not in user_of(chat)


@respx.mock
def test_caller_facts_override_the_service(monkeypatch):
    facts, chat = facts_service(monkeypatch, return_value=httpx.Response(200, json=FACTS))
    client.post("/v1/run", json={"prompt": "fact_post", "vars": {"topic": "decaf", "facts": "[x] Mine."}})
    assert "Facts:\n[x] Mine." in user_of(chat)
    assert facts.call_count == 0


@respx.mock
def test_facts_not_fetched_for_prompts_without_the_var(monkeypatch):
    facts, _ = facts_service(monkeypatch, return_value=httpx.Response(200, json=FACTS))
    respx.post(f"{OLLAMA}/api/chat").mock(return_value=reply('{"headlines": ["A", "B"]}'))
    assert client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}}).status_code == 200
    assert facts.call_count == 0


# ---------- access and limits (security audit: /v1/run was open to anything on the network)


def test_key_required_when_configured(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "k1")
    body = {"prompt": "headline", "vars": {"topic": "coffee"}}
    assert client.post("/v1/run", json=body).status_code == 401
    assert client.post("/v1/run", json=body, headers={"X-API-Key": "nope"}).status_code == 401
    # right key gets past auth (then fails on the unknown prompt, without calling any model)
    assert client.post("/v1/run", json={"prompt": "nope"}, headers={"X-API-Key": "k1"}).status_code == 404


def test_unlisted_model_refused_before_any_llm_call(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    monkeypatch.setattr(main, "ALLOWED_MODELS", set())
    r = client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "c"}, "model": "gpt-5-pro"})
    assert r.status_code == 403


@respx.mock
def test_allowed_and_default_models_accepted(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    monkeypatch.setattr(main, "ALLOWED_MODELS", {"mkt-verifier"})
    respx.post(f"{OLLAMA}/api/chat").mock(return_value=reply('{"headlines": ["A", "B"]}'))
    for model in ("mkt-verifier", main.MODEL):
        r = client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "c"}, "model": model})
        assert r.status_code == 200, model


def test_oversized_vars_and_bad_temperature_rejected(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    big = {"prompt": "headline", "vars": {"topic": "x" * (main.MAX_VARS_CHARS + 1)}}
    assert client.post("/v1/run", json=big).status_code == 413
    hot = {"prompt": "headline", "vars": {"topic": "c"}, "temperature": 9}
    assert client.post("/v1/run", json=hot).status_code == 422
