# Install checklist (one person, one afternoon)

Everything below runs on one machine you control. Nothing is posted, emailed or sent to a CRM
until you turn it on, and every post still waits for a person to approve it.

Sections 1–5 are the install (about an hour, most of it downloads). After that,
[PILOT.md](../PILOT.md) is the day-by-day plan. Sections 6–12 are for when you need them.

Words used below: a **profile** is how much of the system you install; a **bridge** is the small
service that hands a post to another tool (Postiz, WordPress, Listmonk, a CRM); **dry run** means
the bridge shows what it *would* send (`would_send`) and sends nothing.

## 1. Pick a profile

| Profile | What you get | Containers | RAM (idle, measured) | Images on disk |
|---|---|---|---|---|
| **core** | The agent: chat (24) with its writing tools, knowledge base, research, calendar, fact checker (44), quality gate, approval form (38), publisher to one webhook (39), campaigns (45), learning from your edits (46), weekly plan and report, monthly client report for approval (85), status page, control room (72) | 26 | 1.35 GB | ≈ 2.7 GB |
| **growth** | core + content engine (61, a month from one pillar), Postiz / WordPress-Ghost / Listmonk bridges (54, 62, 63), Umami and Search Console sync (55, 67), experiments (74–76), reviews (58, 60), customer language (70), video (71), clips (73), paid-ads reporting (84, read-only) and lifecycle email flows with a holdout (86, dry run only) | 38 | 1.69 GB (before 84, 86) | ≈ 6.3 GB |
| **full** | growth + competitor ads (78), website assistant (79) with its optional hosted gateway, lead hub (80), AI visibility (82, 83), product feed titles for Merchant Center (87) | 44 | 1.95 GB (40 measured) | ≈ 6.4 GB |

Start with **core**. Moving up or down later is one command (section 9). Numbers are from
`DOCKER-RUN.md` (colima on an M-series Mac). Ollama runs next to the stack and needs its own
memory: about 5 GB while a 7B model is loaded.

## 2. Prerequisites

- [ ] A machine with **16 GB RAM** (8 GB works with a 3B model; tool choice gets worse), and
      **15 GB free disk** for core, **25 GB** for growth/full (images, build space, models).
- [ ] **Docker** with Compose v2: Docker Desktop, OrbStack, colima (give its VM ≥ 4 GB for core,
      8 GB for growth/full, as tested) or docker-ce on Linux. `docker compose version` must work.
- [ ] `git`, `curl`, `openssl`, `python3` (all present on macOS and most Linux servers).
- [ ] The code, all folders side by side: `git clone` this repo, or clone `01-marketing-stack` and
      run `./01-marketing-stack/scripts/clone-all.sh <github-user>`. The commands below run from
      the top folder (the one that holds `01-marketing-stack`).
