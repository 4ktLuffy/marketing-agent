"""Brand service: one brand profile, a compact prompt summary, and a rule checker."""
import os
import re
import threading

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="brand-service")

SUMMARY_MAX = 1200
MAX_EXCLAMATIONS = 2
MAX_CAPS_WORDS = 3

# Emoji: flag pairs, or a pictograph with optional variation selector / skin tone,
# joined by ZWJ into one visible emoji. Close enough for a "max per post" rule.
_PICTO = "[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u231A-\u23FF]"
_MOD = "(?:\uFE0F)?(?:[\U0001F3FB-\U0001F3FF])?"


def strip_vs(s: str) -> str:
    """Drop variation selectors so '☕' and '☕️' compare equal."""
    return s.replace("\ufe0f", "").replace("\ufe0e", "")


EMOJI_RE = re.compile(
    rf"[\U0001F1E6-\U0001F1FF]{{2}}|{_PICTO}{_MOD}(?:\u200D{_PICTO}{_MOD})*"
)
CAPS_RE = re.compile(r"\b[A-Z][A-Z0-9]*[A-Z]\b")  # 2+ letters, all upper case

_cache: dict = {"path": None, "mtime": None, "data": None}
_lock = threading.Lock()


def brand_file() -> str:
    return os.environ.get("BRAND_FILE", "/config/brand.yaml")


def load_brand() -> dict:
    """Return the parsed brand yaml, re-reading it only when its mtime changes."""
    path = brand_file()
    try:
        mtime = os.stat(path).st_mtime_ns
    except FileNotFoundError:
        raise HTTPException(503, f"brand file not found: {path} (set BRAND_FILE)")
    with _lock:
        if _cache["path"] != path or _cache["mtime"] != mtime:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                raise HTTPException(500, f"brand file {path} must be a yaml mapping")
            _cache.update(path=path, mtime=mtime, data=data)
        return _cache["data"]


def find_token(text: str, phrase: str) -> str | None:
    """Case-insensitive match that does not fire inside other words ('cure' vs 'secure').

    A '*' stands for up to three words, so "best * in the world" also catches
    "best coffee in the world" -- the variants models actually write.
    """
    parts = [re.escape(p.strip()) for p in phrase.strip().split("*")]
    body = r"(?:\s+\S+){0,3}\s+".join(p for p in parts if p)
    pattern = r"(?<!\w)" + body + r"(?!\w)"
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(0) if m else None


def contains_token(text: str, phrase: str) -> bool:
    return find_token(text, phrase) is not None


def _join(items, sep="; ") -> str:
    return sep.join(str(i).strip().rstrip(".") for i in items)


def build_summary(b: dict) -> str:
    """Plain-text profile for prompts. Hard rules first, so truncation only drops colour."""
    lines = [f"Brand: {b.get('name', '')} - {b.get('one_liner', '')}"]
    if b.get("website"):
        lines.append(f"Website: {b['website']}")
    aud = b.get("audience")
    if isinstance(aud, dict):
        lines.append(f"Audience: {aud.get('primary', '')}")
    elif aud:
        lines.append(f"Audience: {aud}")
    voice = b.get("voice") or {}
    if voice.get("tone"):
        lines.append(f"Tone: {voice['tone']}")
    if b.get("banned_phrases"):
        lines.append("Never say: " + _join(b["banned_phrases"], ", "))
    emoji_max = (b.get("emoji_policy") or {}).get("max_per_post")
    if emoji_max is not None:
        allowed = (b.get("emoji_policy") or {}).get("allowed")
        lines.append(f"Emoji: max {emoji_max} per post" + (f", only these: {' '.join(allowed)}" if allowed else ""))
    if b.get("preferred_hashtags"):
        lines.append("Hashtags: " + _join(b["preferred_hashtags"], " "))
    if b.get("key_messages"):
        lines.append("Key messages: " + _join(b["key_messages"]))
    if voice.get("do"):
        lines.append("Do: " + _join(voice["do"]))
    if voice.get("dont"):
        lines.append("Don't: " + _join(voice["dont"]))
    prods = b.get("products") or []
    if prods:
        lines.append("Products: " + _join(
            f"{p.get('name')} ({p.get('price')}): {p.get('one_line', '')}" for p in prods
        ))
    text = "\n".join(lines)
    if len(text) > SUMMARY_MAX:
        text = text[: SUMMARY_MAX - 3].rstrip() + "..."
    return text


