# page-extractor

Deploy **07 of 71** of the local-LLM marketing agent. It turns a web page (by URL or raw HTML)
into clean fields the LLM can work with: title, meta description, headings, main text, link
counts and Open Graph tags. Navigation, headers, footers, sidebars, forms and scripts are
dropped, so a 7B model reads the content and not the chrome. It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n calls it at `http://page-extractor:8000` (env `EXTRACTOR_URL`).
It is stateless and needs outbound internet, so it also runs fine on any container host
(Render, Fly.io, Railway).

## Run

```bash
docker build -t page-extractor .
docker run --rm -p 8107:8000 page-extractor
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --port 8107
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/extract` | `{"url"}` or `{"html"}` (exactly one) | `{"url","title","description","lang","headings":[{"level","text"}],"text","word_count","links":{"internal","external"},"og":{}}` |

```bash
curl -s localhost:8107/extract -H 'content-type: application/json' \
  -d '{"url":"https://example.com"}'
# {"url":"https://example.com","title":"Example Domain","description":null,"lang":"en","headings":[{"level":1,"text":"Example Domain"}],"text":"Example Domain This domain is for use in documentation examples ...","word_count":19,"links":{"internal":0,"external":1},"og":{}}
```

- `url` in the response is the final URL after redirects, or `null` for `html` input.
- `headings` are h1–h3 in page order. `text` is the main content (`<article>`, else `<main>`,
  else `<body>`), whitespace collapsed, capped at 20,000 characters; `word_count` counts the
  whole main text, before the cap.
- `og` holds every `og:*` meta tag with the prefix removed: `{"title": ..., "image": ...}`.
- Links to the same host (ignoring `www.`) are internal. For `html` input there is no host,
  so relative links are internal and absolute ones external.
- Fetching: 15 s timeout, up to 5 redirects, 5 MB max, User-Agent
  `marketing-agent/1.0 (+page-extractor)`.

Errors (pages): `422` if both or neither of `url`/`html` are given, or the URL is blocked (see below).
`502` if the page cannot be fetched (timeout, HTTP 4xx/5xx, not HTML, over 5 MB); `detail`
says why.

**SSRF guard.** Only `http`/`https`. The host is resolved and refused if any address is
loopback, private, link-local, reserved or multicast. Every redirect hop is checked again.

## YouTube videos

A YouTube video URL returns the video's **transcript** instead of the page HTML, so the
repurpose tool (30) and the research tool (31) turn a video into posts with no workflow change:
they send `{"url"}` as before.

Recognised forms (`http`/`https`, default port): `youtube.com/watch?v=ID`, `m.youtube.com/watch?v=ID`,
`youtu.be/ID`, `youtube.com/shorts/ID`, `youtube.com/embed/ID`, `youtube.com/live/ID`. The video id
must be exactly 11 characters of `A-Z a-z 0-9 _ -`; a malformed one is a `422`. Other YouTube pages
(channels, playlists, search) and look-alike hosts are extracted as normal pages.

```bash
curl -s localhost:8107/extract -H 'content-type: application/json' \
  -d '{"url":"https://youtu.be/<11-char id>"}'
# {"url":"https://www.youtube.com/watch?v=<id>","title":"<video title>","description":"YouTube video by <channel>",
#  "lang":"en","headings":[],"text":"<paragraph>\n\n<paragraph>...","word_count":1234,
#  "links":{"internal":0,"external":0},"og":{},
#  "source":"youtube_transcript","language":"en","duration_s":612,"video_id":"<id>"}
```

- `text`: caption lines joined into paragraphs (blank line between them). A paragraph closes at a
  sentence end once it spans 20 s, and always at 30 s (auto-generated captions have no
  punctuation). `[Music]`-style tags are dropped. Capped at 20,000 characters like pages;
  `word_count` is before the cap.
- Language: the first of `TRANSCRIPT_LANGS` that exists (a manual track before an auto-generated
  one); if none match, the first available track. `language` (and `lang`) is the track's code.
- `title` and `description` (channel) come from YouTube's oEmbed endpoint, fetched through the same
  SSRF-guarded client as pages. If oEmbed fails they are `null`; the transcript is still returned.
- `duration_s` is the end of the last caption, so it is approximate (can be `null`).
- Transcripts come from [`youtube-transcript-api`](https://pypi.org/project/youtube-transcript-api/)
  (unofficial: it reads the same caption data as the YouTube player; no API key). Its HTTP calls
  go only to youtube.com with the validated id, 15 s timeout, no proxies from the environment.

Errors: `422` `{"detail":"no transcript available for this video (captions off)"}` when the video
has no captions; `422` when it is private, removed or the id does not exist; `502` when YouTube
cannot be reached or blocks the server. YouTube often blocks cloud-provider IP ranges
(AWS, GCP, Azure...), so on a cloud host expect `502 ... blocked ...`: run the extractor on a
home/office connection, or paste the transcript as `text` into the repurpose tool instead.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `ALLOW_PRIVATE_URLS` | `false` | `true` allows private and loopback addresses. Only for trusted local testing. |
| `TRANSCRIPT_LANGS` | `en` | Comma-separated caption languages in order of preference, e.g. `de,en`. Any available track is used if none match. |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