- [ ] **Ollama** ([ollama.com/download](https://ollama.com/download)), installed and running, on
      this machine (Mac: the native app, so it uses the GPU) or on another one you can reach
      (then set `OLLAMA_URL` in `.env`, see `02-ollama-models/README.md`). Then the models:

      ```bash
      ./02-ollama-models/scripts/setup.sh          # mkt-agent, mkt-writer, qwen3-embedding (≈ 5.5 GB)
      python3 02-ollama-models/scripts/smoke_test.py   # three PASS lines
      ```

      Linux with Ollama on the host: start it with `OLLAMA_HOST=0.0.0.0` (containers can't reach
      127.0.0.1).

## 3. Install

```bash
cd 01-marketing-stack
./scripts/install.sh                    # asks: profile, public n8n URL
```

Or without questions: `./scripts/install.sh --profile core --yes`. Options: `--url
https://n8n.example.com/` (public n8n address for form links and webhooks), `--owner-email
you@example.com` (n8n login), `--context <docker-context>` (or `DOCKER_CONTEXT=...`).

What it does, in order (about 2.5 minutes with images already built; a first build adds about
4 minutes for core, image downloads included, and about 10 more for growth, mostly the video and
clip images):

1. Creates `.env` from `.env.example` with a generated value for every secret (mode 0600, ignored
   by git). An existing `.env` is kept; only placeholders are filled.
2. Runs `scripts/preflight.sh` and stops on any FAIL (Docker, `.env`, folders, Ollama and the
   models, compose file, ports).
3. Builds and starts the profile, and waits until every container is healthy.
4. Creates the n8n owner account (password generated into `.env`).
5. Imports the workflows of the profile (`scripts/import-n8n.sh`) and runs `scripts/smoke-test.sh`
   (containers, status page, n8n, workflows, access control, one model call, one fact check).
6. Prints the addresses.

It is safe to run again at any time: after changing `.env`, after `git pull`, or to change profile.

## 4. First login

Every login is in `01-marketing-stack/.env`. Open the file to read them; nothing prints them.

| Where | Address | Login (keys in `.env`) |
|---|---|---|
| Control room: review queue, calendar, stats, brand setup | <http://localhost:8172> | `CONTROL_USER` / `CONTROL_PASSWORD` |
| n8n: editor and the chat agent (**24 · Marketing chat agent → Open chat**) | <http://localhost:5678> | `N8N_OWNER_EMAIL` / `N8N_OWNER_PASSWORD` |
| Approval form (38), knowledge form (42), rules form (51) | `<n8n>/form/mkt-content-approval`, `…/mkt-knowledge-add`, `…/mkt-rules-review` | `FORMS_USER` / `FORMS_PASSWORD` |
| Status page (every service green?) | <http://localhost:8122> | none (localhost only) |

- [ ] Status page all green, control room opens, n8n chat answers "What can you do?".

**The install is done.** Do section 5 today, then follow [PILOT.md](../PILOT.md).

## 5. Teach it your brand

The example brand (*Northwind Roasters*) must go before real use. Do it on install day: the
scheduled workflows start the next morning (trend digest daily 08:00, content plan Monday 07:00,
UTC unless you set `TZ` in `.env`) and draft with whatever brand is set. Preflight warns while
`brand.yaml` still holds the example, even after you replaced it in Brand setup.

- [ ] **Brand**: control room → More → **Brand setup** (on a computer: **Brand** in the top bar):
      basics, products, facts, rules, voice, review. Or edit `05-brand-service/config/brand.yaml`
      by hand. Saved edits live in the `brand_data` volume on top of `brand.yaml`.
- [ ] **Facts**: 10–20 true, checkable statements. The fact checker (44) lets a draft claim only
      what is in the facts or the brief, so missing facts mean more drafts sent back to you.
- [ ] **Voice interview**: Brand setup step 5 (ten questions → a voice profile you check before
      saving). By hand: `05-brand-service/README.md`, *Brand voice from an interview*.
- [ ] **Knowledge**: FAQs, product pages, policies through the knowledge form (42), one page or
      URL at a time.
- [ ] First test: in the chat, "Write a LinkedIn post about <product>", then approve or reject it
      in the control room. Rejections with a reason are rewritten automatically.

## 6. Turn on publishing (only after checking a dry run)

Approved posts go out every 15 minutes through the publisher (39). Until you set a destination,
nothing is sent. Every bridge starts in dry run (`POSTIZ_DRY_RUN`, `CMS_DRY_RUN`,
`NEWSLETTER_DRY_RUN`, `LEADS_DRY_RUN` = `true` in `.env`) and answers with `would_send` instead.
After changing `.env`, re-run `install.sh` (a plain `docker compose restart` does not read `.env`).

- [ ] **core**: set `PUBLISH_WEBHOOK_URL` (Zapier, Make, Buffer or your own hook; optional
      `PUBLISH_WEBHOOK_KEY`) in `.env`, re-run `install.sh`. This webhook has **no dry run**:
      whatever it points at gets every approved post. Point it first at a hook that only logs
      (e.g. a Zapier/Make test step), and approve one post.
- [ ] **growth, social via Postiz (54)**: `POSTIZ_URL`, `POSTIZ_API_KEY`, `POSTIZ_CHANNEL_MAP`
      (checked against its `GET /integrations`), `PUBLISH_WEBHOOK_URL=http://postiz-bridge:8000/publish` and `PUBLISH_WEBHOOK_KEY` = your
      `INTERNAL_API_KEY`.
      Re-run `install.sh`, approve one post, read `would_send`. Only then `POSTIZ_DRY_RUN=false`.
- [ ] **growth, blog → WordPress/Ghost (62)**: `CMS`, `WP_*` or `GHOST_*`; check `would_send`,
      then `CMS_DRY_RUN=false`. Posts arrive as CMS drafts.
- [ ] **growth, newsletter → Listmonk (63)**: `LISTMONK_*`; check, then `NEWSLETTER_DRY_RUN=false`.
      Campaigns arrive as drafts; a person presses send. No Listmonk yet: `--profile newsletter`
      runs one (`docker compose --profile growth --profile newsletter up -d`).
- [ ] **full, CRM (80)**: `LEADS_CRM`, token; `LEADS_DRY_RUN=false` last.

Each bridge's README (54, 62, 63, 80) has the exact check. `NOTIFY_WEBHOOK_URL` (Slack, Discord,
Teams) or `NOTIFY_FORMAT=telegram` sends you the digests, reports and errors.

## 7. Optional: hosted models

Everything runs on your machine by default. A hosted OpenAI-compatible model (e.g. a free Groq
key) makes some steps faster or stricter; the text of those calls then goes to that provider.

- [ ] Stricter fact checking: `VERIFIER_PROVIDER=openai`, `VERIFIER_MODEL=openai/gpt-oss-20b`,
      `OPENAI_BASE_URL`, `OPENAI_API_KEY` (uncomment them in `.env`; measured on 113 labelled
      claims: 54/55 invented claims caught and 2/58 true ones flagged, vs 53/55 and 7/58 on the
      local model).
- [ ] Faster chat: `CHAT_PROVIDER=hosted`, `CHAT_API_KEY` (local model stays as fallback).
- [ ] **Fast site assistant (full profile).** On a laptop model a sourced website answer takes
      45–75 s; visitors leave. Set `ASSISTANT_OPENAI_API_KEY` and
      `ASSISTANT_GATEWAY_URL=http://llm-gateway-assistant:8000` (answers on Groq
      `openai/gpt-oss-120b`), plus the hosted fact checking above with
      `VERIFIER_MODEL=openai/gpt-oss-20b`. Measured on the 42 assistant eval messages: median
      0.9 s, sourced answers 1.4–11 s, 0 invented facts. Every visitor message and the excerpts
      used to answer it then go to the provider; each answered question costs about 1–2k
      tokens. `ASSISTANT_ANSWER_TIMEOUT_S` hands slower answers to a person; empty (the default)
      means 20 s with the hosted gateway, 120 s with everything local.
- [ ] Re-run `install.sh`. Preflight checks `OPENAI_API_KEY` against the provider without
      printing it; the chat key is only used by n8n.

## 8. Phone access (HTTPS)

Every port is bound to 127.0.0.1. To review from a phone, put a reverse proxy with HTTPS in front
of the control room (and the n8n forms, if you use them), or reach the machine over Tailscale or
WireGuard. Caddy example:

```caddy
review.example.com {
    reverse_proxy 127.0.0.1:8172
}
n8n.example.com {
    reverse_proxy 127.0.0.1:5678
}
```

- [ ] `.env`: `CONTROL_COOKIE_SECURE=true`; `N8N_PUBLIC_URL=https://n8n.example.com/` (or
      `install.sh --url https://n8n.example.com/`); `CARDS_PUBLIC_URL` / `VIDEO_PUBLIC_URL` /
      `CLIPS_PUBLIC_URL` to addresses the reviewer's browser can reach, if n8n forms show media.
- [ ] `.env`: `CONTROL_TRUSTED_PROXIES=172.16.0.0/12` (the address the control room sees for the
      proxy: Docker's gateway), then re-run `install.sh`. Without it, every login attempt counts
      as the proxy's, so a stranger's wrong passwords lock you out too.
- [ ] Never expose `/admin` of 79 or any other port. Details: `72-control-room/README.md`,
      `79-site-assistant/README.md` (the website chat: publish only its public paths) and
      `16-link-shortener` (short links must be public).

## 9. Change profile, update

```bash
./scripts/install.sh --profile growth     # up: builds and starts the extra services, imports their workflows
./scripts/install.sh --profile core       # down: removes the extra containers (their data volumes stay)
                                          #       and unpublishes their workflows
git pull && ./scripts/install.sh          # update (per-repo layout: git pull in each folder first)
```

The profile lives in `.env` as `COMPOSE_PROFILES`, in a block `install.sh` rewrites on every run,
together with the empty URLs of the services the profile doesn't install (so the workflows that
use them skip that step) and the status page's service list. Change the profile with `install.sh`,
not by editing that block. Plain `docker compose ps`, `logs` and `restart` use it automatically.

## 10. Backups

What to keep: `01-marketing-stack/.env` (**N8N_ENCRYPTION_KEY** above all: without it n8n's saved
credentials are unreadable), `05-brand-service/config/`, `01-marketing-stack/secrets/`, and the
Docker volumes `marketing-agent_*` (n8n's database `pg_data`, calendar, campaigns, learning, brand
edits, knowledge base, links, …).

```bash
mkdir -p ~/mkt-backup
for v in $(docker volume ls -q --filter label=com.docker.compose.project=marketing-agent); do
  docker run --rm -v "$v":/v:ro -v ~/mkt-backup:/b alpine tar czf "/b/$v.tgz" -C /v .
done
```

Stop the stack first (`docker compose stop`) for a consistent Postgres copy, or dump it live:
`docker compose exec -T postgres pg_dump -U n8n n8n > ~/mkt-backup/n8n.sql`. Restore = create
the volume, untar into it, start.

## 11. Uninstall

In `01-marketing-stack`:

```bash
docker compose --profile full --profile newsletter down        # stop and remove containers, keep data
docker compose --profile full --profile newsletter down -v     # also DELETE every volume (all data)
docker image ls 'marketing-agent-*'                            # then remove the images if you want the disk back
```

One client of several (section 12): `./scripts/uninstall.sh --client <slug>` (asks you to type the
name; removes only that client's containers, volumes, network, `.env.<slug>` and `clients/<slug>/`).

## 12. Several clients on one host (agency mode)

One install per client, side by side on one Docker host (`docs/agency-mode.md`, option A). Each
client is its own compose project: its own containers, network, volumes, keys, n8n, control
room and status page. Only the host's Ollama and the built images are shared (neither holds
client data).

```bash
./scripts/install.sh --client acme --profile core --yes       # first client: ports 82xx, n8n 8299
./scripts/install.sh --client globex --profile growth --yes   # next free block: 83xx, n8n 8399
./scripts/install.sh --client acme                            # re-run / update one client
./scripts/uninstall.sh --client acme                          # offboard: containers, volumes, env, files
./scripts/leak-test.sh --context <docker-context>             # prove isolation with two test clients
```

What `--client <slug>` sets up (slug: a–z first, then a–z, 0–9 or `-`; it is the compose project):

| Per client | Where |
|---|---|
| Env file with its own generated secrets (`INTERNAL_API_KEY`, `APPROVER_KEY`, `CONTROL_ROOM_KEY`, forms and control-room passwords, n8n owner), mode 0600, never printed | `01-marketing-stack/.env.<slug>` |
| Port block: every `127.0.0.1:81NN` becomes `<prefix>NN` (82..99, the next free one, or `--port-prefix`); n8n on `<prefix>99` | `PORT_PREFIX`, `N8N_PORT` in the env file |
| n8n public URL, card/short-link/video/clip URLs on the client's ports | `N8N_PUBLIC_URL`, `CARDS_PUBLIC_URL`, … |
| Schedules moved by 10 minutes per block (82 → +10, 83 → +20, …, 87 → +0), so N Monday planners don't queue on one Ollama at 07:00. Only the copy imported into that client's n8n moves (`scripts/shift-cron.py`); the workflow files stay as they are | `SCHEDULE_OFFSET_MIN` |
| Its own `brand.yaml` base, RSS feed list and `secrets/` (e.g. `gsc.json`), copied from the examples | `01-marketing-stack/clients/<slug>/` (ignored by git) |
| Network `<slug>_marketing`: `brand-service` etc. resolve only to that client's containers | compose |
| Volumes `<slug>_*` (backups: section 10 with `project=<slug>`) | compose |
| Only what a browser or the internet opens gets a host port: n8n, control room, status page, site assistant, short links, lead webhooks, card/video/clip media (and Listmonk, Ollama if installed). The other services publish nothing, because Docker Desktop and colima let every container reach 127.0.0.1 ports through `host.docker.internal`: with them published, one client's containers could call another client's services. To call one by hand: `docker compose -p acme --env-file .env.acme exec llm-gateway ...` | `COMPOSE_FILE=docker-compose.yml:docker-compose.private.yml` in `.env.<slug>` |

Addresses of a client: control room `http://localhost:<prefix>72`, n8n `http://localhost:<prefix>99`,
status page `http://localhost:<prefix>22`; `install.sh` prints them. Plain compose commands need
the project and file: `docker compose -p acme --env-file .env.acme ps` (or `MKT_CLIENT=acme` for
the scripts: `MKT_CLIENT=acme ./scripts/smoke-test.sh`).

- [ ] Set each client's brand in **its** control room (Brand setup), or edit
      `clients/<slug>/brand/brand.yaml`, never `05-brand-service/config/brand.yaml` (that is the
      single install's and the template for new clients).
- [ ] Memory: core ≈ 1.35 GB per client idle, plus Ollama once (about 5 GB with a 7B model).
      Measured with two core clients: `DOCKER-RUN.md`, "Two-client leak test".
- [ ] Run `./scripts/leak-test.sh --context <ctx>` after every change to `docker-compose.yml`,
      the install scripts, 03, 05, 19 or 44. It installs two throwaway clients, plants a canary
      fact in each and checks prompts, the claim checker, keys, DNS, ports and uninstall; exit 1 on
      any leak. It refuses to touch a client that already exists.
- [ ] Browser: two n8n instances on `localhost` share a cookie name, so logging in to one logs you
      out of the other in the same browser profile. Use one browser profile per client, or give
      each n8n its own host name behind your proxy.

## Troubleshooting

- `install.sh` stops at preflight: fix each FAIL it lists (most often Ollama not running or a
  model missing) and run it again.
- A service unhealthy: `docker compose logs <service>`; the status page names it.
- "Connection refused" on a 127.0.0.1 port while the container is healthy (seen on colima after
  a container was recreated): `install.sh` restarts such a service once by itself; by hand
  `docker compose stop <service>; sleep 5; docker compose start <service>`.
- Several Docker contexts: run everything with `DOCKER_CONTEXT=<name>` or `install.sh --context <name>`.
