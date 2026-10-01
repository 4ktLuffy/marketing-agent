# ad-library-sync

Deploy **78 of 91** of the local-LLM marketing agent. It is the **competitor registry** and
the competitors' **ads**. It uses official APIs only.

- **Registry.** This is the one list of competitors. Each has a name, website, key pages,
  social handles, a Meta page id, markets, notes and a status (`active`, `suggested` or
  `ignored`). The key pages of an active competitor become watches in `09-change-monitor`.
  78 keeps them in sync: re-running changes nothing, and removing or ignoring a competitor
  removes its watches.
- **Ads.** It reads the **Meta Ad Library API** with your own token. Ads are deduped by
  Meta ad id, and 78 records `first_seen`, `last_seen`, text changes and stops.
- **Links.** Some ad libraries have no API, and Meta has none for non-EU commercial ads.
  For those, 78 stores **links** to the public library pages. A person opens them.
  **No scraping and no headless browser.**
- **Suggestions.** Some websites keep coming up in our own reviews (58), social-listening
  mentions (11) and trend digests (06). 78 stores them as `suggested`, with the quotes.
  Nothing is tracked until a person accepts one.

It uses no LLM except for the monthly positioning map (below). The competitor watch (37) reads its ads and links every 6 hours. The chat
tool `track_competitor` (81) adds competitors, and the weekly report (41) shows suggestions.

## What is legal and official (and what is not)

| Source | What 78 does | Why |
|---|---|---|
| Meta Ad Library API (`ads_archive`) | **Implemented** (`POST /sync`) | Official Graph API. Commercial ads come back **only if the ad was delivered in the EU**. Meta's reference says: "Ads that did not reach any location in the EU will only return if they are about social issues, elections or politics." |
| Meta, markets outside the EU | Link to the Ad Library page (`GET /links`) | The API has no commercial ads there. facebook.com's robots.txt forbids automated collection without written permission. |
| TikTok Commercial Content API | **Not implemented**, link only | The API exists (`POST https://open.tiktokapis.com/v2/research/adlib/ad/query/`, scope `research.adlib.basic`), but it needs a developer app **approved by TikTok**, covers the EU only, and returns ids, dates, reach and media URLs. It returns no ad copy, so the digest would have nothing to quote. Once you have approved access, it can be added the same way as Meta. |
| Google Ads Transparency Center | Link only (advertiser page or domain search) | No official API for commercial ads. Scraping is outside Google's terms. The public BigQuery dataset (EEA) needs a GCP project with billing, so it is not used. |
| LinkedIn Ad Library | Link only (company or advertiser-name search) | No API for other companies' ads. robots.txt disallows everything, and automated access is prohibited without permission. |
| Third-party "ad library APIs" (Apify, SearchAPI, ...) | Not used | They scrape. That moves the terms-of-service risk to the vendor and to you; it does not remove it. |

## Where to deploy

Deploy it on the **Docker host** that runs `01-marketing-stack`, as a container on the
`marketing` network, with a volume on `/data`. n8n calls it at `http://ad-library-sync:8000`
(env `AD_LIBRARY_URL`). It calls `graph.facebook.com` over the internet. On the same network
it calls `change-monitor` (09), `review-hub` (58), `social-listening` (11) and
`knowledge-base` (06). It keeps state in SQLite, so run exactly one instance.

## Meta access (you do this once, by hand)

Meta requires a real person's verification. Nobody can do it for you:

