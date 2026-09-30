# approval-service

Deploy **90 of 90** of the local-LLM marketing agent. It applies the approver's decisions from the
control room (72) to the content calendar (19) **without n8n**. It needs no model and no database.

Until now every approval from the control room went through n8n: 72 posted the decisions to the
webhook `mkt-apply-decisions` (workflow 72), which ran the approval form's decision code and
called 19 with the approver key. This service does the same job, so approving works with n8n
stopped or not installed.

- **Same request, same answer.** `POST /decisions` takes exactly what 72 sends to the webhook,
  `{reviewer, decisions: [{id, decision, text?, reason?, publish_at?, seen_sha256?}]}` with
  `X-Control-Key`, and answers `{ok, summary[], not_in_review[], stale[], failed[], message}`,
  word for word like n8n. 72 only changes the URL it posts to.
- **Only items in review.** It reads the items in review from 19 first. A decision on anything
  else is listed in `not_in_review` and nothing is changed for it.
- **Approval bound to the text the approver saw.** Approve sends the card's hash
  (`seen_sha256`, else the hash 19 just returned) as `expected_sha256`; edit & approve saves the
  new text with `if_match_sha256` and approves with `expected_body`. If the text changed after the
  card was shown, 19 refuses (409 or 428) and the item comes back in `stale` with one line:
  "NOT approved: changed since you looked — reopen the card".
- **Your name in the audit.** Every call to 19 carries `X-Actor: <reviewer>`, so 19's audit shows
  the full name, and "bound to <sha>" on each approval.
- **Learning and the content engine, fail-soft.** Approved, edited and rejected items become
  learning events (46). Items the content engine wrote (notes `engine pillar #P`) report the
  decision to 61's stop rule. If 46 or 61 is down, the decisions are still saved and the summary
  ends with a note.

## What each decision does

Two stages, like n8n: every stage-1 call of the batch, then every stage-2 call. Publish time is
`publish_at`, else the item's `scheduled_at`, else the next full hour (UTC). Notes read
"`<action> by <reviewer>[: reason]`".

| Decision | Stage 1 (19) | Stage 2 (19) | Learning (46) |
|---|---|---|---|
| `approve` | `PATCH /items/{id}` `{scheduled_at, if_match_sha256}` | `POST /items/{id}/status` `{approved, note, expected_sha256}` | `approved` |
| `edit`, text changed | `PATCH` `{scheduled_at, body, if_match_sha256}` | `status` `{approved, note, expected_body}` | `edited` |
| `edit`, text unchanged or empty | as `approve` | as `approve` | `approved` |
| `edit` of a rendered video or a video-channel item (not a clip) | `PATCH` `{body, video_url: null, image_url: null if it was the poster, if_match_sha256}` | `status` `{draft, "edited video needs a new render (not approved)"}` | none |
| `reject_drop` | `status` `{rejected, note}` | | `rejected` |
| `reject_rewrite` without a reason | `status` `{rejected, note}` | | `rejected` |
| `reject_rewrite` with a reason | `status` `{rejected, note}` | `status` `{draft, "sent back to rewrite by hand: <reason>"}` | `rejected` |
| `back_to_draft` | `status` `{draft, note}` | | none |
| `skip` | nothing | | none |

If an item's stage-1 call is refused, its stage-2 call is not made (n8n makes it anyway; the
bound approval then refuses it too). No learning event or engine outcome is sent for an item 19
refused.

## What stays on n8n

- **Publishing** (workflow 39) and the approval form (38).
- **Automatic rewrites** (workflow 49). Here "reject – rewrite it" with a reason puts the item back
  to draft with the reason for a person to rewrite; the summary says so.
- **Video re-render** (71). An edited video script is saved, its old video removed, and the item
  goes back to draft to be rendered again. It is never approved with a video of the old script.
  A clip from your own footage (73, `/clips/<id>.mp4`) is approved with its edited caption.

`tools/workflow-generator/tests/approval_parity_test.js` runs n8n's decision code (from the
built `72-control-room/n8n/workflow.json`) and this service's planner on the same cases:
approve with and without hashes, publish times, edit, unchanged edit, clips, rejects,
back to draft, skip, items not in review, the same id twice, bad requests word for word, and
19's refusals (stale, 428, other errors). The three differences above are asserted there.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing` network
(compose service `approval-service`, **no host port**: it holds APPROVER_KEY and only the control
room calls it). The control room reaches it at `APPROVAL_URL=http://approval-service:8000`, which the
stack sets from `CONTROL_APPROVAL_URL` (`install.sh --approval service`); empty = n8n as before. No volume: it keeps no state. Run one instance (a batch is applied one at a time).

## Security

- **Only this service and n8n hold `APPROVER_KEY`.** The control room never does. The key goes
  only to the content calendar (19), never to the learning service or the content engine, and
  never into an answer or a log line.
- **Only the control room may call it.** `POST /decisions` needs `X-Control-Key` equal to
  `CONTROL_ROOM_KEY` (constant-time compare; missing or wrong is 401). A key under 16 characters,
  or equal to `INTERNAL_API_KEY` or `APPROVER_KEY`, refuses everything (503). Do not publish the
  port outside the host: a client install closes it like the other internal services.
- `GET /health` needs no key and shows configuration problems by name only.
- Header values are Latin-1: a reviewer name outside it (e.g. Ge'ez script) is not sent as
  `X-Actor`, and 19 records the first word of "approved by <name>" from the note instead. The note
  and the learning event keep the full name.

## Run

```bash
docker build -t approval-service .
docker run --rm -p 8190:8000 --env-file .env approval-service
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me APPROVER_KEY=change-me-too CONTROL_ROOM_KEY=change-me-to-a-long-random-value \
  CALENDAR_URL=http://localhost:8119 uvicorn app.main:app --port 8190
```

## How to use it

72 does this for you. By hand:

```bash
curl -s localhost:8190/decisions -H 'X-Control-Key: change-me-to-a-long-random-value' \
  -H 'content-type: application/json' \
  -d '{"reviewer": "Sam", "decisions": [{"id": 12, "decision": "approve", "seen_sha256": "<body_sha256 you saw>"},
       {"id": 13, "decision": "reject_rewrite", "reason": "too salesy"}]}'
```

## Endpoints

| Method | Path | What |
|---|---|---|
| GET | `/health` | `{status: ok \| misconfigured, problems[], calendar: reachable?, learning, engine}`; no key |
| POST | `/decisions` | `X-Control-Key`; 1 to 50 decisions → `{ok, summary, not_in_review, stale, failed, message}`; 401 wrong key, 422 bad input (n8n's messages), 502 calendar unreachable before anything changed, 503 not configured |

Refusals are `{ok: false, error, detail}`, like the n8n webhook's `{ok: false, error}`.

## Configuration

See `.env.example`. `INTERNAL_API_KEY`, `APPROVER_KEY`, `CONTROL_ROOM_KEY` and `CALENDAR_URL` are
required. `LEARNING_URL` and `ENGINE_URL` are optional. `REQUEST_TIMEOUT` (seconds, default 15).

## Tests

`pytest -q` runs the planner's unit tests (no other service), the API checks, and, in the monorepo,
flow tests against the real content calendar (19) in-process with a temporary database, and the
n8n parity test (needs `node`, or `NODE=/path/to/node`). Outside the monorepo those two are skipped
with the reason.

## CI

`.github/workflows/ci.yml` runs `pytest` on every push and pull request, and on `main` builds
and pushes the image to GHCR.
