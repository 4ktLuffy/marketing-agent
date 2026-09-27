"""Image cards: branded social/OG images rendered with Pillow."""
import hmac
import io
import os
import re
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field, field_validator

app = FastAPI(title="image-cards")

SIZES = {"og": (1200, 630), "square": (1080, 1080), "story": (1080, 1920)}
THEMES = {
    "light": {"bg": "#F8F7F4", "title": "#16181D", "muted": "#5B6070", "accent": "#4F46E5"},
    "dark": {"bg": "#14161C", "title": "#F5F6F8", "muted": "#A3A8B8", "accent": "#818CF8"},
}
DEJAVU_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
# Channel -> card size for POST /cards when no size is given. Feeds are square on the
# Meta networks, link previews are OG-shaped on LinkedIn and X, vertical video is story.
CHANNEL_SIZES = {
    "instagram": "square", "threads": "square", "facebook": "square",
    "linkedin": "og", "x": "og", "twitter": "og",
    "story": "story", "reels": "story", "tiktok": "story", "shorts": "story", "video": "story",
}
# Stored card ids: 32 lower-case hex characters (128 random bits). Nothing else is ever
# turned into a file path.
CARD_ID = re.compile(r"[0-9a-f]{32}")


class CardRequest(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    subtitle: str | None = Field(None, max_length=200)
    brand: str | None = Field(None, max_length=40)
    size: Literal["og", "square", "story"] = "og"
    theme: Literal["light", "dark"] = "light"
    accent: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")

    @field_validator("title")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank")
        return " ".join(v.split())


@lru_cache(maxsize=64)
def font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    """FONT_BOLD_PATH/FONT_PATH override, then DejaVu (Docker), then Pillow's built-in font."""
    env = (os.getenv("FONT_BOLD_PATH") or os.getenv("FONT_PATH")) if bold else os.getenv("FONT_PATH")
    for path in (env, DEJAVU_BOLD if bold else DEJAVU):
        if path and os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def wrap(text: str, fnt, width: int) -> list[str]:
    """Greedy word wrap; words wider than the box are split by characters."""
    lines, line = [], ""
    for word in text.split():
        while fnt.getlength(word) > width:  # hard-break an over-long word
            cut = 1
            while cut < len(word) and fnt.getlength(word[: cut + 1]) <= width:
                cut += 1
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:cut])
            word = word[cut:]
        candidate = f"{line} {word}".strip()
        if fnt.getlength(candidate) <= width:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def line_height(fnt) -> int:
    ascent, descent = fnt.getmetrics()
    return int((ascent + descent) * 1.12)


def clamp_lines(lines: list[str], max_lines: int, fnt, width: int) -> list[str]:
    if len(lines) <= max_lines:
        return lines
    last = lines[max_lines - 1]
    while last and fnt.getlength(last + "…") > width:
        last = last[:-1]
    return lines[: max_lines - 1] + [last.rstrip() + "…"]


def fit_title(title: str, width: int, height: int, max_size: int, min_size: int):
    """Largest font size at which the wrapped title fits the box."""
    for size in range(max_size, min_size - 1, -2):
        fnt = font(True, size)
        lines = wrap(title, fnt, width)
        if len(lines) * line_height(fnt) <= height:
            return fnt, lines
    fnt = font(True, min_size)
    return fnt, clamp_lines(wrap(title, fnt, width), max(1, height // line_height(fnt)), fnt, width)


def render(req: CardRequest) -> bytes:
    w, h = SIZES[req.size]
    colors = dict(THEMES[req.theme])
    if req.accent:
        colors["accent"] = req.accent
    img = Image.new("RGB", (w, h), colors["bg"])
    draw = ImageDraw.Draw(img)

    margin = int(w * 0.08)
    box_w = w - 2 * margin
    top, bottom = margin, h - margin

    # Footer: brand name bottom-left.
    if req.brand:
        brand_font = font(True, int(w * 0.028))
        bottom -= line_height(brand_font)
        draw.text((margin, bottom), req.brand.strip(), font=brand_font, fill=colors["muted"])
        bottom -= int(w * 0.03)

    # Subtle accent bar above the title.
    bar_h = max(6, w // 150)
    draw.rectangle((margin, top, margin + int(w * 0.07), top + bar_h), fill=colors["accent"])
    top += bar_h + int(w * 0.04)

    sub_lines, sub_font = [], None
    if req.subtitle and req.subtitle.strip():
        sub_font = font(False, int(w * 0.034))
        sub_lines = clamp_lines(wrap(req.subtitle.strip(), sub_font, box_w), 4, sub_font, box_w)
    sub_h = len(sub_lines) * line_height(sub_font) if sub_lines else 0
    gap = int(w * 0.03) if sub_lines else 0

    title_font, title_lines = fit_title(
        req.title, box_w, bottom - top - sub_h - gap, max_size=int(w * 0.085), min_size=int(w * 0.025)
    )
    title_h = len(title_lines) * line_height(title_font)

    # Centre the title+subtitle block vertically in the remaining space.
    y = top + max(0, (bottom - top - title_h - gap - sub_h) // 2)
    for line in title_lines:
        draw.text((margin, y), line, font=title_font, fill=colors["title"])
        y += line_height(title_font)
    y += gap
    for line in sub_lines:
        draw.text((margin, y), line, font=sub_font, fill=colors["muted"])
        y += line_height(sub_font)

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/card", response_class=Response, responses={200: {"content": {"image/png": {}}}})
def card(req: CardRequest):
    return Response(render(req), media_type="image/png")


# ---------- stored cards (POST /cards, GET /cards/{id}.png)


class StoredCardRequest(CardRequest):
    channel: str | None = Field(None, max_length=40)


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data/cards"))


def public_base(request: Request) -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)).strip().rstrip("/")


@app.post("/cards", status_code=201, dependencies=[Depends(require_key)])
def create_card(req: StoredCardRequest, request: Request):
    """Render a card, keep it under DATA_DIR and return a URL anyone with the link can load."""
    if "size" not in req.model_fields_set and req.channel:
        req.size = CHANNEL_SIZES.get(req.channel.strip().lower(), "og")
    png = render(req)
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    card_id = secrets.token_hex(16)
    tmp = folder / f".{card_id}.tmp"
    tmp.write_bytes(png)
    os.replace(tmp, folder / f"{card_id}.png")  # never serve a half-written file
    w, h = SIZES[req.size]
    return {"id": card_id, "url": f"{public_base(request)}/cards/{card_id}.png",
            "width": w, "height": h, "size": req.size}


@app.get("/cards/{card_id}.png", response_class=FileResponse,
         responses={200: {"content": {"image/png": {}}}})
def get_card(card_id: str):
    if not CARD_ID.fullmatch(card_id):
        raise HTTPException(404, "card not found")
    path = data_dir() / f"{card_id}.png"
    if not path.is_file():
        raise HTTPException(404, "card not found")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})
