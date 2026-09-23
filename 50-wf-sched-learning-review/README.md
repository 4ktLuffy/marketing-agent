# 50 · Learn from reviews

Deploy **50 of 53** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every Friday it asks the learning service (46) to look at the week's edits and rejections and propose general writing rules (for example "no rhetorical questions in openers"). New proposals are posted to your webhook with a link to the rules form (51). Nothing is used until you keep it.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/50-wf-sched-learning-review/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf50LearnRevi
```

Its workflow id is fixed (`mktWf50LearnRevi`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

Fridays 16:00. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `NOTIFY_WEBHOOK_URL` | optional |
| `N8N_PUBLIC_URL` | for the form link |

## Depends on

- `46-learning-service`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
