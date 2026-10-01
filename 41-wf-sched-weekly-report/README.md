# 41 · Weekly KPI report

Deploy **41 of 91** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every Monday it pulls last week's KPIs against the week before (20), has the LLM write plain-language highlights, then adds a **Next actions** section: last week's tracked clicks by channel and top posts (45 `/insights`), the hook styles that earn clicks (45 `/insights/hooks`), the scorecards of active campaigns (45) and, when `ENGINE_URL` is set, the review health of active content pillars (61) go to the LLM (prompt `weekly_actions`), which returns a headline, what changed and at most ONE next action per channel. The workflow checks every action in code: each `why` must cite a number that appears in that data (sign ignored), or the action is dropped and counted; the same check removes unsupported numbers from the headline and the what-changed lines. It renders an HTML report (21) and sends it to your webhook and/or email. Any source that fails is left out; if the LLM fails, the report goes out without the actions. An **Experiments** section follows (45 `/experiments`): running experiments with posts assigned and the next look, decided winners, and the no-difference or inconclusive ones. When `AD_LIBRARY_URL` is set, a **Competitor suggestions** line follows: 78 `POST /suggestions/scan` looks for websites that our reviews (58), social mentions (11) and trend digests (06) name at least twice, stores them as `suggested` with the quotes, and the line lists the ones waiting with the command to accept or ignore them. Nothing is tracked until a person accepts. When `VISIBILITY_URL` is set, an **AI visibility** line follows: from 82 `GET /summary`, how often the brand is named in unaided AI answers per provider (with the change vs the previous run and the citation rate), and how many sentences about the brand the claim checker could not support. When `ADS_URL` is set, an **Ads** section follows: from 84 `GET /summary` (last week vs the week before), spend, conversions, CPL, ROAS, CTR and CPC per platform, per currency in total and for the top campaigns, as finished sentences computed by ads-sync, plus spend not mapped to a campaign and the open pacing/CPL/ROAS/zero-conversion alerts. The same sentences go into the `weekly_actions` data, so an ads action passes the number check only when it quotes them.

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
| `ENGINE_URL` | optional; content engine (61) for pillar health |
| `AD_LIBRARY_URL` | optional; 78-ad-library-sync for competitor suggestions |
| `VISIBILITY_URL` | optional; 82-ai-visibility for the AI visibility line |
| `ADS_URL` | optional; 84-ads-sync for the Ads section |

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
- `45-campaign-service`
- `61-content-engine (optional)`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
