# 37 · Competitor watch

Deploy **37 of 89** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every 6 hours it diffs the competitor pages you watch (09) and, when `AD_LIBRARY_URL` is set, syncs the competitors' ads from the official Meta Ad Library API (78 `POST /sync`, EU-delivered ads only) and reads the week's new, changed or stopped ads (78 `GET /ads`) and the manual-check links (78 `GET /links`). It goes on when a page changed or an ad appeared, changed or stopped since the last run. The LLM (prompt `competitor_changes`, with the ad texts) explains what changed and whether to react.

**Checked in code, not trusted:** every quote in the digest must be an exact ad text from 78 or an exact part of a page diff; any other quote of 3+ words is replaced by `[quote removed: ...]` and counted. The code then adds: *Possible launches* (added lines that say new / introducing / launched / now available ...), *New, changed or stopped ads this week* (each ad's exact text and its Ad Library link) and *Check by hand* (Meta for all countries, Google Ads Transparency Center, LinkedIn and TikTok ad libraries: no API or outside the EU, so a person opens them; nothing is scraped). The digest is saved to the knowledge base and posted to your webhook.

**Pricing page changed** (78 labels it `<name> · pricing`, or the URL has /pricing, /plans ...): the LLM (prompt `competitor_brief`, with the approved facts from 05) suggests match, counter or ignore, with reasons and response points. Lines with a number that is in neither the diff nor the facts are dropped. The brief is saved to the calendar (19) as an `idea` with channel `competitor_brief`, which the publisher (39) never sends. At most 3 briefs per run.

With `AD_LIBRARY_URL` empty, or 78 down, it works as before on page changes only.

**Monthly positioning map** (1st of the month, 07:00, needs `AD_LIBRARY_URL`): 78 `POST /positioning/build` sorts what each active competitor says (their watched pages' latest text in 09 and their active ads) and what we say (approved facts from 05, our own watched pages) into messaging themes (prompt `positioning_themes`); 78 keeps only quotes that are an exact part of their source. The map (themes x brands, white space = no competitor claims it AND one of our approved facts backs it, crowded themes, shifts since last month) is saved to the knowledge base (doc `positioning-<YYYY-MM>`), to the calendar as an `idea` with channel `positioning` (the publisher never sends it), and the white space, crowded themes and top shifts are posted to your webhook. The control room shows it under More → Positioning. A failed build is reported and saves nothing.

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
  n8n publish:workflow --id=mktWf37Competito
```

Its workflow id is fixed (`mktWf37Competito`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

every 6 hours; positioning map on the 1st of each month at 07:00. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `NOTIFY_WEBHOOK_URL` | optional |
| `AD_LIBRARY_URL` | 78-ad-library-sync, e.g. `http://ad-library-sync:8000`. Empty = pages only, no ads or links |

## Setup

Add competitors in 78 (their key pages become 09 watches automatically), or ask the chat agent "track competitor https://rival.example.com" (tool 81):

```bash
curl -X POST localhost:8178/competitors -H "X-API-Key: $INTERNAL_API_KEY" \
  -H 'content-type: application/json' -d '{"name":"Rival Beans","website":"https://rival.example.com/","key_pages":[{"url":"https://rival.example.com/pricing","type":"pricing"}],"meta_page_id":"123456789"}'
```

Pages can still be watched by hand in 09 (`POST localhost:8109/watches`).

## Depends on

- `03-llm-gateway`
- `05-brand-service`
- `06-knowledge-base`
- `09-change-monitor`
- `19-content-calendar`
- `78-ad-library-sync (optional)`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
