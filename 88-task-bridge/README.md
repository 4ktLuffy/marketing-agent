# task-bridge

Deploy **88 of 91** of the local-LLM marketing agent. It lets **any business use any chatbot**,
free plans included, and still only publish **facts it can stand behind**. It needs **no model**.

- **A task pack for any chatbot.** `POST /tasks` (goal, pieces, scope, publish date) builds plain
  text to paste into ChatGPT, Claude, Gemini or anything else. It fits a free plan
  (`PACK_MAX_CHARS`, 8000 by default). It lists the confirmed facts from the brand service (05)
  that are valid **on the publish date** for **this task's scope** (site, region, channel,
  segment, plan tier, variant). Each fact has a slot such as `[[weekday-rate]]`.
- **You see what leaves the business.** Public facts go out as text. Internal facts go out
  **only as a slot** with a neutral description ("the approved partner rate"). Their value
  never leaves. Restricted facts never go out at all. The share preview lists the exact fact
  lines in the pack (`sent`), the slot-only keys (`slotted`) and everything held back with its
  reason (`withheld`: expired, out of scope, restricted, internal, too long...).
- **Approved posts feed the next pack.** Free chats forget everything between sessions, so the pack
  carries the business's own voice: after VOICE, an `APPROVED EXAMPLES` block quotes up to 2 short
  pieces (each at most 400 characters, cut at a sentence end) that were approved **and exported**
  earlier, from 88's own records (the export manifest and the piece text with the same hash). Same
  channel first, else the same channel family, newest first, never from the task itself. An example
  is used only if it is still true: it is re-checked with the same evidence check as `/check` against
  the facts valid on the new task's publish date and scope, and skipped on any blocking finding (an
  old price), on any `[[slot]]`, or if it holds an internal or restricted value. Examples are the
  lowest priority in the pack: they never push out a fact or past the size target. The share preview
  lists them as `examples: [{task_id, piece_key, chars}]`. `PACK_EXAMPLES=off` turns this off (default
  `on`; `/health` shows it).
- **Paste the answer back.** The answer is split into pieces at the `=== 1 LINKEDIN ===`
  markers the pack asks for. Common chatbot variants (`**1. LinkedIn**`, `### Post 2 –
  Instagram`, `1) Email:`, `LinkedIn:`) work too. Preamble ("Sure! Here are...") and sign-off
  ("Let me know...") are removed and shown as removed. If the markers do not line up, nothing
  is guessed: you split it by hand.
- **Slots filled here.** `[[k]]`, `\[\[k\]\]`, `{{k}}`, `⟦k⟧`, `【k】` and more are filled with the
  fact's value text. A slot for an unknown, expired, out-of-scope, restricted or changed fact
  blocks the piece. So does `[[missing: ...]]`, which the pack tells the chatbot to write when a
  fact is missing.
- **Evidence for every sentence, in code.** Prices, percentages, quantities, dates, times and
  claim words ("certified", "guarantee", "UL", "made in"...) are compared with the facts:
  **match** (quoted), **wrong scope**, **conflicting or expired**, **no source**, **forbidden
  phrase**, **missing disclosure**, or **review** (never blocks).
