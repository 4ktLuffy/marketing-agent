# 37 · Competitor watch

Deploy **37 of 53** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every 6 hours it diffs the competitor pages you watch (09). When something changed, the LLM explains what changed and whether to react. The note is saved to the knowledge base and posted to your webhook.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/37-wf-sched-competitor-watch/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf37Competito
```

Its workflow id is fixed (`mktWf37Competito`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

every 6 hours. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `NOTIFY_WEBHOOK_URL` | optional |

## Setup

Add pages to watch:

```bash
curl -X POST localhost:8109/watches -H "X-API-Key: $INTERNAL_API_KEY" \
  -H 'content-type: application/json' -d '{"url":"https://competitor.com/pricing","label":"Rival pricing"}'
```

## Depends on

- `03-llm-gateway`
- `06-knowledge-base`
- `09-change-monitor`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
