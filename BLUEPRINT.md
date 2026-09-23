# Marketing Agent — blueprint

A marketing agent that runs on a **local LLM (Ollama)**, is driven by **n8n**, and is
reachable two ways: **n8n chat** (you talk to it) and **schedules** (it works on its own).

Every folder in `marketing-agent/` is one deploy = one GitHub repo. 53 deploys (01–44 below, 45–53 in Phase 2).

## What the agent does

| You say / schedule fires | What happens |
|---|---|
| "Write a LinkedIn + X post about our launch" | Chat agent → social writer → brand check + platform limits → draft saved to calendar |
| "Blog post on X for small-business owners" | Chat agent → blog writer (uses brand KB) → readability + brand check |
| "Google Ads copy for spring sale" | Chat agent → ad copy (JSON, char limits enforced) |
| "Summarise competitor.com/pricing" | Chat agent → page extractor → summary + opportunities |
| "Keyword ideas for 'coffee subscription'" | Chat agent → autocomplete scrape → LLM clusters by intent |
| Every morning 08:00 | RSS + HN/Reddit mentions → trend digest |
| Every 6 h | Competitor pages diffed → change summary |
| Every 15 min | Approved + due calendar items → published |
| Monday 07:00 | Next week's content plan drafted into calendar |
| Monday 09:00 | KPIs from analytics → HTML weekly report |

Nothing is published without a human moving it to `approved` (approval form, deploy 38).

## Why it is split this way

A 7B local model picks tools reliably from ~10, not from 40. So:

- **One chat agent** (deploy 24) sees 11 tools. Each tool is an n8n sub-workflow.
- **Sub-workflows** do the multi-step work deterministically (fetch → LLM → check).
- **LLM calls go through the gateway** (03), which owns prompt templates (04), forces
  JSON with a schema, validates, and retries. Small models need that.
- **Deterministic work is plain services** (no LLM): extraction, SEO checks, limits,
  UTM, readability. Cheap, testable, never hallucinate.

## Deploys

`Where` = where it runs. **Docker host** = the machine running `01-marketing-stack`
(each service joins the `marketing` docker network). **n8n** = imported into n8n.

| # | Folder | Kind | Where | Depends on |
|---|---|---|---|---|
| 01 | `01-marketing-stack` | docker compose | Docker host | all services |
| 02 | `02-ollama-models` | Modelfiles + script | machine running Ollama | — |
| 03 | `03-llm-gateway` | service | Docker host | Ollama, 04, 05 |
| 04 | `04-prompt-library` | prompt files | mounted into 03 | — |
| 05 | `05-brand-service` | service | Docker host | — |
| 06 | `06-knowledge-base` | service | Docker host | Ollama (embeddings) |
| 07 | `07-page-extractor` | service | Docker host | internet |
| 08 | `08-rss-watcher` | service | Docker host | internet |
| 09 | `09-change-monitor` | service | Docker host | internet |
| 10 | `10-keyword-suggest` | service | Docker host | internet |
| 11 | `11-social-listening` | service | Docker host | internet |
| 12 | `12-seo-auditor` | service | Docker host | internet |
| 13 | `13-readability` | service | Docker host | — |
| 14 | `14-platform-rules` | service | Docker host | — |
| 15 | `15-utm-builder` | service | Docker host | — |
| 16 | `16-link-shortener` | service | Docker host (public URL) | — |
| 17 | `17-image-cards` | service | Docker host | — |
| 18 | `18-email-renderer` | service | Docker host | — |
| 19 | `19-content-calendar` | service | Docker host | — |
| 20 | `20-analytics-ingest` | service | Docker host | — |
| 21 | `21-report-builder` | service | Docker host | — |
| 22 | `22-status-page` | service | Docker host | all services |
| 23 | `23-eval-suite` | CLI | your laptop / CI | 03, 05, 14 |
| 24 | `24-wf-chat-agent` | n8n workflow | n8n | Ollama, 25–33 |
| 25 | `25-wf-tool-blog-writer` | n8n sub-workflow | n8n | 03, 06, 13, 05 |
| 26 | `26-wf-tool-social-writer` | n8n sub-workflow | n8n | 03, 14, 05, 15 |
| 27 | `27-wf-tool-ad-copy` | n8n sub-workflow | n8n | 03, 14 |
| 28 | `28-wf-tool-email-writer` | n8n sub-workflow | n8n | 03, 18, 05 |
| 29 | `29-wf-tool-seo-brief` | n8n sub-workflow | n8n | 03, 10, 12 |
| 30 | `30-wf-tool-repurpose` | n8n sub-workflow | n8n | 03, 07, 14 |
| 31 | `31-wf-tool-research-url` | n8n sub-workflow | n8n | 03, 07 |
| 32 | `32-wf-tool-keyword-research` | n8n sub-workflow | n8n | 03, 10 |
| 33 | `33-wf-tool-calendar` | n8n sub-workflow | n8n | 19 |
| 34 | `34-wf-tool-kb-answer` | n8n sub-workflow | n8n | 03, 06 |
| 35 | `35-wf-tool-quality-gate` | n8n sub-workflow | n8n | 05, 13, 14, 03 |
| 36 | `36-wf-sched-trend-digest` | n8n workflow (cron) | n8n | 08, 11, 03 |
| 37 | `37-wf-sched-competitor-watch` | n8n workflow (cron) | n8n | 09, 03 |
| 38 | `38-wf-approval-form` | n8n workflow (form) | n8n | 19 |
| 39 | `39-wf-sched-publisher` | n8n workflow (cron) | n8n | 19, 16 |
| 40 | `40-wf-sched-content-planner` | n8n workflow (cron) | n8n | 03, 19 |
| 41 | `41-wf-sched-weekly-report` | n8n workflow (cron) | n8n | 20, 03, 21 |
| 42 | `42-wf-kb-ingest-form` | n8n workflow (form) | n8n | 06, 07 |
| 43 | `43-wf-error-handler` | n8n error workflow | n8n | — |
| 44 | `44-claim-checker` | service | Docker host | 03, 05, 06 |

