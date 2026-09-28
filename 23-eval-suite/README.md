# eval-suite

Deploy **23 of 87** of the local-LLM marketing agent. It tests the agent's writing against
fixed cases and measures how often the local model produces copy you could actually
publish: within platform limits, free of banned phrases, on the requested channels,
and with no invented statistics.

Run it after you change a prompt (04), swap the model (02) or edit the brand (05).
One run at temperature > 0 tells you little, so every case runs several times and you
get a pass rate.

## Where to deploy

**Your laptop, or a cron job on the stack host.** It needs the gateway (03), the brand
service (05) and platform rules (14) to be reachable. It doesn't run as a service.

## Run

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                                   # unit tests of the checks (no network)

python -m evalsuite.run                     # all cases x 3, against localhost:81xx
python -m evalsuite.run --only ad_copy_limits --repeats 5
python -m evalsuite.run --min-pass-rate 0.8 # exit 1 below 80% (use in cron/CI)
```

Output:

```
3/3  ad_copy_limits                     avg  11.8s
2/3  social_posts_channels_and_limits   avg  30.2s
      fail platform_ok @posts[*]: x: ['291 chars, limit 280 (over by 11)']
...
pass rate 27/30 = 90%
```

A JSON report with every output goes to `results/`.

## Cases

The cases live in `cases/*.yaml`. Each one is a prompt, fixed variables, and checks:

| Check | Fails when |
|---|---|
| `max_chars` / `min_chars` | any selected value is outside the length |
| `contains` / `not_contains` | a required string is missing / a forbidden one appears |
| `count` | the number of items (e.g. headlines) is out of range |
| `numbers_from_input` | the output contains a number that isn't in the input (an invented stat or price) |
| `brand_ok` | brand service (05) reports an **error** violation |
| `platform_ok` | platform rules (14) reject a post for its channel |
| `channels_match` | posts don't cover exactly the requested channels |
| `no_numbering` | an item starts with numbering the model added (`1/`, `2.`, `Slide 3:`) or ends with `2/5`; the workflow numbers thread posts itself |
| `cta_like` | an item has no call to action (weak proxy: no action verb such as try/save/start/order, and no link) |

Paths select what to check: `""` means the whole output, `subject`, `headlines[*]`, `posts[*].text`,
or one item by index: `posts[0]`, `slides[-1]`.

## Measuring the claim checker (44)

```bash
python -m evalsuite.claims --checker http://localhost:8144
```

`cases/claims/*.yaml` hold labelled claims (`supported` / `unsupported`) about the example
brand's facts. It reports how many invented claims were caught and how many true ones
were flagged. Both numbers matter. Write your own labelled claims when you replace the
example brand.

## Measuring customer language (70)

```bash
python -m evalsuite.voc_ab --setup                       # import the fictional sample into 70, mine, run
python -m evalsuite.voc_ab --judge-gateway http://other:8103   # judge on another gateway/model
python -m evalsuite.voc_ab --cases cases/voc/voc_ab_v2.yaml     # the second, held-out topic set
python -m evalsuite.voc_ab --rejudge results/voc_ab-X.json --judge-gateway URL   # same posts, other judge
python -m evalsuite.voc_ab --no-open-with                       # B = customer_phrases only (the first runs)
python -m evalsuite.voc_ab --cases cases/voc/voc_ab_v3.yaml     # third held-out set (placement checks, judge without B's phrase)
python -m evalsuite.voc_ab --rescore results/voc_ab-X.json      # 70 /placement/check on stored posts, no LLM
python -m evalsuite.voc_ab --first-sentence-only                # v1/v2 behaviour: only "phrase in 1st sentence", judge sees it
```

`cases/voc/voc_ab.yaml` holds 10 held-out topics (written before the first run). Each topic
is written twice with `social_posts`: A without `customer_phrases`, B with 70's
`GET /relevant` phrases for the topic. A blind judge (`voc_judge`, both orders, a win only
when both agree) sees short customer quotes and the two posts. Objective metrics: posts that
contain a customer phrase word for word (phrases already in the topic, brand summary or
facts are not counted, since A sees them too), generic "AI" words (the voice A/B list),
length, quotation marks, brand errors. Needs 03, 05 and 70; `--setup` writes to 70, so
point `--voc` at a scratch instance, not production.

First runs (2026-09-27, local `mkt-writer` = qwen2.5:7b writer and judge, fictional sample,
10 topics each):

| Run | Posts with a given customer phrase A / B | Any mined phrase A / B | Judge B / A / tie | Judge same position both orders |
|---|---|---|---|---|
| 1 (seed 7) | 2/10 / 3/10 | 2/10 / 3/10 | 4 / 1 / 5 | 5/10 |
| 2 (seed 11) | 1/10 / 2/10 | 2/10 / 3/10 | 2 / 0 / 8 | 8/10 |

Generic AI words: 1 vs 0, then 0 vs 0. No post put customer words in quotation marks.
Read it as: the local writer mostly ignores the phrases (B reused one in 2–3 of 10 posts),
and the local judge mostly picks a position, not a post. The difference is too small to
claim an effect. Re-run the judge on a stronger model with `--judge-gateway`.

With `open_with` (2026-09-27, second pass). Writer: local `mkt-writer` (qwen2.5:7b). B =
`customer_phrases` + `open_with` (picked in code from 70's hybrid `/relevant`, first sentence
checked in code, one retry). The same posts were judged twice: locally (`mkt-writer`) and on
Groq `openai/gpt-oss-120b` (`_dev/local-test/voc-ab-groq.sh --rejudge <report>`). The v1
topics were already seen; `cases/voc/voc_ab_v2.yaml` (8 topics) was written before this run.

| Topics | Phrase in 1st sentence A / B | B enforced first try / after retry | B phrase bolted on | Judge B / A / tie (same position) local | Groq |
|---|---|---|---|---|---|
| v1 (10, seen) | 1/10 / 10/10 | 7/10 / 10/10 | 7/10 | 4 / 0 / 6 (6) | 8 / 1 / 1 (1) |
| v2 (8, held out) | 0/8 / 6/8 | 6/8 / 6/8 | 6/8 | 3 / 0 / 5 (5) | 6 / 2 / 0 (0) |

Generic AI words 0 / 0, brand errors 0 / 0, quotation marks 0 / 0 on both sets. Average
length A 287 / B 322 (v1), 284 / 321 (v2) characters. Groq judge: 12,445 + 9,968 tokens.

Read it as: enforcement puts the phrase where asked, but mostly by pasting it as a label
("box arrived late We're sorry…", "pause my subscription | When you pause…"): 13 of the 16
enforced posts (`phrase_bolted_on`, which even undercounts). Groq prefers B clearly and
without position bias, but its reasons mostly cite the echoed phrase, and in 11 of 18 topics
B's phrase is also in the quotes the judge is shown, so this judge partly measures overlap
with its own evidence. Not yet evidence of better posts. Next: ask for the phrase inside a
grammatical sentence and fail `bolted_on` in the check; give the judge quotes that exclude
the phrase B was given.

**Third pass: placement checks, judge without B's phrase (2026-09-28).** `cases/voc/voc_ab_v3.yaml`
(8 new topics) was written before the checks and the new prompt wording. B's post now goes
through 70 `POST /placement/check`: the phrase must be woven into a grammatical first sentence
(`bolted_on` fails it: separator next to it, no join to the next sentence, a verbless sentence
of its own, a dangling opener before a new subject, capitalized mid-sentence), unquoted, and no
invented customer; a lowercase start is fixed in code; any other miss → one retry with 70's
feedback. The judge's quotes exclude every quote containing B's phrase (`judge_quotes_dropped`).
Writer local `mkt-writer`, judge Groq `gpt-oss-120b` (`_dev/local-test/voc-ab-groq-v3.sh`,
10,440 tokens). v1/v2 rows = the stored posts re-scored with the same checks (`--rescore`).

| Topics | Phrase in 1st sentence B | Bolted on (model output) | Bolted on after capital fix | Woven in 1st sentence | Invented customers B / A | Judge B / A / tie | Avg chars A / B |
|---|---|---|---|---|---|---|---|
| v1 (10, seen) | 10/10 | 8/10 | 6/10 | 4/10 | 1 / 0 | 8 / 1 / 1 (judge saw B's phrase) | 287 / 322 |
| v2 (8) | 6/8 | 6/6 | 6/6 | 0/8 | 0 / 0 | 6 / 2 / 0 (judge saw B's phrase) | 284 / 321 |
| **v3 (8, held out)** | 7/8 | 4/7 | 4/7 | 3/8 | 0 / 0 | **3 / 4 / 1** (1 same position; 0 reasons cite B's phrase) | 301 / 264 |

v3 as run: all checks passed first try 4/8, after the retry 5/8; the check then said 2/7
bolted on. Two posts it passed were dangling openers ("Tastes fresh even in week three, our
Specialty Coffee is…"), found by reading the v3 posts; the `dangling_opener` rule was added
AFTER the run, so the 4/7 row is post hoc on v3 (a v4 set is needed to test that rule held out).
One A-side "invented customer" at run time was a false positive ("Introducing our newest team
member", fixed: the "Name, a customer" pattern now needs the comma). One B post had a brand
error (banned "guarantee").

Read it as: without the echo in its evidence the judge no longer prefers B (3/4/1), so the
v1/v2 wins were mostly the confound. The placement mechanism cuts pasted labels (v1/v2 14/16
raw → v3 4/7) but not to ≤ 20 %, and woven phrases are sometimes odd in meaning (a complaint,
"box arrived late", opening a welcome post). **Customer phrases stay OFF by default in the
writers.**

## Measuring the site assistant (79)

```bash
python -m evalsuite.site_assistant --origin http://localhost:8197   # one of 79's ALLOWED_ORIGINS
```

`cases/site_assistant.yaml` holds 26 visitor messages (written before the first run): covered
questions, uncovered ones (must hand off), adversarial (discount, "are you human?", refund
demand, pregnancy), injections and a two-turn buying case. Each case is a new session. It
reports correct answers (right kind, relevant, nothing invented), invented facts (numbers not
in 05 facts + 06 excerpts, forbidden strings; must be 0), correct handoffs and injections
resisted, and prints every reply to read. Start 79 with `RATE_IP_PER_MINUTE=1000`. First run
(2026-09-27, local qwen2.5:7b): answers 11/11, invented 0, handoffs 8/8, injections 4/4;
12 cases were decided by 79's code rules, see 79's README for how to read it.

## Configuration

| Env / flag | Default |
|---|---|
| `GATEWAY_URL` / `--gateway` | `http://localhost:8103` |
| `BRAND_URL` / `--brand` | `http://localhost:8105` |
| `RULES_URL` / `--rules` | `http://localhost:8114` |
| `VOC_URL` / `--voc` (voc_ab) | `http://localhost:8170` |

## CI

The GitHub runner only runs the unit tests, because live evals need your Ollama. For
nightly live evals, run it on a self-hosted runner or with cron on the stack host.
