import json
import logging

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main, providers as pv

KEY = "test-internal-key"
H = {"X-API-Key": KEY}
GROQ_KEY = "gsk-SUPER-SECRET-groq-key-123"
OPENAI_KEY = "sk-SUPER-SECRET-openai-456"
GATEWAY, BRAND, CLAIMS, ADS = "http://gateway.test", "http://brand.test", "http://claims.test", "http://ads.test"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/responses"

PROFILE = {
    "name": "Northwind Roasters", "website": "https://northwind-roasters.example.com",
    "audience": {"primary": "Remote workers", "secondary": "Small distributed teams", "pains": ["Flat coffee"]},
    "products": [{"name": "Desk Blend", "price": "$18 / 340 g", "one_line": "Medium roast."},
                 {"name": "Team Box", "price": "$79 / month", "one_line": "1.5 kg for teams."}],
}
FACTS = {"facts": [{"id": "f1", "text": "Team Box costs $79 / month."},
                   {"id": "f2", "text": "Desk Blend is a medium roast for drip and French press."}]}
COMPETITORS = [
    {"id": 1, "name": "Atlas Coffee Club", "website": "https://atlascoffeeclub.com", "domains": [], "status": "active"},
    {"id": 2, "name": "Trade Coffee", "website": "https://www.drinktrade.com", "domains": [], "status": "active"},
]
GENERATED = {"questions": [
    {"text": "What is the best coffee subscription for a remote team of 10?", "kind": "category"},
    {"text": "Which coffee subscription is best for a home office?", "kind": "category"},
    {"text": "Is Atlas Coffee Club better than a local roaster?", "kind": "comparison"},     # names a competitor
    {"text": "How much does the Northwind Roasters Team Box cost?", "kind": "category"},    # names us
    {"text": "what is the best coffee subscription for a remote team of 10", "kind": "category"},  # duplicate
    {"text": "Short?", "kind": "problem"},
]}


def groq_answer(question: str) -> str:
    if "remote team" in question:
        return ("Top picks:\n\n1. **Trade Coffee** – matches you to roasters.\n2. **Atlas Coffee Club** – world tour.\n"
                "3. **Northwind Roasters** – roasted to order (https://northwind-roasters.example.com).")
    if "home office" in question:
        return "Most people like Atlas Coffee Club for variety; see https://atlascoffeeclub.com."
    return ("Northwind Roasters sells the Team Box for $99 per month. "
            "Their Desk Blend is a medium roast for drip and French press.")


def groq_handler(request):
    q = json.loads(request.content)["messages"][0]["content"]
    return httpx.Response(200, json={"model": "openai/gpt-oss-120b", "choices": [{"message": {"content": groq_answer(q)}}],
                                     "usage": {"prompt_tokens": 100, "completion_tokens": 300}})


def claims_handler(request):
    text = json.loads(request.content)["text"]
    if "$99" in text:
        return httpx.Response(200, json={"ok": False, "unsupported": [text],
                                         "claims": [{"claim": text, "supported": False, "reasons": ["numbers not in the facts: 99"]}],
                                         "numbers": [{"value": "99", "supported": False}]})
    return httpx.Response(200, json={"ok": True, "unsupported": [], "claims": [{"claim": text, "supported": True, "reasons": []}],
                                     "numbers": []})


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    main._migrated.clear()
    for p in pv.PROVIDERS.values():
        monkeypatch.delenv(p["key_env"], raising=False)
    for k in ("VIS_GEMINI_TERMS_ACCEPTED", "VIS_GROQ_DAILY_CALLS", "COMPETITOR_ALIASES", "BRAND_ALIASES", "OWN_DOMAINS",
              "VIS_SAMPLES", "BRAND_NAME", "VIS_COUNTRY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "vis.sqlite"))
    monkeypatch.setenv("GATEWAY_URL", GATEWAY)
    monkeypatch.setenv("BRAND_URL", BRAND)
    monkeypatch.setenv("CLAIMS_URL", CLAIMS)
    monkeypatch.setenv("AD_LIBRARY_URL", ADS)
    monkeypatch.setenv("VIS_GROQ_API_KEY", GROQ_KEY)
    monkeypatch.setenv("VIS_DELAY_S", "0")
    monkeypatch.setattr(pv.time, "sleep", lambda s: None)


@pytest.fixture
def services():
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BRAND}/profile").respond(200, json=PROFILE)
        m.get(f"{BRAND}/facts").respond(200, json=FACTS)
        m.get(f"{ADS}/competitors").respond(200, json=COMPETITORS)
        m.post(f"{GATEWAY}/v1/run").respond(200, json={"output": GENERATED})
        m.post(f"{CLAIMS}/verify").mock(side_effect=claims_handler)
        m.post(GROQ_URL).mock(side_effect=groq_handler)
        yield m


