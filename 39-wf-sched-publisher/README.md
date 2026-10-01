# 39 · Publisher

Deploy **39 of 91** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every 15 minutes it takes the approved calendar items that are due, swaps each link for a tracked short link (16) and routes each post by channel: `blog` goes to the CMS bridge (`CMS_PUBLISH_URL`, 62), every other channel to your publish endpoint (`PUBLISH_WEBHOOK_URL`, e.g. 54 Postiz). A channel whose URL is empty is skipped. An item is marked published only if the endpoint accepted it and it really went live: a dry run is not published, and a blog post the CMS created as a **draft** stays `approved` with the note `sent to CMS as draft: <url>` (and `external_url` set), so a person presses Publish in the CMS and the post is not sent again. A CMS that publishes live (`cms_status` `publish`/`future`/`published`/`scheduled`) marks it published. Review replies (channel `review_reply`, from 60) and newsletters (`newsletter`, 66, already a Listmonk draft) are never sent: a person handles those by hand. Nor are work items for people (`blog_refresh`, `seo_brief`, `competitor_brief`, `lead_reply`, `visibility_gap`, `positioning`) or client reports (`client_report`, 85): a person forwards an approved report. An approved email flow (`email_flow`, 86) is never posted either: approving it lets 86 run the sequence.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand, from this folder:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  sh -c 'cat > /tmp/wf.json && n8n import:workflow --input=/tmp/wf.json' < workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf39Publisher
```

Its workflow id is fixed (`mktWf39Publisher`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

every 15 minutes. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `PUBLISH_WEBHOOK_URL` | endpoint for every channel except `blog`; it receives `{id, channel, title, text, link, short_url, campaign, image_url, video_url, scheduled_at}` (`image_url`: the post's image, e.g. a card from 17, or null; `video_url`: its video, e.g. an MP4 from 71, or null): a Zapier/Make/Buffer hook or your own, e.g. 54-postiz-bridge. Empty = social items are not sent. |
| `PUBLISH_WEBHOOK_KEY` | optional; sent as `X-API-Key` to `PUBLISH_WEBHOOK_URL` (only when set) |
| `CMS_PUBLISH_URL` | endpoint for `blog` items, same payload: `http://cms-bridge:8000/publish` (62). Empty = blog items are not sent. |
| `CMS_PUBLISH_KEY` | sent as `X-API-Key` to `CMS_PUBLISH_URL`; empty = `INTERNAL_API_KEY` |

## Setup

To post straight from n8n instead, replace the **Publish** node with n8n's LinkedIn, X or Facebook Graph node (with its credential) and keep the **Succeeded** check after it.

A blog item that was sent to the CMS carries the note `sent to CMS as ...` and is skipped from then on. A dry-run send (cms-bridge `DRY_RUN=true`) only holds the item for 24 hours, so after going live every approved blog item is sent for real within a day; nothing to delete by hand.

## Depends on

- `16-link-shortener`
- `19-content-calendar`
- `62-cms-bridge (blog items, optional)`
- `54-postiz-bridge or any webhook (social items, optional)`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
