# Evaluation

How the agent's quality is measured, and what the measurements say. Every figure on this page
comes from a run recorded in the repo: `CHECKPOINT.md`, `START-HERE.md`, `_dev/NIGHT-LOG.md`
(build log for 2026-09-23), or the deploy READMEs. Case files are in `23-eval-suite/cases/`.

## How quality is measured

The eval suite (deploy 23) has three instruments, all run against the live gateway and model.

| Instrument | Command | What it measures |
|---|---|---|
| Content eval | `python -m evalsuite.run` | Does a prompt produce copy you could publish: limits, channels, banned phrases, no invented numbers |
| Claim checker eval | `python -m evalsuite.claims` | How many invented claims the fact checker catches, and how many true ones it wrongly flags |
| Tool selection eval | `python -m evalsuite.tools` | Does the chat agent pick the right tool first. Default set: 24 realistic requests (`cases/tools/requests.yaml`); `--cases heldout_v2` runs a held-out set of 20. `--provider openai` runs it against a hosted API |

### Content checks are code, not an LLM judge

Each case in `cases/*.yaml` is a prompt, fixed inputs, and checks that run in code:

| Check | Fails when |
|---|---|
| `max_chars` / `min_chars` | a selected value is outside the length |
| `contains` / `not_contains` | a required string is missing, or a forbidden one appears |
| `count` | the number of items is out of range |
| `numbers_from_input` | the output contains a number that is not in the input (invented stat or price) |
| `brand_ok` | the brand service reports an error-level violation |
| `platform_ok` | platform rules reject the post for its channel |
| `channels_match` | the posts do not cover exactly the requested channels |

A case passes a run only if the gateway answered and every check passed.

### Repeats, and why a single green run means little

Prompts run at temperature above 0, so every case runs several times (3 by default) and the
suite reports a pass rate. `START-HERE.md` states the uncertainty: with 30 runs, 87% has an
error bar of roughly plus or minus 12 points, so it reads as "most outputs pass", not a precise
score. Before comparing two models, the recommendation is `--repeats 10`.

The per-case column below ("2/3", "3/3") is the stricter view: a case that must work every
time for a user to trust it needs all its runs to pass. On the 10-case run, 6 of 10 cases
passed all 3 runs, even though the run-level rate was 87%. That gap is why the per-case numbers
are reported, not just the average.

### Held-out sets, written before the run

The claim checker was tuned on labelled sets. Tuning on a set and reporting on the same set
overstates accuracy, so each new version was measured on a **held-out set written before that
version ran**. Only those rows are unbiased. The sets are in
`23-eval-suite/cases/claims/` (`northwind_holdout.yaml`, `northwind_v5.yaml`,
`northwind_prose.yaml`, `northwind_v6.yaml`, ...). The v6 file states in its header that it was
written before that version ran, on either model.

The same rule now applies to tool selection. After a tool-description change dropped the local
score from 72/72 to 70/72, a held-out set (`cases/tools/heldout_v2.yaml`, 20 new requests) was
written **before** changing the descriptions again. Its header says not to tune descriptions
to it. No result on it is recorded yet.

### Negative controls

A check that cannot fail proves nothing. The negative controls used:

- **Both error directions for the fact checker.** Every labelled set mixes supported claims
  (must pass) with invented ones (must be caught). "Caught" and "flagged true" are always
  reported together; a checker that flags everything would score 100% caught.
- **Regression tests that fail on the old code.** The migration race fix has a test that fails
  3/3 on the old code. The gateway cache fix has a test that fails on the old code. The
  calendar's overflow fix (ids of 2^63 and above) has a test that the old `main.py` fails. The
  SSRF fix for IPv6 addresses that embed an IPv4 address was checked with a negative control.
- **Refusal cases.** The knowledge-base answer case asks something the context does not contain
  and passes only if the model refuses. The chat agent was asked to "approve item 2" and must be
  refused.
- **Hand calculation.** The weekly report's numbers were compared to a hand calculation from
  the raw CSV: identical.

## Results

All local results use `qwen2.5:7b` on an M-series Mac with 16 GB.

### Local vs hosted (Groq `openai/gpt-oss-120b`)

