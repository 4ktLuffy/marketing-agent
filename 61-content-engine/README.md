# content-engine

Deploy **61 of 87** of the local-LLM marketing agent. It turns **one substantial pillar**
(an essay, a guide, a talk transcript) into **a month of varied posts** without drifting
into spam. The design and the research behind every number are in
`_dev/research/content-volume.md` (section 4, stages A, B, D2/D3 and E). The service does four things:

- **Stores the pillar and its atoms.** Atoms are small, self-contained ideas (claim, story,
  faq, tip, stat, objection, quote) that the `pillar_atoms` prompt (04) extracts. Each one is
  checked by the claim checker (44) in the workflow. Only atoms marked `verified` are planned.
- **Plans the month in code.** It deterministically assigns atoms × hook styles × formats ×
  channels to dated slots, with caps. A 7B model can't be trusted to produce 80 distinct
  slots in one call, so the planning doesn't happen in a prompt.
- **Checks novelty.** It tells you whether a draft is too close to something already on
  that channel in the last 90 days, using word 5-gram overlap, the same opening words, and
  embedding cosine similarity (Ollama).
- **Applies the stop rule.** When too many drafts in a batch are rejected or edited, the
  pillar pauses until a person resumes it.

It never writes a post, never calls the LLM gateway and never publishes. The drafting
workflow (to be built) calls this API, writes into the calendar (19), and **a person
approves every piece** in the approval form (38).

## Where to deploy

Run it on the **Docker host** that runs `01-marketing-stack`, as the container `content-engine` on the
`marketing` network with a volume on `/data`. n8n calls it at `http://content-engine:8000`
(env `ENGINE_URL`). It needs Ollama for the embedding check only, at `OLLAMA_URL`, with the
model `qwen3-embedding:0.6b` that 02 already pulls for the knowledge base. If Ollama is
unreachable, the check still runs without embeddings. State is kept in SQLite, so run
exactly one instance and back up the volume.

## Run

```bash
docker build -t content-engine .
docker run --rm -p 8161:8000 -e INTERNAL_API_KEY=change-me \
  -e OLLAMA_URL=http://host.docker.internal:11434 -v engine-data:/data content-engine
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./engine.sqlite OLLAMA_URL=http://127.0.0.1:11434 \
  uvicorn app.main:app --port 8161
```

## Endpoints

