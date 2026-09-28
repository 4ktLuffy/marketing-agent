# flow-runner

Deploy **86 of 87** of the local-LLM marketing agent. It runs **triggered lifecycle emails**:
a welcome series when someone subscribes, onboarding when a trial starts, a win-back when a
customer goes quiet, or your own flow on your own event.

- **Consent first.** A contact enters a flow only with recorded consent. `unsubscribed` stops
  every flow at once and for good: nothing in this service ever lifts it.
- **A person approves each flow once.** A flow has versions. Only the approved version runs.
  Every edit makes a new draft version, which needs approval again. The whole sequence is one
  item in the control room (72), so the reviewer sees every email together.
- **Measured with a holdout, not before/after.** When a contact enters, a hash puts them in the
  `flow` arm or the `holdout` arm (15 % by default). The holdout gets nothing. `/results`
  compares clicks, purchases and unsubscribes between the arms with a 95 % interval. It never
  uses opens. An **A/A mode** (both arms get the same emails) checks that the tracking works.
- **Hard limits.** A daily cap (200 by default) and a kill switch. Exit events (such as
  `purchased`) are checked right before each send. No contact ever gets the same step twice.
- **Nothing is sent.** `DRY_RUN` is on by default, and the stack's compose file fixes it to
  `true`. A "send" is a row in the outbox (`GET /outbox`). See *Sending for real* below.

It uses no LLM itself. Drafting a flow calls the llm-gateway (03) with the `email_sequence`
prompt, the one the content formats tool (57) uses, and the claim checker (44).

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing` network
(compose service `flow-runner`, **growth** and **full** profiles, port `127.0.0.1:8186`), with a
volume on `/data`. n8n calls it at `http://flow-runner:8000` (env `FLOW_URL`). It is internal
only: a client install closes its port (`docker-compose.private.yml`). Events reach it through
n8n or another container on the network. It keeps state in SQLite, so run exactly one instance
and back up the volume. It calls 19, 03 and 44 on the same network.

## Run

```bash
docker build -t flow-runner .
docker run --rm -p 8186:8000 --env-file .env -v flow-data:/data flow-runner
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me APPROVER_KEY=change-me-too DB_PATH=./flows.sqlite uvicorn app.main:app --port 8186
```

## How a flow goes live

1. **Write it.** Either by hand, `POST /flows/welcome/versions` with the steps, or drafted:
   `POST /flows/welcome/draft {"goal": "...", "link": "https://..."}`. A draft checks each email
   with the claim checker. Sentences it could not support are listed in the version's `notes`.
2. **Ask for approval.** `POST /flows/welcome/versions/1/submit` puts the whole sequence into the
   content calendar (19) as one `email_flow` item, `in_review`. It shows up in the control room
   and in the approval form (38) like any other draft. The publisher (39) never posts an
   `email_flow` item.
3. **Approve it** in the control room. The reviewer may edit the text there. Every 15 minutes the
   workflow calls `POST /reviews/sync` with the approver key, which only n8n holds. An approved
   item approves the version. An edited text becomes a new version, approved as it was
   approved. A text that no longer has the `--- Email N of M · H hours after entry ---` and
   `Subject:` lines is refused and reported. A rejected item rejects the version.
   Without the calendar, approve directly: `POST /flows/welcome/versions/1/approve` with
   `X-Approver-Key`.
4. **Send events.** `POST /events` from your shop, form tool or CRM, through n8n.

The approved version keeps running while a new version waits for approval. Contacts already in
the flow continue with the newly approved version's steps from where they are.

## Endpoints

