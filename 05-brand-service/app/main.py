"""Brand service: one brand profile, a compact prompt summary, and a rule checker."""
import copy
import hmac
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
from datetime import date
from typing import Annotated, Literal

import yaml
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from . import ai_sheen
from . import kit_rules
from . import facts_store as store

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

_cache: dict = {"key": None, "data": None}
_lock = threading.Lock()
_write_lock = threading.Lock()  # one read-modify-write of the overrides file at a time


def brand_file() -> str:
    return os.environ.get("BRAND_FILE", "/config/brand.yaml")


def overrides_file() -> str:
    return os.environ.get("BRAND_OVERRIDES_FILE", "/config/brand.overrides.json")


def _stamp(path: str):
    """Changes whenever the file is rewritten (atomic replace gives a new inode too)."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _read_base(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise HTTPException(500, f"brand file {path} must be a yaml mapping")
    return data


def load_overrides() -> dict:
    """The edits saved through PUT /brand/editable, or {}. A broken file is logged and
    ignored (the base brand still works), never a 500."""
    path = overrides_file()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable overrides file %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("ignoring overrides file %s: not a JSON object", path)
        return {}
    return data


def load_base() -> dict:
    """brand.yaml as shipped or mounted (read-only; the service never writes it)."""
    path = brand_file()
    if _stamp(path) is None:
        raise HTTPException(503, f"brand file not found: {path} (set BRAND_FILE)")
    return _read_base(path)


def load_brand() -> dict:
    """brand.yaml with the saved overrides merged on top, re-read only when either file changes."""
    path, opath = brand_file(), overrides_file()
    stamp = _stamp(path)
    if stamp is None:
        raise HTTPException(503, f"brand file not found: {path} (set BRAND_FILE)")
    key = (path, stamp, opath, _stamp(opath))
    with _lock:
        if _cache["key"] != key:
            _cache.update(key=key, data=merge_brand(_read_base(path), load_overrides()))
        return _cache["data"]


# --- Merge rules for the overrides file -------------------------------------------------
# Top-level keys of the overrides replace the base's. Mappings (audience, voice, emoji_policy)
# merge key by key, so a base key the editor doesn't show (emoji_policy.note) survives.
# Lists and scalars replace. Products merge by name (case-insensitive): an override replaces
# the base product with that name in place, new names are appended, and names listed in
# `_removed_products` are dropped.
REMOVED = "_removed_products"
PRODUCT_KEYS = ("name", "price", "one_line", "aliases")


def _pkey(p) -> str:
    return str((p or {}).get("name", "")).strip().casefold() if isinstance(p, dict) else ""


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def merge_products(base: list, over: list, removed: list) -> list:
    by = {_pkey(p): p for p in over if _pkey(p)}
    gone = {str(n).strip().casefold() for n in removed}
    out = []
    for p in base:
        k = _pkey(p)
        if k in gone:
            continue
        out.append(copy.deepcopy(by.pop(k, p)))
    return out + [copy.deepcopy(p) for p in over if _pkey(p) in by]


def merge_brand(base: dict, over: dict) -> dict:
    out = _deep_merge(base, {k: v for k, v in over.items() if k not in ("products", REMOVED)})
    if "products" in over or REMOVED in over:
        out["products"] = merge_products(base.get("products") or [], over.get("products") or [],
                                         over.get(REMOVED) or [])
    return out


def find_token(text: str, phrase: str) -> str | None:
    """Case-insensitive match that does not fire inside other words ('cure' vs 'secure').

    A '*' stands for up to three words, so "best * in the world" also catches
    "best coffee in the world" -- the variants models actually write.
    """
    parts = [re.escape(p.strip()) for p in phrase.strip().split("*")]
    body = r"(?:\s+\S+){0,3}\s+".join(p for p in parts if p)
    plural = r"(?:s|es)?" if re.search(r"[A-Za-z]$", phrase.strip()) else ""   # "designated driver" = "designated drivers"
    pattern = r"(?<!\w)" + body + plural + r"(?!\w)"
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
    """Enforced whenever INTERNAL_API_KEY is set (the stack sets it); open for local tests.

    Reads need it too: with several clients on one machine (install.sh --client) each has its own
    key, so a URL that points at the wrong client's brand fails with 401 instead of quietly
    answering with another brand's facts."""
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


