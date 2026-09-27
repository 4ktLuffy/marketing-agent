# clip-finder

Deploy **73 of 81** of the local-LLM marketing agent. It turns one **long video** (a talk, a
webinar, a podcast recording) into a few **short vertical clips**, 1080×1920, with
word-by-word captions burned in, ready for Reels, TikTok or Shorts:

1. **Transcribe** locally with [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
   (MIT; model `small` by default, CPU int8, VAD filter, word timestamps).
2. **Candidate windows** are built in code: runs of whole sentences lasting
   `min_s`..`max_s` (20–60 s by default).
3. The **LLM scores** the windows through the gateway (03, prompt `clip_scoring` in 04):
   hook strength in the first 3 s, standalone sense, payoff, quotability, plus a title and
   the strongest line. The model only judges the transcript it is given; code checks its
   answer (below).
4. The best **non-overlapping** windows are picked and their in/out points **snapped** to
   scene cuts ([PySceneDetect](https://github.com/Breakthrough/PySceneDetect), BSD-3) or
   silences (ffmpeg `silencedetect`) within 1.5 s, so no clip starts or ends mid-word.
5. Each clip is **rendered**: cut, reframed to 9:16 (centre crop, blurred-background fit, or
   face-centred), captions as an ASS karaoke track burned in by ffmpeg/libass, H.264 + AAC,
   `+faststart`, plus a poster JPEG and an SRT file.

Fully local: no paid APIs. The only network calls are the gateway, and the download of the
source file when you give a link. It writes no posts: the workflow that uses it (77)
writes captions and sends clips to the calendar for approval.

**Rights.** You are responsible for having the rights to every video you give it. It
never downloads from video platforms: a YouTube, TikTok, Instagram, Vimeo, X, Facebook, …
link is refused with `422` "download the video yourself and upload it" (their terms forbid
downloading). Upload the file, or give a direct link to the media file (your own CDN,
storage bucket, podcast host).

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing` network,
with a volume on `/data`. n8n calls it at `http://clip-finder:8000` (env `CLIPS_URL`). The
clips are served from `PUBLIC_BASE_URL`, which must be reachable from the reviewer's
browser (the approval form links to them). It is CPU work: one job at a time.

## Run

```bash
docker build -t clip-finder .      # downloads faster-whisper-small (~460 MB) once, pinned revision
docker run --rm -p 8173:8000 -e INTERNAL_API_KEY=change-me -e GATEWAY_URL=http://host.docker.internal:8103 \
  -v clips-data:/data clip-finder
```

The image installs `ffmpeg` (with libass) and `fonts-dejavu-core` with apt and downloads
[Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) (MIT)
at build time, pinned to a commit, into `/models/faster-whisper-small`. It runs with
`HF_HUB_OFFLINE=1`: a running container never downloads a model.

Local without Docker (a Mac, for example):

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt     # imageio-ffmpeg brings an ffmpeg binary with libass
pytest -q
INTERNAL_API_KEY=change-me DATA_DIR=./data GATEWAY_URL=http://127.0.0.1:8103 \
  PUBLIC_BASE_URL=http://localhost:8173 uvicorn app.main:app --port 8173
```

`WHISPER_MODEL=small` (the default outside the image) downloads the model to the Hugging
Face cache on first use.

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status","ffmpeg":{"found","source"},"whisper":{"installed","model"},"scene_detection","busy","limits"}`; `503` without ffmpeg |
| POST | `/jobs` 🔑 | JSON `{"url", "max_clips"?, "min_s"?, "max_s"?, "language"?, "reframe"?}` **or** `multipart/form-data` with `file` and the same fields | `202` `{"id","status":"queued","status_url"}` |
| GET | `/jobs/{id}` 🔑 | — | the job (below) |
| GET | `/clips/{id}.mp4` | — | the clip, `video/mp4`; no key: the 32-hex id is the secret |
| GET | `/clips/{id}.jpg` | — | its poster |
| GET | `/clips/{id}.srt` | — | its captions as SRT |

Parameters: `max_clips` 1–`MAX_CLIPS` (default 5), `min_s` 5–120 (20), `max_s` 10–180 (60,
at least `min_s + 5`), `language` an ISO code like `en` (default: detected), `reframe`
`blur` (default: the whole frame on a blurred background, so slide text is never cut) | `center` | `face` (falls back to `blur` when no face model is installed).

```bash
curl -s localhost:8173/jobs -H 'X-API-Key: change-me' -F file=@talk.mp4 -F max_clips=3
curl -s localhost:8173/jobs -H 'X-API-Key: change-me' -H 'content-type: application/json' \
  -d '{"url": "https://cdn.example.com/webinars/2026-09.mp4", "max_clips": 5}'
# {"id":"5f0c…","status":"queued","status_url":"http://localhost:8173/jobs/5f0c…"}
curl -s localhost:8173/jobs/5f0c… -H 'X-API-Key: change-me'
```

Job:

```json
{
  "id": "5f0c…", "status": "queued|running|done|failed", "stage": "download|audio|transcribe|scenes|score|render|done",
  "progress": 0.0, "created_at": "…Z", "started_at": "…Z", "finished_at": "…Z",
  "source": {"kind": "url|upload", "name": "https://cdn.example.com/webinars/2026-09.mp4", "bytes": 1234,
             "duration_s": 191.3, "has_video": true, "width": 1920, "height": 1080},
  "params": {"max_clips": 5, "min_s": 20, "max_s": 60, "language": null, "reframe": "blur"},
  "transcript": {"words": 519, "language": "en", "language_probability": 0.99, "model": "small"},
  "candidates": 24, "scene_cuts": 6, "scoring": {"batches": 4, "failed_batches": 0, "scored": 24},
  "timings_ms": {"download": 0, "audio": 0, "silences": 0, "transcribe": 0, "scenes": 0, "score": 0, "render": 0, "total": 0},
  "notes": [], "error": null,
  "clips": [{"id": "…", "index": 1, "start_s": 12.4, "end_s": 51.9, "duration_s": 39.5,
             "title": "…", "hook": "…", "reason": "…", "score": 71.5,
             "scores": {"hook": 8, "standalone": 7, "payoff": 7, "quotable": 6},
             "transcript": "…", "snapped": {"start": "scene|silence|pad", "end": "…"},
             "reframe": "center|blur|face|audio_only", "width": 1080, "height": 1920, "size_bytes": 0,
             "url": "…/clips/….mp4", "poster_url": "…/clips/….jpg", "srt_url": "…/clips/….srt"}]
}
```

The source URL is stored without its query string (signed links carry tokens there).
Clips are in time order. `score` = 10 × (0.35 hook + 0.25 standalone + 0.25 payoff + 0.15
quotable), 0–100.

### How clips are chosen

- **Sentences** end at `.`, `?`, `!`, at a pause of 1.2 s, and a run-on stretch is cut at
  a comma after 15 s (hard cut at 25 s). A **window** is a run of whole sentences; for each
  starting sentence the shortest window reaching `min_s` and the longest within `max_s`
  are candidates. Starts are thinned to one per `WINDOW_STRIDE_S` (8 s) and at most
  `MAX_CANDIDATES` (48) are scored, spread evenly over the video.
- **Scoring** goes to the gateway in batches of `SCORE_BATCH` (6), each retried once. The
  code keeps the model honest: ids outside the batch are ignored, scores are clamped to
  0–10, a `hook` line that is not copied word for word from its window is replaced by the
  window's opening words, and a title containing a number the window does not say is
  replaced by the opening words too. A failed batch skips its windows (noted in `notes`);
  if nothing could be scored the job fails.
- **Picking:** highest score first, skipping anything that overlaps a clip already picked,
  up to `max_clips`, at least `MIN_SCORE`.
- **Snapping:** the start moves back to a scene cut within 1.5 s before the first word
  (never into the previous word), else into the silence before it, else 0.12 s lead-in;
  the end mirrors that after the last word. A clip never runs longer than `max_s + 1`.

### Rendering

- **Reframe:** `center` takes a 9:16 slice from the middle of a landscape frame; `blur`
  fits the whole frame to the width over a blurred, zoomed copy of itself (good for slides
  and screen recordings); `face` centres the slice on the median face position over 8
  stills, using MediaPipe's face detector **when a MediaPipe build with the bundled
  detector is installed** (it is not in `requirements.txt`: current MediaPipe wheels ship
  without it and would need a model download, which this service never does) — otherwise
  it falls back to `center` and says so in `notes`. Vertical sources are scaled and padded.
  Audio-only sources (m4a, mp3) get a plain dark background with the captions.
- **Captions:** lines of up to 4 words / 22 characters; each word turns amber as it is
  spoken (ASS `\k` karaoke), white text with a black outline, placed above the bottom
  ~350 px that platforms cover with their own buttons. The ASS file is passed to ffmpeg by
  a relative name from the job's temp dir, so no path or transcript text is ever part of
  the filter string; `{` `}` `\` are removed from caption text.
- **Output:** H.264 (CRF 21) + AAC 128k stereo, 30 fps, `+faststart`; poster at 1 s; SRT
  with the same lines.

### Limits and errors

`401`/`503` key errors as everywhere. `422`: a video-platform link, a non-http(s) URL, a
URL that resolves to a private address (SSRF guard, copied from 07; re-checked on every
redirect; `ALLOW_PRIVATE_URLS=true` lifts it), invalid parameters, both or neither of
`url`/`file`. `413`: upload over `MAX_UPLOAD_MB`. `429`: a job is already running (one at a
time; the check happens before an upload body is read). A job **fails** (status `failed`,
`error` says why) when the download fails (not `video/*`/`audio/*`, or
`application/octet-stream` without a media extension; larger than `MAX_UPLOAD_MB`), the
file has no sound track, runs over `MAX_SOURCE_MINUTES` or under `min_s`, has no speech,
no window fits, scoring fails entirely, or ffmpeg fails (killed after `FFMPEG_TIMEOUT`).
A job that was running when the service stopped is marked failed at startup; temp files
under `DATA_DIR/tmp` are removed after every job and at startup.

## Contract for the workflow

`77-wf-tool-clips` (chat tool `clip_video`) implements it:

1. `POST $CLIPS_URL/jobs` with the file or link and `X-API-Key: $INTERNAL_API_KEY`, then
   poll `GET $CLIPS_URL/jobs/{id}` (every 15–30 s) until `status` is `done` or `failed`.
   `429` → wait and retry; `failed` → report `error` to the person.
2. For each clip, draft the post text from the clip's `transcript` (e.g. prompt
   `social_posts` with `context` = transcript, so the post only states what was said) and
   run the usual quality gate (35).
3. Create one calendar item (19) per clip: `channel: "video"` (or the target platform),
   `title` = clip `title`, `body` = the drafted caption, `video_url` = clip `url`,
   `image_url` = clip `poster_url`, `status: "in_review"`, and a note
   `clip <index> of job <job id>, <start_s>-<end_s> s, score <score>`.
4. The approval form (38) already shows `video_url`; the publisher (39) and
   `54-postiz-bridge` send it on. 54 fetches clip and poster URLs under `CLIPS_PUBLIC_URL`
   from the internal `CLIPS_INTERNAL_URL` (`http://clip-finder:8000` in the stack), as it does
   for 71's videos (`VIDEO_PUBLIC_URL` → `VIDEO_URL`).

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for `/jobs`. Unset → `503`; wrong or missing key → `401`. |
| `DATA_DIR` | `/data` | `jobs/` (one JSON per job), `clips/`, `tmp/`. |
| `PUBLIC_BASE_URL` | request's base URL | Base of `url` / `poster_url` / `srt_url` / `status_url`. |
| `GATEWAY_URL` | `http://llm-gateway:8000` | Gateway running `clip_scoring`. |
| `GATEWAY_TIMEOUT` | `300` | Seconds per scoring call. |
| `WHISPER_MODEL` | `small` (`/models/faster-whisper-small` in the image) | Size name or local folder. |
| `WHISPER_COMPUTE`, `WHISPER_THREADS`, `WHISPER_BEAM` | `int8`, `0` (auto), `5` | faster-whisper settings. |
| `MAX_UPLOAD_MB` | `1024` | Upload / download cap. |
| `MAX_SOURCE_MINUTES` | `90` | Longest source. |
| `MAX_CLIPS` | `10` | Highest `max_clips` accepted. |
| `DOWNLOAD_TIMEOUT` | `60` | Seconds of network inactivity before a download fails. |
| `FFMPEG_TIMEOUT` | `600` | Seconds per ffmpeg call. |
| `WINDOW_STRIDE_S`, `MAX_CANDIDATES`, `SCORE_BATCH`, `MIN_SCORE` | `8`, `48`, `6`, `0` | Candidate spacing, how many are scored, per call, lowest score kept. |
| `RETENTION_DAYS` | `0` (keep) | Delete clips and job records older than this, at startup and per job. |
| `ALLOW_PRIVATE_URLS` | `false` | Allow links to private addresses (a NAS on your LAN). |
| `CAPTION_FONT_FILE`, `CAPTION_FONT_NAME` | DejaVu Sans Bold (image) | Another caption font. |

## Licences

Dependencies only, nothing copied: faster-whisper (MIT), the `small` model (MIT),
PySceneDetect (BSD-3), OpenCV (Apache-2.0), FastAPI/httpx/Pillow (permissive). Ideas (LLM
scoring of transcript windows, snapping to scene cuts, blurred-background fit) come from
openshorts and ClipsAI; no code from them. `ultralytics` (AGPL) is deliberately not used.
The Debian `ffmpeg` / `imageio-ffmpeg` builds include GPL components (libx264): fine for
running the service yourself; check before you redistribute the image. The SSRF guard in
`app/net.py` is copied from our own `07-page-extractor`.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`. `tests/test_integration.py` runs the real pipeline (generated video
+ macOS `say` speech, real Whisper, mocked scorer); it is skipped on Linux and when the
Whisper model is not already on the machine.
