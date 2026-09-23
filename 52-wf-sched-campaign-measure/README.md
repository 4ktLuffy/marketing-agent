# 52 · Measure campaigns

Deploy **52 of 53** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every morning it measures each active campaign against its targets (clicks from the link shortener, visits and conversions from analytics), completes campaigns past their end date, and lists campaigns that ended with a target that was never measured.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/52-wf-sched-campaign-measure/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf52CampMeasu
```

Its workflow id is fixed (`mktWf52CampMeasu`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

06:00 daily. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `NOTIFY_WEBHOOK_URL` | optional |

## Depends on

- `45-campaign-service`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