- **Arithmetic in code.** Chatbots get sums wrong. `POST /quote` computes quantity × price (× nights)
  in exact decimals from public facts valid on the publish date, with their disclosures, and returns a
  ready text block. In every check (`/check`, submit) a stated total ("20 crates ... 32,000 birr"), a
  percentage change ("prices up 10%") or a saving ("was 2,000, now 1,700, save 500") is recomputed from
  the known price or the prices in the text: a wrong one is a blocking `conflict_or_expired` ("20 ×
  1,700 birr = 34,000 birr, not 32,000"), a right one a `match` showing the sum. Text with no computation
  is never blocked.
- **Approval bound to the exact text.** Each piece becomes a content-calendar (19) item with
  `origin=task-bridge` and `require_bound_approval`. A clean piece goes to review; a blocked
  piece stays a draft. The notes use the prefixes the control room (72) already flags
  (`unsupported claim:`, `quality gate:`, `warnings:`).
- **"It's there, in other words."** A person can accept one **missing disclosure** finding
  when the text says it differently (`POST /tasks/{id}/pieces/{key}/accept` with the hash of
  the text they saw, their name and a note). Nothing else can be accepted: a wrong price, an
  expired or out-of-scope fact or a blocked placeholder must be fixed. The acceptance is kept
  in the append-only events and listed in the export manifest and checklist; the piece moves to
  review only when nothing else blocks it, and approval is still bound to the text.
  The person can also copy the exact words that say it (`wording`). 88 then proposes them to the
  brand service (05) as the business's own wording for that disclosure (a draft there; if 05 is
  down the acceptance still stands and the answer says why the proposal failed). Once the owner
  confirms it in 05, the fact carries it in `disclosure_wordings`, and the next time those exact
  words (case and spacing ignored) are in a piece, the disclosure counts as said: no finding to
  accept again. A wording saved for one fact never counts for another.
- **Zero-AI templates.** Repetitive posts (a weekly crate price list, a room rate card, opening
  hours) need no chatbot. `POST /templates/render` `{template: price_list|rate_card|facts_digest,
  channel, scope, publish_on, title?, intro?, subjects?, group_by?}` writes the post from the facts
  valid on the publish date in the scope: **public facts only** (never internal, restricted, expired
  or slots), each line from the fact's own `value_text`, a disclosure shared by every line stated once
  at the top ("Prices per crate:"), rate facts of one subject grouped ("Twin Room: $125 one guest /
  $131 two guests"), the owner-confirmed kit disclosures added ("Selling to persons under 21 is
  prohibited."), and the channel's limit kept (14, else a default): SMS gets a compact message (lines
  that do not fit are named in `notes`), a long list is split into messages that each carry the header.
  Returns `{text, parts, facts_used, chars, notes}`; with no matching facts `text` is empty and a note
  says why. `POST /templates/task` (same fields plus `channel` or `channels`, `goal?`) renders each
  channel, creates a normal task, pastes the text as provider `template` and submits it: the same
  checks and a calendar item bound to the text. A person still approves. Nothing from the template
  skips the checker; if it ever flags its own text, the template is wrong, not the checker.
- **Ready-to-post export.** `GET /tasks/{id}/export` refuses (409) until every piece is
  approved, the approved text has the same hash as the checked text, and every fact of the
  pack is still valid at the publish date. The export is read back and compared before it is
  returned. It carries a manifest: pieces, hashes, disclosures, a posting checklist, the fact
  set version and approval audit references.
- **Facts change, copy follows.** Every 10 minutes (and on `POST /reconcile`) it reads
  `/facts/changes` from 05. A piece whose pack used a changed fact goes back from review or
  approved to draft with the note `warnings: fact <key> changed`. A published piece becomes a
  blocker to check by hand.

Nothing is posted anywhere. You post the exported text yourself.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing` network
(compose service `task-bridge`, port `127.0.0.1:8188`), with a volume on `/data`. It calls the
brand service (05) and the content calendar (19), and optionally platform rules (14), the claim
checker (44) and the lead hub (80) on the same network. It does not use n8n. It is internal
only: a client install closes its port (`docker-compose.private.yml`). It keeps state in
SQLite, so run exactly one instance and back up the volume.

## Run

```bash
docker build -t task-bridge .
docker run --rm -p 8188:8000 --env-file .env -v task-data:/data task-bridge
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./tasks.sqlite BRAND_URL=http://localhost:8105 \
  CALENDAR_URL=http://localhost:8119 uvicorn app.main:app --port 8188