Every endpoint except `/health` needs the header `X-API-Key: $INTERNAL_API_KEY` (contacts are
personal data, so reads need the key too). 🔐 marks the ones that also need
`X-Approver-Key: $APPROVER_KEY`. With `APPROVER_KEY` unset they are refused (`503`).

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok","dry_run","paused","daily_cap","send_path"}` |
| GET | `/flows` | — | every flow: trigger, mode, holdout share, approved version, contacts per arm and status |
| POST | `/flows` | `{"name","trigger","exit_events"?,"holdout_pct"?,"mode"?: "holdout"\|"aa"}` | the new flow (`201`) |
| GET | `/flows/{name}` | — | the flow with every version |
| PATCH | `/flows/{name}` | `{"holdout_pct"?, "mode"?}` | the flow. A new share applies to contacts who enter from now on. |
| POST | `/flows/{name}/versions` | `{"steps": [{"delay_hours","subject","body_markdown"}], "exit_events"?, "notes"?}` | a new **draft** version (`201`) |
| POST | `/flows/{name}/draft` | `{"goal","audience"?,"offer"?,"emails"?: 3..5,"link"?}` | a new draft version from the model, with `claims_flagged` (`201`); `502` when 03 or 44 fails |
| POST | `/flows/{name}/versions/{n}/submit` | — | the version, `in_review`, with `calendar_item_id` when `CALENDAR_URL` is set |
| POST | `/flows/{name}/versions/{n}/approve` 🔐 | `{"approved_by"?}` | the version, `approved`. The previous approved version becomes `retired`. |
| POST | `/flows/{name}/versions/{n}/reject` | — | the version, `rejected` |
| POST | `/reviews/sync` | — (🔐 to approve) | `{"created","approved","rejected","waiting","waiting_for_approver_key","problems"}` |
| POST | `/events` | `{"id"?, "type", "contact": {"email","consent"?: true\|false,"source"?}, "at"?, "data"?}` | `{"duplicate","entered":[{flow,arm}],"exited":[flows],"skipped":[{flow,reason}]}` |
| POST | `/tick` | — | `{"sent","failed","exited","capped","cap","sent_today","paused","by_flow","notices","dry_run"}` |
| DELETE | `/contacts/{email}` | — | `{"erased", "rows", "kept_unsubscribe"}`. Erasure on request: the contact, its events, enrollments and outbox rows are deleted. If they had unsubscribed, only a salted hash of the address is kept, so they are never mailed again. Needs `X-Approver-Key`. Keep `FLOW_SALT` (or the database) unchanged, or old hashes stop matching. |
| POST | `/pause` | `{"reason"?}` | `{"paused": true}`. Stops sending and entries. |
| POST | `/resume` 🔐 | — | `{"paused"}`. Still true while `FLOW_PAUSED=true`. |
| GET | `/outbox` | `?flow=&status=dry_run\|sent\|failed\|pending&limit=50&offset=0` | outbox rows, newest first |
| GET | `/flows/{name}/results` | `?days=30` | per arm and the differences, see *Results* |

**Events.** `type` is any lower-case name. These have a meaning here:

| Event | Effect |
|---|---|
| `subscribed`, `trial_started`, `inactive` | start `welcome`, `onboarding`, `winback` (a custom flow names its own trigger) |
| `unsubscribed` | suppressed forever, exits every flow |
| `purchased`, or any event in a version's `exit_events` | exits that flow when it happens after the contact entered |
| `clicked`, `purchased`, `unsubscribed` | counted in `/results` |

`contact.consent: true` records consent (with `source` and the event time). `false` withdraws it
and exits every flow. Leave it out when the event says nothing about consent (a purchase from
your shop). A contact enters a flow only when all of these hold: recorded consent, not
suppressed, the flow has an approved version, flows are not paused, the event is under
`FLOW_ENTRY_MAX_AGE_HOURS` old, and the contact has not been in this flow before (a contact
enters each flow once). An event with an `id` that was seen before changes nothing.

```bash
curl -s localhost:8186/events -H 'content-type: application/json' -H 'X-API-Key: change-me' -d '{
  "id": "shop-4711", "type": "subscribed",
  "contact": {"email": "maya@example.com", "consent": true, "source": "footer signup"}}'
# {"type":"subscribed","email":"maya@example.com","duplicate":false,
#  "entered":[{"flow":"welcome","arm":"flow"}],"exited":[],"skipped":[]}
```

**Steps.** `delay_hours` counts from when the contact entered (0 = the next tick). Steps are in
order. At most one email of a flow goes to a contact per tick, and two emails of one flow are at
least `FLOW_MIN_GAP_HOURS` apart, so a tick that was down for a day never sends a burst.

## Sending (tick)

n8n calls `POST /tick` every 15 minutes. For every contact in the `flow` arm (both arms in A/A
mode) whose next step is due, in order of due time, until the daily cap:

1. Right before the send, inside one database transaction: the contact is still in the flow, has
   consent, is not suppressed, has no exit event since entering, the version is still the
   approved one, and flows are not paused. Without consent, when suppressed or after an exit
   event, the contact exits the flow; otherwise the step waits for the next tick.
2. The outbox row is written. It is unique per contact and step, so a replayed tick, a second
   tick at the same time, or a crash never sends a step twice.
3. `DRY_RUN`: the row (status `dry_run`) is the whole send.

The cap counts every outbox row of the UTC day. The first tick that hits it, and the first tick
of a pause, return a notice once, so the owner hears it once and not every 15 minutes.

