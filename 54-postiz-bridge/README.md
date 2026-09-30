# postiz-bridge

Deploy **54 of 89** of the local-LLM marketing agent. It receives the approved posts that the
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
| POST | `/publish` 🔑 | `{"id","channel","title","text","link"?,"campaign"?,"image_url"?,"video_url"?}` | `{"url","postiz_id","status","media","video_note"?}` |

```bash
curl -s localhost:8154/integrations
# [{"id":"cm4ean69r0003w8w1cdomox9n","name":"Acme","provider":"x","disabled":false,
#   "profile":"acme","channels":["x"]}]

curl -s localhost:8154/publish -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"id":12,"channel":"linkedin","title":"Autumn launch","text":"New pricing is live.",
       "link":"https://s.example/abc","campaign":"autumn-launch"}'
# DRY_RUN=true:  {"status":"dry_run","url":null,"postiz_id":null,"channel":"linkedin",
#                 "integration":"<id>","would_upload":null|{...},"would_send":{...the Postiz request body...}}
# DRY_RUN=false: {"url":null,"postiz_id":"cm5...","status":"queued","channel":"linkedin",
#                 "provider":"linkedin-page","media":[{"id","path"}]}
```

### What `/publish` does

- `channel` is lowercased and looked up in `CHANNEL_MAP`. Not there → `422` naming the channel.
- The post text is `text`, plus `link` on its own line when the text does not already contain
  it. `title` is not posted (social posts have no title). Plain text is sent as `<p>`
  paragraphs, the HTML the Postiz editor stores, so line breaks and `&` survive.
- It asks Postiz for the integration's provider (`GET /integrations`, cached 5 min), then
  sends `POST /posts` with `type: "now"` and `shortLink: false` (links are already short
  links from 16).
- `image_url` (the publisher 39 sends the calendar item's image, a card from 17): the bridge
  downloads it (PNG/JPEG/GIF/WebP, at most 10 MB, no redirects, never with the Postiz key),
  uploads it with `POST /upload` (multipart field `file`) and attaches the returned
  `{id, path}` as the post's `image`. An image URL starting with `CARDS_PUBLIC_URL` is fetched
  from `CARDS_URL` instead, because the public URL (e.g. `http://localhost:8117`) is for the
  reviewer's browser and is not reachable from inside the container. An image that cannot be
  loaded or uploaded → `502` with `image_error` and nothing is posted, so the item stays
  `approved` and is retried; to publish without it, move the item back to draft and clear
  `image_url`.
- `video_url` (the publisher sends the item's video, an MP4 from 71-video-assembly): see
  [Video](#video) below. When the video is attached, it is the post's only media (the image,
  usually the video's poster, is not uploaded next to it).
- Providers that need media → `422` unless the post has an image or a video: Instagram and
  TikTok accept one image or a video, Pinterest and Dribbble one image, YouTube needs a
  video. Providers that need settings the
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

### Video

- The bridge downloads the video (MP4 or MOV, no redirects, never with the Postiz key; a URL
  under `VIDEO_PUBLIC_URL` is fetched from `VIDEO_URL`, a clip of `73-clip-finder` under
  `CLIPS_PUBLIC_URL` from `CLIPS_INTERNAL_URL`, and their posters likewise), uploads it with the same
  `POST /upload` as images, with its own content type (`video/mp4`), and attaches the
  returned `{id, path}` as the post's `image` list (Postiz keeps images and videos in one
  media library). **Not verified against a live Postiz** in this repo: the tests mock the
  upload. Postiz may refuse a file type or size its own storage settings don't allow; that
  is a `502` with `postiz_error`, like a failed image upload.
- A video this post can't carry is **left off** and the rest goes out as before (text, plus
  the image if there is one), with the reason in `video_note` (`video not attached: ...`):
  larger than `MAX_VIDEO_MB`, longer than the network takes through its API (X: 140 s), a
  network that takes images only (Pinterest, Dribbble), not an MP4/MOV, or gone (4xx, e.g.
  removed by 71's `RETENTION_DAYS`). The duration comes from the MP4 header (`mvhd`).
- A video service that is unreachable, times out or answers 5xx → `502` with `video_error`
  and nothing is posted; the item stays approved and the publisher retries.
- The dry run downloads the video too and reports it in `would_upload` (`video_url`,
  `fetched_from`, `endpoint`, `file`, `content_type`, `bytes`, `duration_s`) plus
  `video_note`.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for `/publish`. Not set → `503`; wrong or missing header → `401`. |
| `POSTIZ_URL` | `https://api.postiz.com` | Postiz backend; `/public/v1` is appended (a URL that already ends in it is fine). Self-hosted: your `NEXT_PUBLIC_BACKEND_URL`, e.g. `https://postiz.example.com/api`. |
| `POSTIZ_API_KEY` | — | Postiz public API key, sent as `Authorization: <key>`. Never logged or returned. Not needed in dry run. |
| `CHANNEL_MAP` | `{}` | JSON `{"linkedin":"<integration id>",...}`. A value can also be `{"id":"...","settings":{...},"provider"?:"..."}`. Invalid JSON: the service starts, `/health` shows the problem, `/publish` answers `503`. |
| `DRY_RUN` | `true` | Anything other than `false`/`0`/`no`/`off` keeps it on: validate and answer `{"status":"dry_run"}` without calling Postiz. With an `image_url`, the dry run still downloads the image (from our side, not Postiz) and reports it in `would_upload` (`image_url`, `fetched_from`, `endpoint`, `file`, `content_type`, `bytes`). |
| `CARDS_PUBLIC_URL` | — | Public base of `17-image-cards` (the stack sets it from `CARDS_PUBLIC_URL`). |
| `CARDS_URL` | — | Internal base of `17-image-cards` (`http://image-cards:8000` in the stack); image URLs under `CARDS_PUBLIC_URL` are fetched from here. |
| `VIDEO_PUBLIC_URL` | — | Public base of `71-video-assembly` (its `PUBLIC_BASE_URL`). |
| `VIDEO_URL` | — | Internal base of `71-video-assembly` (`http://video-assembly:8000` in the stack); video URLs under `VIDEO_PUBLIC_URL` are fetched from here. |
| `CLIPS_PUBLIC_URL` | — | Public base of `73-clip-finder` (its `PUBLIC_BASE_URL`). |
| `CLIPS_INTERNAL_URL` | — | Internal base of `73-clip-finder` (`http://clip-finder:8000` in the stack); clip and poster URLs under `CLIPS_PUBLIC_URL` are fetched from here. |
| `MAX_VIDEO_MB` | `100` | Largest video the bridge attaches; a larger one is left off with a `video_note`. |

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
- Media upload (`POST /upload`, multipart field `file` → `{id, name, path, ...}`) and the
  post's `image: [{id, path}]`: `upload` in the controller above, `MediaFile` and `PostContent`
  in the openapi.json above, and
  <https://github.com/gitroomhq/postiz-app/blob/main/libraries/nestjs-libraries/src/dtos/media/media.dto.ts>
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
