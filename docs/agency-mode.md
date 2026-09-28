# Agency mode: several clients in one install

Status (2026-09-28): **Phase 1 built and leak-tested** (option A, one install per client; see
"Phase 1" below). Phases 2–4 are design only. Gap #5 in `_dev/research/hiring-demand.md`
("Agency mode: multi-client, multi-voice").

## Why build it

- **Demand.** "A large share of buyers are agencies" (hiring-demand.md, gap #5, citing the
  n8n Jobs board and an n8n community thread). The minimum build named there: per-client
  brand.yaml and facts, per-person voice profiles, a report per client, isolation between clients.
- **Price signals.** Agencies quote $3k–$15k per build and $2.5k–$8k/month on retainer
  (hiring-demand.md fact 4, Lets Viz). Per-client work: lead-gen retainers $1.5k–$4k/month;
  ghostwriting $1.5k–$3k/month per person, one operator self-reports 12 clients at $1.5k/month
  (hiring-demand.md #1, #6). These are asking prices from sellers, not audited data.
- **Competitors charge for it.** In the capability table, multi-brand is one of three rows
  where we score `–`, and 10 of 15 paid tool groups score `Y` (commercial-tools.md §1). Jasper Business
  (unlimited voices; Pro has 2), Predis Rise (4 brands), Lately agency and Supergrow Teams all
  price on it (commercial-tools.md §5, row 5). Credits are the main complaint (§2b.2), so a flat
  per-client fee is a selling point, and so is "client data never leaves the box" (§4.2).

## What assumes one brand today (read from the code)

| Where | The single-brand assumption |
|---|---|
| 05 brand | One `BRAND_FILE`, one `BRAND_OVERRIDES_FILE`, one `VOICE_FILE`; `/facts` is global |
| 03 gateway | `BRAND_URL` from env; brand summary, learned rules (46) and facts are held in **process-wide caches** (60 s TTL) and the last good value is **kept on error** |
| 44 claim checker | Reads `BRAND_URL/facts` and `KB_URL` from env: one fact set, one knowledge base |
| 06 KB | No owner column on docs; `/search` searches everything |
| 19 calendar | `items` table has no brand; one `APPROVER_KEY` for every approval |
| 45 campaigns, 61 engine | Campaigns, KPIs, experiments, pillars, slots: no brand column |
| 46 learning | `events` and `rules` global; `/examples` returns any brand's approved finals |
| 58 reviews, 70 customer language | Reviews, testimonials, quotes, personas: no brand column |
| 78 competitors, 80 leads, 82 visibility | One competitor registry; one CRM token, `BOOKING_URL`, `WEBHOOK_SECRETS`; one question set, aliases from 05 |
| 72 control room | One `CONTROL_USER`/`CONTROL_PASSWORD`, one `CONTROL_REVIEWER` string, one `CONTROL_ROOM_KEY` |
| n8n (24–83) | Every service URL, `APPROVER_KEY`, notify target, `PLAN_CHANNELS`, `LISTENING_QUERY` is a **global** `$env.*` (e.g. `CALENDAR_URL` in 23 places, `GATEWAY_URL` in 26) |
| 01 compose | `name: marketing-agent`; network has a fixed `name: marketing`; host ports fixed at `127.0.0.1:81NN` |

Two findings change the options below:

1. **The gateway cache is the easiest leak.** With one gateway serving two brands, brand B's
   prompt within 60 s of brand A's gets A's summary, rules and facts; if 05 is down, it gets
   A's forever. Any shared gateway needs the brand in the cache key and no cross-brand fallback.
2. **Two compose projects on one host share the `marketing` network**, because its name is
   fixed. Both projects then register the alias `brand-service`, and Docker DNS answers with
   either container. That is a silent cross-client leak in the naive "one install per client"
   setup. The network name must come from the project (`name: ${COMPOSE_PROJECT_NAME}_net`).
   Host ports collide too (second project fails to start); they need a per-client offset. (Both fixed in Phase 1; the leak test's `--inject-leak` recreates the shared network and catches it.)

## The options

### A. One install per client (one compose project per client, one n8n each)

`docker compose -p client-acme --env-file clients/acme.env up -d`. Each client gets its own
n8n, Postgres, volumes, keys, approver, control room. Ollama is shared (it holds no client data).

- **Pros.** Isolation by construction: no service code changes, no query can forget a filter.
  Each client can be on a different profile (core for one, full for another), backed up,
  exported and deleted as a unit ("offboard = `docker compose -p client-x down -v`").
- **Cons.** Ops cost grows linearly: core is 26 containers and 1.35 GB idle, full is 41 and
  1.95 GB (01-marketing-stack/INSTALL.md), so 5 clients on full ≈ 10 GB before the model's
  ~5 GB. N n8n instances to upgrade and re-import. The agency has N logins. Every client's
  schedules hit one Ollama at the same minute (Monday 07:00 planner x N): stagger the crons.
- **Leak risks.** Only the two infrastructure ones above (shared network name, host ports),
  plus Ollama's KV cache, which does not persist prompts across requests.

### B. `brand_id` in every service (true multi-tenant, one of everything)

Every table gets `brand_id`, every endpoint takes it (header `X-Brand`), every workflow passes
it, the gateway injects that brand's summary/facts/rules, per-brand API keys and approver keys.

- **Pros.** Lowest memory; one n8n; one control room with a brand switcher is natural;
  cross-client reporting is a SQL query.
- **Cons.** Touches ~25 services and ~45 workflows. Every query that forgets `WHERE brand_id`
  is a leak, and that is exactly the kind of bug tests miss. Schedules must fan out per brand.
  Per-client offboarding is a delete across 20 SQLite files. Largest effort, highest risk.
- **Leak risks.** Gateway caches; 46 `/examples` feeding brand A's approved posts as few-shot
  examples for brand B; 44 checking B's copy against A's facts (then A's facts pass as
  "supported" in B's post); 06 search returning A's documents; 61 near-duplicate check
  comparing across brands (harmless) and 45 experiments pooling data (wrong results).

### C. Hybrid: shared n8n, per-client service stacks (compose project per client)

One n8n (and one control room) for the agency; per client, a compose project with the
stateful services (03, 05, 06, 19, 44, 45, 46, 58, 61, 70, 78, 80, 82). Stateless tools
(07, 10, 12–18) are shared.

- **Pros.** Data isolation stays physical (separate volumes and gateways). One place to
  upgrade workflows. Less memory than A (one n8n + Postgres saved per client).
- **Cons.** n8n `$env` is process-wide, so every workflow must resolve URLs per brand: a
  `brands` map (brand → service base URLs, keys) read at the start of each run, and every
  schedule becomes a loop over brands. That is most of B's workflow work without B's
  memory savings. One `APPROVER_KEY` in n8n would approve for every client unless keys are
  per brand in the map. A bug in the map or loop sends A's draft to B's calendar.
- **Leak risks.** Wrong URL resolution in a workflow (one bad expression = cross-client write);
  a shared stateless service is fine as long as it keeps no state (07/10/12–18 keep none;
  16 link shortener and 17 image cards do keep state and stay per client).

### Migration effort per deploy (S < 1 day, M 1–3 days, L > 3 days)

| Deploy | A | B | C | What B/C need |
|---|---|---|---|---|
| 01 stack, install.sh | M | S | M | A/C: project-scoped network, port offset, `clients/<id>.env`, cron stagger |
| 03 gateway | – | M | – | brand in cache key, no cross-brand fallback, `X-Brand` → 05/46 |
| 05 brand | – | M | – | brand.yaml, overrides, voice per brand dir; `/brands` list |
| 06 KB | – | M | – | owner column, filtered search, per-brand delete |
| 19 calendar | – | M | – | `brand_id` (ADDED_COLUMNS pattern), per-brand approver key |
| 44 claim checker | – | M | – | facts + KB scoped by the request's brand; refuse without one |
| 45 campaigns / experiments | – | M | – | brand on campaigns, KPIs, experiments |
| 46 learning | – | M | – | brand on events, rules, `/examples` |
| 58, 70 | – | S each | – | brand column, filtered reads |
| 61 engine | – | M | – | pillars/slots/novelty per brand |
| 78, 80, 82 | – | M each | – | per-brand competitors; CRM tokens/booking/secrets; question sets |
| 16, 17, 20, 21, 22 | – | S each | – | 20 events per brand; 21 renders the brand name/logo |
| 72 control room | L (portal) | L | L | see "Control room" below |
| n8n workflows (~45) | – | L | L | pass brand everywhere; schedules loop over brands |
| 23 eval + leak tests | M | L | M | see "Tests that prove isolation" |
| **Total** | **~2–3 weeks** | **~8–10 weeks** | **~6–8 weeks** | |

## Security (all options)

- **Per-client approver.** Each client has its own `APPROVER_KEY` (A/C: its own env; B: a
  keys table in 19). The key that approves ACME's posts cannot approve Globex's. The rule that
  the agent never approves its own work (START-HERE) carries over unchanged.
- **People, not one shared login.** 72 today has one user. Agency mode needs named users with
  a role per client: `agency_admin` (all clients), `editor` (drafts, not approve), `client_approver`
  (approve/reject own brand only), `client_viewer` (read-only report and queue).
- **Audit log.** 19 records a note on status changes and 46 records `reviewer` on events, but
  there is no append-only log. Add one per client: who (named user), what (item, from → to,
  diff hash), when, from where (control room / form 38 / chat). Clients will ask "who approved
  this post?"; the answer must name a person, not "control room".
- **Secrets per client.** CRM tokens (80), Postiz/CMS keys (54, 62), GSC credentials (67),
  hosted-model keys (82, `CHAT_PROVIDER=hosted`) belong to the client and live only in that
  client's env. The agency admin can rotate them but the UI never shows them.
- **Offboarding.** Export (calendar, reports, facts, testimonials with consent records) then
  delete. A: one command. B: a per-brand delete in every service, which needs its own test.

## Billing hooks

Flat per-client pricing fits the credit complaint (commercial-tools.md §2b.2). Count, per
client per month, from data already stored: drafts created (19), items approved and published
(19), campaigns run (45), visibility runs and hosted-API spend (82 has a `usage` table), leads
handled (80), clips/videos rendered (71, 73). Gateway calls and model seconds need a new
counter in 03 (it already returns `duration_ms`). Output: a monthly CSV per client from the
agency portal. No payment integration; the agency invoices.

## Recommendation: A now, with a thin agency layer; C later only if memory forces it

A sells to a first agency within weeks and cannot leak through application code. B is the
right end state for a SaaS, which this is not: it spends 8–10 weeks making every query a
possible leak. C costs most of B's workflow work for a memory saving of about one n8n and one
Postgres per client. Revisit C when an agency runs more than ~8 clients on one host.

### Phase 1: safe per-client installs (~1 week) — enough for a first agency pilot

**Done 2026-09-28** (items 1, 2, 4; item 3 not started). How to use it: `01-marketing-stack/INSTALL.md`
section 12. Results: `01-marketing-stack/DOCKER-RUN.md`, "Two-client leak test".

1. **Done.** 01: network `<project>_marketing` (no fixed name); `install.sh --client <slug>` =
   compose project `<slug>` with `.env.<slug>` (mode 0600, every secret generated, never printed,
   incl. its own `APPROVER_KEY`, `CONTROL_ROOM_KEY`, forms and control-room passwords); host
   ports `127.0.0.1:${PORT_PREFIX}NN` (82..99, the next free block) with n8n on `<prefix>99`,
   while the single install keeps `81NN` and 5678 exactly; n8n public and media URLs on the
   client's ports; its own `clients/<slug>/` (brand.yaml base, feed list, `secrets/`); every
   container labelled `com.marketing-agent.client=<slug>`. Built images are shared by name
   (`marketing-agent-<service>`), so a second client does not rebuild. Status page and control
   room per client (`<prefix>22`, `<prefix>72`). `uninstall.sh --client <slug>` offboards one
   client and refuses any compose project install.sh did not create.
2. **Done.** `SCHEDULE_OFFSET_MIN` (10 min per port block). n8n schedule triggers are fixed cron
   strings in the workflow JSON, so `import-n8n.sh` rewrites the cron of that client's imported
   copy (`scripts/shift-cron.py`: plain minutes shift with carry into a plain hour, `*/15` becomes
   a list); the workflow files do not change.
3. Not started: per-client weekly report (41) with the client's name and logo from 05.
4. **Done, not in CI.** `scripts/leak-test.sh` (checks a–g, deterministic canary greps, positive
   controls, `--inject-leak` negative control that must exit 1). Run on colima: 0 leaks, 0 failed
   controls; the injected shared-network leak was caught (5 LEAK lines). It needs Docker and
   Ollama, so CI runs only `tests/test-clients.sh` (63 checks: names, port blocks, flags,
   rendered ports/networks/labels, cron shift on every shipped workflow).

Closed after Phase 1: on Docker Desktop and colima a container reaches ports published on
127.0.0.1 through `host.docker.internal` (checked on colima), so client A's containers could call
client B's services. Now a client publishes only what a browser or the internet opens
(`docker-compose.private.yml`, set by `install.sh --client`); the leak test checks from inside each
client that none of the other's internal ports answers, with the other's public control-room port
as the positive control. Also, 05 wants the client's own key on every read, so a wrong URL gets a
401 and no facts instead of another brand's. Still open: the single install (no `--client`) keeps
all its ports, so on a machine with clients, install every brand as a client. Two n8n on
`localhost` share a login cookie in one browser;
the schedule stagger was checked on the workflow files, not read back from a running n8n.

### Phase 2: agency portal (~1–2 weeks) — what the agency owner logs into

A small new service (or a mode of 72) that holds no client data: it lists clients, links to
each client's control room, shows each one's review queue count, last publish, weekly-report
link, health (22), and the monthly billing CSV. It talks to each client's 72 with that
client's read-only key.

### Phase 3: client approvals and people (~2 weeks)

Named users and roles in 72, a read-only client portal for approvals, per-person voice
profiles (hiring-demand #6: one client often has several people to ghostwrite for; 05 gets
`voices/<person>.json`, writers take `voice=`), and the append-only audit log.

### Phase 4 (only if needed): C

Shared n8n with a brands map, schedules looping over brands, per-brand approver keys in the
map. Build it only with the Phase 1 leak tests already green, and extend them first.

## Tests that prove isolation

Two clients, ACME (coffee) and Globex (bikes), each with a fact that only it has
(`ACME_CANARY`: "Our decaf is roasted in Hobart"; `GLOBEX_CANARY`: "Frames carry a 9-year
warranty") and a banned word only it has.

| Test | Pass means |
|---|---|
| Write 20 posts for Globex (26, 25, 57) | no ACME canary words, product names or numbers in any output |
| Claim-check "roasted in Hobart" as Globex (44) | flagged unsupported (would pass if ACME's facts leaked in) |
| Gateway: ACME prompt then Globex prompt within 1 s | Globex's rendered prompt contains Globex's summary, not ACME's (inspect the rendered text) |
| Gateway: Globex's 05 down | Globex calls get no brand text or fail; never ACME's cached text |
| 06 search as Globex for "Hobart" | zero results |
| 46 `/examples` for Globex | only Globex's approved finals |
| Approve an ACME item with Globex's approver key | 403 |
| Globex control-room user opens an ACME item id | 404 (not 403: do not confirm it exists) |
| DNS from inside Globex's n8n: `brand-service` | resolves only to Globex's container (A/C network test) |
| Offboard Globex | no Globex rows, files or volumes remain; ACME unaffected |
| Weekly report for ACME | no Globex numbers, campaign names or competitors |

The canary tests are deterministic (grep the output and the rendered prompts), so they run in
CI on every change to 01, 03, 05, 06, 44, 46 and the workflows. A pass on 20 drafts is not a
proof: they back up the structural guarantee (separate volumes and gateways in A), they do not
replace it.

## Control room (72) in agency mode

- **Brand switcher.** A client picker in the header, always showing the current client's name
  and colour, so an approver never approves in the wrong client by mistake. In A it is a link
  between control rooms through the portal (one login, session per client); in B/C a
  per-request brand with the brand echoed in every decision (the webhook refuses a decision
  whose item belongs to another brand).
- **Per-brand queues.** Queue, calendar, performance and engine status per client; the portal
  shows "waiting for review" counts across clients, sorted by the oldest item.
- **Client read-only portal for approvals.** The client's approver logs in, sees only their
  queue, previews each post as it will look on the channel, approves or rejects with a reason
  (the reason feeds 49 rewrite and 46 learning as today). No calendar editing, no brand setup,
  no chat, no keys. Optional: the agency marks items "agency-approved, waiting for client" so
  a post needs both before 39 publishes it (two approvals, both logged).
- **Per-client report view.** The weekly report (41) and AI-visibility numbers (83) for that
  client, downloadable, so the Monday report is what the agency sends.
