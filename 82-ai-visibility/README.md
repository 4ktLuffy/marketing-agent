# ai-visibility

Deploy **82 of 91** of the marketing agent. It is a **GEO tracker**: when buyers ask
AI assistants about your category, does the answer **name** your brand, **cite** your site,
put you **high in the list**, and say **true things** about you, compared with your competitors?

1. **Questions.** A set of 15–30 realistic buyer questions ("best coffee subscription for a
   remote team of 5?"). The LLM drafts them from your brand profile, products and audience
   (prompt `visibility_questions`). Most name no brand, so they measure **unaided** visibility;
   a few name your brand, to check what AI says about you. A person edits and **approves** the
   set. Approved sets never change, so weekly numbers stay comparable; an edit makes a new
   version.
2. **Runs.** Every approved question goes to every provider that has a key, **3 times** each
   by default (answers vary from run to run). Answers and cited URLs are stored.
3. **Analysis, in code.** Brand and competitor mentions (whole words, aliases), citations of
   your domains and theirs, position in ranked lists (numbered, bulleted, tables), share of
   voice, with 95% Wilson intervals because N is small.
4. **Accuracy.** Every sentence that names your brand (or one of your products, in an answer
   that names the brand) goes to the claim checker (44) against your approved facts. What it
   cannot support lands in "AI says things about you that your facts don't support" (a wrong
   price, an invented product). Sentences where the AI says it doesn't know you are counted
   separately, not checked.
5. **Gaps.** Questions where a competitor is named or cited and you are not, with the pages the
   answers cite and a suggested answer-first FAQ: input for SEO briefs (29/69) and page
   refreshes (68). Workflow 83 runs all of this weekly and turns the gaps and wrong claims into
   calendar ideas.

**Official APIs only.** It never scrapes chatgpt.com, perplexity.ai, Google AI Overviews or
AI Mode: their terms forbid automated extraction, and chatgpt.com's robots.txt disallows
everything (sources in `_dev/research/ai-visibility.md`). An API answer is a **proxy**: one
study found only 15.5–31.6% brand-mention overlap between API and app answers
([Surfer](https://surferseo.com/blog/llm-scraped-ai-answers-vs-api-results/), a vendor that
sells a scraping tracker). Every summary says so.

## How this compares

- **Commercial trackers** ([Profound](https://www.tryprofound.com/pricing),
  [Peec AI](https://peec.ai/pricing), [Otterly.AI](https://otterly.ai/pricing),
  [Semrush AI Visibility](https://www.semrush.com/pricing/ai/),
  [Ahrefs Brand Radar](https://ahrefs.com/brand-radar)) cost about $29 to $3,000+ a month
  (seen 2026-09-27). They measure the same things (mention rate, citations/source share,
  position, sentiment, share of voice), mostly daily. Several collect answers from the
  consumer apps with headless browsers or bought scraped data; none publishes how many runs
  per prompt it uses or a confidence interval.
- **Open-source trackers**: [getcito](https://github.com/ai-search-guru/getcito-worlds-first-open-source-aio-aeo-or-geo-tool)
  (MIT, ~440 stars), [elmo](https://github.com/elmohq/elmo) (MIT, ~370) and
  [geo-aeo-tracker](https://github.com/danishashko/geo-aeo-tracker) (MIT, ~275) lean on paid
  scrapers (Bright Data, DataForSEO) for the consumer surfaces.
  [Canonry](https://github.com/Canonry/canonry) (MIT, ~160) is API-only, like this one.
  No code was copied from any of them.
- **Volatility is the known problem.** In a SparkToro/Gumshoe study, AI tools gave the same
  brand list twice less than 1 time in 100, but a brand's **visibility %** across many runs was
  stable ([SparkToro](https://sparktoro.com/blog/new-research-ais-are-highly-inconsistent-when-recommending-brands-or-products-marketers-should-take-care-when-tracking-ai-visibility/)).
  So 82 reports rates over repeated samples with intervals, and treats list position as
  secondary (average and median, only when listed).
- **What 82 adds:** accuracy checks against your approved facts (44), competitors from the
  one registry (78), gaps fed into your own SEO and refresh workflows, and it runs on your own
  keys and hardware.

## Providers

Each provider is used only when its key env var is set. Keys are sent only to that provider,
in a header (never in a URL), and are never stored, logged or returned. A provider's error
body is never stored either (it can echo part of a key); only `HTTP 401` and the like.

| Provider | API | Sources it returns | Cost per question (list, 2026-09) |
|---|---|---|---|
| `openai_search` | [Responses API](https://developers.openai.com/api/docs/guides/tools-web-search) + `web_search` tool, default `gpt-5-mini` | `url_citation` annotations on the output text | $0.01 per search call + tokens ($0.25 / $2.00 per 1M) |
| `perplexity` | [Agent API](https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/overview) `POST /v1/agent` + `web_search`, model `perplexity/sonar` | `search_results` output items, `url_citation` annotations | the cost the API reports (`usage.cost.total_cost`); about $0.0025 per search + tokens |
| `gemini_grounded` | [Gemini API](https://ai.google.dev/gemini-api/docs/google-search) + `google_search` grounding, `gemini-2.5-flash` | `groundingMetadata.groundingChunks[].web` (the URI is a Google redirect; the title is the domain, which 82 matches on) | $35 per 1,000 grounded prompts + tokens |
| `groq_knowledge` | Groq chat completions, `openai/gpt-oss-120b`, **no web search** | none: "what the model remembers". URLs it writes are kept apart (`urls_in_text`), never counted as citations; citation rate is `null` | $0.15 / $0.60 per 1M tokens |

**Perplexity:** Sonar chat completions were supported only until 2026-09-27, so 82 uses the
Agent API. It still reads the old format (`choices`, `citations[]`, `search_results[]`).

**Gemini:** Google's [Gemini API terms](https://ai.google.dev/gemini-api/terms) say you will
not "cache, frame, syndicate, resell, analyze, train on, or otherwise learn from Grounded
Results", and that Search Suggestions must be shown with them. Scoring stored grounded answers
every week looks like "analyze". So `gemini_grounded` stays **off even with a key** until you
set `VIS_GEMINI_TERMS_ACCEPTED=true` after deciding (with legal advice) that your use is
allowed. This is a design constraint, not legal advice.

Anthropic's web search tool would fit the same pattern
([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool),
$10 per 1,000 searches); it is not implemented.

**Caps.** Per provider per UTC day: `VIS_<P>_DAILY_CALLS` (default 100; Groq 200),
optional `VIS_<P>_DAILY_TOKENS` and `VIS_<P>_DAILY_USD` (0 = no cap). `<P>` is `OPENAI`,
`PERPLEXITY`, `GEMINI` or `GROQ`. A call is counted before it is made; once a cap is hit, the
rest of that provider's questions are stored as `skipped: daily call cap reached (N)`, so a
run shows exactly what was not asked. `GET /usage` and `GET /providers` show calls, tokens,
searches and cost per day. Prices can be overridden with `VIS_<P>_PRICE_IN` / `_PRICE_OUT`
(USD per 1M tokens) and `_PRICE_SEARCH` (USD per search).

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing` network,
with a volume on `/data`. n8n (workflows 83 and 41) calls it at `http://ai-visibility:8000`
(env `VISIBILITY_URL`). It calls the provider APIs over the internet, and on the network the
brand service (05), the competitor registry (78), the gateway (03, for question drafts) and the
claim checker (44). Compose binds the port to `127.0.0.1:8182`. It keeps state in SQLite, so run
exactly one instance and back up the volume.

## Run

```bash
docker build -t ai-visibility .
docker run --rm -p 8182:8000 --network marketing-agent_marketing --env-file .env -v visibility-data:/data ai-visibility
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./visibility.sqlite BRAND_URL=http://localhost:8105 \
  AD_LIBRARY_URL=http://localhost:8178 GATEWAY_URL=http://localhost:8103 CLAIMS_URL=http://localhost:8144 \
  VIS_GROQ_API_KEY=... uvicorn app.main:app --port 8182
```

First use:

```bash
H="X-API-Key: $INTERNAL_API_KEY"
curl -s -XPOST localhost:8182/questions/generate -H "$H" -H 'content-type: application/json' -d '{"count":20,"branded":3,"market":"US"}'
# edit the draft (replace the whole list), then approve it
curl -s -XPUT localhost:8182/question-sets/1 -H "$H" -H 'content-type: application/json' -d '{"questions":[{"text":"..."}]}'
curl -s -XPOST localhost:8182/question-sets/1/approve -H "$H" -H 'content-type: application/json' -d '{"approved_by":"sam"}'
curl -s -XPOST 'localhost:8182/runs?wait=true' -H "$H" -H 'content-type: application/json' -d '{}'
curl -s localhost:8182/summary; curl -s localhost:8182/gaps; curl -s localhost:8182/claims/wrong
```

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`. Reads are open on the internal network, like
78's (the compose port is bound to localhost).

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `providers_enabled`, `active_set` `{id, version, questions}`, `last_run`, `claims_check`, `competitors_source`. No secrets. |
| GET | `/providers` | — | each provider: `enabled`, `key_set`, `needs` (a missing terms flag), `web_search`, `model`, `key_env` (the name, never the value), `caps`, `prices`, `today` usage |
| GET | `/usage` | `?days=7` | `{since, rows: [{day, provider, calls, errors, input_tokens, output_tokens, searches, cost_usd}], total_cost_usd, total_tokens}` |
| POST | `/questions/generate` 🔑 | `{"count": 15–30 (20), "branded": 0–6 (3), "market"?, "notes"?}` | `201` draft set with `questions`, `dropped` (with why) and `warnings` |
| POST | `/question-sets` 🔑 | `{"questions": [{"text", "kind"?}], "notes"?}` | `201` a draft set written by hand |
| GET | `/question-sets` | — | every set with its `version`, `status` (`draft`, `approved`, `retired`) and question count |
| GET | `/question-sets/{id}` | — | the set and its questions (`id, pos, text, kind, branded`) |
| PUT | `/question-sets/{id}` 🔑 | same as POST | replaces a **draft** set's questions. `409` for an approved or retired set |
| POST | `/question-sets/{id}/revise` 🔑 | — | `201` an editable draft copy with the next version number and `parent_id` |
| POST | `/question-sets/{id}/approve` 🔑 | `{"approved_by"?}` | the set, `approved`; the previously approved set becomes `retired` |
| POST | `/runs` 🔑 | `{"set_id"?, "providers"?, "samples"? (1–10), "question_ids"?, "check_accuracy"?: true}`, `?wait=true` | `202 {id, status: "running", providers, providers_without_key, samples, set_id, set_version}`; with `wait=true`, `200` and the finished run. `409`: no enabled provider, no approved set, or a run already running |
| GET | `/runs` | `?limit=20` | recent runs |
| GET | `/runs/{id}` | — | the run: status, providers, samples, `entities` (what counted as us and each competitor), `stats`, answer count |
| GET | `/summary` | `?since=ISO&run_id=` | the latest finished run (since `since`): per provider (below), `accuracy`, `notes`, `history` (mention rate per run), `method` |
| GET | `/claims/wrong` | `?run_id=` or `?since=` | `{runs, claims: [{sentence, kind: number\|unsupported, reasons, providers, questions, count, answer_ids, first_seen}]}` grouped by sentence |
| GET | `/questions/{id}/answers` | `?run_id=` | the question and every answer (text, citations, `urls_in_text`, `analysis`, tokens, cost, error, `wrong_claims`) from that run (default: the latest with that question) |
| GET | `/gaps` | `?run_id=&limit=20` | `{run_id, brand, total, gaps: [{question, kind, providers, answers, competitors: [{name, mentions, cited, avg_position}], cited_pages: [{url, title, domain, owner: us\|<competitor>\|third party, count}], brief_keyword, suggested_faq: {question, answer_first, fact}}]}` |

**Per provider in `/summary`:** `mention` (answers to unaided questions that name us:
`{k, n, rate, ci95}`), `mention_by_question` (questions where at least one sample names us),
`citation` (answers citing our domains; `null` for `groq_knowledge`), `listed`, `avg_position`,
`median_position`, `positions` (histogram), `share_of_voice` (`{name: {mentions, share}}` for us and
each competitor, over unaided answers), `competitors` (each one's mention and citation rate and
average position), `branded` (`answers`, `knows_us`, `says_unknown`, `wrong_claims`), `tokens`,
`cost_usd`, `errors`, `skipped`, and `trend` (`mention_delta`, `citation_delta`, `sov_delta` vs the
previous finished run **on the same question set**; `null` otherwise, with a note).

### How the parts behave

- **Who is "us".** The brand `name` from 05 `/profile`, its optional `aliases:` list, and
  `BRAND_ALIASES`. Our domains: `website`, `allowed_domains` and `OWN_DOMAINS`. Product names
  do not count as a brand mention unless `COUNT_PRODUCT_NAMES=true` ("Team Box" is too
  generic), but they do mark sentences for the accuracy check.
- **Competitors** are the `active` ones in 78 (`GET /competitors?status=active`): name,
  website host and `domains`. 78 has no alias field, so `COMPETITOR_ALIASES="Atlas Coffee
  Club=Atlas|Atlas Coffee; Trade Coffee=Trade"` adds them. Each run stores this snapshot, so an
  old run is never re-read with new aliases.
- **Mentions** are whole words. Several words match in any case, with spaces or a hyphen
  between them and a possessive `'s`. A single word ("Trade") matches only as written or in
  capitals, so it is not found in "trade-off" or "fair trade". A domain matches as a domain,
  not inside a longer host. Markdown bold, links and citation markers are removed first.
- **List position** is the 1-based place of the first item that names the entity, in the first
  list that names it. Numbered items (`1.`, `2)`, `### 3.`) form a list, and a new list starts
  when the numbers restart at 1. Top-level bullets count when there is no numbered list;
  indented sub-bullets never count. Table rows count, with the header skipped. Only the item's
  head (its bold lead, or the text up to the first dash or colon) is searched, so "better than
  X" in item 1's description does not put X at position 1.
- **Citations** are the provider's own source fields (table above), never URLs written in the
  text. Subdomains of a domain count.
- **Rates** count answers to unaided questions (branded questions name us by design). The 95%
  interval is Wilson's; it treats every answer as independent, which overstates precision when
  the samples for one question agree, so `mention_by_question` is given too.
- **Question checks.** A drafted question that names a competitor is dropped (it would measure
  their brand), one that names us becomes `branded`, and duplicates go. Fewer than 15 usable
  questions, or fewer branded ones than asked for, is a warning: add them by hand before approving.
- **One run at a time.** Providers are asked in parallel (one thread each), questions in order
  with `VIS_DELAY_S` between calls; a `429` or `5xx` is retried twice (Retry-After, at most 30 s).
  A run interrupted by a restart is marked `failed`.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for 🔑 endpoints (`503` when unset). Also sent to 03 and 44. |
| `DB_PATH` | `/data/visibility.sqlite` | SQLite file (WAL) |
| `VIS_OPENAI_API_KEY` / `VIS_PERPLEXITY_API_KEY` / `VIS_GEMINI_API_KEY` / `VIS_GROQ_API_KEY` | empty | Provider keys; empty = provider off. In the stack: `VISIBILITY_*_API_KEY` in `01-marketing-stack/.env` |
| `VIS_GEMINI_TERMS_ACCEPTED` | `false` | Gemini grounding runs only with this `true` (see Gemini above) |
| `VIS_OPENAI_MODEL` / `VIS_PERPLEXITY_MODEL` / `VIS_GEMINI_MODEL` / `VIS_GROQ_MODEL` | `gpt-5-mini` / `perplexity/sonar` / `gemini-2.5-flash` / `openai/gpt-oss-120b` | Models |
| `VIS_GROQ_REASONING_EFFORT` | `low` | For gpt-oss models |
| `VIS_PERPLEXITY_TOOL_CHOICE` | `auto` | `required` makes every Perplexity answer search |
| `VIS_<P>_DAILY_CALLS` / `_DAILY_TOKENS` / `_DAILY_USD` | 100 (Groq 200) / 0 / 0 | Caps per UTC day |
| `VIS_<P>_PRICE_IN` / `_PRICE_OUT` / `_PRICE_SEARCH` | list prices above | Cost accounting |
| `VIS_SAMPLES` | `3` | Answers per question per provider |
| `VIS_MAX_OUTPUT_TOKENS` | `2048` | Longest answer (reasoning tokens count) |
| `VIS_COUNTRY` | empty | 2-letter country for OpenAI's search location, and the questions' market |
| `VIS_DELAY_S` | `1` | Pause between two calls to one provider |
| `VIS_TIMEOUT` | `120` | Seconds per provider call |
| `VIS_MAX_CLAIM_CHECKS` | `60` | Distinct sentences sent to 44 per run |
| `BRAND_ALIASES` / `OWN_DOMAINS` / `COMPETITOR_ALIASES` / `COUNT_PRODUCT_NAMES` | empty / empty / empty / `false` | See "Who is us" |
| `BRAND_NAME` | empty | Used only when 05 is unreachable |
| `BRAND_URL` / `AD_LIBRARY_URL` / `GATEWAY_URL` / `CLAIMS_URL` | `http://brand-service:8000` / empty / `http://llm-gateway:8000` / empty | 05, 78, 03, 44. Empty 78: no competitors. Empty 44: no accuracy check |
| `GATEWAY_TIMEOUT` / `CLAIMS_TIMEOUT` | `300` / `600` | Seconds |

## Tested

- Unit tests (`pytest -q`): mention detection (aliases, whole words, case rules, domains),
  list positions (numbered, headings, restarts, bullets, tables, sub-bullets), citation parsing
  for each provider's documented format (OpenAI `url_citation`, Perplexity Agent API and legacy
  Sonar, Gemini redirect URIs with domain titles, Groq none), Wilson intervals, wrong-claim
  detection through 44, "I don't know this brand" counted not checked, daily caps, a provider
  without a key skipped, keys never in logs, responses or the database file, question-set
  versioning and trend only on the same set version. Every external service is mocked with respx.
- A real run: see "Real run" below.

## Real run (2026-09-27, Groq only)

Local services (no Docker), `groq_knowledge` only (`openai/gpt-oss-120b`, reasoning effort low,
`VIS_MAX_OUTPUT_TOKENS=1000`), the fictional example brand, and two real coffee subscriptions
(Trade Coffee, Atlas Coffee Club) added as competitors to a **local** 78 for this test only.

- **Questions.** The local writer model (`mkt-writer`, qwen2.5 7B) drafted 15 usable questions
  but **0 of the 3 branded ones** asked for (hence the warning that is now given). By hand, 3
  vague questions were rewritten and 3 branded ones added: 18 approved (15 unaided, 3 branded).
- **Run:** 18 questions × 2 samples = 36 calls, 0 errors, 5 minutes including the claim checks.
  **35,487 Groq tokens** (2,944 in, 32,543 out), about **$0.02** at list price.
- **Northwind Roasters: named in 0 of 30 unaided answers** (95% interval 0–11%; 0 of 15
  questions, 0–20%). That is the expected baseline: the brand is fictional and this provider has
  no web search.
- **Competitor detection works:** Trade Coffee in 11 of 30 answers (37%, 22–54%, average list
  position 2.5), Atlas Coffee Club in 4 of 30 (13%, 5–30%, position 4.75). Share of voice: Trade
  73%, Atlas 27%, Northwind 0%. 7 gaps, led by "Which coffee subscriptions offer free shipping
  in the US?" (both competitors named); its suggested FAQ used the matching approved fact
  ("Free shipping on subscriptions in the US").
- **Accuracy:** asked by name, the model never said it didn't know the brand. It **invented**
  specifics: 14 sentences were checked, 10 flagged, including the Team Box "priced at $69.00" and
  "at $199" (the fact says $79), a "2023–2024 release" and a 12 oz bag. One flag was a paraphrase
  of a true fact ("You can pause your subscription at any time", from "skip or pause a delivery
  ... at any time"): the claim checker's known paraphrase weakness, so each flag needs a look.
- Every answer reached the 1,000-token cap (gpt-oss writes long tables), so lists were sometimes
  cut short; the default cap is 2,048.
- The key was loaded only into 82's process; it is not in its log or database file.

## Known limits

- **API answers are not app answers.** No personalisation, memory, location or the app's own
  search stack. Read the numbers as a trend on a fixed panel, not as "your ChatGPT ranking".
- **`groq_knowledge` has no web search**: it shows what a model remembers from training, which
  lags by months. A new brand scores zero there whatever its site says.
- "Not supported by your approved facts" is not always wrong: 44 only knows what is in
  `brand.yaml` and the knowledge base. Each claim needs a person's look.
- Mention matching is literal. A misspelt brand name or a nickname not in the aliases is missed;
  a competitor whose name is a common word needs a careful alias.
- Answers are kept (a few KB each; about 25 MB a year at 20 questions × 4 providers × 3 samples
  weekly). There is no retention setting yet.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
