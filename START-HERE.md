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
yours in `05-brand-service/config/brand.yaml` and the knowledge-base form (42).

## The 53 deploys: each folder is one GitHub repo

| Group | Deploys | Where each one goes |
|---|---|---|
| **Platform** | 01 stack · 02 Ollama models · 03 LLM gateway · 04 prompt library | 01 → Docker host · 02 → the Ollama machine · 03 → container · 04 → mounted into 03 |
| **Brand & memory** | 05 brand service · 06 knowledge base | containers |
| **Research** | 07 page extractor · 08 RSS watcher · 09 change monitor · 10 keyword suggest · 11 social listening · 12 SEO auditor | containers |
| **Content tools** | 13 readability · 14 platform rules · 15 UTM builder · 16 link shortener · 17 image cards · 18 email renderer | containers (16 needs a public URL) |
| **Operations** | 19 content calendar · 20 analytics ingest · 21 report builder · 22 status page | containers |
| **Quality** | 23 eval suite · 44 claim checker | 23 → your laptop or a cron job · 44 → container |
| **The agent** | 24 chat agent | n8n |
| **Agent tools** | 25 blog · 26 social · 27 ads · 28 email · 29 SEO brief · 30 repurpose · 31 research URL · 32 keywords · 33 calendar · 34 knowledge answer · 35 quality gate | n8n (sub-workflows) |
| **Autonomous** | 36 trend digest · 37 competitor watch · 39 publisher · 40 content planner · 41 weekly report | n8n (schedules) |
| **People** | 38 approval form · 42 knowledge-base form · 51 rules review form | n8n (forms) |
| **Campaigns** | 45 campaign service · 47 plan-campaign tool · 48 campaign drafter · 52 daily measurement · 53 campaigns tool | 45 → container · rest → n8n |
| **Learning** | 46 learning service · 49 revise rejected drafts · 50 weekly rule proposals | 46 → container · rest → n8n |
| **Safety net** | 43 error handler | n8n |

Every folder has its own `README.md` with a **Where to deploy** section, its config, and how to
test it. `BLUEPRINT.md` has the architecture and every API contract.

## Deploy order

```text
1. Push each folder to its own GitHub repo (same name as the folder).
2. On the machine with Ollama:        02-ollama-models  → ./scripts/setup.sh
3. On the Docker host:                clone all repos side by side
                                      (01-marketing-stack/scripts/clone-all.sh <github-user>)
4. 01-marketing-stack:                cp .env.example .env  → fill the 3 secrets
                                      ./scripts/preflight.sh      (fix every FAIL)
                                      docker compose up -d --build
                                      open http://localhost:8122   (all green?)
5. n8n (http://localhost:5678):       create the owner account
                                      01-marketing-stack/scripts/import-n8n.sh
                                      01-marketing-stack/scripts/smoke-test.sh
6. Teach it your brand:               edit 05-brand-service/config/brand.yaml
                                      add FAQs/product facts in the knowledge form (42)
7. Chat:                              n8n → "24 · Marketing chat agent" → Open chat
                                      "Plan a campaign for …" → review drafts in the approval form
```

To push one folder as a repo (repeat per folder, or script it):

```bash
cd 07-page-extractor
git init -b main && git add . && git commit -m "page-extractor"
gh repo create <you>/07-page-extractor --private --source=. --push
```

## Tested before handover

Everything ran on an M-series Mac with 16 GB and `qwen2.5:7b`. Docker wasn't available
on the build machine, so the services ran with uvicorn and n8n 2.40.5 ran from npm.
**The Dockerfiles and the compose stack were not run.** `docker compose config` validates, and
every service image was simulated: each service was installed into a clean Python 3.12
environment from only its `requirements.txt` and started. All 22 answered `/health`.
`preflight.sh` and `smoke-test.sh` check the real Docker run on your machine.

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
- **Image cards (17)** work as a service, but no workflow calls them yet.
- **Publishing** goes to one webhook (`PUBLISH_WEBHOOK_URL`). To post natively, swap the
  *Publish* node in 39 for n8n's LinkedIn, X or Facebook node.