## Conventions (every service)

```
NN-name/
  README.md            what it does, where to deploy, env vars, endpoints, curl
  Dockerfile           python:3.12-slim, non-root, port 8000, HEALTHCHECK
  requirements.txt     pinned
  app/main.py          FastAPI app
  tests/test_main.py   pytest, no network (httpx mocked with respx)
  .github/workflows/ci.yml   test + build + push image to GHCR
  .gitignore  .dockerignore  .env.example (if it has env vars)
```

- Container port **8000**. Host port for local debugging = **81NN** (e.g. 15 → 8115).
- `GET /health` → `{"status":"ok"}` on every service.
- Write endpoints on stateful services need header `X-API-Key: $INTERNAL_API_KEY`.
- State in SQLite under `/data` (a docker volume).
- Services that fetch URLs refuse private/loopback addresses (SSRF guard) unless
  `ALLOW_PRIVATE_URLS=true`.

n8n reaches services by env vars set in the stack (`$env.GATEWAY_URL` etc.), so the
same workflow JSON works in docker and on a laptop.

| Env var in n8n | Default in stack |
|---|---|
| `GATEWAY_URL` | `http://llm-gateway:8000` |
| `BRAND_URL` | `http://brand-service:8000` |
| `KB_URL` | `http://knowledge-base:8000` |
| `EXTRACTOR_URL` | `http://page-extractor:8000` |
| `RSS_URL` | `http://rss-watcher:8000` |
| `MONITOR_URL` | `http://change-monitor:8000` |
| `KEYWORDS_URL` | `http://keyword-suggest:8000` |
| `LISTENING_URL` | `http://social-listening:8000` |
| `SEO_URL` | `http://seo-auditor:8000` |
| `READABILITY_URL` | `http://readability:8000` |
| `RULES_URL` | `http://platform-rules:8000` |
| `UTM_URL` | `http://utm-builder:8000` |
| `SHORTENER_URL` | `http://link-shortener:8000` |
| `CARDS_URL` | `http://image-cards:8000` |
| `EMAIL_RENDER_URL` | `http://email-renderer:8000` |
| `CALENDAR_URL` | `http://content-calendar:8000` |
| `ANALYTICS_URL` | `http://analytics-ingest:8000` |
| `REPORT_URL` | `http://report-builder:8000` |
| `CLAIMS_URL` | `http://claim-checker:8000` |
| `INTERNAL_API_KEY` | from `.env` |

## API contracts

All bodies are JSON unless stated.

**03 llm-gateway**
- `GET /v1/prompts` → `[{"name","description","output","required_vars","optional_vars"}]`
- `POST /v1/run` `{"prompt","vars":{},"model"?,"temperature"?}` →
  `{"prompt","model","output": str|object,"attempts","duration_ms"}`.
  404 unknown prompt · 422 missing vars · 502 Ollama error or invalid JSON after retries.
  Var `brand` is injected from 05 `/profile/summary` if `BRAND_URL` is set.