def write_json_atomic(path: str, data, prefix: str) -> None:
    """Write-then-rename, so a reader never sees half a file."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def save_voice(profile: dict) -> None:
    write_json_atomic(voice_file(), profile, ".voice-")


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


@app.get("/profile", dependencies=[Depends(require_key)])
def profile():
    return load_brand()


def legacy_items(b: dict) -> list[tuple[str, dict]]:
    """(text, origin) for every statement copy may rely on, in the legacy /facts order:
    explicit facts, the one-liner, products (price line, then description), key messages."""
    items = [(str(f).strip(), {}) for f in b.get("facts") or []]
    if b.get("one_liner"):
        items.append((str(b["one_liner"]).strip(), {}))  # the brand's own approved positioning line
    for prod in b.get("products") or []:
        if prod.get("price"):
            items.append((f"{prod.get('name')} costs {prod.get('price')}.",
                          {"product": prod.get("name"), "price": prod.get("price")}))
        items.append((f"{prod.get('name')}: {prod.get('one_line', '')}".strip(), {"product": prod.get("name")}))
    items += [(str(m).strip(), {}) for m in b.get("key_messages") or []]
    return [(t, o) for t, o in items if t]


def build_facts(b: dict) -> list[dict]:
    """Every statement copy may rely on: explicit facts, products, key messages."""
    return [{"id": f"f{i + 1}", "text": t} for i, (t, _) in enumerate(legacy_items(b))]


def keyed_items(b: dict) -> list[tuple[str, str, dict]]:
    """legacy_items with the derived fact key of each line: `price-<product-slug>` for a price
    line, else `legacy-<sha8(text)>` (also when two price lines would share a slug)."""
    out, seen = [], {}
    for text, origin in legacy_items(b):
        key = None
        if origin.get("price"):
            s = store.slug(str(origin["product"]))
            key = f"price-{s}" if len(s) >= 2 else None
        if key is None or seen.get(key, text) != text:
            key = f"legacy-{store.sha8(text)}"
        seen[key] = text
        out.append((text, key, origin))
    return out


def derived_facts(b: dict) -> list[dict]:
    """The brand profile's facts as read-only v2 facts (scope all, public, always valid)."""
    out, seen = [], set()
    for text, key, origin in keyed_items(b):
        if key in seen:
            continue
        seen.add(key)
        f = copy.deepcopy(store.BLANK_FACT)
        product = origin.get("product")
        f.update(key=key, text=text, sensitivity="public", risk="low",
                 subject={"kind": "product" if product else "business", "ref": str(product or b.get("name") or "")},
                 fact_type="price" if origin.get("price") else "claim",
                 attribute="price" if origin.get("price") else None,
                 value_text=str(origin["price"]) if origin.get("price") else None,
                 source={"kind": "owner_statement", "ref": "brand profile (brand.yaml + saved edits)"})
        f.update(status="active", version=1, latest_version=1, derived=True, superseded_by=None,
                 disclosure_wordings=[])
        out.append(f)
    return out


def _derived_state(b: dict, conn, day: str | None = None) -> tuple[list[dict], dict[str, str]]:
    derived, hidden = derived_facts(b), store.hidden_derived(conn, day)
    for f in derived:
        if f["key"] in hidden:
            f.update(status="superseded", superseded_by=hidden[f["key"]])
    return derived, hidden


def _fs(conn, derived: list[dict]) -> str:
    return store.fact_set_version(conn, [f["text"] for f in derived if f["status"] == "active"])


