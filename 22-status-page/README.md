# status-page

Deploy **22 of 60** of the local-LLM marketing agent. It checks every service in the
stack at the same time (`GET /health`, 3 s timeout) and shows the result as one
auto-refreshing page with green and red dots, plus a JSON endpoint for n8n or uptime
monitors. It can also check Ollama. It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, so it can reach every service by name. Publish its port (for example 8122)
to see the page in your browser. It keeps no state.

## Run

```bash
docker build -t status-page .
docker run --rm -p 8122:8000 --network marketing status-page
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
SERVICES="utm-builder=http://localhost:8115,readability=http://localhost:8113" \
  uvicorn app.main:app --port 8122
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| GET | `/status` | — | `{"checked_at","all_ok","services":[{"name","url","ok","latency_ms","error"}]}` |
| GET | `/` | — | HTML table, refreshes every 30 s |

```bash
curl -s localhost:8122/status
# {"checked_at":"2026-09-23T10:00:00Z","all_ok":false,"services":[
#   {"name":"llm-gateway","url":"http://llm-gateway:8000","ok":true,"latency_ms":4,"error":null},
#   {"name":"rss-watcher","url":"http://rss-watcher:8000","ok":false,"latency_ms":3001,"error":"timeout after 3s"}, ...]}
```

- A service is `ok` only if `/health` answers HTTP 200 within the timeout. Otherwise
  `error` says why: `HTTP 503`, `timeout after 3s`, or the connection error.
- `all_ok` is `true` when every check passed. It is an extra field that makes n8n
  alerts easier.
- Every call runs the checks again. There is no cache, so do not poll it more often
  than you need.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `SERVICES` | every stack service | `name=url,name=url`. A bare `name` means `http://name:8000`. |
| `OLLAMA_URL` | — | If set, also checks `GET $OLLAMA_URL/api/tags`, shown as `ollama` |
| `CHECK_TIMEOUT` | `3` | Seconds per check |

By default, the page checks `llm-gateway, brand-service, knowledge-base, page-extractor,
rss-watcher, change-monitor, keyword-suggest, social-listening, seo-auditor, readability,
platform-rules, utm-builder, link-shortener, image-cards, email-renderer,
content-calendar, analytics-ingest, report-builder`, each at `http://<name>:8000`.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
