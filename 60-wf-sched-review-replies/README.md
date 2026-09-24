# 60 · Review replies

Deploy **60 of 60** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every morning it drafts replies to new customer reviews. It takes the reviews with status `new` from the review hub (58), at most `REVIEW_REPLIES_PER_RUN` per run, oldest first, and one at a time: gets the reply context (58), writes a reply with the `review_reply` prompt through the gateway (03, with the approved facts), and saves it to the content calendar (19) as channel `review_reply`, titled `Reply to <source> review #<id> (<rating>★)`. The body is the reply, a line `---`, and the original review quoted. A review that mentions health, safety or legal action (decided in code by 58, or by the model's `needs_human`) is saved as a `draft` with the note `needs a person: <why>`, and a member of the team handles it. Every other reply goes through the quality gate (35, report only): with no problems it goes to the approval form as `in_review`, otherwise it stays a `draft` with the problems in its notes. Then the review is marked `drafted` in 58, so the next run skips it (a review that already has a reply draft in the calendar is never drafted twice).

**Nothing is posted automatically.** Nothing goes to Google, Trustpilot or any other platform, and the publisher (39) skips `review_reply` items even when approved: after approval, a person copies the reply, posts it on the platform, and sets the review to `replied` in 58. If the model or the review hub fails for one review, that review stays `new` and is retried the next day. A summary goes to `NOTIFY_WEBHOOK_URL` when any reply was drafted. With `REVIEWS_URL` empty the workflow does nothing.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand, from this folder:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  sh -c 'cat > /tmp/wf.json && n8n import:workflow --input=/tmp/wf.json' < workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf60RevReply0
```

Its workflow id is fixed (`mktWf60RevReply0`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

Daily 09:00. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `REVIEWS_URL` | the review hub (58); empty = the workflow does nothing |
| `REVIEW_REPLIES_PER_RUN` | most reviews drafted per run; default 10 (the rest wait for the next day) |
| `NOTIFY_WEBHOOK_URL` | optional; receives the summary |

## Depends on

- `58-review-hub`
- `03-llm-gateway`
- `04-prompt-library (prompt `review_reply`)`
- `19-content-calendar`
- `35-wf-tool-quality-gate`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
