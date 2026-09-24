# rss-watcher

Deploy **08 of 60** of the local-LLM marketing agent. It polls RSS and Atom feeds and returns
only the items it has not returned before, as plain text, so the morning trend digest (36)
never summarises the same article twice. It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a volume on `/data`. n8n calls it at `http://rss-watcher:8000` (env `RSS_URL`).
It keeps state in SQLite, so on other hosts give it a persistent disk.

## Run

```bash
docker build -t rss-watcher .
docker run --rm -p 8108:8000 -v rss-data:/data rss-watcher
```

Use your own feed list by mounting it over the default:
`-v $PWD/my-feeds.txt:/config/feeds.txt:ro`.

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
FEEDS_FILE=config/feeds.txt DB_PATH=./data/rss.sqlite uvicorn app.main:app --port 8108
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/poll` | `{"feeds"?:[url],"max_items_per_feed":20}` | `{"new_items":[{"feed","title","link","published","summary"}],"checked","errors":[{"feed","error"}]}` |
| GET | `/items?limit=50` | — | most recent stored items, same shape as `new_items` |

```bash
curl -s localhost:8108/poll -H 'content-type: application/json' -d '{"max_items_per_feed":5}'
# {"new_items":[{"feed":"https://moz.com/feeds/blog.rss","title":"...","link":"https://moz.com/blog/...","published":"2026-09-22T14:00:00+00:00","summary":"..."}, ...],"checked":5,"errors":[]}
```

- Without `feeds`, the list comes from `FEEDS_FILE` (one URL per line, `#` comments). The
  image ships `config/feeds.txt` with five marketing/SEO blogs. No feeds at all = `422`.
- An item is new the first time its feed + guid (or link) is seen. The first poll of a feed
  returns up to `max_items_per_feed` items (1–200); later polls return only new ones.
- `published` is ISO 8601 UTC, or `null` if the feed has no date. `summary` is plain text,
  at most 500 characters.
- `checked` is the number of feeds polled. A feed that fails (timeout, HTTP error, not a
  feed, blocked address) is listed in `errors`; the rest are still polled.
- `/items` sorts by published date (fetch time if none), newest first; `limit` is 1–500.

**SSRF guard.** Feed URLs may come from the request, so they get the same guard as the other
fetching services: only `http`/`https`, and hosts resolving to loopback, private, link-local,
reserved or multicast addresses are refused (per redirect hop). Fetching: 15 s timeout, up to
5 redirects, 5 MB max.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `FEEDS_FILE` | `/config/feeds.txt` | Default feed list. |
| `DB_PATH` | `/data/rss.sqlite` | SQLite file of seen items. |
| `ALLOW_PRIVATE_URLS` | `false` | `true` allows private and loopback addresses. Only for trusted local testing. |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