1. **Confirm your identity and location** at [facebook.com/ID](https://www.facebook.com/ID).
   This is the confirmation used for ads about social issues, elections or politics. It asks
   for a government-issued ID and your country, and takes a few days.
2. Create a **Meta for Developers** account at [developers.facebook.com](https://developers.facebook.com/)
   and accept the Platform Policy.
3. Open [facebook.com/ads/library/api](https://www.facebook.com/ads/library/api), choose
   **Access the API**, and create an app (**My Apps → Create App**).
4. In the [Graph API Explorer](https://developers.facebook.com/tools/explorer/), pick the app
   and generate a **user access token**. Tokens from the Explorer are short-lived. Exchange
   the token for a **long-lived** one (about 60 days) with the Access Token Debugger's
   **Extend Access Token**.
5. Put the token in `01-marketing-stack/.env` as `META_AD_LIBRARY_TOKEN=...`. Keep it out of
   every repository. Then run `docker compose up -d ad-library-sync`.
6. Check it: `curl -s localhost:8178/health` shows `"configured": true`.
7. Find each competitor's **page id**. Open their Facebook page, then **About → Page
   transparency**. Or open the Ad Library UI: the page id is `view_all_page_id=` in the URL.
   Without a page id, 78 searches by name (`KEYWORD_EXACT_PHRASE`) and keeps only ads whose
   page name contains the competitor's name.

When the token expires, `/sync` returns `502` with "expired or invalid". Repeat step 4.

## Run

```bash
docker build -t ad-library-sync .
docker run --rm -p 8178:8000 --network marketing-agent_marketing --env-file .env -v ads-data:/data ad-library-sync
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./ads.sqlite uvicorn app.main:app --port 8178
```

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`. `{ref}` is a competitor's id or its name
(case-insensitive, URL-encoded).

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `configured` (token set), `config_errors`, `graph_version`, `countries`, `non_eu_countries`, `monitor_sync`, counts per status, `last_sync`. No secrets. |
| POST | `/competitors` 🔑 | competitor (below); `?upsert=true` | `201` competitor (`200` when upsert updated one) |
| GET | `/competitors` | `?status=active\|suggested\|ignored` | `[competitor]` |
| GET | `/competitors/{ref}` | — | competitor, or `404` |
| PATCH | `/competitors/{ref}` 🔑 | any competitor fields | competitor (watches re-synced) |
| POST | `/competitors/{ref}/status` 🔑 | `{"status":"active\|suggested\|ignored"}` | competitor. Accepts or ignores a suggestion. |
| DELETE | `/competitors/{ref}` 🔑 | — | `{"deleted","watches_removed","watch_error"}`. Its ads are deleted too. |
| POST | `/watches/sync` 🔑 | — | re-applies every competitor's pages to 09 (e.g. after 09's database was reset) |
| POST | `/sync` 🔑 | `?competitor=` (optional) | Meta sync summary (below) |
| GET | `/ads` | `?competitor=&since=YYYY-MM-DD\|ISO&limit=100` | ads new, changed or stopped since `since` (default 7 days ago) |
| GET | `/links` | `?status=active` | `[{"competitor","links"}]` |
| GET | `/links/{ref}` | — | `{"competitor","links":[{"platform","market","api","url","note"}]}` |
| POST | `/suggestions/scan` 🔑 | `?min_mentions=` | `{"scanned","errors","min_mentions","new","updated","pending"}` |
| GET | `/suggestions` | — | competitors with status `suggested`, with `evidence` |
| POST | `/positioning/build` 🔑 | `?month=YYYY-MM` (default: this month) | this month's positioning map (below); replaces that month's snapshot. `502` when every LLM call failed (nothing stored) |
| GET | `/positioning` | — | `[{"id","month","built_at","brands","white_space","crowded","shifts"}]`, newest month first |
| GET | `/positioning/latest`, `/positioning/{id}` | — | a stored map, or `404` |
| GET | `/positioning/diff` | `?from=<id>&to=<id>` | `{"from","to","shifts"}` between two stored maps (ordered by month) |

### Competitor

```bash
curl -s localhost:8178/competitors -H "X-API-Key: $INTERNAL_API_KEY" -H 'content-type: application/json' -d '{
  "name": "Rival Beans", "website": "https://rival.example.com/",
  "key_pages": [{"url": "https://rival.example.com/pricing", "type": "pricing"},
                {"url": "https://rival.example.com/shop/decaf", "type": "product", "css": "main"}],
  "social": {"instagram": "@rivalbeans", "linkedin": "rival-beans"},
  "meta_page_id": "123456789", "google_advertiser_id": "AR01234567890123456789",
  "linkedin_company_id": "1234567", "domains": ["rivalbeans.shop"],
  "markets": ["DE", "FR"], "notes": "main rival in DACH", "status": "active"}'
```

- `name` is required and unique. `status` defaults to `active`.
- `key_pages[].type` is one of `home`, `pricing`, `product`, `features`, `about` or `other`.
  A key page can override 09's `css`, `xpath`, `include_filters`, `ignore_patterns` and
  `trigger_text` (see 09's README).
- `markets` are 2-letter countries. When empty, `AD_COUNTRIES` is used.
- The response includes `watch_state`: `watches` (`id`, `url`, `label`), `added`, `removed`,
  `kept`, `errors` (a page 09 refused, e.g. a private address), and `error` (09 unreachable or
  `MONITOR_URL` unset). A 09 problem never fails the registry write.
- `?upsert=true` matches by name or by website host. It merges key pages by URL and updates
  the other fields you sent. The chat tool (81) uses it.

**Watches in 09.** An active competitor gets one watch for its `website` (type `home`) and
one per key page. Each watch has `tag: "competitor:<id>"` and the label `"<name> · <type>"`.
Its default `ignore_patterns` drop these lines:

- all pages: "last updated" lines, copyright, date-only lines and cookie notices
- pricing and product pages: urgency lines ("Only 3 left", "12 people are viewing", "Offer
  ends in", countdowns)
- home and about pages: counters ("Trusted by 10,000+")

Watches with the same URL and settings are kept, with their snapshots. A change deletes the
watch and adds it again, which starts a new baseline. Watches without the tag, added by hand
in 09, are never touched.

### `POST /sync` (Meta Ad Library API)

For each active competitor, 78 makes paged calls to
`GET https://graph.facebook.com/<META_GRAPH_VERSION>/ads_archive` with:

| Parameter | Value |
|---|---|
| `search_page_ids` | `[<meta_page_id>]`. Without a page id: `search_terms=<name>` and `search_type=KEYWORD_EXACT_PHRASE` |
| `ad_reached_countries` | the competitor's **EU** markets as a JSON array, e.g. `["DE","FR"]`. Non-EU markets are skipped with a warning. |
| `ad_active_status` / `ad_type` | `ALL` / `ALL` |
| `ad_delivery_date_min` | today minus `AD_LOOKBACK_DAYS` (default 90) |
| `fields` | `id, page_id, page_name, ad_creative_bodies, ad_creative_link_titles, ad_creative_link_descriptions, ad_creative_link_captions, ad_delivery_start_time, ad_delivery_stop_time, publisher_platforms, languages` |
| `limit` | 100 per page |

- `ad_snapshot_url` is **not** requested, because it contains the access token. `library_url`
  (`https://www.facebook.com/ads/library/?id=<ad id>`) is the public page instead.
- **Paging:** 78 follows `paging.next`, but only on `graph.facebook.com`. It stops when
  `data` is empty. It reads at most 20 pages (2,000 ads) per competitor and warns when it
  stops early.
- **Dedupe and tracking:** the key is the Meta ad `id`.
  - `first_seen` is set once. `last_seen` is updated on every sync.
  - `changed_at` is set when the text (bodies, titles, descriptions, captions) changes.
  - `status` is `stopped` once `ad_delivery_stop_time` is in the past. `stopped_seen_at` is
    the sync where 78 first saw that.
- **All or nothing:** if any Graph call fails, nothing is written.

```json
{"synced_at":"2026-09-27T06:00:03+00:00",
 "competitors":[{"competitor":"Rival Beans","seen":42,"new":3,"changed":1,"stopped":2}],
 "seen":42,"new":3,"changed":1,"stopped":2,
 "warnings":["Rival Beans: US outside the EU: Meta's API returns no commercial ads there; use GET /links/{competitor}"]}
```

### `GET /ads`

```json
{"since":"2026-09-20T00:00:00+00:00","count":1,"ads":[{"ad_id":"1203","competitor":"Rival Beans",
  "change":"new","status":"active","texts":["Fresh beans every month. First box free."],
  "link_titles":["Try Rival"],"link_descriptions":[],"link_captions":["rival.example.com"],
  "platforms":["facebook","instagram"],"languages":["de"],"delivery_start":"2026-09-18",
  "delivery_stop":null,"first_seen":"2026-09-20T06:00:03+00:00","last_seen":"2026-09-27T06:00:03+00:00",
  "changed_at":null,"stopped_seen_at":null,"library_url":"https://www.facebook.com/ads/library/?id=1203"}]}
```

`change` is `new` (first seen since `since`), `stopped`, or `changed` (text changed). `texts`
are the exact ad bodies. The competitor watch (37) quotes only these.

### `GET /links/{ref}`

These are URLs only. 78 never fetches them.

| Platform | Link |
|---|---|
| `meta` | One link for all countries and one per market: `https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=<C>&media_type=all&view_all_page_id=<id>&search_type=page`, or a keyword search by name. `api: true` for EU markets, where `/sync` covers them. |
| `google` | `https://adstransparency.google.com/advertiser/<AR id>?region=anywhere`, and one domain search per domain: `https://adstransparency.google.com/?region=anywhere&domain=<domain>` |
| `linkedin` | `https://www.linkedin.com/ad-library/search?companyIds=<id>`, or `?accountOwner=<name>` |
| `tiktok` | `https://library.tiktok.com/ads?region=all&adv_name=<name>`. If the search box is empty, type the name. |

### `POST /positioning/build` (competitor positioning map)

Which messaging themes each **active** competitor claims, and which we claim. The only part of 78
that uses an LLM.

1. **What they say now**: the latest text of each competitor's watched pages (09 `GET /snapshots`,
   tag `competitor:<id>`; 37 re-checks them every 6 hours) and their **active** ads stored here
   (bodies, link titles and descriptions).
2. **What we say**: the approved facts (05 `GET /facts`: facts, products, key messages) and our own
   watched pages (09 watches on the 05 `website` host or `OWN_DOMAINS`).
3. One gateway call per company (prompt `positioning_themes`, no `{{ brand }}`) sorts claims into the
   themes (default: price, freshness, convenience, quality, ethics, speed, team_office, gifting,
   guarantee; `POSITIONING_THEMES` overrides), each with a quote and its source id.
4. **Code keeps a claim only if** the theme is known, the source id is one it sent, and the quote is an
   exact part of that source (case, spacing and apostrophe style may differ; words may not; 3–25
   words). The stored quote is the source's own span. Drops are counted in `stats`.

The result: `cells[theme][brand] = {count, sources, from_facts, quotes (≤ 5, with source kind, ref,
URL)}`; `crowded` (claimed by at least half the competitors, min 2); `white_space` (**no competitor
claims it and one of our approved facts backs it**: our web pages alone never make white space);
`open_no_fact` (nobody claims it, no fact of ours backs it); `they_claim_we_dont`; `shifts` since the
previous month (started / stopped talking about a theme, or the count at least doubled or halved by 2+,
e.g. "Rival Beans started talking about price in October 2026"; a "started" whose quotes were already in
last month's text, or a "stopped" whose old quotes are still there, is the model re-labelling unchanged
words and is dropped and counted in `shifts_suppressed`); `markdown` (the report 37 saves to
the knowledge base); `usage` (tokens). A company with nothing to read, or whose LLM call failed, has an
`error` and is left out of crowded/white space.

37 runs it on the 1st of each month at 07:00; the control room (72) shows it under More → Positioning.

### `POST /suggestions/scan`

It reads three sources. Each is optional, and a failure goes to `errors`:

- `GET {REVIEWS_URL}/reviews`: review text
- `POST {LISTENING_URL}/search` with `LISTENING_QUERY`, last 30 days: mention URL, title and
  text
- `POST {KB_URL}/search`: only chunks with source `trend-digest`

It finds website domains in the text. These are ignored:

- platforms: reddit, HN, social networks, YouTube, GitHub, Wikipedia, Trustpilot, link
  shorteners, example domains
- `OWN_DOMAINS`
- domains of competitors already in the registry

A domain named in at least `SUGGEST_MIN_MENTIONS` (default 2) different items is stored as
`suggested`. It gets up to 10 pieces of evidence: `source`, `ref`, and a verbatim snippet of up
to 240 characters. Later scans add evidence to existing suggestions. An `ignored` competitor
stays ignored.

Google results pages are **not** a source, because scraping them is outside Google's terms and
there is no official SERP API. Search Console (67) returns queries, not competitor domains.

### Errors

| Status | When |
|---|---|
| `401` | missing or wrong `X-API-Key` |
| `404` | unknown competitor |
| `409` | name already exists (use PATCH or `?upsert=true`) |
| `422` | bad body: page id not numeric, market not 2 letters, type or status unknown, not an http(s) URL, `since` not a date |
| `429` | Meta rate limit (codes 4, 17, 32, 613, or HTTP 429). The `Retry-After` header comes from Meta, or 300 s when Meta sent none. A wait of up to 5 s is retried once inside the call. |
| `502` | Meta refused. **Expired or invalid token** (code 190 or HTTP 401): says to make a new long-lived token. **No permission** (codes 10, 200 or HTTP 403): says to confirm identity at facebook.com/ID and to get Ad Library API access for the app. Other Graph errors show Meta's message. |
| `503` | `INTERNAL_API_KEY` not set (keyed endpoints), or `META_AD_LIBRARY_TOKEN` not set (`/sync` only; the registry, links and suggestions still work) |

The token never appears in a response or a log line. 78 removes it from every message and
from `access_token=` in URLs, and it sets `httpx` logging to WARNING because request URLs
carry the token.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for keyed endpoints. Also sent to 09 and 58. |
| `META_AD_LIBRARY_TOKEN` | — | Your long-lived user token (see Meta access). Empty: `/sync` returns `503` and `configured` is `false`. |
| `META_GRAPH_VERSION` | `v26.0` | Graph API version, as in Meta's reference on 2026-09-27. |
| `AD_COUNTRIES` | `DE,FR,NL,IE` | Markets used when a competitor has none. These are **EU examples**: only EU countries return commercial ads. |
| `AD_LOOKBACK_DAYS` | `90` | `ad_delivery_date_min` = today minus this (1–365). |
| `MONITOR_URL` | empty | 09 base URL (`http://change-monitor:8000`). Empty: key pages are not watched. |
| `REVIEWS_URL`, `LISTENING_URL`, `LISTENING_QUERY`, `KB_URL` | empty | Suggestion sources. Empty: that source is skipped. |
| `OWN_DOMAINS` | empty | Our domains, comma-separated, never suggested. |
| `SUGGEST_MIN_MENTIONS` | `2` | Evidence items needed before a domain is suggested. |
| `BRAND_URL` | empty | 05 base URL: our name, website and approved facts for the positioning map. Empty: no white space. |
| `GATEWAY_URL` | empty | 03 base URL for the positioning map (prompt `positioning_themes`). Empty: `/positioning/build` returns `502`. |
| `POSITIONING_THEMES` | the 9 above | JSON object `{"key": "what counts"}` (keys: lowercase, `_`). |
| `POSITIONING_MAX_CHARS` / `POSITIONING_MAX_CLAIMS` | `6000` / `24` | Source text per company sent to the model; claims asked for. |
| `POSITIONING_TIMEOUT` | `300` | Seconds per gateway call. |
| `DB_PATH` | `/data/ads.sqlite` | SQLite file. |

## Known limits

- Meta's API covers only ads delivered in the EU (plus political and issue ads). A brand
  whose competitors advertise only outside the EU gets no ads from it. Use the links.
- Image and video ads without text come back with empty `texts`.
- A search by name without a page id can miss ads from pages with another name.
- 78 does not detect that an ad disappeared from the library. It records `stopped` only
  from Meta's `ad_delivery_stop_time`.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
