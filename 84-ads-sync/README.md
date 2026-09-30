# ads-sync

Deploy **84 of 89** of the local-LLM marketing agent. It is **paid-ads reporting**: spend,
conversions, CPL, ROAS, CTR and CPC per platform and per campaign, monthly budgets with
**pacing**, and **alerts** when a campaign overspends, underspends, gets too expensive per lead,
earns too little per euro, or spends for days without a single conversion.

- **Read-only.** It uses each platform's reporting API and nothing else. No code path can create,
  change, pause or re-budget an ad, an ad set, a campaign or a bid.
- **Numbers are computed here, in code.** The platforms supply spend, impressions, clicks,
  conversions and revenue per day. Every ratio (CTR, CPC, CPM, CPL, ROAS, CVR), every
  week-over-week change and every pacing figure is computed by this service. The weekly
  report's model (41) only quotes the finished sentences in `facts`, and 41 checks each number in code.
- **Currencies are never converted or mixed.** Totals are per currency. A budget only counts
  spend in its own currency.

It uses no LLM. The daily workflow (`n8n/workflow.json`) syncs and sends new alerts. The weekly
report (41) gets an **Ads** section, and the control room (72) shows an ads panel on its performance page.

| Platform | Status | API |
|---|---|---|
| Meta (Facebook, Instagram) | **Implemented** | Marketing API Insights, Graph `v26.0` (released 2026-07-29) |
| Google Ads | **Implemented** | Google Ads API REST `v25` (released 2026-07-22), `googleAds:searchStream` |
| LinkedIn Ads | **Not implemented** | See *LinkedIn* below |

## Where to deploy

Run it on the **Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network (compose service `ads-sync`, **growth** and **full** profiles, port `127.0.0.1:8184`), with
a volume on `/data`. n8n calls it at `http://ads-sync:8000` (env `ADS_URL`). It calls
`graph.facebook.com`, `oauth2.googleapis.com` and `googleads.googleapis.com` over the internet. On
the same network it calls `campaign-service` (45) and, if you turn it on, `analytics-ingest` (20). It
keeps state in SQLite, so run exactly one instance.

Every credential is empty by default. `/health` then says `"configured": false`, `/sync` returns
`503`, and the daily workflow does nothing.

## Meta access (you do this once)

You need admin access to the **Business Manager** (business.facebook.com) that owns the ad account.