🔑 = needs the header `X-API-Key: $INTERNAL_API_KEY`. Errors are FastAPI's `{"detail": ...}`.
404 = unknown id; 422 = invalid body.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/pillars` 🔑 | `{"title","brief","audience","source_text"?,"source_url"?,"channels":[...],"month":"YYYY-MM","promo_max":0.2}` | pillar, `201` |
| GET | `/pillars` | `?month=YYYY-MM&status=active\|paused` | `[pillar]` without `source_text`, newest first |
| GET | `/pillars/{id}` | — | pillar |
| POST | `/pillars/{id}/atoms` 🔑 | `{"atoms":[{"kind","text","verified","evidence"?,"promo"?}]}` (1–200) | `{"added","ids","skipped","verified_total","min_atoms"}`, `201` |
| GET | `/pillars/{id}/atoms` | `?verified=true\|false` | `[atom]` |
| POST | `/pillars/{id}/plan` 🔑 | `{"start_date"?,"weeks":4,"cadence"?:{channel: posts_per_week},"seed"?}` | plan (below) |
| GET | `/pillars/{id}/slots` | `?status=planned\|drafted\|dropped&channel=` | `[slot]` by date, time |
| GET | `/slots/{id}` | — | slot |
| POST | `/slots/{id}/status` 🔑 | `{"status":"planned"\|"drafted"\|"dropped","calendar_item_id"?,"reason"?}` | slot |
| POST | `/novelty/register` 🔑 | `{"item_id","channel","text","created_at"?}` | `{"item_id","channel","embedded"}` |
| POST | `/novelty/check` | `{"text","channel","exclude_item_id"?,"any_channel":false}` | novelty verdict (below) |
| POST | `/pillars/{id}/outcomes` 🔑 | `{"item_id","decision":"approved"\|"edited"\|"rejected"}` | `{"recorded","counted_decision"}` + health |
| GET | `/pillars/{id}/health` | — | health (below) |
| POST | `/pillars/{id}/resume` 🔑 | — | health, `paused:false` |

**Shapes**

- pillar: `{id, title, brief, audience, source_text, source_url, channels[], month, promo_max,
  status: active|paused, paused_reason, window_start, created_at, updated_at, atoms,
  atoms_verified, slots: {planned, drafted, dropped}}`
- atom: `{id, pillar_id, kind, text, verified, evidence, promo, created_at}`
- slot: `{id, pillar_id, date: YYYY-MM-DD, time_utc: HH:MM, channel, format, atom_id,
  hook_style, status: planned|drafted|dropped, calendar_item_id, reason, created_at,
  updated_at, experiment_id, arm, experiment: {id, variable, arm, value, brief} | null,
  atom: {kind, text, evidence, promo}}`
- plan: `{pillar_id, seed, start_date, end_date, weeks, cadence, requested: {channel: n},
  planned: {channel: n}, kept_drafted: {channel: n}, unfilled: {channel: n}, atoms_verified,
  removed_calendar_item_ids: [the idea items of the planned slots this re-plan deleted],
  experiments: [{id, variable, channels, arms: {A, B}, assigned: {A, B}, error}],
  experiments_error, slots: [the planned slots]}`
- novelty verdict: `{novel, reasons: [str], closest: {item_id, channel, score_ngram,
  score_embed} | null, compared, embedding_checked, embed_model, thresholds}`
- health: `{pillar_id, since, decided, approved, edited, rejected, approved_clean_rate,
  rejected_rate, min_decisions, paused, reason}` (rates are `null` before any decision)

Channels: `linkedin, x, instagram, facebook, threads, mastodon, blog, email, video`.
Formats: `post, thread, carousel_text, blog, email, video_script`.
Hook styles: `question, fact_led, story, how_to, benefit, contrarian` (the enum of
`social_posts` in 04 and `/insights/hooks` in 45).

### How the planner works (`POST /pillars/{id}/plan`)

1. **Enough atoms.** If the pillar has fewer than `MIN_ATOMS` (12) verified atoms, it returns
   `422`: "pillar N has 7 verified atoms; planning needs at least 12 ...". A paused pillar
   returns `409`.
2. **Slots per channel** = `cadence × weeks`. Default cadence per week, inside the platform
   guidance from the research (section 2): linkedin 3, x 7 (one a day), instagram 3,
   facebook 3, threads 3, mastodon 3, blog 1, email 1, video 2. `cadence` in the body
   overrides it per channel (0–7; naming a channel the pillar doesn't have is a `422`).
   `start_date` defaults to the 1st of the pillar's month. Week *w* is the 7 days from
   `start_date + 7w`.
3. **Days and times.** One post per channel per day at most. Weekdays come first and are
   spread out (3 a week → Mon/Wed/Fri), rotated per channel so channels don't all post on
   the same day. Weekends are used only at 6–7 a week. The time is picked (seeded) from a
   small per-channel list of UTC hours, on the quarter hour.
4. **Formats rotate per channel.** x: 6 posts then 1 thread (one thread a week at 7/wk).
   linkedin: post, post, carousel_text. instagram: carousel_text, post. blog → blog,
   email → email, video → video_script. Everything else is a post.
5. **Atom and hook per slot, earliest slot first.** Every atom is used on **at most 3
   channels**, **never twice on one channel**, and **with a different hook each time**, so
   the same (atom, hook) pair never repeats. The planner prefers an atom that hasn't been
   used within 48 hours on another channel, then the least-used atom, then a seeded order.
   The hook is the style this channel has used least so far, so each channel gets a mix.
   **Promo atoms** are held to at most `promo_max` of each channel's slots, rounded down
   (0.2 of 12 LinkedIn slots → 2), which also caps the overall share.
6. **Unfilled slots are reported, not faked.** If the atoms run out, the plan lists the
   shortfall in `unfilled`. Because an atom never repeats on a channel, **a channel's slot
   count can't exceed the number of verified atoms**. X at 7 a week for 4 weeks needs 28
   atoms, and the overall ceiling is 3 × atoms.
7. **Deterministic.** The same atoms, body and `seed` (default: the pillar id) give the
   same plan.
8. **Idempotent re-plan.** Re-planning deletes only the slots still `planned` and plans again.
   `drafted` slots are kept. They hold their day on their channel, count toward the week's
   cadence, and count against their atom's channel and hook limits. `dropped` slots are
   kept as history and don't count.

With the default cadence, the design's six channels (linkedin, x, instagram, blog, email,
video) give 17 slots a week, **68 in 4 weeks**. The design's "about 85" assumed LinkedIn at
4 a week and X at about 10 a week. Pass those as `cadence` if you want that volume, and have
at least 40 verified atoms.

### Experiments (`CAMPAIGNS_URL`)

With `CAMPAIGNS_URL` set (the stack sets it), a plan first reads
`GET {CAMPAIGNS_URL}/experiments?status=approved,running` from the campaign service (45).
Every slot on a channel of such an experiment gets one of its two arms:

- At most 2 experiments per channel share its slots, round-robin, oldest first.
- Arms are balanced by weekday first, then by hour bucket (before 12, 12–17, after 17 UTC),
  then overall: each slot, in date order, takes the arm with fewer slots on that weekday,
  in that bucket, in total; a tie is broken at random (seeded, so the plan stays
  deterministic). Drafted slots that already have an arm count too. A `time` experiment
  sets the hour, so only weekday and total are balanced.
- The arm is forced on the slot: a `hook_style` arm sets `hook_style` (the atom is chosen so
  the (atom, hook) pair still never repeats), `format` sets the format, `time` the
  `time_utc`. `cta` and `length` arms are instructions: the drafter (65) adds them to the
  prompt, and keeps a `hook_style` arm on its novelty retry.
- After saving, the plan reports each experiment's slots to
  `POST {CAMPAIGNS_URL}/experiments/{id}/assign` (with `remove_slot_ids`: the experiment
  slots a re-plan deleted). The first report starts an approved experiment. If 45 refuses
  (e.g. the experiment was stopped) or is down, those slots lose their experiment fields and
  `experiments[].error` says why; a plan never fails because of an experiment.
- Without `CAMPAIGNS_URL` nothing of this happens. If 45 cannot be read, the plan has no
  experiments and `experiments_error` says why.

### Slot status

`planned → drafted` (the workflow wrote a calendar item: pass `calendar_item_id`),
`planned → dropped`, and `drafted → dropped` (for example, a gate failed twice: pass
`reason`). Anything else is a `409`. Setting the current status again only updates
`calendar_item_id` / `reason`.
The plan tool (64) uses that on planned slots: `{"status":"planned","calendar_item_id":N}`
records the calendar `idea` item it created for the slot; the drafter (65) then fills that
item. A drafted or dropped slot can't go back to `planned` (`409`). A re-plan deletes
planned slots, including their `calendar_item_id`, and returns those ids as
`removed_calendar_item_ids`; 64 moves those idea items to `rejected` with the note
"re-planned" so they don't stay in the calendar as orphans.

### Novelty (`/novelty/*`)

Register every draft you keep, with its calendar item id: `POST /novelty/register`. The
call is an upsert on `item_id`, so a revised draft replaces its old text. Before keeping a
new draft, call `POST /novelty/check`. It compares the draft with the texts registered on
the **same channel** (`any_channel: true` compares all channels) during the last
`NOVELTY_DAYS` (90), leaving out `exclude_item_id` (the item's own earlier version). A draft
is **not novel** if any of these holds:

| Check | Rule | Default |
|---|---|---|
| (a) 5-gram overlap | Jaccard of word 5-grams (lowercased, punctuation stripped; a text under 5 words is one gram) `≥ NGRAM_MAX` | 0.30 |
| (b) same opening | first 8 words, normalized the same way, identical | — |
| (c) embedding | cosine of Ollama `/api/embed` vectors (`EMBED_MODEL`) `≥ EMBED_MAX` | 0.85 |

`reasons` has one line per hit (e.g. `"same first 8 words as item 12"`). `closest` is the
most similar registered text: by embedding when available, otherwise by 5-gram score.
Embeddings are cached in SQLite, keyed by model and text. When Ollama is down or answers
something invalid, (c) is skipped and the result says `embedding_checked: false`. The
check is then weaker: flag that to the reviewer, don't treat it as a pass.
`embedding_checked` is `true` when there was nothing to compare against.

Measured on this machine with the real `qwen3-embedding:0.6b` against one registered
LinkedIn post: an identical text scored 5-gram 1.00 and cosine 1.00. A paraphrase that
kept the first 8 words scored 5-gram 0.10 and cosine 0.92, so it was flagged twice (opening
and embedding). An unrelated text scored 0.00 and 0.30 and was novel. These thresholds are
starting guesses from the design. Measure them against about 50 human "same post?" labels
before relying on them.

### Stop rule (`/outcomes`, `/health`, `/resume`)

Post each reviewer decision on an item from this pillar to `POST /pillars/{id}/outcomes`.
**The first decision per item counts.** A later one is ignored (`recorded: false`), so a
draft that was rewritten after a rejection still counts as a rejection. Once at least
`MIN_DECISIONS` (10) decisions have been made since the last resume, the pillar **pauses**
when `rejected_rate > 0.25` or `approved_clean_rate < 0.5` (approved without edits). Both
checks are strict: exactly 25% rejected or exactly 50% clean doesn't pause. A paused
pillar stays paused as later decisions come in. It refuses `/plan` with `409` and reports
`paused: true` and `reason` on `/health`. Only `POST /resume` un-pauses it. That starts a new
window, and decisions before it no longer count.

```bash
K='X-API-Key: change-me'; J='content-type: application/json'
curl -s localhost:8161/pillars -H "$K" -H "$J" -d '{"title":"Why remote teams need a coffee ritual",
  "brief":"shared weekly coffee call","audience":"people-ops leads","channels":["linkedin","x","instagram"],
  "month":"2026-10","promo_max":0.2}'
curl -s localhost:8161/pillars/1/atoms -H "$K" -H "$J" -d '{"atoms":[{"kind":"tip",
  "text":"Pick a coffee-call slot that works across time zones.","verified":true,"evidence":null,"promo":false}]}'
curl -s localhost:8161/pillars/1/plan -H "$K" -H "$J" -d '{"start_date":"2026-10-05","weeks":4}'
curl -s localhost:8161/novelty/check -H "$J" -d '{"text":"draft text","channel":"linkedin"}'
```

## For the workflow that drives it

This is the contract a future n8n workflow (the design's stage A to E) builds against:

1. `POST /pillars`, then the gateway `pillar_atoms` (vars `pillar_title`, `brief`,
   `audience`, `source_text`, `n`), then 44 `/verify` per atom with the pillar as context,
   then `POST /pillars/{id}/atoms` with `verified` set from 44. If `verified_total <
   min_atoms`, stop and tell the person the pillar is too thin.
2. `POST /pillars/{id}/plan`. Show `unfilled` if it isn't all zero.
3. For each `planned` slot, draft from `slot.atom.text` + `evidence` with the slot's
   `hook_style` and `format`. Then `POST /novelty/check`. If it isn't novel, rewrite once
   with a different hook. If it fails again, `POST /slots/{id}/status {"status":"dropped","reason":...}`.
   Otherwise create the calendar item (19), `POST /novelty/register`, and set
   `POST /slots/{id}/status {"status":"drafted","calendar_item_id":...}`.
4. For each approval-form decision on these items, `POST /pillars/{id}/outcomes`. If the
   result says `paused: true`, stop drafting and notify.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for write endpoints. Unset → writes return `503`; wrong or missing key → `401`. |
| `DB_PATH` | `/data/engine.sqlite` | SQLite file (WAL mode; writes use `BEGIN IMMEDIATE`) |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama, for `/api/embed` |
| `EMBED_MODEL` | `qwen3-embedding:0.6b` | embedding model |
| `CAMPAIGNS_URL` | — (the stack: `http://campaign-service:8000`) | campaign service (45) for experiments; empty = none |
| `EMBED_TIMEOUT` | `60` | seconds per embedding call |
| `MIN_ATOMS` | `12` | verified atoms needed to plan |
| `NOVELTY_DAYS` | `90` | how far back novelty looks |
| `NGRAM_MAX` | `0.30` | 5-gram Jaccard at or above this = duplicate |
| `EMBED_MAX` | `0.85` | cosine at or above this = duplicate |
| `MIN_DECISIONS` | `10` | decisions before the stop rule applies |
| `REJECT_MAX` | `0.25` | pause when the rejected share is above this |
| `CLEAN_MIN` | `0.5` | pause when the approved-unedited share is below this |

## CI

`.github/workflows/ci.yml` runs the tests (Ollama mocked with respx), then pushes
`ghcr.io/<you>/<repo>:latest` on every push to `main`.