@app.get("/facts", dependencies=[Depends(require_key)])
def facts(max_sensitivity: store.Sensitivity = "internal"):
    """Legacy shape, ids f1..fN. With an empty or absent fact store the response is exactly
    what it was before the store existed. Stored facts are appended only when active, valid
    today and not restricted (restricted never appears here, whatever max_sensitivity says)."""
    b = load_brand()
    if not store.exists():
        return {"facts": build_facts(b)}
    try:
        with store.reading() as conn:
            hidden, rows = store.hidden_derived(conn), store.stored_facts(conn)
            succ = store.successor_map(conn)
    except (store.StoreError, sqlite3.Error) as exc:  # the brand's own facts still work
        log.warning("fact store unreadable, serving brand profile facts only: %s", exc)
        return {"facts": build_facts(b)}
    texts = [t for t, key, _ in keyed_items(b) if key not in hidden]
    out = [{"id": f"f{i + 1}", "text": t} for i, t in enumerate(texts)]
    limit = "internal" if max_sensitivity == "restricted" else max_sensitivity
    day = store.today()
    for row, data in rows:
        if store.exclusion_reason(store.status_on(row, day, succ), data, day, limit, {}) not in (None, "scope_unspecified"):
            continue
        note = store.scope_note(data["scope"])
        out.append({"id": f"f{len(out) + 1}", "text": store.with_scope_note(data["text"], note),
                    "key": data["key"], "version": row["current_version"], "scope_note": note})
    return {"facts": out}


# --- Fact store v2 (contract: _dev/phase1-contracts.md §1-§2) --------------------------------

@app.exception_handler(store.StoreError)
def _store_error(request: Request, exc: store.StoreError):
    return JSONResponse({"detail": str(exc)}, status_code=500)


@app.exception_handler(store.NotFound)
def _not_found(request: Request, exc: store.NotFound):
    return JSONResponse({"detail": f"not found: {exc}"}, status_code=404)


@app.exception_handler(store.Conflict)
def _conflict(request: Request, exc: store.Conflict):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(store.Invalid)
def _invalid(request: Request, exc: store.Invalid):
    return JSONResponse({"detail": [{"loc": list(exc.loc), "msg": exc.msg, "type": "value_error"}]},
                        status_code=422)


def require_owner(x_owner_key: str | None = Header(default=None)):
    """A human's key (FACT_OWNER_KEY, held by 05 and the control room only). Refuses when unset,
    so a stack without it can draft facts but nothing becomes a fact on its own."""
    expected = os.environ.get("FACT_OWNER_KEY")
    if not expected:
        raise HTTPException(503, "FACT_OWNER_KEY is not set: confirming facts is switched off")
    if not (x_owner_key and hmac.compare_digest(x_owner_key.encode(), expected.encode())):
        raise HTTPException(403, "missing or wrong X-Owner-Key")


def actor(x_actor: str | None = Header(default=None)) -> str | None:
    """Optional `X-Actor` (who made the change, for the version history), max 80 chars."""
    return (x_actor or "").strip()[:80] or None


def _derived_keys() -> set[str]:
    return {f["key"] for f in derived_facts(load_brand())}


def _parse_fact(body, loc=("body",)) -> store.FactIn:
    try:
        return store.FactIn.model_validate(body)
    except ValidationError as exc:
        errs = exc.errors(include_url=False, include_context=False)
        raise RequestValidationError([e | {"loc": (*loc, *e["loc"])} for e in errs]) from exc


class ImportIn(store._Strict):
    facts: list[store.FactIn] = Field(default=[], max_length=500)
    confirm: bool = False


@app.get("/facts/v2", dependencies=[Depends(require_key)])
def list_facts_v2(status: store.Status | None = None, include_derived: bool = True):
    b = load_brand()
    with store.reading() as conn:
        derived, _ = _derived_state(b, conn)
        day = store.today()
        succ, wordings = store.successor_map(conn), store.active_wordings(conn)
        stored = [store.render(row, data, day, succ, wordings) for row, data in store.stored_facts(conn)]
        fs = _fs(conn, derived)
    items = (derived if include_derived else []) + stored
    return {"fact_set_version": fs, "facts": [f for f in items if status is None or f["status"] == status]}


