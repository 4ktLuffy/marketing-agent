# link-shortener

Deploy **16 of 60** of the local-LLM marketing agent. It turns long UTM links into short ones
on your own domain and counts clicks per day and per referring site, so the publisher (39) can
post tidy links and the weekly report can show what got clicked. It stores no IP addresses.
It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a **public URL** (reverse proxy or tunnel) because people click the links.
n8n calls it at `http://link-shortener:8000` (env `SHORTENER_URL`). State lives in SQLite
at `/data/links.sqlite`, so mount a volume on `/data`.

## Run

```bash
docker build -t link-shortener .
docker run --rm -p 8116:8000 -v links-data:/data \
  -e INTERNAL_API_KEY=change-me -e BASE_URL=https://go.example.com link-shortener
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
DB_PATH=./links.sqlite INTERNAL_API_KEY=change-me uvicorn app.main:app --port 8116
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/links` 🔑 | `{"url","slug"?}` | 201 `{"slug","short_url","url"}` |
| GET | `/{slug}` | — | 302 to the long URL, records a click |
| GET | `/links` | `?utm_campaign=&utm_content=&limit=100` | `[{"slug","short_url","url","clicks","created_at"}]`, newest first |
| GET | `/links/{slug}/stats` | — | `{"slug","url","clicks","by_day":{"YYYY-MM-DD":n},"referrers":{"host":n}}` |

🔑 = header `X-API-Key: $INTERNAL_API_KEY`. Wrong or missing key → 401. If the service has
no `INTERNAL_API_KEY` set, writes are refused with 503.

```bash
curl -s localhost:8116/links -H 'X-API-Key: change-me' -H 'content-type: application/json' \
  -d '{"url":"https://example.com/sale?utm_source=linkedin","slug":"spring"}'
# {"slug":"spring","short_url":"http://localhost:8116/spring","url":"https://example.com/sale?utm_source=linkedin"}
curl -s localhost:8116/links/spring/stats
# {"slug":"spring","url":"...","clicks":1,"by_day":{"2026-09-22":1},"referrers":{"news.ycombinator.com":1}}
```

- `url` must be absolute `http(s)`, otherwise 422.
- No `slug` → a random 6-character base62 slug. A custom slug must match
  `^[a-zA-Z0-9_-]{3,40}$` (422 otherwise). A taken slug is 409. `health`, `links`, `docs`,
  `openapi.json` and `redoc` are reserved (422).
- Unknown slug → 404. A click stores the UTC time and the referrer's host name only.
  Clicks with no `Referer` are counted under `direct`. Days are UTC.

### Listing links

`GET /links` needs no key. It returns links newest first, each with its total click count.

- `utm_campaign` and `utm_content` filter on the query string of the **target** URL
  (parsed with `urllib.parse`, so `%20` and `+` are decoded). Matching is exact and
  case-sensitive: `utm_campaign=spring` does not match `spring-sale` or `Spring`. Both
  filters together must both match. An empty value (`?utm_campaign=`) means no filter.
- No filters → all links, up to `limit`.
- `limit` is 1–1000 (default 100); anything else is 422. It applies after filtering.
- `links` is a reserved slug, so `/links` never collides with `GET /{slug}`.

```bash
curl -s 'localhost:8116/links?utm_campaign=spring-launch&limit=10'
# [{"slug":"spring","short_url":"http://localhost:8116/spring","url":"https://example.com/sale?utm_campaign=spring-launch&utm_content=12","clicks":4,"created_at":"2026-09-22T09:00:00+00:00"}]
```

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — (required for `POST /links`) | Shared key for write calls |
| `BASE_URL` | `http://localhost:8116` | Public short domain used in `short_url` |
| `DB_PATH` | `/data/links.sqlite` | SQLite file |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
