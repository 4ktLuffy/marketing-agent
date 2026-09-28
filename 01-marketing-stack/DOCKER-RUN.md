# First full run in Docker (2026-09-27)

Until this run every service had only been tested as a local process. This is the first time
the whole stack (Postgres, n8n and 38 service containers = 40) was built and run by
`docker compose` and driven end to end inside Docker.

## Where

| | |
|---|---|
| Host | M-series Mac, Ollama 0.32.9 native on the Mac (`mkt-agent`, `mkt-writer`, `qwen3-embedding:0.6b`) |
| Docker | colima 0.10.3, profile `arm` (vz, aarch64, 6 CPU, 8 GB, virtiofs), Docker 29.5.2, Compose 5.5.0, **no buildx** (classic builder) |
| Context | `docker --context colima-arm …` / `DOCKER_CONTEXT=colima-arm docker compose …` |
| `.env` | fresh random test secrets (`openssl rand -hex 24` equivalent), every DRY_RUN on, no external keys |

## What was run

1. `.env` from `.env.example` (mode 0600, `git check-ignore` confirms it is ignored).
2. `scripts/preflight.sh`: all PASS (one WARN: example brand).
3. `docker compose build`: 36 light images, then 71, then 73.
4. `docker compose up -d`: 40 containers, all healthy after about 40 s.
5. n8n owner created with `POST /rest/owner/setup` (no UI step needed), then `scripts/import-n8n.sh`, then `scripts/smoke-test.sh`.
6. End-to-end in Docker, dry run, local model through host Ollama (below).

## Ollama from the containers (colima)

`host.docker.internal` resolves inside colima containers to 192.168.5.2 (the host), with or
without the compose file's `extra_hosts: host-gateway`, and the Mac's Ollama on 127.0.0.1:11434
answers there. `host.lima.internal` is the same address. **No change needed**: the default
`OLLAMA_URL=http://host.docker.internal:11434` works on colima as on Docker Desktop. README and
`.env.example` now say so.

## Fixes

