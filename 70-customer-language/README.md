# customer-language

Deploy **70 of 83** of the local-LLM marketing agent. It is a "voice of customer" engine.
It collects what customers wrote or searched, finds the words they repeat, and gives writers
the phrases that fit a topic, so copy uses the customers' words rather than generic AI words.

- **Sources.** Reviews, search queries, survey answers, support messages, sales-call notes
  and comments. You import them, or pull them from `58-review-hub` and `67-gsc-sync`.
- **Phrases (no LLM).** Word n-grams (2–5 words) that appear in at least 2 different
  sources, with counts and the sources they came from.
- **Themes (LLM, checked in code).** Pains, desires, objections, triggers, outcomes and
  word choices. Each theme has quotes that are **exact parts** of the source they cite.
- **Relevant phrases** for a topic (embeddings plus keyword overlap, or keyword overlap alone when Ollama is down).
- **A headline bank.** Headlines and hooks that each reuse one customer phrase word for word.
- **Personas** built only from the themes. Every attribute cites theme or quote ids, and
  no names, ages or demographics are invented.

**Everything is grounded.** A phrase is shown in a form copied from a source. A theme quote
must be an exact substring of its source text, or code drops it (only case, whitespace and
apostrophe style may differ). A theme with fewer than 2 valid quotes from 2 different
sources is dropped. A persona attribute without a valid citation is dropped. This is the
same rule as the testimonials in 58, applied to mining.

## Why

The top complaint about AI marketing copy is that it sounds generic: "grammatically
correct but somewhat generic", "robotic and formulaic", "cookie-cutter"
(`_dev/research/commercial-tools.md`, `_dev/research/demand-users.md`). The standard fix
among conversion copywriters is voice-of-customer research, "review mining": read what
customers write, collect the exact words they repeat, and build the copy from those words.

