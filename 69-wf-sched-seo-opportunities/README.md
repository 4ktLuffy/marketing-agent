# 69 · SEO opportunities

Deploy **69 of 89** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every Wednesday it turns Search Console's striking-distance queries into SEO briefs. It syncs 67 (`POST /sync`) and reads `GET /queries/opportunities`: queries with at least `SEO_MIN_IMPRESSIONS` impressions in the last 28 days at an average position between `SEO_MIN_POSITION` and `SEO_MAX_POSITION`, biggest first, each with our page that ranks best for it. A query with a calendar item noted `seo brief for "<query>"` created in the last 90 days is skipped, and at most `SEO_BRIEFS_PER_WEEK` briefs are made per 7 days. One query at a time, it calls the SEO brief tool (29) with the query as `keyword` and our ranking page as `competitor_url` (29 audits it with 12, so the brief improves the page that already ranks, as the 67 README suggests). 29 only returns markdown, so this workflow saves it: a calendar item (19) with channel `seo_brief` (never published by 39), status `idea`, title `SEO brief: <query>`, our page as `link`, notes `seo brief for "<query>" (pos P, impressions I)`. The body starts with the Search Console numbers (position and its trend, impressions, clicks), our page, the other queries that page gets (67 `/pages/{url}/queries`), then the brief. Ask the chat agent to write from it, or improve the page by hand. With `GSC_URL` empty, or gsc-sync not configured, the workflow finishes with a message and does nothing. A summary goes to `NOTIFY_WEBHOOK_URL`.

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
  n8n publish:workflow --id=mktWf69SeoOpport
```

Its workflow id is fixed (`mktWf69SeoOpport`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

Wednesdays 07:00. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `GSC_URL` | 67-gsc-sync, e.g. `http://gsc-sync:8000`. Empty (or gsc-sync not configured) = the workflow does nothing |
| `SEO_BRIEFS_PER_WEEK` | most briefs per 7 days; default 2 (0 turns it off) |
| `SEO_MIN_IMPRESSIONS` | impressions a query needs in the last 28 days; default 100 |
| `SEO_MIN_POSITION` | best average position that still counts as an opportunity; default 5 |
| `SEO_MAX_POSITION` | worst average position that counts; default 20 |
| `SEO_AUDIENCE` | optional audience passed to 29 |
| `NOTIFY_WEBHOOK_URL` | optional; receives the summary |

## Depends on

- `67-gsc-sync`
- `29-wf-tool-seo-brief`
- `19-content-calendar`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
