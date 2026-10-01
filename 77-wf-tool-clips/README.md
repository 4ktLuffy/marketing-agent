# 77 · Clips from a video

Deploy **77 of 91** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Turns one long video the user owns (a webinar, a talk, a podcast recording) into short vertical clips, each saved to the content calendar (19) as a `video` item for approval. It sends the direct link to the clip finder (73, `POST $CLIPS_URL/jobs`: transcription, clip scoring, 9:16 render with burned-in captions) and polls the job every 20 s (`GET /jobs/{id}`; a job takes minutes: about 4 for a 3-minute talk on a laptop) until it is done, failed, or `CLIPS_MAX_WAIT_MIN` has passed. While another video is being cut (73 answers 429) it retries every 30 s within the same limit. A link to a video platform (YouTube, TikTok, Instagram, Vimeo, …) is refused by 73 with 422 (their terms forbid downloading): the tool returns that message and asks for a direct link to the file. For each clip, one at a time, it writes a caption through the gateway (03, prompt `social_posts` for the first channel asked for, default `instagram, tiktok`; topic = the clip's title and the start of its transcript; the clip's transcript is the only source, the gateway adds the approved facts), runs it through the quality gate (35, report only, with the transcript as evidence for the claim checker: it is the user's own content) and saves it: title = the clip's title, body = the caption only (it is what gets published), `video_url` = the clip MP4, `image_url` = its poster, status `in_review` if the gate passed and `draft` with the problems otherwise, notes `clip m:ss–m:ss from <source> (score N)`, `video preview: <clip url> (<n> s)` and the job id. Editing the caption in the approval form (38) or the control room (72) keeps the clip (it is not a video script, nothing is re-rendered). The publisher (39) sends the clip as `video_url`; `54-postiz-bridge` fetches clips under `CLIPS_PUBLIC_URL` from `CLIPS_INTERNAL_URL` and posts them to the integration mapped to `video` in its `CHANNEL_MAP`.

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
  n8n publish:workflow --id=mktWf77ClipsTool
```

Its workflow id is fixed (`mktWf77ClipsTool`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `url` | direct http(s) link to the video or audio file (not a YouTube/TikTok/… page) |
| `max_clips` | optional; how many clips, default 3, at most `CLIPS_MAX_PER_VIDEO` |
| `channels` | optional; comma list of instagram, tiktok, youtube, facebook, x, linkedin, threads (default `instagram, tiktok`); the caption is written for the first |
| `topic` | optional; what the video is about, context for the captions |

**Returns:** `{result}`: the calendar items created (id, title, time range, status, clip URL) and the approval link, or why no clips were made

**Called by:** the chat agent (24), tool `clip_video`

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `CLIPS_URL` | the clip finder (73), `http://clip-finder:8000` in the stack; empty = the tool says it is not set up |
| `CLIPS_MAX_WAIT_MIN` | how long to wait for a job, default 30 minutes |
| `CLIPS_MAX_PER_VIDEO` | most clips per video, default 5 (73 accepts up to its `MAX_CLIPS`) |
| `N8N_PUBLIC_URL` | for the approval form link |

## Depends on

- `73-clip-finder`
- `03-llm-gateway`
- `04-prompt-library (prompt `social_posts`)`
- `19-content-calendar`
- `35-wf-tool-quality-gate`
- `54-postiz-bridge (to publish, `CLIPS_PUBLIC_URL`)`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "url": "https://cdn.example.com/webinars/2026-09.mp4",
  "max_clips": "3",
  "channels": "instagram, tiktok",
  "topic": "our September webinar on office coffee rituals"
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
