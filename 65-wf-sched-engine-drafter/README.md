# 65 · Engine drafter

Deploy **65 of 91** of the marketing agent. This deploy is an n8n scheduled workflow.

Every morning it drafts the content engine's next slots. It takes the `planned` slots of active pillars (61; a pillar paused by the stop rule is skipped) dated from today to `ENGINE_LOOKAHEAD_DAYS` ahead, earliest first, at most `ENGINE_DRAFTS_PER_RUN`, each with the `idea` calendar item that the plan tool (64) created. One slot at a time, it writes the piece from the slot's atom (and its evidence) with the slot's hook style and format through the gateway (03), using the same prompts as the writers: `social_posts` for posts (as 26 and 48 do, with `prefer_hooks` = the slot's hook and 2 examples from 46: approved posts that earned clearly more clicks on the slot's channel first, else the most recently approved), `x_thread` for X threads and `carousel_text` for carousels (same hook and examples; code numbers the thread's posts `1/N` and lays the carousel out as `Slide k — Title` blocks plus the caption), `blog_post` (25), `email_newsletter` (28) and `video_script` (57). Those writers save new calendar items, so they are not called as sub-workflows: this workflow fills the existing idea item instead. It then asks 61 whether the draft is too close to anything on that channel in the last 90 days (`/novelty/check`); if so it writes once more with a different hook, and if that is still too close the slot is dropped with the reason (the idea item keeps a note). A novel draft goes through the quality gate (35; report-only for video scripts, X threads and carousels; each part of an X thread is checked against 280 characters), gets a title card from 17 on image channels (a carousel gets one per slide: the first is the item's image, all are listed in the item's notes; a card that fails is named, the draft is kept), and a video script is rendered as a preview MP4 by 71 (`VIDEO_URL`, voice `VIDEO_VOICE`; saved as `video_url`, its poster as the image, with the note `video preview: <url> (<n> s)`, or `video not rendered: <reason>` when it fails: the draft is kept either way), and it is saved to its calendar item (title, body, hook style, image, video), which moves to `in_review` if the gate passed and stays `draft` with the problems otherwise. The final text is registered in 61 for later novelty checks and the slot is marked `drafted`. A failure on one slot leaves it `planned` for the next run. A summary with a link to the approval form goes to `NOTIFY_WEBHOOK_URL`. Nothing is published until a person approves it.

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
  n8n publish:workflow --id=mktWf65EngDraft0
```

Its workflow id is fixed (`mktWf65EngDraft0`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

Daily 06:00. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `ENGINE_URL` | the content engine (61) |
| `ENGINE_DRAFTS_PER_RUN` | most slots drafted per run; default 12 (each takes a few minutes on a local model) |
| `ENGINE_LOOKAHEAD_DAYS` | draft slots dated up to this many days ahead; default 7 |
| `NOTIFY_WEBHOOK_URL` | optional; receives the summary |
| `N8N_PUBLIC_URL` | used for the approval-form link |
| `VIDEO_URL` | the video service (71); empty = video scripts get no preview video |
| `VIDEO_VOICE` | voice for the preview: `piper`, `say` (macOS only) or `none`; empty = 71's `TTS_BACKEND` |

## Depends on

- `61-content-engine`
- `03-llm-gateway`
- `19-content-calendar`
- `35-wf-tool-quality-gate`
- `46-learning-service`
- `05-brand-service`
- `17-image-cards`
- `64-wf-tool-content-engine`
- `71-video-assembly (optional)`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
