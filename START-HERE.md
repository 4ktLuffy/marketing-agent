# Local-LLM marketing agent: start here

A marketing agent that runs on **your own model (Ollama)**, is orchestrated by **n8n**, and
works two ways:

- **You chat with it** in n8n's chat: "write an X + LinkedIn post about our decaf",
  "SEO brief for 'coffee subscription'", "what's waiting for review?".
- **It runs campaigns**: "plan a Team Box campaign in October on LinkedIn and email, goal
  20 sign-ups". It creates the campaign with targets, plans dated posts, drafts them in the
  background, and measures clicks and results against the targets every day.
- **It learns from you**: every approve, edit and rejection is recorded. A rejection with a
  reason is rewritten automatically. Each week it proposes writing rules from your edits,
  and you keep or reject them in a form.
- **It works on a schedule**: a morning trend digest, a competitor watch every 6 h, a weekly
  content plan, a weekly KPI report, and publishing approved posts every 15 minutes.

**Every draft is fact-checked** against your approved facts (claim checker, 44): claims the
facts don't support are rewritten out or sent to you flagged.

**Nothing is published until a person approves it** in the approval form. The agent can't
approve its own work; that rule is enforced in the workflow, not just stated in a prompt.

The example brand is a made-up coffee subscription, *Northwind Roasters*. Replace it with
yours in the control room's **Brand setup** (or `05-brand-service/config/brand.yaml`) and add
your FAQs in the knowledge-base form (42). After installing, [PILOT.md](PILOT.md) is the
day-by-day plan.

## The 90 deploys: each folder is one GitHub repo

