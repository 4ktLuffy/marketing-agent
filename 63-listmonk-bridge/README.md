# listmonk-bridge

Deploy **63 of 91** of the local-LLM marketing agent. It puts the week's newsletter into
[Listmonk](https://listmonk.app) (self-hosted, AGPL) as a **draft campaign**, so a person
reviews it there and presses send. It uses no LLM.

`DRY_RUN` is `true` by default: nothing reaches Listmonk until you set it to `false`.
`ALLOW_SCHEDULE` is `false` by default: even with `send_at`, the campaign stays a draft.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as the `listmonk-bridge` container on the
`marketing` network (already in the stack's compose file, port `127.0.0.1:8163`). n8n reads
its URL from `NEWSLETTER_URL=http://listmonk-bridge:8000`. It is stateless, so it also runs
on any container host (Render, Fly.io, Railway), as long as it can reach Listmonk.

No Listmonk yet? The stack ships an optional one:

```bash
cd ../01-marketing-stack
docker compose --profile newsletter up -d     # listmonk + its own postgres, http://localhost:9000
```

A plain `docker compose up` does not start it.

## Run

```bash
docker build -t listmonk-bridge .
docker run --rm -p 8163:8000 --env-file .env listmonk-bridge
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me LISTMONK_LIST_IDS=3 uvicorn app.main:app --port 8163
```

### Setup, step by step

1. In Listmonk: Admin → Users → New, type **API user**, with permission to manage campaigns
   and read lists. Copy the token (shown once).
2. Set `LISTMONK_URL`, `LISTMONK_USER`, `LISTMONK_TOKEN`; keep `DRY_RUN=true`; start.
3. `curl -s -H 'X-API-Key: change-me' localhost:8163/lists` and put the newsletter list
   id(s) in `LISTMONK_LIST_IDS`.
4. Send one newsletter to `POST /campaigns` and read `would_send` in the dry-run answer.
5. Set `DRY_RUN=false`, restart, send again, open the returned `url` in Listmonk.
6. Optional: `TEST_EMAILS` (addresses that are Listmonk subscribers) and `POST /campaigns/{id}/test`.

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status","configured","dry_run","allow_schedule","listmonk_url","public_url","auth_mode","credentials_set","list_ids","test_emails","config_errors"}` (never the token) |
| GET | `/lists` 🔑 | — | `[{"id","name","subscriber_count","allowed"}]` from Listmonk |
| POST | `/campaigns` 🔑 | see below | `{"status":"created"\|"dry_run","campaign_id","url",...}` |
| POST | `/campaigns/{id}/test` 🔑 | — | `{"status":"sent"\|"dry_run","campaign_id","recipients"}` |

### Contract for the n8n newsletter workflow

The workflow (JTBD #7: newsletter from the week's content) calls
`POST {{$env.NEWSLETTER_URL}}/campaigns` with header `X-API-Key: {{$env.INTERNAL_API_KEY}}`
and a JSON body:

| Field | Required | Meaning |
|---|---|---|
| `subject` | yes | ≤ 200 characters (whitespace is collapsed) |
| `preheader` | no | ≤ 300 characters. Listmonk has no preheader field, so it is added as a hidden block at the top of the body, unless the body already contains it (18 does). |
| `body_html` | one of | HTML, e.g. the `html` that `18-email-renderer` returns |
| `body_markdown` | one of | markdown, e.g. the body from `28-wf-tool-email-writer`; Listmonk renders it (content type `markdown`) |
| `body_text` | no | plain-text part (`altbody`), e.g. 18's `text` |
| `list_ids` | no | Listmonk list ids. Default: `LISTMONK_LIST_IDS`. Each must be in `LISTMONK_LIST_IDS` when that is set. |
| `send_at` | no | ISO 8601, future; no offset means UTC. Used only when `ALLOW_SCHEDULE=true`. |
| `name` | no | campaign name in Listmonk; default `Newsletter YYYY-MM-DD: <subject>` |

Send exactly one of `body_html` / `body_markdown`. The body is at most 200 KB and must not
contain `<script>`. Either render first with 18 (`POST {{$env.EMAIL_RENDER_URL}}/render` →
send `html` as `body_html` and `text` as `body_text`), or send the writer's markdown as
`body_markdown` and let Listmonk's template wrap it. The bridge does not call 18 itself.

```bash
curl -s localhost:8163/campaigns -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"subject":"This week at Acme Roasters","preheader":"Three new roasts",
       "body_markdown":"# Hi\n\nThree new roasts landed this week.\n\n[Unsubscribe]({{unsubscribe_url}})"}'
# DRY_RUN=true:  {"status":"dry_run","campaign_id":null,"url":null,"scheduled":false,
#                 "would_schedule":false,"send_at_ignored":false,"note":null,
#                 "would_send":{...the Listmonk request body...}}
# DRY_RUN=false: {"status":"created","campaign_id":17,"url":"http://localhost:9000/admin/campaigns/17",
#                 "scheduled":false,"send_at":null,"send_at_ignored":false,"note":null,
#                 "list_ids":[3],"content_type":"markdown"}
```

### What `/campaigns` does

- Builds a Listmonk campaign: `type: regular`, `content_type` `html` or `markdown`,
  `messenger: email`, `tags: ["marketing-agent"]`, plus `from_email` / `template_id` when set.
- Rewrites 18's `{{unsubscribe_url}}` placeholder to Listmonk's `{{ UnsubscribeURL }}`.
  Listmonk compiles the body as a Go template, so any other unknown `{{ ... }}` makes
  Listmonk answer 400 (→ our 502 with Listmonk's message).
- `POST /api/campaigns` → Listmonk always creates a **draft**. Nothing is sent.
- Only when `ALLOW_SCHEDULE=true` **and** `send_at` is given: `send_at` goes into the
  campaign and then `PUT /api/campaigns/{id}/status {"status":"scheduled"}`. If that second
  call fails, the answer is 502 with the `campaign_id` and `url` of the draft that exists.
  With `ALLOW_SCHEDULE=false`, `send_at` is dropped (`send_at_ignored: true`), so a stale
  date cannot block edits to the draft later.
- `url` is `{LISTMONK_PUBLIC_URL}/admin/campaigns/{id}`.
- Each call creates a new draft; the bridge keeps no record, so do not retry blindly.

### Errors

| Code | When |
|---|---|
| 401 | missing or wrong `X-API-Key` |
| 404 | `/campaigns/{id}/test` for a campaign Listmonk does not have |
| 422 | subject/body/preheader limits, `<script>`, both or neither body, list id not in `LISTMONK_LIST_IDS`, no list at all, `send_at` in the past |
| 502 | Listmonk 4xx/5xx, unreachable, or 30 s timeout: `{"message","listmonk_status","listmonk_error"}`. Listmonk 401/403 says to check the API user and `LISTMONK_AUTH_MODE`. |
| 503 | `INTERNAL_API_KEY` not set; live call without `LISTMONK_URL`/`USER`/`TOKEN`; invalid `LISTMONK_LIST_IDS`; test without `TEST_EMAILS` |

In dry run, `/campaigns` and `/campaigns/{id}/test` never call Listmonk. `/lists` is
read-only, so it always calls Listmonk and needs it configured.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for every endpoint but `/health`. |
| `LISTMONK_URL` | — | Listmonk base URL, e.g. `http://listmonk:9000` (a trailing `/api` is fine). |
| `LISTMONK_USER` | — | Listmonk API user name. |
| `LISTMONK_TOKEN` | — | Its token. Never logged or returned (scrubbed from Listmonk error text too). |
| `LISTMONK_AUTH_MODE` | `token` | `token` = `Authorization: token user:token` (v4+); `basic` = HTTP basic auth with the same pair. |
| `LISTMONK_LIST_IDS` | — | Comma-separated ids: default audience and allowlist. |
| `LISTMONK_PUBLIC_URL` | `LISTMONK_URL` | Browser URL used in the returned `url`. |
| `LISTMONK_FROM_EMAIL` | Listmonk setting | e.g. `Acme <news@acme.example>`. |
| `LISTMONK_TEMPLATE_ID` | Listmonk default | Campaign template. 18's `html` is a full document; for it, create a template whose body is only `{{ template "content" . }}` and set its id, so the Listmonk default layout does not wrap it twice. |
| `DRY_RUN` | `true` | Anything other than `false`/`0`/`no`/`off` keeps it on. |
| `ALLOW_SCHEDULE` | `false` | Only `true`/`1`/`yes`/`on` turn it on. |
| `TEST_EMAILS` | — | Comma-separated; must already be Listmonk subscribers (Listmonk rule). |

## Listmonk API reference used

Checked against the Listmonk docs and source (master) in September 2026:

- Auth and envelopes: <https://listmonk.app/docs/apis/apis/>
- Campaigns: <https://listmonk.app/docs/apis/campaigns/>;
  `CreateCampaign`, `TestCampaign`, `validateCampaignFields` in
  <https://github.com/knadh/listmonk/blob/master/cmd/campaigns.go> (drafts only, send_at must
  be future, test needs full fields + known subscribers)
- Status rules (scheduled needs send_at, only from draft/paused):
  `UpdateCampaignStatus` in <https://github.com/knadh/listmonk/blob/master/internal/core/campaigns.go>
- Markdown via goldmark with raw HTML allowed: <https://github.com/knadh/listmonk/blob/master/models/common.go>
- Lists: <https://listmonk.app/docs/apis/lists/>
- Docker: <https://github.com/knadh/listmonk/blob/master/docker-compose.yml>

The tests mock these shapes with respx.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