1. **Create an app.** At [developers.facebook.com/apps](https://developers.facebook.com/apps), choose
   **Create app → Other → Business**, and link it to your business. Add the **Marketing API** product.
   Development access is enough to read your own ad accounts. Standard access (Advanced access to
   `ads_read`, requested in App Review) raises the rate limits.
2. **Create a system user.** In **Business Settings → Users → System users → Add**, create an
   *Employee* (not admin) system user, for example `ads-reader`.
3. **Give it the ad account.** Choose **Assign assets → Ad accounts →** your account, with the
   *View performance* permission only.
4. **Generate the token.** Choose **Generate new token**, pick the app, set expiry **Never**, and
   tick only **`ads_read`**. Copy the token once. Meta will not show it again.
5. In `01-marketing-stack/.env`, set `META_ACCESS_TOKEN=...` and
   `META_AD_ACCOUNT_IDS=act_123456789`. The id is in Ads Manager's account menu. Use commas for
   several accounts.
6. Run `docker compose up -d ads-sync`, then `curl -s localhost:8184/health`. It shows
   `platforms.meta.configured: true` and never the token.

**What counts as a conversion.** `META_CONVERSION_ACTIONS` (default `lead`) lists the Meta
`action_type`s that are summed as conversions. `META_REVENUE_ACTIONS` (default `omni_purchase`)
lists the `action_values` summed as revenue. Meta's action types overlap. For example, `lead`
already includes `offsite_conversion.fb_pixel_lead`, and `omni_purchase` includes `purchase`. List
only one of each pair, or the conversions are counted twice. For a shop, use
`META_CONVERSION_ACTIONS=omni_purchase`.

## Google Ads access (you do this once)

1. **Developer token.** Sign in to a Google Ads **manager account** (create one free at
   ads.google.com/home/tools/manager-accounts if you have none, and link your ad account to it).
   Open **Admin → API Center**, accept the terms, and copy the developer token. A new token only
   works with **test accounts**. Apply for **Basic access** in the same page to read real accounts;
   approval takes a few days. Until then `/sync` returns a `502` that names this step.
2. **OAuth client.** In the [Google Cloud console](https://console.cloud.google.com/), enable the
   **Google Ads API** for a project. Then go to **APIs & Services → OAuth consent screen**: user type
   *Internal* if you have Google Workspace, otherwise *External*, then **Publish app**. Under
   **Credentials → Create credentials → OAuth client ID → Web application**, add
   `https://developers.google.com/oauthplayground` as a redirect URI. Copy the client id and secret.
3. **Refresh token.** In the [OAuth 2.0 Playground](https://developers.google.com/oauthplayground),
   click the gear icon, tick **Use your own OAuth credentials**, and paste the client id and secret. For
   step 1, enter the scope `https://www.googleapis.com/auth/adwords` and authorize with a Google user
   that can see the ad account. In step 2, **Exchange authorization code for tokens** and copy the
   **refresh token**. If the consent screen stays in *Testing*, the refresh token **expires after 7
   days**, and `/sync` then says `invalid_grant`.
4. In `.env`, set `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`,
   `GOOGLE_ADS_REFRESH_TOKEN` and `GOOGLE_ADS_CUSTOMER_IDS` (the ad account ids; dashes are fine).
   Set `GOOGLE_ADS_LOGIN_CUSTOMER_ID` to the **manager** account id when the user reaches the ad account
   through the manager (the usual case after step 1).
5. Run `docker compose up -d ads-sync` and check `curl -s localhost:8184/health`.

## LinkedIn

**Not implemented.** The reporting endpoint is documented and clear:
`GET https://api.linkedin.com/rest/adAnalytics?q=analytics&pivot=CAMPAIGN&timeGranularity=DAILY`,
with `LinkedIn-Version: YYYYMM` and `X-Restli-Protocol-Version: 2.0.0`, scope `r_ads_reporting`,
and `costInLocalCurrency` / `externalWebsiteConversions` among the fields. What stops it for now:

- It needs LinkedIn to approve your app for the **Advertising API** product.
- It needs a 3-legged OAuth flow with a redirect back to your server, and tokens that last 60 days.
- Campaign names need a second API (`adCampaigns`) to resolve the URNs.
- Marketing versions are retired about a year after release.

That is a larger setup than Meta or Google for a small team, and it can't be tested here without
approved access. The normalized row format and every derived metric are platform-neutral, so a
LinkedIn connector would only need to produce the same daily rows.

## Run

```bash
docker build -t ads-sync .
docker run --rm -p 8184:8000 --network marketing-agent_marketing --env-file .env -v ads-sync-data:/data ads-sync
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./ads.sqlite uvicorn app.main:app --port 8184
```

## Endpoints

🔑 marks endpoints that need the header `X-API-Key: $INTERNAL_API_KEY`. The read endpoints need no
key, the same as `/kpis` on 20. They return only aggregated ad numbers.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | per platform `configured` and what is `missing` (names, never values), API versions, last sync |
| POST | `/sync` 🔑 | `{"days":7,"end_date":"YYYY-MM-DD","platforms":["meta","google"],"meta_levels":["campaign","adset","ad"]}` (all optional) | window, per-platform rows / calls / rate usage, warnings |
| GET | `/summary` | `?days=7&end=YYYY-MM-DD&top=10` | per platform + per-currency total + top campaigns, current vs previous, `delta_pct`, `alerts`, `facts` |
| GET | `/campaigns` | `?days=30&end=` | platform campaigns with the slug they map to and how (`mapping`, `utm_campaign`, `name`, `unmapped`) |
| POST | `/mappings` 🔑 | `{"platform":"meta","campaign_id":"2385...","slug":"autumn-launch"}` | the mapping (replaces one for the same campaign) |
| GET / DELETE | `/mappings`, `/mappings/{platform}/{campaign_id}` 🔑 | — | list / delete |
| POST | `/budgets` 🔑 | `{"campaign":"autumn-launch","month":"2026-09","amount":3000,"currency":"EUR","weighting":"linear"\|"weekday","weekday_weights":[1,1,1,1,1,0.5,0.5],"cpl_target":20,"roas_target":3}` | the budget (one per campaign and month; posting again replaces it) |
| GET / DELETE | `/budgets?month=`, `/budgets/{id}` 🔑 | — | list / delete |
| GET | `/pacing` | `?as_of=YYYY-MM-DD` | every budget of that month: spend to date, planned to date, pace, projection |
| GET | `/alerts` | `?as_of=` | current alerts (records nothing) |
| POST | `/alerts/check` 🔑 | `?as_of=` | `alerts` + `new`: the ones not sent for the same rule and campaign in the last `ALERT_REPEAT_DAYS`; `new` ones are marked sent |

`end`, `end_date` and `as_of` default to **yesterday (UTC)**. Today's numbers are still moving.

### `POST /sync`

- It pulls **daily** rows for the `days` days ending `end_date` (1–90, default 7) and **replaces**
  that platform's rows for those days. Platforms restate recent days as conversions are
  attributed, so the daily workflow re-syncs 7 days every morning. Sync the month once
  (`{"days": 31}`) so pacing has the month to date.
- **Per platform, all or nothing.** If Meta fails and Google works, Google's rows are stored,
  Meta's old rows stay, and the answer is `200` with `platforms.meta.ok: false` and the reason. If
  every platform fails, the answer has that error's status (see *Errors*).
- **Meta:** `GET https://graph.facebook.com/v26.0/act_<id>/insights` with `level` (campaign, and
  optionally adset or ad), `time_increment=1`, `time_range={"since","until"}`, `limit=500`, and the fields
  `date_start, account_currency, campaign_id, campaign_name, spend, impressions, clicks, actions,
  action_values` (plus the adset/ad id and name). The token goes in the `Authorization: Bearer` header,
  never in a URL. Meta's `paging.next` links therefore carry no token, and they are followed only on
  `graph.facebook.com` (at most 50 pages). The rate-limit headers `x-business-use-case-usage`,
  `x-ad-account-usage` and `x-app-usage` are read. The highest usage is returned as
  `rate_usage_pct`, and a warning is added at 90 % or more.
- **Google:** it first calls `POST https://oauth2.googleapis.com/token` (`grant_type=refresh_token`).
  The access token is cached until 60 s before it expires. Then
  `POST https://googleads.googleapis.com/v25/customers/<id>/googleAds:searchStream` with the headers
  `developer-token`, `login-customer-id` (optional) and `Authorization`, and this GAQL:

  ```sql
  SELECT customer.currency_code, campaign.id, campaign.name, campaign.final_url_suffix,
         campaign.tracking_url_template, segments.date, metrics.cost_micros, metrics.impressions,
         metrics.clicks, metrics.conversions, metrics.conversions_value
  FROM campaign WHERE segments.date BETWEEN '<since>' AND '<until>'
  ```

  `searchStream` returns every row as a JSON array of batches, with no page tokens. Cost is in
  micros (÷ 1,000,000), and int64 values arrive as strings. `segments.date` makes the rows daily.
  An explicit `BETWEEN` replaces `LAST_7_DAYS`, so the window is exactly the one requested.
- Levels: summary, pacing and alerts read **campaign** rows only. Adset and ad rows (Meta,
  `meta_levels`) are stored beside them for later drill-down and never counted twice.

### Mapping to our campaigns (45)

A platform campaign belongs to one of our campaigns (its `slug`, the `utm_campaign` of every link)
by the first rule that matches:

1. An explicit **mapping** (`POST /mappings`).
2. **`utm_campaign`** in the Google campaign's *Final URL suffix* or *tracking template* (for example
   `utm_source=google&utm_medium=cpc&utm_campaign=autumn-launch`). It is used when 45 knows that
   slug, or when 45 is unreachable.
3. **Our slug inside the campaign name**, after the same slugify as 45: `Autumn Launch | Prospecting` →
   `autumn-launch-prospecting` contains `autumn-launch`. If several slugs match, the longest wins. The
   known slugs are refreshed from 45 `GET /campaigns` on every sync.

Unmapped campaigns are keyed `meta:<id>` or `google:<id>`. You can set a budget on that key too.
`/summary` reports `unmapped_spend`. Meta's `url_tags` sit on the ad creative, not in Insights,
so Meta campaigns map by mapping or by name.

### `GET /summary`

`current` is the `days` days ending `end`. `previous` is the `days` days before. For each
platform + currency, each currency's total, and the top campaigns by spend, you get `current`,
`previous` and `delta_pct` (spend, impressions, clicks, conversions, revenue, CTR, CPC, CPL, ROAS; `null`
when the previous value is 0 or missing). Also returned: `alerts` as of `end`, and `facts`. These are
finished sentences such as

```
meta ads: spend 770.00 EUR in the last 7 days, 700.00 EUR the 7 days before (+10.0%)
meta ads: 25 conversions in the last 7 days, CPL 30.80 EUR (20.00 EUR the 7 days before, +54.0%)
campaign autumn-launch: spend 910.00 EUR in the last 7 days, 35 conversions, CPL 26.00 EUR, ROAS 2.69
alert overspend: autumn-launch: spent 3310.00 of a 3000.00 budget by day 27 of 30; plan was 2700.00 (23% over). ...
```

The weekly report puts `facts` into the `weekly_actions` prompt's `facts` list. An action whose
`why` cites a number that is not in them is dropped (41 *Check actions*).

### Pacing

For the month that contains `as_of`, with budget *B* and day *d* of *N*:

- **linear:** planned to date = *B* × *d* / *N*.
- **weekday:** planned to date = *B* × (sum of the weights of days 1..*d*) / (sum of the weights of
  every day in the month). The weights run Monday to Sunday, default `[1,1,1,1,1,0.5,0.5]`. Use
  `[1,1,1,1,1,0,0]` for weekdays only.
- `pace` = spend to date / planned to date (1.0 = on plan). `projected_month_spend` = spend to date ÷
  the planned fraction. Before the month, planned is 0 and `pace` is `null`. From the last day on, the
  whole budget is planned.
- Spend of every platform mapped to the campaign counts, in the budget's currency only. Spend in
  another currency is named in `warning`.

### Alert rules

| Rule | Fires when | Default |
|---|---|---|
| `overspend` | pace ≥ 1 + `ALERT_OVERSPEND_PCT`/100 (severity high at twice the threshold) | 15 % |
| `underspend` | pace ≤ 1 − `ALERT_UNDERSPEND_PCT`/100, from day `ALERT_MIN_DAYS` on | 25 %, day 3 |
| `cpl_above_target` | spend / conversions over the last `ALERT_WINDOW_DAYS` days > the budget's `cpl_target` (no CPL alert with 0 conversions: see the next rule) | 7 days |
| `roas_below_target` | revenue / spend over the window < `roas_target` (with spend > 0) | 7 days |
| `spend_no_conversions` | spend > 0 and 0 conversions on **each** of the last `ALERT_ZERO_CONV_DAYS` days, and at least `ALERT_ZERO_CONV_MIN_SPEND` in total. A day with no row breaks the streak, because nothing was reported. Runs for every campaign, with or without a budget. | 3 days, 0 |

`POST /alerts/check` returns every current alert plus `new`: alerts not sent for the same rule and
campaign within `ALERT_REPEAT_DAYS` (default 7; 0 = every time). The daily workflow notifies only `new`.

### Errors

| Status | When |
|---|---|
| `401` | missing or wrong `X-API-Key` on a keyed endpoint |
| `422` | bad body or query (days out of 1–90, future `end_date`, unknown platform or level, bad month / currency / weights) |
| `429` + `Retry-After` | rate limit. **Meta** codes 4, 17, 32, 613, 80000 (ads insights), 80004. Retry-After comes from `estimated_time_to_regain_access` (minutes) in `x-business-use-case-usage`, else the `retry-after` header, else 300 s. **Google** `RESOURCE_EXHAUSTED`: from `retryDelay`, else `retry-after`. Waits of 5 s or less are retried here first. |
| `502` | the platform refused. Each message names the fix. **Meta** code 190: `META_ACCESS_TOKEN` expired, revoked or invalid; make a new system-user token. Codes 10/200–299: missing `ads_read` or the ad account is not assigned. **Google** `invalid_grant`: the refresh token expired or was revoked (consent screen in *Testing* → 7 days). `DEVELOPER_TOKEN_NOT_APPROVED`: apply for Basic access. `USER_PERMISSION_DENIED`: set `GOOGLE_ADS_LOGIN_CUSTOMER_ID`. A `401` from Google Ads is retried once with a fresh access token. A platform that can't be reached is also a 502. |
| `503` | `INTERNAL_API_KEY` is unset, or no platform is configured (the message names the empty variables) |

No credential appears in a response or a log line. Messages are scrubbed of every configured
secret and of any `access_token=` or `Bearer` value, and `httpx` request logging is off.

## Optional: push to 20-analytics-ingest

When `ANALYTICS_URL` is set (compose: `ADS_ANALYTICS_URL`), every sync also uploads the daily
campaign rows to `POST {ANALYTICS_URL}/upload?source=generic&label=ads`:
`date,channel,campaign,impressions,clicks,conversions,spend`. `channel` is `meta_ads` or
`google_ads`, and `campaign` is the mapped slug. Then 45's campaign measurement
(`/kpis?campaign=<slug>`) sees ad spend and CPA. It is **off by default**: 20's totals without
`source=` add every source, so ad impressions would change the site's blended CTR in the weekly
report. A failed push becomes a warning.

## Workflow (shipped here, generated by `tools/workflow-generator`)

`n8n/workflow.json` (id `mktWf84AdsSync00`, imported with the growth profile) runs **daily at 07:30**:
`GET /health` (it stops quietly when `ADS_URL` is empty or no platform is configured) → `POST
/sync {"days":7}` → `POST /alerts/check` → one notification (shared `NOTIFY_FORMAT` helper) with the
**new** alerts and any sync problem. With nothing new, nothing is sent.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for keyed endpoints (unset → `503`). Also sent as `X-API-Key` to 20. |
| `DB_PATH` | `/data/ads-sync.sqlite` | SQLite file |
| `META_ACCESS_TOKEN` | empty | System-user token with `ads_read` |
| `META_AD_ACCOUNT_IDS` | empty | `act_123,act_456` (or bare digits) |
| `META_GRAPH_VERSION` | `v26.0` | Graph API version |
| `META_CONVERSION_ACTIONS` / `META_REVENUE_ACTIONS` | `lead` / `omni_purchase` | action types summed as conversions / revenue |
| `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN` | empty | see *Google Ads access* |
| `GOOGLE_ADS_CUSTOMER_IDS` | empty | ad accounts to report on |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | empty | manager account id, when access goes through one |
| `GOOGLE_ADS_API_VERSION` | `v25` | Google Ads API version |
| `CAMPAIGNS_URL` | empty | 45-campaign-service for the known slugs (compose sets it) |
| `ANALYTICS_URL` | empty (off) | optional push to 20 |
| `ALERT_OVERSPEND_PCT`, `ALERT_UNDERSPEND_PCT`, `ALERT_MIN_DAYS`, `ALERT_WINDOW_DAYS`, `ALERT_ZERO_CONV_DAYS`, `ALERT_ZERO_CONV_MIN_SPEND`, `ALERT_REPEAT_DAYS` | 15, 25, 3, 7, 3, 0, 7 | alert rules |

## Known limits

- **Attribution is the platform's.** Meta and Google each count conversions with their own
  window and model, and both claim a conversion both touched. Summing them gives an upper bound,
  not deduplicated conversions.
- Insights calls are synchronous. That suits one to a few accounts. Very large accounts at ad level may need
  Meta's asynchronous report jobs (`POST /insights` → poll), which are not implemented.
- Only the `campaign` resource is read on Google (no ad group or keyword level yet). Performance Max
  campaigns report at campaign level, so they are included.
- No currency conversion. A budget in EUR ignores USD spend and says so.
- Spend for `today` is excluded by default, because it is still accruing.

## CI

`.github/workflows/ci.yml` runs the tests (respx fixtures only; no real Meta or Google call), then pushes
`ghcr.io/<you>/<repo>:latest` on every push to `main`.
