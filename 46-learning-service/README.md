# learning-service

Deploy **46 of 91** of the marketing agent. It learns from the reviewer. Every
approval, edit and rejection is recorded as an event. The service turns those events into
two things the writers use:

- **Examples.** The most recent texts a human signed off, edited ones first (the
  reviewer's own words), for few-shot prompting.
- **Rules.** `/reflect` asks the LLM, via `03-llm-gateway` and its `reflect_rule` prompt,
  what general rule an edit or rejection implies. New rules start as `pending`. A human
  activates or rejects them (`51-wf-rules-review-form`). Active rules are served as a
  summary that the gateway appends to the brand text in every prompt.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a volume on `/data`. n8n calls it at `http://learning-service:8000`
(env `LEARNING_URL`). It needs the gateway at `GATEWAY_URL` for `/reflect` only. It keeps
state in SQLite, so run exactly one instance and back up the volume.

## Run

```bash
docker build -t learning-service .
docker run --rm -p 8146:8000 -e INTERNAL_API_KEY=change-me \
  -e GATEWAY_URL=http://llm-gateway:8000 -v learning-data:/data learning-service
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./learning.sqlite GATEWAY_URL=http://localhost:8103 \
  uvicorn app.main:app --port 8146
```

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/events` 🔑 | `{"item_id","channel","campaign_id"?,"decision","draft","final"?,"reason"?,"reviewer"?}` | event, `201` |
| GET | `/events` | `?decision=&since=ISO` | `[event]`, oldest first |
| GET | `/items/{item_id}/attempts` | — | `{"item_id","rejections"}` |
| GET | `/examples` | `?channel=&k=3&by=approval` (k 1–10; `by` = `approval` \| `performance`) | `[{"text","channel","decision"}]`; with `by=performance` also `"item_id","basis"` and, for performers, `"clicks","hook_style"`; headers `X-Examples-Basis`, `X-Examples-Note` |
| POST | `/reflect` 🔑 | `{"since_days":7,"max_events":20}` (body optional) | `{"created":[rule],"reflected":n,"errors":[{"event_id","error"}]}` |
| GET | `/rules` | `?status=pending\|active\|rejected\|provisional\|retired` | `[rule]` |
| GET | `/rules/review` | — | `[rule]`: pending rules and replicated provisional ones (form 51) |
| POST | `/rules/{id}/status` 🔑 | `{"status":"active"\|"rejected"}` | rule |
| POST | `/rules/from-experiment` 🔑 | `{"experiment_id","decision","variable","channels","values":[2],"winner"?,"loser"?,"lift_hdi"?,"decided_at"?,"summary"?}` | `{"action","rule"}` |
| GET | `/rules/summary` | — | `{"summary","count"}` |

An event is `{id, item_id, channel, campaign_id, decision, draft, final, reason, reviewer,
created_at, reflected_at}`. `decision` is `approved`, `edited` or `rejected`. Empty
`final`, `reason` and `reviewer` are stored as `null`.

A rule is `{id, text, scope, status, source_event_ids[], created_at, source, support,
contradicts, replicated, source_experiment_ids[], contradicting_experiment_ids[], evidence[],
last_confirmed_at}`. `scope` is `all` or a channel name. `source` is `review` (reflected from
edits) or `experiment`; the counters are 0 for review rules.

```bash
curl -s localhost:8146/events -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"item_id":12,"channel":"linkedin","decision":"edited","draft":"We are thrilled!!!","final":"We shipped it."}'

