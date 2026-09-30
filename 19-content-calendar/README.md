# content-calendar

Deploy **19 of 90** of the local-LLM marketing agent. It is the central store for every
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
docker run --rm -p 8119:8000 -e INTERNAL_API_KEY=change-me -e APPROVER_KEY=change-me-too \
  -v calendar-data:/data content-calendar
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me APPROVER_KEY=change-me-too DB_PATH=./calendar.sqlite uvicorn app.main:app --port 8119
```

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/items` 🔑 | `{"title","channel","body","status"?,"scheduled_at"?,"campaign"?,"campaign_id"?,"link"?,"notes"?,"hook_style"?,"image_url"?,"video_url"?,"origin"?,"require_bound_approval"?}` | item, `201` |
| GET | `/items` | `?status=&channel=&campaign_id=&hook_style=&from=&to=` | `[item]` |
| GET | `/items/{id}` | — | item |
| PATCH | `/items/{id}` 🔑 | any of `title, channel, body, scheduled_at, campaign, campaign_id, link, notes, hook_style, image_url, video_url`, plus `if_match_sha256`? | item |
| POST | `/items/{id}/status` 🔑 | `{"status","note"?,"expected_sha256"?,"expected_body"?}` | item |
| POST | `/items/{id}/published` 🔑 | `{"external_url"?,"short_url"?}` (body optional) | item |
| POST | `/items/{id}/notes` 🔑 | `{"note","external_url"?}` | item: appends `[time] note` to `notes` (one line, ≤ 1000 chars) in any status, status unchanged; sets `external_url` when given (http(s)). The publisher (39) uses it for blog posts sent to the CMS as drafts. |
| GET | `/items/{id}/versions` 🔑 | — | `[{"n","body","body_sha256","image_url","video_url","created_by","created_at"}]`, oldest first |
| GET | `/items/{id}/audit` 🔑 | — | `[{"at","actor","action","from_status","to_status","body_sha256","detail"}]`, oldest first |
| POST | `/client-links` 🔑 | `{"item_ids":[…],"label"?,"days"? (1–30, default 7),"pin" (6 digits)}` | link + `token` (the only time it is returned), `201` |
| GET | `/client-links` 🔑 | — | `[{"id","label","item_ids","expires_at","created_by","created_at","revoked_at","uses","wrong_pins","state"}]` (no token, no PIN), newest first |
| POST | `/client-links/{id}/revoke` 🔑 | — | the link, `state` `revoked` |
| POST | `/client-links/resolve` 🔑 | `{"token","pin"}` | `{"id","label","expires_at","items":[{"id","title","channel","body","body_sha256","version","status","scheduled_at","image_url","video_url","link","origin","notes","client_responses"}]}` |
| POST | `/client-links/respond` 🔑 | `{"token","pin","item_id","body_sha256","decision":"approve"\|"changes","name","comment"?}` | `{"item_id","decision","status","version","body_sha256"}` |
| GET | `/due` | `?now=ISO` (default: now, UTC) | `[item]` approved, `scheduled_at <= now` |

Every write may send `X-Actor: <name>`: it is recorded as the actor in the audit log and as
`created_by` of a new version (at most 80 characters). Without it, a status change takes the
name from its note (`approved by Sam` → `Sam`), otherwise the actor is `unknown`.

An item is:

```json
{"id":1,"title":"Launch post","channel":"linkedin","body":"...","status":"draft",
 "scheduled_at":"2026-10-01T09:00:00Z","published_at":null,"campaign":"launch",
 "link":"https://example.com","external_url":null,"notes":null,
 "created_at":"2026-09-23T10:00:00Z","updated_at":"2026-09-23T10:00:00Z",
 "campaign_id":3,"short_url":null,"hook_style":"question",
 "image_url":"http://localhost:8117/cards/3f9c....png","video_url":null,
 "origin":null,"require_bound":false,
 "body_sha256":"9c1f…(64 hex)","version":2}
```

- `campaign_id` (integer ≥ 1 or `null`) links the item to a campaign in
  `45-campaign-service`. It is set via `POST /items` or `PATCH`, and `PATCH` can change
  it at any status, including after approval. A string such as `"3"` is a 422 error.
  `GET /items?campaign_id=N` combines with `status`, `channel`, `from` and `to`.
- `campaign` (free text) is the older label field; it is unchanged and separate from `campaign_id`.
- `short_url` is set only by `POST /items/{id}/published` (the shortened link that was
  posted). `PATCH` does not accept it.
- `hook_style` (text or `null`) records how the post opens, e.g. `question`, `fact_led`,
  `story`, `how_to`, `benefit`, `contrarian` (the social writer, workflow 26, sets it from
  the prompt output). It is trimmed and lower-cased; blank means `null`; at most 40
  characters. It is content, so like `body` it is frozen once the item is `approved`.
  `GET /items?hook_style=question` filters on it. `45-campaign-service`
  `GET /insights/hooks` joins clicks to it to learn which hook style earns clicks.
- `image_url` (an `http(s)` URL of at most 2000 characters, or `null`; blank means `null`,
  anything else is a 422) is the post's image. The social writer (26) and campaign drafter (48)
  set it to a card from `17-image-cards`; the approval form (38) shows it and the publisher
  (39) sends it on (`54-postiz-bridge` uploads it to Postiz). Like `body` it is content, so
  it is frozen once the item is `approved`.