- Joanna Wiebe, Copyhackers:
  [Amazon review mining for copywriting](https://copyhackers.com/2014/10/amazon-review-mining/).
  She recommends taking the message from prospects instead of writing it yourself. She
  reports a headline taken from reviews that got 400 % more clicks than the control.
- Copyhackers:
  [Google review mining, a shortcut to gathering VoC fast](https://copyhackers.com/how-to-do-rapid-fire-review-mining/).
  It uses search operators ("tired of", "frustrated by") to find customers' pain language.
- Intercom podcast:
  [Copy Hackers' Joanna Wiebe on crafting copy that converts](https://www.intercom.com/blog/podcasts/copy-hackers-joanna-wiebe/)
  (2017). Wistia email headlines built from review and interview language converted
  3.5× better than the original.

These are practitioner case studies, not controlled trials. The A/B eval below measures
what this deploy does with the local model.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing` network,
with a volume on `/data`. n8n calls it at `http://customer-language:8000` (env `VOC_URL`).
It calls the gateway (03) for themes, headlines and personas, Ollama for embeddings,
and, for `/sources/pull`, `review-hub` (58) and `gsc-sync` (67). It keeps state in
SQLite, so run exactly one instance.

## Run

```bash
docker build -t customer-language .
docker run --rm -p 8170:8000 --network marketing -e INTERNAL_API_KEY=change-me \
  -e GATEWAY_URL=http://llm-gateway:8000 -v voc-data:/data customer-language
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./voc.sqlite GATEWAY_URL=http://localhost:8103 \
  OLLAMA_URL=http://localhost:11434 uvicorn app.main:app --port 8170
```

The prompts `customer_themes`, `customer_headlines` and `customer_personas` must be in the
gateway's prompt library (04).

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| GET | `/stats` | — | source counts by kind, phrases, themes, quotes, the last `/mine` report |
| POST | `/sources/import` 🔑 | `[{"kind","source_ref","text","weight"?}]` (≤ 5000) | `{"added","updated","unchanged"}` |
| GET | `/sources` | `?kind=&limit=100` | `[source]` |
| POST | `/sources/pull` 🔑 | — | per source (`reviews`, `search_queries`): `{"ok","fetched","added","updated","unchanged"}` or `{"ok":false,"error"\|"skipped"}` |
| POST | `/mine` 🔑 | `{"min_sources":2,"min_n":2,"max_n":5,"ignore":[],"llm":true,"batch_size":30,...}` (all optional) | mining report (below) |
| GET | `/phrases` | `?min_sources=2&kind=review&limit=100` | `[{"id","phrase","words","sources","occurrences","weight","source_ids","kinds","examples"}]` |
| GET | `/themes` | `?kind=pain\|desire\|objection\|trigger\|outcome\|word_choice` | `[{"id","name","kind","sources","quotes":[{"id","source_id","source_kind","source_ref","quote"}]}]` |
| GET | `/relevant` | `?topic=...&k=8&kinds=phrase,quote&max_quote_words=12` | `{"topic","method":"hybrid"\|"keyword"\|"none","embed_model","items":[...],"customer_phrases"}` |
| POST | `/headlines` 🔑 | `{"topic","channel"?,"n":8,"k":8,"phrases"?,"facts"?,"max_chars":140}` | `{"phrases_offered","headlines":[{"text","phrase","phrase_id","source_ids"}],"dropped":[{"text","reason"}]}` |
| POST | `/personas` 🔑 | `{"n":3}` (2–4) | `{"built_from","note","personas","attributes_dropped","personas_dropped","labels_replaced"}` |
| GET | `/personas` | — | the last personas, `404` before the first |

### Sources

`kind` is `review`, `search_query`, `survey`, `support`, `sales_call` or `comment`.
`source_ref` is your id for it (e.g. `trustpilot:123`). An import is idempotent on
`(kind, source_ref)`: the same ref again updates the text if it changed. `weight`
(default 1) ranks phrases with the same number of sources. One invalid item rejects the
whole batch with `422`, and nothing is stored.

`POST /sources/pull` is fail-soft. Each source is reported separately, and one that is
unset, down or not synced (67 answers `409` before its first sync) doesn't stop the other.

- **58** `GET $REVIEWS_URL/reviews`: every review, whatever its status, as
  `kind=review`, `source_ref=review-hub:<id>`.
- **67** `GET $GSC_URL/queries/opportunities?min_impressions=10&min_position=1&max_position=100&limit=500`:
  the site's top queries by impressions (a wide filter, not only striking distance), as
  `kind=search_query`, `source_ref=gsc:<query>`, `weight = log10(impressions)` (at least 1).

### Mining (`POST /mine`)

1. **Phrases, in code.** The text is lowercased, curly apostrophes are made straight, and
   it is split into words. N-grams never cross sentence punctuation (`. ! ? ; : ,`,
   brackets, quotes, line breaks, spaced dashes). A hyphen inside a word doesn't break
   them. An n-gram may not start or end with a stopword ("the", "my", "is", ...). It may
   start with a negation ("doesn't taste like decaf") and end with a verb particle ("never
   run out", "turns up"). It is kept when it appears in `min_sources` different sources.
   Sources with the same normalized text count once. A shorter phrase is dropped when a
   longer kept phrase contains it and comes from exactly the same sources. The phrase is
   shown in its most common verbatim form, and `examples` gives the exact span in up to 5
   sources. `ignore` drops brand words (product names are not customer language), e.g.
   `["Desk Blend", "Team Box"]`.
2. **Themes, LLM plus code.** Sources are sent in batches of `batch_size`, in a fixed mixed
   order, so reviews and searches share a batch. The batch goes to prompt
   `customer_themes` as `[s12] (review) text` lines. For each proposed quote, code checks
   the following. The `source_id` must be in that batch, otherwise `unknown_source`. The
   quote must be an exact part of that source's text, otherwise `not_verbatim` (a
   paraphrase, "..." stitching, fixed grammar). It must have at least 2 words, otherwise
   `too_short`. The **source's** span is stored, not the model's copy. Themes with the
   same name (ignoring case and `_`/`-`), or with the same kind and a shared quote, are
   merged across batches. A theme with fewer than `min_quotes` (2) valid quotes, or from
   fewer than `min_theme_sources` (2) sources, is dropped.

A mine replaces all phrases. It replaces all themes when at least one batch reached the
gateway. If every batch failed, the previous themes stay, and the errors are in
`themes.llm_errors`. The report:

```json
{"sources":75,"phrases":{"kept":37,"min_sources":2,"n":[2,5]},
 "themes":{"ran":true,"batches":3,"proposed":24,"kept":16,"merged":0,"dropped":[{"name":"price increase","kind":"objection","valid_quotes":1}],
           "quotes_proposed":47,"quotes_valid_in_kept_themes":39,"quotes_dropped":{"not_verbatim":5},"quotes_dropped_total":5}}
```

### Relevant phrases (`GET /relevant`), the contract for writers

The candidates are the mined phrases and theme quotes of at most `max_quote_words` (12)
words, deduplicated. With Ollama up, it ranks them by cosine similarity between
a hybrid score (`method: "hybrid"`):

    score = 0.6 × cosine(EMBED_MODEL vectors of topic and candidate)
          + 0.4 × Jaccard(content-word stems of topic, content-word stems of candidate)

Each item also carries `embedding_score` and `keyword_jaccard`. The keyword term is there
because `qwen3-embedding:0.6b` alone put short category phrases ("coffee subscription") near
the top for most topics; Jaccard rewards a phrase for the topic's own words and penalizes
words the topic doesn't have. `HYBRID_EMBED_WEIGHT` (default `0.6`) sets the split; `1` is
pure embedding. Vectors are cached in SQLite (key = model + text), so only new texts are
embedded. With Ollama down, it ranks by stemmed keyword overlap (`method: "keyword"`) and
drops candidates with none.

```bash
curl -s "localhost:8170/relevant?topic=Swiss%20Water%20decaf%20for%20the%20afternoon&k=3"
# {"topic":"...","method":"hybrid","embed_model":"qwen3-embedding:0.6b",
#  "items":[{"id":"q46","type":"quote","text":"decaf that doesn't taste like decaf","sources":1,"source_ids":[42],
#            "theme":"decaf taste","theme_kind":"desire","embedding_score":0.7487,"keyword_jaccard":0.2,
#            "score":0.5292}, ...],
#  "customer_phrases":"- decaf that doesn't taste like decaf\n- decaf that doesn't taste\n- afternoon cup"}
```

**Wiring contract (26 social writer, 65 engine drafter; to be done):** before calling the
gateway, `GET $VOC_URL/relevant?topic=<topic>&k=8`, and pass `customer_phrases` from the
response unchanged as the `customer_phrases` var of `social_posts`. The prompt then says:
"Customers describe this in their own words as: … Reuse one of these phrases where it fits
naturally; never put words in a customer's mouth or present them as a quote." Omit the var
(or pass `""`) when `VOC_URL` is empty, the call fails, or `items` is empty. The prompt then
renders exactly as before. Don't block a draft on this service. Posts that put customer
words in quotation marks are flagged by 58 `/testimonials/check` like any other quote.

**Asking is not enough; enforce the opening phrase.** Measured: with `customer_phrases`
alone the local writer reused a given phrase in 2–3 of 10 posts (the same lesson as the
emoji policy: small models ignore soft instructions). So the writer should also:

1. Pick one phrase **in code**: the first `/relevant` item that is at most 8 words, has no
   sentence punctuation inside (`. ! ? ; :`), no quotation marks, and is not already in the
   topic, brand summary or facts. Strip trailing punctuation.
2. Pass it as `open_with` to `social_posts` (with `customer_phrases` as above). The prompt
   says the first sentence of every post must contain it word for word, not in quotation
   marks, not attributed to a customer.
3. **Check in code** that each post's first sentence (up to the first `. ! ? …` followed by a
   space, or a line break) contains the phrase: case-insensitive, whitespace- and
   apostrophe-normalized, whole words. On a miss, call once more with the same vars plus
   `open_with_feedback` = `Your previous answer broke that rule: its first sentence was "…".
   Write the post again so that its first sentence contains "<phrase>" word for word.`
4. If the retry misses too, keep the retry's post (don't block the draft) and note it.

Reference implementation: `23-eval-suite/evalsuite/voc_ab.py` (`pick_open_with`,
`first_sentence`, `opens_with`, `open_with_feedback`, `write_b`).

### Headline bank (`POST /headlines`)

The service offers the model the `k` most relevant items (or the `phrases` you pass: each
must be a mined phrase or quote, otherwise `422`), labelled `[p1] ...`. It calls
`customer_headlines` with `facts` set to what you passed (default empty, so the gateway
doesn't add all brand facts). Then code keeps a headline only when all of these hold:

- it contains one of the offered phrases word for word (case and spacing may differ, whole
  words only). `phrase` is the one it actually contains, not the one the model claimed;
- it has no number that is not in the phrases, the topic or `facts`;
- it has no quotation marks (customer words are reused as copy, never presented as a quote);
- it is at most `max_chars` long, and not a duplicate.

Everything else is listed in `dropped` with a reason. Sample (local model, fictional data,
topic "Decaf you actually want to drink in the afternoon"): 4 kept, e.g. *"Decaf that
doesn't taste like decaf, really?"* (from a search query) and *"Our perfect afternoon cup"*
(3 sources). 4 dropped for having no customer phrase ("Decaf that actually tastes good!").

### Personas (`POST /personas`)

Themes and their quotes go to `customer_personas` as `[t3] name (kind, N customers)` with
`[q12] quote` lines. Code then checks every item of `goals`, `pains`, `objections` and
`words_they_use`:

- it must cite at least one existing theme (`t3`) or quote (`q12`), or it is dropped;
- `words_they_use` must be an exact part of a cited quote (the quote's span is stored);
- a number not in the cited quotes, or an age ("34-year-old", "in her 30s") not in the
  sources, drops the item;
- a label with a number, an age or a capitalized word that appears in no source ("Sarah",
  "Brooklyn") becomes `Persona N`;
- a persona with fewer than 2 grounded items is dropped.

Each kept item carries `evidence`: the verbatim quotes with their source ids.
`built_from.sources` is the number of distinct real sources behind the themes, and `note`
says so, e.g. "Built only from 35 real customer sources (16 themes)". A persona is a
pattern of needs, not a character.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for write endpoints; also sent to the gateway, 58 and 67. Not set → writes return `503`; wrong/missing key → `401` |
| `DB_PATH` | `/data/voc.sqlite` | SQLite file (WAL; writes use `BEGIN IMMEDIATE`) |
| `GATEWAY_URL` | `http://llm-gateway:8000` | 03 LLM gateway |
| `GATEWAY_TIMEOUT` | `300` | seconds per gateway call (a mining batch takes 30–60 s on qwen2.5:7b) |
| `REVIEWS_URL` | empty | 58 review-hub for `/sources/pull`; empty = skipped |
| `GSC_URL` | empty | 67 gsc-sync for `/sources/pull`; empty = skipped |
| `PULL_GSC_MIN_IMPRESSIONS` / `PULL_GSC_LIMIT` | `10` / `500` | query filter for the 67 pull |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | embeddings for `/relevant` |
| `EMBED_MODEL` | `qwen3-embedding:0.6b` | same as 61 |
| `EMBED_TIMEOUT` | `60` | seconds |
| `HYBRID_EMBED_WEIGHT` | `0.6` | `/relevant` score = this × cosine + (1 − this) × keyword Jaccard |

## Sample data (tests and eval only)

`samples/northwind_sources.json` is **FICTIONAL EXAMPLE DATA** for the example brand
Northwind Roasters (05): 40 reviews, 25 search queries and 10 support messages, written for
this repo and not copied from anywhere. Don't import it into a production database; use a
scratch `DB_PATH`. On it, with `ignore` = the four product names: 37 phrases (e.g.
"arrived late" in 8 sources, "doesn't taste like decaf" 5, "pause my subscription" 5,
"pile of bags" 3, "worth every penny" 2). The LLM step (qwen2.5:7b via `mkt-writer`, 3
batches) proposed 24 themes with 47 quotes. Code dropped 5 non-verbatim quotes and 3
themes left with 1 valid quote, and kept 16 themes with 39 quotes. All 39 kept quotes are
exact substrings of their sources (checked by script).

## Measured: does it make posts less generic?

`23-eval-suite`: `python -m evalsuite.voc_ab --setup`. 10 held-out topics; A = `social_posts`
without `customer_phrases`, B = with `/relevant` for the topic; the local model writes and
judges; blind, both orders. Two runs on 2026-09-27:

| Run | Posts with a given customer phrase A / B | Judge B / A / tie | Judge same position both orders | Generic AI words A / B |
|---|---|---|---|---|
| 1 | 2/10 / 3/10 | 4 / 1 / 5 | 5/10 | 1 / 0 |
| 2 | 1/10 / 2/10 | 2 / 0 / 8 | 8/10 | 0 / 0 |

**Honest reading: no clear effect yet.** The local writer reused a given phrase in only 2–3
of 10 posts, and the local judge mostly picked a position rather than a post. B was never
worse, and no post quoted customers, but 10 topics and a position-biased judge can't
separate these. Run the judge on a stronger model (`--judge-gateway`) before drawing a
conclusion.

**Second pass, with `open_with` enforced** (same day; details in the 23 README). B
reused the phrase in the first sentence in 10/10 (seen topics) and 6/8 (8 new held-out
topics) posts, against 1/10 and 0/8 for A. Judge on Groq `gpt-oss-120b` (no position bias:
0–1 same-position picks): B 8 / A 1 / tie 1, and B 6 / A 2 / tie 0. Local judge: 4/0/6 and
3/0/5, still mostly picking a position. **But** in 13 of the 16 enforced posts the phrase
is pasted as a label ("box arrived late We're sorry…"), and the judge's reasons mostly
cite the echoed phrase, which is often also in the quotes it is shown. So: the mechanism
works, the posts are not yet clearly better.

## Known limits

- **Verbatim is not the same as on-topic.** The code proves each quote is really in its
  source, not that it belongs to the theme. On the sample, a few quotes sit under the wrong
  theme ("Too expensive for daily drinking" under "coffee freshness"). Read `/themes` before
  you build copy on a theme.
- **Relevance is weak with `qwen3-embedding:0.6b`.** Short phrases that share a
  category word ("coffee subscription") rank high for most topics. For "pausing a delivery
  before you travel" it offered "arrived late" rather than "pause my subscription". A query
  instruction prefix and scoring by source context didn't fix it (tried on topics outside
  the eval). The hybrid score (0.6 embedding + 0.4 keyword Jaccard) now puts "pause my
  subscription" first for "What happens when you cancel your subscription" and "never run
  out" for "Never running out of coffee…", but a generic phrase can still win when the
  topic shares no word with any phrase ("coffee subscription" for "Getting better coffee
  out of a cheap drip machine"), and a complaint can be picked ("box arrived late" for
  "Changing how often your box arrives").
- **Complaints are offered too.** `/relevant` doesn't filter by sentiment, so a pain quote
  ("Arrived late again. Third time.") can be offered to a writer. The prompt says reuse
  "where it fits naturally". Filter by `theme_kind` if you want only positive language.
- **Emoji in quotes.** The gateway removes emoji outside the brand's policy from every JSON
  string (`EMOJI_POLICY_SKIP`). A quote containing one then fails the verbatim check and is
  dropped. Add `customer_themes` to `EMOJI_POLICY_SKIP` if your sources use emoji.
- **English only** (stopwords, stemming).

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