curl -s localhost:8146/rules/summary
# {"summary":"Rules learned from your edits:\n- No exclamation marks.\n- [linkedin] At most 3 hashtags.","count":2}
```

### How the parts behave

- **Examples.** The list contains `edited` finals, newest first, then `approved` texts,
  newest first. An `approved` text is its `final`, or its `draft` when no `final` was
  sent. `edited` events without a `final` and all `rejected` events are left out. The
  `channel` filter ignores case.
- **Examples by performance** (`by=performance`, used by writers 26, 48 and 65): the
  approved posts that earned clearly more clicks than a typical post of their channel
  (research: learning from performance data, `_dev/research/ai-marketing-wins.md` #2).
  46 reads `GET {CAMPAIGNS_URL}/insights/posts?days=PERF_MAX_AGE_DAYS&channel=` (45: every
  tracked post, its clicks from the short links, hook style and date) and keeps a post
  only if a reviewer approved or edited it here and it shows **evidence**
  (`app/performance.py`):
  - it is `PERF_SETTLE_DAYS`..`PERF_MAX_AGE_DAYS` old, its channel has at least
    `PERF_MIN_POSTS` such posts, and it has at least `PERF_MIN_CLICKS` clicks;
  - P(clicks this high | a typical post) ≤ `PERF_ALPHA` / n, with a typical post
    negative-binomial with the median and the MAD-based variance of the channel's other
    posts (Poisson when they are not overdispersed); n = posts in the channel.

  Passing posts are ranked by clicks × 0.5^(age / `PERF_HALF_LIFE_DAYS`), then at most one
  per hook style and none whose words overlap a chosen one by `PERF_DUP_JACCARD` or more
  (calendar items carry no atom id; near-identical text stands in for "same idea"). The
  remaining slots are filled with the approval examples above (`basis: "approval"`). If
  45 is down, slow or nothing passes, all k are approval examples; `X-Examples-Basis` is
  `performance`, `mixed` or `approval` and `X-Examples-Note` says why.
  Simulation (20 posts per channel, 400 runs, `python -m tests.test_performance`): with
  all posts equally good it names a "winner" in 4 % of runs (naive "most clicks": 100 %)
  and two independent looks agree 93 % of the time (naive 6 %); when 3 posts truly get
  3× the clicks it names them in 95 % of runs with precision 1.00, about as stable as
  the naive pick (0.55 vs 0.56). With overdispersed clicks (Gamma-Poisson, shape 2) it is
  weaker: 41 % false winners in the all-equal world, precision 0.81 vs 0.66 naive. No
  evaluation on real posts yet: there is no click data to replay.
- **Reflect.** It takes `edited` and `rejected` events from the last `since_days` that have
  not been reflected yet, oldest first, at most `max_events`. For each one it calls
  `POST {GATEWAY_URL}/v1/run` with
  `{"prompt":"reflect_rule","vars":{"draft","final","reason","channel"}}` (empty values
  are sent as `null`). The gateway output must be `{"generalizable","rule","scope"}`.
  - A successful call sets `reflected_at` on the event, so each event is reflected once.
  - If the gateway call fails or returns another shape, the event is not marked. It is
    listed in `errors` and retried on the next run.
  - A rule is kept only if `generalizable` is true and the text is not empty.
    Whitespace is collapsed and the text is cut to 200 characters. The scope is
    lower-cased, and a missing scope means `all`.
  - Rules are deduplicated by their text, ignoring case and whitespace. A rule that
    matches an existing `pending` rule adds its event to that rule's
    `source_event_ids`. A rule that matches an `active` or `rejected` rule is dropped, so
    a rejected rule is never proposed again.
- **Summary.** It lists `active` rules only, one `- rule` line each, with a channel-scoped
  rule shown as `- [linkedin] rule`. With no active rules the response is
  `{"summary":"","count":0}`.
- **Attempts.** `rejections` is the number of `rejected` events for that item.

- **Rules from experiments** (`POST /rules/from-experiment`, called by workflow 75 when 45
  decides an experiment). The key is the variable, the channels and the two values (in any
  order). Design: `_dev/research/experiment-loop.md` section 5.
  - `winner` and no rule yet: a **provisional** rule "Prefer X over Y (hooks) on x." with
    `support` 1. Provisional rules are not in `/rules/summary`, so writers never see them.
  - `winner` in the same direction from another experiment: `support` + 1; a provisional
    rule becomes `replicated` and appears in `/rules/review` for form 51. Only then can
    `POST /rules/{id}/status {"status":"active"}` activate it (`409` before).
  - `winner` the other way, or `no_practical_difference`: `contradicts` + 1. An active rule
    goes back to provisional (not replicated). With `contradicts ≥ support` the rule is
    `retired`; a contradicting winner then starts its own provisional rule.
  - `inconclusive`: nothing changes. An experiment is counted once (`already_counted`).

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for write endpoints. If it is not set, writes return `503`. A wrong or missing key returns `401`. |
| `DB_PATH` | `/data/learning.sqlite` | SQLite file |
| `GATEWAY_URL` | `http://llm-gateway:8000` | LLM gateway (deploy 03), used by `/reflect` |
| `GATEWAY_TIMEOUT` | `300` | seconds per gateway call |
| `CAMPAIGNS_URL` | `http://campaign-service:8000` | 45, read by `/examples?by=performance` |
| `CAMPAIGNS_TIMEOUT` | `8` | seconds for that call; on timeout the examples fall back to approval |
| `PERF_MIN_CLICKS`, `PERF_MIN_POSTS`, `PERF_ALPHA` | `5`, `5`, `0.1` | evidence rule |
| `PERF_SETTLE_DAYS`, `PERF_MAX_AGE_DAYS`, `PERF_HALF_LIFE_DAYS` | `2`, `90`, `45` | post age window and recency decay |
| `PERF_DUP_JACCARD` | `0.5` | word overlap that counts as the same idea |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
