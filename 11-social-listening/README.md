# social-listening

Deploy **11 of 60** of the local-LLM marketing agent. It finds recent mentions of a brand,
product or topic on Hacker News (Algolia API) and Reddit (public search JSON) and returns them
in one shape, newest first, for the morning trend digest (36). No API keys. It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n calls it at `http://social-listening:8000` (env `LISTENING_URL`).
It needs outbound internet. It is stateless, so it also runs fine on any container host
(Render, Fly.io, Railway).

## Run

```bash
docker build -t social-listening .
docker run --rm -p 8111:8000 social-listening
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --port 8111
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/search` | `{"query","sources":["hackernews","reddit"],"days":7,"limit":25}` | `{"mentions":[{"source","title","url","author","score","comments","created_at","text"}],"errors":[{"source","error"}]}` |

```bash
curl -s localhost:8111/search -H 'content-type: application/json' \
  -d '{"query":"ollama","limit":5}'
# {"mentions":[{"source":"hackernews","title":"...","url":"https://news.ycombinator.com/item?id=...","created_at":"2026-09-22T14:01:07Z", ...}],
#  "errors":[{"source":"reddit","error":"HTTP 403: Reddit refused the unauthenticated request"}]}
```

- `limit` (1–100) applies per source; `days` (1–365) filters by `created_at`.
- Hacker News returns stories and comments. For a comment, `title` is the parent story's title
  and `url` is the comment's HN page.
- `text` is plain text, at most 500 characters. `created_at` is ISO 8601 UTC.
- An unknown source is a 422. A source that fails does not fail the request; it is listed in
  `errors`. Reddit often answers unauthenticated requests from datacenter or home IPs with
  403/429. That is expected, and Hacker News results still come back.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `REDDIT_USER_AGENT` | `marketing-agent-social-listening/1.0 (self-hosted brand monitoring)` | Reddit blocks generic user agents; describe your deployment |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