| Measure | Local `qwen2.5:7b` | Groq `gpt-oss-120b` | Source |
|---|---|---|---|
| Fact checker, held-out set v6 (12 claims): invented claims caught | 5/6 | 6/6 | NIGHT-LOG, `01-marketing-stack/README.md` |
| Fact checker, held-out set v6: true sentences flagged | 3/6 | 1/6 | same |
| Social posts prompt, JSON | not part of this comparison | empty JSON at default reasoning effort; 4/4 at `reasoning_effort=low` | `03-llm-gateway/README.md` |
| Gateway call time | 2-35 s per call (blog and multi-channel social posts about 30 s) | not recorded | `03-llm-gateway/README.md` |
| Chat agent turn (hosted variant in local n8n) | not recorded | calendar question 3.2 s, campaign list 2.1 s | NIGHT-LOG cycle 1 |

Notes:
- The v6 set has 6 claims per label. The flagged-true difference (3/6 vs 1/6) is the larger
  signal, but it is not a precise rate. This result is why the stack has a separate verifier
  gateway: fact checking can run on the hosted model while writing stays local.
- **No tool-selection result on Groq is recorded** in the sources above. Run
  `python -m evalsuite.tools --provider openai ...` before quoting one.
- **Rate limits on the free tier.** Groq's free tier allows 8k tokens per minute. One
  multi-tool chat turn (about 2k prompt tokens per model call, 4-5 calls) reaches it. n8n's
  OpenAI node does not retry a `429`, so without a fallback 3 of 3 turns in a burst failed.
  With the local model as fallback, the turn in which the 5th call hit a `429` was finished by
  the local model (execution verified).

### Claim checker versions (local `qwen2.5:7b`)

From `44-claim-checker/README.md`. Only held-out rows are unbiased.

| Version | Invented caught | True flagged | Set |
|---|---|---|---|
| Ask the model "is this supported? yes/no" | 3/13 | 1/13 | first set (abandoned) |
| First sentence-level version | 6/8 | 1/8 | held-out |
| Split lists, re-ask skipped details | 6/6 | 1/6 | held-out (v5) |
| + questions skipped, all sets at that point | 48/49 | 14/49 | six sets, mostly used for tuning |
| + calls to action and moods ignored, literal matches accepted in code | **5/6** | **0/9** | held-out real-post sentences |
| Current version, all labelled claims | **53/55** | **7/58** | all seven sets (113 claims) |

Read it as: it rarely lets an invented fact through (about 1 in 25) and flags roughly 1 in 8
true sentences. A flagged true sentence gets reworded by the quality gate or goes to a human as
a draft. For ad claims that is the safer direction to be wrong, but it is a real cost.

The 44 README counts seven sets; `cases/claims/` now holds eight files. The README does not
say whether the 113-claim row was re-measured after the claim-checker prompt changes that
NIGHT-LOG records with set v6. v6 results are in the local vs hosted table above.