- `video_url` (same rules as `image_url`) is the post's video: an MP4 from
  `71-video-assembly`, set by the content formats (57) and the engine drafter (65) for
  `video_script` drafts. The approval form links to it, the publisher sends it on and
  `54-postiz-bridge` uploads it as media. Frozen once `approved`, like `image_url`.
- `body_sha256` is the canonical hash of `body`: sha256 (hex) of the UTF-8 text after
  `\r\n` → `\n` and trimming outer whitespace. It is computed, never sent.
- `version` is the number of the latest version (below). `origin` (e.g. `task-bridge`; at most
  40 characters of `a-z 0-9 . _ -`) says where the item came from; `require_bound` is set by
  `require_bound_approval: true` on `POST /items` and cannot be changed later.
- A database created by an older version gets the `campaign_id`, `short_url`, `hook_style`,
  `image_url`, `video_url`, `origin` and `require_bound` columns added on first use
  (`ALTER TABLE ... ADD COLUMN`). Existing rows read them as `null` (`require_bound` as
  `false`). On the same first use every existing item gets its current body and media as
  version 1 (`created_by` `migration`, `created_at` = the item's `updated_at`), once.

### Versions and audit log

- `item_versions` is append-only. Creating an item writes version 1; a `PATCH` that changes
  `body`, `image_url` or `video_url` writes the next version (the same text again does not).
  Title, channel, schedule and other fields are not versioned, but are audited.
- `audit` gets a row for creating an item (`created`), for every `PATCH` that changes
  something (`edit`, `detail` lists the fields), and for **every** status change including
  `/published` (`status`, with `from_status`, `to_status`), whether or not a note was given.
  Each row has the body hash at that moment. `notes` still gets a line only when a note is given.

### Client approval links

For agencies: a client signs off posts without an account, through the control room's public
page (72 `/c/<token>`). `POST /client-links` makes a link for some items and returns a random
token (32 bytes, URL-safe) **once**; the caller (72) generates the 6-digit PIN and shows both once.
Stored: `sha256(token)` and `HMAC-SHA256(key=token, PIN)`, so the database alone can neither
open a link nor be used to try PINs.

- `resolve` / `respond` need the token and the PIN (constant-time compare). Unknown token `404`;
  withdrawn or expired `410`; a wrong PIN `403` `{"message":"wrong PIN: N tries left","tries_left"}`;
  the 5th wrong PIN locks the link for good (`409`, also for the right PIN); a right PIN resets the count.
- `respond` works only for items in the link (`404` otherwise) that are not `published` or
  `rejected` (`409`), and only when `body_sha256` is the current text's hash (`409` "the text
  changed since the link was opened", nothing written). `name` is required (≤ 80), `comment` ≤ 2000
  and required for `changes`; both are made one line and `|` becomes `/` (a comment can't forge a
  flag segment in `notes`).
- `approve` writes an audit row (`actor` `client:<name>`, `action` `client_approved`, the hash
  seen, `detail` "via client link #N") and the note `client approved by <name> (v<n>)`. **The
  status does not change**: the agency's own approval (approver key, bound to the text) stays
  the one that counts.
- `changes` writes `client_changes_requested` with the comment, and moves an `in_review` or
  `approved` item to `draft` (no approver key needed: nothing is approved by it), note
  `in_review -> draft: client requested changes (<name>, v<n>): <comment>`; other statuses keep
  theirs and get the note without the transition.
- `client_responses` in `resolve` lists the answers given through that link only.
- Link + PIN prove who had both, not a legal signature.

### Version-bound approval

The reviewer approves a text, not an id. The approval form (38) and the control room (72,
through its n8n webhook) send the hash of the text the reviewer saw:

- `POST /items/{id}/status` with `status: approved` accepts `expected_sha256` (64 lower-case hex)
  or `expected_body` (the text itself, hashed here the same way). If it does not match the
  current body: `409 {"detail": {"message": "changed since you looked", "current_sha256": "…"}}`
  and nothing changes. The audit row of a checked approval says `bound to <hash>`.
- An item created with `require_bound_approval: true` (the task bridge 88 does this) **must**
  send one of them: without, approving is `428` with a message saying so.
- Items without `require_bound` (every item written by the existing workflows, and every
  migrated item) still approve without a hash, as before; a hash that is sent is still checked.
- `PATCH` accepts `if_match_sha256` (not stored): if the current body has a different hash,
  `409` as above and nothing is saved. Edit & approve uses it, then approves with
  `expected_body` = the edited text.
- The hash is checked only when approving; other moves (reject, back to draft) ignore it.

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
| `idea` | `draft`, `rejected` (an idea that won't be written, e.g. re-planned away by the content engine) |
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
  `published` item, `PATCH` also refuses to change `title`, `channel`, `body`,
  `link`, `hook_style`, `image_url` or `video_url` (409 error), because a human approved that exact text. Move the item back to
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
| `APPROVER_KEY` | — | Required to approve and publish: moving an item to `approved` or `published` and `POST /items/{id}/published` also need header `X-Approver-Key` (wrong or missing: `403`). **Unset refuses them (`503`)**: there is no open mode any more. Other moves (e.g. `approved` → `draft`) need no approver key. Only n8n holds it (approval form 38, publisher 39), so a service with `INTERNAL_API_KEY` can write drafts but never approve them. |
| `DB_PATH` | `/data/calendar.sqlite` | SQLite file |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