**05 brand-service** — config `BRAND_FILE` (yaml)
- `GET /profile` → the brand yaml as JSON
- `GET /profile/summary` → `{"summary": str}` (compact, for prompts)
- `POST /check` `{"text","channel"?}` → `{"ok","violations":[{"rule","detail","severity":"error|warn"}]}`

**06 knowledge-base**
- `POST /docs` 🔑 `{"doc_id"?,"title","text","source"?}` → `{"doc_id","chunks"}`
- `GET /docs` → `[{"doc_id","title","source","chunks","created_at"}]`
- `DELETE /docs/{doc_id}` 🔑
- `POST /search` `{"query","k":5}` → `{"results":[{"doc_id","title","source","chunk","score"}]}`

**07 page-extractor**
- `POST /extract` `{"url"?|"html"?}` → `{"url","title","description","lang","headings":[{"level","text"}],"text","word_count","links":{"internal","external"},"og":{}}`

**08 rss-watcher** — default feeds from `FEEDS_FILE`
- `POST /poll` `{"feeds"?:[url],"max_items_per_feed":20}` → `{"new_items":[{"feed","title","link","published","summary"}],"checked","errors":[]}`
- `GET /items?limit=50`

**09 change-monitor**
- `POST /watches` 🔑 `{"url","label"?}` · `GET /watches` · `DELETE /watches/{id}` 🔑
- `POST /check` → `{"changed":[{"id","url","label","diff","added_words","removed_words"}],"unchanged","errors":[]}`

**10 keyword-suggest**
- `POST /suggest` `{"seed","expand":true,"lang":"en","country":"us"}` → `{"seed","keywords":[{"keyword","source","modifier"}],"count"}`

**11 social-listening**
- `POST /search` `{"query","sources":["hackernews","reddit"],"days":7,"limit":25}` →
  `{"mentions":[{"source","title","url","author","score","comments","created_at","text"}],"errors":[]}`

**12 seo-auditor**
- `POST /audit` `{"url"?|"html"?,"keyword"?}` → `{"url","score","checks":[{"id","status":"pass|warn|fail","message"}]}`

**13 readability**
- `POST /score` `{"text"}` → `{"flesch_reading_ease","fk_grade","words","sentences","avg_sentence_length","long_sentences":[],"passive_voice_count","adverb_count","verdict":"easy|ok|hard"}`

**14 platform-rules**
- `GET /rules` · `POST /validate` `{"channel","text"}` → `{"ok","channel","length","limit","hashtags","violations":[{"rule","detail"}]}`
- channels: `x, linkedin, instagram, facebook, threads, mastodon, email_subject, google_ads_headline, google_ads_description, meta_description`

**15 utm-builder**
- `POST /build` `{"url","source","medium","campaign","term"?,"content"?}` → `{"url","params":{}}`
- `POST /parse` `{"url"}` → `{"base_url","params":{}}` · `GET /conventions`

**16 link-shortener** — `BASE_URL` is the public short domain
- `POST /links` 🔑 `{"url","slug"?}` → `{"slug","short_url","url"}`
- `GET /{slug}` → 302 · `GET /links/{slug}/stats` → `{"slug","url","clicks","by_day":{},"referrers":{}}`

**17 image-cards**
- `POST /card` `{"title","subtitle"?,"brand"?,"size":"og|square|story","theme":"light|dark"}` → `image/png`

**18 email-renderer**
- `POST /render` `{"subject","preheader"?,"body_markdown","cta_text"?,"cta_url"?,"footer"?}` → `{"html","text"}`

**19 content-calendar**
- `POST /items` 🔑 `{"title","channel","body","status":"draft","scheduled_at"?,"campaign"?,"link"?}` → item
- `GET /items?status=&channel=&from=&to=` · `GET /items/{id}` · `PATCH /items/{id}` 🔑
- `POST /items/{id}/status` 🔑 `{"status","note"?}` (transitions below)
- `GET /due?now=ISO` → approved items with `scheduled_at <= now`
- `POST /items/{id}/published` 🔑 `{"external_url"?}`
- item = `{"id","title","channel","body","status","scheduled_at","published_at","campaign","link","external_url","notes","created_at","updated_at"}`
- transitions: `idea→draft`, `draft→in_review|rejected`, `in_review→approved|draft|rejected`, `approved→published|draft`, `rejected→draft`

**20 analytics-ingest**
- `POST /upload?source=generic|ga4` body `text/csv` 🔑 → `{"rows_imported"}`
- `GET /kpis?from=YYYY-MM-DD&to=YYYY-MM-DD&compare=true` →
  `{"period":{"from","to"},"totals":{impressions,clicks,sessions,conversions,spend,ctr,cvr,cpa},"by_channel":[...],"previous":{...}|null,"delta_pct":{...}|null}`

