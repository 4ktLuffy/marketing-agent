import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main

GW, BRAND = "http://gw.test", "http://brand.test"
FACTS = [
    {"id": "f1", "text": "Desk Blend is a medium roast with chocolate and hazelnut tasting notes."},
    {"id": "f2", "text": "Roasted within 48 hours of shipping."},
    {"id": "f3", "text": "Our Swiss Water decaf is roasted in small batches every Tuesday."},
    {"id": "f4", "text": "Desk Blend costs $18 / 340 g."},
]
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def configure(monkeypatch):
    monkeypatch.setattr(main, "GATEWAY_URL", GW)
    monkeypatch.setattr(main, "BRAND_URL", BRAND)
    monkeypatch.setattr(main, "KB_URL", "")
    monkeypatch.setattr(main, "CHECK_MODE", "lenient")


def fake_gateway(answers: dict):
    """Route gateway calls by prompt name; each answer may be a dict or a list (per call)."""
    calls = {k: 0 for k in answers}

    def handler(request):
        prompt = json.loads(request.content)["prompt"]
        a = answers[prompt]
        if isinstance(a, list):
            a, calls[prompt] = a[calls[prompt]], calls[prompt] + 1
        return httpx.Response(200, json={"output": a})
    return handler


def mock(answers):
    respx.get(f"{BRAND}/facts").mock(return_value=httpx.Response(200, json={"facts": FACTS}))
    respx.post(f"{GW}/v1/run").mock(side_effect=fake_gateway(answers))


def details(*d):
    return {"details": list(d)}


def checks(*c):
    return {"checks": [{"detail": d, "stated": s, "quote": q} for d, s, q in c]}


def test_numbers_ignore_links_hashtags_and_list_markers():
    text = "1. Save 20% now https://x.com/p/99 #top10\n2. 10,000 fans"
    assert main.numbers(text) == {"20", "10000"}


def test_sentences_drop_links_hashtags_questions_and_empty_bits():
    assert main.sentences("Changed your mind? Try it now! https://x.com #coffee\n\nDesk Blend is medium.") == [
        "Try it now!", "Desk Blend is medium."]


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_empty_text_422():
    assert client.post("/verify", json={"text": "  "}).status_code == 422


@respx.mock
def test_supported_sentence_passes():
    mock({"claim_details": details("Desk Blend", "medium roast"),
          "detail_check": checks(("Desk Blend", True, "Desk Blend is a medium roast"),
                                 ("medium roast", True, "Desk Blend is a medium roast"))})
    body = client.post("/verify", json={"text": "Desk Blend is a medium roast."}).json()
    assert body["ok"] is True and body["unsupported"] == []


@respx.mock
def test_sentence_without_details_is_not_checked():
    mock({"claim_details": details(), "detail_check": checks()})
    assert client.post("/verify", json={"text": "Try it now!"}).json()["ok"] is True


@respx.mock
def test_detail_not_stated_is_flagged():
    mock({"claim_details": details("caramel"), "detail_check": checks(("caramel", False, ""))})
    body = client.post("/verify", json={"text": "Desk Blend has notes of caramel."}).json()
    assert body["ok"] is False
    assert body["claims"][0]["reasons"] == ["not in the facts: caramel"]


@respx.mock
def test_invented_quote_is_rejected():
    # negative control: the model says "stated" and quotes text the facts never contain
    mock({"claim_details": details("2025 Golden Bean award"),
          "detail_check": checks(("2025 Golden Bean award", True, "Desk Blend won the 2025 Golden Bean award"))})
    assert client.post("/verify", json={"text": "Desk Blend won the Golden Bean award."}).json()["ok"] is False


