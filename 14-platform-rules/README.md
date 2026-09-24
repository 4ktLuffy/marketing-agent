# platform-rules

Deploy **14 of 60** of the local-LLM marketing agent. It knows the character limits and
hashtag caps of each channel and tells you, before anything is scheduled, whether a draft
fits. Small local models are bad at counting, so the social writer, ad copy and repurpose
tools check every draft here instead of trusting the LLM.
It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n calls it at `http://platform-rules:8000` (env `RULES_URL`).
It is stateless, so it also runs fine on any container host (Render, Fly.io, Railway).

## Run

```bash
docker build -t platform-rules .
docker run --rm -p 8114:8000 platform-rules
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --port 8114
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| GET | `/rules` | — | `{"length_unit","channels":{name:{limit,...}}}` |
| POST | `/validate` | `{"channel","text"}` | `{"ok","channel","length","limit","hashtags","violations":[{"rule","detail","severity"}]}` |

```bash
curl -s localhost:8114/validate -H 'content-type: application/json' \
  -d '{"channel":"google_ads_headline","text":"Fresh Specialty Coffee, Delivered Weekly"}'
# {"ok":false,"channel":"google_ads_headline","length":40,"limit":30,"hashtags":0,
#  "violations":[{"rule":"too_long","detail":"40 chars, limit 30 (over by 10)","severity":"error"}]}
```

An unknown channel returns 422 with the list of valid channels. Channel names are
case-insensitive.

| Channel | Limit | Extra |
|---|---|---|
| `x` | 280 | each `http(s)://` URL counts as 23 |
| `linkedin` | 3000 | |
| `instagram` | 2200 | max 30 hashtags |
| `facebook` | 63206 | |
| `threads` | 500 | |
| `mastodon` | 500 | each URL counts as 23 (default instance limit) |
| `email_subject` | 78 | warn above 60 |
| `google_ads_headline` | 30 | |
| `google_ads_description` | 90 | |
| `meta_description` | 160 | warn below 70 |

Violations: `too_long`, `too_many_hashtags` and `empty` are `"severity":"error"` and make
`ok` false; `above_recommended` and `below_recommended` are `"severity":"warn"` and leave
`ok` true. (`severity` is an extra field beyond the blueprint contract, same shape as
the brand service.)

Length is counted in Unicode code points. An emoji is 1, a flag or ZWJ emoji is several;
X's own counter weights emoji and CJK characters as 2, so a post near 280 with many emoji
may still be rejected by X. Bare domains without `http(s)://` are counted at their real
length. Hashtags are `#word` not preceded by a letter, digit, `#` or `&`.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
