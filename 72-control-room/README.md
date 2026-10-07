# control-room

Deploy **72 of 91** of the marketing agent. A small, mobile-first web app for the one
person who approves the agent's work:

1. **Login**: one owner from `CONTROL_USER` / `CONTROL_PASSWORD`, or named people with roles
   (owner, approver, writer) from a users file; see *People and roles*.
2. **Review queue**: one card per item in review, previewed the way its network shows it (image
   card, video player with poster, "…see more" fold), with the flags the quality gate, claim
   checker and novelty check wrote, the hook style and the content pillar. Big **Approve / Edit /
   Reject / Skip** buttons, swipe right (approve) or left (reject) on a phone, keys
   `a` `e` `r` `s` (`j`/`k` to move, `?` for help), and an **Undo** toast: a decision waits
   `UNDO_SECONDS` (5) before it is sent, so Undo never has to reverse anything.
3. **Edit & approve**: the text with a character counter per network (limits from
   `14-platform-rules` `GET /rules`, URLs counted as 23 on X and Mastodon), the fold preview, a
   publish time and an optional reason.
4. **Calendar** (month, week, list) coloured by status; drag an item to move it.
5. **Item detail**: notes as a timeline, links, image, video player, rejections so far (46).
6. **Performance**: clicks by channel, top posts and hook-style winners (45 `/insights`,
   `/insights/hooks`), and a **Paid ads** panel when `ADS_URL` is set: spend, conversions, CPL,
   ROAS, CTR and CPC per platform and per campaign, and the open pacing alerts (84 `/summary`,
   read-only).
7. **Content engine**: each pillar's health and pause state, with a **Resume** button (61).
8. **Campaigns**: scorecards (target vs actual vs time elapsed) and campaigns that ended unmeasured (45).
9. **Chat**: a link to the n8n chat (see *Chat* below for why it is a link).
10. **Health**: the status page's summary (22).
11. **Brand setup** (`/brand`): six short steps instead of editing `brand.yaml`: basics, products
    (add/edit/remove rows), facts, rules (banned phrases, emoji, other domains, disclaimers), the
    voice interview (the ten 05 questions → the gateway's `voice_profile` prompt → you review and
    save), and a review with the completeness score and the summary the agent reads. Each step saves
    through 05 `PUT /brand/editable`; 05 validates every field and its errors appear next to the
    field (htmx; works without JavaScript too). The agent uses a change within 60 s (the gateway's
    brand cache). Reset (step 6) goes back to the shipped `brand.yaml`.
12. **Positioning** (`/positioning`, under More): 78's monthly positioning map. A table of themes ×
    brands (the number = how many exact quotes claim that theme; open it to read the quotes with links
    to their page or ad), the white space (no competitor claims it and an approved fact backs it),
    crowded themes and the shifts since last month, with older months as tabs. Read-only.
13. **Product feed** (`/feeds`, under More): the uploads to 87 feed-optimizer with their counts
    (proposed, rejected by reason, approved) and the downloads (full feed, supplemental feed),
    passed through with the service key. Read-only: approving needs the approver key on 87.
14. **Activity** (`/activity`, in the top bar and the phone tab bar): what the agent is doing now
    and what it did. Read-only. It refreshes every 4 s while the tab is visible and pauses when
    it is hidden.
    - **Now**: the model calls running at this moment, with the ability that asked (e.g. "Social
      post writer"), the prompt, the model, a **local** or **hosted** badge and the seconds so far.
      "Idle — nothing running" when there are none.
    - **Timeline**: newest first, grouped by day. AI calls ("Social post writer asked mkt-writer
      (local) for social posts — 14.2 s, OK") and content changes from the calendar (19: drafts
      created, sent for review, approved, rejected, published; last `ACTIVITY_DAYS` days) with a
      link to the item. Filters: All, AI calls, Content, Errors.
    - **Models**: one card per model today (UTC): calls, failures, average and p95 time, tokens in
      and out, and a small line of the last 30 call times.
    - **Abilities**: a tile for everything the agent can do (writers, scheduled jobs, checks,
      services), installed or not (from the install profile and the service URLs), and when it
      last called a model. Open a tile to see only its calls.
    - A small row shows each source (each gateway, the calendar). One that fails says
      "unreachable"; the rest of the page still works.

    The data comes from each gateway's `GET /v1/activity` (03; metadata only, never prompt text,
    vars or output; kept in memory, so a gateway restart empties it) and from 19 `GET /items`.
    Calls show which ability made them through the `X-Caller` header that every workflow and
    service sends. Gateways appear by label ("main", "verifier", "assistant"), never by URL.
    The chat agent (24) calls its model through the gateway too (03 `/v1/chat/completions`), so
    each model step of a chat shows as "Chat agent asked mkt-agent (local) for a chat step". It
    does not with `CHAT_PROVIDER=direct` or `hosted`, which bypass the gateway.

