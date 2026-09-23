# page-extractor

Deploy **07 of 53** of the local-LLM marketing agent. It turns a web page (by URL or raw HTML)
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

Errors: `422` if both or neither of `url`/`html` are given, or the URL is blocked (see below).
`502` if the page cannot be fetched (timeout, HTTP 4xx/5xx, not HTML, over 5 MB); `detail`
says why.

**SSRF guard.** Only `http`/`https`. The host is resolved and refused if any address is
loopback, private, link-local, reserved or multicast. Every redirect hop is checked again.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `ALLOW_PRIVATE_URLS` | `false` | `true` allows private and loopback addresses. Only for trusted local testing. |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
