# learning-service

Deploy **46 of 53** of the local-LLM marketing agent. It learns from the reviewer. Every
approval, edit and rejection is recorded as an event. The service turns those events into
two things the writers use:

- **Examples.** The most recent texts a human signed off, edited ones first (the
  reviewer's own words), for few-shot prompting.
- **Rules.** `/reflect` asks the LLM, via `03-llm-gateway` and its `reflect_rule` prompt,
  what general rule an edit or rejection implies. New rules start as `pending`. A human
  activates or rejects them (`51-wf-rules-review-form`). Active rules are served as a
  summary that the gateway appends to the brand text in every prompt.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a volume on `/data`. n8n calls it at `http://learning-service:8000`
(env `LEARNING_URL`). It needs the gateway at `GATEWAY_URL` for `/reflect` only. It keeps
state in SQLite, so run exactly one instance and back up the volume.

## Run

```bash
docker build -t learning-service .
docker run --rm -p 8146:8000 -e INTERNAL_API_KEY=change-me \
  -e GATEWAY_URL=http://llm-gateway:8000 -v learning-data:/data learning-service
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./learning.sqlite GATEWAY_URL=http://localhost:8103 \
  uvicorn app.main:app --port 8146
```

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/events` 🔑 | `{"item_id","channel","campaign_id"?,"decision","draft","final"?,"reason"?,"reviewer"?}` | event, `201` |
| GET | `/events` | `?decision=&since=ISO` | `[event]`, oldest first |
| GET | `/items/{item_id}/attempts` | — | `{"item_id","rejections"}` |
| GET | `/examples` | `?channel=&k=3` (k 1–10) | `[{"text","channel","decision"}]` |
| POST | `/reflect` 🔑 | `{"since_days":7,"max_events":20}` (body optional) | `{"created":[rule],"reflected":n,"errors":[{"event_id","error"}]}` |
| GET | `/rules` | `?status=pending\|active\|rejected` | `[rule]` |
| POST | `/rules/{id}/status` 🔑 | `{"status":"active"\|"rejected"}` | rule |
| GET | `/rules/summary` | — | `{"summary","count"}` |

An event is `{id, item_id, channel, campaign_id, decision, draft, final, reason, reviewer,
created_at, reflected_at}`. `decision` is `approved`, `edited` or `rejected`. Empty
`final`, `reason` and `reviewer` are stored as `null`.

A rule is `{id, text, scope, status, source_event_ids[], created_at}`. `scope` is `all` or
a channel name.

```bash
curl -s localhost:8146/events -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"item_id":12,"channel":"linkedin","decision":"edited","draft":"We are thrilled!!!","final":"We shipped it."}'

curl -s localhost:8146/rules/summary
# {"summary":"Rules learned from your edits:\n- No exclamation marks.\n- [linkedin] At most 3 hashtags.","count":2}
```

### How the parts behave

- **Examples.** The list contains `edited` finals, newest first, then `approved` texts,
  newest first. An `approved` text is its `final`, or its `draft` when no `final` was
  sent. `edited` events without a `final` and all `rejected` events are left out. The
  `channel` filter ignores case.
- **Reflect.** It takes `edited` and `rejected` events from the last `since_days` that have
  not been reflected yet, oldest first, at most `max_events`. For each one it calls
  `POST {GATEWAY_URL}/v1/run` with
  `{"prompt":"reflect_rule","vars":{"draft","final","reason","channel"}}` (empty values
  are sent as `null`). The gateway output must be `{"generalizable","rule","scope"}`.
  - A successful call sets `reflected_at` on the event, so each event is reflected once.
  - If the gateway call fails or returns another shape, the event is not marked. It is
    listed in `errors` and retried on the next run.
  - A rule is kept only if `generalizable` is true and the text is not empty.
    Whitespace is collapsed and the text is cut to 200 characters. The scope is
    lower-cased, and a missing scope means `all`.
  - Rules are deduplicated by their text, ignoring case and whitespace. A rule that
    matches an existing `pending` rule adds its event to that rule's
    `source_event_ids`. A rule that matches an `active` or `rejected` rule is dropped, so
    a rejected rule is never proposed again.
- **Summary.** It lists `active` rules only, one `- rule` line each, with a channel-scoped
  rule shown as `- [linkedin] rule`. With no active rules the response is
  `{"summary":"","count":0}`.
- **Attempts.** `rejections` is the number of `rejected` events for that item.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for write endpoints. If it is not set, writes return `503`. A wrong or missing key returns `401`. |
| `DB_PATH` | `/data/learning.sqlite` | SQLite file |
| `GATEWAY_URL` | `http://llm-gateway:8000` | LLM gateway (deploy 03), used by `/reflect` |
| `GATEWAY_TIMEOUT` | `300` | seconds per gateway call |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
