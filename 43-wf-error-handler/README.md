# 43 · Error handler

Deploy **43 of 81** of the local-LLM marketing agent. This deploy is an n8n workflow.

Every other workflow reports failures here (`settings.errorWorkflow`). It posts the workflow name, the failing node, the error and a link to the execution to your webhook.

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
  n8n publish:workflow --id=mktWf43ErrorHand
```

Its workflow id is fixed (`mktWf43ErrorHand`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Trigger

n8n Error Trigger

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `NOTIFY_WEBHOOK_URL` | Slack/Discord/Teams incoming webhook. Without it (or Telegram), errors are only in n8n's execution list. |
| `NOTIFY_FORMAT` | `generic` (default, `{text, content}`), `slack`, `discord` or `telegram`; used by every workflow that notifies (`n8nlib.NOTIFY_FORMAT_JS`). Summaries where drafts wait for a person end with the approval-form link |
| `TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID` | for `NOTIFY_FORMAT=telegram` (Bot API `sendMessage`, HTML); `TELEGRAM_API_URL` optionally points to a self-hosted Bot API server |

## Depends on

- `nothing`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
