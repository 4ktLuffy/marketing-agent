# video-assembly

Deploy **71 of 90** of the local-LLM marketing agent. It turns a short-form video script
(the output of prompt `video_script` in 04, written by deploy 57) into a finished
**vertical MP4, 1080×1920**, ready for Reels, TikTok or Shorts:

- one frame per segment (hook, each beat, CTA) with the big on-screen text, a brand bar
  (logo square, name, beat counter, progress bar) and the spoken line **burned in as a
  caption**;
- an optional **voiceover**, spoken locally (piper), with each segment lasting as long
  as its line;
- short crossfades between segments, H.264 + AAC, 30 fps, `+faststart`.

It runs fully locally: no paid APIs, no network calls, no LLM. The most-viewed n8n
marketing templates all make video with paid services (Creatomate, ElevenLabs, fal, …);
this one is a free, text-first preview of the same idea. A person still films the shots
(each beat's `shot`) or posts this text-and-voice version as it is.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a volume on `/data`. n8n calls it at `http://video-assembly:8000`
(env `VIDEO_URL`). The videos are served from `PUBLIC_BASE_URL`, which must be reachable
from the reviewer's browser (the approval form links to them). Rendering is CPU work
(about 1 s per 15 s of video without voice, ~6 s with voice on a laptop); one instance is
enough.

## Run

```bash
docker build -t video-assembly .        # downloads the piper voice (about 60 MB) once
docker run --rm -p 8171:8000 -e INTERNAL_API_KEY=change-me -v video-data:/data video-assembly
```

The image installs `ffmpeg` with apt and downloads the piper voice `en_US-lessac-medium`
at build time from the official
[rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) repository, pinned to
its `v1.0.0` revision. Pick another voice from the same repository with build args:

```bash
docker build -t video-assembly \
  --build-arg PIPER_VOICE_PATH=en/en_GB/alba/medium --build-arg PIPER_VOICE_NAME=en_GB-alba-medium .
```

Local without Docker (a Mac, for example):

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt     # imageio-ffmpeg brings an ffmpeg binary
pytest -q
INTERNAL_API_KEY=change-me DATA_DIR=./data TTS_BACKEND=say uvicorn app.main:app --port 8171
```

`ffmpeg` on `PATH` is used if present, otherwise the binary bundled with `imageio-ffmpeg`.

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok","ffmpeg":{"found","source"},"voice_backends":{"piper","say","none"},"default_backend","max_seconds"}`; `503` + `"degraded"` when no ffmpeg is found |
| POST | `/render` 🔑 | see below | `201` `{"id","url","poster_url","duration_s","width","height","fps","size_bytes","voice_backend","render_ms","segments":[{"index","kind","on_screen","spoken","start_s","duration_s"}]}` |
| GET | `/videos/{id}.mp4` | — | the video (`video/mp4`), no key: the id is the secret |
| GET | `/videos/{id}.jpg` | — | the poster (the hook frame), `image/jpeg` |

```json
{
  "script": { "hook": {"spoken": "...", "on_screen": "..."},
              "beats": [{"spoken": "...", "on_screen": "...", "shot": "..."}],
              "cta": "...", "caption": "...", "hashtags": ["..."], "estimated_seconds": 40 },
  "brand": {"name": "Northside Coffee", "accent": "#B45309", "logo_text": "NC"},
  "voice": {"backend": "piper", "voice_id": null},
  "style": {"theme": "light", "show_shots": false}
}
```

`script` is exactly the gateway's `output` for prompt `video_script` (extra fields are
ignored; `caption` and `hashtags` are not drawn, they're for the post). `brand`, `voice`
and `style` are optional.

```bash
curl -s localhost:8171/render -H 'content-type: application/json' -H 'X-API-Key: change-me' -d '{
  "script": {"hook": {"spoken": "Ever wondered how decaf loses its caffeine?", "on_screen": "Decaf, explained"},
             "beats": [{"spoken": "It starts as green beans, soaked in warm water.", "on_screen": "Green beans first",
                        "shot": "close-up of green beans"}],
             "cta": "Follow for more coffee tips"},
  "brand": {"name": "Northside Coffee", "logo_text": "NC"}, "voice": {"backend": "none"}}'
# {"id":"7b4b…","url":"http://localhost:8171/videos/7b4b….mp4","duration_s":7.3,"width":1080,"height":1920,…}
```

### How a video is built

- **Segments:** hook, then each beat (1–8), then the CTA. The hook and beats show
  `on_screen` big and `spoken` as the caption; the CTA frame is the accent colour with the
  CTA line big. With `style.show_shots` a beat also shows its `shot` in small print (a
  filming note for the reviewer). Emoji are dropped from the frames and the voice: the
  fonts can't draw them and a voice would read their names.
- **Text** is word-wrapped and shrinks until it fits (the approach of 17-image-cards);
  words wider than the frame are broken; anything still too long ends in `…`. Text stays
  out of the top ~150 px and bottom ~350 px, where platforms draw their own buttons.
- **Captions are drawn on the frame** with Pillow, so ffmpeg needs no subtitle filter and
  no fontconfig. ffmpeg's command line only ever holds temp file paths and numbers.
- **Timing:** with a voice, a segment lasts its audio + 0.3 s (at least 1.5 s). With
  `none`, 2.6 words per second of the spoken line (at least 1.5 s). Segments crossfade for
  0.25 s, so the total is the sum minus 0.25 s per join. A video longer than `MAX_SECONDS`
  (90) is refused with `422` (checked before rendering for `none`, after speech otherwise).
- **Audio:** the voice clips are placed at each segment's start in one track. With
  `none` the track is silent, but it is there: some platforms refuse uploads without audio.

### Voice backends

| `voice.backend` | Where | Notes |
|---|---|---|
| `piper` | Docker (default there) | [piper-tts](https://github.com/OHF-Voice/piper1-gpl), local neural TTS, voice from `PIPER_VOICE`. `voice_id` picks another `.onnx` in the same folder (e.g. `en_GB-alba-medium`). |
| `say` | macOS only | The Mac's built-in `say`, for trying the service on a laptop. Refused elsewhere. `voice_id` must be an installed voice (`say -v '?'`). |
| `none` | anywhere | No voice; silent audio track. |

No `voice.backend` → `TTS_BACKEND`. A backend that can't run here (piper without its
model, `say` on Linux, an unknown name, an unknown voice) is a `422` with a message that
says what's missing, not a `500`. `/health` lists which backends are available.

### Limits

`422` for an invalid script (0 or more than 8 beats, blank text, over-long fields), a bad
`accent`, an unavailable backend, or a video over `MAX_SECONDS`. `429` when
`MAX_CONCURRENT_RENDERS` renders are already running. ffmpeg is killed after
`FFMPEG_TIMEOUT` seconds (`500`). Each render works in a temp folder under
`DATA_DIR/tmp` that is removed afterwards (and on startup, in case of a crash).

## How the workflows use it

Wired in 57 (content formats) and 65 (engine drafter):

1. After a `video_script` draft has been written and gated, the workflow calls
   `POST $VIDEO_URL/render` with `X-API-Key: $INTERNAL_API_KEY`, the gateway's `output` as
   `script`, `brand` from the brand service (05 `/profile`: name, `accent_color` if valid,
   initials as `logo_text`) and `voice.backend` from n8n's `VIDEO_VOICE` (empty: left out,
   so `TTS_BACKEND` applies). No `VIDEO_URL`: no call.