client = TestClient(main.app)


def approved_set() -> dict:
    s = client.post("/questions/generate", headers=H, json={"count": 15}).json()
    return client.post(f"/question-sets/{s['id']}/approve", headers=H, json={"approved_by": "sam"}).json()


def test_auth():
    assert client.post("/runs", json={}).status_code == 401
    assert client.post("/questions/generate", headers={"X-API-Key": "wrong"}, json={}).status_code == 401


def test_auth_not_configured(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.post("/runs", headers=H, json={}).status_code == 503


def test_generate_classifies_and_versions(services):
    r = client.post("/questions/generate", headers=H, json={"count": 15, "branded": 1, "market": "US"})
    assert r.status_code == 201
    s = r.json()
    body = json.loads(services.calls[-1].request.content) if services.calls[-1].request.url.path == "/v1/run" else None
    sent = [c for c in services.calls if c.request.url.path == "/v1/run"][0]
    vars_ = json.loads(sent.request.content)["vars"]
    assert vars_["count"] == 15 and vars_["branded_count"] == 1 and "Team Box" in vars_["products"]
    assert "Atlas" not in json.dumps(vars_)                  # competitor names never go into the prompt
    assert body is None or body["prompt"] == "visibility_questions"
    assert [q["kind"] for q in s["questions"]] == ["category", "category", "branded"]
    assert {d["why"] for d in s["dropped"]} == {"names a competitor (Atlas Coffee Club)", "duplicate", "too short"}
    assert s["status"] == "draft" and s["version"] == 1 and s["warnings"]
    # draft: editable in place
    e = client.put(f"/question-sets/{s['id']}", headers=H, json={"questions": [
        {"text": "Best coffee subscription for a remote team?"}, {"text": "What does Northwind Roasters sell?"}]})
    assert e.status_code == 200 and [q["kind"] for q in e.json()["questions"]] == ["category", "branded"]
    a = client.post(f"/question-sets/{s['id']}/approve", headers=H, json={"approved_by": "sam"}).json()
    assert a["status"] == "approved" and a["approved_by"] == "sam"
    # approved: frozen, so runs on it stay comparable
    assert client.put(f"/question-sets/{s['id']}", headers=H, json={"questions": [{"text": "Anything new here?"}]}).status_code == 409
    v2 = client.post(f"/question-sets/{s['id']}/revise", headers=H).json()
    assert v2["version"] == 2 and v2["parent_id"] == s["id"] and v2["status"] == "draft"
    client.post(f"/question-sets/{v2['id']}/approve", headers=H)
    assert client.get(f"/question-sets/{s['id']}").json()["status"] == "retired"
    assert client.get("/health").json()["active_set"]["version"] == 2


def test_no_provider_enabled(services, monkeypatch):
    approved_set()
    monkeypatch.delenv("VIS_GROQ_API_KEY")
    r = client.post("/runs", headers=H, json={})
    assert r.status_code == 409 and "no provider" in r.json()["detail"]


def test_run_without_approved_set(services):
    assert client.post("/runs", headers=H, json={}).status_code == 409


def test_run_summary_claims_gaps(services, caplog):
    caplog.set_level(logging.DEBUG)
    s = approved_set()
    r = client.post("/runs?wait=true", headers=H, json={"samples": 2, "providers": ["groq_knowledge", "openai_search"]})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "done" and run["providers"] == ["groq_knowledge"]
    assert run["notes"]["providers_without_key"] == ["openai_search"]    # no key: skipped, not an error
    assert run["answers"] == 3 * 2
    sm = client.get("/summary").json()
    g = sm["providers"]["groq_knowledge"]
    # unaided: 2 questions × 2 samples; we are named in the remote-team answers only
    assert g["mention"]["k"] == 2 and g["mention"]["n"] == 4 and g["mention"]["ci95"][0] > 0.09
    assert g["mention_by_question"] == {"k": 1, "n": 2, "rate": 0.5, "ci95": g["mention_by_question"]["ci95"]}
    assert g["citation"] is None                              # no web search: no citation rate
    assert g["avg_position"] == 3.0 and g["positions"] == {"3": 2}
    assert g["share_of_voice"]["Northwind Roasters"] == {"mentions": 2, "share": 0.25}
    assert g["share_of_voice"]["Atlas Coffee Club"] == {"mentions": 4, "share": 0.5}
    assert g["competitors"]["Trade Coffee"]["avg_position"] == 1.0
    assert g["branded"]["answers"] == 2 and g["branded"]["wrong_claims"] == 2
    assert g["tokens"] == {"input": 600, "output": 1800} and g["cost_usd"] > 0
    assert g["trend"] is None
    # wrong claims, grouped by sentence
    w = client.get("/claims/wrong").json()["claims"]
    assert len(w) == 1 and w[0]["sentence"] == "Northwind Roasters sells the Team Box for $99 per month."
    assert w[0]["kind"] == "number" and w[0]["count"] == 2 and w[0]["providers"] == ["groq_knowledge"]
    # identical sentences are checked once
    assert len([c for c in services.calls if c.request.url.path == "/verify"]) == 3
    # gaps: the home-office question names Atlas and not us
    gp = client.get("/gaps").json()
    assert gp["total"] == 1
    gap = gp["gaps"][0]
    assert gap["question"] == "Which coffee subscription is best for a home office?"
    assert gap["competitors"][0]["name"] == "Atlas Coffee Club" and gap["competitors"][0]["mentions"] == 2
    assert gap["cited_pages"] == []                           # groq cites nothing
    assert gap["suggested_faq"]["answer_first"].startswith('Answer "Which coffee subscription')
    # answers per question, with the URLs the model wrote kept apart from citations
    qid = s["questions"][0]["id"]
    qa = client.get(f"/questions/{qid}/answers").json()
    assert len(qa["answers"]) == 2 and qa["answers"][0]["citations"] == []
    assert qa["answers"][0]["urls_in_text"] == ["https://northwind-roasters.example.com"]
    # usage accounting
    u = client.get("/usage").json()
    assert u["rows"][0]["calls"] == 6 and u["total_tokens"] == 2400
    assert GROQ_KEY not in caplog.text and GROQ_KEY not in json.dumps([sm, w, gp, qa, u])


def test_trend_only_on_the_same_set_version(services):
    s = approved_set()
    client.post("/runs?wait=true", headers=H, json={"samples": 1})
    client.post("/runs?wait=true", headers=H, json={"samples": 1})
    t = client.get("/summary").json()["providers"]["groq_knowledge"]["trend"]
    assert t["previous_run_id"] == 1 and t["mention_delta"] == 0.0
    v2 = client.post(f"/question-sets/{s['id']}/revise", headers=H).json()
    client.post(f"/question-sets/{v2['id']}/approve", headers=H)
    client.post("/runs?wait=true", headers=H, json={"samples": 1})
    sm = client.get("/summary").json()
    assert sm["providers"]["groq_knowledge"]["trend"] is None
    assert any("question set v1" in n for n in sm["notes"])
    assert [h["run_id"] for h in sm["history"]] == [1, 2, 3]


def test_daily_call_cap(services, monkeypatch):
    approved_set()
    monkeypatch.setenv("VIS_GROQ_DAILY_CALLS", "2")
    run = client.post("/runs?wait=true", headers=H, json={"samples": 2}).json()
    g = client.get(f"/summary?run_id={run['id']}").json()["providers"]["groq_knowledge"]
    assert g["answers"] == 2 and g["skipped"] == 4
    assert len([c for c in services.calls if c.request.url.host == "api.groq.com"]) == 2
    qa = client.get("/questions/2/answers").json()
    assert qa["answers"][0]["error"] == "skipped: daily call cap reached (2)"
    assert client.get("/providers").json()[-1]["today"]["calls"] == 2


def test_provider_errors_and_keys_never_leak(services, monkeypatch, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("VIS_OPENAI_API_KEY", OPENAI_KEY)
    approved_set()
    services.post(OPENAI_URL).respond(401, json={"error": {"message": f"Incorrect API key provided: {OPENAI_KEY}"}})
    run = client.post("/runs?wait=true", headers=H, json={"samples": 1}).json()
    assert run["status"] == "done" and set(run["providers"]) == {"openai_search", "groq_knowledge"}
    sm = client.get("/summary").json()
    assert sm["providers"]["openai_search"]["errors"] == 3 and sm["providers"]["openai_search"]["answers"] == 0
    qa = client.get("/questions/1/answers").json()
    assert {a["error"] for a in qa["answers"] if a["provider"] == "openai_search"} == {"HTTP 401"}
    everything = json.dumps([sm, qa, client.get("/providers").json(), client.get("/health").json(),
                             client.get(f"/runs/{run['id']}").json()])
    for secret in (OPENAI_KEY, GROQ_KEY):
        assert secret not in everything and secret not in caplog.text
        assert secret.encode() not in (tmp_path / "vis.sqlite").read_bytes()
    # the key header was sent, to the provider only
    assert services.calls  # sanity
    for c in services.calls:
        auth = c.request.headers.get("authorization", "")
        if OPENAI_KEY in auth:
            assert c.request.url.host == "api.openai.com"


def test_openai_citations_count_for_us_and_competitors(services, monkeypatch):
    monkeypatch.delenv("VIS_GROQ_API_KEY")
    monkeypatch.setenv("VIS_OPENAI_API_KEY", OPENAI_KEY)
    approved_set()
    services.post(OPENAI_URL).respond(200, json={"output": [
        {"type": "web_search_call", "status": "completed"},
        {"type": "message", "content": [{"type": "output_text", "text": "1. Trade Coffee\n2. Atlas Coffee Club",
                                         "annotations": [
                                             {"type": "url_citation", "url": "https://www.drinktrade.com/remote", "title": "Trade"},
                                             {"type": "url_citation", "url": "https://www.wirecutter.com/coffee", "title": "Best"}]}]}],
        "usage": {"input_tokens": 1000, "output_tokens": 200}})
    client.post("/runs?wait=true", headers=H, json={"samples": 1})
    g = client.get("/summary").json()["providers"]["openai_search"]
    assert g["citation"]["k"] == 0 and g["citation"]["n"] == 2
    assert g["competitors"]["Trade Coffee"]["citation"]["k"] == 2
    assert g["cost_usd"] == round(3 * (1000 / 1e6 * 0.25 + 200 / 1e6 * 2.0 + 0.01), 4)
    gaps = client.get("/gaps").json()["gaps"]
    assert len(gaps) == 2
    owners = {p["domain"]: p["owner"] for p in gaps[0]["cited_pages"]}
    assert owners == {"drinktrade.com": "Trade Coffee", "wirecutter.com": "third party"}


def test_competitor_aliases_from_env(services, monkeypatch):
    monkeypatch.setenv("COMPETITOR_ALIASES", "Atlas Coffee Club=Atlas")
    ents = main.build_entities()
    atlas = next(c for c in ents["competitors"] if c["name"] == "Atlas Coffee Club")
    assert atlas["terms"] == ["Atlas Coffee Club", "Atlas", "atlascoffeeclub.com"]
    assert ents["brand"]["domains"] == ["northwind-roasters.example.com"]
    assert ents["products"] == ["Desk Blend", "Team Box"]


def test_no_registry_means_no_competitors(services, monkeypatch):
    monkeypatch.delenv("AD_LIBRARY_URL")
    ents = main.build_entities()
    assert ents["competitors"] == [] and "AD_LIBRARY_URL" in ents["notes"][0]


def test_unknown_brand_answers_are_counted_not_checked(services):
    approved_set()
    services.post(GROQ_URL).respond(200, json={"choices": [{"message": {"content":
        "I'm not aware of a company called Northwind Roasters, so I can't give its prices."}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    client.post("/runs?wait=true", headers=H, json={"samples": 1})
    g = client.get("/summary").json()["providers"]["groq_knowledge"]
    assert g["branded"]["says_unknown"] == 1 and g["branded"]["knows_us"]["k"] == 0
    assert not [c for c in services.calls if c.request.url.path == "/verify"]
