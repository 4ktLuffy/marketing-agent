# 85 · Monthly client report

Deploy **85 of 91** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

On the 1st of every month it builds a report on the month before, for a client (agencies) or for whoever the owner reports to. It compares the last calendar month with the month before, from every source that is installed: analytics (20 `/kpis`, all sources, plus the Umami rows of 55 and the Search Console rows of 67 when `UMAMI_SYNC_URL` / `GSC_URL` are set), tracked posts and their clicks (45 `/insights/posts`), campaigns and experiments (45), and paid ads (84 `/summary`, when `ADS_URL` is set). A source whose URL is empty (not installed on this profile) is skipped; one that fails is left out. Both are named in the reviewer notes.

**What we did** (no LLM): the calendar items published or approved in that month, counted by channel with their titles, the campaigns that were active (with their KPI actuals against targets) and the experiments decided. **What changed** (no LLM): a table with the value, the previous value and the change for each measure, computed in code.

**Summary**: the LLM (prompt `client_report_summary`) writes 3 to 5 short sentences for the client from those finished sentences. It is **checked in code, not trusted**: a sentence with a number that is not in the data is dropped (the same check as 41: thousands separators and the sign are ignored; spelled-out counts and "doubled" count as numbers); so is a sentence that names one measure and says it went the wrong way ("clicks fell" when they rose; checked only when that measure's rows all moved the same way, and not for plans), and a sentence that claims a cause ("because", "led to", "drove", "thanks to" ...) without hedging ("may", "might", "likely", "we can't tell yet" ...). The counts and the dropped sentences go to the reviewer notes, never to the client text. If the LLM fails, the report has the tables only.

The report is rendered by 21 with the brand name from 05 and saved to the calendar (19) as channel `client_report`, status `in_review`, title `Client report <Month YYYY>`, so it goes through the approval form and the control room like everything else. A month that already has a report is not made again. **Nothing is ever sent to the client**: the publisher (39) never sends `client_report` items, even approved. The owner gets a notification (`NOTIFY_WEBHOOK_URL`); a person downloads the approved report from the control room (**Download**, one HTML file) and forwards it.

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
  n8n publish:workflow --id=mktWf85ClientRep
```

Its workflow id is fixed (`mktWf85ClientRep`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

1st of every month, 08:00. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `CLIENT_REPORT_MONTH` | optional `YYYY-MM`: report that month instead of the last one (set it, run the workflow by hand, then clear it) |
| `REPORT_CURRENCY` | optional prefix for money from analytics (20), e.g. `EUR ` or `$`; ad money always carries its own currency |
| `CLIENT_NAME` | optional; used when the brand profile (05) has no name |
| `UMAMI_SYNC_URL, GSC_URL, ADS_URL` | optional sources; empty = skipped |
| `NOTIFY_WEBHOOK_URL` | optional; the owner's notification (never the client) |

## Setup

To rebuild a month, reject its `Client report <Month YYYY>` item and run the workflow again (with `CLIENT_REPORT_MONTH` set, for an older month).

The calendar body is the report as markdown (tiles table, summary, what we did, what changed). The HTML page from 21 is in the *Render report* node of the run.

## Depends on

- `03-llm-gateway`
- `05-brand-service`
- `19-content-calendar`
- `20-analytics-ingest`
- `21-report-builder`
- `45-campaign-service`
- `55-umami-sync, 67-gsc-sync, 84-ads-sync (optional)`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
