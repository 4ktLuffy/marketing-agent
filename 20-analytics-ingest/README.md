# analytics-ingest

Deploy **20 of 60** of the local-LLM marketing agent. It stores daily metrics per channel
from CSV exports (your own sheet, or a GA4 export) and computes KPIs for any period,
compared with the period before it. The weekly report (deploy 41) reads `/kpis` and
passes the result to `21-report-builder`. It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a volume on `/data`. n8n calls it at `http://analytics-ingest:8000`
(env `ANALYTICS_URL`). It keeps state in SQLite, so run exactly one instance.

## Run

```bash
docker build -t analytics-ingest .
docker run --rm -p 8120:8000 -e INTERNAL_API_KEY=change-me -v analytics-data:/data analytics-ingest
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./analytics.sqlite uvicorn app.main:app --port 8120
```

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/upload?source=generic\|ga4&label=` 🔑 | raw CSV (`text/csv`) | `{"rows_imported"}` |
| GET | `/kpis` | `?from=YYYY-MM-DD&to=YYYY-MM-DD&compare=true&source=&campaign=` | KPIs, see below |

```bash
curl -s 'localhost:8120/upload?source=generic' -H 'X-API-Key: change-me' \
  -H 'content-type: text/csv' --data-binary @examples/generic.csv
# {"rows_imported":56}

curl -s 'localhost:8120/kpis?from=2026-09-14&to=2026-09-20&source=generic'
```

```json
{"period":{"from":"2026-09-14","to":"2026-09-20"},
 "totals":{"impressions":189599,"clicks":4952,"sessions":7804,"conversions":321,"spend":3297.44,
           "ctr":0.0261,"cvr":0.0411,"cpa":10.27},
 "by_channel":[{"channel":"organic","impressions":0,"clicks":0,"sessions":3315,"conversions":99,
                "spend":0,"ctr":null,"cvr":0.0299,"cpa":0.0}, ...],
 "previous":{"period":{"from":"2026-09-07","to":"2026-09-13"},"impressions":158400,"clicks":4142,
             "sessions":6530,"conversions":248,"spend":3146.2,"ctr":0.0261,"cvr":0.038,"cpa":12.69},
 "delta_pct":{"impressions":19.7,"clicks":19.6,"sessions":19.5,"conversions":29.4,"spend":4.8,
              "ctr":0.0,"cvr":8.2,"cpa":-19.1},
 "by_campaign":[{"campaign":"spring-launch","sessions":2676,"clicks":2933,"conversions":113,"spend":936.0},
                {"campaign":"brand-search","sessions":1813,"clicks":2019,"conversions":109,"spend":2361.44}]}
```

### CSV formats

**`source=generic`** (headers are case-insensitive, column order is free):
`date, channel, campaign, impressions, clicks, sessions, conversions, spend`.
`date` (`YYYY-MM-DD`) and `channel` are required. `campaign` is optional and holds the
`utm_campaign` value (for example the campaign slug from `45-campaign-service`); a missing
column or empty cell means `""` (no campaign). Missing numbers count as 0.
`1,234` and `$12.50` are accepted. See `examples/generic.csv`.

**`source=ga4`**: a GA4 report or exploration exported as CSV (see `examples/ga4.csv`).
Lines starting with `#` are skipped.

| GA4 column | Becomes |
|---|---|
| `Date` (`YYYYMMDD` or `YYYY-MM-DD`) | `date` |
| `Session default channel group` or `Default channel group` | `channel` |
| `Sessions` | `sessions` |
| `Key events` or `Conversions` | `conversions` |
| `Ads cost`, `Ads clicks`, `Impressions` (if present) | `spend`, `clicks`, `impressions` |
| `Session campaign` or `Session manual campaign name` (if present; the first wins) | `campaign` (missing → `""`) |
| `Engaged sessions` and anything else | ignored |

