# change-monitor

Deploy **09 of 53** of the local-LLM marketing agent. It watches competitor pages (pricing,
features, landing pages) and, on each check, reports which ones changed with a line diff of
their visible text. The competitor-watch schedule (37) runs `/check` every 6 hours and has the
LLM summarise the diffs. It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a volume on `/data`. n8n calls it at `http://change-monitor:8000`
(env `MONITOR_URL`). It keeps state in SQLite, so on other hosts give it a persistent disk.

## Run

```bash
docker build -t change-monitor .
docker run --rm -p 8109:8000 -e INTERNAL_API_KEY=change-me -v monitor-data:/data change-monitor
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./data/monitor.sqlite uvicorn app.main:app --port 8109
```

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/watches` 🔑 | `{"url","label"?}` | `201` watch `{"id","url","label","created_at","last_checked_at"}` |
| GET | `/watches` | — | `[watch]` |
| DELETE | `/watches/{id}` 🔑 | — | `204`, or `404` |
| POST | `/check` | — | `{"changed":[{"id","url","label","diff","added_words","removed_words"}],"unchanged","errors":[{"id","url","error"}]}` |

```bash
curl -s localhost:8109/watches -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"url":"https://example.com","label":"Example home"}'
# {"id":1,"url":"https://example.com","label":"Example home","created_at":"2026-09-23T08:00:00+00:00","last_checked_at":null}
curl -s -X POST localhost:8109/check
# {"changed":[],"unchanged":1,"errors":[]}
```

- The first check of a watch stores a baseline and counts as unchanged.
- Page text is the visible text without `<head>`, scripts, styles, `<nav>` and `<footer>`,
  one line per block with whitespace collapsed, so a changed copyright year or menu does not
  count as a change.
- `diff` is a unified diff of those lines (`-` before, `+` after), capped at about 4,000
  characters. `added_words` / `removed_words` are word counts (multiset difference).
- `unchanged` is a count. A page that cannot be fetched goes to `errors` and keeps its old
  snapshot; the other watches are still checked.
- `/check` needs no key: it only reads the watched pages.

Errors: `401` wrong or missing `X-API-Key`. `503` on write endpoints when `INTERNAL_API_KEY`
is not set on the server. `422` if a watch URL is blocked (see below).

**SSRF guard.** Only `http`/`https`. The host is resolved and refused if any address is
loopback, private, link-local, reserved or multicast, both when the watch is added and on
every fetch, including each redirect hop. Fetching: 15 s timeout, up to 5 redirects, 5 MB max.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for write endpoints. Unset = writes return `503`. |
| `DB_PATH` | `/data/monitor.sqlite` | SQLite file with watches and snapshots. |
| `ALLOW_PRIVATE_URLS` | `false` | `true` allows private and loopback addresses. Only for trusted local testing. |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
