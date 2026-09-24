# postiz-bridge

Deploy **54 of 60** of the local-LLM marketing agent. It receives the approved posts that the
publisher (39) sends to `PUBLISH_WEBHOOK_URL` and posts them through
[Postiz](https://postiz.com), self-hosted or cloud. Each of our channels (`linkedin`, `x`, …)
maps to one connected Postiz integration. It uses no LLM.

`DRY_RUN` is `true` by default: nothing is posted until you set it to `false`.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as the `postiz-bridge` container on the
`marketing` network (already in the stack's compose file). Point the publisher at it:
`PUBLISH_WEBHOOK_URL=http://postiz-bridge:8000/publish`. It is stateless, so it also runs on
any container host (Render, Fly.io, Railway), as long as it can reach Postiz.

## Run

```bash
docker build -t postiz-bridge .
docker run --rm -p 8154:8000 --env-file .env postiz-bridge
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me CHANNEL_MAP='{"linkedin":"<id>"}' uvicorn app.main:app --port 8154
```

### Setup, step by step

1. In Postiz, connect your channels and create an API key (Settings → Public API).
2. Set `POSTIZ_URL` and `POSTIZ_API_KEY`, keep `DRY_RUN=true`, start the service.
3. `curl -s localhost:8154/integrations` and copy each integration `id` into `CHANNEL_MAP`.
4. Send a test item to `/publish` and check `would_send` in the dry-run answer.
5. Set `DRY_RUN=false` and restart.

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"\|"degraded","dry_run","postiz_url","postiz_key_set","channels","config_errors"}` |
| GET | `/integrations` | — | `[{"id","name","provider","disabled","profile","channels"}]` from Postiz |
| POST | `/publish` 🔑 | `{"id","channel","title","text","link"?,"campaign"?}` | `{"url","postiz_id","status"}` |

```bash
curl -s localhost:8154/integrations
# [{"id":"cm4ean69r0003w8w1cdomox9n","name":"Acme","provider":"x","disabled":false,
#   "profile":"acme","channels":["x"]}]

curl -s localhost:8154/publish -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"id":12,"channel":"linkedin","title":"Autumn launch","text":"New pricing is live.",
       "link":"https://s.example/abc","campaign":"autumn-launch"}'
# DRY_RUN=true:  {"status":"dry_run","url":null,"postiz_id":null,"channel":"linkedin",
#                 "integration":"<id>","would_send":{...the Postiz request body...}}
# DRY_RUN=false: {"url":null,"postiz_id":"cm5...","status":"queued","channel":"linkedin",
#                 "provider":"linkedin-page"}
```

### What `/publish` does

- `channel` is lowercased and looked up in `CHANNEL_MAP`. Not there → `422` naming the channel.
- The post text is `text`, plus `link` on its own line when the text does not already contain
  it. `title` is not posted (social posts have no title). Plain text is sent as `<p>`
  paragraphs, the HTML the Postiz editor stores, so line breaks and `&` survive.
- It asks Postiz for the integration's provider (`GET /integrations`, cached 5 min), then
  sends `POST /posts` with `type: "now"` and `shortLink: false` (links are already short
  links from 16).
- Providers that need media (Instagram, TikTok, YouTube, Pinterest, Dribbble) → `422`: this
  bridge posts text only and Postiz would reject the post. Providers that need settings the
  bridge cannot guess (Reddit/Lemmy `subreddit`, Discord/Slack `channel`, Medium, Dev.to,
  Hashnode, WordPress `title`…, Listmonk) → `422` unless you give them in `CHANNEL_MAP`.
  X gets `who_can_reply_post: "everyone"`.
- `url` is always `null`: Postiz publishes in the background and only knows the public post
  URL (`releaseURL`) after the network accepted it. `status` is `queued`. The post shows up
  in the Postiz calendar; failures show there too.
- The same `id` + `channel` sent again after success returns the first answer with
  `status: "already_published"` instead of posting twice (kept in memory until restart).
- Postiz errors (4xx, 5xx, unreachable, 30 s timeout) → `502` with
  `{"message","postiz_status","postiz_error"}`. Postiz rate limit (`429`) → `502` with a
  `Retry-After` header and `retry_after` in the body; the publisher retries on its next run.
- Mapped id that Postiz does not have, a disabled integration, a missing `POSTIZ_API_KEY`
  when live, or an invalid `CHANNEL_MAP` → `503` with the reason.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for `/publish`. Not set → `503`; wrong or missing header → `401`. |
| `POSTIZ_URL` | `https://api.postiz.com` | Postiz backend; `/public/v1` is appended (a URL that already ends in it is fine). Self-hosted: your `NEXT_PUBLIC_BACKEND_URL`, e.g. `https://postiz.example.com/api`. |
| `POSTIZ_API_KEY` | — | Postiz public API key, sent as `Authorization: <key>`. Never logged or returned. Not needed in dry run. |
| `CHANNEL_MAP` | `{}` | JSON `{"linkedin":"<integration id>",...}`. A value can also be `{"id":"...","settings":{...},"provider"?:"..."}`. Invalid JSON: the service starts, `/health` shows the problem, `/publish` answers `503`. |
| `DRY_RUN` | `true` | Anything other than `false`/`0`/`no`/`off` keeps it on: validate and answer `{"status":"dry_run"}` without calling Postiz. |

## Postiz API reference used

Checked against the Postiz docs and source in September 2026:

- Base URL (cloud `https://api.postiz.com/public/v1`, self-hosted
  `{NEXT_PUBLIC_BACKEND_URL}/public/v1`):
  <https://github.com/gitroomhq/postiz-docs/blob/main/snippets/base-url.mdx>,
  servers in <https://github.com/gitroomhq/postiz-docs/blob/main/public-api/openapi.json>
- Auth header, rate limits: <https://docs.postiz.com/public-api/introduction>,
  <https://github.com/gitroomhq/postiz-app/blob/main/apps/backend/src/services/auth/public.auth.middleware.ts>,
  <https://github.com/gitroomhq/postiz-app/blob/main/libraries/nestjs-libraries/src/throttler/throttler.provider.ts>,
  <https://github.com/gitroomhq/postiz-app/blob/main/apps/backend/src/app.module.ts> (`API_LIMIT`, per hour)
- Endpoints and response shapes (`GET /integrations`, `POST /posts` → `[{postId, integration}]`):
  <https://github.com/gitroomhq/postiz-app/blob/main/apps/backend/src/public-api/routes/v1/public.integrations.controller.ts>
- Create-post body and per-provider settings: <https://docs.postiz.com/public-api/posts/create>,
  <https://github.com/gitroomhq/postiz-app/blob/main/libraries/nestjs-libraries/src/dtos/posts/create.post.dto.ts>,
  <https://github.com/gitroomhq/postiz-app/tree/main/libraries/nestjs-libraries/src/dtos/posts/providers-settings>
- Which providers need media (`checkValidity`):
  <https://github.com/gitroomhq/postiz-app/tree/main/libraries/nestjs-libraries/src/integrations/social>
- Validation error shape `{statusCode, provider, name, message}`:
  <https://github.com/gitroomhq/postiz-app/blob/main/apps/backend/src/api/routes/posts.validation.exception.ts>
- Content HTML handling: `sanitize.post.content.ts` and `strip.html.validation.ts` in
  <https://github.com/gitroomhq/postiz-app/tree/main/libraries/helpers/src/utils>

The tests mock these shapes; they have not been run against a live Postiz.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