```

## How to use it

```bash
K='X-API-Key: change-me'; J='content-type: application/json'
# 1. A task. Returns the id, the pack and the share preview.
curl -s localhost:8188/tasks -H "$K" -H "$J" -d '{"goal": "Autumn midweek stays",
  "pieces": [{"key": "p1", "channel": "linkedin", "kind": "post"}, {"key": "p2", "channel": "instagram"}],
  "scope": {"sites": ["lakeside"]}, "publish_on": "2026-10-06"}'
# 2. Copy the pack (plain text) into any chatbot.
curl -s 'localhost:8188/tasks/T-ABC234/pack?format=txt' -H "$K"
# 3. Paste the chatbot's answer back. Check "split" and "problems".
curl -s localhost:8188/tasks/T-ABC234/paste -H "$K" -H "$J" -d '{"text": "...", "provider": "chatgpt"}'
#    If there are problems, split it yourself (a new draft id comes back):
curl -s localhost:8188/tasks/T-ABC234/drafts/1/split -H "$K" -H "$J" \
     -d '{"pieces": [{"piece_key": "p1", "text": "..."}, {"piece_key": "p2", "text": "..."}]}'
# 4. Submit: slots filled, evidence, brand and platform checks, one calendar item per piece.
curl -s localhost:8188/tasks/T-ABC234/submit -H "$K" -H "$J" -d '{"draft_id": 1}'
# 5. Approve the items in the control room (72). Then export.
curl -s 'localhost:8188/tasks/T-ABC234/export?format=md' -H "$K" -o T-ABC234.md
```

## Endpoints

All but `/health` need `X-API-Key`.

| Method | Path | What |
|---|---|---|
| GET | `/health` | Status and which services are configured |
| POST | `/tasks` | New task: pack, `pack_sha256`, `fact_set_version`, `snapshot`, `share_preview` |
| GET | `/tasks`, `/tasks/{id}` | List; one task with pieces, drafts, exports and its audit events |
| GET | `/tasks/{id}/pack` | The pack (`?format=txt` for plain text) |
| POST | `/tasks/{id}/paste` | `{text, provider}` (≤ `PASTE_MAX_CHARS`) → `{draft_id, split, problems}` |
| GET | `/tasks/{id}/drafts/{d}` | A draft with the pasted text and its split |
| POST | `/tasks/{id}/drafts/{d}/split` | Split by hand → a new draft |
| POST | `/tasks/{id}/submit` | `{draft_id}` → per piece `filled_text`, `filled_sha256`, `blocked`, `findings`, `calendar_item_id` |
| POST | `/tasks/{id}/pieces/{key}/accept` | `{finding, expected_sha256, by, note, wording?}`: accept a missing-disclosure finding; 409 if the text changed, 422 for any other label, 422 when `wording` (3–200 chars) is not copied from the text; with a wording the answer has `wording_proposal: {id, status}` or `{error}` |
| POST | `/check` | Stateless `{text, scope, publish_on, channel}` → `{findings, blocked, filled_text}` |
| POST | `/quote` | Exact sum from public price facts: `{scope, publish_on, lines:[{fact_key, quantity, nights?, guests?}], currency?, allow_internal?}` → `{lines, total, currency, text, notes}`. Internal facts only with `allow_internal: true` and header `X-Internal-Quote: yes` (text marked confidential); restricted, expired, out-of-scope facts and mixed currencies are 422 |
| POST | `/audit` | Read-only public content drift audit (see below): `{sources, scope, on}` → per source the drift against today's facts |
| GET | `/tasks/{id}/export` | `?format=txt\|md`; 409 with reasons until ready |
| POST | `/reconcile` | Run the fact-change reconcile now |
| GET | `/blockers` | `[{kind, count, text, link}]`: missing facts, blocked slots, changed or expired facts in use, published pieces with changed facts, facts due for review, leads waiting (80) |

## Public content drift audit

`POST /audit` compares what the business's **own public text** says with today's facts, and reports
only what has **drifted**: the 2024 rate card the website still links, an expired offer on a landing
page, an old post with last year's price, another branch's price on this branch's page, an internal
value (a partner rate) written on a public page. It never writes anything (no task, no calendar item).

```bash
curl -s localhost:8188/audit -H "$K" -H "$J" -d '{"sources": [
    {"url": "https://lodge.example/rates.pdf"},
    {"text": "Midweek Escape from £210 a night.", "label": "old LinkedIn post"}],
  "scope": {"sites": ["quayside"]}, "on": "2026-10-01"}'
