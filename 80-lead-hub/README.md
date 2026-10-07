# lead-hub

Deploy **80 of 91** of the marketing agent. It handles people who contacted you
(a website form, the site assistant, a form tool's webhook, or someone you typed in):

1. **Capture.** It stores the lead, deduplicated by email. A second message from the same
   person is added to the same lead's timeline.
2. **Enrich.** It reads **the lead's own company website only**: the homepage and one about
   page, through the page extractor (07), and only where `robots.txt` allows it. The
   `lead_enrich` prompt summarises what the pages say. Every statement needs a quote that is
   really on the page, then the claim checker (44) checks it against the page text.
   Anything unsupported is dropped.
3. **Score.** Deterministic rules from a YAML file give a score from 0 to 100, a grade from
   A to D, and a list of reasons (for example "asked for a call or demo +20").
4. **Route.** It creates or updates the contact in HubSpot and/or Pipedrive, matched by
   email, and adds a note. Grade A also notifies a person immediately, B goes to the daily
   digest, and C/D get the nurture tag. **Dry run by default.**
5. **Draft a first reply.** The `lead_first_reply` prompt answers the lead's own message from
   approved facts and offers one next step (your booking link). The claim checker removes
   unsupported sentences. **A person approves and sends every reply. Nothing is sent
   automatically.**

**It only handles inbound leads.** It never sends cold outreach and never reads LinkedIn or
any other social network. It never calls data brokers (Clearbit, Apollo, ZoomInfo...),
search engines, or any site other than the lead's own. Nothing about a lead leaves the hub
unless the lead gave consent **and** `DRY_RUN=false`.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a volume on `/data`. n8n and the site assistant (79) call it at
`http://lead-hub:8000` (env `LEADS_URL`). Form tools (Tally, Typeform, n8n forms) post to
`/leads/webhook/<source>`. To reach it from outside, put that one path behind your HTTPS
reverse proxy. Compose binds the port to `127.0.0.1`. It keeps state in SQLite, so run
exactly one instance and back up the volume. It needs 07, 03 and 44 on the network.

## Run

```bash
docker build -t lead-hub .
docker run --rm -p 8180:8000 -e INTERNAL_API_KEY=change-me -v lead-data:/data lead-hub
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./leads.sqlite EXTRACTOR_URL=http://localhost:8107 \
  GATEWAY_URL=http://localhost:8103 CLAIMS_URL=http://localhost:8144 uvicorn app.main:app --port 8180
```

## Endpoints

Every endpoint except `/health` and the webhook needs the header `X-API-Key: $INTERNAL_API_KEY`
(leads are personal data, so reads need the key too).

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok","dry_run","crm":[...]}` |
| POST | `/leads` | `{"source": "site_assistant"\|"form"\|"webhook"\|"manual", "email", "name"?, "company_domain"?, "message"?, "transcript_ref"?, "consent"?: {"text","at"}, "utm"?: {}}` | `{"id","created","merged","company_domain","domain_note","enrich_status","consent","processing"}`: `201` new, `200` merged |
| POST | `/leads/webhook/{source}` | the form tool's JSON (secret, see below) | same as `POST /leads` |
| GET | `/leads` | `?grade=A..D&source=&status=&consent=true\|false&q=&limit=50&offset=0` | `[lead]`, most recently updated first |
| GET | `/leads/{id}` | — | lead with `enrichment`, `score_detail`, `route`, `reply` and `timeline` (every activity) |
| POST | `/leads/{id}/process` | `?force_enrich=true` | runs enrich, score, route and reply again (synchronously) and returns the lead |
| POST | `/leads/{id}/consent` | `{"given": true, "text", "at"?}` or `{"given": false}` | lead. Withdrawing consent stops all follow-up. |
| POST | `/leads/{id}/reply/approve` | `{"subject"?, "body"?, "approved_by"?}` (edits optional) | reply, `status: approved` |
| POST | `/leads/{id}/reply/send` | — | stub: with `DRY_RUN` it returns `{"sent":false,"dry_run":true,"would_send":{to,subject,body}}`. Without `DRY_RUN` it returns `501`: send the reply from your mail client |
| POST | `/leads/{id}/reply/sent` | — | records that a person sent it; the lead becomes `replied` |
| DELETE | `/leads/{id}` | — | erases the lead: `{"id","erased":true,"crm_copies":[...],"calendar_item_id","calendar"}` |
| GET | `/leads/{id}/export` | — | `{"exported_at","lead"}`: everything held about the lead |
| GET | `/stats` | — | totals by grade, status, source, enrichment and reply status, plus how many leads have consent |
| GET | `/digest` | `?grade=B&since=ISO` (default: the last 24 h) | `{"since","grade","leads":[{id,name,company_domain,score,grade,consent,reasons,reply_status}]}` for the daily digest |
| POST | `/admin/purge` | — | `{"erased":[ids],"retention_days"}`. This also runs on every capture. |
| GET | `/scoring/rules` | — | the parsed scoring rules |

```bash
curl -s localhost:8180/leads -H 'content-type: application/json' -H 'X-API-Key: change-me' -d '{
  "source":"form","email":"maya@acme-robotics.com","name":"Maya Chen",
  "message":"Can we book a demo? We are a remote team of 40 and want pricing.",
  "consent":{"text":"I agree to be contacted about my enquiry.","at":"2026-09-27T10:00:00Z"}}'
# {"id":1,"created":true,"merged":false,"company_domain":"acme-robotics.com","domain_note":"email",
#  "enrich_status":"pending","consent":true,"processing":true}
```

### How the parts behave

- **Capture and dedupe.** The email is lower-cased and is the key. The first capture creates
  the lead. Later ones add a `merged` activity with the new message, add the source to
  `sources`, fill in a name, domain or UTM that was missing, and replace the consent record
  if a new one comes with them. Processing runs in the background after the response,
  one lead at a time.
- **Which website.** The domain comes from the email. When the email is from a free mail
  provider (Gmail, Outlook, Yahoo, iCloud, Proton, GMX, ... about 100 domains, plus
  `FREE_MAIL_EXTRA`), the hub uses the `company_domain` the lead typed instead, and when
  there is none it does no enrichment (`enrich_status: skipped`). It never fetches social
  networks, link-in-bio hosts or data brokers (LinkedIn, Facebook, X, Instagram, TikTok,
  YouTube, Crunchbase, ZoomInfo, Apollo, Linktree...), even when a lead types one in as
  their website. IP addresses are refused too.
- **Polite fetching.** It reads `robots.txt` first. 07 has no robots check, so the hub
  fetches that one file itself, with the same public-address guard as 07. A `Disallow`
  for `marketing-agent` or `*` skips the page. A 401 or 403 on `robots.txt` means the site
  is not read at all, a 5xx means "try later", and a 404 means everything is allowed. Then it
  fetches `/` and the first of `/about`, `/about-us` or `/company` that exists, with
  `ENRICH_DELAY_S` between requests (at most 5 requests per site). A page that redirects
  to another domain is discarded. Each page is capped at 8,000 characters.
- **Grounded summary.** The prompt returns `industry`, `sells`, up to 6 `facts` and
  `size_hints`, each with a quote. The code then drops any fact or size hint whose quote
  is not an exact part of the fetched text (whitespace and curly quotes are normalised).
  It sends the remaining facts, plus "The company works in <industry>." and "The company
  sells <what>.", to 44 as one statement per line, with the page text as `context`. Every
  statement 44 does not support is dropped. A statement 44 split differently or did not
  return counts as unsupported too. `enrichment.dropped` lists what was removed and why,
  and `enrichment.sources` lists the URLs that were read. Size hints are kept only if a
  quote on the page states them. Nothing is estimated.
- **Scoring** (`config/scoring.yaml`, or `SCORING_RULES`). Each rule has a `category`
  (`fit`, `intent`, `engagement`), a `field` (message, enrichment, industry, size, source,
  utm_*, messages, has_company_domain, has_consent), keywords (matched as whole words, in
  any case), `points` (negative points subtract) and a `reason`. Each category's total is
  capped, and the score is clamped to 0–100. Grades use the `grades` thresholds (defaults:
  A ≥ 70, B ≥ 45, C ≥ 25, else D). `score_detail.reasons` lists every rule that matched and
  its points. A broken rules file is an error, never a silent zero. The example rules
  describe the example coffee brand's ideal customer (remote teams asking for demos or
  pricing). Replace them with your own. There is no LLM in the score.
