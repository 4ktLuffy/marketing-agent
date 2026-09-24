# review-hub

Deploy **58 of 60** of the local-LLM marketing agent. It holds three things:

- **A reviews inbox.** Reviews you import from Google, Trustpilot or by hand, each with a
  status (`new`, `drafted`, `replied`, `ignored`).
- **A proof bank.** Testimonials that are word-for-word quotes from real reviews or
  messages, with a record of whether the customer agreed to be quoted.
- **Reply context.** Everything the `review_reply` prompt (04) needs to draft a reply,
  plus flags for routing it (negative review, mentions a problem).

It uses no LLM. It never scrapes a review platform and never posts anything: **a person
posts every reply**, after reading the draft.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a volume on `/data`. n8n calls it at `http://review-hub:8000`
(env `REVIEWS_URL`). It keeps state in SQLite, so run exactly one instance and back up
the volume.

## Run

```bash
docker build -t review-hub .
docker run --rm -p 8158:8000 -e INTERNAL_API_KEY=change-me -v review-data:/data review-hub
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./reviews.sqlite uvicorn app.main:app --port 8158
```

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/reviews/import` 🔑 | `[{"source","external_id","author","rating","text","created_at"}]` (≤ 1000) | `{"imported","duplicates","ids","skipped"}` |
| GET | `/reviews` | `?status=new\|drafted\|replied\|ignored&min_rating=&max_rating=` | `[review]`, newest first |
| GET | `/reviews/{id}` | — | review |
| POST | `/reviews/{id}/status` 🔑 | `{"status"}` | review |
| GET | `/reviews/{id}/reply-context` | — | `{"review_id","source","review","rating","author_first_name","negative","mentions_problem","problem_words","needs_human","escalate_words","vars"}`. `needs_human` is decided in code from health/safety/legal words (sick, allergic, hospital, glass, lawyer, lawsuit, chargeback, …): route those to a person even if the model's reply says otherwise (the model missed 1 of 3 health cases in the eval) |
| POST | `/testimonials/{id}/consent` | key | `{"consent": false, "note": "asked by email"}` records consent withdrawn (or given); a withdrawn quote stops being usable immediately and `/testimonials/check` flags it |
| POST | `/testimonials` 🔑 | `{"quote","author_display","source_review_id"?,"consent","consent_note"?}` | testimonial, `201` |
| GET | `/testimonials` | `?usable=true` (only `consent=true`) · `?usable=false` · none = all | `[testimonial]` |
| POST | `/testimonials/check` | `{"text"}` | `{"ok","quotes":[{"text","start","end","ok","testimonial_id","reason"}],"violations":[...]}` |

A review is `{id, source, external_id, author, rating, text, created_at, status,
imported_at, updated_at}`. A testimonial is `{id, quote, author_display,
source_review_id, consent, consent_note, created_at}`.

```bash
curl -s localhost:8158/reviews/import -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '[{"source":"google","external_id":"g-1","author":"Maya Chen","rating":5,
        "text":"The Desk Blend is the best part of my workday.","created_at":"2026-09-20"}]'
# {"imported":1,"duplicates":0,"ids":[1],"skipped":[]}

curl -s localhost:8158/testimonials -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"quote":"the best part of my workday","author_display":"Maya, remote team lead",
       "source_review_id":1,"consent":true,"consent_note":"said yes by email, 2026-09-21"}'

curl -s localhost:8158/testimonials/check -H 'content-type: application/json' \
  -d '{"text":"Customers say “the best coffee of my workday”."}'
# {"ok":false,...,"violations":[{"text":"the best coffee of my workday","ok":false,
#   "testimonial_id":null,"reason":"not a verbatim consented testimonial (invented or paraphrased)",...}]}
```

### How the parts behave

- **Import is idempotent** on `(source, external_id)`. A review already stored is left
  exactly as it is (text and status), and is listed in `skipped`. The same `external_id`
  on another source is a different review. `source` is `google`, `trustpilot` or
  `manual`; `rating` is an integer 1–5 (`"5"` is a 422 error); `created_at` is ISO 8601
  and is stored as UTC `YYYY-MM-DDTHH:MM:SSZ` (no offset means UTC). One invalid review
  rejects the whole batch with 422, and nothing is stored.
- **Status** can move between any of the four values; the service doesn't enforce an
  order. n8n sets `drafted` when a reply draft goes to the approval form; the person who
  posts the reply sets `replied`.
- **Reply context.** `negative` is `rating <= 2`. `mentions_problem` is true when the
  text contains one of a short list of whole words (late, delayed, broken, damaged,
  refund, missing, wrong, never arrived, stale, cancel, ...); `problem_words` lists the
  ones found. It is a routing flag, not a verdict. `author_first_name` is the first word
  of the author name, with titles (Dr, Mr, Ms, ...) skipped; it is `null` for anonymous
  or initial-only authors ("A Google user", "J."). `vars` is ready to send as the
  gateway's `vars` for prompt `review_reply` (a missing first name becomes `"there"`).
- **Testimonials are verbatim.** With `source_review_id`, `quote` must be an exact
  substring of that review's text, or the request fails with 422. Before comparing,
  runs of whitespace become one space and curly apostrophes (’) become straight ones.
  Nothing else is changed: case, punctuation and words must match, so a paraphrase, a
  quote stitched together with "..." or an invented line is refused. A review that
  doesn't exist is 404. Without `source_review_id` (a quote from an email, say), the
  quote is stored as given; the person entering it is responsible for it being verbatim.
- **Consent.** `consent` is a required boolean (`"yes"` is a 422 error). Record how and
  when it was given in `consent_note`. Only `consent=true` testimonials are usable, and
  `/testimonials/check` accepts only those.
- **Check.** It finds every span in double quotes, straight or curly, in any pairing
  (`"…"`, `“…”`, `„…“`). A span is fine when it is part of a consented testimonial,
  word for word (same normalization as above). Otherwise it is a violation: `reason` is
  `testimonial has no consent to quote` (it matches a testimonial without consent) or
  `not a verbatim consented testimonial (invented or paraphrased)`. Scare quotes
  (`a “fresh” take`) are flagged too: rephrase them without quotation marks. `start` and
  `end` are character offsets of the span, quotes included. The quality gate can call
  this on any draft.

### Why testimonials are checked in code

The FTC's rule on fake reviews and testimonials (16 CFR Part 465, in force since
October 2024) bans reviews and testimonials that misrepresent that they come from a real
customer or misrepresent that customer's experience, AI-written ones included, and it
also covers suppressing negative reviews
([FTC announcement](https://www.ftc.gov/news-events/news/press-releases/2024/08/federal-trade-commission-announces-final-rule-banning-fake-reviews-testimonials)).
So the agent may **pick** quotes from the proof bank, never write, paraphrase or combine
them, and every quote in a draft is checked against the bank word for word. Review
requests must not be sent only to happy customers, and replies never ask for a better
rating (see the `review_reply` prompt in 04). This is a design constraint, not legal
advice.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for write endpoints. If it is not set, writes return `503`. A wrong or missing key returns `401`. |
| `DB_PATH` | `/data/reviews.sqlite` | SQLite file (WAL mode; writes use `BEGIN IMMEDIATE`, so parallel imports never duplicate a review) |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
