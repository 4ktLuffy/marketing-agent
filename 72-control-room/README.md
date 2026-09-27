# control-room

Deploy **72 of 83** of the local-LLM marketing agent. A small, mobile-first web app for the one
person who approves the agent's work:

1. **Login** for one approver (`CONTROL_USER` / `CONTROL_PASSWORD`).
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
   `/insights/hooks`).
7. **Content engine**: each pillar's health and pause state, with a **Resume** button (61).
8. **Campaigns**: scorecards (target vs actual vs time elapsed) and campaigns that ended unmeasured (45).
9. **Chat**: a link to the n8n chat (see *Chat* below for why it is a link).
10. **Health**: the status page's summary (22).

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

## Security

- The browser gets an **HttpOnly, SameSite=Strict** session cookie (**Secure** when the request
  came over HTTPS, or always with `COOKIE_SECURE=true`) and a CSRF token that every POST must
  carry (htmx sends it as `X-CSRF-Token`). A POST without it is refused with `403`.
- Login: constant-time comparison of user and password; after `LOGIN_MAX_FAILURES` (5) failures
  within `LOGIN_WINDOW_MINUTES` (15) that address gets `429` (and all logins do after 4× that
  from everywhere). A new session id at every login; sessions end after
  `SESSION_IDLE_MINUTES` (120) idle or `SESSION_HOURS` (12) in total. Sessions live in memory:
  a restart logs you out, and the app runs as **one** worker.
- Keys (`CONTROL_ROOM_KEY`, `INTERNAL_API_KEY`) and internal URLs never reach the browser; a test
  renders every page, fragment, JSON answer and static file and searches them for the key values
  and the internal host names.
- Which call carries which key (`app/backends.py`): reads carry none; the decision webhook gets
  `X-Control-Key` only; moving an item on the calendar (`PATCH scheduled_at`, 19) and resuming a
  pillar (61) get `X-API-Key`. Nothing gets `X-Approver-Key`.
- Images (17) and videos (71) are streamed through `/media/...` (login required, ids checked,
  `Range` passed on), so a phone on HTTPS never needs ports 8117/8171.
- Strict headers: CSP without inline scripts or eval (`script-src 'self'`), `frame-ancestors
  'none'`, `no-store` on pages, HSTS over HTTPS.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing` network
(service `control-room`, `127.0.0.1:8172`). It keeps no state on disk. It calls n8n and the
services by their container names.

1. In `01-marketing-stack/.env`: `CONTROL_USER`, `CONTROL_PASSWORD` (required),
   `CONTROL_ROOM_KEY` (required; `openssl rand -hex 24`; `preflight.sh` fails if it equals
   another key). n8n gets the same `CONTROL_ROOM_KEY` from the stack.
2. `docker compose up -d --build control-room n8n`, then `scripts/import-n8n.sh` (it imports
   `72-control-room/n8n/workflow.json` with the other workflows and publishes it).
3. Open <http://localhost:8172>.

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
docker run --rm -p 127.0.0.1:8172:8000 --network marketing \
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

| Env var | Default | Meaning |
|---|---|---|
| `CONTROL_USER` | `approver` | Login name |
| `CONTROL_PASSWORD` | — | **Required.** Without it nobody can log in (`503`) |
| `CONTROL_REVIEWER` | `CONTROL_USER` | Name in the notes and learning events ("approved by …") |
| `CONTROL_ROOM_KEY` | — | Sent as `X-Control-Key` to the n8n webhook; n8n must have the same value (at least 16 characters, or the webhook refuses everything) |
| `INTERNAL_API_KEY` | — | Moving calendar items (19) and resuming pillars (61) |
| `N8N_BASE_URL` | `http://n8n:5678` | n8n inside the network (the webhook) |
| `N8N_PUBLIC_URL` | `http://localhost:5678` | n8n as your browser reaches it (the chat link) |
| `CALENDAR_URL` `CAMPAIGNS_URL` `LEARNING_URL` `ENGINE_URL` `RULES_URL` `STATUS_URL` `CARDS_URL` `VIDEO_URL` | the stack's container names | Services 19, 45, 46, 61, 14, 22, 17, 71 |
| `COOKIE_SECURE` | `auto` | `auto`: Secure when the request is HTTPS (needs the proxy's `X-Forwarded-Proto` trusted, `FORWARDED_ALLOW_IPS`); `true` behind HTTPS |
| `UNDO_SECONDS` | `5` | How long a decision waits before it is sent |
| `SESSION_HOURS` / `SESSION_IDLE_MINUTES` | `12` / `120` | Session limits |
| `LOGIN_MAX_FAILURES` / `LOGIN_WINDOW_MINUTES` | `5` / `15` | Login rate limit |
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
| `GET /calendar`, `GET /calendar/events?start=&end=`, `POST /calendar/reschedule` | Calendar, its JSON feed, drag to move (`{id, start}`; idea/draft/in_review/approved only) |
| `GET /performance?days=7\|30\|90`, `/engine`, `POST /engine/{id}/resume`, `/campaigns`, `/chat`, `/status` | Dashboards |
| `GET /media/cards/{id}.png`, `/media/videos/{id}.mp4\|jpg` | Images and videos, streamed from 17 / 71 |

## The n8n workflow (`n8n/workflow.json`)

`72 · Control room · Apply decisions`, id `mktWf72ApplyDeci`, generated with the other workflows
(don't edit it by hand). `POST <n8n>/webhook/mkt-apply-decisions` with header `X-Control-Key`:

```json
{"reviewer": "henos", "decisions": [{"id": 12, "decision": "edit", "text": "…", "reason": "shorter", "publish_at": "2026-10-03 08:30"}]}
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
