"""Tests for customer-language. No network: respx mocks the gateway, Ollama, 58 and 67."""
import json
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app, contains_phrase, content_stems, find_verbatim, hybrid_score, jaccard, mine_phrases, ngrams, stem

KEY = "test-key"
AUTH = {"X-API-Key": KEY}
GW = "http://gw.test"
OLLAMA = "http://ollama.test"
SAMPLES = Path(__file__).parent.parent / "samples" / "northwind_sources.json"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "voc.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("GATEWAY_URL", GW)
    monkeypatch.setenv("OLLAMA_URL", OLLAMA)
    monkeypatch.delenv("REVIEWS_URL", raising=False)
    monkeypatch.delenv("GSC_URL", raising=False)


client = TestClient(app)

SOURCES = [
    {"kind": "review", "source_ref": "r1", "text": "My box arrived late twice this month. I never run out otherwise."},
    {"kind": "review", "source_ref": "r2", "text": "Arrived late again!  Great coffee though, and I never run out."},
    {"kind": "support", "source_ref": "s1", "text": "My order arrived late and I'm out of coffee."},
    {"kind": "search_query", "source_ref": "q1", "text": "decaf that doesn't taste like decaf"},
    {"kind": "review", "source_ref": "r3", "text": "The decaf doesn't taste like decaf. Lovely afternoon cup."},
]


