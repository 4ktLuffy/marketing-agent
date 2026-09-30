# Local-LLM marketing agent

A marketing agent that runs on **your own model (Ollama)** and is orchestrated by **n8n**.
You chat with it, or it works on a schedule. It plans campaigns, writes posts, emails, ads
and SEO briefs, fact-checks every draft against your approved facts, learns from your
edits, and publishes only what you approve.

- **Start here:** [START-HERE.md](START-HERE.md) has what it does, the deploy order and test results.
- **Architecture and API contracts:** [BLUEPRINT.md](BLUEPRINT.md)
- **Build log:** [CHECKPOINT.md](CHECKPOINT.md) lists what was verified end to end and every bug found and fixed.

## Layout

Each numbered folder is a self-contained deploy with its own README (*Where to deploy*,
configuration, how to test). They also work as separate repos.

| Folders | What |
|---|---|
| `01`–`04` | Docker stack, Ollama models, LLM gateway, prompt library |
| `05`–`22`, `44`–`46` | Python services (FastAPI): brand, knowledge base, research, content tools, calendar, analytics, fact checker, campaigns, learning |
| `23` | Eval suite: writing quality, fact-checker accuracy, tool selection |
| `24`–`43`, `47`–`53`, `56`, `57`, `59`, `60`, `64`–`66`, `68`, `69`, `74`–`77`, `81`, `83`, `85` | n8n workflows: chat agent, its tools, schedules, forms, content engine, newsletter, refresh, SEO, experiments, clips, competitors, monthly client report |
| `54`, `55`, `58`, `61`–`63`, `67`, `70`, `71`, `73`, `78`–`80`, `82`, `84`, `86`, `87` | Postiz bridge, Umami sync, review hub, content engine, CMS bridge (WordPress/Ghost), Listmonk bridge, Search Console sync, customer-language engine, video assembly, clip finder, ad library + competitor registry, site assistant, lead hub, AI visibility (GEO) tracker, paid-ads reporting, lifecycle email flows with a holdout, product feed titles for Merchant Center (read-only or dry run; all bridges dry run by default) |
| `88` | Task bridge: a task pack for any chatbot (free ChatGPT, Claude, Gemini…) with a preview of what leaves the business, paste-back, evidence per sentence against scoped facts, approval bound to the exact text, ready-to-post export. No model calls needed |
| `89` | Claude connector (MCP, optional profile `claude`): Claude Desktop, Claude Code or one claude.ai custom connector can read public facts, make packs, submit answers and check text; it has no approve, confirm or publish tool and holds only the internal key |
| `90` | Approval service: applies the control room's decisions to the calendar without n8n (install.sh `--approval service`); holds the approver key, only the control room may call it; approvals stay bound to the exact text and name the reviewer |
| `72` | Control room: mobile web app to review, schedule, watch performance, and see what the agent is doing (Activity: running calls, timeline, models, abilities) |
| `tools/workflow-generator` | The Python that generates every n8n `workflow.json` (edit there, not in the JSON; CI checks they match) |

## Install

One person, one afternoon: [01-marketing-stack/INSTALL.md](01-marketing-stack/INSTALL.md) is the
checklist. The short version:

```bash
# Needs Docker and Ollama (https://ollama.com/download) installed and running.
./02-ollama-models/scripts/setup.sh                          # download the local models (about 5.5 GB)
./01-marketing-stack/scripts/install.sh --profile core       # secrets, build, start, n8n owner, workflows, final check
```

Profiles: **core** (the agent, 28 containers), **growth** (+ content engine, publishing bridges,
SEO/analytics sync, experiments, reviews, video, clips, paid-ads reporting, email flows; 40) and **full**
(+ competitor ads, site assistant, lead hub, AI visibility, product feed; 46). Re-running the installer is safe
and is also how you change profile or apply `.env` changes.

Installed? Replace the example brand (*Northwind Roasters*, a fictional coffee subscription) in
the control room's **Brand setup**, then follow [PILOT.md](PILOT.md) for the first weeks.

## CI

`.github/workflows/ci.yml` runs every deploy's tests, validates all n8n workflows, regenerates them
with `tools/workflow-generator` and fails if anything differs, validates the compose file for each
profile, and shellchecks the scripts.

## Licence

MIT, see [LICENSE](LICENSE). Each numbered deploy folder carries the same `LICENSE`, so it stays
licensed when pushed as its own repo. Vendored third-party files keep their own licences
(`72-control-room/app/static/vendor/`: htmx 0BSD, FullCalendar and Chart.js MIT).
