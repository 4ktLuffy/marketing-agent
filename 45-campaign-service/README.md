# campaign-service

Deploy **45 of 60** of the local-LLM marketing agent. It holds each campaign: the goal,
audience, channels, dates and the KPI targets, set before anything ships. It builds UTM
links for the campaign, pulls actual results from the link shortener (16) and analytics (20),
and returns a scorecard that says which targets are met, on track or behind. It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a volume on `/data`. n8n calls it at `http://campaign-service:8000`
(env `CAMPAIGNS_URL`). It calls `link-shortener`, `analytics-ingest` and
`content-calendar` on the same network. It keeps state in SQLite, so run exactly one
instance and back up the volume.

## Run

```bash
docker build -t campaign-service .
docker run --rm -p 8145:8000 -e INTERNAL_API_KEY=change-me -v campaign-data:/data \
  --network marketing campaign-service
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./campaigns.sqlite uvicorn app.main:app --port 8145
```

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/campaigns` 🔑 | `{"name","slug"?,"goal_type","goal_text","audience","offer"?,"landing_url"?,"channels":[],"start_date","end_date","budget"?,"notes"?,"kpis"?:[{"metric","target_value","baseline_value"?,"source"?}]}` | campaign, `201` |
| GET | `/campaigns` | `?status=` (one or a comma list) | `[campaign]` |
| GET | `/campaigns/{id}` | — | campaign |
| GET | `/campaigns/by-slug/{slug}` | — | campaign |
| PATCH | `/campaigns/{id}` 🔑 | any field except `status` and `kpis` | campaign |
| POST | `/campaigns/{id}/status` 🔑 | `{"status"}` | campaign |
| POST | `/campaigns/{id}/kpis` 🔑 | `{"metric","target_value","baseline_value"?,"source"?}` | KPI, `201` |
| PATCH | `/campaigns/{id}/kpis/{kid}` 🔑 | any of `target_value, baseline_value, actual_value` | KPI |
| DELETE | `/campaigns/{id}/kpis/{kid}` 🔑 | — | `{"deleted": kid}` |
| POST | `/campaigns/{id}/link` | `{"channel","content"?,"url"?}` | `{"url","params"}` |
| POST | `/campaigns/{id}/measure` 🔑 | — | scorecard |
| GET | `/campaigns/{id}/scorecard` | — | scorecard |
| GET | `/report/unmeasured` | — | `[campaign + "unmeasured": [metric]]` |
| GET | `/insights` | `?days=90` | `{"by_channel","top_posts","errors"}` |
| GET | `/insights/hooks` | `?days=90&explore=0.2&seed=` | `{"recommended","explored","styles","unlabeled_posts","method","errors"}` |

A campaign is:

```json
{"id":1,"slug":"autumn-launch","name":"Autumn Launch","goal_type":"traffic",
 "goal_text":"Visits to pricing","audience":"Agency owners","offer":null,
 "landing_url":"https://example.com/pricing","channels":["linkedin","email"],
 "start_date":"2026-09-15","end_date":"2026-10-14","status":"planned","budget":null,
 "notes":null,"created_at":"2026-09-23T09:51:44Z","updated_at":"2026-09-23T09:51:44Z",
 "kpis":[{"id":1,"campaign_id":1,"metric":"clicks","target_value":500.0,"baseline_value":null,
          "source":"shortener","actual_value":null,"measured_at":null}]}
```

```bash
curl -s localhost:8145/campaigns -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"name":"Autumn Launch","goal_type":"traffic","goal_text":"Visits to pricing",
       "audience":"Agency owners","landing_url":"https://example.com/pricing",
       "channels":["LinkedIn","email"],"start_date":"2026-09-15","end_date":"2026-10-14"}'
# 201 {"id":1,"slug":"autumn-launch",...,"status":"planned","kpis":[]}

curl -s localhost:8145/campaigns/1/status -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"status":"active"}'
# 409 {"detail":{"message":"add at least one KPI (a target) before activating the campaign",
#      "current":"planned","allowed":["active","cancelled"]}}

