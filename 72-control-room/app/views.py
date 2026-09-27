"""Turn service data into what the templates show. Pure functions (tested directly)."""
import re
from urllib.parse import urlsplit

STATUS_COLORS = {
    "idea": "#8a8f98", "draft": "#5b6b82", "in_review": "#c98a00", "approved": "#1f8a4c",
    "published": "#2f6fd6", "rejected": "#c0392b",
}
# Where each network cuts the text behind "…see more" in the feed (approximate, mobile).
FOLD = {"linkedin": 210, "instagram": 125, "facebook": 480, "threads": 500}
EDITABLE_TIME = {"idea", "draft", "in_review", "approved"}

_CARD = re.compile(r"/cards/([0-9a-f]{16,64})\.png$")
_VIDEO = re.compile(r"/videos/([0-9a-f]{16,64})\.(mp4|jpg)$")
_CLIP = re.compile(r"/clips/([0-9a-f]{32})\.(mp4|jpg)$")
_TRANSITION = re.compile(r"^\[(\d{4}-\d\d-\d\dT[\d:]+Z)\]\s+(\w+)\s*->\s*(\w+)(?::\s*(.*))?$")
_STAMP = re.compile(r"^\[(\d{4}-\d\d-\d\dT[\d:]+Z)\]\s*(.*)$")
_PILLAR = re.compile(r"engine pillar #(\d+)")

_FLAG_RULES = [
    (re.compile(r"^(?:quality gate|needs a human)\s*:\s*", re.I), "danger"),
    (re.compile(r"^drafted(?: by the engine)?; needs a human\.?\s*(?:quality gate:\s*)?", re.I), "danger"),
    (re.compile(r"^unsupported claim\s*:\s*", re.I), "danger"),
    (re.compile(r"^warnings?\s*:\s*", re.I), "warn"),
    (re.compile(r"^auto-fixed\s*:\s*", re.I), "info"),
]
_FLAG_WORDS = [
    (re.compile(r"video (?:not rendered|could not be re-rendered)", re.I), "danger"),
    (re.compile(r"novelty NOT checked|near-duplicate|without embeddings", re.I), "warn"),
]


def http_url(u) -> str | None:
    return u if isinstance(u, str) and re.match(r"^https?://\S+$", u) and len(u) <= 2000 else None


def media_src(url) -> str | None:
    """Image cards (17) and videos (71) are served through the control room (/media/...), so a
    phone on HTTPS never needs their ports; any other http(s) URL is used as is."""
    u = http_url(url)
    if not u:
        return None
    path = urlsplit(u).path
    if m := _CARD.search(path):
        return f"/media/cards/{m.group(1)}.png"
    if m := _VIDEO.search(path):
        return f"/media/videos/{m.group(1)}.{m.group(2)}"
    if m := _CLIP.search(path):
        return f"/media/clips/{m.group(1)}.{m.group(2)}"
    return u


def poster_for(item: dict) -> str | None:
    img = media_src(item.get("image_url"))
    if img:
        return img
    v = http_url(item.get("video_url"))
    if v and _VIDEO.search(urlsplit(v).path):
        return media_src(v[:-4] + ".jpg")
    return None


def video_seconds(item: dict) -> int | None:
    v = item.get("video_url")
    if not v:
        return None
    m = re.search(re.escape(f"video preview: {v} (") + r"([\d.]+) s\)", str(item.get("notes") or ""))
    return round(float(m.group(1))) if m else None


def _segments(notes: str):
    for line in str(notes or "").replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = _TRANSITION.match(line)
        if m:
            line = m.group(4) or ""
        else:
            m2 = _STAMP.match(line)
            line = m2.group(2) if m2 else line
        for part in line.split(" | "):
            yield part.strip()


def flags(item: dict) -> list[dict]:
    """Problems the quality gate, claim checker, novelty check or video render wrote into notes.
    [{level: danger|warn|info, text}], deduplicated, at most 8."""
    out, seen = [], set()

    def add(level, text):
        text = re.sub(r"\s+", " ", text).strip(" ;.")
        if text and text.lower() not in seen and len(out) < 8:
            seen.add(text.lower())
            out.append({"level": level, "text": text[:300]})

    for seg in _segments(item.get("notes")):
        for rule, level in _FLAG_RULES:
            if rule.match(seg):
                rest = rule.sub("", seg, count=1)
                for p in re.split(r";\s+", rest):
                    add(level, p)
                break
        else:
            for rule, level in _FLAG_WORDS:
                if rule.search(seg):
                    add(level, seg)
                    break
    return out


def timeline(item: dict) -> list[dict]:
    """notes -> [{at, change, text}] in order (19 appends one line per status change)."""
    out = []
    for line in str(item.get("notes") or "").replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = _TRANSITION.match(line)
        if m:
            out.append({"at": m.group(1), "change": f"{m.group(2)} → {m.group(3)}", "text": m.group(4) or ""})
            continue
        m2 = _STAMP.match(line)
        out.append({"at": m2.group(1) if m2 else None, "change": None, "text": m2.group(2) if m2 else line})
    return out


def pillar_id(item: dict) -> int | None:
    m = _PILLAR.search(str(item.get("notes") or ""))
    return int(m.group(1)) if m else None


def fold_at(channel: str | None) -> int | None:
    return FOLD.get(str(channel or "").lower())


def card(item: dict, pillars: dict | None = None) -> dict:
    """Everything a queue card or the detail page shows about one calendar item."""
    ch = str(item.get("channel") or "").lower()
    pid = pillar_id(item)
    video = media_src(item.get("video_url")) if http_url(item.get("video_url")) else None
    body = str(item.get("body") or "")
    fold = fold_at(ch)
    if fold and len(body) > fold:   # the feed cuts at a word boundary
        space = body.rfind(" ", 0, fold + 1)
        fold = space if space > fold * 0.7 else fold
    return {
        "item": item,
        "id": item.get("id"),
        "channel": ch or "other",
        "image": media_src(item.get("image_url")),
        "video": video,
        "video_link": http_url(item.get("video_url")),
        "poster": poster_for(item) if video else None,
        "video_s": video_seconds(item),
        "flags": flags(item),
        "pillar_id": pid,
        "pillar": (pillars or {}).get(pid) if pid else None,
        "hook": item.get("hook_style"),
        "links": [(k, u) for k in ("link", "short_url", "external_url") if (u := http_url(item.get(k)))],
        "fold": fold if fold and len(body) > fold else None,
        "body_head": body[:fold] if fold and len(body) > fold else body,
        "body_tail": body[fold:] if fold and len(body) > fold else "",
    }


def event(item: dict) -> dict:
    """A calendar item as a FullCalendar event."""
    status = item.get("status") or "draft"
    return {
        "id": str(item.get("id")),
        "title": f"{item.get('channel') or ''} · {item.get('title') or ''}".strip(" ·"),
        "start": item.get("scheduled_at"),
        "url": f"/items/{item.get('id')}",
        "color": STATUS_COLORS.get(status, "#5b6b82"),
        "editable": status in EDITABLE_TIME,
        "extendedProps": {"status": status, "channel": item.get("channel")},
    }