@app.get("/facts/v2/{key}", dependencies=[Depends(require_key)])
def get_fact_v2(key: str):
    b = load_brand()
    with store.reading() as conn:
        derived, _ = _derived_state(b, conn)
        for f in derived:
            if f["key"] == key:
                data = {k: v for k, v in f.items() if k not in store.READ_ONLY and k != "status"}
                return f | {"versions": [{"version": 1, "status": f["status"], "data": data,
                                          "created_by": "brand profile", "created_at": None, "confirmed_by": None}]}
        if conn is None:
            raise store.NotFound(key)
        return store.get(conn, key)


@app.post("/facts/v2", dependencies=[Depends(require_key)])
def create_fact_v2(fact: store.FactIn, who: str | None = Depends(actor)):
    return store.create(fact, _derived_keys(), who or "api")


@app.put("/facts/v2/{key}", dependencies=[Depends(require_key)])
def update_fact_v2(key: str, body: dict = Body(...), who: str | None = Depends(actor)):
    body = {"key": key, **body}
    fact = _parse_fact(body)
    if fact.key != key:
        raise store.Invalid(("body", "key"), f"key '{fact.key}' does not match the URL ('{key}')")
    return store.update(key, fact, _derived_keys(), who or "api")


@app.post("/facts/v2/import", dependencies=[Depends(require_key), Depends(require_owner)])
def import_facts_v2(body: ImportIn, who: str | None = Depends(actor)):
    keys = [f.key for f in body.facts]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise store.Invalid(("body", "facts"), f"keys used twice: {', '.join(dup)}")
    return store.import_facts(body.facts, body.confirm, _derived_keys(), who or "owner")


@app.post("/facts/v2/{key}/confirm", dependencies=[Depends(require_key), Depends(require_owner)])
def confirm_fact_v2(key: str, who: str | None = Depends(actor)):
    return store.confirm(key, _derived_keys(), who or "owner")


@app.post("/facts/v2/{key}/retire", dependencies=[Depends(require_key), Depends(require_owner)])
def retire_fact_v2(key: str, who: str | None = Depends(actor)):
    return store.retire(key, _derived_keys(), who or "owner")


DAY_RE = re.compile(store.DATE_PATTERN)


@app.get("/facts/query", dependencies=[Depends(require_key)])
def query_facts(site: list[str] = Query([]), region: list[str] = Query([]), channel: list[str] = Query([]),
                segment: list[str] = Query([]), plan_tier: list[str] = Query([]), variant: list[str] = Query([]),
                at: str | None = None, max_sensitivity: store.Sensitivity = "internal"):
    """Facts usable for a task with this scope on day `at`, and why every other fact is not."""
    if at is not None:
        try:
            if not DAY_RE.match(at):
                raise ValueError
            date.fromisoformat(at)
        except ValueError:
            raise HTTPException(422, f"at: expected a date YYYY-MM-DD, got {at!r}")
    day = at or store.today()
    task = {"sites": site, "regions": region, "channels": channel, "segments": segment,
            "plan_tiers": plan_tier, "variants": variant}
    b = load_brand()
    out, excluded = [], []
    with store.reading() as conn:
        derived, _ = _derived_state(b, conn, day)
        succ, wordings = store.successor_map(conn), store.active_wordings(conn)
        for f in derived:
            if f["status"] != "active":
                excluded.append({"key": f["key"], "reason": f["status"]})
            else:
                out.append(f)
        for row, data in store.stored_facts(conn):
            reason = store.exclusion_reason(store.status_on(row, day, succ), data, day, max_sensitivity, task)
            if reason:
                excluded.append({"key": data["key"], "reason": reason})
            else:
                out.append(store.render(row, data, day, succ, wordings))
        fs = _fs(conn, derived)
    return {"fact_set_version": fs, "at": day, "facts": out, "excluded": excluded}


@app.get("/facts/changes", dependencies=[Depends(require_key)])
def fact_changes(since: int = Query(0, ge=0)):
    with store.reading() as conn:
        return store.changes_since(conn, since)


# --- Disclosure wordings: the business's own words for a required disclosure ---
# 88 proposes one when a person accepts a missing_disclosure finding ("it's there, in other words").
# It is served on the fact (`disclosure_wordings`) only after the owner confirms it. Confirming or
# dismissing never writes /facts/changes and never touches allowed_phrasing or legacy /facts.

