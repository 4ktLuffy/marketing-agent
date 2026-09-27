"""One 1080x1920 frame per segment, drawn with Pillow (same wrap/fit approach as 17-image-cards).

Captions are burned in here, by drawing the spoken line on the frame, so ffmpeg never needs
its subtitle filters (and so no fontconfig).
"""
import os
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1080, 1920
MARGIN = 80
THEMES = {
    "light": {"bg": "#F8F7F4", "title": "#16181D", "muted": "#5B6070", "accent": "#4F46E5"},
    "dark": {"bg": "#14161C", "title": "#F5F6F8", "muted": "#A3A8B8", "accent": "#818CF8"},
}
# DejaVu ships in the Docker image; the macOS paths only help local runs on a Mac.
BOLD_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf")
REGULAR_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/System/Library/Fonts/Supplemental/Arial.ttf")

# Layout boxes (y ranges). Platforms put their own buttons and caption over the bottom ~350 px
# and the top ~150 px, so text stays out of those strips.
BAR_Y = 150
TEXT_BOX = (330, 1180)
CAPTION_BOX = (1250, 1570)


@dataclass
class Segment:
    kind: str            # hook | beat | cta
    on_screen: str       # big text
    spoken: str          # voiceover line, burned in as the caption
    shot: str | None = None


@lru_cache(maxsize=64)
def font(bold: bool, size: int):
    """FONT_BOLD_PATH/FONT_PATH override, then DejaVu (Docker) or Arial (macOS), then Pillow's font."""
    env = (os.getenv("FONT_BOLD_PATH") or os.getenv("FONT_PATH")) if bold else os.getenv("FONT_PATH")
    for path in (env, *(BOLD_FONTS if bold else REGULAR_FONTS)):
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
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def line_height(fnt) -> int:
    ascent, descent = fnt.getmetrics()
    return int((ascent + descent) * 1.15)


def clamp_lines(lines: list[str], max_lines: int, fnt, width: int) -> list[str]:
    if len(lines) <= max_lines:
        return lines
    last = lines[max_lines - 1]
    while last and fnt.getlength(last + "…") > width:
        last = last[:-1]
    return lines[: max_lines - 1] + [last.rstrip() + "…"]


def fit(text: str, width: int, height: int, max_size: int, min_size: int, bold: bool = True):
    """Largest font size at which the wrapped text fits the box; clamped with … at min_size."""
    for size in range(max_size, min_size - 1, -2):
        fnt = font(bold, size)
        lines = wrap(text, fnt, width)
        if len(lines) * line_height(fnt) <= height:
            return fnt, lines
    fnt = font(bold, min_size)
    return fnt, clamp_lines(wrap(text, fnt, width), max(1, height // line_height(fnt)), fnt, width)


def _hex(c: str) -> tuple[int, int, int]:
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def on_color(bg: str) -> str:
    """Black or white text, whichever reads better on bg."""
    r, g, b = _hex(bg)
    return "#111111" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#FFFFFF"


def _centered(draw, lines, fnt, y, fill):
    for line in lines:
        draw.text(((WIDTH - fnt.getlength(line)) / 2, y), line, font=fnt, fill=fill)
        y += line_height(fnt)
    return y


def render_frame(seg: Segment, index: int, total: int, beat_no: int | None, beat_count: int,
                 brand_name: str | None = None, logo_text: str | None = None,
                 accent: str | None = None, theme: str = "light", show_shot: bool = False) -> Image.Image:
    colors = dict(THEMES[theme])
    if accent:
        colors["accent"] = accent
    bg = colors["accent"] if seg.kind == "cta" else colors["bg"]
    title_color = on_color(bg) if seg.kind == "cta" else colors["title"]
    muted = title_color if seg.kind == "cta" else colors["muted"]
    img = Image.new("RGB", (WIDTH, HEIGHT), bg)
    draw = ImageDraw.Draw(img)
    box_w = WIDTH - 2 * MARGIN

    # Progress bar across the top: how far into the video this segment is.
    draw.rectangle((0, 0, WIDTH, 12), fill=colors["muted"] if seg.kind != "cta" else bg)
    fill_to = int(WIDTH * (index + 1) / total)
    draw.rectangle((0, 0, fill_to, 12), fill=colors["accent"] if seg.kind != "cta" else title_color)

    # Brand bar: logo square + name on the left, beat counter on the right.
    x = MARGIN
    if logo_text:
        lf = font(True, 40)
        side = 88
        sq_fill = colors["accent"] if seg.kind != "cta" else title_color
        draw.rounded_rectangle((x, BAR_Y, x + side, BAR_Y + side), radius=18, fill=sq_fill)
        lt = logo_text.strip()[:3]
        tw = lf.getlength(lt)
        asc, desc = lf.getmetrics()
        draw.text((x + (side - tw) / 2, BAR_Y + (side - asc - desc) / 2), lt, font=lf, fill=on_color(sq_fill))
        x += side + 24
    if brand_name:
        bf = font(True, 42)
        name = clamp_lines(wrap(brand_name.strip(), bf, WIDTH - x - MARGIN - 200), 1, bf, WIDTH - x - MARGIN - 200)
        asc, desc = bf.getmetrics()
        draw.text((x, BAR_Y + (88 - asc - desc) / 2), name[0] if name else "", font=bf, fill=muted)
    if beat_no is not None:
        cf = font(True, 40)
        counter = f"{beat_no} / {beat_count}"
        asc, desc = cf.getmetrics()
        draw.text((WIDTH - MARGIN - cf.getlength(counter), BAR_Y + (88 - asc - desc) / 2), counter,
                  font=cf, fill=muted)

    # Big on-screen text, centred in the text box.
    top, bottom = TEXT_BOX
    shot_lines, shot_font = [], None
    if show_shot and seg.shot:
        shot_font = font(False, 34)
        shot_lines = clamp_lines(wrap(f"Shot: {seg.shot}", shot_font, box_w), 3, shot_font, box_w)
    shot_h = len(shot_lines) * line_height(shot_font) + 40 if shot_lines else 0
    big_font, big_lines = fit(seg.on_screen, box_w, bottom - top - shot_h - 40,
                              max_size=132 if seg.kind != "beat" else 116, min_size=48)
    text_h = len(big_lines) * line_height(big_font)
    y = top + max(0, (bottom - top - text_h - shot_h - 40) // 2)
    y = _centered(draw, big_lines, big_font, y, title_color)
    # Accent underline under the big text.
    ul = colors["accent"] if seg.kind != "cta" else title_color
    draw.rounded_rectangle(((WIDTH - 160) / 2, y + 16, (WIDTH + 160) / 2, y + 28), radius=6, fill=ul)
    if shot_lines:
        _centered(draw, shot_lines, shot_font, y + 60, muted)

    # Caption block: the spoken line, white on a dark rounded box (readable in both themes).
    if seg.spoken and seg.kind != "cta":
        pad = 36
        c_top, c_bottom = CAPTION_BOX
        cap_font, cap_lines = fit(seg.spoken, box_w - 2 * pad, c_bottom - c_top - 2 * pad,
                                  max_size=54, min_size=32, bold=True)
        cap_h = len(cap_lines) * line_height(cap_font) + 2 * pad
        overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        box_top = c_bottom - cap_h
        od.rounded_rectangle((MARGIN, box_top, WIDTH - MARGIN, c_bottom), radius=28, fill=(10, 10, 14, 200))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)
        _centered(draw, cap_lines, cap_font, box_top + pad, "#FFFFFF")
    return img
