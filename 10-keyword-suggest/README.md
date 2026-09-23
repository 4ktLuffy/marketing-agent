# keyword-suggest

Deploy **10 of 53** of the local-LLM marketing agent. It turns a seed phrase into a list of
real search queries by scraping Google and DuckDuckGo autocomplete (no API keys), expanded with
question words and a–z suffixes. The keyword-research tool (32) hands the list to the LLM to
cluster by intent. It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n calls it at `http://keyword-suggest:8000` (env `KEYWORDS_URL`).
It needs outbound internet. It is stateless (results are cached in memory for an hour), so it
also runs fine on any container host (Render, Fly.io, Railway).

## Run

```bash
docker build -t keyword-suggest .
docker run --rm -p 8110:8000 keyword-suggest
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --port 8110
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/suggest` | `{"seed","expand":true,"lang":"en","country":"us"}` | `{"seed","keywords":[{"keyword","source","modifier"}],"count","errors"}` |

```bash
curl -s localhost:8110/suggest -H 'content-type: application/json' \
  -d '{"seed":"coffee subscription"}'
# {"seed":"coffee subscription","keywords":[{"keyword":"coffee subscription uk","source":"google","modifier":""}, ...],"count":368,"errors":[]}
```

`source` is `google` or `duckduckgo`. `modifier` is `""` for the plain seed, otherwise the
modifier that produced it (`how`, `what`, `why`, `best` go before the seed; `vs`, `for`,
`near me`, `cheap`, `without` and `a`–`z` go after it). With `expand` that is 36 queries per
source, run 5 at a time. Keywords are deduplicated case-insensitively, first hit wins.

A source that fails or times out does not fail the request: you get what the other source
returned plus `errors:[{"source","error","failed_queries"}]`. Partial results are not cached.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `CONCURRENCY` | `5` | Upstream requests in flight at once |
| `TOTAL_TIMEOUT_S` | `20` | Stop waiting after this; unfinished queries go into `errors` |
| `CACHE_TTL_S` | `3600` | In-memory cache lifetime per `(seed, expand, lang, country)` |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
