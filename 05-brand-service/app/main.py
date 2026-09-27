"""Brand service: one brand profile, a compact prompt summary, and a rule checker."""
import hmac
import json
import logging
import os
import re
import tempfile
import threading
from typing import Annotated

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("brand-service")

app = FastAPI(title="brand-service")

SUMMARY_MAX = 1600  # was 1200: the example brand's product list (last line) got cut off (night 5)
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


# --- Voice profile (learned from an interview; stored apart from the read-only brand.yaml) ---

VOICE_QUESTIONS = [
    "Who do you talk to? Describe one real customer.",
    "How would you explain what you sell to a friend, in two or three sentences?",
    "Which three words describe how you sound? Which three words should never describe you?",
    "Paste a post you loved (yours or anyone's) and say why.",
    "How much humour, and what kind?",
    "How formal are you? Would you write \"we're\" or \"we are\", \"hi\" or \"dear\"?",
    "Emoji, exclamation marks, hashtags - how many, and when?",
    "What do competitors or other brands in your space sound like that you don't want to?",
    "What does a customer say about you, in their own words?",
    "What do you never promise or claim?",
]


Rule = Annotated[str, Field(min_length=1, max_length=120)]
Word = Annotated[str, Field(min_length=1, max_length=40)]
Sample = Annotated[str, Field(min_length=1, max_length=200)]


class VoiceProfile(BaseModel):
    """Output of the 04 `voice_profile` prompt. Limits match that prompt's schema."""
    summary: str = Field(min_length=1, max_length=300)
    do: list[Rule] = Field(default=[], max_length=6)
    dont: list[Rule] = Field(default=[], max_length=6)
    words_we_use: list[Word] = Field(default=[], max_length=10)
    words_we_avoid: list[Word] = Field(default=[], max_length=10)
    sentence_style: str = Field(default="", max_length=200)
    sample_lines: list[Sample] = Field(default=[], max_length=3)


def voice_file() -> str:
    return os.environ.get("VOICE_FILE", "/config/voice.json")


def require_key(x_api_key: str | None = Header(default=None)):
    """Enforced whenever INTERNAL_API_KEY is set (the stack sets it); open for local tests."""
    expected = os.environ.get("INTERNAL_API_KEY")
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(401, "missing or wrong X-API-Key")


def load_voice() -> dict | None:
    """The stored profile, or None. A broken file is logged and ignored, never a 500."""
    try:
        with open(voice_file(), encoding="utf-8") as fh:
            return VoiceProfile(**json.load(fh)).model_dump()
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        log.warning("ignoring unreadable voice file %s: %s", voice_file(), exc)
        return None


def save_voice(profile: dict) -> None:
    """Write-then-rename, so a reader never sees half a file."""
    path = voice_file()
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".voice-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(profile, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def voice_lines(v: dict) -> list[str]:
    """The voice rules for the prompt summary: summary, do, don't, words to avoid."""
    lines = [f"Voice: {v['summary'].strip()}"]
    if v.get("do"):
        lines.append("Voice do: " + _join(v["do"]))
    if v.get("dont"):
        lines.append("Voice don't: " + _join(v["dont"]))
    if v.get("words_we_avoid"):
        lines.append("Avoid words: " + _join(v["words_we_avoid"], ", "))
    return lines


def voice_extras(v: dict) -> list[str]:
    """Lower-priority voice detail, appended at the very end only while it fits."""
    extras = []
    if (v.get("sentence_style") or "").strip():
        extras.append("\nSentences: " + v["sentence_style"].strip())
    if v.get("words_we_use"):
        extras.append("\nWords we use: " + _join(v["words_we_use"], ", "))
    # Sample lines are NOT sent to writers: in the eval they carried invented details (a customer
    # name, "by noon") that writers copy. They stay stored for people to read (GET /voice).
    return extras