- **Routing.**

  | Grade | `route.action` | `route.tag` | What happens |
  |---|---|---|---|
  | A | `notify_now` | `hot` | CRM sync, plus an alert to `NOTIFY_WEBHOOK_URL` with only the lead id, score, domain and reasons (no name, email or message) |
  | B | `daily_digest` | `digest` | CRM sync; listed by `GET /digest` for the daily summary |
  | C, D | `nurture` | `nurture` | CRM sync with the nurture tag (HubSpot: `HUBSPOT_TAG_PROPERTY`; Pipedrive: `PIPEDRIVE_NURTURE_LABEL_ID`) |

  Without consent, `route.blocked` says so and nothing is sent anywhere. With
  `DRY_RUN=true`, `route.crm[].plan` holds the exact requests that would be sent (method,
  URL, JSON). Tokens are never stored, returned or logged.
- **CRM create-or-update by email** (checked against the current API docs, 2026-09):
  - **HubSpot** (private app token, `Authorization: Bearer`). It looks the contact up with
    `GET /crm/v3/objects/contacts/{email}?idProperty=email`. A 404 means create it with
    `POST /crm/v3/objects/contacts` (email, first and last name, website, and
    `lifecyclestage: lead` on create only). Otherwise it updates it with
    `PATCH /crm/v3/objects/contacts/{id}`. Then it adds a note with
    `POST /crm/v3/objects/notes` and association type `202` (note to contact). The note
    holds the source, score and reasons, their last message, the checked facts with source
    URLs, and the consent record.
  - **Pipedrive** (API token in the `x-api-token` header, `PIPEDRIVE_BASE_URL=https://<company>.pipedrive.com`).
    It searches with `GET /api/v2/persons/search?term=<email>&fields=email&exact_match=true`,
    then creates the person with `POST /api/v2/persons` (name, `emails[]`) or updates the
    name and labels with `PATCH /api/v2/persons/{id}` (their email list is never
    overwritten). Then it adds a note with `POST /api/v1/notes` (`content`, `person_id`).
  - A CRM error is stored as `"<step>: HTTP <code>"` and never includes response bodies.