@respx.mock
def test_strict_mode_catches_wrong_value_in_real_quote(monkeypatch):
    # the model marks "every Friday" stated, quoting a real fact that says Tuesday
    answers = {"claim_details": details("every Friday"),
               "detail_check": checks(("every Friday", True, "Our Swiss Water decaf is roasted in small batches every Tuesday"))}
    mock(answers)
    monkeypatch.setattr(main, "CHECK_MODE", "lenient")
    assert client.post("/verify", json={"text": "The decaf is roasted every Friday."}).json()["ok"] is True
    monkeypatch.setattr(main, "CHECK_MODE", "strict")
    assert client.post("/verify", json={"text": "The decaf is roasted every Friday."}).json()["ok"] is False


@respx.mock
def test_pure_number_details_are_left_to_the_number_check():
    # "$18" is in the facts; the LLM wrongly says not stated, but number details skip the LLM
    mock({"claim_details": details("$18"), "detail_check": checks(("$18", False, ""))})
    body = client.post("/verify", json={"text": "Desk Blend: $18."}).json()
    assert body["ok"] is True


@respx.mock
def test_invented_number_is_flagged():
    mock({"claim_details": details(), "detail_check": checks()})
    body = client.post("/verify", json={"text": "Roasted within 24 hours."}).json()
    assert body["ok"] is False
    assert body["claims"][0]["reasons"] == ["numbers not in the facts: 24"]


@respx.mock
def test_number_from_the_brief_counts_as_evidence():
    mock({"claim_details": details(), "detail_check": checks()})
    body = client.post("/verify", json={"text": "Launch week: 15% off.", "context": "Offer: 15% off in launch week."}).json()
    assert body["ok"] is True


@respx.mock
def test_brand_down_is_502():
    respx.get(f"{BRAND}/facts").mock(side_effect=httpx.ConnectError("down"))
    r = client.post("/verify", json={"text": "Anything."})
    assert r.status_code == 502 and "facts unavailable" in r.json()["detail"]


def test_quote_normalization_accepts_ids_joined_lines_and_trims_but_not_inventions():
    ev = "[f1] Desk Blend is a medium roast with chocolate and hazelnut tasting notes.\n[f4] Team Box contains 1.5 kg of coffee across two blends and ships to each teammate."
    assert main.in_evidence("[f1] Desk Blend is a medium roast with chocolate and hazelnut tasting notes.", ev)
    assert main.in_evidence("[f1] Desk Blend is a medium roast\n[f4] Team Box ships to each teammate.", ev)
    assert main.in_evidence("Team Box ships to each teammate.", ev)
    assert not main.in_evidence("Desk Blend won the 2025 Golden Bean award.", ev)
    assert not main.in_evidence("", ev)


@respx.mock
def test_fact_ids_do_not_count_as_evidence_numbers():
    # regression: with 15+ facts, the id "[f15]" made an invented "$15" look supported
    facts = [{"id": f"f{i}", "text": f"Fact number {i * 100 + 7}."} for i in range(1, 16)]
    respx.get(f"{BRAND}/facts").mock(return_value=httpx.Response(200, json={"facts": facts}))
    respx.post(f"{GW}/v1/run").mock(side_effect=fake_gateway({"claim_details": details(), "detail_check": checks()}))
    body = client.post("/verify", json={"text": "Desk Blend costs $15."}).json()
    assert body["ok"] is False and body["claims"][0]["reasons"] == ["numbers not in the facts: 15"]


@respx.mock
def test_details_the_model_skipped_twice_count_as_unsupported():
    # regression: the model split "chocolate and caramel" correctly, then returned no checks;
    # the one-by-one retry also returns nothing, so both stay unsupported
    mock({"claim_details": details("chocolate notes", "caramel notes"), "detail_check": {"checks": []}})
    body = client.post("/verify", json={"text": "Our decaf has notes of chocolate and caramel."}).json()
    assert body["ok"] is False
    # "chocolate notes" is covered word-for-word by a fact; "caramel notes" never gets an answer
    assert body["claims"][0]["reasons"] == ["not in the facts: caramel notes"]


