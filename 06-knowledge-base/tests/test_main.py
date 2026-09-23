import json
import re
import zlib

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import CHUNK_SIZE, EMBED_BATCH, app, chunk_text

OLLAMA = "http://ollama.test"
KEY = {"X-API-Key": "test-key"}
client = TestClient(app)


def fake_vector(text: str, dims: int = 32) -> list[float]:
    """Deterministic bag-of-words vector: shared words -> higher cosine similarity."""
    v = [0.0] * dims
    for word in re.findall(r"[a-z]+", text.lower()):
        v[zlib.crc32(word.encode()) % dims] += 1.0
    return v


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "kb.sqlite"))
    monkeypatch.setenv("OLLAMA_URL", OLLAMA)
    monkeypatch.setenv("EMBED_MODEL", "fake-embed")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")


@pytest.fixture
def ollama():
    with respx.mock(assert_all_called=False) as mock:
        def reply(request):
            body = json.loads(request.content)
            assert body["model"] == "fake-embed"
            return httpx.Response(200, json={"embeddings": [fake_vector(t) for t in body["input"]]})

        yield mock.post(f"{OLLAMA}/api/embed").mock(side_effect=reply)


def add(doc):
    return client.post("/docs", json=doc, headers=KEY)


# ---------- chunking ----------

def test_short_text_is_one_chunk():
    assert chunk_text("One short paragraph.") == ["One short paragraph."]


def test_chunks_respect_size_and_overlap():
    paras = [" ".join(f"Sentence {p}-{i} about coffee roasting." for i in range(12)) for p in range(6)]
    chunks = chunk_text("\n\n".join(paras))
    assert len(chunks) > 3
    assert all(len(c) <= CHUNK_SIZE for c in chunks)
    for prev, nxt in zip(chunks, chunks[1:]):
        # the next chunk starts with text from the end of the previous one
        assert prev[-40:].split()[-1] in nxt[:160]
    # chunks break at sentence ends, not mid-sentence
    assert all(c.endswith(".") for c in chunks)


def test_giant_word_is_hard_split():
    chunks = chunk_text("x" * 2000)
    assert all(len(c) <= CHUNK_SIZE for c in chunks)
    assert "".join(c.split()[-1] for c in chunks).count("x") >= 2000


# ---------- auth ----------

def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_swagger_moved_so_get_docs_is_the_api():
    assert client.get("/docs").json() == []
    assert client.get("/swagger").status_code == 200


def test_write_without_key_env_is_503(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    r = client.post("/docs", json={"title": "t", "text": "x"}, headers=KEY)
    assert r.status_code == 503
    assert "INTERNAL_API_KEY" in r.json()["detail"]
    assert client.delete("/docs/abc", headers=KEY).status_code == 503


def test_write_with_wrong_key_is_401():
    r = client.post("/docs", json={"title": "t", "text": "x"}, headers={"X-API-Key": "nope"})
    assert r.status_code == 401
    assert client.post("/docs", json={"title": "t", "text": "x"}).status_code == 401
    assert client.delete("/docs/abc").status_code == 401


# ---------- docs ----------

def test_add_list_and_replace(ollama):
    r = add({"doc_id": "pricing", "title": "Pricing", "text": "Desk Blend costs 18 dollars.",
             "source": "https://example.com/pricing"})
    assert r.status_code == 200
    assert r.json() == {"doc_id": "pricing", "chunks": 1}

    docs = client.get("/docs").json()
    assert [d["doc_id"] for d in docs] == ["pricing"]
    assert set(docs[0]) == {"doc_id", "title", "source", "chunks", "created_at"}

    long_text = "\n\n".join("Paragraph %d. " % i + "Coffee facts here. " * 40 for i in range(4))
    r = add({"doc_id": "pricing", "title": "Pricing v2", "text": long_text})
    assert r.json()["chunks"] > 1
    docs = client.get("/docs").json()
    assert len(docs) == 1 and docs[0]["title"] == "Pricing v2"
    assert docs[0]["chunks"] == r.json()["chunks"]


def test_add_generates_doc_id(ollama):
    doc_id = add({"title": "No id", "text": "Some text."}).json()["doc_id"]
    assert len(doc_id) == 32


def test_embeddings_are_batched(ollama):
    text = "\n\n".join(f"Paragraph {i}. " + "Word " * 150 for i in range(EMBED_BATCH + 5))
    n = add({"title": "Big", "text": text}).json()["chunks"]
    assert n > EMBED_BATCH
    sizes = [len(json.loads(c.request.content)["input"]) for c in ollama.calls]
    assert sum(sizes) == n and len(sizes) > 1
    assert all(s <= EMBED_BATCH for s in sizes)


def test_add_rejects_blank_text():
    assert add({"title": "t", "text": "   "}).status_code == 422


def test_ollama_failure_is_502_and_keeps_old_version(ollama):
    add({"doc_id": "d1", "title": "Old", "text": "Original text."})
    ollama.mock(return_value=httpx.Response(500, text="model not found"))
    r = add({"doc_id": "d1", "title": "New", "text": "Replacement text."})
    assert r.status_code == 502
    assert client.get("/docs").json()[0]["title"] == "Old"


def test_delete(ollama):
    add({"doc_id": "d1", "title": "T", "text": "Some text."})
    assert client.delete("/docs/d1", headers=KEY).json() == {"deleted": "d1"}
    assert client.get("/docs").json() == []
    assert client.delete("/docs/d1", headers=KEY).status_code == 404


# ---------- search ----------

def test_search_ranks_relevant_chunk_first(ollama):
    add({"doc_id": "ship", "title": "Shipping", "source": "faq",
         "text": "Shipping is free on every subscription in the US."})
    add({"doc_id": "roast", "title": "Roasting",
         "text": "We roast beans on Monday and print the roast date on the bag."})
    body = client.post("/search", json={"query": "when do you roast the beans", "k": 2}).json()
    results = body["results"]
    assert len(results) == 2
    assert results[0]["doc_id"] == "roast"
    assert set(results[0]) == {"doc_id", "title", "source", "chunk", "score"}
    assert results[0]["score"] > results[1]["score"]


def test_search_respects_k_and_empty_kb(ollama):
    assert client.post("/search", json={"query": "anything"}).json() == {"results": []}
    assert not ollama.called  # no embedding call needed for an empty KB
    for i in range(3):
        add({"doc_id": f"d{i}", "title": "T", "text": f"Doc number {i}."})
    assert len(client.post("/search", json={"query": "doc", "k": 2}).json()["results"]) == 2


def test_search_validation():
    assert client.post("/search", json={"query": ""}).status_code == 422
    assert client.post("/search", json={"query": "q", "k": 0}).status_code == 422


def test_search_ollama_down_is_502(ollama):
    add({"doc_id": "d1", "title": "T", "text": "Some text."})
    ollama.mock(side_effect=httpx.ConnectError("refused"))
    assert client.post("/search", json={"query": "text"}).status_code == 502
