# claim-checker

Deploy **44 of 90** of the local-LLM marketing agent. It flags statements in marketing copy
that your **approved facts don't support**: invented tasting notes, wrong prices, made-up
policies, awards and statistics.

Invented facts are the most common complaint about AI marketing tools, and in the US
the FTC requires a "reasonable basis" for ad claims. The quality gate (35) sends every
draft here; unsupported claims get rewritten or go to a human as a draft.

## How it decides

Evidence = the brand's approved facts (05 `GET /facts`) + the brief you gave for this
piece (`context`) + matching knowledge-base excerpts (06).

1. **Numbers are checked in code.** Every number in the copy must appear in the
   evidence. Details that are only a number plus units ("48 hours", "per 250 g bag")
   are also decided here, not by the model.
2. **Each sentence is split into its details by the LLM without seeing the evidence.**
   Lists are split too ("chocolate and caramel" → two details). Questions, calls to action
   and moods ("wake up and smell the coffee") give no details and aren't checked.
   A detail whose words all appear in one fact line is accepted in code.
3. **Each detail is looked up in the evidence by the LLM, which must quote where it's
   stated.** The quote has to really exist in the evidence (checked in code). A detail
   the model skips is asked again on its own; if it's still unanswered it counts as
   unsupported, because an empty answer must never mean "all fine".
4. **Values are compared in code, whatever the model says.** A detail's number words
   ("three blends"), weekdays and months must appear in the evidence, and a detail whose quote
   holds a different value of the same kind ("within 60 days" quoting "within 30 days") is flagged.
5. **Second chance for paraphrases** (lenient mode only). A detail judged not stated is asked
   once more, alone, against the 1–3 evidence lines that share most words with it (prompt
   `detail_entails`: "do these lines imply it? yes/no + quote"). A yes counts only if, in code,
   the quote is really in those lines, it contains every number/day/month of the detail, the
   detail's names and places (capitalised words, "UK") are in those lines, and a detail with
   every/all/any is backed by a quote that also has one. So it can only turn a false alarm into
   a pass; it cannot approve a changed value, a new place or a wider scope.
6. The LLM **never gives a yes/no verdict** on a whole claim. See below for why.

## How well it works (qwen2.5:7b, labelled claims in `23-eval-suite/cases/claims/`)

| Version | Invented claims caught | True claims flagged | Set |
|---|---|---|---|
| Ask the model "is this supported? yes/no" | 3 / 13 | 1 / 13 | first set (abandoned) |
| First sentence-level version | 6 / 8 | 1 / 8 | held-out |
| After splitting lists and re-asking skipped details | 6 / 6 | 1 / 6 | held-out (v5) |
| + questions skipped, all sets at that point | 48 / 49 | 14 / 49 | six sets (most used while tuning) |
| + non-facts (calls to action, moods) ignored, literal matches accepted in code | **5 / 6** | **0 / 9** | held-out **real-post sentences** |
| Current version, all 113 labelled claims | **53 / 55** | **7 / 58** | all seven sets |

**Hosted checker (hybrid: writing local, checking on Groq, `REASONING_EFFORT=low`, product scoping on):**

| Checker model | Invented caught | True flagged | Set |
|---|---|---|---|
| `openai/gpt-oss-20b` | **54 / 55** | **2 / 58** | same 113 claims (one run) |
| `openai/gpt-oss-20b` | 6 / 6 | 0 / 6 | held-out: details moved between products (v2) |
| `openai/gpt-oss-120b` | 6 / 6 | 0 / 6 | same held-out set (v2) |
| `openai/gpt-oss-120b`, before product scoping | 1 / 7 | 1 / 5 | details moved between products (v1) |
| `openai/gpt-oss-20b` / `120b` | 6 / 6 · 6 / 6 | 1 / 6 · 1–3 / 6 | v6 (120b varied between runs) |

**Paraphrase update (2026-09-28, `openai/gpt-oss-20b`, low effort):** value checks in code, the
second chance, and the alias `Rotation` for Single-Origin Rotation in the example brand. Before and
after ran through a record/replay cache, so both versions saw the **same model answers** for every
call they share; the differences come from the change, not run-to-run noise.

| Set | Invented caught before → after | True flagged before → after |
|---|---|---|
| **paraphrase v1 (held-out, written before this change ran)** | 8 / 10 → **9 / 10** | 3 / 14 → **2 / 14** |
| seven older sets (113 claims) | 54 / 55 → 54 / 55 | 3 / 58 → **0 / 58** |
| transfer v2 + v6 | 12 / 12 → 12 / 12 | 2 / 12 → **0 / 12** |
| **all 161** | 74 / 77 → **75 / 77** | 8 / 84 → **2 / 84** |
| paraphrase v1 (held-out), **local `mkt-writer` (qwen2.5:7b)**, same replay method | 7 / 10 → **8 / 10** | 5 / 14 → **2 / 14** |

