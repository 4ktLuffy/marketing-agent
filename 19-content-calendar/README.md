# content-calendar

Deploy **19 of 53** of the local-LLM marketing agent. It is the central store for every
piece of content: the chat agent saves drafts here, a human approves them here, and the
publisher picks up approved, due items from here. It enforces the review workflow, so
nothing reaches `approved` without passing `in_review`. It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a volume on `/data`. n8n calls it at `http://content-calendar:8000`
(env `CALENDAR_URL`). It keeps state in SQLite, so run exactly one instance and back up
the volume.

## Run

```bash
docker build -t content-calendar .
docker run --rm -p 8119:8000 -e INTERNAL_API_KEY=change-me -v calendar-data:/data content-calendar
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./calendar.sqlite uvicorn app.main:app --port 8119
```

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/items` 🔑 | `{"title","channel","body","status"?,"scheduled_at"?,"campaign"?,"campaign_id"?,"link"?,"notes"?}` | item, `201` |
| GET | `/items` | `?status=&channel=&campaign_id=&from=&to=` | `[item]` |
| GET | `/items/{id}` | — | item |
| PATCH | `/items/{id}` 🔑 | any of `title, channel, body, scheduled_at, campaign, campaign_id, link, notes` | item |
| POST | `/items/{id}/status` 🔑 | `{"status","note"?}` | item |
| POST | `/items/{id}/published` 🔑 | `{"external_url"?,"short_url"?}` (body optional) | item |
| GET | `/due` | `?now=ISO` (default: now, UTC) | `[item]` approved, `scheduled_at <= now` |

An item is:

```json
{"id":1,"title":"Launch post","channel":"linkedin","body":"...","status":"draft",
 "scheduled_at":"2026-10-01T09:00:00Z","published_at":null,"campaign":"launch",
 "link":"https://example.com","external_url":null,"notes":null,
 "created_at":"2026-09-23T10:00:00Z","updated_at":"2026-09-23T10:00:00Z",
 "campaign_id":3,"short_url":null}
```

- `campaign_id` (integer ≥ 1 or `null`) links the item to a campaign in
  `45-campaign-service`. It is set via `POST /items` or `PATCH`, and `PATCH` can change
  it at any status, including after approval. A string such as `"3"` is a 422 error.
  `GET /items?campaign_id=N` combines with `status`, `channel`, `from` and `to`.
- `campaign` (free text) is the older label field; it is unchanged and separate from `campaign_id`.
- `short_url` is set only by `POST /items/{id}/published` (the shortened link that was
  posted). `PATCH` does not accept it.
- A database created by an older version gets the `campaign_id` and `short_url` columns
  added on first use (`ALTER TABLE ... ADD COLUMN`). Existing rows read them as `null`.

```bash
curl -s localhost:8119/items -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"title":"Launch post","channel":"linkedin","body":"We launched.","scheduled_at":"2026-10-01T11:00:00+02:00"}'
# {"id":1,...,"status":"draft","scheduled_at":"2026-10-01T09:00:00Z",...}

curl -s localhost:8119/items/1/status -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"status":"approved"}'
# 409 {"detail":{"message":"cannot move from draft to approved","current":"draft","allowed":["in_review","rejected"]}}
```

### Status workflow

| From | Allowed next |
|---|---|
| `idea` | `draft` |
| `draft` | `in_review`, `rejected` |
| `in_review` | `approved`, `draft`, `rejected` |
| `approved` | `published`, `draft` |
| `rejected` | `draft` |
| `published` | — (final) |

- New items start as `draft` unless you send `idea` or `in_review`. Any other start
  status is a 422 error.
- An invalid move is a `409` error whose `detail` lists `current` and `allowed`.
- With a `note`, `/status` appends a line such as
  `[2026-09-23T10:00:00Z] in_review -> draft: tone too salesy` to `notes`.
- `PATCH` cannot change `status`; sending it is a 422 error. For an `approved` or
  `published` item, `PATCH` also refuses to change `title`, `channel`, `body` or
  `link` (409 error), because a human approved that exact text. Move the item back to
  `draft` to edit it. You can still change `scheduled_at`, `campaign`, `campaign_id` and
  `notes`. In `idea`, `draft` and `in_review` all fields can be edited, including `body`.
- Moving to `published` (via `/status` or `/published`) sets `published_at`.
  `/published` works only from `approved`.

### Times

- `scheduled_at`, `now`, `from` and `to` take ISO 8601 with or without an offset.
  A time without an offset is read as UTC. Times are stored and returned as UTC
  `YYYY-MM-DDTHH:MM:SSZ`, with seconds as the smallest unit.
- `from`/`to` filter on `scheduled_at` and include both ends. A bare date as `to`
  (`2026-10-01`) means the end of that day. Unscheduled items are left out when you set
  `from` or `to`.
- `status` takes one value or a comma list: `?status=approved,in_review`.
- If a `+02:00` offset arrives in a query string without URL-encoding (as `" 02:00"`),
  it is still read correctly.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for write endpoints. If it is not set, writes return `503`. |
| `DB_PATH` | `/data/calendar.sqlite` | SQLite file |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
