# knowledge-base

Deploy **06 of 53** of the local-LLM marketing agent. It is the agent's memory of your
business: you add documents (FAQ, product pages, past posts), it splits them into chunks,
embeds them with a local Ollama embedding model and stores everything in SQLite. The blog
writer and the KB-answer tool search it so the LLM writes from your facts, not its guesses.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a volume on `/data`. n8n calls it at `http://knowledge-base:8000`
(env `KB_URL`). It needs to reach Ollama (default `http://host.docker.internal:11434`,
i.e. Ollama running on the Docker host itself).

## Run

```bash
ollama pull qwen3-embedding:0.6b
docker build -t knowledge-base .
docker run --rm -p 8106:8000 -v kb-data:/data -e INTERNAL_API_KEY=change-me \
  --add-host=host.docker.internal:host-gateway knowledge-base
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
DB_PATH=./data/kb.sqlite OLLAMA_URL=http://localhost:11434 INTERNAL_API_KEY=change-me \
  uvicorn app.main:app --port 8106
```

## Endpoints

(key) = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/docs` (key) | `{"doc_id"?,"title","text","source"?}` | `{"doc_id","chunks"}` |
| GET | `/docs` | — | `[{"doc_id","title","source","chunks","created_at"}]`, newest first |
| DELETE | `/docs/{doc_id}` (key) | — | `{"deleted":doc_id}`, 404 if unknown |
| POST | `/search` | `{"query","k":5}` | `{"results":[{"doc_id","title","source","chunk","score"}]}` |

```bash
curl -s localhost:8106/docs -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"doc_id":"shipping","title":"Shipping FAQ","source":"faq","text":"Shipping is free on every subscription in the US.\n\nYou can pause or skip a delivery any time from your account page."}'
# {"doc_id":"shipping","chunks":1}

curl -s localhost:8106/search -H 'content-type: application/json' \
  -d '{"query":"can I pause my subscription?","k":2}'
# {"results":[{"doc_id":"shipping","title":"Shipping FAQ","source":"faq","chunk":"Shipping is free ...","score":0.5431}, ...]}
```

- `POST /docs` without `doc_id` generates one; with an existing `doc_id` it **replaces**
  that document. Embedding happens first, so if Ollama fails (502) the old version stays.
- Writes return **503** when `INTERNAL_API_KEY` is not set on the server, **401** on a
  missing or wrong key.
- Ollama unreachable or erroring gives **502** on `POST /docs` and `POST /search`.
- `k` is 1 to 50. `score` is cosine similarity (higher is closer).
- Swagger UI lives at `/swagger`, because `/docs` is the documents resource.

How it works: text is split on blank lines into paragraphs, then into sentences, and packed
into chunks of at most 800 characters; each chunk after the first starts with the last
~120 characters of the one before. Chunks are embedded in batches of 32 via
`POST {OLLAMA_URL}/api/embed` and stored as float32 blobs. Search scores every chunk in
pure Python, which is fine for a few thousand chunks (a small-business KB); chunks embedded
with a model of a different vector size are skipped, so re-add documents after changing
`EMBED_MODEL`.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | (unset) | required for writes; unset disables them (503) |
| `DB_PATH` | `/data/kb.sqlite` | SQLite file |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama base URL |
| `EMBED_MODEL` | `qwen3-embedding:0.6b` | Ollama embedding model |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