class CheckRequest(BaseModel):
    text: str
    channel: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/profile")
def profile():
    return load_brand()


def build_facts(b: dict) -> list[dict]:
    """Every statement copy may rely on: explicit facts, products, key messages."""
    facts = [str(f).strip() for f in b.get("facts") or []]
    if b.get("one_liner"):
        facts.append(str(b["one_liner"]).strip())  # the brand's own approved positioning line
    for prod in b.get("products") or []:
        line = f"{prod.get('name')} costs {prod.get('price')}." if prod.get("price") else ""
        facts += [s for s in (line, f"{prod.get('name')}: {prod.get('one_line', '')}".strip()) if s]
    facts += [str(m).strip() for m in b.get("key_messages") or []]
    return [{"id": f"f{i + 1}", "text": f} for i, f in enumerate(f for f in facts if f)]


@app.get("/facts")
def facts():
    return {"facts": build_facts(load_brand())}


@app.get("/profile/summary")
def profile_summary():
    return {"summary": build_summary(load_brand())}


@app.post("/check")
def check(req: CheckRequest):
    b = load_brand()
    text = req.text
    violations = []

    def add(rule, detail, severity, match=None):
        v = {"rule": rule, "detail": detail, "severity": severity}
        if match:
            v["match"] = match  # the exact offending text, for automated rewrites
        violations.append(v)

    for phrase in b.get("banned_phrases") or []:
        found = find_token(text, phrase)
        if found:
            # Quote the text as written: an LLM fixing the copy needs the exact words.
            rule = f" (rule: '{phrase}')" if found.lower() != phrase.lower() else ""
            add("banned_phrase", f"contains banned phrase '{found}'{rule}", "error", match=found)

    if req.channel:
        channel = req.channel.strip().lower()
        options = (b.get("required_disclaimers") or {}).get(channel)
        if options and not any(contains_token(text, o) for o in options):
            add("missing_disclaimer",
                f"channel '{channel}' requires one of: {', '.join(options)}", "error")

    policy = b.get("emoji_policy") or {}
    found = EMOJI_RE.findall(text)
    emoji_max = policy.get("max_per_post")
    if emoji_max is not None and len(found) > emoji_max:
        add("too_many_emojis", f"{len(found)} emojis, max {emoji_max}", "warn")
    # Optional whitelist: small models pick odd emojis (a blood drop on a coffee post).
    allowed = {strip_vs(e) for e in policy.get("allowed") or []}
    if allowed:
        for e in dict.fromkeys(strip_vs(f) for f in found):
            if e not in allowed:
                add("emoji_not_allowed", f"emoji {e} is not in the brand's emoji set",
                    policy.get("not_allowed_severity", "error"), match=e)

    allowed = {a.upper() for a in b.get("allowed_acronyms") or []}
    caps = [w for w in CAPS_RE.findall(text) if w not in allowed]
    if len(caps) > MAX_CAPS_WORDS:
        add("all_caps", f"{len(caps)} all-caps words ({', '.join(caps[:5])}), max {MAX_CAPS_WORDS}",
            "warn")

    bangs = text.count("!")
    if bangs > MAX_EXCLAMATIONS:
        add("exclamation_marks", f"{bangs} exclamation marks, max {MAX_EXCLAMATIONS}", "warn")

    ok = not any(v["severity"] == "error" for v in violations)
    return {"ok": ok, "violations": violations}
