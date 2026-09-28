# 68 · Content refresh

Deploy **68 of 87** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every Monday it finds the pages that are losing Google clicks and drafts a refresh plan for each (refreshing old posts is capability #1 in the night-5 research: HubSpot doubled leads that way). It syncs Search Console (67 `POST /sync`, 28 days vs the 28 before) and reads `GET /pages/declining` (at least `REFRESH_MIN_CLICKS` clicks before and a `REFRESH_MIN_DROP` fall). A page with a calendar item noted `refresh of <url> (` created in the last 60 days is skipped, and at most `REFRESH_PER_WEEK` pages are refreshed per 7 days, most clicks lost first. One page at a time: it reads the live page (07 `/extract`, text cut to 12,000 characters) and the queries it used to win (67 `/pages/{url}/queries`, previous window, with current numbers beside), and the LLM (prompt `content_refresh`, with the approved facts) writes a diagnosis, a new title (≤ 60) and meta description (≤ 155), 3–8 concrete changes (`add_section`, `rewrite`, `update_fact`, `remove`, `add_faq`, each with where, what and a why) and up to 4 answer-first FAQs.

**Checked in code, not trusted:** a change whose `why` has a number that is not in the input (the Search Console numbers, the page, the facts), or that cites neither a query nor a number, is dropped; so is a change whose `what` has an unknown number, and an `update_fact` that does not name an approved fact label (05 `/facts`). The words meant for the page then go through the quality gate (35, report only, the page text as evidence, the page URL as an allowed link). The plan is saved to the calendar (19) as channel `blog_refresh`, titled `Refresh: <page title>`, with the page as `link`: `in_review` when the gate passed, else `draft` with the problems in the notes. The notes start with `refresh of <url> (clicks X → Y)`, which is also how the next run knows it was done. The body is readable as it is: clicks before → now, diagnosis, new title and meta, the numbered changes with their reasons, the FAQ, the queries, and what the checks dropped.

**Nothing on the site changes automatically.** A person edits the page in the CMS from the plan. The publisher (39) never sends `blog_refresh` items anywhere, even approved: approving just records that the plan was accepted. With `GSC_URL` empty, or gsc-sync not configured, the workflow finishes with a message and does nothing. A summary goes to `NOTIFY_WEBHOOK_URL`.

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
  n8n publish:workflow --id=mktWf68ContRefre
```

Its workflow id is fixed (`mktWf68ContRefre`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

Mondays 07:00. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `GSC_URL` | 67-gsc-sync, e.g. `http://gsc-sync:8000`. Empty (or gsc-sync not configured) = the workflow does nothing |
| `REFRESH_PER_WEEK` | most pages refreshed per 7 days; default 3 (0 turns it off) |
| `REFRESH_MIN_CLICKS` | clicks a page needs in the previous 28 days to count; default 20 |
| `REFRESH_MIN_DROP` | fall in clicks that counts as declining, as a fraction; default 0.3 (30 %) |
| `NOTIFY_WEBHOOK_URL` | optional; receives the summary |

## Depends on

- `67-gsc-sync`
- `07-page-extractor`
- `03-llm-gateway`
- `04-prompt-library (prompt `content_refresh`)`
- `05-brand-service (approved facts)`
- `35-wf-tool-quality-gate`
- `19-content-calendar`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