def imp(items=SOURCES):
    r = client.post("/sources/import", json=items, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def gw_reply(output: dict):
    return httpx.Response(200, json={"prompt": "x", "model": "mkt-writer", "output": output, "attempts": 1})


def ids_by_ref() -> dict:
    return {s["source_ref"]: s["id"] for s in client.get("/sources").json()}


# ---------- basics


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("method,path,body", [
    ("post", "/sources/import", []),
    ("post", "/sources/pull", None),
    ("post", "/mine", {}),
    ("post", "/headlines", {"topic": "x"}),
    ("post", "/personas", {}),
])
def test_writes_need_the_key(method, path, body):
    assert getattr(client, method)(path, json=body).status_code == 401
    assert getattr(client, method)(path, json=body, headers={"X-API-Key": "wrong"}).status_code == 401


def test_writes_refused_when_service_has_no_key(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.post("/sources/import", json=SOURCES, headers=AUTH).status_code == 503


def test_import_is_idempotent_and_updates_changed_text():
    assert imp() == {"added": 5, "updated": 0, "unchanged": 0}
    changed = [dict(SOURCES[0], text="Edited review text here.")] + SOURCES[1:]
    assert imp(changed) == {"added": 0, "updated": 1, "unchanged": 4}
    assert client.get("/stats").json()["sources_by_kind"] == {"review": 3, "support": 1, "search_query": 1}


@pytest.mark.parametrize("bad", [
    {"kind": "tweet", "source_ref": "a", "text": "x"},
    {"kind": "review", "source_ref": " ", "text": "x"},
    {"kind": "review", "source_ref": "a", "text": "x", "weight": 0},
])
def test_import_validates(bad):
    assert client.post("/sources/import", json=[bad], headers=AUTH).status_code == 422
    assert client.get("/sources").json() == []


# ---------- text helpers


def test_ngrams_are_trimmed_and_do_not_cross_punctuation():
    got = {n for n, _ in ngrams("The box arrived late. Again, it was the grind-size.", 2, 5)}
    assert "box arrived late" in got and "arrived late" in got
    assert not any(g.startswith("the ") or g.endswith(" the") for g in got)
    assert "late again" not in got  # crosses a full stop
    assert "grind size" in got      # a hyphen inside a word is not a break
    spans = dict(ngrams("It's the Grind-Size", 2, 2))
    assert spans["grind size"] == "Grind-Size"  # verbatim span from the source


def test_negations_may_start_but_not_end_a_phrase():
    got = {n for n, _ in ngrams("It doesn't taste like decaf and I never run out", 2, 5)}
    assert "doesn't taste like decaf" in got and "never run out" in got
    assert not any(g.endswith("doesn't") for g in got)


def test_find_verbatim_allows_case_space_apostrophes_only():
    text = "The decaf doesn’t   taste like decaf. Lovely."
    assert find_verbatim("doesn't taste like DECAF", text) == "doesn’t   taste like decaf"
    assert find_verbatim("“doesn't taste like decaf”", text) == "doesn’t   taste like decaf"
    assert find_verbatim("does not taste like decaf", text) is None   # paraphrase
    assert find_verbatim("decaf ... Lovely", text) is None             # stitched


def test_contains_phrase_whole_words():
    assert contains_phrase("Never Run Out of beans again", "never run out")
    assert not contains_phrase("never run outside", "never run out")


# ---------- mining: phrases


def test_mine_phrases_need_two_sources_and_drop_subsumed():
    src = [dict(s, id=i + 1, weight=1.0) for i, s in enumerate(SOURCES)]
    got = {p["normalized"]: p for p in mine_phrases(src)}
    assert got["arrived late"]["sources"] == 3
    assert got["never run out"]["sources"] == 2
    assert "doesn't taste like decaf" in got
    assert "taste like decaf" not in got  # same sources as the longer phrase
    assert "twice this month" not in got  # one source only
    for p in got.values():  # every example is a verbatim part of its source
        for ex in p["examples"]:
            assert ex["text"] in src[ex["source_id"] - 1]["text"]


def test_duplicate_texts_count_once_and_ignore_list():
    src = [{"id": 1, "kind": "review", "text": "Team Box is great", "weight": 1.0},
           {"id": 2, "kind": "review", "text": "team box is  great", "weight": 1.0},
           {"id": 3, "kind": "review", "text": "The Team Box works", "weight": 1.0}]
    got = {p["normalized"]: p for p in mine_phrases(src)}
    assert got["team box"]["sources"] == 2
    assert "team box" not in {p["normalized"] for p in mine_phrases(src, ignore=["Team Box"])}


# ---------- mining: themes


THEMES_OUT = lambda ids: {"themes": [  # noqa: E731
    {"name": "late deliveries", "kind": "pain", "quotes": [
        {"source_id": f"s{ids['r1']}", "quote": "my box arrived LATE twice this month"},   # ok (case)
        {"source_id": f"s{ids['r2']}", "quote": "Arrived late again!"},                      # ok
        {"source_id": f"s{ids['s1']}", "quote": "my order came late"},                       # paraphrase
        {"source_id": "s999", "quote": "arrived late"},                                      # unknown
    ]},
    {"name": "decaf tastes real", "kind": "outcome", "quotes": [
        {"source_id": f"s{ids['r3']}", "quote": "doesn't taste like decaf"},
        {"source_id": f"s{ids['q1']}", "quote": "decaf that tastes like real coffee"},       # invented
    ]},
    {"name": "bad kind", "kind": "feeling", "quotes": []},
]}


@respx.mock
def test_mine_keeps_only_verbatim_quotes_and_themes_with_two():
    imp()
    ids = ids_by_ref()
    respx.post(f"{GW}/v1/run").mock(return_value=gw_reply(THEMES_OUT(ids)))
    r = client.post("/mine", json={"batch_size": 50}, headers=AUTH)
    assert r.status_code == 200, r.text
    t = r.json()["themes"]
    assert t["kept"] == 1 and t["invalid"] == 1
    assert t["quotes_dropped"] == {"not_verbatim": 2, "unknown_source": 1}
    assert [d["name"] for d in t["dropped"]] == ["decaf tastes real"]
    themes = client.get("/themes").json()
    assert len(themes) == 1 and themes[0]["kind"] == "pain" and themes[0]["sources"] == 2
    src = {s["id"]: s["text"] for s in client.get("/sources").json()}
    for q in themes[0]["quotes"]:
        assert q["quote"] in src[q["source_id"]]  # stored exactly as in the source
    assert themes[0]["quotes"][0]["quote"] == "My box arrived late twice this month"
    assert client.get("/themes?kind=outcome").json() == []
    body = json.loads(respx.calls.last.request.content)
    assert body["prompt"] == "customer_themes" and f"[s{ids['r1']}] (review)" in body["vars"]["snippets"]
    assert respx.calls.last.request.headers["X-API-Key"] == KEY


@respx.mock
def test_quotes_must_come_from_the_batch_they_were_shown_in():
    imp()
    ids = ids_by_ref()
    respx.post(f"{GW}/v1/run").mock(return_value=gw_reply(THEMES_OUT(ids)))
    r = client.post("/mine", json={"batch_size": 2}, headers=AUTH).json()
    assert r["themes"]["batches"] == 3
    assert r["themes"]["quotes_dropped"]["unknown_source"] > 3


@respx.mock
def test_mine_keeps_phrases_and_old_themes_when_gateway_is_down():
    imp()
    ids = ids_by_ref()
    route = respx.post(f"{GW}/v1/run")
    route.mock(return_value=gw_reply(THEMES_OUT(ids)))
    client.post("/mine", json={"batch_size": 50}, headers=AUTH)
    route.mock(side_effect=httpx.ConnectError("down"))
    r = client.post("/mine", json={"batch_size": 50}, headers=AUTH).json()
    assert r["themes"]["ran"] is False and "unreachable" in r["themes"]["llm_errors"][0]
    assert len(client.get("/themes").json()) == 1
    assert client.get("/phrases").json()


def test_mine_without_llm_and_without_sources():
    assert client.post("/mine", json={"llm": False}, headers=AUTH).status_code == 409
    imp()
    r = client.post("/mine", json={"llm": False}, headers=AUTH).json()
    assert r["themes"] is None and r["phrases"]["kept"] > 0
    phrases = client.get("/phrases?min_sources=3").json()
    assert [p["phrase"] for p in phrases] == ["arrived late"]


# ---------- relevant


def fake_embed(request):
    """Deterministic vectors: dimension 0 = 'late', 1 = 'decaf', 2 = everything else."""
    texts = json.loads(request.content)["input"]
    vec = lambda t: [1.0 if "late" in t.lower() else 0.0, 1.0 if "decaf" in t.lower() else 0.0, 0.1]  # noqa: E731
    return httpx.Response(200, json={"embeddings": [vec(t) for t in texts]})


@respx.mock
def test_relevant_uses_embeddings_and_caches_them():
    imp()
    client.post("/mine", json={"llm": False}, headers=AUTH)
    route = respx.post(f"{OLLAMA}/api/embed").mock(side_effect=fake_embed)
    r = client.get("/relevant", params={"topic": "when a delivery is late", "k": 1}).json()
    assert r["method"] == "hybrid" and route.call_count == 1
    assert all("late" in i["text"].lower() for i in r["items"])
    assert r["customer_phrases"].startswith("- ")
    client.get("/relevant", params={"topic": "when a delivery is late", "k": 2})
    assert route.call_count == 1  # all vectors came from the cache


@respx.mock
def test_relevant_falls_back_to_keywords_when_ollama_is_down():
    imp()
    client.post("/mine", json={"llm": False}, headers=AUTH)
    respx.post(f"{OLLAMA}/api/embed").mock(side_effect=httpx.ConnectError("down"))
    r = client.get("/relevant", params={"topic": "decaf for the afternoon", "k": 5}).json()
    assert r["method"] == "keyword"
    assert r["items"] and all(i["score"] > 0 for i in r["items"])
    assert "decaf" in r["items"][0]["text"].lower()


def test_stem_and_jaccard():
    assert stem("running") == stem("run") and stem("arrived") == stem("arrive") and stem("bags") == stem("bag")
    assert stem("coffee") == "coffee"
    t = content_stems("Never running out of coffee in the middle of the week")
    assert jaccard(t, content_stems("never run out")) > jaccard(t, content_stems("coffee subscription"))
    assert jaccard(set(), set()) == 0.0


def test_hybrid_score_is_0_6_embedding_plus_0_4_jaccard(monkeypatch):
    t = content_stems("pausing a delivery before you travel")
    # jaccard({paus, delivery, travel}, {paus, delivery}) = 2/3
    assert hybrid_score(0.5, t, "pause delivery") == pytest.approx(0.6 * 0.5 + 0.4 * 2 / 3)
    monkeypatch.setenv("HYBRID_EMBED_WEIGHT", "1")
    assert hybrid_score(0.5, t, "pause delivery") == pytest.approx(0.5)


def generic_embed(request):
    """The failure seen with qwen3-embedding:0.6b: the generic phrase is nearest to every topic."""
    texts = json.loads(request.content)["input"]
    vec = lambda t: [1.0, 0.0] if "coffee subscription" in t.lower() else [0.8, 0.6]  # noqa: E731
    return httpx.Response(200, json={"embeddings": [[1.0, 0.0] if i == 0 else vec(t) for i, t in enumerate(texts)]})


@respx.mock
def test_relevant_hybrid_rerank_beats_a_generic_nearest_phrase():
    imp([{"kind": "review", "source_ref": f"r{i}", "text": t} for i, t in enumerate([
        "Great coffee subscription. I pause my subscription when I travel.",
        "Best coffee subscription ever; I pause my subscription every summer.",
    ])])
    client.post("/mine", json={"llm": False}, headers=AUTH)
    respx.post(f"{OLLAMA}/api/embed").mock(side_effect=generic_embed)
    r = client.get("/relevant", params={"topic": "How to pause my subscription before a trip", "k": 2}).json()
    items = {i["text"]: i for i in r["items"]}
    assert items["coffee subscription"]["embedding_score"] > items["pause my subscription"]["embedding_score"]
    assert r["items"][0]["text"] == "pause my subscription"  # keyword overlap flips the order
    top = r["items"][0]
    assert top["score"] == pytest.approx(0.6 * top["embedding_score"] + 0.4 * top["keyword_jaccard"], abs=1e-3)


def test_relevant_rejects_unknown_kinds():
    assert client.get("/relevant", params={"topic": "x", "kinds": "tweets"}).status_code == 422


# ---------- headlines


@respx.mock
def test_headlines_keep_only_verbatim_phrase_no_new_numbers_no_quotes():
    imp()
    client.post("/mine", json={"llm": False}, headers=AUTH)
    respx.post(f"{OLLAMA}/api/embed").mock(side_effect=fake_embed)
    gw = respx.post(f"{GW}/v1/run").mock(return_value=gw_reply({"headlines": [
        {"phrase_id": "p1", "text": "Arrived late? Not on our watch."},
        {"phrase_id": "p1", "text": "Late deliveries, fixed."},                     # no phrase
        {"phrase_id": "p1", "text": "Arrived late 3 times? Never again."},          # invented number
        {"phrase_id": "p1", "text": "“Arrived late” is not in our vocabulary."},    # presented as a quote
        {"phrase_id": "p1", "text": "arrived late? not on our watch."},              # duplicate
    ]}))
    r = client.post("/headlines", json={"topic": "late delivery", "channel": "x", "n": 5, "k": 3}, headers=AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert [h["text"] for h in out["headlines"]] == ["Arrived late? Not on our watch."]
    assert out["headlines"][0]["phrase"].lower() == "arrived late" and len(out["headlines"][0]["source_ids"]) == 3
    reasons = [d["reason"] for d in out["dropped"]]
    assert reasons[0].startswith("does not contain") and reasons[1].startswith("number")
    assert reasons[2].startswith("quotation marks") and reasons[3] == "duplicate"
    sent = json.loads(gw.calls.last.request.content)
    assert sent["prompt"] == "customer_headlines" and sent["vars"]["facts"] == ""
    assert "[p1] " in sent["vars"]["phrases"]


def test_headlines_reject_phrases_that_were_not_mined():
    imp()
    client.post("/mine", json={"llm": False}, headers=AUTH)
    r = client.post("/headlines", json={"topic": "x", "phrases": ["totally invented"]}, headers=AUTH)
    assert r.status_code == 422


# ---------- personas


@respx.mock
def test_personas_drop_uncited_and_invented_attributes():
    imp()
    ids = ids_by_ref()
    gw = respx.post(f"{GW}/v1/run")
    gw.mock(return_value=gw_reply(THEMES_OUT(ids)))
    client.post("/mine", json={"batch_size": 50, "min_theme_sources": 1, "min_quotes": 1}, headers=AUTH)
    themes = client.get("/themes").json()
    late = next(t for t in themes if t["kind"] == "pain")
    q_late = late["quotes"][0]["id"]
    gw.mock(return_value=gw_reply({"personas": [
        {"label": "the one who runs out", "goals": [{"text": "Never be without coffee", "cites": [late["id"]]}],
         "pains": [{"text": "Boxes arrive late", "cites": [q_late]},
                   {"text": "Hates the price", "cites": []}],                       # no citation
         "objections": [{"text": "Worried about 5 late boxes", "cites": [q_late]}],  # invented number
         "words_they_use": [{"text": "arrived late twice", "cites": [q_late]},
                            {"text": "always late", "cites": [q_late]}]},          # not verbatim
        {"label": "Sarah, 34, busy mum", "goals": [{"text": "x", "cites": ["t999"]}],
         "pains": [], "objections": [], "words_they_use": []},
    ]}))
    r = client.post("/personas", json={"n": 2}, headers=AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["personas"]) == 1
    p = out["personas"][0]
    assert p["label"] == "the one who runs out"
    assert [a["text"] for a in p["attributes"]["pains"]] == ["Boxes arrive late"]
    assert [a["text"] for a in p["attributes"]["words_they_use"]] == ["arrived late twice"]
    assert p["attributes"]["objections"] == []
    assert all(e["quote"] for a in p["attributes"]["goals"] for e in a["evidence"])
    reasons = {d["reason"] for d in out["attributes_dropped"]}
    assert any(x.startswith("no valid citation") for x in reasons)
    assert "number not in the cited quotes" in reasons
    assert "words_they_use is not a verbatim part of a cited quote" in reasons
    assert out["labels_replaced"][0]["label"] == "Sarah, 34, busy mum"
    assert out["personas_dropped"][0]["label"] == "Persona 2"
    assert out["built_from"]["themes"] == len(themes) and "real customer sources" in out["note"]
    assert client.get("/personas").json()["id"] == out["id"]


def test_personas_need_themes():
    imp()
    assert client.post("/personas", json={}, headers=AUTH).status_code == 409


# ---------- pull


@respx.mock
def test_pull_imports_reviews_and_queries(monkeypatch):
    monkeypatch.setenv("REVIEWS_URL", "http://reviews.test")
    monkeypatch.setenv("GSC_URL", "http://gsc.test")
    respx.get("http://reviews.test/reviews").mock(return_value=httpx.Response(200, json=[
        {"id": 7, "text": "Arrived late again.", "status": "replied"}, {"id": 8, "text": " "}]))
    respx.get("http://gsc.test/queries/opportunities").mock(return_value=httpx.Response(200, json={
        "queries": [{"query": "decaf that doesn't taste like decaf", "impressions": 1000}]}))
    r = client.post("/sources/pull", headers=AUTH).json()
    assert r["reviews"] == {"ok": True, "fetched": 1, "added": 1, "updated": 0, "unchanged": 0}
    assert r["search_queries"]["added"] == 1
    refs = {s["source_ref"]: s for s in client.get("/sources").json()}
    assert refs["gsc:decaf that doesn't taste like decaf"]["weight"] == 3.0
    assert client.post("/sources/pull", headers=AUTH).json()["reviews"]["unchanged"] == 1


@respx.mock
def test_pull_is_fail_soft(monkeypatch):
    monkeypatch.setenv("REVIEWS_URL", "http://reviews.test")
    monkeypatch.setenv("GSC_URL", "http://gsc.test")
    respx.get("http://reviews.test/reviews").mock(side_effect=httpx.ConnectError("down"))
    respx.get("http://gsc.test/queries/opportunities").mock(
        return_value=httpx.Response(409, json={"detail": "no sync yet"}))
    r = client.post("/sources/pull", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["reviews"] == {"ok": False, "error": "ConnectError"}
    assert r.json()["search_queries"]["ok"] is False and "409" in r.json()["search_queries"]["error"]


def test_pull_without_config_does_nothing():
    r = client.post("/sources/pull", headers=AUTH).json()
    assert r["reviews"]["skipped"] and r["search_queries"]["skipped"]


# ---------- sample data


def test_sample_file_is_marked_fictional_and_imports():
    data = json.loads(SAMPLES.read_text())
    assert list(data)[0] == "_notice" and data["_notice"].startswith("FICTIONAL EXAMPLE DATA")
    kinds = [s["kind"] for s in data["sources"]]
    assert kinds.count("review") >= 35 and kinds.count("search_query") >= 20 and kinds.count("support") >= 8
    assert imp(data["sources"])["added"] == len(data["sources"])
    assert client.post("/mine", json={"llm": False}, headers=AUTH).json()["phrases"]["kept"] >= 20


def test_merge_themes_by_name_or_shared_quote():
    from collections import Counter

    from app.main import merge_themes
    st = {"themes_dropped": [], "themes_merged": 0, "quotes_dropped": Counter()}
    q = lambda sid, text: {"source_id": sid, "quote": text}  # noqa: E731
    got = merge_themes([
        {"name": "delivery_late", "kind": "pain", "quotes": [q(1, "arrived late"), q(2, "late again")]},
        {"name": "Delivery late", "kind": "pain", "quotes": [q(3, "box came late")]},
        {"name": "unreliable delivery", "kind": "pain", "quotes": [q(2, "late again"), q(4, "hit and miss")]},
        {"name": "late but kind", "kind": "outcome", "quotes": [q(1, "arrived late"), q(5, "nice note")]},
    ], 2, 2, st)
    assert [(t["name"], len(t["quotes"])) for t in got] == [("delivery late", 4), ("late but kind", 2)]
    assert st["themes_merged"] == 1 and st["quotes_dropped"]["duplicate"] == 1
