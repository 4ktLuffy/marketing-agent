# 40 · Weekly content planner

Deploy **40 of 53** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every Monday it plans the following week, using which channels and posts got the most clicks recently (45 insights): a mix of formats for each channel, avoiding angles published recently. The plan goes into the calendar as `idea` items with dates. Ask the chat agent to write any of them.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/40-wf-sched-content-planner/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf40ContentPl
```

Its workflow id is fixed (`mktWf40ContentPl`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

Mondays 07:00. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `PLAN_CHANNELS` | default `linkedin, instagram, x` |
| `PLAN_POSTS_PER_CHANNEL` | default 3 |
| `PLAN_THEMES` | optional |
| `NOTIFY_WEBHOOK_URL` | optional |

## Depends on

- `03-llm-gateway`
- `19-content-calendar`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
