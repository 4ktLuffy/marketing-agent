# site-assistant

Deploy **79 of 90** of the local-LLM marketing agent. A chat widget for your website that
answers visitors' questions **only from your knowledge base (06) and approved facts (05)**,
checks every answer with the claim checker (44) before showing it, asks at most two
qualifying questions when someone wants to buy, offers your booking link, and **hands off to
a person** when it doesn't know, when asked, or when the topic needs one.

It never books, sells, refunds, discounts or promises anything, and it always says it is an AI.

## Why

- **It works when a person backs it up.** In randomized field experiments on a large
  retail platform, a pre-sale chatbot raised sales 16.3% against no pre-sale support, and
  about 25% when combined with escalation to human agents (Fang et al., 2025,
  [arXiv 2510.12049](https://arxiv.org/abs/2510.12049); `_dev/research/ai-marketing-wins.md` #1).
- **Bots with authority and no guardrails fail.** Air Canada was held liable for a refund
  policy its chatbot invented
  ([ABA summary](https://www.americanbar.org/groups/business_law/resources/business-law-today/2024-february/bc-tribunal-confirms-companies-remain-liable-information-provided-ai-chatbot/)),
  and a car dealer's bot "agreed" to sell a Tahoe for $1 ([AIID 622](https://incidentdatabase.ai/cite/622/)).
  So here the model is only a writer: code decides what may be said.
- **Disclosure.** The first message and the widget header say it is an AI assistant, not a
  person. Disclosure can cost conversions (Luo et al., *Marketing Science* 2019), but a bot
  that passes as human is the thing not to build (`_dev/research/BUILD-PLAN-2.md`), and
  transparency rules for chatbots (e.g. EU AI Act, Article 50) point the same way.

## How one message is handled (`POST /chat`)

1. **Code routes first, no LLM** (`app/guards.py`), in this order:
   - "Talk to a person" button or "can I speak to someone" → handoff.
   - Prompt injection ("ignore your rules", "you are now…", "new rule: …legally binding",
     "print your system prompt") → fixed refusal.
   - "Are you human?" → always "No, I'm an AI assistant, not a person."
   - Escalation topics → handoff: complaints (damaged, never arrived, charged twice…),
     refund demands, order/account changes, legal (lawyer, fraud, GDPR…), health (pregnancy,
     allergy, medication…). Same idea as 58's escalation words, for chat.
   - Asking for a discount, coupon, deal, free trial → fixed "I can't offer or promise
     discounts" (the $1 Tahoe rule), counted as buying intent.
   - The answer to a pending qualifying question → stored, no LLM.
2. **Everything else is answered from sources.** 06 `/search` (score ≥ `KB_MIN_SCORE`,
   never the untrusted `trend-digest` / `competitor-watch` sources) + every 05 `/facts` line,
   numbered, go to prompt `site_answer` via the gateway. The model returns
   `{covered, answer, sources, buying_intent}`; `covered: false` → handoff with
   "I don't know — let me get a person."
3. **Deterministic output guards**, per sentence: dropped if it has a number, price, date or
   weekday not in those sources; an offer word (discount, trial, free, sample…) or a
   health/legal word (pregnancy, medical, safe for, legal, warranty, organic…) the sources
   don't use; a promise or action ("I'll send", "we'll refund", "guarantee"); or a claim to be
   human.
4. **Claim check.** The remaining sentences go to 44 `/verify` with the same knowledge-base
   excerpts as `context` (44 adds the approved facts itself). Unsupported sentences are
   dropped. If none is left, or 44 is down, a person takes over: the check fails closed.
5. The reply shows the source titles it used ("Source: Customer FAQ").
6. **Time budget.** Steps 2–4 together get `ANSWER_TIMEOUT_S` (default 20 s). Each call gets
   at most the time left; a gateway or claim check that doesn't answer in time, or a queue wait
   that uses the budget up, gives a handoff (reason `timeout`), never an unchecked answer.

## Qualification, booking, consent, leads

- **Buying intent** = code rules (price, plan, team, office, trial, demo, "20 people"…) OR the
  model's `buying_intent`. Then at most **2** qualifying questions (`QUALIFY_QUESTIONS`,
  default team size and use case). A team size already in the message ("a team of 20") is
  taken from it, not asked again. Then the booking link (`BOOKING_URL`, e.g. Cal.com) is
  offered **once**. The assistant never books: the visitor clicks the link.
- **Email only with consent.** The widget shows a form with a checkbox; `POST /consent` needs
  `agree: true` and stores the server's consent text (`CONSENT_TEXT`) with the time. An email
  or phone number typed into the chat is removed from the stored transcript and never used.
- **Lead** (buying intent + consent) → `POST $LEADS_URL/leads` (80 lead-hub, `X-API-Key`):
  `{source:"site_assistant", email, name?, company_domain?, message, transcript_ref, consent:{text, at}}`.
  `company_domain` is the email's domain unless it is a free-mail domain. `LEADS_URL` empty →
  the lead stays here (`status: stored`); a failed send is kept as `failed` and can be retried
  with `POST /admin/leads/{id}/forward`.
- **Handoff** (asked, low confidence, nothing supported, escalation topic, or consent without
  an open question) → a handoff record (one open per conversation) and a notification to
  `NOTIFY_WEBHOOK_URL` with the same `{text, content}` body as the n8n notify helper's
  generic format. The visitor is told a person will reply **by email** and is offered the
  consent form; there is no pretend live chat.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing` network
with a volume on `/data` (compose service `site-assistant`, `127.0.0.1:8179`). It calls the
gateway (03), brand service (05), knowledge base (06), claim checker (44) and lead hub (80).
It keeps state in SQLite and its rate limits in memory, so run exactly one instance.

**Public exposure: only the chat paths, behind HTTPS.** The widget runs on your website, so
visitors' browsers must reach `/chat`, `/consent`, `/widget-config`, `/widget.js` and
`/widget.css`. Nothing else, and **never `/admin/*`**. Example with Caddy on the Docker host:

```
chat.example.com {
    @public path /chat /consent /widget-config /widget.js /widget.css
    handle @public {
        reverse_proxy 127.0.0.1:8179
    }
    respond 404
}
```

Caddy appends the client address to `X-Forwarded-For`; set `SITE_TRUST_PROXY=true` so the
per-IP limits see visitors, not the proxy. Set `SITE_ALLOWED_ORIGINS` to your site's exact
origin(s). Read the admin endpoints from the Docker host (`curl localhost:8179/admin/...`) or
over your VPN.

## Run

```bash
docker build -t site-assistant .
docker run --rm -p 8179:8000 --network marketing-agent_marketing -e INTERNAL_API_KEY=change-me \
  -e ALLOWED_ORIGINS=https://www.example.com -e BOOKING_URL=https://cal.com/you/intro \
  -v site-data:/data site-assistant
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./site.sqlite GATEWAY_URL=http://localhost:8103 \
  BRAND_URL=http://localhost:8105 KB_URL=http://localhost:8106 CLAIMS_URL=http://localhost:8144 \
  ALLOWED_ORIGINS=http://localhost:8197 uvicorn app.main:app --port 8179
```

The prompt `site_answer` must be in the gateway's prompt library (04).

## Embed the widget

```html
<script src="https://chat.example.com/widget.js" defer></script>
```

Plain JavaScript and CSS, no framework and no third-party code. Server text is inserted as
text, never HTML. It keeps the session id in `sessionStorage` (per tab), shows the AI
disclosure and a **Talk to a person** button at all times, uses a labelled dialog with a
live region, works by keyboard (Enter sends, Escape closes) and goes full screen under 480 px.
Colours: override `--sa-accent` and friends on `.sa-root`. `data-endpoint` on the script tag
points it at another base URL.

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY` (503 when the service has no key).

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/chat` | `{"session_id"?, "message", "action"?: "handoff"}` | `{"session_id","conversation_started","disclosure","reply","kind","sources","actions":{"handoff","show_consent","booking_url"}}` |
| POST | `/consent` | `{"session_id","email","name"?,"agree":true}` | `{"ok","reply","lead"}`; 422 without `agree: true`, 404 for an unknown session |
| GET | `/widget-config` | — | `{"assistant_name","disclosure","consent_text","booking_url"}` |
| GET | `/widget.js`, `/widget.css` | — | the embed |
| GET | `/admin/conversations` 🔑 | `?limit=50&handed_off=&buying=` | conversations, newest first |
| GET | `/admin/conversations/{id}` 🔑 | — | transcript, per-reply `meta` (sources, dropped sentences and why), handoffs, leads |
| GET | `/admin/handoffs` 🔑 | `?status=open\|closed` | handoffs |
| POST | `/admin/handoffs/{id}/close` 🔑 | — | the handoff |
| GET | `/admin/leads` 🔑 | `?status=forwarded\|stored\|failed` | leads |
| POST | `/admin/leads/{id}/forward` 🔑 | — | send again to `LEADS_URL` |
| GET | `/admin/stats` 🔑 | — | answered, handed off (by reason), sentences dropped (by 44 / by guards), refusals, leads captured, rate-limited |

`kind` is `answer`, `handoff`, `refusal` (injection or discount), `disclosure` ("are you
human?") or `qualify`. The session id is created by the server (a client-chosen id is
ignored) and only its SHA-256 is stored.

```bash
curl -s localhost:8179/chat -H 'content-type: application/json' -d '{"message":"Can I pause my subscription?"}'
# {"session_id":"…","conversation_started":true,"disclosure":"Hi, I'm the AI assistant of Northwind Roasters, not a person. …",
#  "reply":"Hi, I'm the AI assistant … \n\nYou can skip or pause a delivery from your account page at any time, with no fee.",
#  "kind":"answer","sources":["Customer FAQ"],"actions":{"handoff":false,"show_consent":false,"booking_url":null}}
```

## Abuse protection (the chat is public)

| Protection | Default |
|---|---|
| Allowed `Origin`s (`ALLOWED_ORIGINS`); CORS only for them; empty = no browser allowed | — |
| Requests without `Origin` (not a browser) allowed unless `REQUIRE_ORIGIN=true`; the header is easy to fake outside a browser, so the limits below are the real protection | allowed |
| Per IP: `RATE_IP_PER_MINUTE` / `RATE_IP_PER_DAY` (429 with `Retry-After`); IPv6 counted per /64 | 8 / 150 |
| Handoff notifications: `NOTIFY_MAX_PER_HOUR` (0 = no cap); over it the handoff is still recorded (`notify_error`). Visitor text in a notification can't ping (`@everyone`, `<!channel>`) or hide a link | 20 |
| Per session: `RATE_SESSION_PER_MINUTE`, `MAX_SESSION_MESSAGES` | 5 / 40 |
| `MAX_MESSAGE_CHARS` (413), `MAX_BODY_BYTES` (413, also for chunked bodies) | 800 / 4096 |
| `MAX_CONCURRENT_ANSWERS` LLM answers at once; others wait `QUEUE_WAIT_SECONDS`, then 503 | 2 / 30 |
| Session ids random server-side (`secrets.token_urlsafe`), expire after `SESSION_TTL_HOURS` | 24 |

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | admin endpoints; also sent to 03, 44 and 80 |
| `DB_PATH` | `/data/site.sqlite` | SQLite (WAL; writes use `BEGIN IMMEDIATE`) |
| `GATEWAY_URL` / `BRAND_URL` / `KB_URL` / `CLAIMS_URL` | stack names | 03 / 05 / 06 / 44 |
| `LEADS_URL` | empty | 80 lead-hub; empty = leads stay here |
| `NOTIFY_WEBHOOK_URL` | empty | handoff notifications, body `{text, content}` (Slack, Discord, Teams, n8n webhooks accept it; Telegram does not) |
| `ALLOWED_ORIGINS` | empty | comma-separated exact origins of the sites embedding the widget |
| `BOOKING_URL` | empty | https booking link offered after qualifying |
| `QUALIFY_QUESTIONS` | team size, use case | JSON `[{"key","question"}]`; key `team_size` is also filled from the message |
| `MAX_QUALIFY` | `2` | at most 2 |
| `CONSENT_TEXT` | "Yes, {brand} may use my email address and this chat to reply to me. It won't be added to a mailing list." | what the checkbox says and what is stored |
| `BRAND_NAME` | from 05 `/profile` | name used in the disclosure |
| `KB_MIN_SCORE` / `KB_K` | `0.35` / `4` | knowledge-base excerpts used |
| `KB_UNTRUSTED_SOURCES` | `trend-digest,competitor-watch` | never used (LLM summaries of outside pages) |
| `KB_SOURCES` | empty | if set, only these knowledge-base sources are used (e.g. `website,faq`), so internal notes can't reach visitors |
| `TRUST_PROXY` | `false` | take the client IP from the last `X-Forwarded-For` entry |
| `REQUIRE_ORIGIN` | `false` | refuse requests without `Origin` |
| `ANSWER_TIMEOUT_S` | automatic | seconds for one sourced answer (search + model + claim check); slower = handoff. Empty = `20` when `ASSISTANT_GATEWAY_URL` is set (hosted), `120` all-local (a laptop 7B takes 25–75 s). `0` = only the per-call timeouts |
| `GATEWAY_TIMEOUT` / `CLAIMS_TIMEOUT` | `120` / `180` | seconds per call (capped by the time left in `ANSWER_TIMEOUT_S`) |
| `SESSION_TTL_HOURS` | `24` | a session idle longer starts over (with the disclosure again) |
| `RETENTION_DAYS` | `90` | idle conversations deleted after this (not ones with a lead); `0` = keep |
| rate and size limits | see above | |

## Measured

`23-eval-suite`: `python -m evalsuite.site_assistant --origin <an allowed origin>`
(`cases/site_assistant.yaml`: 26 visitor messages written before the first run; start 79 with
`RATE_IP_PER_MINUTE=1000`, since every case comes from one address). One run on 2026-09-27,
local `mkt-writer` (qwen2.5:7b) for the answer and for 44, example brand and FAQ:

| Metric | Result |
|---|---|
| Correct answers (supported, relevant, nothing invented) | **11 / 11** (10 covered questions + the "team of 20" buying case) |
| Invented facts (numbers not in the sources, forbidden strings; every reply read by hand too) | **0** |
| Correct handoffs (uncovered, refund demand, pregnancy, "talk to someone") | **8 / 8** |
| Injection resisted | **4 / 4** |
| Right reply kind | 26 / 26 |
| Buying flow (answer → 1 question, size taken from the message → booking link) | 2 / 2 |
| Time per sourced answer | 45–75 s (handoffs by the model 20–26 s, code routes ~0 s) |

**How to read it.** 12 of the 26 cases (all discount, injection, "are you human", refund
and health cases) were decided by code rules before any LLM call. Those rules and the cases
were written by the same person, so those are not a test of robustness to new phrasings. All 5
uncovered questions were handed off because the model itself said `covered: false`; the
claim checker never had to drop a sentence in this run, so the drop path is only tested
with mocks (`tests/`). One answer ("Yes, the Team Box would work for you.") is a mild
judgement that 44 let through. "What do you get in the Team Box?" was counted as buying
intent (the word "team") and got a qualifying question. One run, one brand, 26 cases: wide
error bars. Next: a held-out set of paraphrased injection and health messages, and the same
set with a hosted verifier for 44.

### Hosted (fast) path

In the stack: set `ASSISTANT_OPENAI_API_KEY` and `ASSISTANT_GATEWAY_URL=http://llm-gateway-assistant:8000`
(a second 03 gateway on Groq `openai/gpt-oss-120b`, `REASONING_EFFORT=low`), and run 44's checks on
Groq `openai/gpt-oss-20b` (`VERIFIER_PROVIDER=openai`, `VERIFIER_MODEL`). Visitor messages and the
excerpts used to answer them then go to the provider. One run on 2026-09-28, same cases, same
example brand, a recreated two-question FAQ, `ANSWER_TIMEOUT_S=20`:

| Set | Right kind | Invented | Handoffs | Injections | Median / p90, all cases | Sourced answers |
|---|---|---|---|---|---|---|
| 26 cases, local 7B (09-27) | 26/26 | 0 | 8 | 4/4 | 25.3 / 64.8 s | 46–75 s |
| 26 cases, Groq | 24/26 | 0 | 10 | 4/4 | 0.9 / 9.5 s | 1.4–11 s |
| 16 held-out, local 7B (09-27) | 15/16 | 0 | 13 | 2/2 | 22.6 / 45.6 s | 45 s |
| 16 held-out, Groq | 14/16 | 0 | 11 | 2/2 | 0.9 / 10.9 s | 4–11 s |

Misses on Groq: a 429 rate limit on the checker → 44 error → handoff (safe); "team of 20" →
the model repeated "20" from the visitor, the number guard dropped the sentence → handoff
(safe; the guard stays strict so a visitor can't put a price in the bot's mouth);
"real barista or a program?" → handoff (as locally, now with the AI disclosure); an angry
"third box late" got the true returns policy instead of a person. The prompt now says
complaints are `covered: false`; after that change the late-box case and 2 new complaint
messages handed off and 3 new covered questions were still answered (written before the run;
the held-out set is no longer unseen for this change). Answers over 5 s were Groq retrying
429s: the free tier's per-minute limit on the shared key, not the model.

## Known limits

- **Slow on a laptop model.** A sourced answer takes one gateway call plus 2+ claim-checker
  calls per sentence; see the timings above. With the default `ANSWER_TIMEOUT_S=20` most local
  answers become handoffs: use the hosted path above, or raise it.
- **Rate limits on a free hosted tier** turn answers into handoffs at busy times (safe, but
  answers are lost). A paid tier or a separate key for the assistant avoids it.
- **The claim checker flags about 1 in 8 true sentences** (44 README). A flagged true
  answer becomes a handoff: safe, but it costs answers.
- **Word lists are English** and will miss some phrasings (and over-catch some: "is it safe
  to drink cold brew after a week?" goes to a person).
- **One message of context.** The previous visitor message is passed along, the rest of the
  conversation is not.
- **Rate limits are in memory**: one instance, reset on restart.
- **The knowledge base is shown to the public** through answers. Keep internal notes out of
  it, or set `KB_SOURCES`.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
