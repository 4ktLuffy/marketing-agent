"""Knowledge base: chunk documents, embed them with Ollama, search by cosine similarity."""
import hmac
import math
import os
import re
import sqlite3
import uuid
from array import array
from contextlib import contextmanager
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

# /docs is our documents resource, so move FastAPI's Swagger UI out of the way.
app = FastAPI(title="knowledge-base", docs_url="/swagger")

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
EMBED_BATCH = 32

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    doc_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT,
    chunks INTEGER NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES docs(doc_id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
"""


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@contextmanager
def db():
    """One connection per request: commit on success, roll back on error, always close."""
    path = env("DB_PATH", "/data/kb.sqlite")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        with conn:
            yield conn
    finally:
        conn.close()


# ---------- chunking ----------

SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _split_long(unit: str, size: int) -> list[str]:
    """Break a unit longer than `size` at word boundaries (hard cut for giant words)."""
    out, cur = [], ""
    for word in unit.split():
        while len(word) > size:
            if cur:
                out.append(cur)
                cur = ""
            out.append(word[:size])
            word = word[size:]
        if cur and len(cur) + 1 + len(word) > size:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    if cur:
        out.append(cur)
    return out


def _tail(text: str, n: int) -> str:
    """Last ~n chars of text, starting on a word boundary."""
    if len(text) <= n:
        return text
    tail = text[-n:]
    space = tail.find(" ")
    return tail[space + 1:] if 0 <= space < len(tail) - 1 else tail


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Pack paragraphs, then sentences, into chunks of <= size chars.

    Each chunk after the first starts with the last ~`overlap` chars of the previous one,
    so a fact that straddles a boundary is still retrievable.
    """
    units: list[tuple[str, str]] = []  # (separator before unit, unit)
    for para in re.split(r"\n\s*\n", text.strip()):
        para = " ".join(para.split())
        if not para:
            continue
        sep = "\n\n"
        for sentence in SENTENCE_RE.split(para):
            for piece in _split_long(sentence, size - overlap - 1):
                units.append((sep, piece))
                sep = " "

    chunks: list[str] = []
    cur = ""
    for sep, unit in units:
        if not cur:
            cur = unit
        elif len(cur) + len(sep) + len(unit) <= size:
            cur += sep + unit
        else:
            chunks.append(cur)
            cur = _tail(cur, overlap) + " " + unit
    if cur:
        chunks.append(cur)
    return chunks


# ---------- embeddings ----------

async def embed(texts: list[str]) -> list[list[float]]:
    url = env("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/") + "/api/embed"
    model = env("EMBED_MODEL", "qwen3-embedding:0.6b")
    vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=120) as client:
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i:i + EMBED_BATCH]
            try:
                r = await client.post(url, json={"model": model, "input": batch})
                r.raise_for_status()
                got = r.json()["embeddings"]
            except (httpx.HTTPError, KeyError, ValueError) as e:
                raise HTTPException(502, f"embedding call to Ollama failed: {e}")
            if len(got) != len(batch):
                raise HTTPException(502, f"Ollama returned {len(got)} vectors for {len(batch)} inputs")
            vectors.extend(got)
    return vectors


def to_blob(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def from_blob(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------- API ----------

def require_key(x_api_key: str | None = Header(default=None)):
    expected = env("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "writes disabled: INTERNAL_API_KEY is not set on the server")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


class DocIn(BaseModel):
    doc_id: str | None = None
    title: str = Field(min_length=1)
    text: str
    source: str | None = None

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text is empty")
        return v


class SearchIn(BaseModel):
    query: str = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=50)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/docs", dependencies=[Depends(require_key)])
async def add_doc(doc: DocIn):
    doc_id = (doc.doc_id or "").strip() or uuid.uuid4().hex
    pieces = chunk_text(doc.text)
    # Embed before touching the DB, so an Ollama failure never loses the old version.
    vectors = await embed(pieces)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db() as conn:
        conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))  # replace = cascade delete
        conn.execute(
            "INSERT INTO docs (doc_id, title, source, chunks, model, created_at) VALUES (?,?,?,?,?,?)",
            (doc_id, doc.title, doc.source, len(pieces), env("EMBED_MODEL", "qwen3-embedding:0.6b"), now),
        )
        conn.executemany(
            "INSERT INTO chunks (doc_id, idx, text, embedding) VALUES (?,?,?,?)",
            [(doc_id, i, t, to_blob(v)) for i, (t, v) in enumerate(zip(pieces, vectors))],
        )
    return {"doc_id": doc_id, "chunks": len(pieces)}


@app.get("/docs")
def list_docs():
    with db() as conn:
        rows = conn.execute(
            "SELECT doc_id, title, source, chunks, created_at FROM docs ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@app.delete("/docs/{doc_id}", dependencies=[Depends(require_key)])
def delete_doc(doc_id: str):
    with db() as conn:
        n = conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,)).rowcount
    if not n:
        raise HTTPException(404, f"doc '{doc_id}' not found")
    return {"deleted": doc_id}


@app.post("/search")
async def search(req: SearchIn):
    with db() as conn:
        rows = conn.execute(
            "SELECT c.doc_id, d.title, d.source, c.text, c.embedding "
            "FROM chunks c JOIN docs d USING (doc_id)"
        ).fetchall()
    if not rows:
        return {"results": []}
    qvec = (await embed([req.query]))[0]
    scored = []
    for r in rows:
        vec = from_blob(r["embedding"])
        if len(vec) != len(qvec):  # chunk embedded with a different model; skip it
            continue
        scored.append((cosine(qvec, vec), r))
    scored.sort(key=lambda s: s[0], reverse=True)
    return {"results": [
        {"doc_id": r["doc_id"], "title": r["title"], "source": r["source"],
         "chunk": r["text"], "score": round(s, 4)}
        for s, r in scored[:req.k]
    ]}
