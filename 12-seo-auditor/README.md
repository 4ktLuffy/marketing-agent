# seo-auditor

Deploy **12 of 53** of the local-LLM marketing agent. It runs deterministic on-page SEO checks
on a URL or raw HTML (title and description length, headings, alt text, canonical, Open Graph,
indexability, word count, and keyword placement) and returns each as pass / warn / fail with
a 0–100 score. The SEO brief tool feeds the failed checks to the LLM. It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n calls it at `http://seo-auditor:8000` (env `SEO_URL`).
It is stateless and needs outbound internet, so it also runs fine on any container host
(Render, Fly.io, Railway).

## Run

```bash
docker build -t seo-auditor .
docker run --rm -p 8112:8000 seo-auditor
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --port 8112
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/audit` | `{"url"}` or `{"html"}` (exactly one), `"keyword"?` | `{"url","score","checks":[{"id","status","message"}]}` |

```bash
curl -s localhost:8112/audit -H 'content-type: application/json' \
  -d '{"url":"https://example.com","keyword":"example"}'
# {"url":"https://example.com","score":77,"checks":[{"id":"title","status":"warn","message":"Title is 14 characters; aim for 30-60"}, ...]}
```

### Checks

| id | pass | warn | fail |
|---|---|---|---|
| `title` | 30–60 chars | other length | missing |
| `meta_description` | 70–160 chars | other length | missing |
| `h1` | exactly one | — | none, or more than one |
| `heading_order` | no skipped levels | a level is skipped (h2 → h4) | — |
| `img_alt` | ≥ 90% of images have `alt` (or no images) | 50–90% | < 50% |
| `canonical` | `<link rel="canonical">` present | missing | — |
| `open_graph` | `og:title` and `og:image` present | either missing | — |
| `lang` | `<html lang>` set | missing | — |
| `viewport` | viewport meta present | — | missing |
| `indexable` | no `noindex` | — | `noindex` in robots meta or `X-Robots-Tag` |
| `word_count` | main text ≥ 300 words | fewer | — |
| `keyword_in_title` · `keyword_in_h1` · `keyword_in_intro` (first 100 words) · `keyword_in_description` | keyword found (case-insensitive) | not found | — |

Keyword checks appear only when `keyword` is given. `alt=""` counts as covered (it is correct
for decorative images). `score = round(100 * (pass + 0.5 * warn) / total)`.

Errors: `422` if both or neither of `url`/`html` are given, or the URL is blocked (see below).
`502` if the page cannot be fetched; `detail` says why.

**SSRF guard.** Only `http`/`https`. The host is resolved and refused if any address is
loopback, private, link-local, reserved or multicast. Every redirect hop is checked again.
Fetching: 15 s timeout, up to 5 redirects, 5 MB max.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `ALLOW_PRIVATE_URLS` | `false` | `true` allows private and loopback addresses. Only for trusted local testing. |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
