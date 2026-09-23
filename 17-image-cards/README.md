# image-cards

Deploy **17 of 53** of the local-LLM marketing agent. It renders a clean title card as a PNG
(Open Graph, square post or story size, light or dark) so every blog post and social post can
have an image without a designer. The title is word-wrapped and the font shrinks until it fits.
It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n calls it at `http://image-cards:8000` (env `CARDS_URL`).
It is stateless, so it also runs fine on any container host (Render, Fly.io, Railway).

## Run

```bash
docker build -t image-cards .
docker run --rm -p 8117:8000 image-cards
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
| POST | `/card` | `{"title","subtitle"?,"brand"?,"size":"og","theme":"light"}` | `image/png` |

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

Anything else is a 422.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `FONT_PATH` | DejaVu Sans (installed in the image) | TTF/OTF for subtitle and brand |
| `FONT_BOLD_PATH` | DejaVu Sans Bold, else `FONT_PATH` | TTF/OTF for the title |

Outside Docker, if neither DejaVu nor `FONT_PATH` is present, Pillow's built-in font is used
(it works, but has no bold weight).

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