Only the paraphrase row is held-out: the false alarms fixed in the other rows ("Swiss Water
process", "whenever you like", "somewhere new") are the examples this change was designed from.
The extra catch is the alias: "The Rotation is a medium roast" was checked against every
product's facts and passed on the Desk Blend's. Still wrong after the change: "Every order ships free" passes
(the model says free shipping on US subscriptions states it; scope is only guarded on the second
chance), and two true sentences stay flagged because the every/all guard refused the model's yes
("get all your money back", "every bag ships…"): the guard's price for blocking "every order".
On Groq the model already flagged every changed day and number word, so the value check changed
reasons, not verdicts, there (locally too); it is what catches them when the model says "stated"
(tests; those fail on the previous version). Locally "any bag, opened or not" also still passes.
Cost: the second chance asked 81 extra questions over the 161 claims (mostly details of invented
claims, which are rejected anyway): 39.7k tokens on top of 137.5k, about +29%.

The 20b model is the recommended hosted checker: as accurate here as 120b, cheaper, faster, and
on Groq it has its own daily token budget. The 113-claim run used about 90k tokens (~800 per claim).

Only the **held-out** rows are unbiased: those sets were written before that version
ran. The small sets mean wide error bars. Read it as: **it rarely lets an invented fact
through (about 1 in 25), and it flags roughly 1 in 8 true sentences**. A flagged true sentence gets
reworded by the quality gate or goes to you as a draft. That's the safer way to be
wrong for ad claims, but it's a real cost.

**Known gaps**
- **Right attribute, wrong value** ("roasted every Monday" when the fact says Tuesday)
  was the most frequent miss in earlier versions. Days, months and numbers are now compared in
  code; other values (roast level, colour, place) still rely on the model.
- **Paraphrases can be flagged** ("costs nothing" vs "no fee").
- **Vague value claims** ("a sustainable choice") are sometimes treated as mood, not fact.
- **Scope widening** ("every order ships free" vs free shipping on US subscriptions) can pass when
  the model calls it stated.
- Number words are checked in code except "one" ("one of our blends" is rarely a value).

What makes it work much better than anything above: **more and clearer facts** in
`05-brand-service/config/brand.yaml`. Every claim you want the agent to make should be
written there, one short sentence each.

## Where to deploy

**Docker host** running `01-marketing-stack`. n8n calls it at
`http://claim-checker:8000` (env `CLAIMS_URL`). It needs the gateway (03), the brand
service (05) and, optionally, the knowledge base (06).

## Run

```bash
docker build -t claim-checker .
docker run --rm -p 8144:8000 -e GATEWAY_URL=http://host.docker.internal:8103 \
  -e BRAND_URL=http://host.docker.internal:8105 claim-checker
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
GATEWAY_URL=http://localhost:8103 BRAND_URL=http://localhost:8105 uvicorn app.main:app --port 8144
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok","mode"}` |
| POST | `/verify` | `{"text","context"?,"extra_facts"?:[],"facts"?:[]}` | `{"ok","unsupported":[...],"claims":[{"claim","supported","reasons","evidence":[{"id","quote"}]}],"numbers":[{"value","supported"}],"evidence_lines"}` |

**Your own facts (`facts`, used by the task bridge 88).** Up to 200 lines, each at most 500
characters. When given, they are the approved facts (labelled `[s1]`, `[s2]` …) and 05 `/facts`
is **not** read; the brief (`context`), `extra_facts` and knowledge-base excerpts still add to them
as before, and product scoping (05 `/profile`) still applies. Slot keys (`[[weekday-rate]] =`) and
list dashes are removed from each line first, so a key is never counted as a number in the facts.
Without `facts` nothing changes.

**`evidence` per claim**: the lines that supported it, as `{"id": "s3", "quote": "..."}` (ids:
`f`/`s` facts, `x` extra facts, `c` brief, `k` knowledge base). It comes from the checks already
made: the line holding each number, the line a detail was literally found in, and the model's
quote (mapped back to its line) for details the model or the second chance accepted. Empty when
the claim is not supported.

```bash
curl -s localhost:8144/verify -H 'content-type: application/json' -d '{
  "text": "Meet our Swiss Water decaf: notes of chocolate and caramel, roasted within 24 hours."
}'
# {"ok": false, "unsupported": ["Meet our Swiss Water decaf: ..."],
#  "claims": [{"reasons": ["numbers not in the facts: 24", "not in the facts: caramel notes", ...]}], ...}
```

It takes about 2–10 s per sentence on a laptop, since each sentence needs 2+ LLM calls (plus one per detail found not stated, for the second chance).

## Configuration

| Env | Default | Meaning |
|---|---|---|
| `GATEWAY_URL` | `http://llm-gateway:8000` | runs the `claim_details` and `detail_check` prompts (04) |
| `BRAND_URL` | `http://brand-service:8000` | source of approved facts |
| `KB_URL` | empty | knowledge base; its matching excerpts also count as evidence |
| `CHECK_MODE` | `lenient` | `strict` also requires each detail's words to appear in its quote and has no second chance (the literal mode). It flagged a third of true sentences in testing. Value checks apply in both modes |
| `VERIFIER_MODEL` | empty | a different Ollama model for the checking prompts. Measure it with `python -m evalsuite.claims` before switching. (MiniCheck models are CC BY-NC, i.e. **not for commercial use**) |
| `KB_MIN_SCORE` | `0.35` | minimum knowledge-base search score to count as evidence |
| `KB_UNTRUSTED_SOURCES` | `trend-digest,competitor-watch` | knowledge-base sources that never count as evidence: they are LLM summaries of untrusted pages, so they could otherwise approve their own claims |
| `INTERNAL_API_KEY` | empty | when set (the stack sets it), `/verify` requires header `X-API-Key` with this value. Every call costs LLM time or hosted-model tokens, so it is not open to anything on the network |
| `MAX_TEXT_CHARS` | `20000` | longest `text` / `context` accepted (422 above it) |
| `MAX_SENTENCES` | `80` | most sentences checked in one request (413 above it; check long copy in parts). `extra_facts`: at most 50, each ≤ 1000 characters |

## Measure it yourself

```bash
cd ../23-eval-suite
python -m evalsuite.claims --checker http://localhost:8144
```

After you replace the example brand, write labelled claims about **your** facts
(`cases/claims/*.yaml`). The included ones describe the example coffee brand.

## CI

Runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on every push to `main`.