15. **Facts** (`/facts`, in the top bar and under More): the business's facts from 05 (v2), grouped
    by what they are about. Each shows its status (active, **draft: needs your confirmation**,
    expired, expires soon, due for review, superseded, retired), where it applies in words ("only:
    Quayside branch; delivery channel"), its dates, who may see it (public / internal: only a
    placeholder leaves / restricted: never leaves), source and owner. Filters: needs you, to
    confirm, expiring in 30 days, expired, due for review, open questions. **Add / Edit** is one
    form: the common fields first (the sentence, what it is about, kind, value as written, dates,
    who may see it, where it applies) and "More details" for the rest (key, value, unit, currency,
    per, conditions as rows with "Add another condition", review date, source, kind of claim, risk,
    evidence, must-say / good / never-say wording). Saving makes a **draft**; **Confirm** and
    **Retire** are buttons on the list. Facts from `brand.yaml` show as read-only, with **Turn into
    a scoped fact** (a new draft with `supersedes_key`; confirm it to replace the old one). **Starter
    kits** (`/facts/kits`): pick your kind of business, preview the rules (words to avoid and why,
    what must always be said, facts worth adding), apply them as draft rules, then confirm or
    dismiss each. **Open questions** (from tasks) can be answered or dismissed.
    **Set up from your website or documents** (`/facts/setup`, on the Facts page and under More):
    a new business gets a first fact base without typing every fact. Give up to 5 web addresses
    (a page, or a link to a PDF), up to 5 PDFs (brochure, price list, menu; 10 MB and 50 pages
    each; read by 07, page by page) and/or pasted text, and pick the type of business (05's
    starter kits). The text is cut into parts of about 4,200 characters (so each copy box, with the instructions and known facts, stays within the 8,000 characters a free chat handles well) (a PDF part marks its
    pages). Two ways to get proposals: **the model** (03 `/v1/run`, prompt `propose_facts`, 3
    parts per click; offered only when the gateway answers and has that prompt, labelled "uses
    your local model" or "uses your hosted model: the source text goes to that provider") or **any
    free chatbot** at zero cost: per part, a readonly box to copy (instructions, your *public*
    facts to skip, the part, and the exact line format
    `FACT | subject kind | subject | type | value as written | scope | valid from | valid to | quote: "..."`
    plus `QUESTION | ... | quote: "..."`), then a box to paste the chatbot's answer back. The
    answer is read tolerantly (preamble and sign-off, code fences, bullets, numbering, bold,
    markdown tables, smart quotes, a missing `quote:`, `-` / `n/a` cells, escaped or full-width
    bars, tabs). **Whoever wrote a proposal, the control room checks it**: its quote must occur in
    the source (whitespace, case, quote marks and dashes normalised; a quote shortened with "..."
    is refused), every number of the value must be in the quote and every number of the sentence
    in the source page, a date is kept only if the quote states it, and a proposal that repeats a
    fact you have (any status) or an earlier proposal is set aside. The review shows each
    proposal with **the quote highlighted in its place**, where it came from (`rates.pdf p.2`),
    the value, the suggested scope in words, editable dates and wording, and **who may see it:
    internal until you choose public**; set-aside ones are listed with the reason. Tick what to
    keep → 05 `POST /facts/v2` as **drafts** (source `{kind: url|doc, ref: "where: \"quote\""}`),
    ticked questions ("airport transfers available: price?") → 05 `POST /questions`, then the
    Facts page opens on "To confirm". **Nothing is confirmed here and the owner key is never sent**:
    confirming stays the Confirm button on the Facts page. The source text lives in the control
    room's memory for 6 hours (a restart means starting again).
16. **Tasks** (`/tasks`, in the top bar and under More): write with any AI chat without handing it
    your business (88 task-bridge). **New task**: the goal, the pieces (channel + kind + optional
    length; "Add another piece" / "Remove" work without JavaScript), the publish date, who it is
    for, and "only for" pickers built from the sites, regions, channels, customer groups, plan
    tiers and variants your facts name. **The pack page shows the data-sharing preview first**:
    "This leaves your business: N fact lines" (exactly 88's `share_preview.sent`), the placeholders
    whose value is never sent, and what is held back and why. Then the pack (a readonly box, with
    a Copy button when JavaScript is on), "which AI will you use?" and the paste box. After
    pasting you see how the answer was split (the removed preamble and sign-off shown struck out)
    and can put text in the right piece yourself; **nothing reaches the calendar until you
    submit**. After submitting, each piece shows its filled text and evidence per sentence:
    **matches** (with the fact's quote), **needs your judgement**, **conflicting or expired**,
    **wrong scope**, **no source**, **missing disclosure**, **forbidden wording**, **blocked
    placeholder**, each with the fact key and a one-line why, and a link to the calendar item it
    made. **Download** (Markdown or text) appears only when 88 says every piece is approved and
    its facts still hold; otherwise the page lists what is missing.
17. **Blockers** (`/blockers`, under More, and one compact line at the top of the queue): e.g. "2
    posts need a price confirmation · 1 fact expired in use · 3 enquiries waiting", with links.
    Queue cards of items that came from a task show the evidence chips and "from task T-…".
18. **Results** (`/tasks/results`, from Tasks and under More): "is this saving us time, and which
    AI works best for us?", from what 88 and 19 already record (nothing new is stored; the reads
    are cached for 60 s and cover the newest 200 tasks), for the last 30 days or all. Time from a
    task's creation to its **first usable draft** (the first submit with no piece blocked); **pastes**
    per task (a hand split is not a paste); pieces **blocked on first paste** and why (evidence
    labels, and brand / channel rule errors while that first submit is still the piece's current
    one); **waiting for approval** (19 audit: entered in_review → approved); **reviewer edits** (19
    versions written while in_review); pieces **exported**; **facts changed after approval** (88
    `fact_changed` from approved). **By AI** (the AI chosen when pasting; a piece counts for the AI
    whose answer was first submitted for it): pieces, blocked-on-first-paste and approved as
    "k of n (p%)", median edits; under 10 pieces an AI says "too few to compare" and nothing is
    ranked unless two or more AIs have 10+. **Cost**: pasted drafts are "model calls: 0"; model
    calls and time for AI-assisted steps (88 model check, 44, onboarding, voice) come from the
    gateways' recent activity log, or "not measured". Your own time is not measured (a "minutes
    spent" field would need storage in 88; later).

The n8n forms (38 approval, 42 knowledge, 51 rules) stay; use either.

## How a decision is applied

The control room does not change a status itself and **does not hold `APPROVER_KEY`**. It posts
decisions to n8n:

```text
browser --cookie + CSRF--> control room --X-Control-Key--> n8n webhook mkt-apply-decisions
        (after the Undo window)                            (n8n/workflow.json, 72 · Control room · Apply decisions)
```

That workflow runs **the same code as the approval form (38)**: the generator builds both from
one shared constant (`DECISIONS_CORE`) and one shared chain of nodes (`decision_tail`), and a
test runs both workflows' code on the same decisions and compares the results. So an approve,
edit & approve, reject – rewrite, reject – drop or back to draft does exactly what the form does:

| Decision | Calendar (19) | Learning (46) | Engine (61) | Other |
|---|---|---|---|---|
| Approve | publish time set (given, the item's own, or the next full hour), `in_review → approved` | `approved` event | outcome for engine items | |
| Edit & approve | new text + time, then `approved` | `edited` event (draft and your final) | outcome | a video script is rendered again (71) first; if it can't be, the edit is saved and the item goes back to `draft` |
| Reject – rewrite it | `rejected`, then `draft` | `rejected` event with the reason | outcome | the reviser (49) rewrites it (needs a reason) |
| Reject – drop it | `rejected` | `rejected` event | outcome | |
| Back to draft | `draft` | — | — | |
| Skip | nothing (the card moves to the end) | — | — | |

Only items that are `in_review` when the decision arrives are changed; the others are reported
back ("Not in review any more"). The result of each batch appears above the queue.

### Approval is bound to the text you saw

19 gives every item a `body_sha256` (the hash of its text) and keeps every version and an
audit log (see 19's README). The hash travels from your phone to 19:

1. Each card (and the edit page) carries the item's `body_sha256` in a hidden `seen` field.
2. `/decide` accepts `seen` (64 lower-case hex characters, or empty; anything else is `422`)
   and sends it to n8n as `seen_sha256` with the decision.
3. The webhook checks it again and passes it on: **Approve** sends `expected_sha256: seen` to
   19's status call (and `if_match_sha256` on the schedule PATCH); **Edit & approve** PATCHes
   the new text with `if_match_sha256: seen`, then approves with `expected_body` = your text.
4. If the text changed after the card was rendered (a rewrite, another reviewer's edit), 19
   answers `409 changed since you looked` and changes nothing. The result above the queue then
   shows `#N: NOT approved: changed since you looked — reopen the card` for that item only;
   the other decisions in the batch go through, and no learning event is logged for it.

A card rendered before 19 had hashes (or by an older control room) sends no `seen`; the webhook
then uses the hash of the item as it fetches it when the decision arrives, which protects less.
The approval form (38) has no `seen` field: it uses the hash of each item as fetched when the
form page was rendered, which is the text shown on that page. Items that 19 marks
`require_bound` (from the task bridge, 88) cannot be approved without a hash (`428`, shown the
same way).

## Security

- The browser gets an **HttpOnly, SameSite=Strict** session cookie (**Secure** when the request
  came over HTTPS, or always with `COOKIE_SECURE=true`) and a CSRF token that every POST must
  carry (htmx sends it as `X-CSRF-Token`). A POST without it is refused with `403`.
- Login: constant-time comparison of user and password (with a users file: one scrypt compare
  per attempt, also for an unknown name); after `LOGIN_MAX_FAILURES` (5) failures
  within `LOGIN_WINDOW_MINUTES` (15) that address gets `429` (and all logins do after 4× that
  from everywhere). A new session id at every login; sessions end after
  `SESSION_IDLE_MINUTES` (120) idle or `SESSION_HOURS` (12) in total. Sessions live in memory:
  a restart logs you out, and the app runs as **one** worker.
- Keys (`CONTROL_ROOM_KEY`, `INTERNAL_API_KEY`) and internal URLs never reach the browser; a test
  renders every page, fragment, JSON answer and static file and searches them for the key values
  and the internal host names.
- Which call carries which key (`app/backends.py`): reads carry none; the decision webhook gets
  `X-Control-Key` only; moving an item on the calendar (`PATCH scheduled_at`, 19), resuming a
  pillar (61), reading/saving/resetting the editable brand and saving the voice profile (05), and
  the voice interview prompt (03) and reading each gateway's activity log (03 `/v1/activity`)
  get `X-API-Key`. The facts pages (05 `/facts/v2`, `/facts/query`, `/questions`, `/starter-kits`,
  `/rules`) and every task-bridge call (88) get `X-API-Key`. Nothing gets `X-Approver-Key`.
- **The owner-key rule.** `FACT_OWNER_KEY` is held by 05 and the control room only. The control
  room sends it (`X-Owner-Key`) on exactly these calls: confirm or retire a fact, import facts,
  apply a starter kit, confirm or dismiss a kit rule, confirm or dismiss a learned disclosure
  wording. Adding or editing a fact never sends it, so an
  edit is always a draft until a person presses Confirm; AI output never becomes a fact on its
  own. The key is never rendered (tests search every page for it). Unset = those buttons are off
  and the control room refuses the call itself, before asking 05.
- Images (17) and videos (71) are streamed through `/media/...` (login required, ids checked,
  `Range` passed on), so a phone on HTTPS never needs ports 8117/8171.
- Strict headers: CSP without inline scripts or eval (`script-src 'self'`), `frame-ancestors
  'none'`, `no-store` on pages, HSTS over HTTPS.

### People and roles

Without a users file nothing changes: `CONTROL_USER` / `CONTROL_PASSWORD` is the only login, and
that person is the **owner** (named `CONTROL_REVIEWER`, or `CONTROL_USER`, on decisions).

To give several people their own login, add them to the users file (`CONTROL_USERS_FILE`,
default `/data/users.json` when `/data` exists). **Add yourself as owner first**: once the file
exists, it alone decides who can log in and `CONTROL_PASSWORD` stops working.

```bash
docker compose exec control-room python -m app.users add alex owner --display "Alex"
docker compose exec control-room python -m app.users add sam approver --display "Sam Parker"
docker compose exec control-room python -m app.users add sara writer --display "Sara"
docker compose exec control-room python -m app.users list
docker compose exec control-room python -m app.users passwd sam
docker compose exec control-room python -m app.users remove sara
```

The password is asked twice and never echoed (or read from stdin with `--password-stdin`; at
least 8 characters). The file holds only scrypt hashes (`scrypt$<salt>$<hash>`, n=2^14, r=8, p=1,
16-byte salt), is written atomically with mode 600, and is read again when it changes: a new
person can log in at once, a removed person is logged out at their next click, and a changed role
applies at the next request. No restart is needed. The last owner can't be removed. A file that
exists but can't be read refuses every login (it never falls back to `CONTROL_PASSWORD`).

Keep the file on a volume, or it is lost when the container is recreated: mount one at `/data`
(the image creates `/data` owned by the app user), e.g. in the stack's compose file
`volumes: ["control-room-data:/data"]`.

| Role | May |
|---|---|
| **writer** | read every page; write drafts: new tasks, paste, split, submit; add, edit and convert facts; answer questions; onboarding |
| **approver** | everything a writer may, and: approve / edit / reject in the queue, undo, move items on the calendar, resume a pillar, accept a finding ("it's there, in other words"), make and withdraw client links |
| **owner** | everything, and: confirm / retire facts, apply starter kits, confirm / dismiss kit rules and learned wordings, change the brand setup (including reset and the voice profile) |

The server enforces this on every POST (`403`: "Your role (writer) can't do this; ask the
owner."); the pages also hide buttons a role can't use. Every page shows who is signed in. The
person's display name is what other services are told: the `reviewer` of each decision batch
(decisions made by different people are sent in separate batches), `X-Actor` on calls to 05 and
on client links (accents folded to ASCII for the header), and `by` on an accepted finding (88).

### Client approval links (`/c/...`, no login)

An agency sends posts to its client for sign-off: **More → Client links → New link** (or "Send to
client" on a queue card / "Send to client for sign-off" on an item) → pick posts, a name and how
long it works (1, 3, 7, 14 or 30 days) → the page shows the link `CONTROL_PUBLIC_URL/c/<token>`
and a generated 6-digit PIN **once**. Neither is stored readable anywhere: 19 keeps a sha256 of
the token and an HMAC of the PIN keyed with the token; the control room keeps nothing. Lost one?
Withdraw it on **Client links** and make a new one.

- **Link + PIN are the identity check.** Send them by different routes (link by email, PIN by text
  or a call). They prove who had both, **not** a legal signature.
- The client's page shows the brand name, each post exactly as it will go out (text, image or
  video, link), the evidence **labels** (e.g. "matches", "no source"; no fact keys, quotes, flag
  wording or notes) and, per post, **Approve** / **Request changes** with their name (required)
  and a comment (required for changes). Nothing internal: no notes, keys, internal URLs, the
  owner's user name, or posts outside the link. Images and videos are streamed through
  `/c/m/...` only for that link's posts.
- **Approve** records the client's sign-off (19 audit `client_approved` + note "client approved by
  X (vN)"); it never approves the post. You still approve in the queue, bound to the text as usual.
  **Request changes** records it and moves an `in_review` (or `approved`) post back to draft, so it
  can't go out as it is. An answer carries the hash of the text the client saw; if the text changed
  since, 19 refuses (`409`) and the client sees the new text. Queue cards show "client approved by
  X" / "client asked for changes (X)", marked "earlier version" when the text changed since.
- The client page has no owner session and no owner powers: every read and answer goes through
  19 with the token + PIN (`X-API-Key` added here). After the PIN, a short client session (cookie
  `cr_client`, `Path=/c/`, HttpOnly, SameSite=Strict, `CLIENT_SESSION_MINUTES` idle) holds the
  token and PIN in memory for that one link. CSRF: the PIN form uses a per-page cookie token like
  the login form; each answer form carries the client session's token.
- Wrong PINs: 19 locks a link after 5 (then only a new link helps); here each address gets
  `CLIENT_PIN_MAX_FAILURES` (10) wrong PINs or unknown links per `CLIENT_PIN_WINDOW_MINUTES` (15),
  then `429`. Client pages are `no-store`, `X-Robots-Tag: noindex` and `Referrer-Policy: no-referrer`.
- The token is part of the URL path. The control room's own access log writes it as `/c/***`
  (`app/logredact.py`), but a reverse proxy in front logs its own copy: turn off or mask that
  path there too (Caddy: a `log` block that skips `/c/*`). The PIN is never in a URL.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing` network
(service `control-room`, `127.0.0.1:8172`). It keeps no state on disk. It calls n8n and the
services by their container names.

`01-marketing-stack/scripts/install.sh` does all of this (every profile includes the control
room); the steps below are for setting it up by hand.

1. In `01-marketing-stack/.env`: `CONTROL_USER`, `CONTROL_PASSWORD` (required),
   `CONTROL_ROOM_KEY` (required; `openssl rand -hex 24`; `preflight.sh` fails if it equals
   another key). `install.sh` generates the password and the key. n8n gets the same
   `CONTROL_ROOM_KEY` from the stack.
2. `docker compose up -d --build control-room n8n`, then `scripts/import-n8n.sh` (it imports
   `72-control-room/n8n/workflow.json` with the other workflows and publishes it).
3. Open <http://localhost:8172>. Brand setup is under **More** on a phone and **Brand** in the
   top bar on a computer.

**On a phone:** the port is bound to localhost on purpose. Put it behind your HTTPS reverse proxy
and set `CONTROL_COOKIE_SECURE=true` in the stack's `.env`, for example with Caddy:

```text
review.example.com {
    reverse_proxy 127.0.0.1:8172
}
```

or reach the host over a private network (Tailscale, WireGuard) with HTTPS. Don't expose it over
plain HTTP. Add it to your home screen (it has a web app manifest).

## Run

```bash
docker build -t control-room .
docker run --rm -p 127.0.0.1:8172:8000 --network marketing-agent_marketing \
  -e CONTROL_PASSWORD=... -e CONTROL_ROOM_KEY=... -e INTERNAL_API_KEY=... control-room
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
CONTROL_PASSWORD=... CONTROL_ROOM_KEY=... INTERNAL_API_KEY=... N8N_BASE_URL=http://localhost:5678 \
  CALENDAR_URL=http://localhost:8119 CAMPAIGNS_URL=http://localhost:8145 LEARNING_URL=http://localhost:8146 \
  ENGINE_URL=http://localhost:8161 RULES_URL=http://localhost:8114 STATUS_URL=http://localhost:8122 \
  CARDS_URL=http://localhost:8117 VIDEO_URL=http://localhost:8171 \
  uvicorn app.main:app --port 8172
```

## Configuration

In the stack, `docker-compose.yml` passes `CONTROL_USER`, `CONTROL_PASSWORD`, `CONTROL_ROOM_KEY`,
`INTERNAL_API_KEY`, `N8N_PUBLIC_URL`, the service URLs, and these with a `CONTROL_` prefix in
`.env`: `CONTROL_COOKIE_SECURE`, `CONTROL_TRUSTED_PROXIES`, `CONTROL_UNDO_SECONDS`,
`CONTROL_SESSION_HOURS`, `CONTROL_SESSION_IDLE_MINUTES`. The other settings below keep their
defaults there.

| Env var | Default | Meaning |
|---|---|---|
| `CONTROL_USER` | `approver` | Login name |
| `CONTROL_PASSWORD` | — | **Required** without a users file. Without either nobody can log in (`503`) |
| `CONTROL_USERS_FILE` | `/data/users.json` if `/data` exists, else none | Named users and roles (see *People and roles*); set it empty to force the single `CONTROL_USER` login |
| `TRUSTED_PROXIES` | empty | comma-separated IPs/CIDRs of your reverse proxy (e.g. `172.16.0.0/12` for the Docker network). Only then is `X-Forwarded-For` used for login rate limits, so one visitor's wrong passwords don't lock you out; without it every request behind a proxy counts as the proxy's address |
| `CONTROL_REVIEWER` | `CONTROL_USER` | Name in the notes and learning events ("approved by …") for the single `CONTROL_USER` login; with a users file each person's display name is used |
| `CONTROL_ROOM_KEY` | — | Sent as `X-Control-Key` to the n8n webhook; n8n must have the same value (at least 16 characters, or the webhook refuses everything) |
| `INTERNAL_API_KEY` | — | Moving calendar items (19) and resuming pillars (61) |
| `N8N_BASE_URL` | `http://n8n:5678` | n8n inside the network (the webhook) |
| `APPROVAL_URL` | empty | 90 approval service. Set (the stack uses `http://approval-service:8000` when `CONTROL_APPROVAL_URL` is set) = decisions go there instead of the n8n webhook, with the same payload and `X-Control-Key`; the control room still never holds the approver key |
| `N8N_PUBLIC_URL` | `http://localhost:5678` | n8n as your browser reaches it (the chat link) |
| `CALENDAR_URL` `CAMPAIGNS_URL` `LEARNING_URL` `ENGINE_URL` `RULES_URL` `STATUS_URL` `CARDS_URL` `VIDEO_URL` | the stack's container names | Services 19, 45, 46, 61, 14, 22, 17, 71 |
| `ADS_URL` | empty (panel says "not installed") | 84 ads-sync, e.g. `http://ads-sync:8000` |
| `REPORT_URL` | `http://report-builder:8000` | 21 report-builder for **Download**; empty = not installed |
| `BRAND_URL` / `GATEWAY_URL` | `http://brand-service:8000` / `http://llm-gateway:8000` | Brand setup: 05 and 03 |
| `AD_LIBRARY_URL` | `http://ad-library-sync:8000` | Positioning page: 78 (`GET /positioning…`, no key). Empty = not installed |
| `FEED_URL` | empty (page says "not installed") | 87 feed-optimizer, e.g. `http://feed-optimizer:8000` (full profile) |
| `TASKS_URL` | `http://task-bridge:8000` | 88 task-bridge: Tasks and Blockers pages, the queue's blockers line. Empty = not installed |
| `EXTRACTOR_URL` | `http://page-extractor:8000` | 07 page-extractor: web pages and PDFs on the onboarding page. Empty = paste text only |
| `FACT_OWNER_KEY` | empty (confirm buttons off) | Sent as `X-Owner-Key` to 05 only to confirm/retire facts, import, apply a starter kit and confirm/dismiss its rules. Must equal 05's value. Never shown |
| `ACTIVITY_GATEWAYS` | empty | more gateways for the Activity page, comma-separated, `label=url` or just `url`; an empty url is skipped. `GATEWAY_URL` is always read as "main". The stack passes `verifier=http://llm-gateway-verifier:8000,assistant=${ASSISTANT_GATEWAY_URL}` |
| `INSTALL_PROFILE` | `full` | `core`, `growth` or `full` (the stack passes `COMPOSE_PROFILES`; the largest wins). Which ability tiles say "installed" |
| `ACTIVITY_DAYS` | `7` | how far back the Activity timeline reads calendar changes |
| `VOICE_TIMEOUT_SECONDS` | `300` | How long to wait for the model to write the voice profile |
| `COOKIE_SECURE` | `auto` | `auto`: Secure when the request is HTTPS (needs the proxy's `X-Forwarded-Proto` trusted, `FORWARDED_ALLOW_IPS`); `true` behind HTTPS |
| `UNDO_SECONDS` | `5` | How long a decision waits before it is sent |
| `SESSION_HOURS` / `SESSION_IDLE_MINUTES` | `12` / `120` | Session limits |
| `LOGIN_MAX_FAILURES` / `LOGIN_WINDOW_MINUTES` | `5` / `15` | Login rate limit |
| `CONTROL_PUBLIC_URL` | empty (the address of the request) | The address clients open, for client links, e.g. `https://review.agency.example` |
| `CLIENT_PIN_MAX_FAILURES` / `CLIENT_PIN_WINDOW_MINUTES` | `10` / `15` | Wrong PINs or unknown client links per address before `429` |
| `CLIENT_SESSION_MINUTES` | `30` | A client re-enters the PIN after this long idle (4× in total) |
| `DECISION_TIMEOUT_SECONDS` | `600` | How long to wait for n8n (a re-rendered video can take minutes) |

## Pages and endpoints

| Path | What |
|---|---|
| `GET /health` | `{"status":"ok"}` (no login; the container health check) |
| `GET /login`, `POST /login`, `POST /logout` | Session |
| `GET /` (`?tab=flagged`) | Review queue |
| `POST /decide` | `id`, `decision` (`approve` `edit` `reject_rewrite` `reject_drop` `back_to_draft`), `text`, `reason`, `publish_at` → queued for `UNDO_SECONDS` |
| `POST /undo/{token}` | Cancels a queued decision; `409` once it is being sent |
| `GET /results` | The last results from n8n (the queue polls it) |
| `GET /items/{id}`, `GET /items/{id}/edit` | Detail; edit & approve |
| `GET /items/{id}/download` | The item's body as one HTML file (21 `/document`), e.g. a client report (85) to forward |
| `GET /calendar`, `GET /calendar/events?start=&end=`, `POST /calendar/reschedule` | Calendar, its JSON feed, drag to move (`{id, start}`; idea/draft/in_review/approved only) |
| `GET /performance?days=7\|30\|90`, `/engine`, `POST /engine/{id}/resume`, `/campaigns`, `/chat`, `/status` | Dashboards |
| `GET /media/cards/{id}.png`, `/media/videos/{id}.mp4\|jpg` | Images and videos, streamed from 17 / 71 |
| `GET /brand`, `GET /brand/step/{1-6}`, `POST /brand/step/{1-4}` | Brand setup; a POST saves that step (htmx gets the form back with errors or "Saved") |
| `GET /positioning?id=&theme=&brand=` | Positioning map (78): a month (default latest), and one cell's quotes |
| `GET /feeds`, `GET /feeds/{id}/export.csv\|tsv`, `/feeds/{id}/supplemental.csv\|tsv` | Product feed (87): uploads with counts, and the downloads |
| `GET /activity?filter=all\|ai\|content\|errors&ability=NN` | Activity page (works without JavaScript; the filters are links) |
| `GET /activity/data` (same params) | The page's data as JSON: `{updated_at, profile, sources, now, timeline, models, abilities}`; `401` JSON when logged out |
| `POST /brand/voice/generate`, `POST /brand/voice/save`, `POST /brand/reset` | Voice interview → profile to review → save (05 `PUT /voice`); reset needs `confirm=yes` |
| `GET /facts?show=all\|attention\|drafts\|expiring\|expired\|due\|questions\|wordings` | Facts (05 v2), grouped by subject, with open questions and learned wordings to confirm (05 `GET /disclosure-wordings?status=draft`) |
| `POST /facts/wordings/{id}/confirm\|dismiss` | Owner only, owner key (05 `/disclosure-wordings/{id}/confirm\|dismiss`) |
| `GET /facts/new`, `POST /facts/new`, `GET/POST /facts/{key}/edit` | Add / edit a fact (a draft; 05 `POST`/`PUT /facts/v2`) |
| `POST /facts/{key}/confirm\|retire` | Owner key (05 `/facts/v2/{key}/confirm\|retire`) |
| `POST /facts/{key}/convert` | A brand-profile fact → a new draft with `supersedes_key` |
| `GET /facts/kits?kit=`, `POST /facts/kits/{id}/apply`, `POST /facts/rules/{id}/confirm\|dismiss` | Starter kits: preview, apply (owner key), confirm/dismiss each rule (owner key) |
| `POST /facts/questions/{id}/answer\|dismiss` | Open questions (05 `/questions`) |
| `GET /facts/setup`, `POST /facts/setup` (multipart: `urls`, `pdfs`, `text`, `business_type`) | Onboarding: sources through 07 → a run (memory only) |
| `GET /facts/setup/{run}`, `POST /facts/setup/{run}/paste` (`chunk`, `answer`), `POST /facts/setup/{run}/model` | The parts: copyable packs + paste boxes, or the model (03 `propose_facts`) |
| `GET /facts/setup/{run}/review`, `POST /facts/setup/{run}/save` (`keep_N`, `text_N`, `sens_N`, `from_N`, `to_N`, `ask_N`) | Verified proposals → 05 drafts and open questions (never confirmed here) |
| `GET /tasks`, `GET /tasks/new`, `POST /tasks/new` | Tasks (88); the form's `action=add` / `remove=N` add or remove a piece |
| `GET /tasks/{id}/pack`, `POST /tasks/{id}/paste` | Data-sharing preview, pack, paste box → the split |
| `POST /tasks/{id}/split`, `POST /tasks/{id}/submit` | Manual split; submit → evidence and calendar items |
| `GET /tasks/{id}`, `GET /tasks/{id}/export?format=md\|txt` | Pieces with evidence; download (88 refuses with `409` until ready) |
| `POST /tasks/{id}/accept` (`piece_key`, `finding`, `sha`, `note`, optional `wording`) | Accept a missing-disclosure finding (88); a `wording` (3–200 characters, copied from the post) is saved as a draft for the owner to confirm |
| `GET /blockers` | Blockers (88 `/blockers`) |
| `GET /client-links`, `GET /client-links/new?items=N`, `POST /client-links/new` (`item_id`…, `label`, `days`), `POST /client-links/{id}/revoke` | Client links (19 `/client-links`): list, make (link + PIN shown once), withdraw |
| `GET /c/{token}`, `POST /c/{token}` (`pin`), `POST /c/{token}/respond` (`item_id`, `body_sha256`, `decision` approve\|changes, `name`, `comment`), `GET /c/m/{kind}/{name}` | **No login.** The client's PIN form, posts and answers (19 `resolve` / `respond`), and that link's media |
| `GET /tasks/results?range=30d\|all` | Results (88 `GET /tasks?limit=200`, `GET /tasks/{id}`; 19 `GET /items/{id}/audit\|versions`; gateway activity), cached 60 s |

## The n8n workflow (`n8n/workflow.json`)

`72 · Control room · Apply decisions`, id `mktWf72ApplyDeci`, generated with the other workflows
(don't edit it by hand). `POST <n8n>/webhook/mkt-apply-decisions` with header `X-Control-Key`:

```json
{"reviewer": "alex", "decisions": [{"id": 12, "decision": "edit", "text": "…", "reason": "shorter", "publish_at": "2026-10-03 08:30"}]}
```

→ `{"ok", "summary": ["#12: edited and approved for 2026-10-03 08:30 UTC"], "not_in_review": [], "failed": [], "message"}`.
A wrong or missing key → `401`; an invalid body → `422`. It needs `CONTROL_ROOM_KEY`,
`CALENDAR_URL`, `LEARNING_URL`, `APPROVER_KEY` (and `ENGINE_URL`, `VIDEO_URL`, `BRAND_URL` as for
form 38) in n8n's environment; failures go to `43-wf-error-handler`.

## Chat

The chat page links to n8n's own chat (`<N8N_PUBLIC_URL>/webhook/mkt-marketing-chat/chat`, n8n
login). n8n's chat widget is under the Sustainable Use License, so it can't be copied into this
repo, and proxying the streaming chat webhook through the control room is a separate piece of
work. The agent can't approve anything in either place.

## Front-end libraries

No build step: Jinja pages with [htmx](https://htmx.org) 2.0.11 (0BSD),
[FullCalendar](https://fullcalendar.io) 6.1.21 standard bundle (MIT: month, week, list, drag) and
[Chart.js](https://www.chartjs.org) 4.5.1 (MIT). They are **vendored** unmodified in
`app/static/vendor/` (works offline, no CDN), each with its licence file next to it
(`htmx.LICENSE`, `fullcalendar/LICENSE.md`, `chart.js.LICENSE.md`); the FullCalendar and Chart.js
files keep their licence headers. SHA-256 of the files as downloaded from npm:

| File | SHA-256 |
|---|---|
| `htmx.min.js` | `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717` |
| `fullcalendar/index.global.min.js` | `4e72ff169af857bbb8fee626364949d570424b00df36c01bd087a9580371515e` |
| `chart.umd.min.js` | `48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a` |

No other outside code is in this repo.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on every
push to `main`.

## Audit: is your public content still true?

**More → Audit** compares what your own web pages, PDFs or old posts say with today's facts. Paste
URLs (one per line; the task bridge reads them through the page extractor, 07) or paste text, pick
the scope, and you get a table: *where* · *it says* · *the facts say* · *why*. Only contradictions are
listed: an old or wrong price, another branch's fact, an expired offer, or an internal value that must
never be public. Nothing is changed anywhere. Writers and up can run it; image-only PDFs (scanned rate
cards) can't be read — the page says so.
