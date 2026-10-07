# Marketing Agent — blueprint

A marketing agent that works on top of **any AI** (free or paid chatbots through task packs, Claude
through its connector, a hosted model, or a local model through Ollama), is driven by **n8n**, and is
reachable two ways: **n8n chat** (you talk to it) and **schedules** (it works on its own).

Every folder in `marketing-agent/` is one deploy = one GitHub repo. 91 deploys (01–44 below, 45–53 in Phase 2, 54–81 in Phase 3, 82–83 AI visibility, 84 paid ads, 85 client report, 86 email flows, 87 product feed, 88 task bridge, 89 Claude connector, 90 approval service, 91 ERP facts).

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
| Every 15 min | Approved + due calendar items → published (blog → CMS bridge as a draft, social → publish webhook) |
| Monday 07:00 | Next week's content plan drafted into calendar |
| Monday 09:00 | KPIs, clicks, hooks, campaigns, pillars → HTML weekly report with one next action per channel |
| Friday 10:00 | This week's published items → newsletter issue → Listmonk draft campaign (66) |

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

### Install profiles (01)

| Profile | Services (compose) | Workflows not imported |
|---|---|---|
| core | n8n, postgres, 03 (+ verifier), 05–22, 44, 45, 46, 72 | 56, 60, 65, 68, 69, 74, 75, 76, 83, 84 (n8n/) |
| growth | core + 54, 55, 58, 61, 62, 63, 67, 70, 71, 73, 84 | 83 |
| full | growth + 78, 79, 80, 82 | — |

Rule: a service whose absence would break a core workflow is in core (45 is: the chat tools 47
and 53 need it, and 26, 40, 41 and 72 read it). A workflow that only does something with a growth/full service is imported only
with that profile (`01-marketing-stack/scripts/profiles.sh`). A chat tool is always imported and
answers "not installed" when its service's URL is empty (64, 77, 81), so the agent never calls a
missing workflow. On a smaller profile `install.sh` sets the n8n URLs of missing services to empty,
and every workflow that reads them skips that step.

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
- `POST /items/{id}/notes` 🔑 `{"note","external_url"?}` appends a line to `notes` in any status (39: "sent to CMS as draft")
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
- item gets `hook_style` (str|null); accepted by `POST /items` and `PATCH` (locked once approved,
  like `body`); `GET /items?hook_style=` filters

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
- `GET /insights/hooks?days=90&explore=0.2&seed=` → same join as `/insights`, grouped by the
  calendar item's `hook_style` (question|fact_led|story|how_to|benefit|contrarian):
  `{"recommended":[2 styles],"explored":style|null,"styles":[{"hook_style","posts","clicks",
  "clicks_per_post","posterior_mean","sample","recommended"}],"unlabeled_posts","method","errors"}`.
  Thompson sampling on Gamma(1+clicks, 1+posts) per style; with probability `explore` the second
  pick is a least-tested style. All six styles always listed. Workflow 26 passes `recommended` as
  `prefer_hooks` to `social_posts`.

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

## Phase 3: real publishing, real analytics, fewer invented facts

| # | Folder | Kind | Where | Depends on |
|---|---|---|---|---|
| 54 | `54-postiz-bridge` | service | Docker host | Postiz (self-hosted or cloud) |
| 55 | `55-umami-sync` | service | Docker host | Umami, 20 |
| 56 | `56-wf-sched-analytics-sync` | n8n workflow (cron) | n8n | 55 |