@app.post("/disclosure-wordings", dependencies=[Depends(require_key)])
def propose_wording(w: store.WordingIn):
    b = load_brand()
    with store.reading() as conn:
        found = next((f for f in derived_facts(b) if f["key"] == w.fact_key), None)
        if found is None and conn is not None:
            row = store._row(conn, w.fact_key)
            found = store._version_data(conn, w.fact_key, row["current_version"]) if row is not None else None
    if found is None:
        raise HTTPException(404, f"no fact with key '{w.fact_key}'")
    wanted = store.norm_text(w.disclosure)
    match = next((str(d) for d in found.get("required_disclosures") or [] if store.norm_text(d) == wanted), None)
    if match is None:
        raise store.Invalid(("body", "disclosure"),
                            f"'{w.disclosure}' is not a required disclosure of fact '{w.fact_key}'")
    row, created = store.propose_wording(w, match)
    return JSONResponse(row, status_code=201 if created else 200)


@app.get("/disclosure-wordings", dependencies=[Depends(require_key)])
def list_wordings(status: Literal["draft", "active", "dismissed"] | None = None, fact_key: str | None = None):
    with store.reading() as conn:
        return {"wordings": store.list_wordings(conn, status, fact_key)}


@app.post("/disclosure-wordings/{wid}/confirm", dependencies=[Depends(require_key), Depends(require_owner)])
def confirm_wording(wid: int, who: str | None = Depends(actor)):
    return store.set_wording_status(wid, "active", who or "owner")


@app.post("/disclosure-wordings/{wid}/dismiss", dependencies=[Depends(require_key), Depends(require_owner)])
def dismiss_wording(wid: int, who: str | None = Depends(actor)):
    return store.set_wording_status(wid, "dismissed", who or "owner")


# --- Open questions ---

@app.get("/questions", dependencies=[Depends(require_key)])
def list_questions(status: Literal["open", "answered", "dismissed"] | None = None):
    with store.reading() as conn:
        return {"questions": store.list_questions(conn, status)}


@app.post("/questions", dependencies=[Depends(require_key)])
def add_question(q: store.QuestionIn):
    return store.add_question(q)


@app.post("/questions/{qid}/answer", dependencies=[Depends(require_key)])
def answer_question(qid: int, body: store.AnswerIn | None = None):
    return store.close_question(qid, "answered", body.answer if body else None)


@app.post("/questions/{qid}/dismiss", dependencies=[Depends(require_key)])
def dismiss_question(qid: int):
    return store.close_question(qid, "dismissed")


# --- Industry starter kits: data, not code; applying one creates DRAFT rules only ---

def _kits() -> dict[str, dict]:
    try:
        return store.load_starter_kits()
    except (OSError, ValueError) as exc:
        raise HTTPException(500, str(exc)) from exc


@app.get("/starter-kits", dependencies=[Depends(require_key)])
def starter_kits():
    return list(_kits().values())


@app.post("/starter-kits/{kit_id}/apply", dependencies=[Depends(require_key), Depends(require_owner)])
def apply_starter_kit(kit_id: str):
    kit = _kits().get(kit_id)
    if kit is None:
        raise HTTPException(404, f"no starter kit '{kit_id}'")
    return store.apply_kit(kit)


@app.get("/rules", dependencies=[Depends(require_key)])
def list_rules(status: Literal["draft", "active", "dismissed"] | None = None, kit: str | None = None):
    with store.reading() as conn:
        return {"rules": store.list_rules(conn, status, kit)}


@app.post("/rules/{rid}/confirm", dependencies=[Depends(require_key), Depends(require_owner)])
def confirm_rule(rid: int):
    return store.set_rule_status(rid, "active")


@app.post("/rules/{rid}/dismiss", dependencies=[Depends(require_key), Depends(require_owner)])
def dismiss_rule(rid: int):
    return store.set_rule_status(rid, "dismissed")


@app.get("/profile/summary", dependencies=[Depends(require_key)])
def profile_summary():
    return {"summary": build_summary(load_brand(), load_voice())}


