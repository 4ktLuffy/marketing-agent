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
    main._brand_cache.update(at=0.0, summary="")
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
    main._brand_cache.update(at=0.0, summary="")
    main._learning_cache.update(at=0.0, summary="")
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
    main._learning_cache.update(at=0.0, summary="")
    respx.get(f"{LEARNING}/rules/summary").mock(
        return_value=httpx.Response(200, json={"summary": "Rules learned from your edits:\n- x"}))
    chat = respx.post(f"{OLLAMA}/api/chat").mock(return_value=reply('{"headlines": ["A", "B"]}'))
    client.post("/v1/run", json={"prompt": "headline", "vars": {"topic": "coffee"}})
    assert system_of(chat) == "Brand: Rules learned from your edits:\n- x"