`CHECK_MODE=strict` (each detail's words must also appear in its quote) catches more wrong
values but flagged about a third of true sentences in testing, so `lenient` is the default.

### Tool selection (chat agent, `python -m evalsuite.tools`)

| Configuration | Right tool first |
|---|---|
| 11 tools | 88% |
| 13 tools (after adding 2 campaign tools) | 96% |
| 13 tools, after sharpening the knowledge-base tool's description | 100% (72/72) |
| 13 tools, after changing the repurpose and social tool descriptions (current) | **70/72**; both misses were "Instagram caption", where no tool was called |

Model comparison, same 24 requests and 13 tools, before the latest description change
(`02-ollama-models/README.md`):

| Model | Right tool | Notes |
|---|---|---|
| `qwen2.5:7b` (kept) | 72/72 (3 runs) | |
| `qwen3.5:4b-mlx` | 45/48 (2 runs) | smaller, slightly worse |
| `granite4:7b-a1b-h` | 43/48 (2 runs) | calls tools for small talk ("What can you do?") |

### Content eval (`python -m evalsuite.run`)

Most recent recorded run (`CHECKPOINT.md`), 14 cases x 3 runs: **35/42 = 83%**.
(`cases/*.yaml` holds 16 cases now; no run of the full current set is recorded.)

| Slice | Result |
|---|---|
| Learning cases (rule reflection) | 12/12 |
| Original 10 cases | 23/30, down from 26/30; within 3-run noise |

Failures in that run: invented numbers from the bare blog prompt, a link dropped by the bare
social prompt (workflow 26 restores it; the case tests the raw prompt), and a knowledge-base
answer that left out "30".

Earlier per-case run on the original 10 cases (`START-HERE.md`), 26/30 = 87%, up from 80%
before the fixes found during testing:

| Case | Passed | What failed |
|---|---|---|
| ad copy within Google limits | 2/3 | a "3" not in the input |
| social posts: channels, limits, link | 2/3 | link missing from 2 posts (the workflow re-adds it) |
| social posts: no invented stats | 3/3 | |
| email subject and preheader lengths | 3/3 | |
| blog structure, no invented numbers | 2/3 | a "10" not in the input |
| rewrite removes banned claims | 3/3 | 0/6 before the prompt change |
| KB answer refuses when it does not know | 3/3 | |
| KB answer cites its source | 2/3 | answer left out the "30 days" |
| keyword clusters use only real keywords | 3/3 | |
| competitor summary, no invented prices | 3/3 | |

Rule reflection was checked separately: 20/20 correct after a prompt fix (style edits become
rules; fact fixes do not), stored as cases in `cases/learning.yaml`.

### System checks

| What | Result | When |
|---|---|---|
| Unit tests across 21 deploys | 370 passed | Phase 1 handover (`START-HERE.md`) |
| Content calendar (19) unit tests after the overflow fix | 116 passed | NIGHT-LOG cycle 1 |
| Workflows import and publish in n8n 2.40.5 | 20/20 | Phase 1 handover |
| Tool workflows 25-35 end to end with the real model | all 11 work | Phase 1 handover |
| Campaign drafter (48) | 7/7 drafts reached `in_review`; one with an unsupported claim held as draft | Phase 2 |
| Weekly report numbers vs hand calculation | identical | Phase 1 handover |
| Service images simulated from `requirements.txt` in clean Python 3.12 | 22/22 healthy | Phase 2 (before 54, 55 and the verifier gateway existed) |
| Quality gate removing a non-whitelisted emoji from a real draft | 3/3 | after the first push |
| Hosted chat variant in local n8n: calendar question with the correct tool call, campaign list | both answered | NIGHT-LOG cycle 1 |

54 (Postiz bridge) and 55 (Umami sync) have unit tests that mock the external APIs; neither
has been run against a live Postiz or Umami. 56 is an n8n workflow; its CI checks the JSON and
node connections.

## Limitations and failures

- **Small sets, wide error bars.** Held-out sets have 6 to 9 claims per label. Differences of
  one claim are noise.
- **The fact checker has known gaps**: right attribute with the wrong value ("roasted every
  Monday" vs Tuesday), paraphrases flagged ("costs nothing" vs "no fee"), vague value claims
  ("a sustainable choice") treated as mood, number words ("three blends") not checked in code.
- **Automatic fixes do not always succeed.** On the hardest test, about 1 in 6 quality-gate
  rewrites did not fix the problem; those items stay `draft` for a human.
- **Invented numbers still appear in bare prompts** (blog, ad copy). The workflows catch them
  with the number check; the prompt alone does not prevent them.
- **Tight formats read badly** sometimes: "Boost your workday! Free first" in a 30-character
  ad headline.
- **The 7B model adds vague hype** ("expert roasters who hand-roast") that no check catches.
- **Tool choice dropped to 70/72** after the latest description change. The held-out set v2
  was written before any further change, so the next change can be measured on it.
- **Groq's free tier fits little chat.** One multi-tool turn can use the whole 8k
  tokens-per-minute budget; the local fallback then finishes the turn.
- **Live evals are not in CI.** The GitHub runner only runs unit tests, because live evals need
  a model. Nightly live evals need a self-hosted runner or cron on the stack host.
- **`docker compose up` has not been run**; see [architecture.md](architecture.md#deployment-status).