**21 report-builder**
- `POST /render` `{"title","period","kpis","highlights_markdown"?}` → `{"html","markdown"}`

**44 claim-checker**
- `POST /verify` `{"text","context"?,"extra_facts"?:[]}` → `{"ok","unsupported":[...],"claims":[{"claim","supported","reasons"}],"numbers":[{"value","supported"}],"evidence_lines"}`
- evidence = 05 `/facts` + `context` + `extra_facts` + 06 search results

**22 status-page** — `SERVICES="name=url,name=url"`
- `GET /` HTML · `GET /status` JSON

## As built: additions to the contracts above

All additive. Nothing above was removed or renamed.

| Deploy | Addition |
|---|---|
| 03 | Text outputs wrapped in one ```` ``` ```` fence are unwrapped; `<think>` blocks are stripped |
| 05 | Violations may carry `match` (the exact offending text). `banned_phrases` support `*` = up to three words (`best * in the world`) |
| 06 | FastAPI docs moved to `/swagger` (because `/docs` is the document list). `POST /docs` → 200, `DELETE` → `{"deleted": id}` |
| 07 | `og` keys without the `og:` prefix (`{"title","image",…}`) |
| 09 | `POST /watches` → 201, `DELETE` → 204. `added_words` / `removed_words` / `unchanged` are counts |
| 10 | Response also has `errors: [{source, error, failed_queries}]` |
| 14 | Violations carry `severity` (`error` / `warn`); `ok` is false only on errors |
| 16 | `POST /links` → 201. Referrers without a Referer header count as `direct` |
| 19 | `POST /items` → 201. Once `approved`/`published`, title/channel/body/link are locked (move back to `draft` to edit) |
| 20 | `previous` is flat (`previous.sessions`, `previous.period`). Optional `?source=` filter |
| 22 | Response also has `all_ok` |
| 05 | `GET /facts`: approved facts (explicit `facts:` + products + key messages) |
| 35 (n8n) | Takes `context`; unsupported claims from 44 are errors that trigger the rewrite |
| 33 (n8n) | The calendar tool refuses `approved`/`published`: only the approval form (38) can approve |

## Phase 2: campaigns, learning, measurement (deploys 45–53)

Why: research on other open-source marketing agents and tools found no open-source tool
with a campaign object that holds goals and targets, and no system that learns from a
reviewer's edits. Those are built here. Publishing, analytics and email are better done by
integrating Postiz, Umami and Listmonk later; that work isn't part of this phase.

| # | Folder | Kind | Where | Depends on |
|---|---|---|---|---|
| 45 | `45-campaign-service` | service | Docker host | 16, 19, 20 |
| 46 | `46-learning-service` | service | Docker host | 03 |
| 47 | `47-wf-tool-plan-campaign` | n8n sub-workflow (agent tool) | n8n | 03, 45, 19, 48 |
| 48 | `48-wf-campaign-drafter` | n8n sub-workflow (async) | n8n | 03, 45, 19, 35, 46 |
| 49 | `49-wf-revise-draft` | n8n sub-workflow (async) | n8n | 03, 19, 35, 46 |
| 50 | `50-wf-sched-learning-review` | n8n workflow (cron) | n8n | 46 |
| 51 | `51-wf-rules-review-form` | n8n workflow (form) | n8n | 46 |
| 52 | `52-wf-sched-campaign-measure` | n8n workflow (cron) | n8n | 45 |
| 53 | `53-wf-tool-campaigns` | n8n sub-workflow (agent tool) | n8n | 45 |

New env vars in n8n: `CAMPAIGNS_URL=http://campaign-service:8000`, `LEARNING_URL=http://learning-service:8000`.

### Changes to existing contracts (additive)

**19 content-calendar**
- item gets `campaign_id` (int|null) and `short_url` (str|null)
- `POST /items` and `PATCH /items/{id}` accept `campaign_id`
- `GET /items?campaign_id=N` filters by campaign (combinable with `status`, `channel`)
- `POST /items/{id}/published` accepts `{"external_url"?, "short_url"?}`
- `PATCH` of `body` stays allowed in `idea`, `draft`, `in_review` (locked once approved)

**16 link-shortener**
- `GET /links?utm_campaign=&utm_content=&limit=100` (no key) →
  `[{"slug","short_url","url","clicks","created_at"}]`, filtered on the target URL's query params

**20 analytics-ingest**
- generic CSV: optional `campaign` column (= utm_campaign). GA4 preset: `Session campaign`
  or `Session manual campaign name` → campaign. Missing → `""`.