2. The calendar item (19) gets `url` as `video_url`, `poster_url` as `image_url` when it has
   no image, and the note `video preview: <url> (<duration_s> s)`. A `422` (too long, voice
   unavailable), `429` or an unreachable service only adds `video not rendered: <reason>`:
   the script is saved either way.
3. The approval form (38) shows `▶ Watch the video (N s)` and the poster, so the reviewer
   watches the preview before approving. The publisher (39) sends `video_url` on, and
   `54-postiz-bridge` uploads it as the post's media (it fetches URLs under
   `VIDEO_PUBLIC_URL` from `VIDEO_URL`). This service never uploads anything itself.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for `POST /render`. Unset → `503`; wrong or missing key → `401`. |
| `DATA_DIR` | `/data` | Videos and posters in `DATA_DIR/videos`, temp work in `DATA_DIR/tmp`. |
| `PUBLIC_BASE_URL` | request's base URL | Base of `url` / `poster_url`. |
| `TTS_BACKEND` | `none` (`piper` in the image) | Voice when the request names none. |
| `PIPER_VOICE` | `/voices/en_US-lessac-medium.onnx` in the image | Piper model; its `.onnx.json` must sit next to it. |
| `MAX_SECONDS` | `90` | Longest video. |
| `FFMPEG_TIMEOUT` | `180` | Seconds before ffmpeg is killed. |
| `MAX_CONCURRENT_RENDERS` | `2` | More at once → `429`. |
| `RETENTION_DAYS` | `0` (keep) | Delete stored videos older than this, at startup and on each render. |
| `FONT_PATH`, `FONT_BOLD_PATH` | DejaVu (image) | Other TTF/OTF fonts. |

## Licences

`piper-tts` (piper1-gpl) is GPL-3.0 and the Debian `ffmpeg` / `imageio-ffmpeg` builds
include GPL components (libx264). That is fine for running the service yourself; check it
before you redistribute the image. Each piper voice has its own licence in its
`MODEL_CARD` on Hugging Face (`en_US-lessac-medium` is based on the Blizzard 2013 Lessac
data; read its card before commercial use).

## CI

`.github/workflows/ci.yml` runs the tests (the integration test renders a real video
with the bundled ffmpeg), then pushes `ghcr.io/<you>/<repo>:latest` on every push to `main`.