@respx.mock
def test_partial_check_list_flags_only_the_missing_detail():
    mock({"claim_details": details("medium roast", "caramel notes"),
          "detail_check": [checks(("medium roast", True, "Desk Blend is a medium roast")),
                           checks(("caramel notes", False, ""))]})
    body = client.post("/verify", json={"text": "Desk Blend: medium roast, caramel notes."}).json()
    assert body["claims"][0]["reasons"] == ["not in the facts: caramel notes"]


@respx.mock
def test_skipped_detail_is_asked_again_alone():
    mock({"claim_details": details("medium roast", "chocolate notes"),
          "detail_check": [checks(("medium roast", True, "Desk Blend is a medium roast")),
                           checks(("chocolate notes", True, "chocolate and hazelnut tasting notes"))]})
    assert client.post("/verify", json={"text": "Desk Blend: medium roast, chocolate notes."}).json()["ok"] is True


def test_number_details_with_units_go_to_the_number_check():
    assert main.is_number_detail("48 hours")
    assert main.is_number_detail("per 250 g bag")
    assert main.is_number_detail("$18")
    assert not main.is_number_detail("roasted within 48 hours")   # has a non-unit word
    assert not main.is_number_detail("medium roast")


@respx.mock
def test_detail_literally_in_one_fact_needs_no_llm():
    # the model wrongly says "not stated"; the words are all in fact f3, so it passes
    mock({"claim_details": details("roasted in small batches", "every Tuesday"),
          "detail_check": checks(("roasted in small batches", False, ""), ("every Tuesday", False, ""))})
    assert client.post("/verify", json={"text": "Decaf roasted in small batches every Tuesday."}).json()["ok"] is True


@respx.mock
def test_word_shortcut_does_not_accept_an_invented_item():
    # "caramel" is in no fact line, so the shortcut must leave it to the model (who says no)
    mock({"claim_details": details("chocolate notes", "caramel notes"),
          "detail_check": checks(("caramel notes", False, ""))})
    body = client.post("/verify", json={"text": "Notes of chocolate and caramel."}).json()
    assert body["ok"] is False and body["claims"][0]["reasons"] == ["not in the facts: caramel notes"]


def test_numbers_split_lists_but_keep_thousands():
    assert main.numbers("every 1,2 or 4 weeks") == {"1", "2", "4"}
    assert main.numbers("10,000 fans and 1.5 kg") == {"10000", "1.5"}


@respx.mock
def test_untrusted_kb_sources_are_not_evidence(monkeypatch):
    # security audit H1: an LLM-written digest in the KB must not approve its own claims
    monkeypatch.setattr(main, "KB_URL", "http://kb.test")
    respx.post("http://kb.test/search").mock(return_value=httpx.Response(200, json={"results": [
        {"doc_id": "digest-1", "title": "Trend digest", "source": "trend-digest", "chunk": "Our coffee is certified organic.", "score": 0.9},
        {"doc_id": "faq", "title": "FAQ", "source": "website", "chunk": "Returns within 30 days.", "score": 0.9}]}))
    mock({"claim_details": details(), "detail_check": checks()})
    lines = main.gather_evidence(main.VerifyRequest(text="anything"))
    assert any("Returns within 30 days" in l for l in lines)
    assert not any("certified organic" in l for l in lines)


def test_key_enforced_when_configured(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "k1")
    assert client.post("/verify", json={"text": "Hi."}).status_code == 401
    assert client.post("/verify", json={"text": "Hi."}, headers={"X-API-Key": "wrong"}).status_code == 401