### 54 postiz-bridge
The publisher (39) posts approved items to `PUBLISH_WEBHOOK_URL`; point that at this service.
- `POST /publish` 🔑 `{"id","channel","title","text","link"?,"campaign"?}` → `{"url"?, "postiz_id", "status"}`
  (39 stores `url` as the item's `external_url`). Channel → Postiz integration via `CHANNEL_MAP`.
  Unmapped channel → 422 naming the channel. Postiz error → 502. `DRY_RUN=true` → validates and
  returns `{"status":"dry_run"}` without calling Postiz.
- `GET /integrations` → the Postiz integrations (id, name, provider) to fill `CHANNEL_MAP`.
- Env: `POSTIZ_URL`, `POSTIZ_API_KEY`, `CHANNEL_MAP` (JSON `{"linkedin":"<integration id>",...}`),
  `DRY_RUN`, `INTERNAL_API_KEY`.

### 55 umami-sync
- `POST /sync` 🔑 `{"from":"YYYY-MM-DD","to":"YYYY-MM-DD"}` → reads Umami per day × utm_campaign ×
  utm_source (as channel): sessions (visits) and conversions (event `CONVERSION_EVENT`), then uploads
  them to 20 as generic CSV with `?source=generic&label=umami` → `{"rows","days","errors":[]}`.
- `GET /health`
- Env: `UMAMI_URL`, `UMAMI_WEBSITE_ID`, `UMAMI_USERNAME`/`UMAMI_PASSWORD` (self-hosted login) or
  `UMAMI_API_KEY` (cloud), `CONVERSION_EVENT`, `ANALYTICS_URL`, `INTERNAL_API_KEY`.

**20 analytics-ingest (additive):** `POST /upload?...&label=<name>` stores rows under source `<name>`
(preset still chosen by `source`), so synced rows never overwrite manual CSV uploads.

**03 llm-gateway (additive):** injects `facts` (numbered lines from 05 `GET /facts`, cached 60 s)
alongside `brand`, so writing prompts can list the only claims they may make.

## Content engine (61)
One pillar → a month of planned, varied slots. Design and sources: `_dev/research/content-volume.md` §4.
The service plans in code and gates novelty; it never writes or publishes. n8n env `ENGINE_URL=http://content-engine:8000`.
Details, shapes and the workflow contract: `61-content-engine/README.md`.
- `POST /pillars` 🔑 `{title, brief, audience, source_text?, source_url?, channels[], month: YYYY-MM, promo_max: 0.2}` → 201.
  Channels: linkedin|x|instagram|facebook|threads|mastodon|blog|email|video. `GET /pillars?month=&status=` · `GET /pillars/{id}`
- `POST /pillars/{id}/atoms` 🔑 `{atoms:[{kind: claim|story|faq|tip|stat|objection|quote, text, verified, evidence?, promo?}]}` → 201
  `{added, ids, skipped, verified_total, min_atoms}` (same text case/space-insensitive → skipped). `GET /pillars/{id}/atoms?verified=`
- `POST /pillars/{id}/plan` 🔑 `{start_date? (default 1st of month), weeks: 4 (1–8), cadence?: {channel: 0–7/week}, seed? (default pillar id)}` →
  `{requested, planned, kept_drafted, unfilled: {channel: n}, slots[]}`. 422 when verified atoms < `MIN_ATOMS` (12); 409 when paused.
  Slot: `{id, pillar_id, date, time_utc, channel, format: post|thread|carousel_text|blog|email|video_script, atom_id,
  hook_style, status: planned|drafted|dropped, calendar_item_id, reason, atom:{kind,text,evidence,promo}}`.
  Rules: slots per channel = cadence × weeks (default linkedin 3, x 7, instagram/facebook/threads/mastodon 3, blog 1,
  email 1, video 2 per week); ≤ 1 per channel per day, weekdays first; each atom on ≤ 3 channels, never twice on one
  channel, a different hook each time; promo ≤ floor(promo_max × channel slots); deterministic per seed; re-plan
  replaces only `planned` slots (drafted ones are kept and counted). Shortfall is reported in `unfilled`.
- `GET /pillars/{id}/slots?status=&channel=` · `GET /slots/{id}` · `POST /slots/{id}/status` 🔑 `{status: planned|drafted|dropped,
  calendar_item_id?, reason?}` (planned→drafted|dropped, drafted→dropped; same status = update fields only; else 409)
- `POST /novelty/register` 🔑 `{item_id, channel, text, created_at?}` (upsert on item_id) ·
  `POST /novelty/check` `{text, channel, exclude_item_id?, any_channel: false}` → `{novel, reasons[], closest:{item_id,
  channel, score_ngram, score_embed}, compared, embedding_checked}`. Not novel when word-5-gram Jaccard ≥ `NGRAM_MAX` (0.30),
  or the first 8 normalized words match, or Ollama `/api/embed` cosine ≥ `EMBED_MAX` (0.85); same channel, last
  `NOVELTY_DAYS` (90). Ollama down → embedding skipped, `embedding_checked: false`.
- `POST /pillars/{id}/outcomes` 🔑 `{item_id, decision: approved|edited|rejected}` (first decision per item counts) ·
  `GET /pillars/{id}/health` → `{decided, approved_clean_rate, rejected_rate, paused, reason, ...}`. After ≥ `MIN_DECISIONS`
  (10) since the last resume: pause when rejected_rate > 0.25 or approved_clean_rate < 0.5. `POST /pillars/{id}/resume` 🔑.
- Workflows: **64** `plan_content_month` (chat tool): pillar → `pillar_atoms` → drop faq/objection ending "?" → 44
  `/verify` per atom (one at a time) → atoms → plan → one calendar `idea` per slot (notes `engine pillar #P slot #S atom #A
  (<kind>)`, slot keeps `planned` + `calendar_item_id`). **65** daily 06:00: planned slots of active pillars within
  `ENGINE_LOOKAHEAD_DAYS` (7), ≤ `ENGINE_DRAFTS_PER_RUN` (12), one at a time → prompt by format → `/novelty/check` (1 retry
  with another hook, then slot dropped) → 35 → card (17) → PATCH the idea item → draft/in_review → `/novelty/register` →
  slot drafted. **38** posts each decision on an engine item to `/outcomes`; **40** adds no idea on a channel+day that has
  an engine item.
- 04 prompt `pillar_atoms` {pillar_title, brief, audience, source_text?, facts?, n=24} → `{atoms:[{kind, text ≤ 280, promo, evidence}]}` (15–30).

## Experiment loop (45, 61, 46; workflows 74–76)
Fixed two-arm tests of ONE variable, decided in code; the LLM only proposes. Design and sources:
`_dev/research/experiment-loop.md` §5. Unit = one post; metric = its short-link clicks in the first 72 h.
- **16 (additive):** `GET /links?...&window_hours=72` → each link also has `clicks_window` (clicks before
  `created_at + window_hours`; the publisher 39 creates the link at publish time).
- **45 experiments** (`app/experiments.py`, stats in `app/expstats.py`): experiment `{id, hypothesis, variable:
  hook_style|format|cta|length|time, channels[], metric: clicks_72h, min_posts_per_arm: 12 (4–200), max_weeks: 8 (1–26),
  rope: 0.15, status: proposed|approved|running|decided|stopped, decision: winner|no_practical_difference|inconclusive|null,
  winner_arm, created_by: agent|human, approved_by, arms:[{label: A|B, value, brief}], assigned:{A,B}, looks[], next_look_at}`.
  - `POST /experiments` 🔑 → 201 `proposed`; exactly 2 arms (hook_style/format values from the enums, time = HH:MM).
    409 when the same variable + values + channel is proposed/approved/running, or was decided
    `no_practical_difference` (stored so it is not re-run); a past winner may be re-run (replication).
  - `GET /experiments?status=a,b&channel=` · `GET /experiments/{id}` · `GET /experiments/{id}/assignments`
  - `POST /experiments/{id}/status` 🔑 `{status: approved|running|stopped, by?, reason?}`; `approved` also needs
    `X-Approver-Key` = `APPROVER_KEY` (only n8n holds it); `decided` only via /decide.
  - `POST /experiments/{id}/assign` 🔑 `{slots:[{slot_id, arm, channel, date, time_utc, item_id?}], remove_slot_ids[]}` (61
    calls it); the first assignment starts an approved experiment (`running`, `started_at`); ≤ 2 running per channel (409).
  - `GET /experiments/{id}/analysis` → at the latest preset look k (every `EXPERIMENT_LOOK_DAYS`=7 days from `started_at`,
    capped at `max_weeks`), with only posts whose 72 h window closed before the look: per arm `{value, posts, clicks,
    clicks_per_post, posterior_mean, pending, unpublished}`, `lift_hdi` (95% HDI of rate_B/rate_A − 1), `p_b_better`,
    `expected_loss`, `dispersion`, `decision: continue|winner|no_practical_difference|inconclusive`, `winner`, `summary`.
    Before the first look: `decision: not_due`, no numbers. Item ids of slots come from `{ENGINE_URL}/slots/{id}`.
  - `POST /experiments/{id}/decide` 🔑 records the due look (409 + `next_look_at` when none is due; 502 when 16 is down);
    anything but `continue` → `decided`.
  - Rule: Gamma-Poisson per arm, prior = pooled clicks/post of both arms × `EXPERIMENT_PRIOR_POSTS` (2) posts, counts
    divided by the pooled Pearson dispersion (≥ 1); HDI above +rope or below −rope → winner; inside ±rope →
    no_practical_difference; no verdict before `min_posts_per_arm` per arm; last look → inconclusive.
- **61 (additive):** with `CAMPAIGNS_URL` set, `POST /pillars/{id}/plan` reads approved/running experiments and gives each
  slot on their channels an arm, balanced by weekday, then hour bucket, then overall (random tie-break); hook_style /
  format / time arms are forced on the slot; slot gets `experiment_id`, `arm`, `experiment: {id, variable, arm, value, brief}`;
  plan response gets `experiments[]`, `experiments_error`. It reports slots (and re-plan removals) to 45 `/assign`; a refused
  or failed report clears the slot's experiment. **65** keeps a hook_style arm on the novelty retry and adds cta/length arms
  to the prompt.
- **46 (additive):** rule statuses add `provisional|retired`; rule gets `source: review|experiment, support, contradicts,
  replicated, source_experiment_ids[], contradicting_experiment_ids[], evidence[], last_confirmed_at`.
  `POST /rules/from-experiment` 🔑 `{experiment_id, decision, variable, channels[], values[2], winner?, loser?, lift_hdi?,
  decided_at?, summary?}` → `{action: created|replicated|confirmed|contradicted|demoted|retired|counted|already_counted|none, rule}`.
  Winner → provisional "Prefer X over Y (…) on C."; later same-direction winner → support+1, `replicated`; contradiction or
  no-difference → contradicts+1, active → provisional, retired when contradicts ≥ support. `GET /rules/review` = pending +
  replicated provisional (form 51). Active needs replicated (else 409). `/rules/summary` stays active-only, so writers never
  see provisional rules.
- Workflows: **74** Mondays 07:00: 45 hooks/insights + all experiments → prompt `experiment_proposals` → code checks
  (valid values, allowed channel, evidence numbers in the data, no repeat) → `POST /experiments` (created_by agent) →
  notify with the form link. **75** Mondays 08:30: `/decide` per running experiment → winner/no-difference to 46
  `/rules/from-experiment` → notify. **76** form `mkt-experiments`: approve (with approver key) / reject proposals.
  **41** adds an "Experiments" section; **51** lists `/rules/review`.

## AI visibility / GEO (82, workflow 83)

- **82 ai-visibility** (service, port 8182, SQLite `/data`). Providers, official APIs only, each on only with its key:
  `openai_search` (Responses API + `web_search`, `url_citation` annotations), `perplexity` (Agent API `POST /v1/agent`,
  `search_results` items; Sonar chat completions retired 2026-09-27), `gemini_grounded` (`google_search` grounding;
  also needs `VIS_GEMINI_TERMS_ACCEPTED=true`, Google's terms forbid analysing Grounded Results), `groq_knowledge`
  (no web search). Daily call/token/USD caps per provider; `/usage` holds tokens and cost.
- Question sets are versioned: `POST /questions/generate` 🔑 {count 15–30, branded 0–6} → prompt `visibility_questions`
  → draft (a question naming a competitor is dropped, one naming us becomes `branded`); `PUT /question-sets/{id}` 🔑
  (draft only), `POST /question-sets/{id}/approve` 🔑 (retires the previous), `/revise` 🔑 (new draft version).
- `POST /runs` 🔑 {set_id?, providers?, samples? (3), question_ids?} `?wait=true` → approved questions × providers ×
  samples. Code measures brand/competitor mentions (whole words, aliases from 05 `aliases`/`BRAND_ALIASES` and
  `COMPETITOR_ALIASES`; competitors = 78 active registry), citations of our/their domains, list position, share of
  voice. Sentences about us → 44 `/verify`; unsupported → `claims`.
- Reads: `GET /summary?since=&run_id=` (per provider: mention rate on unaided questions with Wilson ci95, per-question
  rate, citation rate (null without web search), list position, SoV, competitors, branded accuracy, trend vs the
  previous run on the same set), `GET /claims/wrong`, `GET /gaps` (competitor named/cited, we are not; cited pages;
  suggested answer-first FAQ; `brief_keyword`), `GET /questions/{id}/answers`, `/providers`, `/usage`, `/runs`.
- **83** Tuesdays 06:00: `/health` → `POST /runs?wait=true` → summary, wrong claims, gaps → notify; wrong claims then
  gaps → calendar `visibility_gap` ideas (≤ `VISIBILITY_IDEAS_PER_WEEK`, once per 28 days). **39** `NOT_POSTS` includes
  `visibility_gap`. **41** adds an "AI visibility" line from `/summary`.

## Paid ads (84)

- **84 ads-sync** (service, port 8184, SQLite `/data`, growth profile). Read-only: Meta Marketing API Insights
  (`GET /v26.0/act_<id>/insights`, `time_increment=1`, campaign/adset/ad level, token in the Authorization header,
  `x-business-use-case-usage` parsed) and Google Ads API (`POST /v25/customers/<id>/googleAds:searchStream`, GAQL over
  `campaign` with `segments.date`, OAuth refresh token + developer token + optional `login-customer-id`). LinkedIn Ads:
  not implemented (README says why). Daily rows per platform/account/level/entity: spend, impressions, clicks,
  conversions, revenue, currency; CTR, CPC, CPM, CPL, ROAS, CVR and deltas computed in code; currencies never mixed.
- Campaign mapping to 45 slugs: `POST /mappings` 🔑, then `utm_campaign` in the Google campaign's URL suffix/template,
  then our slug inside the platform campaign name.
- `POST /budgets` 🔑 {campaign, month, amount, currency, weighting linear|weekday, weekday_weights?, cpl_target?,
  roas_target?}; `GET /pacing`; `GET /alerts` (overspend ≥ `ALERT_OVERSPEND_PCT`, underspend, CPL above / ROAS below
  target over `ALERT_WINDOW_DAYS`, spend with 0 conversions for `ALERT_ZERO_CONV_DAYS`); `POST /alerts/check` 🔑 marks
  the new ones sent (again after `ALERT_REPEAT_DAYS`); `GET /summary?days=7` (per platform, per currency total, top
  campaigns, week-over-week deltas, `facts` sentences).
- `84-ads-sync/n8n/workflow.json` daily 07:30: `/health` → `POST /sync {days:7}` → `POST /alerts/check` → notify new
  alerts and sync failures. **41** puts 84's `facts` into the `weekly_actions` data (the number check accepts only
  those numbers) and adds an "Ads" section; **72** performance page shows an ads panel.

## Client report (85)

- **85** n8n schedule, 1st of the month 08:00, every profile (a source whose URL env is empty is skipped). Last
  calendar month vs the month before (`CLIENT_REPORT_MONTH=YYYY-MM` overrides). One GET per installed source: 05
  `/profile` 🔑 (name), 20 `/kpis?compare=false` for each month (all sources; `source=umami` with `UMAMI_SYNC_URL`,
  `source=gsc` with `GSC_URL`), 45 `/insights/posts`, `/campaigns`, `/experiments`, 84 `/summary?days=<days in
  month>&end=<last day>` with `ADS_URL`, 19 `/items?status=published,approved` and `?channel=client_report`.
- "What we did" (calendar work of the month by channel, active campaigns with KPI actuals, decided experiments) and
  "What changed" (value, previous, change per measure) are computed in code. Prompt `client_report_summary` gets them
  as finished sentences; the workflow drops a sentence with a number (or ISO date, spelled-out count, "doubled")
  not in that data, and one with a causal word without a hedge. Dropped sentences and skipped/failed sources go to
  the item's notes only. LLM failure: tables only.
- 21 `/render` (pipe tables in the highlights) → 19 item `client_report` / `in_review`, title `Client report
  <Month YYYY>`; one per month. 39 lists `client_report` in `NOT_POSTS`: approving it never sends it. The owner is
  notified; a person forwards the report.

## Lifecycle email flows (86)

- **86 flow-runner** (service, port 8186, SQLite `/data`, growth profile, `DRY_RUN` fixed to `true` in compose).
  Flows `welcome` (trigger `subscribed`), `onboarding` (`trial_started`), `winback` (`inactive`) and custom ones
  (`POST /flows` 🔑). A flow has versions `{steps: [{delay_hours, subject, body_markdown}], exit_events}`;
  `delay_hours` counts from entry. Every edit (`POST /flows/{name}/versions` 🔑) is a new draft; only the one
  `approved` version runs. `POST /flows/{name}/draft` 🔑 drafts one with prompt `email_sequence` (03, the prompt 57
  uses) and checks each email with 44 (`/verify`); flagged sentences go to the version's notes.
- `POST /events` 🔑 `{id?, type, contact: {email, consent?, source?}, at?, data?}`, idempotent on `id`. Entry needs
  recorded consent, an approved version, no pause, an event under `FLOW_ENTRY_MAX_AGE_HOURS` (48) old, and the
  contact not already in that flow. `unsubscribed` suppresses the contact forever and exits every flow; an exit event
  exits a flow when it happens after entry. `consent: false` withdraws consent and exits every flow.
- Arms: `sha256(salt|flow|email)` → `holdout` for `holdout_pct` % (default 15), else `flow`. The salt is `FLOW_SALT`
  or generated once per install. Holdout contacts get nothing; in A/A mode (`PATCH /flows/{name} {mode:"aa"}`) both
  arms get the same emails, as the negative control for the tracking.
- `POST /tick` 🔑: the next due step per contact (never two in a row: `FLOW_MIN_GAP_HOURS`, 12), up to
  `FLOW_DAILY_CAP` (200) outbox rows per UTC day. Right before each send, inside the send transaction: enrollment
  active, contact consented and not suppressed, no exit event since entry, version still approved, not paused. The
  outbox row is written first (unique per contact and step), so a step is never sent twice. `DRY_RUN`: the row is
  the send. Otherwise `POST {NEWSLETTER_URL}/tx` (63), a stub: 63 has no transactional send yet, so it fails and is
  recorded as failed, never retried. Kill switch: `FLOW_PAUSED=true` or `POST /pause` 🔑; `POST /resume` needs
  `X-Approver-Key`.
- Review: `POST /flows/{name}/versions/{n}/submit` 🔑 → 19 item `email_flow` / `in_review`, body = the whole
  sequence (`--- Email 1 of 3 · 0 hours after entry ---` / `Subject: …` / body). 39 lists `email_flow` in
  `NOT_POSTS`. `POST /reviews/sync` 🔑 + `X-Approver-Key`: 19 item approved → version approved (an edited text is
  parsed into a new version and approved; unreadable text is refused); rejected → rejected. Or approve directly:
  `POST /flows/{name}/versions/{n}/approve` 🔑 + `X-Approver-Key` (refused when `APPROVER_KEY` is unset).
- `GET /flows/{name}/results?days=30` 🔑: per arm entered, emails sent, clicked, purchased, unsubscribed (contacts
  with the event after entry); flow − holdout with a 95 % two-proportion interval, "not enough data" under
  `FLOW_MIN_N` (100) per arm or fewer than 5 events. No opens.
- `86-flow-runner/n8n/workflow.json` every 15 minutes: `/health` (skipped when `FLOW_URL` is empty) → `POST
  /reviews/sync` (with the approver key) → `POST /tick` → notify only when something was sent, a flow was approved,
  rejected or is waiting, a cap or pause was hit (once each), or a call failed.

## Product feed titles (87)

- **87 feed-optimizer** (service, port 8187, SQLite `/data`, full profile). `POST /batches?name=` 🔑 with the feed
  file as the body (Google Merchant Center CSV or TSV with a header; UTF-8, BOM kept; `FEED_MAX_BYTES` 10 MB,
  `FEED_MAX_ROWS` 5000; unique non-empty `id`; unknown columns kept). The upload is stored as it came.
- Every product gets a rule-based title at upload (no LLM): brand + gender/age + the cleaned original title +
  product type (short titles only) + the colour, size, material and pattern the title lacks. A word the original
  title has that a filled field contradicts is left out, with a warning.
- `POST /batches/{id}/propose` 🔑 `{limit, descriptions, llm}`: per pending product one call to 03 with prompt
  `feed_title` (vars: `product` = the row's descriptive fields as JSON, never price/id/gtin/links/custom labels;
  `with_description`). The title and description are checked in `app/checks.py`: a number, colour, material, size,
  gender, age or claim word not in the row (a filled field wins over the rest of the row), another product's or a
  well-known brand, promo text, a price, symbols, ALL CAPS, over 150 (5000) characters, a missing brand, HTML or a
  link → rejected with reasons; the rule-based title (or the original description) is used instead. A gateway that
  is unreachable stops the run with the product still pending.
- `POST /batches/{id}/approve` / `revoke` 🔑 + `X-Approver-Key` (`{ids}` or `{all: true}`); the approved text is
  copied. `GET /batches/{id}/export.csv|tsv` 🔑: the uploaded file with only approved `title`/`description` cells
  replaced (same format: every other byte as uploaded; the export is parsed again and must match on every other
  column, else 500). `GET /batches/{id}/supplemental.csv|tsv` 🔑: `id,title(,description)` of approved products.
  Nothing goes to Merchant Center.
- Control room (72) page **Product feed** (`FEED_URL`; empty = "not installed"): uploads with counts and the
  downloads, proxied with the service key. Approval stays with the approver key (72 holds none).

## Task bridge and scoped facts (88, 05)
- **Why**: most people use free chatbots, which can't connect to our server and must not be scripted.
  The most common marketing error in every industry is a correct fact used in the wrong scope or
  after it expired (another branch's price, a plan-only feature, a certificate for one variant).
- **05 scoped facts (v2)**: each fact has a stable key, subject, type, value as written, basis,
  conditions, scope (sites, regions, channels, segments, plan tiers, variants), validity dates,
  source, claim class, required disclosures, allowed/forbidden phrasing, owner, sensitivity
  (public/internal/restricted), append-only versions. Confirming needs `FACT_OWNER_KEY` (05 and the
  control room only). Industry starter kits suggest fact types and risky phrasings. Legacy `/facts`
  keeps its ids and shape.
- **88 task bridge**: pack (≤ 8,000 characters, share preview, internal values only as `[[slots]]`,
  restricted never) → paste-back (tolerant piece splitting) → slot filling and deterministic
  evidence per sentence (match, needs judgement, conflicting/expired at the publish date, wrong
  scope, no source, missing disclosure, forbidden phrase) → 19 items bound to their text hash →
  export only when approved, unchanged and still valid; fact changes send items back to draft.
- **19**: item versions, append-only audit, approval with the hash the reviewer saw (409 if changed).
- **Measured**: three sets of fictional companies (lodge, manufacturer, SaaS, shop, clinic, café,
  consultancy; freight, tutoring, homes, building supplies, salon; plus a third held-out set).