```

- **Sources**: 1 to 10, each exactly one of `{"url"}` (http/https) or `{"text", "label"?}`. `on` is the
  day the facts must be valid (default today); `scope` is as in `/check`. Pasted text totals at most
  200,000 characters (413 over); a fetched page that no longer fits the shared budget is that
  source's error. Rate-limited with `/check` (`RATE_PER_MIN`). Needs `X-API-Key`.
- **URLs are read by the page extractor (07)**: `EXTRACTOR_URL` (HTML and text PDFs; SSRF-guarded;
  07 returns at most 20,000 characters per page, which the source's `note` says when reached). An
  image-only PDF comes back empty: the source gets `note: "no text found (image-only PDF?)"`. If
  `EXTRACTOR_URL` is unset, 07 is down, or a page cannot be fetched, that source gets an `error`;
  the other sources are still audited.
- **Same check as `/check`** (`evidence.check_text`, facts valid on `on` in `scope`), but only
  `conflict_or_expired`, `wrong_scope` and `slot_blocked` (an internal or restricted value written out)
  are reported as drift. `match` is only counted; `review`, `no_source`, `missing_disclosure` and
  `forbidden_phrase` are not part of a drift audit (use `/check` for a draft).
- **Answer**: `{on, scope, fact_set_version, sources: [{source, kind, sentences_checked,
  ok_matches, drift: [{sentence, label, fact_key, says, should_say, current_fact_key, leak, detail}],
  note, error}], totals: {sources, drift, errors}}`. `says` is the value the text states that the fact
  does not (else the sentence); `should_say` is the **public** value text of the fact that holds today
  for the same thing (the fact itself, the one that supersedes it, or a valid in-scope fact with the same
  subject and attribute), or `null` when there is none or the fact is internal (`leak: true`).
  A source with no drift and no error is clean.

## The evidence labels

A finding is `{sentence, label, fact_key, quote, blocking, detail}`.

| Label | When | Blocks |
|---|---|---|
| `match` | the value (or claim word) is in a fact that is valid in scope on the publish date; the fact is quoted | no |
| `wrong_scope` | the value is a fact's that is valid, but only for another site, plan, variant... | yes |
| `conflict_or_expired` | the value is an expired, superseded or not-yet-valid fact's; or it differs from an in-scope fact about the same subject; or an expired offer is named; or a deadline is before the publish date | yes |
| `no_source` | a claim word, price or percentage no fact supports | yes |
| `forbidden_phrase` | a fact's forbidden phrasing, unless an in-scope fact allows it for the subject the sentence names | yes |
| `missing_disclosure` | a fact is used (slot, value or claim word) and none of its required disclosures (nor a wording the owner confirmed for it, `disclosure_wordings`) is in the piece | yes |
| `slot_blocked` | a slot that cannot be filled | yes |
| `review` | a quantity no fact mentions, or a mention of an out-of-scope or expired subject with nothing checkable | no |

**Few false warnings.** A value counts as a match if it appears anywhere in an in-scope fact
(its value, value text or sentence). It is flagged only against a stale or out-of-scope fact's
**own** value. Bare numbers are ignored. Prices compare by amount and currency (`£1,200` =
`1200 GBP` = `£1.2k`). Disclosures compare word sets (`2 sharing` = `two sharing`). A sentence
that names the right subject may use that subject's allowed phrasing. Truthful paraphrases in
the tests raise no blocking finding.

**Model check (optional).** With `MODEL_CHECK=auto` and `CLAIMS_URL` set, each piece also goes to
the claim checker (44) with the pack's public fact lines. It can only **add** non-blocking
`review` or `no_source` findings. It never removes or unblocks one. The default is off: zero
model calls.

**Narrow model check (`MODEL_CHECK=review`, optional, off by default).** This mode sends only
the sentences where the rules are unsure. A sentence goes when (a) it has a non-blocking
`review` finding and nothing blocking, or (b) it has no finding at all but contains an offer,
scope or claim cue: a number, a price or amount word, `%`, free / on us / two for one, every /
all / any branch (shop, site, location ...), 24/7, day and night, weekends, award / rated /
voted / best / No.1 / certified / approved, since <year>, or N+ customers. At most 12 sentences
per piece go to 44 `/verify`, with the pack's public fact lines. Each line also carries its
validity dates and scope ("valid until 30 September 2026; applies only to sites: porthleven").
No `context` is sent: 44 turns context into evidence lines, so an instruction there would
count as a fact. The model can only **add** one non-blocking `review` finding per sentence,
with the detail `model check: <reason>`. It never blocks, and it never removes or changes a
rule finding. The call uses `CLAIMS_TIMEOUT`. On any error the piece keeps its rule findings
and gets a `model check skipped: ...` note. `auto` and `off` behave as before.

Measured on 2026-09-30 with local Ollama `qwen2.5:7b` (03 gateway → 44 in lenient mode;
`23-eval-suite: python -m evalsuite.task_bridge --claims-gateway ... --model-check-mode review`).
Results are in `23-eval-suite/results/task-bridge-model-review-{v12,v13,v4}-qwen2.5-7b.json`.
The scorer counts only blocking findings, so its numbers match the zero-model run. The model
notes were counted separately:

| set | rules (zero-model) | rule misses with a model note | false model notes | other notes | s/draft mean (max) |
|---|---|---|---|---|---|
| companies-v12 | 67/69, FW 0, MNF 0 | 2 of 2 | 0 | 0 | 3.5 (30.7, cold model) |
| companies-v13 | 39/47, FW 0, MNF 3 | 4 of 8 | 2 (both in 02-paraphrase) | 0 | 2.3 (12.9) |
| companies-v4 (control) | 95/95 | – | 1 (01-clean) | 0 | 3.5 (18.2) |

Catches: "24/7" and "day and night, weekends included" (scope), "migrate your data for free,
up to 10,000 contacts" (expired offer), "Trusted by 5,000+ sales teams", "Rated outstanding
by ...", and "from age 3". The false notes were on "Nine stamps ... (fact: 9)", "Two weeks on
us", and "your number one job this autumn". Four v13 misses were not reached. One is on a
sentence that already blocks for another reason, so it is never sent. Two have no cue ("at
their door today", "any day of the week"). One is a missing disclosure, which 44 does not
check.

Verdict: the mode catches 6 of 10 rule misses, with 3 false notes over 90 drafts. The notes are
advice and never gate anything. It is worth turning on where a reviewer reads the `warnings:`
line, but it stays **off by default**. This is one model and one run on small sets, and the
cue list was written after seeing the v12 misses, so v12 is not a held-out result. v13 and v4
are the fairer numbers.

## Configuration

See `.env.example`. `BRAND_URL` and `CALENDAR_URL` are required (503 without them). `RULES_URL`,
`CLAIMS_URL` and `LEADS_URL` are optional: when one is empty or down, that step is skipped with a
note on the piece.

## Known limits

- Evidence is deterministic and English-first. A claim made only in words ("our cheapest room")
  is caught only through claim keywords and forbidden phrasing, not by meaning.
- Weekdays and months alone are not checked; dates are compared by day and month.
- Reconcile treats every fact in a task's pack as used by every piece of the task (conservative:
  a paraphrase may carry a fact without a value or slot).
- Approving still happens in 19 (through n8n and the control room); this service never
  approves or publishes.

## CI

`.github/workflows/ci.yml` runs `pytest` on every push and pull request, and on `main` builds
and pushes the image to GHCR.
