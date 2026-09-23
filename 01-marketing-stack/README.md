# marketing-stack

Deploy **1 of 53** of the local-LLM marketing agent. This repo holds the one
`docker compose` file that runs n8n, Postgres and all 20 services on one private network,
plus the scripts that load the n8n workflows. Ollama runs next to it on the host.

```
             you ──► n8n chat (24) ──► mkt-agent (Ollama, host)
                         │ tools
                         ▼
       sub-workflows 25–35 ──► llm-gateway (03) ──► mkt-writer (Ollama)
                         │          └─ prompts (04), brand (05)
                         ▼
   services 05–22 (extract, SEO, rules, UTM, calendar, analytics, …)
                         ▲
   schedules 36–41 ──────┘       forms 38, 42      errors → 43
```

## Where to deploy

**One Docker host**: your Mac (Docker Desktop or OrbStack) or a Linux server/VPS.
Every other deploy is either built by this compose file (services 03, 05–22), mounted
into it (04 prompts, 05 brand config, 08 feeds), or imported into its n8n
(all `NN-wf-*` workflow deploys).

## Folder layout it expects

Clone all repos side by side. The compose file builds `../03-llm-gateway` and so on.

```
marketing-agent/
  01-marketing-stack/      ← you are here
  02-ollama-models/
  03-llm-gateway/
  ...
  43-wf-error-handler/
```

```bash
mkdir marketing-agent && cd marketing-agent
git clone https://github.com/<you>/01-marketing-stack.git
./01-marketing-stack/scripts/clone-all.sh <you>          # clones 02–43
```

## Run, step by step

```bash
# 1. Models (on the machine running Ollama)
./02-ollama-models/scripts/setup.sh
python3 02-ollama-models/scripts/smoke_test.py

# 2. Secrets
cd 01-marketing-stack
cp .env.example .env
#    fill POSTGRES_PASSWORD, N8N_ENCRYPTION_KEY, INTERNAL_API_KEY  (openssl rand -hex 24)

# 3. Check before starting (changes nothing; fix every FAIL)
./scripts/preflight.sh

# 4. Start everything
docker compose up -d --build
docker compose ps                      # all healthy after ~30 s
open http://localhost:8122             # status page: every service green?

# 5. Load the workflows into n8n
open http://localhost:5678             # create the owner account once
./scripts/import-n8n.sh                # imports credentials + every workflow deploy, activates schedules

# 6. Check the running system end to end (services, n8n, workflows, one LLM call, one fact check)
./scripts/smoke-test.sh

# 7. Talk to it
#    n8n → "24 · Marketing chat agent" → Open chat
#    "Write an X and LinkedIn post about our new decaf, link https://…"
```

## Ports (bound to 127.0.0.1 only)

| Port | What |
|---|---|
| 5678 | n8n |
| 8103–8122 | service NN on port 81NN, for debugging with curl |

Nothing is exposed publicly. For a public short-link domain (16) or public n8n forms,
put a reverse proxy (Caddy, Traefik) or a tunnel (Cloudflare Tunnel) in front of those
two ports only.

## Configuration (`.env`)

| Var | Required | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | yes | n8n database |
| `N8N_ENCRYPTION_KEY` | yes | encrypts n8n credentials. **Never change it after first start** |
| `INTERNAL_API_KEY` | yes | `X-API-Key` for write endpoints of 06, 09, 16, 19, 20 |
| `N8N_PUBLIC_URL` | | public n8n URL (webhooks, form links) |
| `OLLAMA_URL` | | default `http://host.docker.internal:11434` |
| `AGENT_MODEL` / `WRITER_MODEL` / `EMBED_MODEL` | | from 02 |
| `SHORT_LINK_BASE_URL` | | public base of 16, e.g. `https://go.yourbrand.com` |
| `LISTENING_QUERY` | | what 36 searches for on Hacker News/Reddit |
| `PUBLISH_WEBHOOK_URL` | | where 39 sends approved, due posts (Zapier/Make/Buffer hook or your own). Empty = nothing is published or marked published |
| `NOTIFY_WEBHOOK_URL` | | Slack/Discord/Teams incoming webhook: morning digest (36), competitor changes (37), content plan (40), weekly report (41), errors (43) |
| `REPORT_EMAIL_TO` / `REPORT_EMAIL_FROM` | | 41 emails the weekly report here (needs an SMTP credential in n8n) |
| `PLAN_CHANNELS` / `PLAN_POSTS_PER_CHANNEL` / `PLAN_THEMES` | | what the weekly planner (40) plans |

**Linux with Ollama on the host:** Ollama listens on 127.0.0.1 by default, which containers
can't reach. Start it with `OLLAMA_HOST=0.0.0.0` (for example in its systemd unit).
`preflight.sh` warns about this.

Linux without Ollama on the host: `docker compose --profile ollama up -d` and set
`OLLAMA_URL=http://ollama:11434`, then run the 02 setup against it:
`OLLAMA_HOST=http://localhost:11434 ./02-ollama-models/scripts/setup.sh`.

## Updating one piece

Each service is its own repo, so to update one:

```bash
cd ../07-page-extractor && git pull
cd ../01-marketing-stack && docker compose up -d --build page-extractor
```

Prompt changes (04) and brand changes (05 `config/brand.yaml`) are mounted: a
`git pull` there takes effect on the next call without a restart.

## CI

Validates `docker-compose.yml` with `docker compose config` and shellchecks the scripts (`preflight.sh`, `smoke-test.sh`, `import-n8n.sh`, `clone-all.sh`).