- **First reply** (only for leads with consent and a message). The gateway runs
  `lead_first_reply` with the lead's latest message, first name and `BOOKING_URL`. The brand's
  approved facts come from 05 through the gateway. Then 44 checks the reply, with the
  lead's message as context and the booking link as an extra fact. Every sentence 44 flags
  is removed and listed in `reply.removed`. `reply.status` is `drafted`, or `needs_human`
  when the model says the message is a complaint, a job application, a pitch, and so on.
  With `CALENDAR_URL` set, the draft is also saved to the content calendar (19) as a
  **`draft`** item with channel **`lead_reply`**. Approve and send it from the hub
  (`/reply/approve`, then send it yourself and call `/reply/sent`), not from the publisher.
  A new message replaces a draft that has not been approved yet. Once a person approves a
  draft, it is never overwritten.

  > **Note for 39 (publisher):** `lead_reply` is not a post. Its `NOT_POSTS` list
  > (`review_reply`, `newsletter`, `blog_refresh`, `seo_brief`) must also include
  > `lead_reply` before any `lead_reply` item is moved to `in_review` or `approved` in the
  > calendar. Until then the hub creates these items as `draft` only, and the publisher
  > never sees a draft.

### Privacy (GDPR basics)

- **Consent** is `{text, at}`, exactly what the person agreed to and when, stored with the
  time it was recorded. It is required for anything outbound (CRM, alert, reply). A lead
  without consent is still stored, enriched from their company's public website and scored,
  so a person can decide, but nothing about the lead leaves the hub. `POST /leads/{id}/consent`
  records consent given later or withdrawn.
- **Erasure.** `DELETE /leads/{id}` deletes the lead, every activity and message, the
  enrichment, score, route and reply draft. SQLite `secure_delete` overwrites the rows.
  A reply draft in the calendar is overwritten with `[erased ...]` and rejected (the calendar
  has no delete; a `published` item is reported instead). CRM copies are **not** deleted
  automatically: the response lists them under `crm_copies` so a person deletes them there.
- **Retention.** Leads not updated for `RETENTION_DAYS` (default 365) are erased the same way,
  on every capture and by `POST /admin/purge`. `0` keeps leads forever.
- **Export.** `GET /leads/{id}/export` returns everything held about the lead.
- Logs never contain emails, messages or tokens. Background errors log the lead id and the
  error type only.

This is a design constraint, not legal advice.

### Webhooks from form tools

`POST /leads/webhook/<source>` with `WEBHOOK_SECRETS="tally=...,typeform=...,n8n=..."`. The
request is accepted if one of these matches the source's secret:

