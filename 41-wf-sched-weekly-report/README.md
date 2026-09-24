# 41 · Weekly KPI report

Deploy **41 of 60** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every Monday it pulls last week's KPIs against the week before (20), has the LLM write plain-language highlights, renders an HTML report (21), and sends it to your webhook and/or email.

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
  n8n publish:workflow --id=mktWf41WeeklyRep
```

Its workflow id is fixed (`mktWf41WeeklyRep`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

Mondays 09:00. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `NOTIFY_WEBHOOK_URL` | optional |
| `REPORT_EMAIL_TO` | optional; needs an **SMTP** credential attached to the *Email report* node |
| `REPORT_EMAIL_FROM` | optional sender |

## Setup

Upload analytics first, e.g. a GA4 export:

```bash
curl -X POST 'localhost:8120/upload?source=ga4' -H "X-API-Key: $INTERNAL_API_KEY" \
  -H 'content-type: text/csv' --data-binary @ga4-export.csv
```

## Depends on

- `03-llm-gateway`
- `20-analytics-ingest`
- `21-report-builder`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