- upsert key becomes (date, channel, campaign, source)
- `GET /kpis?...&campaign=slug` filters by campaign

**03 llm-gateway**
- optional `LEARNING_URL`: `GET {LEARNING_URL}/rules/summary` → `{"summary"}` is appended to
  the injected `brand` text (cached 60 s; ignored when unavailable)

### 45 campaign-service

Campaign: `id, slug, name, goal_type (awareness|traffic|leads|sales|retention), goal_text,
audience, offer, landing_url, channels[], start_date, end_date (YYYY-MM-DD), status
(planned|active|paused|completed|cancelled), budget, notes, created_at, updated_at, kpis[]`

KPI: `id, campaign_id, metric (clicks|sessions|conversions|signups|revenue|ctr|cvr|open_rate),
target_value, baseline_value, source (shortener|analytics|manual), actual_value, measured_at`

- `POST /campaigns` 🔑 `{name, slug?, goal_type, goal_text, audience, offer?, landing_url?,
  channels[], start_date, end_date, budget?, notes?, kpis?:[{metric, target_value, baseline_value?, source?}]}` → 201 campaign.
  slug default = slugified name (`[a-z0-9-]`, ≤40, unique; 409 on clash). status starts `planned`.
- `GET /campaigns?status=` · `GET /campaigns/{id}` · `GET /campaigns/by-slug/{slug}`
- `PATCH /campaigns/{id}` 🔑 any field except status/kpis; `slug` locked once not `planned` (409)
- `POST /campaigns/{id}/status` 🔑 `{status}`: planned→active|cancelled, active→paused|completed,
  paused→active|completed|cancelled. **→active requires ≥1 KPI** (targets declared before shipping) else 409.
- `POST /campaigns/{id}/kpis` 🔑 add · `PATCH /campaigns/{id}/kpis/{kid}` 🔑 (target/baseline/actual) · `DELETE /campaigns/{id}/kpis/{kid}` 🔑
- `POST /campaigns/{id}/link` `{channel, content?, url?}` → `{"url","params"}`: UTM link with
  utm_campaign=slug, utm_source=channel, utm_medium by channel (email→email, google_ads→cpc,
  blog→referral, else social), utm_content=content; url defaults to landing_url (422 if none)
- `POST /campaigns/{id}/measure` 🔑 → pulls actuals: `clicks` = sum of clicks from
  `{SHORTENER_URL}/links?utm_campaign=slug`; `sessions|conversions|ctr|cvr` from
  `{ANALYTICS_URL}/kpis?campaign=slug&from=start&to=min(end,today)` totals; `manual` untouched.
  Unreachable source → KPI left unmeasured, reported in `errors`. Returns scorecard.
- `GET /campaigns/{id}/scorecard` → `{campaign, kpis:[{metric,target_value,actual_value,
  progress_pct,state: met|on_track|behind|not_measured}], assets:{status: count}, errors:[]}`.
  on_track = progress ≥ elapsed share of campaign days. assets from `{CALENDAR_URL}/items?campaign_id=`.
- `GET /report/unmeasured` → campaigns past end_date (or completed) that have a KPI with no actual.
- `GET /insights?days=90` → from shortener links with utm_content = calendar item id:
  `{"by_channel":[{"channel","posts","clicks","avg_clicks"}],"top_posts":[{"item_id","title","channel","clicks"}]}`
  (item details via `{CALENDAR_URL}/items/{id}`).

### 46 learning-service

- `POST /events` 🔑 `{item_id, channel, campaign_id?, decision: approved|edited|rejected, draft, final?, reason?, reviewer?}` → 201 event
- `GET /events?decision=&since=` · `GET /items/{item_id}/attempts` → `{"item_id","rejections"}`
- `GET /examples?channel=&k=3` → `[{"text","channel","decision"}]`: most recent human-approved
  finals, `edited` first (the reviewer's own words), then `approved`; channel filter optional
- `POST /reflect` 🔑 `{since_days: 7, max_events: 20}` → for edited/rejected events not yet
  reflected: gateway prompt `reflect_rule` `{draft, final, reason, channel}` →
  `{generalizable, rule, scope}`; new pending rules (deduplicated case-insensitively) → `{"created":[rules],"reflected":n}`
- `GET /rules?status=pending|active|rejected` · `POST /rules/{id}/status` 🔑 `{status: active|rejected}`
- `GET /rules/summary` → `{"summary": "Rules learned from your edits:\n- ...", "count"}` (active only;
  channel-scoped rules prefixed `[linkedin]`); empty summary when none
- rule: `{id, text, scope (all|<channel>), status, source_event_ids[], created_at}`