curl -s localhost:8145/campaigns/1/kpis -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"metric":"clicks","target_value":500}'
# 201 {"id":1,"campaign_id":1,"metric":"clicks","target_value":500.0,...,"source":"shortener",...}

curl -s localhost:8145/campaigns/1/link -H 'content-type: application/json' \
  -d '{"channel":"linkedin","content":"12"}'
# {"url":"https://example.com/pricing?utm_source=linkedin&utm_medium=social&utm_campaign=autumn-launch&utm_content=12",
#  "params":{"utm_source":"linkedin","utm_medium":"social","utm_campaign":"autumn-launch","utm_content":"12"}}

curl -s -X POST localhost:8145/campaigns/1/measure -H 'X-API-Key: change-me'
# {"campaign":{...},"elapsed_pct":26.7,
#  "kpis":[{"id":1,"metric":"clicks","source":"shortener","target_value":500.0,"baseline_value":null,
#           "actual_value":210.0,"measured_at":"...","progress_pct":42.0,"state":"on_track"}],
#  "assets":{"published":3,"draft":1},"errors":[]}
```

### Status workflow

| From | Allowed next |
|---|---|
| `planned` | `active`, `cancelled` |
| `active` | `paused`, `completed` |
| `paused` | `active`, `completed`, `cancelled` |
| `completed` | — (final) |
| `cancelled` | — (final) |

- New campaigns start as `planned`.
- Moving to `active` needs at least one KPI, so targets are declared before anything
  ships. Without one it is a `409` error.
- An invalid move is a `409` error whose `detail` lists `current` and `allowed`. An
  unknown status is a `422` error.
- `PATCH` cannot change `status` or `kpis` (422). The `slug` can change only while the
  campaign is `planned`; after that a different slug is a `409` error, because links
  already carry it as `utm_campaign`.

### Fields and rules

- `slug` defaults to the slugified `name`: lowercase, spaces and underscores become `-`,
  only `a-z 0-9 -` kept, at most 40 characters. A slug you send is slugified the same
  way. A slug that is already used is a `409` error.
- `goal_type`: `awareness`, `traffic`, `leads`, `sales`, `retention`.
- `start_date`/`end_date` are `YYYY-MM-DD`; `end_date` before `start_date` is a `422` error.
- `channels` are stored lowercase, without duplicates (`"LinkedIn"` becomes `"linkedin"`).
- KPI `metric`: `clicks`, `sessions`, `conversions`, `signups`, `revenue`, `ctr`, `cvr`,
  `open_rate`. One KPI per metric per campaign (a second one is a `409` error).
- KPI `source` defaults by metric: `clicks` → `shortener`; `sessions`, `conversions`,
  `ctr`, `cvr` → `analytics`; everything else → `manual`. `analytics` may also measure
  `clicks`. A source that cannot deliver the metric is a `422` error.
- `ctr` and `cvr` are fractions (`0.02` = 2 %), like the analytics service returns them.
  Set their targets as fractions too.
- Setting `actual_value` by `PATCH` sets `measured_at`; this is how `manual` KPIs are filled.
- An `active` campaign must keep at least one KPI: deleting its last one is a `409` error.

### Links

`POST /campaigns/{id}/link` builds a link with `utm_campaign` = slug, `utm_source` =
channel and `utm_medium` by channel: `email` → `email`, `google_ads` → `cpc`, `blog` →
`referral`, anything else → `social`. `content` becomes `utm_content`; send the calendar
item id there so `/insights` can match clicks to posts. The target is `url`, or the
campaign's `landing_url` when `url` is not sent; with neither it is a `422` error.
Existing non-UTM query parameters are kept; old UTM parameters are replaced.

### Measuring and the scorecard

`POST /campaigns/{id}/measure` fills `actual_value` for automatic KPIs:

- `shortener` KPIs: the sum of `clicks` from
  `GET {SHORTENER_URL}/links?utm_campaign=<slug>&limit=500`.
- `analytics` KPIs: the matching field of `totals` from
  `GET {ANALYTICS_URL}/kpis?campaign=<slug>&from=<start_date>&to=<min(end_date, today)>`.
  Before the start date nothing is asked.
- `manual` KPIs are left alone.

If a source is unreachable or returns an error, its KPIs keep their previous value and
the failure is listed in `errors` as `{"source","metrics","error"}`. Measuring never
fails because an upstream service is down.

In the scorecard, for each KPI:

- `progress_pct` = `actual / target × 100`, one decimal.
- `state`: `not_measured` when there is no actual yet; `met` when actual ≥ target;
  `on_track` when progress ≥ `elapsed_pct`; otherwise `behind`.
- `elapsed_pct` = days since `start_date` ÷ days in the campaign (both dates included)
  × 100, clamped to 0–100. Before the start it is 0, so every measured KPI is on track.

`assets` counts the campaign's calendar items by status, from
`GET {CALENDAR_URL}/items?campaign_id=<id>`. If the calendar is down, `assets` is `{}`
and the error is listed.

### Reports

- `/report/unmeasured` lists campaigns whose `end_date` has passed, or that are
  `completed`, and still have a KPI without an actual. Cancelled campaigns are left out.
  Each row is the campaign plus `unmeasured`, the metric names that lack an actual.
- `/insights?days=90` reads `GET {SHORTENER_URL}/links?limit=500`, keeps links created in
  the last `days` days whose target URL has a whole-number `utm_content` (a calendar item
  id), and looks each item up once with `GET {CALENDAR_URL}/items/{id}`.
  `by_channel` is `[{"channel","posts","clicks","avg_clicks"}]`, most clicks first;
  `top_posts` is the 10 posts with the most clicks, `[{"item_id","title","channel","clicks"}]`.
  If an item cannot be fetched, its `title` is `null`, its channel falls back to
  `utm_source`, and the failure is listed in `errors`.
- `/insights/hooks?days=90&explore=0.2&seed=` learns which hook style (how a post opens)
  earns clicks. It reuses the `/insights` join (same links, same calendar lookups) and
  groups posts by the calendar item's `hook_style`, one of `question`, `fact_led`,
  `story`, `how_to`, `benefit`, `contrarian` (the enum of `04` `social_posts`). Posts
  without a style, or with another one, are counted in `unlabeled_posts`.
  - Model: clicks of a post ~ Poisson(rate), rate ~ Gamma(1, 1) per style, so after
    `posts` posts with `clicks` clicks the posterior is Gamma(1 + clicks, 1 + posts).
    Clicks **per post** are compared, so a style is not rewarded just for being used more.
  - Thompson sampling: one draw per style, ranked; `recommended` is the top two. With
    probability `explore` (0–1) the second is replaced by a random style among those
    with the fewest posts (never the first), so new styles keep getting tried; `explored`
    names it, else `null`. `seed` (integer) makes the draw repeatable; without it every
    call draws afresh.
  - `styles` lists all six, in ranked order: `{"hook_style","posts","clicks",
    "clicks_per_post" (null with no posts),"posterior_mean","sample","recommended"}`.
    Styles with no data still appear (prior only), so they can be recommended.
  - Shortener down: all styles fall back to the prior and the error is in `errors`.
  - The social writer (workflow 26) calls it before writing and passes `recommended` as
    the prompt's `prefer_hooks`; it saves each post's `hook_style` on the calendar item.
    Tests include a simulation (32 posts at 2 clicks/post vs 8 at 6): the better style
    is first in ≥ 90% of seeds (96 of 100); ranking by raw click totals gets 2 of 100.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for write endpoints. If it is not set, writes return `503`. Also sent as `X-API-Key` to the upstream services. |
| `DB_PATH` | `/data/campaigns.sqlite` | SQLite file |
| `SHORTENER_URL` | `http://link-shortener:8000` | Link shortener (16) |
| `ANALYTICS_URL` | `http://analytics-ingest:8000` | Analytics ingest (20) |
| `CALENDAR_URL` | `http://content-calendar:8000` | Content calendar (19) |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
