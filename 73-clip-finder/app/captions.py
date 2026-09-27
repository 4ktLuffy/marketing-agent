"""Captions for one clip: word-by-word karaoke (.ass, burned in by ffmpeg's libass) and .srt.

Words are grouped into short lines (a few words, as on Reels/TikTok); each line is shown
while it is spoken and each word turns to the highlight colour as it is said (ASS \\k tags).
Times are relative to the clip start.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from .windows import SENTENCE_END, Word

WIDTH, HEIGHT = 1080, 1920
MAX_WORDS = 4
MAX_CHARS = 22
GAP_BREAK_S = 0.6
HOLD_S = 0.5          # keep a line up this long after its last word if nothing follows
# Platforms draw their own buttons and caption over the bottom ~350 px, so captions sit above.
MARGIN_V = 520
FONT_SIZE = 76

# DejaVu ships in the Docker image; the macOS paths only help local runs on a Mac.
FONTS = (
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVu Sans"),
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "Arial"),
)


@dataclass
class Line:
    start: float
    end: float
    words: list[Word]


def caption_font() -> tuple[str | None, str]:
    """(fonts dir for libass, family name). CAPTION_FONT_FILE + CAPTION_FONT_NAME override."""
    path, name = os.getenv("CAPTION_FONT_FILE"), os.getenv("CAPTION_FONT_NAME")
    if path and name and os.path.exists(path):
        return str(Path(path).parent), name
    for p, family in FONTS:
        if os.path.exists(p):
            return str(Path(p).parent), family
    return None, "Sans"


def clean(text: str) -> str:
    """Text safe inside an ASS event: no override blocks, no escapes, one line."""
    text = " ".join(text.split())
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def lines(words: list[Word], clip_start: float, clip_end: float) -> list[Line]:
    """Words inside [clip_start, clip_end], grouped into caption lines, clip-relative times."""
    inside = [Word(clean(w.text), max(0.0, w.start - clip_start), min(clip_end, w.end) - clip_start)
              for w in words if w.end > clip_start and w.start < clip_end and clean(w.text)]
    out: list[Line] = []
    cur: list[Word] = []
    for i, w in enumerate(inside):
        if cur:
            chars = len(" ".join(x.text for x in cur + [w]))
            if (len(cur) >= MAX_WORDS or chars > MAX_CHARS or w.start - cur[-1].end >= GAP_BREAK_S
                    or cur[-1].text.endswith(SENTENCE_END + (",",))):
                out.append(Line(cur[0].start, cur[-1].end, cur))
                cur = []
        cur.append(w)
    if cur:
        out.append(Line(cur[0].start, cur[-1].end, cur))
    clip_len = clip_end - clip_start
    for i, ln in enumerate(out):  # hold each line until the next starts (or HOLD_S)
        nxt = out[i + 1].start if i + 1 < len(out) else clip_len
        ln.end = max(ln.end, min(nxt, ln.end + HOLD_S))
    return out


def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ass(caption_lines: list[Line], font_name: str, highlight: str = "&H0000D7FF") -> str:
    """ASS script. Colours are &HAABBGGRR: highlight (spoken) defaults to amber, upcoming
    words white, black outline and a soft shadow for any background."""
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {WIDTH}\nPlayResY: {HEIGHT}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Karaoke,{font_name},{FONT_SIZE},{highlight},&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,6,2,2,80,80,{MARGIN_V},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for ln in caption_lines:
        parts, t = [], ln.start
        for w in ln.words:
            # \k counts from the line start: a gap before a word is folded into its duration.
            k = max(1, int(round((w.end - t) * 100)))
            parts.append(f"{{\\k{k}}}{w.text}")
            t = w.end
        events.append(f"Dialogue: 0,{_ass_time(ln.start)},{_ass_time(ln.end)},Karaoke,,0,0,0,,"
                      + " ".join(parts))
    return head + "\n".join(events) + "\n"


def srt(caption_lines: list[Line]) -> str:
    blocks = []
    for n, ln in enumerate(caption_lines, 1):
        text = " ".join(w.text for w in ln.words)
        blocks.append(f"{n}\n{_srt_time(ln.start)} --> {_srt_time(ln.end)}\n{text}\n")
    return "\n".join(blocks)
