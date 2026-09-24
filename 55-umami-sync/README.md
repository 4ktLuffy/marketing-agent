# umami-sync

Deploy **55 of 60** of the local-LLM marketing agent. It reads your Umami website
analytics one day at a time: visits and conversions for each `utm_source` and `utm_campaign`
pair. It uploads them to `20-analytics-ingest` as a generic CSV, labelled `umami`.
Campaign scorecards (45) and the weekly report (41) can then use real site numbers
instead of hand-made CSV exports. It uses no LLM and keeps no state.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n (the `56-wf-sched-analytics-sync` cron) calls it at `http://umami-sync:8000`.
It calls Umami (self-hosted or Umami Cloud) and `analytics-ingest` on the same network.

## Run

```bash
docker build -t umami-sync .
docker run --rm -p 8155:8000 --network marketing --env-file .env umami-sync
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me UMAMI_URL=https://umami.example.com UMAMI_WEBSITE_ID=... \
  UMAMI_USERNAME=admin UMAMI_PASSWORD=... CONVERSION_EVENT=signup \
  ANALYTICS_URL=http://localhost:8120 uvicorn app.main:app --port 8155
```

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/sync` 🔑 | `{"from":"YYYY-MM-DD","to":"YYYY-MM-DD"}` | `{"rows","days","errors":[]}` |

```bash
curl -s localhost:8155/sync -H 'X-API-Key: change-me' -H 'content-type: application/json' \
  -d '{"from":"2026-09-14","to":"2026-09-20"}'
# {"rows":23,"days":7,"errors":[]}