def build_summary(b: dict, learned: dict | None = None) -> str:
    """Plain-text profile for prompts. Hard rules first, then the voice rules, so the
    SUMMARY_MAX cut only drops descriptive colour. Voice extras (sentence style, words we
    use, sample lines) come last and only while they fit; they are never cut mid-way."""
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
    if learned:  # right after the hard rules: the cut below can never reach it
        lines += voice_lines(learned)
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
            f"{p.get('name')}{' (' + str(p['price']) + ')' if p.get('price') else ''}: {p.get('one_line', '')}"
            for p in prods
        ))
    text = "\n".join(lines)
    if len(text) > SUMMARY_MAX:
        return text[: SUMMARY_MAX - 3].rstrip() + "..."
    for extra in voice_extras(learned or {}):
        if len(text) + len(extra) > SUMMARY_MAX:
            break
        text += extra
    return text


class CheckRequest(BaseModel):
    text: str
    channel: str | None = None
    # Links the writer was given (the user's link, a CTA URL): their domains are allowed too.
    allowed_domains: list[str] = []


# Domains in copy: full URLs, or bare names like "NorthwindRoasters.com" (a model invented
# that one in a video script on night 5; readers would type it and land somewhere else).
URL_HOST_RE = re.compile(r"https?://([^/\s?#:]+)", re.I)
BARE_DOMAIN_RE = re.compile(
    r"(?<![@\w.-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:com|net|org|io|co|ai|app|dev|shop|store|coffee|info|biz|me|tv|xyz|eu|us|uk|de|fr|es|it|nl|ca|au))"
    r"(?![\w-])", re.I)


def host_of(value: str) -> str:
    """'https://www.Example.com/x' or 'example.com' -> 'example.com'."""
    m = URL_HOST_RE.search(value)
    host = (m.group(1) if m else value).strip().strip("/").lower()
    return host[4:] if host.startswith("www.") else host


def domain_allowed(host: str, allowed: set[str]) -> bool:
    return any(host == a or host.endswith("." + a) for a in allowed)


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
    return {"summary": build_summary(load_brand(), load_voice())}


@app.get("/voice/questions")
def voice_questions():
    return {"questions": VOICE_QUESTIONS}


@app.get("/voice")
def get_voice():
    v = load_voice()
    if v is None:
        raise HTTPException(404, "no voice profile stored")
    return v


@app.put("/voice", dependencies=[Depends(require_key)])
def put_voice(profile: VoiceProfile):
    data = profile.model_dump()
    try:
        save_voice(data)
    except OSError as exc:
        raise HTTPException(500, f"cannot write VOICE_FILE {voice_file()}: {exc.strerror}") from exc
    return data


@app.delete("/voice", dependencies=[Depends(require_key)])
def delete_voice():
    try:
        os.unlink(voice_file())
        return {"deleted": True}
    except FileNotFoundError:
        return {"deleted": False}


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

    for word in ((load_voice() or {}).get("words_we_avoid") or []):
        found = find_token(text, word) if word.strip() else None
        if found:
            add("avoid_word", f"uses '{found}', a word the brand voice avoids", "warn", match=found)

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

    allowed_hosts = {host_of(d) for d in [b.get("website") or "", *(b.get("allowed_domains") or []),
                                           *req.allowed_domains] if d and d.strip()}
    hosts = [host_of(h) for h in URL_HOST_RE.findall(text)]
    hosts += [host_of(h) for h in BARE_DOMAIN_RE.findall(URL_HOST_RE.sub(" ", text))]
    for host in dict.fromkeys(hosts):
        if not domain_allowed(host, allowed_hosts):
            add("unknown_domain", f"link or domain '{host}' is not the brand's website or a link you gave",
                b.get("unknown_domain_severity", "error"), match=host)

    bangs = text.count("!")
    if bangs > MAX_EXCLAMATIONS:
        add("exclamation_marks", f"{bangs} exclamation marks, max {MAX_EXCLAMATIONS}", "warn")

    ok = not any(v["severity"] == "error" for v in violations)
    return {"ok": ok, "violations": violations}
