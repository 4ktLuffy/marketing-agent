# gsc-sync

Deploy **67 of 91** of the marketing agent. It reads your Google Search Console
clicks, impressions, CTR and average position per page and per query for two consecutive
windows: the last N days and the N days before. It keeps them in SQLite and answers two
questions:

- **Which pages are losing clicks?** (`/pages/declining`) This is the input for a
  content-refresh workflow, the #1 capability in the night-5 research.
- **Which queries already rank 5–20 with many impressions?** (`/queries/opportunities`,
  "striking distance") This is the input for SEO briefs (`29-wf-tool-seo-brief`).

It uses no LLM. It can also push daily site totals to `20-analytics-ingest` (optional).

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a volume on `/data` and the service-account key mounted read-only at
`/secrets/gsc.json`. n8n calls it at `http://gsc-sync:8000` (env `GSC_URL`). It calls
`oauth2.googleapis.com` and `searchconsole.googleapis.com` over the internet, and
`analytics-ingest` on the same network when `ANALYTICS_URL` is set. It keeps state in
SQLite, so run exactly one instance.

## Set up Google access (service account)

1. In [Google Cloud console](https://console.cloud.google.com/), pick or create a project.
   Go to **APIs & Services → Library**, find **Google Search Console API**, and click **Enable**.
2. Go to **IAM & Admin → Service accounts → Create service account**. Give it any name,
   for example `gsc-reader`. It needs **no** Cloud roles.
3. Open the service account. Go to **Keys → Add key → Create new key → JSON**. A file is
   downloaded. Its `client_email` looks like
   `gsc-reader@<project>.iam.gserviceaccount.com`.
4. In [Search Console](https://search.google.com/search-console), open the property. Go to
   **Settings → Users and permissions → Add user**, paste that `client_email`, and choose
   **Restricted** (read-only is enough).
5. Put the key on the Docker host. Keep it out of every repository:

   ```bash
   cd 01-marketing-stack
   mkdir -p secrets && mv ~/Downloads/<project>-<id>.json secrets/gsc.json
   sudo chown 10001 secrets/gsc.json && chmod 400 secrets/gsc.json   # the container runs as uid 10001
   ```

   (`secrets/` is in the root `.gitignore` and in this repo's `.gitignore`.) If you can't use
   `sudo`, `chmod 644` works on a single-user host.
6. Set `GSC_SITE_URL` in `01-marketing-stack/.env` **exactly** as Search Console names the
   property. A domain property is `sc-domain:example.com`. A URL-prefix property is the full
   URL with its trailing slash, e.g. `https://example.com/`.
7. `docker compose up -d gsc-sync`, then `curl -s localhost:8167/health`. It should show
   `"configured": true` and the `service_account` email from step 3.

If step 1 or 4 is missing, `/sync` returns `502`. The message says which step to fix
(see Errors).

## Run

```bash
docker build -t gsc-sync .
docker run --rm -p 8167:8000 --network marketing-agent_marketing --env-file .env \
  -v "$PWD/secrets:/secrets:ro" -v gsc-data:/data gsc-sync
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me GSC_SITE_URL=sc-domain:example.com \
  GSC_CREDENTIALS_FILE=./secrets/gsc.json DB_PATH=./gsc.sqlite uvicorn app.main:app --port 8167
```

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`. The read endpoints need no key, the same
as `/kpis` on 20. They only return aggregated Search Console numbers.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | status, `configured`, site, last sync (no secrets) |
| POST | `/sync` 🔑 | `{"days":28,"end_date":"YYYY-MM-DD"}` (both optional) | windows, row counts, warnings |
| GET | `/pages/declining` | `?min_clicks=20&drop=0.3&limit=20` | pages whose clicks fell |
| GET | `/queries/opportunities` | `?min_impressions=100&min_position=5&max_position=20&limit=50` | striking-distance queries + best page |
| GET | `/pages/{url-encoded page}/queries` | `?window=current\|previous&limit=20` | top queries for one page |

### `POST /sync`

```bash
curl -s localhost:8167/sync -H 'X-API-Key: change-me' -H 'content-type: application/json' \
  -d '{"days":28}'
```

```json
{"site":"sc-domain:example.com","synced_at":"2026-09-24T06:00:02+00:00",
 "windows":{"current":{"start":"2026-08-25","end":"2026-09-21"},
            "previous":{"start":"2026-07-28","end":"2026-08-24"}},
 "rows":{"current":{"page":412,"query":3120,"page_query":5870},
         "previous":{"page":398,"query":2987,"page_query":5611}},
 "calls":6,"analytics_rows":null,"warnings":[]}
```

- `days` is 1–90 (default 28). `end_date` defaults to **3 days ago** (UTC), because
  Search Console's `final` data usually lags 2–3 days. `end_date` in the future → `422`.
- Windows: `current` = the `days` days ending on `end_date` (both ends included);
  `previous` = the `days` days just before. They are equal in length and do not overlap.
- For each window it runs `searchanalytics.query` with dimensions `[page]`, `[query]` and
  `[page, query]`, `type=web`, `dataState=final`, `rowLimit=5000`. It pages with
  `startRow` up to 25,000 rows per call type. Above that, a warning is added and the smallest
  rows are missing. A normal sync makes 6 API calls plus 1 token call. The token is cached
  until 60 s before it expires.
- A sync **replaces** the site's previous sync, in one transaction. If any Google call
  fails, nothing is written and the old data stays readable.
- The read endpoints return `409` until the first sync has run.

### `GET /pages/declining` (content refresh)

A page is declining when it had at least `min_clicks` clicks in the previous window and
its clicks fell by at least `drop` (a fraction: `0.3` = 30 %). The fall is computed as
`(previous − current) / previous`.

- **New pages** (no clicks in the previous window) never appear. They can't be divided
  by zero and are not decaying.
- A page with **no clicks at all** in the current window has `drop: 1.0`.
- Pages are sorted by `clicks_lost`, then by `drop`.
- `top_queries` are the page's top 5 queries in the **previous** window (what it used to
  win), each with its current numbers beside. The refresh can then target the queries it lost.

```json
{"site":"sc-domain:example.com",
 "windows":{"current":{"start":"2026-08-25","end":"2026-09-21"},"previous":{...}},
 "synced_at":"...","min_clicks":20,"drop":0.3,
 "pages":[{"page":"https://example.com/blog/cold-brew","drop":0.7,"clicks_lost":70,
   "current":{"clicks":30,"impressions":1000,"ctr":0.03,"position":6.0},
   "previous":{"clicks":100,"impressions":1200,"ctr":0.0833,"position":4.0},
   "top_queries":[{"query":"cold brew ratio",
                   "previous":{"clicks":60,"impressions":1400,"ctr":0.0429,"position":4.0},
                   "current":{"clicks":15,"impressions":1500,"ctr":0.01,"position":9.0}}]}]}
```

A metric that has no row in a window is `{"clicks":0,"impressions":0,"ctr":null,"position":null}`.

### `GET /queries/opportunities` (SEO briefs)

Queries in the **current** window with `impressions ≥ min_impressions` and
`min_position ≤ position ≤ max_position` (average position; 1 = top). They are sorted by
impressions. `best_page` is the page that gets the most clicks for the query (ties: more
impressions, then better position). It is `null` when Search Console hides the page/query
pair (anonymized queries). `previous_position` shows the trend.

```json
{"site":"...","window":{"start":"2026-08-25","end":"2026-09-21"},"synced_at":"...",
 "min_impressions":100,"min_position":5,"max_position":20,
 "queries":[{"query":"coffee beans","clicks":20,"impressions":2000,"ctr":0.01,"position":8.4,
   "previous_position":6.0,
   "best_page":{"page":"https://example.com/beans","clicks":15,"impressions":1500,"ctr":0.01,"position":9.0}}]}
```

### `GET /pages/{page}/queries`

Pass the exact page URL **percent-encoded** (`encodeURIComponent` in n8n), including
the trailing slash if Search Console has one:

```bash
curl -s "localhost:8167/pages/$(python3 -c 'import urllib.parse;print(urllib.parse.quote("https://example.com/beans",safe=""))')/queries"
```

It returns the page's `current` and `previous` totals and its top queries in `window`,
each with the other window beside. An unknown page returns `404`.

### Errors

| Status | When |
|---|---|
| `401` | missing or wrong `X-API-Key` on `/sync` |
| `409` | read endpoint before the first sync |
| `422` | bad body or query (days out of 1–90, future `end_date`, `min_position > max_position`) |
| `502` | Google refused or failed. Messages: **not added as a user** (403/404): names the service-account email and the Search Console path, and reminds you of the `sc-domain:` / trailing-slash rule. **API not enabled** (403 "has not been used"): says to enable the Search Console API. **invalid_grant** from the token endpoint: key deleted or disabled, or server clock skew. A `401` from the API gets one retry with a fresh token first. |
| `503` | not configured: `INTERNAL_API_KEY`, `GSC_SITE_URL`, or the credentials file is missing, unreadable, or not a service-account key. The message names the variable and the path, never the file's content. |

No credential (private key, access token, internal key) appears in a response or a log
line. The service account's **email** is shown on `/health`, because that is what you add
in Search Console. It is not a secret. The key file's `token_uri` is ignored; the
signed assertion goes only to `https://oauth2.googleapis.com/token`.

### Optional: push to 20-analytics-ingest

When `ANALYTICS_URL` is set (e.g. `http://analytics-ingest:8000`), each sync also runs one
`[date]` query over both windows. It uploads daily site totals to
`POST {ANALYTICS_URL}/upload?source=generic&label=gsc`, the same way 55 uploads Umami data:

```csv
date,channel,campaign,impressions,clicks
2026-09-20,google-search,,400,12
```

Read it back with `GET /kpis?source=gsc`. The data goes in **per day, not per page**. 20's
key is `(date, channel, campaign, source)` and has no page column. Putting pages into
`campaign` would fill `by_campaign` in the weekly report with URLs. Push is **off by
default**: 20's totals without `source=` add every source, so organic impressions would
change the blended `ctr` in the weekly report. A push failure becomes a warning. The sync
itself still succeeds.

## Contract for workflows (not built here)

**Content refresh (weekly).** `POST /sync {"days":28}` → `GET /pages/declining?limit=3`.
For each page, send `page` to `07-page-extractor` (current text) and
`12-seo-auditor`. Send `top_queries[].query` (sorted by `previous.clicks −
current.clicks`) as the target keywords to the refresh draft. Then use `44-claim-checker` and
the approval form (38). Measure: 28 days later, sync again and compare that page's
`current.clicks` with the value stored at refresh time.

**SEO brief (29).** 29's inputs today are `keyword`, optional `competitor_url` and
`audience`. From `GET /queries/opportunities`, pass `query` as `keyword`. `best_page.page`
is **our** page that already ranks for it. 29 audits `competitor_url` with 12, and that
works for any URL, so passing `best_page.page` there gives a brief that improves the
existing page instead of starting a new post. A dedicated `own_url` input and a
`position`/`impressions` context line ("ranks 8.4 with 2,000 impressions in 28 days")
would need a change in 29. For a chosen page, `GET /pages/{page}/queries` lists the
related queries the brief should cover.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for `/sync` (unset → `503`). Also sent as `X-API-Key` to 20. |
| `GSC_SITE_URL` | — | Required. `sc-domain:example.com` or `https://example.com/`, exactly as in Search Console. |
| `GSC_CREDENTIALS_FILE` | `/secrets/gsc.json` | Service-account JSON key (read-only mount). |
| `DB_PATH` | `/data/gsc.sqlite` | SQLite file holding the last sync. |
| `ANALYTICS_URL` | empty (off) | 20-analytics-ingest base URL for the optional daily push. |

## Google API used

| What | Call |
|---|---|
| Token | `POST https://oauth2.googleapis.com/token`, form `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=<RS256 JWT>`; claims `iss`=client_email, `scope`=`https://www.googleapis.com/auth/webmasters.readonly`, `aud`=token URL, `iat`, `exp`=iat+3600; header `kid`=private_key_id ([Google: OAuth 2.0 for service accounts](https://developers.google.com/identity/protocols/oauth2/service-account#httprest)) |
| Data | `POST https://searchconsole.googleapis.com/webmasters/v3/sites/{siteUrl, percent-encoded}/searchAnalytics/query` with `startDate`, `endDate`, `dimensions`, `type`, `dataState`, `rowLimit` (max 25,000), `startRow` → `{"rows":[{"keys","clicks","impressions","ctr","position"}]}` ([searchanalytics.query](https://developers.google.com/webmaster-tools/v1/searchanalytics/query)) |

### Known limits

- Search Console hides rare (anonymized) queries in query-level data. So a page's
  `[page]` totals are larger than the sum of its `[page, query]` rows. Declining pages use the
  page totals. Top queries use the pairs.
- `position` is Google's impression-weighted average. For a query across pages it is
  the best position per impression, so it can be better than any single page's position.
- Only `type=web` search is synced (not image, video, news, Discover).
- One property per deploy (`GSC_SITE_URL`). Syncing replaces the previous sync for that site.
  No history is kept beyond the two windows.
- Quota: 1,200 queries per minute per site and per user. A sync uses 6–30 calls, far below it.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