| Deploy | What broke / was wrong | Fix |
|---|---|---|
| 01 `scripts/clone-all.sh` | Its repo list skipped 74, 75, 76 and 77, so a fresh clone lacked 4 workflows (and preflight, which reads the same list, didn't notice) | Added the four repos |
| 01 `scripts/smoke-test.sh` | Always FAILed after a correct import: expected `NN-wf-*` folders (42) but import-n8n.sh also imports `72-control-room/n8n` (43) | Expected count now uses the same globs as import-n8n.sh |
| 01 `scripts/smoke-test.sh` | "all containers running" passed for containers whose healthcheck failed | New check: every container with a healthcheck is `healthy` (negative control: `unhealthy`/`starting` lines are reported) |
| 01 `scripts/import-n8n.sh` | Printed `==> import n8n` for the control room's workflow | Prints the path (`72-control-room/n8n`) |
| 01 `docker-compose.yml` | n8n had no healthcheck, so `ps` couldn't show it healthy and smoke-test couldn't catch a broken n8n | `node -e fetch(/healthz)` healthcheck (node is always in the image), 60 s start period |
| 22 status page | Didn't poll 3 stack services: postiz-bridge, umami-sync, listmonk-bridge | Added to `DEFAULT_SERVICES` (35 → 38 checks) |
| 73 clip finder | The 486 MB whisper `model.bin` was stored twice: `ADD <url>` writes 0600, the following `RUN chmod` copies the file into a new layer. `ADD --chmod` needs BuildKit, which this Compose (no buildx) doesn't use | Download + SHA-256 check + chmod in one `RUN` (same pinned URLs and hash). Image 3.42 GB → 2.49 GB |
| 71 video assembly | Same double layer for the 63 MB piper voice | Same one-`RUN` download. 1.43 GB → 1.30 GB |
| 26 social writer (via `tools/workflow-generator/workflows.py`) | Asked for an X post, the local model answered with `"channel": "twitter"`; *Split posts* dropped it and the tool failed with "The model returned no posts for the requested channels" | The model's channel names go through the same aliases as the request (twitter → x, ig → instagram, …). Regenerated: changes 26 and 30 (shared code). Retried in Docker: X draft created |

No requirements or version pins needed changing: every pinned wheel exists for aarch64
(ctranslate2 4.8.2, onnxruntime 1.30.0, av 18.1.0, opencv-python 5.0.0.93, numpy, lxml 6.1.3,
PyJWT, markdown-it-py). No volume-permission problems: every image creates `/data` owned by uid
10001 before `USER appuser`, so fresh named volumes inherit it; a write test in all 19
volume-backed services and in n8n's volume passed.

## Results

| Check | Result |
|---|---|
| `preflight.sh` | all PASS |
| `docker compose ps` | **40 / 40 healthy** (postgres, n8n, 38 services) |
| Status page :8122 | 38 / 38 checks green, including Ollama |
| `smoke-test.sh` | 8 / 8 PASS (after the fixes above) |
| Chat agent (24), KB question | answered with the local model, 21 s ("not in the knowledge base yet", correct for an empty KB) |
| Chat → social writer (26), LinkedIn | draft `in_review` with an image card (17) that loads from the host, 48 s |
| Chat → social writer, X | failed (twitter/x, fixed above), then draft + card, 83 s |
| Approval form (38) over HTTP with the forms login | item approved and scheduled, 23 s |
| Control room (72) | wrong password 401; the right one redirects to the queue, which renders and shows the waiting X draft, calendar and health pages render |
| Publisher (39, real cron at :00 and :15) → postiz-bridge (54), DRY_RUN | first 422 (empty `CHANNEL_MAP`, by design); with a test map: 200 `dry_run`, the card fetched over the internal URL `http://image-cards:8000/…`; item stays approved |
| Site assistant (79) `/chat` | "Can I pause my subscription?" answered from the facts, 12.7 s; "price of the Team Box in Japan?" → handoff, 2.0 s |
| Clip finder (73) | `/health`: system ffmpeg, whisper model present, scene detection on; whisper small loads in 4.2 s |
| Video assembly (71) | 5 s vertical MP4 with the piper voice rendered in 5.2 s |

For the publisher test, `.env` had `PUBLISH_WEBHOOK_URL=http://postiz-bridge:8000/publish`, the
key and `POSTIZ_CHANNEL_MAP='{"linkedin":"test-linkedin","x":"test-x"}'` (fake ids: a dry run
never calls Postiz).

## Timings

| Step | Time |
|---|---|
| Build 36 light images (parallel) | 125 s |
| Build 71 video assembly (first) | 187 s |
| Build 73 clip finder (first) | 392 s |
| Rebuild 71 + 73 after the Dockerfile fix (pip layers cached) | 162 s |
| Pull n8n 2.40.5 + postgres | 97 s |
| `up -d` → all 40 healthy | 7 s to start, about 40 s to healthy |
| `import-n8n.sh` (43 workflows + restart) | 115–133 s |
| `smoke-test.sh` | 19 s |

## Size and memory

| Image | Size |
|---|---|
| 36 light service images | 225–318 MB each (shared `python:3.12-slim` base, 205 MB) |
| clip-finder (73) | 2.49 GB: ffmpeg + libgl 420 MB, pip 568 MB (ctranslate2, onnxruntime, opencv, av), whisper small 486 MB |
| video-assembly (71) | 1.30 GB: ffmpeg 420 MB, pip 278 MB (piper, onnxruntime), voice 63 MB |
| n8n 2.40.5 / postgres 16 | 1.73 GB / 411 MB |
| All images (`docker system df`) | 7.1 GB |

The colima disk image grows during builds and doesn't shrink by itself:
`colima ssh -p arm -- sudo fstrim -a` gave back 4.6 GB after the first build.

Memory (`docker stats --no-stream`, idle after the tests): **about 1.95 GB for all 40
containers**. n8n 459 MB; every Python service 32–62 MB; clip-finder 62 MB idle (whisper is
loaded per job). The VM had 5.1 GB available of 7.9 GB. Ollama's memory is on the Mac, not
in this figure.

## Still open

- **Social dry runs are re-sent every 15 minutes.** The publisher (39) holds CMS dry runs for
  24 h but not social ones, and each run creates a new short link for the same URL (2 links for
  item 1 after two runs, so about 96 per approved post per day while `DRY_RUN=true`). Needs a
  decision: hold social dry runs like CMS ones, or let 16 reuse a link for the same URL.
- The publisher's first run answered 422 while `CHANNEL_MAP` was empty. That is by design, but a
  dry run can't be tried until a map exists; the README could say so.
- Scripts use plain `docker`; with several Docker contexts, run them with `DOCKER_CONTEXT=…`.
- The build uses the classic builder (Compose warns "requires buildx"). Installing buildx is
  optional; the Dockerfiles now build with both.
- Not exercised in this run: listmonk/ollama profiles, clip jobs on a real video, hosted models,
  the rest of the scheduled workflows.

## Installer run (2026-09-28)

`scripts/install.sh` from a clean state: `docker compose --profile full down -v` (no containers,
no volumes), `.env` deleted. Same host and colima VM as above, `DOCKER_CONTEXT=colima-arm`.
Images were still cached from the first run, so the core build took seconds; a first install on a
new machine also pays the build times in *Timings* above.

| Run | Command | Result | Time |
|---|---|---|---|
| 1 | `install.sh --profile core --yes` | `.env` created (7 secrets generated, mode 0600, git-ignored), preflight PASS, **26/26 healthy**, n8n owner created over `POST /rest/owner/setup`, **34** workflows imported, smoke test **8/8 PASS** | **148 s** (build 5, start 13, import 112, smoke 17) |
| 2 | `install.sh --profile growth --yes` (same `.env`) | **36/36 healthy**, status page 34/34, **42** workflows, smoke **8/8 PASS** | **738 s** (build 520: 71 and 73 rebuilt from scratch, pip layers of 62 and 67; start 17, import 182, smoke 18) |
| 3 | `install.sh --profile core --yes` (downgrade) | the 10 growth containers removed (volumes kept), 26 healthy, smoke PASS; the 8 growth-only workflows unpublished (after a fix, below) | 149 s |

Checked by hand on the core install:

- **Nothing secret printed**: none of the 8 generated values (7 keys + the n8n owner password)
  appears in the install logs. The n8n owner logs in with `N8N_OWNER_EMAIL`/`N8N_OWNER_PASSWORD`
  from `.env` (200; a wrong password 401).
- **Services outside the profile are empty on n8n**: `ENGINE_URL`, `REVIEWS_URL`, `VIDEO_URL`,
  `CMS_PUBLISH_URL`, `VISIBILITY_URL` = empty; `CALENDAR_URL` set. After the growth run the growth
  URLs are set and the full-only ones (`AD_LIBRARY_URL`, `LEADS_URL`, `VISIBILITY_URL`) stay empty.
- **Chat agent on core**: "Plan a month of content for November about our decaf, channels
  linkedin and x" → the agent called tool 64, which answered that the content engine is not
  installed and named `install.sh --profile growth`; the agent passed that on (25 s).
- **Control room on core**: login, queue, calendar, performance, campaigns, status, more, chat and
  results pages 200. The engine page shows "engine: unreachable (ConnectError)" (no content
  engine on core; open point below).
- **Brand edits persist** (new `brand_data` volume, `BRAND_OVERRIDES_FILE`/`VOICE_FILE` in
  `/data`): `PUT /brand/editable` 200 and `PUT /voice` 200 with the key, 401 without it; both
  still there after `docker compose restart brand-service`.
- **Memory** (`docker stats`, idle): core 26 containers **1.35 GB**, growth 36 containers
  **1.69 GB** (full, 40 containers: 1.95 GB in the first run). Ollama's memory is extra, on the host.
- **Images on disk** (`docker system df -v`, unique + shared layers): core ≈ **2.7 GB**, growth
  ≈ **6.3 GB**, full ≈ **6.4 GB**. Free host disk fell from 17 GB to 12 GB during the growth
  build: colima's disk image grows and doesn't shrink by itself (`colima ssh -p arm -- sudo fstrim -a`).
- `down -v` afterwards: 6 s, 0 containers and 0 volumes of the project left.

Found and fixed while making the installer re-run safe:

| What broke | Fix |
|---|---|
| Owner setup got 404: n8n's `/healthz` (and the Docker healthcheck) answer before n8n has registered its REST routes | install.sh waits for `GET /rest/settings` itself, then reads `showSetupOnFirstLoad` |
| smoke-test right after `import-n8n.sh` failed: n8n was still restarting | import-n8n.sh waits until n8n is `healthy` and serves `/rest/settings` again |
| On a re-run, 3 rebuilt and recreated containers (8103, 8105, 8172) were healthy inside but "connection refused" on 127.0.0.1: colima kept a stale port forward. `docker compose up --force-recreate` of another service did not reproduce it, so it is intermittent | install.sh probes every published port after the health wait; a refused one is stopped, left down 5 s and started again (that restored 8103), then reported if still refused |
| Downgrade unpublished only 1 of 8 workflows: `docker compose exec` inside a `while read` loop read the loop's input | `</dev/null` on that exec; re-run unpublished all 8 (34 active = the core set) |

Still open after the installer run:

- The control room's engine page (72) says "unreachable" on core instead of "not installed"; it
  always gets `ENGINE_URL=http://content-engine:8000` (and treats an empty value as that default).
- `--profile full` was not run through the installer (the first run above had all 40 up).
- Not re-measured: a first build on a machine with no cached images (see *Timings*).

## Two-client leak test (2026-09-28)

Agency mode, Phase 1 (`docs/agency-mode.md`): two clients side by side with
`scripts/leak-test.sh --context colima-arm` (same M-series Mac, colima VM `arm`, 8 GB, 6 CPUs;
Ollama on the host, shared). It installs `acme` and `bravo` with `install.sh --client <slug>
--profile core --yes`, plants a canary fact in each brand, runs the checks, uninstalls `acme`,
checks `bravo`, then removes `bravo` too.

| Run | What | Result |
|---|---|---|
| 1 | first build of the core images under their shared names (`marketing-agent-*`) | both installs OK (acme 430 s incl. build 129 s, start 47, import 136, smoke 115; bravo 514 s); stopped at the canary: a bug in the test (the PUT body and the keys both went to curl's stdin) |
| 2 | `--no-build` | 0 leaks, 2 failed controls: the test sent `channels` as a list to `social_posts` (the gateway answers 500 on that; open point below) |
| 3 | `--no-build`, fixed test | **0 leaks, 0 failed controls**, exit 0, 588 s |
| 4 | `--no-build --inject-leak` (negative control: bravo's brand-service joined `acme_marketing` under the name `brand-service`, the old shared-network bug) | **exit 1, 5 LEAK lines**: acme's gateway injected bravo's brand, `brand-service` resolved to both containers, bravo's container name resolved and its address answered. The checks catch the leak they are there for |
| 5 | `--no-build`, with the uninstall guard (check g) | **0 leaks, 0 failed controls**, exit 0, 452 s (installs 157 s + 178 s) |
| 6 | rebuilt images; clients publish only public ports (`docker-compose.private.yml`), brand reads need the client's key, all calls made from inside each stack | **0 leaks, 0 failed controls**, exit 0, 462 s (installs 141 s + 178 s, in-stack smoke 8/8 each). New check: from inside each client, the other's public control-room port answers via `host.docker.internal` (control) and none of its internal ports does. Idle: 1289 + 1667 MiB, VM 3567 of 7922 MiB used |
| 7 | `--no-build --inject-leak` after run 6 | **exit 1, 4 LEAK lines** (DNS to both containers, container name resolves, address answers). Unlike run 4, acme's gateway injected **nothing** of bravo: bravo's brand service refused acme's key; acme's claim checker errored (fails closed) instead of accepting bravo's canary |

Run 5, check by check (each in both directions):

| Check | Result |
|---|---|
| g. `uninstall.sh --client acme-notours --yes` on a dummy container of a foreign compose project, without and with a forged `.env.acme-notours` | refused both times, the container untouched |
| a. Brand text and facts the gateway injects (read 5× with the gateway's own code inside its container) | own name + canary present (control), nothing of the other client |
| a. Real `/v1/run social_posts` after the 60 s cache | no other-client canary (acme 15 s, bravo 7 s; the model used its own canary in 1 of 2 posts) |
| b. Claim checker (44) on the other client's canary | flagged unsupported; own canary supported (control) |
| c. Other client's `APPROVER_KEY` on the calendar (19) | 403; own key 200 (control) |
| c. Other client's `INTERNAL_API_KEY` on calendar and gateway | 401 |
| c. Each n8n's `APPROVER_KEY` (sha256 compared, never printed); the two keys | own key; different |
| d. `brand-service` from inside each gateway | only its own container's address |
| d. Other client's container name / address:8000 from inside | does not resolve / connect times out |
| e. Published ports | 25 + 25, none shared, all on 127.0.0.1, acme only 82xx + 8299, bravo only 83xx + 8399; 8205 serves acme's brand, 8305 bravo's |
| f. `uninstall.sh --client acme --yes` | 5 s; 0 containers, 0 volumes, 0 networks of acme left, `.env.acme` and `clients/acme/` gone; bravo 26/26 healthy, its canary still in its volume |

**Resources** (`docker stats`, idle right after install; the VM from `free -m` inside it):

| | Containers | Memory |
|---|---|---|
| acme (core) | 26 | 1.28–1.30 GB |
| bravo (core) | 26 | 1.31–1.68 GB (the second install measured higher in runs 2, 3 and 5; not investigated) |
| VM with both running | 52 | 3.65 GB used of 7.9 GB, 4.0 GB available |

Both cores fit at once in the 8 GB VM; nothing had to run sequentially. Ollama's memory is on the
host, not in the VM. Disk: images of the core build added ≈ 0.6 GB (the previous
`marketing-agent-*` images are now untagged: 112 images in the VM, 10.3 GB reclaimable, not pruned).
Every test client was removed at the end (0 containers, 0 volumes, no `.env.<client>`); no key
value appears in any install or test log.

Seen but not a failure:

- From inside a client's container, another client's **published** port answers through
  `host.docker.internal` (200 on `/health` of bravo's 05 from acme's gateway). Nothing in a
  client's configuration points there, and the keyed endpoints still need that client's key, but
  05's `GET /facts` and `/profile` are open to any process on the host. Open point below.

Still open after this run:

- 05 `GET /facts`, `/profile`, `/profile/summary` answer without a key; on a multi-client host a
  misconfigured URL in one client could read another's brand through its published port.
- The gateway answers 500 (not 422) when `channels` is a list instead of a comma-separated string.
- The cron shift was tested on every shipped workflow file (`tests/test-clients.sh`), not read back
  from a running client's n8n.
- The leak test needs Docker and Ollama, so it does not run in CI; CI runs the unit tests
  (`tests/test-clients.sh`: 63 checks, no containers).