### Sending for real (not available yet)

The real path is a **stub**. With `DRY_RUN=false` it calls `POST {NEWSLETTER_URL}/tx` on
listmonk-bridge (63) with `{to, subject, body_markdown, idempotency_key}`. 63 has no
transactional send today (it only creates draft campaigns for a person to send), so that call
fails with `404`. The row is then `failed` with the reason, and it is never retried: a real send
that may have gone out is never repeated. The stack's compose file fixes `DRY_RUN` to `true`.

Before real sending, 63 needs a `/tx` endpoint on Listmonk's transactional API (a template with
an unsubscribe link, the subscriber's own consent status), and this README needs the steps to
turn it on.

## Results

`GET /flows/welcome/results?days=30` takes the contacts who entered in the last 30 days. For
each arm: `entered`, `emails_sent`, `received_any`, and how many contacts `clicked`,
`purchased` and `unsubscribed` after entering. `compare` has, per outcome, the flow rate, the
holdout rate, the difference and a 95 % interval (two proportions, normal approximation).
Under `FLOW_MIN_N` (100) contacts in an arm, or under 5 contacts with (or without) the outcome
in an arm, it says `not enough data` and gives no interval.

Clicks count only when something posts `clicked` events, for example n8n from the link
shortener (16). For the holdout arm, a click is any tracked click, which is the fair comparison.
Opens are not measured: Apple Mail Privacy Protection opens emails by itself.

A small shop needs months for a purchase difference to show. Report the interval, not a win.

**A/A check.** Set `{"mode": "aa"}` on a flow for a while. Both arms get the same emails, so any
significant difference means the assignment or the tracking is broken. The tests simulate it:
200 runs of 2,000 contacts with the same true click rate, 94.5 % of them not significant (the
test requires at least 90 %). The positive control, a real 5-point lift with 6,000 contacts
(about 900 held out), is detected in 20 of 20 runs.

## Workflow (shipped here, generated by `tools/workflow-generator`)

`n8n/workflow.json` (id `mktWf86FlowRunnr`, imported with the growth profile) runs **every 15
minutes**: `GET /health` (it stops quietly when `FLOW_URL` is empty) → `POST /reviews/sync` with
the approver key → `POST /tick` → one notification (shared `NOTIFY_FORMAT` helper) only when
emails were sent (dry run says so), a flow was approved, rejected or is waiting for approval, the
cap or a pause was hit, or a call failed. Most ticks send nothing.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for every endpoint but `/health` (unset → `503`). Also sent to 19, 03, 44, 63. |
| `APPROVER_KEY` | — | Required to approve a version and to resume. Unset → both refused. |
| `DB_PATH` | `/data/flows.sqlite` | SQLite file |
| `DRY_RUN` | `true` | Only `false`, `0`, `no` or `off` turn it off. Compose fixes it to `true`. |
| `FLOW_DAILY_CAP` | `200` | outbox rows per UTC day over all flows |
| `FLOW_PAUSED` | `false` | kill switch; `POST /resume` cannot override it |
| `FLOW_HOLDOUT_PCT` | `15` | default holdout share, 0 to 50 |
| `FLOW_SALT` | generated | salt of the arm hash. Keep it: a new salt reshuffles new contacts. |
| `FLOW_MIN_N` | `100` | smallest arm for an interval |
| `FLOW_ENTRY_MAX_AGE_HOURS` | `48` | older events do not start a flow |
| `FLOW_MIN_GAP_HOURS` | `12` | least time between two emails of one flow to one contact |
| `CALENDAR_URL` | empty | content-calendar (19) for review; empty = approve in 86 directly |
| `GATEWAY_URL` / `CLAIMS_URL` | empty | llm-gateway (03) and claim-checker (44) for `/draft` |
| `NEWSLETTER_URL` | empty | listmonk-bridge (63), the real send path (a stub) |

## Known limits

- **No real sending yet** (see above). The outbox shows exactly what would have gone out.
- A contact enters each flow once. A second win-back for the same person needs a new flow name.
- There is no erase endpoint yet. Erasing a person means deleting their rows from the SQLite file
  (keep the suppression if they unsubscribed).
- Emails have no personalisation fields and no unsubscribe footer: that belongs to the real send
  path (Listmonk templates).
- Clicks are only as good as the `clicked` events you send. Nothing wires the link shortener (16)
  to `/events` yet.

## CI

`.github/workflows/ci.yml` runs the tests (respx mocks only; no real call to any service), then
pushes `ghcr.io/<you>/<repo>:latest` on every push to `main`.