curl -s 'localhost:8120/kpis?from=2026-09-14&to=2026-09-20&source=umami'   # read it back from 20
```

- The range is at most **31 days** (both dates included). A longer or reversed range, or a
  date that is not `YYYY-MM-DD`, returns `422`.
- `rows` is the number of rows 20 imported. `days` is the number of days read from Umami.
- A day that cannot be read (Umami error, bad response) is skipped and listed in `errors`
  as `{"day":"2026-09-15","error":"breakdown: Umami returned HTTP 500"}`. The other days are
  still uploaded. A login or upload failure is listed with `"day": null`. `/sync` returns
  `200` in all of these cases, so the caller reads `errors`. It returns `503` only when
  this service is not configured (the message names the missing variable, never a value).
- Warnings, such as a day that reached a result cap (see limits), are listed in `errors`
  too. That day is still uploaded.
- No credential (password, session token, API key, internal key) appears in a response
  or a log line. Error text is scrubbed before it is returned or logged.

### What is uploaded

One CSV per sync, `POST {ANALYTICS_URL}/upload?source=generic&label=umami` with
`X-API-Key: $INTERNAL_API_KEY`:

```csv
date,channel,campaign,sessions,conversions
2026-09-15,direct,,25,2
2026-09-15,linkedin,autumn-launch,42,3
2026-09-15,newsletter,,7,0
```

| Column | From Umami |
|---|---|
| `date` | the day, in `UMAMI_TIMEZONE` |
| `channel` | `utm_source`, trimmed and lowercased; empty or missing → `direct` |
| `campaign` | `utm_campaign`, trimmed, case kept; empty or missing → `""` |
| `sessions` | `visits` from the breakdown (distinct visits with a pageview carrying this pair) |
| `conversions` | number of `CONVERSION_EVENT` custom events whose page URL carried this pair |

`impressions`, `clicks` and `spend` are left out; 20's generic preset stores them as 0.
Only `date` and `channel` are required by 20. When `CONVERSION_EVENT` is not set the
`conversions` column is left out (stored as 0) and no event calls are made.

Because 20 stores the rows under source `umami`, a sync never replaces rows you uploaded
by hand, and syncing a day again replaces the earlier `umami` rows for the same
date, channel and campaign. Use `?source=umami` on `/kpis` to see only these numbers. The
totals without `source` add up every source.

## Umami API used

Checked against the Umami source at tag **v3.4.0** (latest release, 2026-09-17) and the
docs. Umami v2 is not supported: its routes and parameter names differ.

| What | Call | Source |
|---|---|---|
| Self-hosted login | `POST /api/auth/login` `{"username","password"}` → `{"token","user"}`; then `Authorization: Bearer <token>`. If the account has 2FA, the reply is `{"requiresTwoFactor":true,...}`: use an API key instead | [route.ts](https://github.com/umami-software/umami/blob/v3.4.0/src/app/api/auth/login/route.ts), [docs](https://docs.umami.is/docs/api/authentication) |
| API key (Cloud, or self-hosted ≥ 3.4) | `Authorization: Bearer <key>`. Cloud also accepts `x-umami-api-key: <key>` (both may be sent if they hold the same key). This service sends both. Cloud base URL is `https://api.umami.is/v1` (routes without `/api`, e.g. `/v1/websites`); regions are `/v1/us` and `/v1/eu`. Limit: 50 calls per 15 s per key | [cloud API key docs](https://docs.umami.is/docs/cloud/api-key), [MCP docs (x-umami-api-key)](https://docs.umami.is/docs/cloud/mcp), [lib/auth.ts](https://github.com/umami-software/umami/blob/v3.4.0/src/lib/auth.ts), [lib/api-key.ts](https://github.com/umami-software/umami/blob/v3.4.0/src/lib/api-key.ts) |
| Visits per source × campaign | `GET /api/websites/{id}/breakdown?startAt=<ms>&endAt=<ms>&fields=["utmSource","utmCampaign"]` → `[{"views","visitors","visits","bounces","totaltime","utmSource","utmCampaign"}]`. Counts pageviews only, `limit 500` rows | [route.ts](https://github.com/umami-software/umami/blob/v3.4.0/src/app/api/websites/%5BwebsiteId%5D/breakdown/route.ts), [getBreakdown.ts](https://github.com/umami-software/umami/blob/v3.4.0/src/queries/sql/breakdown/getBreakdown.ts), [analytics-schema.ts](https://github.com/umami-software/umami/blob/v3.4.0/src/lib/analytics-schema.ts) |
| Same, Umami 3.0–3.3 | `POST /api/reports/breakdown` `{"websiteId","type":"breakdown","filters":{},"parameters":{"startDate","endDate","fields"}}`. Still served by 3.4 through a rewrite to `/compat/api/reports/*`. Used automatically when the GET above returns 404 | [3.0 route](https://github.com/umami-software/umami/blob/v3.0.0/src/app/api/reports/breakdown/route.ts), [3.4 next.config.ts rewrite](https://github.com/umami-software/umami/blob/v3.4.0/next.config.ts) |
| Conversion events | `GET /api/websites/{id}/events?startAt=<ms>&endAt=<ms>&event=<name>&pageSize=500&page=N` → `{"data":[{"createdAt","urlPath","urlQuery","eventType","eventName",...}],"count","page","pageSize"}`. `event` filters on `event_name` | [route.ts](https://github.com/umami-software/umami/blob/v3.4.0/src/app/api/websites/%5BwebsiteId%5D/events/route.ts), [getWebsiteEvents.ts](https://github.com/umami-software/umami/blob/v3.4.0/src/queries/sql/events/getWebsiteEvents.ts) |
| Filter and parameter names | camelCase: `utmSource`, `utmCampaign`, `event`, … (`filterParams`); `startAt`/`endAt` in **milliseconds**; optional `timezone` (IANA name) and `unit` only affect time bucketing | [lib/schema.ts](https://github.com/umami-software/umami/blob/v3.4.0/src/lib/schema.ts), [lib/request.ts](https://github.com/umami-software/umami/blob/v3.4.0/src/lib/request.ts), [lib/constants.ts](https://github.com/umami-software/umami/blob/v3.4.0/src/lib/constants.ts) |
| Where UTM values come from | `/api/send` reads `utm_*` from the URL of **each event** (pageview or custom event); the full query is also stored as `url_query` | [api/send/route.ts](https://github.com/umami-software/umami/blob/v3.4.0/src/app/api/send/route.ts) |

Why these two calls: Umami's `/metrics?type=utmCampaign` ranks one dimension only and
drops custom events (`event_type NOT IN (2, 5)`). The attribution report lists sources and
campaigns separately (top 20 each), not as pairs. The breakdown groups by both fields at
once. The events list gives each conversion's `urlQuery`, from which this service reads
`utm_source`/`utm_campaign` the same way Umami does.

### Calls, pauses and limits

- Per sync: 1 login (none with an API key), then per day 1 breakdown call and
  1 events call, plus one more events call for each further 500 conversions (at most 10
  pages, so 5,000 conversions a day). The worst case for 31 days is 1 + 31 × 11 = 342 calls;
  a normal 7-day sync makes 15.
- A pause of `UMAMI_PAUSE_SECONDS` (default 0.35 s) comes before every call after the
  first. That keeps a sync under Umami Cloud's 50 calls per 15 s. On `429` the call is
  retried once after `Retry-After` (at most 15 s).
- The day window is 00:00:00.000–23:59:59.999 in `UMAMI_TIMEZONE`, sent as absolute
  `startAt`/`endAt` milliseconds. Umami filters with `BETWEEN`, so both ends are included.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required. Protects `/sync` (unset → `503`) and is sent as `X-API-Key` to 20. |
| `UMAMI_URL` | `https://api.umami.is/v1` when `UMAMI_API_KEY` is set, else required | Self-hosted: the Umami base URL (`/api` is added). Cloud: `https://api.umami.is/v1` (or `/v1/eu`, `/v1/us`); on host `api.umami.is` no `/api` is added. |
| `UMAMI_WEBSITE_ID` | — | Required. The website UUID (Umami → Settings → Websites). |
| `UMAMI_USERNAME`, `UMAMI_PASSWORD` | — | Self-hosted login. Used only when `UMAMI_API_KEY` is empty. |
| `UMAMI_API_KEY` | — | Umami Cloud key, or a self-hosted key (Umami ≥ 3.4, starts with `umami_`). No login call is made. |
| `CONVERSION_EVENT` | — | Custom event name counted as a conversion (e.g. `signup`, as in `umami.track('signup')`). Empty → no conversions column. |
| `UMAMI_TIMEZONE` | `UTC` | IANA zone that defines "a day", e.g. `Europe/Berlin`. |
| `UMAMI_PAUSE_SECONDS` | `0.35` | Pause before each Umami call after the first. |
| `ANALYTICS_URL` | `http://analytics-ingest:8000` | Analytics ingest (20). |

### Known limits

- **Attribution is per page URL.** Umami stores UTM values from each event's own URL, not
  per session. A visit that lands with `?utm_campaign=x` and then opens pages without it is
  counted under `x` and also under `direct`/`""`. A conversion counts under a campaign
  only if the page where the event fired still had the UTM parameters in its URL (for
  example a signup form on the landing page). Conversions on later pages count as `direct`.
- The breakdown returns at most 500 source × campaign pairs a day. When a day reaches
  that, a warning is listed in `errors`.
- Syncing a day again replaces pairs that still exist but does not delete a pair
  that has disappeared from Umami (20 has no delete).
- `utm_source` values that differ only in case are merged (`LinkedIn` = `linkedin`).
- Umami's `equals` filter splits on commas, so a `CONVERSION_EVENT` containing a comma
  matches too much at Umami. This service re-checks `eventName` exactly, but avoid
  commas in the event name.
- A timezone other than UTC needs the IANA time zone database in the image. If the
  name is unknown, `/sync` returns `503`.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