def test_oversized_input_rejected_before_any_llm_call(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    # No respx routes: any gateway/brand call would raise, so these must fail on validation.
    assert client.post("/verify", json={"text": "x" * (main.MAX_TEXT_CHARS + 1)}).status_code == 422
    many = " ".join(f"Sentence number {i} is here." for i in range(main.MAX_SENTENCES + 1))
    assert client.post("/verify", json={"text": many}).status_code == 413
    assert client.post("/verify", json={"text": "Hi.", "extra_facts": ["f"] * 51}).status_code == 422
    assert client.post("/verify", json={"text": "Hi.", "extra_facts": ["f" * 1001]}).status_code == 422


# ---------- product scoping: a product's facts only support claims about that product

PRODUCTS = {"products": [{"name": "Desk Blend", "price": "$18 / 340 g"},
                         {"name": "Swiss Water decaf", "aliases": ["decaf"]},
                         {"name": "Team Box"}]}


def evidence_sent(route):
    return [json.loads(c.request.content)["vars"].get("evidence", "") for c in route.calls
            if json.loads(c.request.content)["prompt"] == "detail_check"]


@respx.mock
def test_other_products_facts_are_not_evidence():
    respx.get(f"{BRAND}/facts").mock(return_value=httpx.Response(200, json={"facts": FACTS}))
    respx.get(f"{BRAND}/profile").mock(return_value=httpx.Response(200, json=PRODUCTS))
    gw = respx.post(f"{GW}/v1/run").mock(side_effect=fake_gateway({
        "claim_details": details("chocolate and hazelnut notes"),
        "detail_check": checks(("chocolate and hazelnut notes", False, "")),
    }))
    r = client.post("/verify", json={"text": "Our decaf has chocolate and hazelnut notes."}).json()
    assert r["ok"] is False
    sent = evidence_sent(gw)[0]
    assert "decaf" in sent and "Desk Blend" not in sent      # only decaf + general facts
    assert "48 hours" in sent


@respx.mock
def test_number_from_another_product_is_flagged():
    respx.get(f"{BRAND}/facts").mock(return_value=httpx.Response(200, json={"facts": FACTS}))
    respx.get(f"{BRAND}/profile").mock(return_value=httpx.Response(200, json=PRODUCTS))
    respx.post(f"{GW}/v1/run").mock(side_effect=fake_gateway({"claim_details": details()}))
    r = client.post("/verify", json={"text": "The Team Box costs $18."}).json()
    assert r["ok"] is False
    assert "numbers not in the facts: 18" in r["claims"][0]["reasons"]
    r = client.post("/verify", json={"text": "Desk Blend costs $18."}).json()   # own price: fine
    assert r["ok"] is True


@respx.mock
def test_sentence_without_product_and_profile_down_keep_all_evidence():
    respx.get(f"{BRAND}/facts").mock(return_value=httpx.Response(200, json={"facts": FACTS}))
    respx.get(f"{BRAND}/profile").mock(side_effect=httpx.ConnectError("down"))
    respx.post(f"{GW}/v1/run").mock(side_effect=fake_gateway({"claim_details": details()}))
    assert client.post("/verify", json={"text": "The Team Box costs $18."}).json()["ok"] is True


def test_scoped_evidence_rules():
    subjects = [("desk blend", __import__("re").compile(r"\bdesk blend\b", 2)),
                ("decaf", __import__("re").compile(r"\bdecaf\b", 2))]
    lines = ["[f1] Desk Blend is medium.", "[f2] Our decaf is roasted Tuesdays.", "[f3] Ships in 48 hours.",
             "[f4] Desk Blend and decaf ship together."]
    assert main.scoped_evidence("Try our decaf.", lines, subjects) == [lines[1], lines[2], lines[3]]
    assert main.scoped_evidence("Coffee for your desk.", lines, subjects) == lines


def test_layout_labels_are_not_claims_but_product_names_stay():
    text = "Hook: Wake up to fresh coffee.\nOn screen: Fresh every week\nSubject: Your box ships today\nTeam Box: two blends."
    got = main.sentences(text)
    assert "Wake up to fresh coffee." in got and "Fresh every week" in got
    assert not any(s.lower().startswith(("hook", "on screen", "subject")) for s in got)
    assert any(s.startswith("Team Box: two blends") for s in got)
