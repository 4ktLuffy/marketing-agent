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
| `24`–`43`, `47`–`53`, `56`, `57`, `59`, `60` | n8n workflows: chat agent, its tools, schedules, forms |
| `54`, `55`, `58` | Publishing bridge to Postiz (dry run by default), Umami analytics sync, review hub (reviews, verbatim testimonials) |

## Quick start

```bash
./02-ollama-models/scripts/setup.sh           # local models
cd 01-marketing-stack
cp .env.example .env                           # fill the 3 secrets
./scripts/preflight.sh                         # fix every FAIL
docker compose up -d --build
./scripts/import-n8n.sh                        # after creating the n8n owner account
./scripts/smoke-test.sh
```

Replace the example brand (*Northwind Roasters*, a fictional coffee subscription) in
`05-brand-service/config/brand.yaml` before real use.

## CI

`.github/workflows/ci.yml` runs every deploy's tests, validates all n8n workflows and the
compose file, and shellchecks the scripts.