| Group | Deploys | Where each one goes |
|---|---|---|
| **Platform** | 01 stack · 02 Ollama models · 03 LLM gateway · 04 prompt library | 01 → Docker host · 02 → the Ollama machine · 03 → container · 04 → mounted into 03 |
| **Brand & memory** | 05 brand service · 06 knowledge base | containers |
| **Research** | 07 page extractor · 08 RSS watcher · 09 change monitor · 10 keyword suggest · 11 social listening · 12 SEO auditor | containers |
| **Content tools** | 13 readability · 14 platform rules · 15 UTM builder · 16 link shortener · 17 image cards · 18 email renderer | containers (16 needs a public URL) |
| **Operations** | 19 content calendar · 20 analytics ingest · 21 report builder · 22 status page | containers |
| **Quality** | 23 eval suite · 44 claim checker | 23 → your laptop or a cron job · 44 → container |
| **The agent** | 24 chat agent (local model, or `CHAT_PROVIDER=hosted` with local fallback) | n8n |
| **Agent tools** | 25 blog · 26 social · 27 ads · 28 email · 29 SEO brief · 30 repurpose · 31 research URL · 32 keywords · 33 calendar · 34 knowledge answer · 35 quality gate · 57 content formats (video script, landing page, email sequence) | n8n (sub-workflows) |
| **Autonomous** | 36 trend digest · 37 competitor watch · 39 publisher · 40 content planner · 41 weekly report · 59 winner recycler (weekly: re-drafts the most-clicked posts, capped) | n8n (schedules) |
| **People** | 38 approval form · 42 knowledge-base form · 51 rules review form | n8n (forms) |
| **Campaigns** | 45 campaign service · 47 plan-campaign tool · 48 campaign drafter · 52 daily measurement · 53 campaigns tool | 45 → container · rest → n8n |
| **Publishing & analytics** | 54 Postiz bridge (dry run by default) · 55 Umami sync · 56 daily analytics sync | 54, 55 → containers · 56 → n8n |
| **Reviews & proof** | 58 review hub: reviews inbox, reply context (health/legal → a person), testimonials kept verbatim with consent · 60 review replies (daily: reply drafts for new reviews; a person posts them, nothing is posted automatically) | 58 → container · 60 → n8n (schedule) |
| **Volume** | 61 content engine: one pillar → atoms → a month of planned slots (atoms × hook × format × channel, capped per channel and per atom), a near-duplicate check (5-grams, opening, embeddings) and a stop rule that pauses the pillar when too many drafts are rejected · 64 plan-a-month chat tool · 65 daily drafter (writes the next week's slots, retries duplicates with another hook, gate, images) | 61 → container · 64, 65 → n8n |
| **Publishing** | 62 CMS bridge (blog → WordPress/Ghost drafts) · 63 Listmonk bridge (newsletter drafts; a person presses send) · 66 weekly newsletter · 39 routes blog → CMS, social → Postiz | 62, 63 → containers · 66 → n8n |
| **Customer language & video** | 70 customer-language engine: mines customers' own words from reviews, searches and support (every quote verbatim with its source) → themes, headline bank, grounded personas · 71 video assembly: script → 1080×1920 MP4 with on-screen text, captions and optional local voice (Piper) | containers |
| **SEO & refresh** | 67 Search Console sync · 68 content refresh plans for pages losing clicks · 69 SEO briefs for queries ranking 5–20 | 67 → container · 68, 69 → n8n |
| **Learning** | 46 learning service · 49 revise rejected drafts · 50 weekly rule proposals | 46 → container · rest → n8n |
| **Control room & clips** | 72 control room: mobile web app to review (swipe, undo), see the calendar, previews, performance and engine status; same decisions as form 38 · 73 clip finder: long video (direct file or upload) → short vertical clips with word-by-word captions, transcribed locally · 77 chat tool `clip_video`: clips → captioned `video` items for approval | containers · 77 → n8n |
| **Inbound & competitors** | 79 site assistant: website chat that answers only from your knowledge base and facts (claim-checked), says it's an AI, qualifies, offers your booking link, hands off to a person · 80 lead hub: consented inbound leads → enrichment from their own website → score with reasons → HubSpot/Pipedrive (dry run) → first reply for you to approve · 78 ad library + competitor registry (official Meta API for EU ads, links elsewhere) · 81 `track_competitor` chat tool · 77 `clip_video` chat tool (clips → approval) | 78–80 → containers · 77, 81 → n8n |
| **AI visibility (GEO)** | 82 ai-visibility: a versioned, approved set of buyer questions asked to AI assistants through their official APIs (OpenAI web search, Perplexity, Groq model knowledge; Gemini only if you accept its terms), 3 answers each; is the brand named, cited, where in the list, share of voice vs the 78 competitors, and sentences about the brand checked by 44 · 83 weekly run: numbers to your notifications, gaps and wrong claims as calendar ideas (`visibility_gap`, never published) | 82 → container · 83 → n8n |
| **Paid ads** | 84 ads-sync: spend, conversions, CPL, ROAS, CTR, CPC per platform and campaign from the Meta Marketing API and the Google Ads API (read-only: it cannot change a budget or an ad), mapped to your campaigns (45), monthly budgets with pacing (linear or weekday-weighted) and alerts (overspend, underspend, CPL above / ROAS below target, spend with 0 conversions); a daily sync + alerts workflow ships in `84-ads-sync/n8n/`, the weekly report (41) gets an Ads section and the control room (72) an ads panel | container (its workflow → n8n) |
| **Client report** | 85 monthly report (1st of the month) for a client or whoever you report to: last month vs the month before from the sources you have installed (20, 55, 67, 45, 84), "what we did" and "what changed" built in code, and a short summary by the model that is checked in code (sentences with a number not in the data, or an unhedged cause, are dropped). It waits for approval as a `client_report` item and is never sent to the client: a person forwards it | n8n (schedule) |
| **Email flows** | 86 flow-runner: triggered lifecycle emails (welcome on `subscribed`, onboarding on `trial_started`, win-back on `inactive`, or your own). Only contacts with recorded consent enter; `unsubscribed` stops every flow forever; exit events (e.g. `purchased`) are checked right before each send. A person approves each flow version once (the whole sequence is one `email_flow` item in the control room); an edit needs approval again. A random 15% holdout gets nothing, and `/results` compares clicks, purchases and unsubscribes between the arms with a 95% interval (never opens); an A/A mode is the check that the tracking works. Daily cap and kill switch. **Always a dry run in this stack**: sends go to an outbox; the real send path is a stub | container (its 15-minute workflow → n8n) |
| **Product feed** | 87 feed-optimizer: upload your Google Merchant Center feed file (CSV/TSV); the model proposes a title per product (brand, product, colour/size/material first), checked in code against that product's own row (an invented number, colour, material, size, claim or other brand is rejected and a rule-based title used instead); a person approves with the approver key; the export is your file with only the approved titles/descriptions changed, or a supplemental feed. Nothing is uploaded to Merchant Center | container (full profile) |
| **Any chatbot, facts you can trust** | 88 task-bridge + scoped facts in 05: pick a task, copy a pack into free ChatGPT/Claude/Gemini (you see exactly what leaves the business; internal values go only as placeholders), paste the answer back; every sentence is checked against facts with scope and dates (wrong branch, plan, variant or channel; expired or not yet valid on the publish date; missing disclosures; certifications and ratings with no source); approval binds to the exact text; export when approved. Works with no model at all | container (core) |
| **Approve without n8n** | 90 approval-service (core, used when `install.sh --approval service`): applies the control room's approve / edit / reject / back-to-draft decisions to the calendar, bound to the text the reviewer saw, with the reviewer's full name in the audit. Holds the approver key; only the control room may call it. Publishing (39) and automatic rewrites (49) still use n8n | container (core) |
| **Claude connector** | 89 mcp-connector (profile `claude`): Claude uses your agent directly (public facts, packs, submit answers, checks) over MCP (stdio or HTTP with a token). It can't approve, confirm facts or publish: a person approves in the control room | container (optional) |
| **Experiments** | the agent proposes one-variable A/B tests weekly (74), a person approves them (76), 61 gives the two versions to planned slots balanced by weekday and hour, 45 decides at weekly looks in code (HDI + ROPE on clicks per post within 72 h; 75); a winner becomes a provisional rule in 46 that writers only get after a later experiment agrees and a person approves it (51) | 45, 46, 61 → containers · 74–76 → n8n |
| **Safety net** | 43 error handler | n8n |

Every folder has its own `README.md` with a **Where to deploy** section, its config, and how to
test it. `BLUEPRINT.md` has the architecture and every API contract.

## Deploy order

**Installing it: [01-marketing-stack/INSTALL.md](01-marketing-stack/INSTALL.md)**, a checklist for
one person (prerequisites, profiles with their memory and disk, first login, brand, publishing,
HTTPS, backups, updates, uninstall). The installer does steps 4 and 5 below in one command.

```text
1. Only if you want one repo per deploy: push each folder to its own GitHub repo
   (same name as the folder). With this repo cloned as-is, skip steps 1 and 3.
2. On the machine with Ollama:        02-ollama-models  → ./scripts/setup.sh
3. On the Docker host:                clone all repos side by side
                                      (01-marketing-stack/scripts/clone-all.sh <github-user>)
4-5. 01-marketing-stack:              ./scripts/install.sh --profile core   (or growth / full)
                                      generates .env and its secrets, preflight, build, start,
                                      n8n owner, import workflows, smoke test; prints the URLs
6. Teach it your brand:               control room → More → Brand setup (on a computer:
                                      Brand in the top bar), or edit
                                      05-brand-service/config/brand.yaml)
                                      add FAQs/product facts in the knowledge form (42)
7. Chat:                              n8n → "24 · Marketing chat agent" → Open chat
                                      "Plan a campaign for …" → review drafts in the control room
8. Run the pilot:                     PILOT.md (daily 10-minute review, turning publishing on)
```

To push one folder as a repo (repeat per folder, or script it):

```bash
cd 07-page-extractor
git init -b main && git add . && git commit -m "page-extractor"
gh repo create <you>/07-page-extractor --private --source=. --push
```

## Tested at the first handover (deploys 01–44)

This table is the record of the first handover, on an M-series Mac with 16 GB and
`qwen2.5:7b`, with the services run without Docker. Since then the whole stack has been built
and run in Docker, and installed from scratch with `install.sh` per profile:
[01-marketing-stack/DOCKER-RUN.md](01-marketing-stack/DOCKER-RUN.md) has those runs, timings,
memory and disk. Each deploy's README has its own current test results.

| What | Result |
|---|---|
| Unit tests across 21 deploys | 370 passed |
| 20 workflows import and publish in n8n 2.40.5 | 20 / 20 |
| Each tool workflow (25–35) run end to end through n8n with the real model | all 11 work |
| Chat agent: KB question, social posts, calendar list, "approve item 2" (refused), keyword research | all behave |
| Approval form → publisher → short link with UTM → marked published | works |
| Scheduled workflows fired on demand: digest, competitor watch, planner, weekly report | all delivered |
| Weekly report numbers vs a hand calculation from the raw CSV | identical |
| Error handler posts failures to the webhook | works |
| Live eval (23), 10 cases × 3 runs on qwen2.5:7b | 26 / 30 = 87% (80% before the fixes found during testing) |
| Claim checker (44) on 113 labelled claims | caught 53 / 55 invented · flagged 7 / 58 true |
| Claim checker on held-out real-post sentences | caught 5 / 6 invented · flagged 0 / 9 fine sentences |

## Eval results (qwen2.5:7b, 3 runs per case)

| Case | Passed | What failed |
|---|---|---|
| ad copy within Google limits | 2/3 | a "3" not in the input |
| social posts: channels, limits, link | 2/3 | link missing from 2 posts (workflow 26 re-adds it itself; this case tests the raw prompt) |
| social posts: no invented stats | 3/3 | |
| email subject/preheader lengths | 3/3 | |
| blog structure, no invented numbers | 2/3 | a "10" not in the input |
| rewrite removes banned claims | 3/3 | (0/6 before the prompt changes) |
| KB answer refuses when it doesn't know | 3/3 | |
| KB answer cites its source | 2/3 | answer left out the "30 days" |
| keyword clusters use only real keywords | 3/3 | |
| competitor summary, no invented prices | 3/3 | |

With 30 runs, the uncertainty on 87% is roughly ±12 points. Treat it as "most outputs
pass", not as a precise score. Rerun with `--repeats 10` before comparing models.

## Why it is built this way (from research on other projects)

Before phase 2 I reviewed 13 open-source marketing agents, about 10 n8n marketing
templates, and the self-hostable marketing tools.

- **No open-source tool I found holds a campaign with goals and targets, or learns from a
  reviewer's edits.** Those are built here (45, 46). The rule-learning follows
  langchain-ai/social-media-agent's reflection step, and the "set targets before
  shipping, report what was never measured" idea comes from the Kai CMO harness.
- **Publishing, analytics and email are better integrated than built:** Postiz (social
  publishing, ~19 networks), Umami (analytics API) and Listmonk (email). They are the
  recommended next step. Our publisher webhook (39), link tracking and CSV import work today.
- **Small local models get worse at picking tools as tools are added.** The chat agent was
  measured before and after adding the 2 campaign tools (23 `evalsuite.tools`): the right
  tool was chosen 72 of 72 times with 13 tools, after one tool description was sharpened.

## Known limitations (observed, not guessed)

- **Invented product details** ("notes of chocolate and caramel" for the decaf) are now
  caught by the claim checker (44) in most cases: 53 of 55 in testing. It isn't perfect.
  It misses about 1 in 25, and flags about 1 in 8 true sentences, which then get
  reworded or sent to you as drafts. Keep `facts:` in `05-brand-service/config/brand.yaml`
  complete; the checker only knows what's written there.
- **Invented numbers** are checked exactly in code by the claim checker: every number in a
  draft must appear in the facts or the brief.
- **Automatic fixes don't always succeed.** When the quality gate's rewrite doesn't fix a
  problem (about 1 in 6 on the hardest test), the item is saved as `draft` with "needs a
  human" instead of going to review. It isn't hidden.
- **Awkward wording in tight formats.** Ad headlines squeezed into 30 characters can read
  badly ("Boost your workday! Free first").
- **Reddit** blocks unauthenticated requests, so social listening (11) mostly returns
  Hacker News results.
- **Publishing**: on the core profile, social posts go to one webhook (`PUBLISH_WEBHOOK_URL`),
  which has no dry run. The growth profile adds the Postiz bridge (54, social) and the CMS
  bridge (62, blog), both in dry run until you turn them on (INSTALL.md, section 6).