- The key is `(date, channel, campaign, source)`. Uploading the same day, channel and
  campaign again **replaces** the earlier values, so you can safely re-upload overlapping
  exports. Rows with the same date, channel and campaign in one file are summed.
- Campaign values are stored as written (trimmed, case-sensitive). GA4 placeholders such
  as `(direct)`, `(organic)` or `(not set)` are kept as they are.
- A day uploaded once **without** a campaign column and again **with** one is stored as
  separate rows (`campaign=""` and the named campaigns), so totals count it twice. Use
  one layout per source.
- If any row is bad, nothing is imported. You get a 422 error with
  `detail.rows = [{"row": <line number in the file>, "error": "..."}]`.
- A `Total` or `Grand total` row is skipped.

### Labels: keeping synced data apart from manual uploads

`POST /upload?source=generic&label=umami` parses the CSV with the preset chosen by
`source` (`generic` or `ga4`) but stores the rows with source **`umami`**. Because the
key is `(date, channel, campaign, source)`, labelled rows never replace rows you uploaded
by hand, and re-syncing a day replaces only the earlier labelled rows. `55-umami-sync`
uses `label=umami`.

- `label` must match `^[a-z0-9_-]{1,32}$` (lowercase letters, digits, `_`, `-`), else `422`.
- Without `label` nothing changes: rows are stored under `source` as before.
- `GET /kpis?source=umami` reports only the labelled rows. `source` on `/kpis` accepts any
  value matching the same pattern; one that was never uploaded gives zero totals.
- Totals without `source` add up every source, so the same visits uploaded by hand
  (`generic`/`ga4`) and synced (`umami`) are counted twice. Pass `source=` to pick one.

```bash
curl -s 'localhost:8120/upload?source=generic&label=umami' -H 'X-API-Key: change-me' \
  -H 'content-type: text/csv' --data-binary $'date,channel,campaign,sessions,conversions\n2026-09-01,linkedin,spring-launch,42,3\n'
curl -s 'localhost:8120/kpis?from=2026-09-01&to=2026-09-01&source=umami'
```

### KPI definitions

| Metric | Formula | `null` when |
|---|---|---|
| `ctr` | clicks / impressions | impressions = 0 |
| `cvr` | conversions / sessions, or conversions / clicks if the period has no sessions (ads-only data) | both are 0 |
| `cpa` | spend / conversions | conversions = 0 |

- `ctr` and `cvr` are fractions (0.0395 means 3.95%), rounded to 4 decimals. `cpa` and
  `spend` are rounded to 2 decimals.
- `by_channel` has the same fields plus `channel`, sorted by sessions, then clicks.
- With `compare=true` (the default), `previous` holds the totals for the period of the
  same length that ends the day before `from`. `delta_pct[m]` is
  `(current - previous) / previous * 100`, rounded to 1 decimal, or `null` when the
  previous value is 0 or null. With `compare=false`, both are `null`.
- If you leave out `from`/`to`, the period is the last 7 full days (ending yesterday,
  server date).
- `?campaign=slug` restricts `totals`, `by_channel` and `previous` to rows whose campaign
  is exactly `slug` (case-sensitive). Without it (or with an empty value) all campaigns
  are summed, as before, and the response also has `by_campaign`: one entry per non-empty
  campaign in the period, `{"campaign","sessions","clicks","conversions","spend"}`, sorted
  by sessions then clicks. `source` applies to it as well. With a campaign filter,
  `by_campaign` is left out.
- Totals add up all sources. If you upload the same traffic both as `generic` and as
  `ga4`, it is counted twice. To avoid that, pass `?source=ga4` (or `generic`).

### Upgrading an existing database

A database created before campaigns existed has the key `(date, channel, source)`.
SQLite cannot change a primary key in place, so on first use the service rebuilds the
`metrics` table once, in one transaction, and keeps every row with `campaign = ""`.
Totals do not change.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for `/upload`. If it is not set, uploads return `503`. |
| `DB_PATH` | `/data/analytics.sqlite` | SQLite file |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