- the header `X-Webhook-Secret: <secret>` (n8n forms, generic tools, Tally custom headers), or
- `Tally-Signature` = base64 HMAC-SHA256 of the raw body, or
- `Typeform-Signature` = `sha256=` + base64 HMAC-SHA256 of the raw body.

An unknown source or a wrong secret gets `401`. The hub maps fields by their label or
key (Tally `data.fields[]`, Typeform `form_response.answers[]` with the definition's
titles, or a flat JSON object):

- email: `email`, `work email`
- name: `name`, `your name` (not `company name`)
- website: `website`, `company website`, `domain`
- message: `message`, `how can we help`, `question`, `details`, `inquiry`/`enquiry`
- consent: a ticked `consent`/`I agree`/`permission` checkbox; its label becomes the consent
  text, and `at` is the time it was received
- UTM: any `utm_*` field

A form without an email is a `422`. The lead's `source` is `webhook`.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for every endpoint except `/health` and the webhook. Not set: `503`. Wrong or missing: `401`. Also sent to 03, 44 and 19. |
| `DB_PATH` | `/data/leads.sqlite` | SQLite file (WAL; writes use `BEGIN IMMEDIATE`, so parallel captures of one email never make two leads) |
| `DRY_RUN` | `true` | `false` lets CRM syncs and the grade A alert actually go out (and only for leads with consent) |
| `RETENTION_DAYS` | `365` | Erase leads not updated for this long. `0` = keep. |
| `WEBHOOK_SECRETS` | empty | `source=secret` pairs, comma-separated |
| `HUBSPOT_TOKEN` / `HUBSPOT_TAG_PROPERTY` | empty | HubSpot private app token (scopes `crm.objects.contacts.read`/`.write`); optional contact property for the routing tag. `HUBSPOT_BASE_URL` defaults to `https://api.hubapi.com` |
| `PIPEDRIVE_TOKEN` / `PIPEDRIVE_BASE_URL` / `PIPEDRIVE_NURTURE_LABEL_ID` | empty | Pipedrive API token, `https://<company>.pipedrive.com`, optional label id for C/D |
| `CRM` | empty | `hubspot,pipedrive`: which CRMs to use. In a dry run the plans are shown even without tokens. Empty = every CRM that has a token. |
| `NOTIFY_WEBHOOK_URL` | empty | Where grade A alerts go (Slack/Discord/n8n webhook) |
| `BOOKING_URL` | empty | The one next step the reply draft offers |
| `EXTRACTOR_URL` / `GATEWAY_URL` / `CLAIMS_URL` | `http://page-extractor:8000` / `http://llm-gateway:8000` / `http://claim-checker:8000` | 07, 03, 44 |
| `CALENDAR_URL` | empty | 19. When set, reply drafts are also saved there as `lead_reply` drafts. |
| `SCORING_RULES` | `config/scoring.yaml` | Scoring rules file |
| `ENRICH` / `REPLY_DRAFTS` / `AUTO_PROCESS` | `true` | Turn enrichment, reply drafts, or background processing after capture on or off |
| `ENRICH_DELAY_S` | `1` | Pause between requests to one company site |
| `ENRICH_CACHE_DAYS` | `30` | A second lead from the same company domain reuses an enrichment this recent instead of fetching the site again (`?force_enrich=true` re-fetches) |
| `FREE_MAIL_EXTRA` | empty | More free-mail domains, comma-separated |
| `ALLOW_PRIVATE_URLS` | `false` | Local tests only: the robots.txt fetch may reach private addresses (07 has the same setting) |
| `SITE_URL_TEMPLATE` | `https://{domain}` | Local tests only, for example `http://{domain}:8190`. The host must stay `{domain}`. |
| `GATEWAY_TIMEOUT` / `CLAIMS_TIMEOUT` | `300` / `600` | Seconds |

## Known limits

- 44 counts the brand's approved facts (05) and the knowledge base (06) as evidence as
  well as the page text. A statement about the lead's company that happens to match one of
  **your** facts could pass. The code check that every quote is on the lead's page limits
  this for facts, but not for the `industry` and `sells` lines.
- Keyword scoring is crude on purpose, so you can read and change it. There is no LLM
  score rationale: the reasons list is the rationale. Measure grades against real
  outcomes (won or lost in the CRM) before trusting them.
- The hub cannot send email. That is deliberate: a person sends every reply.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
