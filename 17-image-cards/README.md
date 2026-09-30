# image-cards

Deploy **17 of 89** of the local-LLM marketing agent. It renders a clean title card as a PNG
(Open Graph, square post or story size, light or dark) so every blog post and social post can
have an image without a designer. The title is word-wrapped and the font shrinks until it fits.
It uses no LLM.

`POST /cards` also keeps the PNG and returns a URL for it: the social writer (26) and the
campaign drafter (48) attach one to every post for an image-friendly channel, the approval
form (38) shows it, and the publisher (39) passes it on so `54-postiz-bridge` can upload it.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n calls it at `http://image-cards:8000` (env `CARDS_URL`).
`POST /card` is stateless. `POST /cards` stores PNGs under `DATA_DIR` (the stack mounts the
`cards_data` volume on `/data`).

## Run

```bash
docker build -t image-cards .
docker run --rm -p 8117:8000 -e INTERNAL_API_KEY=change-me -e PUBLIC_BASE_URL=http://localhost:8117 -v cards-data:/data image-cards
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --port 8117
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/card` | `{"title","subtitle"?,"brand"?,"size":"og","theme":"light","accent"?}` | `image/png` |
| POST | `/cards` 🔑 | same as `/card`, plus `"channel"?` | `201 {"id","url","width","height","size"}` |
| GET | `/cards/{id}.png` | — | the stored `image/png` (no key: the id is 128 random bits) |

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY` (`503` if the service has none set).

```bash
curl -s localhost:8117/card -H 'content-type: application/json' -o card.png \
  -d '{"title":"Spring sale: 20% off every subscription","subtitle":"This week only","brand":"Acme Coffee","size":"og","theme":"dark"}'
```

| Field | Rule |
|---|---|
| `title` | 1–140 characters, required |
| `subtitle` | up to 200 characters, at most 4 lines (then `…`) |
| `brand` | up to 40 characters, drawn bottom-left |
| `size` | `og` 1200×630 · `square` 1080×1080 · `story` 1080×1920 (default `og`) |
| `theme` | `light` or `dark` (default `light`) |
| `accent` | optional `#RRGGBB` for the bar above the title (e.g. your brand colour) |
| `channel` | `/cards` only. When `size` is not given it picks one: `instagram`, `threads`, `facebook` → `square`; `linkedin`, `x` → `og`; `story`, `reels`, `tiktok`, `shorts`, `video` → `story`; anything else → `og` |

Anything else is a 422.

```bash
curl -s localhost:8117/cards -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"title":"Spring sale: 20% off","brand":"Acme Coffee","channel":"instagram"}'
# {"id":"3f9c...","url":"http://localhost:8117/cards/3f9c....png","width":1080,"height":1080,"size":"square"}
```

The `url` must load in the reviewer's browser (approval form 38) and, for publishing, from
wherever the image is fetched. It is built from `PUBLIC_BASE_URL`, or from the request's own
host when that is unset. `GET /cards/{id}.png` accepts only 32 lower-case hex characters as
the id, so no other file can be reached. Stored cards are never deleted by the service; they
are small (tens of KB) but clean `DATA_DIR` yourself if it grows.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `FONT_PATH` | DejaVu Sans (installed in the image) | TTF/OTF for subtitle and brand |
| `FONT_BOLD_PATH` | DejaVu Sans Bold, else `FONT_PATH` | TTF/OTF for the title |
| `INTERNAL_API_KEY` | — | Required for `POST /cards` |
| `DATA_DIR` | `/data/cards` | Where `POST /cards` keeps PNGs |
| `PUBLIC_BASE_URL` | the request's host | Base of the returned `url`, e.g. `http://localhost:8117` or `https://cards.example.com`. The stack sets it from `CARDS_PUBLIC_URL`. |

Outside Docker, if neither DejaVu nor `FONT_PATH` is present, Pillow's built-in font is used
(it works, but has no bold weight).

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
