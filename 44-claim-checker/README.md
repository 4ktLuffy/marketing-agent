# claim-checker

Deploy **44 of 53** of the local-LLM marketing agent. It flags statements in marketing copy
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
4. The LLM **never gives a yes/no verdict** on a claim. See below for why.

## How well it works (qwen2.5:7b, labelled claims in `23-eval-suite/cases/claims/`)

| Version | Invented claims caught | True claims flagged | Set |
|---|---|---|---|
| Ask the model "is this supported? yes/no" | 3 / 13 | 1 / 13 | first set (abandoned) |
| First sentence-level version | 6 / 8 | 1 / 8 | held-out |
| After splitting lists and re-asking skipped details | 6 / 6 | 1 / 6 | held-out (v5) |
| + questions skipped, all sets at that point | 48 / 49 | 14 / 49 | six sets (most used while tuning) |
| + non-facts (calls to action, moods) ignored, literal matches accepted in code | **5 / 6** | **0 / 9** | held-out **real-post sentences** |
| Current version, all 113 labelled claims | **53 / 55** | **7 / 58** | all seven sets |

Only the **held-out** rows are unbiased: those sets were written before that version
ran. The small sets mean wide error bars. Read it as: **it rarely lets an invented fact
through (about 1 in 25), and it flags roughly 1 in 8 true sentences**. A flagged true sentence gets
reworded by the quality gate or goes to you as a draft. That's the safer way to be
wrong for ad claims, but it's a real cost.

**Known gaps**
- **Right attribute, wrong value** ("roasted every Monday" when the fact says Tuesday)
  was the most frequent miss in earlier versions.
- **Paraphrases can be flagged** ("costs nothing" vs "no fee").
- **Vague value claims** ("a sustainable choice") are sometimes treated as mood, not fact.
- **Number words** ("three blends") aren't checked in code; only the LLM step sees them.

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
| POST | `/verify` | `{"text","context"?,"extra_facts"?:[]}` | `{"ok","unsupported":[...],"claims":[{"claim","supported","reasons"}],"numbers":[{"value","supported"}],"evidence_lines"}` |

```bash
curl -s localhost:8144/verify -H 'content-type: application/json' -d '{
  "text": "Meet our Swiss Water decaf: notes of chocolate and caramel, roasted within 24 hours."
}'
# {"ok": false, "unsupported": ["Meet our Swiss Water decaf: ..."],
#  "claims": [{"reasons": ["numbers not in the facts: 24", "not in the facts: caramel notes", ...]}], ...}
```

It takes about 2–10 s per sentence on a laptop, since each sentence needs 2+ LLM calls.

## Configuration

| Env | Default | Meaning |
|---|---|---|
| `GATEWAY_URL` | `http://llm-gateway:8000` | runs the `claim_details` and `detail_check` prompts (04) |
| `BRAND_URL` | `http://brand-service:8000` | source of approved facts |
| `KB_URL` | empty | knowledge base; its matching excerpts also count as evidence |
| `CHECK_MODE` | `lenient` | `strict` also requires each detail's words to appear in its quote. It catches more wrong values but flagged a third of true sentences in testing |
| `VERIFIER_MODEL` | empty | a different Ollama model for the checking prompts. Measure it with `python -m evalsuite.claims` before switching. (MiniCheck models are CC BY-NC, i.e. **not for commercial use**) |
| `KB_MIN_SCORE` | `0.35` | minimum knowledge-base search score to count as evidence |

## Measure it yourself

```bash
cd ../23-eval-suite
python -m evalsuite.claims --checker http://localhost:8144
```

After you replace the example brand, write labelled claims about **your** facts
(`cases/claims/*.yaml`). The included ones describe the example coffee brand.

## CI

Runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on every push to `main`.