@app.get("/voice/questions", dependencies=[Depends(require_key)])
def voice_questions():
    return {"questions": VOICE_QUESTIONS}


@app.get("/voice", dependencies=[Depends(require_key)])
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


@app.post("/check", dependencies=[Depends(require_key)])
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

    try:
        with store.reading() as conn:
            active_rules = store.list_rules(conn, "active", None)
    except store.StoreError:
        active_rules = []
    for v in kit_rules.check(active_rules, text, find_token):
        add(v["rule"], v["detail"], v["severity"], match=v.get("match"))

    if str(b.get("ai_sheen", "on")).lower() not in ("off", "false", "0", "no"):
        for v in ai_sheen.lint(text, b.get("ai_sheen_extra") or []):
            add(v["rule"], v["detail"], v["severity"], match=v["match"])

    bangs = text.count("!")
    if bangs > MAX_EXCLAMATIONS:
        add("exclamation_marks", f"{bangs} exclamation marks, max {MAX_EXCLAMATIONS}", "warn")

    ok = not any(v["severity"] == "error" for v in violations)
    return {"ok": ok, "violations": violations}


# --- Editable brand: overrides saved over the read-only brand.yaml ------------------------

def _text(max_len: int, min_len: int = 1):
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=min_len, max_length=max_len)]


DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
CHANNEL_RE = re.compile(r"^[a-z0-9_-]{1,40}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductIn(_Strict):
    name: _text(80)
    one_line: _text(200)
    price: _text(40, 0) | None = None
    aliases: list[_text(40)] = Field(default=[], max_length=10)

    @field_validator("price")
    @classmethod
    def blank_price_is_none(cls, v):
        return v or None


class AudienceIn(_Strict):
    primary: _text(300, 0) = ""
    secondary: _text(300, 0) = ""


class BrandVoiceIn(_Strict):
    tone: _text(200, 0) = ""
    do: list[_text(120)] = Field(default=[], max_length=10)
    dont: list[_text(120)] = Field(default=[], max_length=10)


class EmojiPolicyIn(_Strict):
    max_per_post: int | None = Field(default=None, ge=0, le=10)
    allowed: list[_text(20)] = Field(default=[], max_length=50)

    @field_validator("allowed")
    @classmethod
    def only_emoji(cls, v):
        for i, e in enumerate(v):
            if not EMOJI_RE.fullmatch(e):
                raise ValueError(f"item {i + 1} ('{e}') is not a single emoji")
        return list(dict.fromkeys(v))


class BrandEdit(_Strict):
    """Every field a person may edit. PUT accepts any subset; fields left out keep their value."""
    name: _text(80) | None = None
    one_liner: _text(200) | None = None
    website: _text(200, 0) | None = None
    audience: AudienceIn | None = None
    voice: BrandVoiceIn | None = None
    products: list[ProductIn] | None = Field(default=None, max_length=50)
    facts: list[_text(200)] | None = Field(default=None, max_length=100)
    key_messages: list[_text(200)] | None = Field(default=None, max_length=30)
    banned_phrases: list[_text(80)] | None = Field(default=None, max_length=100)
    emoji_policy: EmojiPolicyIn | None = None
    allowed_domains: list[_text(200)] | None = Field(default=None, max_length=30)
    required_disclaimers: dict[str, list[_text(60)]] | None = Field(default=None, max_length=20)

    @field_validator("website")
    @classmethod
    def website_is_url(cls, v):
        if v and not (re.match(r"^https?://", v, re.I) and DOMAIN_RE.match(host_of(v))):
            raise ValueError("must be a full address like https://example.com")
        return v

    @field_validator("products")
    @classmethod
    def unique_product_names(cls, v):
        seen = set()
        for p in v or []:
            k = p.name.casefold()
            if k in seen:
                raise ValueError(f"product name '{p.name}' is used twice")
            seen.add(k)
        return v

    @field_validator("banned_phrases")
    @classmethod
    def dedupe_phrases(cls, v):
        return list({p.casefold(): p for p in v}.values()) if v else v

    @field_validator("allowed_domains")
    @classmethod
    def domains_are_hosts(cls, v):
        out = []
        for i, d in enumerate(v or []):
            h = host_of(d)
            if not DOMAIN_RE.match(h):
                raise ValueError(f"item {i + 1} ('{d}') is not a domain like shop.example.com")
            out.append(h)
        return list(dict.fromkeys(out)) if v is not None else v

    @field_validator("required_disclaimers")
    @classmethod
    def channels_and_options(cls, v):
        if v is None:
            return v
        out = {}
        for ch, opts in v.items():
            c = ch.strip().lower()
            if not CHANNEL_RE.match(c):
                raise ValueError(f"channel '{ch}': use letters, digits, - or _ (like paid_social)")
            if not opts:
                raise ValueError(f"channel '{c}': give at least one disclaimer text")
            if len(opts) > 10:
                raise ValueError(f"channel '{c}': at most 10 disclaimer texts")
            out[c] = opts
        return out


def _empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _same(a, b) -> bool:
    return (_empty(a) and _empty(b)) or a == b


def _norm_product(p: dict) -> dict:
    """A base or submitted product in the editor's shape, for comparison and display."""
    return {"name": str(p.get("name") or "").strip(), "one_line": str(p.get("one_line") or "").strip(),
            "price": str(p["price"]).strip() if p.get("price") not in (None, "") else None,
            "aliases": [str(a) for a in p.get("aliases") or []]}


def product_overlay(base: list, submitted: list[dict]) -> tuple[list, list]:
    """(changed or new products, removed base names) for a submitted full product list."""
    by_base = {_pkey(p): p for p in base if isinstance(p, dict)}
    changed = []
    for p in submitted:
        b = by_base.get(_pkey(p))
        if b is not None and _norm_product(b) == _norm_product(p):
            continue
        stored = {k: v for k, v in (b or {}).items() if k not in PRODUCT_KEYS}  # keep keys the editor doesn't show
        stored.update({k: v for k, v in _norm_product(p).items() if not _empty(v)})
        changed.append(stored)
    names = {_pkey(p) for p in submitted}
    removed = [str(p.get("name")) for p in base if isinstance(p, dict) and _pkey(p) not in names]
    return changed, removed


def apply_edit(base: dict, over: dict, changes: dict) -> dict:
    """New overrides: only what differs from the base is kept, so 'overridden' stays honest."""
    over = copy.deepcopy(over)
    for k, v in changes.items():
        if k == "products":
            over.pop("products", None)
            over.pop(REMOVED, None)
            changed, removed = product_overlay(base.get("products") or [], v)
            if changed:
                over["products"] = changed
            if removed:
                over[REMOVED] = removed
        elif isinstance(v, dict) and isinstance(base.get(k), dict):
            combined = {**(over.get(k) if isinstance(over.get(k), dict) else {}), **v}
            diff = {kk: vv for kk, vv in combined.items() if not _same(base[k].get(kk), vv)}
            if diff:
                over[k] = diff
            else:
                over.pop(k, None)
        elif _same(base.get(k), v):
            over.pop(k, None)
        else:
            over[k] = v
    return over


def editable_view(b: dict) -> dict:
    """The merged brand in the editor's shape (every editable field present)."""
    aud = b.get("audience")
    aud = {"primary": str(aud.get("primary") or ""), "secondary": str(aud.get("secondary") or "")} \
        if isinstance(aud, dict) else {"primary": str(aud or ""), "secondary": ""}
    voice = b.get("voice") if isinstance(b.get("voice"), dict) else {}
    ep = b.get("emoji_policy") if isinstance(b.get("emoji_policy"), dict) else {}
    return {
        "name": str(b.get("name") or ""), "one_liner": str(b.get("one_liner") or ""),
        "website": str(b.get("website") or ""), "audience": aud,
        "voice": {"tone": str(voice.get("tone") or ""), "do": list(voice.get("do") or []),
                  "dont": list(voice.get("dont") or [])},
        "products": [_norm_product(p) for p in b.get("products") or [] if isinstance(p, dict)],
        "facts": [str(f) for f in b.get("facts") or []],
        "key_messages": [str(m) for m in b.get("key_messages") or []],
        "banned_phrases": [str(p) for p in b.get("banned_phrases") or []],
        "emoji_policy": {"max_per_post": ep.get("max_per_post"), "allowed": list(ep.get("allowed") or [])},
        "allowed_domains": [str(d) for d in b.get("allowed_domains") or []],
        "required_disclaimers": {str(k): list(v or []) for k, v in (b.get("required_disclaimers") or {}).items()},
    }


def editable_doc() -> dict:
    base, over = load_base(), load_overrides()
    base_names = {_pkey(p) for p in base.get("products") or []}
    changed = [p.get("name") for p in over.get("products") or [] if isinstance(p, dict)]
    return {
        "brand": editable_view(load_brand()),
        "overridden": sorted({k for k in over if k in BrandEdit.model_fields} | ({"products"} if REMOVED in over else set())),
        "products": {"changed": [n for n in changed if _pkey({"name": n}) in base_names],
                     "added": [n for n in changed if _pkey({"name": n}) not in base_names],
                     "removed": list(over.get(REMOVED) or [])},
    }


@app.get("/brand/editable", dependencies=[Depends(require_key)])
def get_editable():
    return editable_doc()


@app.put("/brand/editable", dependencies=[Depends(require_key)])
def put_editable(edit: BrandEdit):
    changes = edit.model_dump(exclude_unset=True)
    nulls = [k for k, v in changes.items() if v is None]
    if nulls:  # a field sent as null would mean "unset" and is too easy to send by mistake
        raise HTTPException(422, [{"loc": ["body", k], "msg": "must not be null (leave the field out to keep it)",
                                   "type": "value_error"} for k in nulls])
    with _write_lock:
        new = apply_edit(load_base(), load_overrides(), changes)
        try:
            write_json_atomic(overrides_file(), new, ".brand-overrides-")
        except OSError as exc:
            raise HTTPException(500, f"cannot write BRAND_OVERRIDES_FILE {overrides_file()}: {exc.strerror}") from exc
    return editable_doc()


@app.delete("/brand/editable", dependencies=[Depends(require_key)])
def delete_editable():
    with _write_lock:
        try:
            os.unlink(overrides_file())
            return {"deleted": True}
        except FileNotFoundError:
            return {"deleted": False}


MIN_FACTS = 5


@app.get("/brand/completeness", dependencies=[Depends(require_key)])
def completeness():
    """A checklist a person can act on: what is set, and what is still missing."""
    b = editable_view(load_brand())
    prods = b["products"]
    no_line = [p["name"] for p in prods if not p["one_line"]]
    checks = [
        ("basics", "Name and one-line description", bool(b["name"] and b["one_liner"]),
         "Add the brand name and a one-line description (step 1)."),
        ("website", "Website", bool(b["website"]), "Add the website address (step 1)."),
        ("audience", "Audience", bool(b["audience"]["primary"]), "Describe who you sell to (step 1)."),
        ("products", "Products with a one-line description",
         bool(prods) and not no_line,
         "Add at least one product (step 2)." if not prods else f"Add a one-line description to: {', '.join(no_line)}."),
        ("facts", f"At least {MIN_FACTS} facts", len(b["facts"]) >= MIN_FACTS,
         f"Add {MIN_FACTS - len(b['facts'])} more true, checkable facts (step 3); the agent may only claim what is there."),
        ("banned_phrases", "Banned phrases", bool(b["banned_phrases"]),
         "List words and claims the agent must never use (step 4)."),
        ("emoji_policy", "Emoji rule", b["emoji_policy"]["max_per_post"] is not None,
         "Set how many emoji a post may have (step 4)."),
        ("voice", "Voice interview", load_voice() is not None, "Answer the 10 voice questions (step 5)."),
    ]
    items = [{"id": i, "label": label, "ok": ok, "missing": None if ok else hint} for i, label, ok, hint in checks]
    done = sum(c["ok"] for c in items)
    return {"score": round(100 * done / len(items)), "done": done, "total": len(items), "checks": items,
            "missing": [c["missing"] for c in items if not c["ok"]]}
