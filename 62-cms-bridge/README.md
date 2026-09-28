# cms-bridge

Deploy **62 of 87** of the local-LLM marketing agent. It receives approved **blog** items from
the publisher (39) and creates them as posts in your CMS: **WordPress** (REST API with an
Application Password) or **Ghost** (Admin API). Markdown becomes HTML, the first paragraph
becomes the excerpt, and an optional `image_url` becomes the featured image. It uses no LLM.

Posts are created as **drafts** by default: a person reads them in the CMS and presses
Publish. Publishing live is opt-in (`WP_STATUS=publish|future`, `GHOST_STATUS=published|scheduled`).

`DRY_RUN` is `true` by default: nothing is sent anywhere until you set it to `false`.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as the `cms-bridge` container on the
`marketing` network (already in the stack's compose file, port `127.0.0.1:8162`). It is
stateless, so it also runs on any container host (Render, Fly.io, Railway), as long as it can
reach your CMS and, for featured images, the image host.

## Run

```bash
docker build -t cms-bridge .
docker run --rm -p 8162:8000 --env-file .env cms-bridge
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
CMS_BRIDGE_KEY=change-me CMS=wordpress WP_URL=https://blog.example.com uvicorn app.main:app --port 8162
```

### Setup, step by step

1. **WordPress:** Users → Profile → Application Passwords → add one for a user who can
   create posts (Author or Editor is enough for drafts). Set `WP_URL`, `WP_USER`,
   `WP_APP_PASSWORD`. Pretty permalinks must be on (the bridge calls `/wp-json/...`).
   **Ghost:** Settings → Integrations → Add custom integration; copy the **Admin API key**
   (`id:secret`) into `GHOST_ADMIN_KEY` and your site URL into `GHOST_URL`.
2. Set `CMS`, keep `DRY_RUN=true`, start the service, check `GET /health` says `configured: true`.
3. Send a test item to `/publish` and read `would_send` in the dry-run answer.
4. Set `DRY_RUN=false` and restart. The next blog item appears as a draft in the CMS.

## Endpoints

🔑 = needs header `X-API-Key: $CMS_BRIDGE_KEY` (falls back to `INTERNAL_API_KEY`).

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"\|"degraded","cms","dry_run","configured","cms_status","allowed_image_hosts","config_errors"}` (never a secret) |
| POST | `/publish` 🔑 | see below | `{"status":"created"\|"dry_run"\|"already_created","cms","external_id","external_url","url","cms_status","warnings"}` |

```bash
curl -s localhost:8162/publish -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"id":12,"channel":"blog","title":"Autumn pricing is here",
       "text":"We cut prices for **small teams**.\n\n## What changed\n\n- Starter: 9 EUR",
       "link":"https://s.example/abc","image_url":"http://image-cards:8000/cards/12.png"}'
# DRY_RUN=true:  {"status":"dry_run","cms":"wordpress","external_id":null,"external_url":null,
#                 "url":null,"cms_status":"draft","would_send":{"method":"POST",
#                 "url":"https://blog.example.com/wp-json/wp/v2/posts","body":{...},
#                 "featured_image":"http://image-cards:8000/cards/12.png"},"warnings":[]}
# DRY_RUN=false: {"status":"created","cms":"wordpress","external_id":501,
#                 "external_url":"https://blog.example.com/?p=501","url":"...same...",
#                 "cms_status":"draft","warnings":[]}
```

### What the publisher must send

The payload is the one 39 already sends to `PUBLISH_WEBHOOK_URL`, plus two optional fields:

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | calendar item id (used to avoid creating the same post twice) |
| `channel` | yes | must be `blog` (case-insensitive); anything else → `422` |
| `title` | yes | post title |
| `text` (or `body`) | yes | markdown body, short link already swapped in |
| `link` / `short_url` | no | appended as a link at the end when the text does not contain it |
| `campaign` | no | ignored (kept for symmetry with 54) |
| `scheduled_at` | only for `future`/`scheduled` | ISO 8601; no zone = UTC |
| `image_url` | no | featured image; only fetched from `ALLOWED_IMAGE_HOSTS` |

**Router in 39 (done):** send `channel == "blog"` items to
`$env.CMS_PUBLISH_URL` (= `http://cms-bridge:8000/publish`) with header
`X-API-Key: $env.CMS_PUBLISH_KEY` (= `CMS_BRIDGE_KEY`, or `INTERNAL_API_KEY` when that is
empty), and every other channel to `PUBLISH_WEBHOOK_URL` as today. Until then, pointing
`PUBLISH_WEBHOOK_URL` here publishes blog items and answers `422` for social ones (they stay
approved and are retried each run). 39 marks the item published (with `url` as `external_url`) only when `cms_status` is
`publish`/`future`/`published`/`scheduled`. A draft (or `status: "dry_run"`) stays `approved` with
the calendar note `sent to CMS as draft: <url>` and is not sent again.

### What `/publish` does

- A first line `# Title` is dropped (the CMS shows the title). Markdown (CommonMark + tables +
  strikethrough) becomes HTML; **raw HTML in the text is escaped**, never passed through.
- Excerpt = plain text of the first paragraph, at most 300 characters (Ghost's limit).
- **WordPress:** `POST {WP_URL}/wp-json/wp/v2/posts` `{title, content, excerpt, status}`;
  `WP_STATUS=future` adds `date_gmt` from `scheduled_at` (a past date publishes at once).
- **Ghost:** `POST {GHOST_URL}/ghost/api/admin/posts/?source=html`
  `{posts:[{title, html, custom_excerpt, status}]}` with `Authorization: Ghost <JWT>`
  (HS256, `kid` = key id, `aud` `/admin/`, valid 5 min) and `Accept-Version: v5.0`;
  `GHOST_STATUS=scheduled` adds `published_at`.
- **Featured image:** `image_url` must be http(s) and its host must be in
  `ALLOWED_IMAGE_HOSTS` (exact names, no redirects followed, 10 s timeout, 5 MB cap,
  `Content-Type: image/*`). Then WordPress `POST /wp-json/wp/v2/media` → `featured_media`, or
  Ghost `POST /ghost/api/admin/images/upload/` → `feature_image`. Any image problem skips the
  image with a line in `warnings`; **the post is still created**.
- The same `id` sent again after success returns the first answer with
  `status: "already_created"` (in memory until restart).
- CMS errors (401/403 wrong credentials, other 4xx, 5xx, redirects, unreachable, 30 s
  timeout) → `502` `{"message","cms_status","cms_error"}` with the CMS's own message.
  Missing credentials when live → `503`. Secrets are scrubbed from every answer and log line.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `CMS_BRIDGE_KEY` | `INTERNAL_API_KEY` | Required for `/publish`. Neither set → `503`; wrong header → `401`. |
| `CMS` | `wordpress` | `wordpress` or `ghost`. |
| `WP_URL`, `WP_USER`, `WP_APP_PASSWORD` | — | WordPress site and Application Password (HTTP Basic). |
| `WP_STATUS` | `draft` | `draft`, `pending`, `private`, `publish`, `future`. |
| `GHOST_URL`, `GHOST_ADMIN_KEY` | — | Ghost site and Admin API key `id:secret`. |
| `GHOST_STATUS` | `draft` | `draft`, `published`, `scheduled`. |
| `ALLOWED_IMAGE_HOSTS` | `image-cards,localhost` | Hosts `image_url` may point at. Empty = never fetch images. |
| `DRY_RUN` | `true` | Anything other than `false`/`0`/`no`/`off` keeps it on: answer `would_send`, no network. |

## CMS API reference used

- WordPress posts and media endpoints:
  <https://developer.wordpress.org/rest-api/reference/posts/#create-a-post>,
  <https://developer.wordpress.org/rest-api/reference/media/#create-a-media-item>
- WordPress Application Passwords:
  <https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/#application-passwords>
- Ghost Admin API, token authentication, posts with `?source=html`, image upload:
  <https://ghost.org/docs/admin-api/#token-authentication>,
  <https://ghost.org/docs/admin-api/#creating-a-post>,
  <https://ghost.org/docs/admin-api/#uploading-an-image>

The tests mock these shapes; they have not been run against a live WordPress or Ghost.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
