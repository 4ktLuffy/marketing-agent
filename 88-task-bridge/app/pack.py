"""The task pack: plain text a person pastes into ANY chatbot (free plans included).

What leaves the business is decided here and nowhere else:
  public facts   -> "- [[key]] = value text — sentence (must include: ...)"
  internal facts -> "- [[key]]: <neutral description> (use the slot, don't write a value)"
  restricted     -> never
The share preview lists exactly the fact lines that are in the pack (sent), the slot-only keys
(slotted) and everything held back with its reason (withheld). The voice summary is scrubbed of
any line that carries an internal or restricted value.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field

from . import channels as CH
from . import facts as F
from . import selfaudit

HEADER_SEP = " · "
NO_MARKDOWN = {"linkedin", "instagram", "facebook", "x", "twitter", "threads", "mastodon", "tiktok", "sms",
               "google_business", "whatsapp"}
VOICE_MAX = 1200


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def marker(n: int, channel: str) -> str:
    return f"=== {n} {channel.upper()} ==="


def neutral(f: dict) -> str:
    """A description of an internal fact that carries no value."""
    what = str(f.get("attribute") or f.get("fact_type") or "value").replace("_", " ").strip()
    ref = F.subject_ref(f)
    desc = f"the approved {what}" + (f" for {ref}" if ref else "")
    if _leaks(desc, f):
        desc = f"the approved {str(f.get('fact_type') or 'value').replace('_', ' ')}"
    return desc


def _value_strings(f: dict) -> list[str]:
    out = [str(f.get("value_text") or "").strip()]
    v = f.get("value")
    if v is not None and not isinstance(v, bool) and str(v).strip():
        out.append(str(v).strip())
    return [s for s in out if s]


def _leaks(text: str, f: dict) -> bool:
    low = text.lower()
    for s in _value_strings(f):
        if re.fullmatch(r"\d+(?:\.\d+)?", s):
            if re.search(r"(?<![\d.])" + re.escape(s) + r"(?![\d])", low):
                return True
        elif s.lower() in low:
            return True
    return False


def _carries(line: str, f: dict) -> bool:
    vt = str(f.get("value_text") or "").strip()
    return len(vt) >= 3 and vt.lower() in line.lower()


def scrub(summary: str, secret_facts: list[dict]) -> str:
    """Drop every line of the voice summary that contains an internal/restricted value or text."""
    keep = []
    for line in (summary or "").replace("\r\n", "\n").split("\n"):
        if any(_leaks(line, f) or (f.get("text") and str(f["text"]).strip().lower() in line.lower())
               for f in secret_facts):
            continue
        keep.append(line)
    out = "\n".join(keep).strip()
    if len(out) > VOICE_MAX:
        out = out[:VOICE_MAX].rsplit("\n", 1)[0].strip() or out[:VOICE_MAX]
    return out


B2B_WORDS = frozenset("operator agent agency b2b trade wholesale reseller dmc distributor partner broker".split())
CONSUMER_WORDS = frozenset("consumer public retail direct individual leisure guest walk customer resident "
                           "traveller traveler tourist couple family visitor".split())
SEGMENT_KEYS = frozenset({"segment", "segments", "customer_segment", "audience", "customer_type", "market"})
GUEST_KEYS = frozenset({"guests", "guest", "occupancy", "adults", "persons", "people", "pax"})
NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
CONTACT_SCORE = 1000


def stem(w: str) -> str:
    """A crude stem, the same on both sides of every comparison: rooms/room, couples/couple, views/view."""
    w = w.lower()
    for suf, rep in (("ies", "y"), ("ches", "ch"), ("shes", "sh"), ("sses", "ss"), ("ing", ""), ("ed", ""),
                     ("ly", ""), ("es", "e"), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) + len(rep) >= 3 and not (suf == "s" and w.endswith("ss")):
            return w[: len(w) - len(suf)] + rep
    return w


def stems(text: str) -> set[str]:
    return {stem(w) for w in F.content_words(text)}


def _stem_seq(text: str) -> list[str]:
    return [stem(w) for w in F.words(text) if len(w) >= 3 and w not in F.STOP and not w.isdigit()]


def _bigrams(text: str) -> set[tuple[str, str]]:
    seq = _stem_seq(text)
    return set(zip(seq, seq[1:]))


def _is_b2b(words: set[str]) -> bool:
    return bool(words & {stem(w) for w in B2B_WORDS})


def fact_segments(f: dict) -> list[str]:
    """Customer segments a fact is for: scope.segments and conditions such as segment="tour operators"."""
    out = list(F.norm_scope(f.get("scope"))["segments"])
    for c in f.get("conditions") or []:
        if isinstance(c, dict) and str(c.get("key") or "").lower() in SEGMENT_KEYS and \
                str(c.get("op") or "=") in ("=", "in", "=="):
            v = c.get("value")
            out += [str(x).strip().lower() for x in (v if isinstance(v, list) else [v]) if str(x or "").strip()]
    return out


def segment_matches(seg: str, task_stems: set[str], task_segments: list[str]) -> bool:
    if seg in task_segments:
        return True
    ss = stems(seg)
    if not ss:
        return True
    if ss <= task_stems:
        return True
    task_b2b = _is_b2b(task_stems)
    if ss & {stem(w) for w in B2B_WORDS}:
        return task_b2b
    if ss & {stem(w) for w in CONSUMER_WORDS}:
        return not task_b2b
    return False


def _variant(ref: str) -> tuple[str, set[str]]:
    """"Twin Room (Lake View)" -> ("twin room", {"lake", "view"})"""
    m = re.search(r"\(([^)]*)\)", ref or "")
    base = re.sub(r"\([^)]*\)", " ", ref or "")
    return " ".join(base.lower().split()), (stems(m.group(1)) if m else set())


def relevance(f: dict, task_text: str, scope: dict) -> int:
    """How much a fact matters to this task (higher first). Contact facts always win; the business and
    site identity is kept; subject/attribute/text words and phrases ("lake view") of the goal,
    audience and notes score the rest."""
    tw = stems(task_text)
    tb = _bigrams(task_text)
    score = 0
    ref = F.subject_ref(f)
    ref_stems = stems(ref)
    score += min(9, 3 * len(ref_stems & tw))
    type_words = stems(" ".join(str(f.get(k) or "").replace("_", " ") for k in ("fact_type", "attribute")))
    if type_words & tw:
        score += 2
    body = " ".join(str(f.get(k) or "") for k in ("text", "value_text"))
    score += min(4, len(stems(body) & tw))
    cond_words: set[str] = set()
    for c in f.get("conditions") or []:
        if isinstance(c, dict) and str(c.get("key") or "").lower() not in GUEST_KEYS | SEGMENT_KEYS:
            v = c.get("value")
            cond_words |= stems(" ".join(map(str, v)) if isinstance(v, list) else str(v or ""))
    if cond_words & tw:
        score += 2
    hay = _bigrams(ref) | _bigrams(body)
    score += min(8, 4 * len(hay & tb))
    fs, ts = F.norm_scope(f.get("scope")), F.norm_scope(scope)
    if any(fs[d] and ts[d] and set(ts[d]) <= set(fs[d]) for d in F.DIMENSIONS):
        score += 2
    kind, ftype = F.subject_kind(f).lower(), str(f.get("fact_type") or "").lower()
    if kind == "contact" or ftype == "contact":
        score += CONTACT_SCORE
    elif kind in ("business", "site", "location", "organisation", "organization") or \
            ftype in ("identity", "description", "location", "address", "hours"):
        score += 4
    elif ftype == "price" and (ref_stems & tw or hay & tb):
        score += 2
    return score


def public_line(f: dict) -> str:
    vt = " ".join(str(f.get("value_text") or "").split())
    txt = " ".join(str(f.get("text") or "").split())
    if not vt and txt:
        # trial: a fact with no value to insert offered as [[key]] gets cited ("… sauna. [[cave-spa]]") or used
        # as content ("All room types: [[room-types]]") and blocks; as plain text it is just written and checked
        line = f"- {txt}"
    else:
        line = f"- [[{f['key']}]] = {vt}" if vt else f"- [[{f['key']}]]"
        if txt:
            line += f" — {txt}"
    disc = [" ".join(str(d).split()) for d in f.get("required_disclosures") or [] if str(d).strip()]
    if disc:
        line += " (must include: " + " / ".join(f'"{d}"' for d in disc) + ")"
    never = [" ".join(x.split()) for x in F.phrases(f.get("forbidden_phrasing"))]
    if never:
        line += " (never write: " + " / ".join(f'"{x}"' for x in never) + ")"
    return line


def internal_line(f: dict) -> str:
    return f"- [[{f['key']}]]: {neutral(f)} (use the slot, don't write a value)"


# ---------- approved examples ("write like these")

EXAMPLE_MAX = 400
EXAMPLES_MAX = 2
EXAMPLES_TITLE = "APPROVED EXAMPLES (match this voice; facts may have changed, use only the FACTS below):"
FAMILIES = {
    "social": {"linkedin", "instagram", "facebook", "x", "threads", "mastodon", "tiktok", "pinterest", "youtube"},
    "messaging": {"telegram", "whatsapp", "sms"},
    "email": {"email"},
    "web": {"website", "blog", "google_business"},
    "ads": {"ad", "google_ads", "meta_ad"},
    "print": {"flyer"},
}
_SLOT_OPEN = re.compile(r"\[\[|\\\[|\{\{|⟦|【|===")


def channel_family(channel: str) -> str | None:
    c = CH.canonical(channel)
    return next((fam for fam, members in FAMILIES.items() if c in members), None)


def example_rank(example_channel: str, task_channels: list[str]) -> int | None:
    """0 = same channel, 1 = same channel family, None = not usable as an example."""
    ec = CH.canonical(example_channel)
    tcs = {CH.canonical(c) for c in task_channels}
    if ec in tcs:
        return 0
    fam = channel_family(ec)
    if fam and fam in {channel_family(c) for c in tcs}:
        return 1
    return None


def trim_example(text: str, limit: int = EXAMPLE_MAX) -> str | None:
    """One line, at most `limit` characters, cut at a sentence end. None when no sentence end fits."""
    t = " ".join((text or "").split())
    if not t:
        return None
    if len(t) <= limit:
        return t
    cut = max((m.end() for m in re.finditer(r"[.!?…][\"')\]]*(?=\s|$)", t[:limit])), default=0)
    return t[:cut].strip() if cut >= 20 else None


def example_safe(text: str, secret_facts: list[dict]) -> bool:
    """No slot or marker, and no internal/restricted value or text (defence in depth: also checked by the caller)."""
    if not text or _SLOT_OPEN.search(text):
        return False
    low = text.lower()
    for f in secret_facts:
        if _leaks(text, f) or _carries(text, f):
            return False
        t = " ".join(str(f.get("text") or "").split()).lower()
        if t and t in low:
            return False
    return True


def examples_block(examples: list[dict]) -> list[str]:
    if not examples:
        return []
    return ["", EXAMPLES_TITLE] + [f'{i}. ({e["channel"]}) "{e["text"]}"' for i, e in enumerate(examples, 1)]


class PackTooLong(ValueError):
    pass


@dataclass
class Built:
    text: str
    sent: list[str]
    slotted: list[str]
    withheld: list[dict]
    snapshot: list[dict]      # {key, version, slot, sensitivity}
    snapshot_facts: list[dict]
    examples: list[dict] = field(default_factory=list)   # {task_id, piece_key, chars}


@dataclass
class Item:
    """One line of the pack: a single fact, or several facts of one subject that differ only by guests."""
    facts: list[dict]
    line: str
    internal: bool
    score: int
    order: int


def _guest_label(v) -> str:
    try:
        n = int(float(v))
        return f"{NUMBER_WORDS[n]} guest{'' if n == 1 else 's'}" if 0 < n < len(NUMBER_WORDS) else f"{n} guests"
    except (TypeError, ValueError):
        return f"{v} guests"


def _guest_condition(f: dict):
    """(key, label) when the fact's only occupancy condition is guests = n; else None."""
    hit = None
    for c in f.get("conditions") or []:
        if isinstance(c, dict) and str(c.get("key") or "").lower() in GUEST_KEYS:
            if hit or str(c.get("op") or "=") not in ("=", "=="):
                return None
            hit = c
    return hit


def _other_conditions(f: dict) -> str:
    return json.dumps([c for c in f.get("conditions") or []
                       if not (isinstance(c, dict) and str(c.get("key") or "").lower() in GUEST_KEYS)],
                      sort_keys=True, default=str)


def _group_key(f: dict):
    ref = F.subject_ref(f)
    if not ref or not _guest_condition(f):
        return None
    return (ref.lower(), f.get("fact_type"), f.get("attribute"), f.get("basis"), f.get("currency"), f.get("unit"),
            f.get("sensitivity") or "public", json.dumps(F.norm_scope(f.get("scope")), sort_keys=True),
            f.get("valid_from"), f.get("valid_to"), _other_conditions(f),
            tuple(sorted(str(d) for d in f.get("required_disclosures") or [])),
            tuple(sorted(F.phrases(f.get("forbidden_phrasing")))))


def _split_value(texts: list[str]):
    """["$125 per room per night", "$131 per room per night"] -> (["$125", "$131"], "per room per night")"""
    parts = [t.split() for t in texts]
    if any(not p for p in parts):
        return None
    n = 0
    while all(len(p) > n + 1 for p in parts) and len({p[-1 - n] for p in parts}) == 1:
        n += 1
    if n == 0:
        return None
    return [" ".join(p[:-n]) for p in parts], " ".join(parts[0][-n:])


def _tail_bits(f: dict) -> str:
    out = ""
    disc = [" ".join(str(d).split()) for d in f.get("required_disclosures") or [] if str(d).strip()]
    if disc:
        out += " (must include: " + " / ".join(f'"{d}"' for d in disc) + ")"
    never = [" ".join(x.split()) for x in F.phrases(f.get("forbidden_phrasing"))]
    if never:
        out += " (never write: " + " / ".join(f'"{x}"' for x in never) + ")"
    return out


def grouped_public_line(members: list[dict]) -> str | None:
    """One line for rate facts of the same subject that differ only by guests; slots are unchanged."""
    members = sorted(members, key=lambda f: float(_guest_condition(f).get("value") or 0)
                     if str(_guest_condition(f).get("value")).replace(".", "", 1).isdigit() else 0)
    labels = [_guest_label(_guest_condition(f).get("value")) for f in members]
    if len(set(labels)) != len(labels):
        return None
    split = _split_value([" ".join(str(f.get("value_text") or "").split()) for f in members])
    if not split:
        return None
    heads, suffix = split
    body = " / ".join(f"[[{f['key']}]] = {h} {lab}" for f, h, lab in zip(members, heads, labels))
    others = [c for c in members[0].get("conditions") or []
              if isinstance(c, dict) and str(c.get("key") or "").lower() not in GUEST_KEYS]
    cond = ""
    if others:
        cond = " when " + ", ".join(f"{c.get('key')} {c.get('op') or '='} " + (
            "/".join(map(str, c["value"])) if isinstance(c.get("value"), list) else str(c.get("value")))
            for c in others)
    return f"- {F.subject_ref(members[0])}: {body}, {suffix}{cond}" + _tail_bits(members[0])


def grouped_internal_line(members: list[dict]) -> str | None:
    members = sorted(members, key=lambda f: str(_guest_condition(f).get("value")))
    labels = [_guest_label(_guest_condition(f).get("value")) for f in members]
    if len(set(labels)) != len(labels):
        return None
    slots = " / ".join(f"[[{f['key']}]] ({lab})" for f, lab in zip(members, labels))
    return f"- {slots}: {neutral(members[0])} (use the slot, don't write a value)"


def build(task_id: str, fact_set_version: str, publish_on: str, goal: str, audience: str | None,
          notes: str | None, pieces: list[dict], scope: dict, query_facts: list[dict], excluded: list[dict],
          all_facts: list[dict], voice: str | None, rules: dict | None, max_chars: int,
          target_chars: int | None = None, calendar: list[str] | None = None,
          examples: list[dict] | None = None, kit_rules: list[dict] | None = None) -> Built:
    day = F.parse_day(publish_on)
    withheld = [{"key": e.get("key"), "reason": e.get("reason")} for e in excluded if e.get("key")]
    seen_withheld = {w["key"] for w in withheld}

    def hold(key, reason):
        if key not in seen_withheld:
            withheld.append({"key": key, "reason": reason})
            seen_withheld.add(key)

    usable = []
    for f in query_facts:
        # Defence in depth: 05 already filtered, but a restricted or stale fact never goes out.
        r = F.classify(f, day, scope)
        if r:
            hold(f["key"], r)
            continue
        usable.append(f)
    # what the business does NOT offer goes out as one always-present line, not as a fact to cite
    absent = [f for f in usable if f.get("fact_type") == "availability" and f.get("value") is False
              and (f.get("sensitivity") or "public") == "public"]
    usable = [f for f in usable if f not in absent]
    secret = [f for f in all_facts + query_facts if (f.get("sensitivity") or "public") != "public"]

    task_text = " ".join([goal, audience or "", notes or ""])
    task_stems = stems(task_text)
    task_segments = F.norm_scope(scope)["segments"]

    # 1. Relevance: a fact for another customer segment, or for a variant the task did not ask for
    #    ("Garden View" when the goal says lake view), does not go out.
    asked = any(v and v <= task_stems for v in (_variant(F.subject_ref(f))[1] for f in usable))
    scored: list[tuple[dict, int]] = []
    for f in usable:
        internal = (f.get("sensitivity") or "public") == "internal"
        segs = fact_segments(f)
        off_segment = bool(segs) and not any(segment_matches(sg, task_stems, task_segments) for sg in segs)
        if off_segment and internal:
            hold(f["key"], "internal_other_segment")
            continue
        base, var = _variant(F.subject_ref(f))
        if asked and var and not var <= task_stems:
            hold(f["key"], "not_relevant")
            continue
        sc = relevance(f, task_text, scope)
        if off_segment:
            sc -= 8
        scored.append((f, sc))

    # 2. Compact lines: same subject, differing only by guests -> one line (slots stay per fact).
    buckets: dict = {}
    for i, (f, sc) in enumerate(scored):
        k = _group_key(f)
        buckets.setdefault(k if k is not None else ("single", i), []).append((i, f, sc))
    items: list[Item] = []
    for members in buckets.values():
        fs = [m[1] for m in members]
        internal = (fs[0].get("sensitivity") or "public") == "internal"
        line = None
        if len(fs) > 1:
            line = grouped_internal_line(fs) if internal else grouped_public_line(fs)
            if line and not internal and any(_carries(line, g) for g in secret):
                line = None
        if line:
            items.append(Item(fs, line, internal, max(m[2] for m in members), members[0][0]))
        else:
            for i, f, sc in members:
                line = internal_line(f) if internal else public_line(f)
                items.append(Item([f], line, internal, sc, i))
    items.sort(key=lambda it: (-it.score, it.order))

    tail = _tail()
    facts_title = "FACTS (use only these. Write the [[slot]] where the value goes, or the value exactly as written):"
    empty = "- (no facts for this task: do not state prices, dates, numbers or certifications)"
    fixed = len(tail) + len(facts_title) + 5 + len(empty)
    calendar = (calendar or []) + kit_rule_lines(kit_rules) + not_offered_lines(absent)
    head = _head(task_id, fact_set_version, publish_on, goal, audience, notes, pieces, voice, secret, rules, calendar)
    voice_kept = True
    if len(head) + fixed > max_chars:       # the voice goes first, the task itself cannot
        head = _head(task_id, fact_set_version, publish_on, goal, audience, notes, pieces, None, secret, rules, calendar)
        voice_kept = False
    if len(head) + fixed > max_chars:
        raise PackTooLong(f"goal, notes and pieces alone are longer than the pack limit ({max_chars} characters)")
    # Free chats want short packs: fill by relevance up to the target, never past the hard cap.
    target = min(max_chars, target_chars) if target_chars else max_chars

    def select(h: str):
        budget = max(target, len(h) + fixed) - len(h) - fixed
        chosen, dropped = [], []
        for it in items:
            if not it.internal and any(_carries(it.line, g) for g in secret):
                dropped.append((it, "contains_internal_value"))
                continue
            if len(it.line) + 1 > budget:
                dropped.append((it, "too_long" if it.score > 0 else "not_relevant"))
                continue
            budget -= len(it.line) + 1
            chosen.append(it)
        return chosen, dropped

    chosen, dropped = select(head)
    # Examples are the lowest priority: they stay only if no fact is lost to them and the hard cap holds.
    used_examples: list[dict] = []
    cands = [e for e in (examples or []) if example_safe(e.get("text", ""), secret)][:EXAMPLES_MAX]
    if voice_kept:
        base_keys = [id(i) for i in chosen]
        base_total = len(head) + fixed + sum(len(i.line) + 1 for i in chosen)
        cap = max(target, base_total)       # examples never push the pack past what it would have been
        for k in range(len(cands), 0, -1):
            h = _head(task_id, fact_set_version, publish_on, goal, audience, notes, pieces, voice, secret, rules,
                      calendar, cands[:k])
            if len(h) + fixed > max_chars:
                continue
            c2, d2 = select(h)
            if [id(i) for i in c2] == base_keys and len(h) + fixed + sum(len(i.line) + 1 for i in c2) <= cap:
                head, chosen, dropped, used_examples = h, c2, d2, cands[:k]
                break
    lines, sent, slotted, snap, snap_facts = [], [], [], [], []
    for it, why in dropped:
        for f in it.facts:
            hold(f["key"], why)
    for it in chosen:
        lines.append(it.line)
        if it.internal:
            slotted += [f["key"] for f in it.facts]
        else:
            sent.append(it.line)
        for f in it.facts:
            if it.internal:
                hold(f["key"], "internal")
            snap.append({"key": f["key"], "version": f.get("version"), "slot": f"[[{f['key']}]]",
                         "sensitivity": f.get("sensitivity") or "public"})
            snap_facts.append(f)
    body = facts_title + "\n" + ("\n".join(lines) if lines else empty)
    text = f"{head}\n\n{body}\n\n{tail}"
    return Built(text, sent, slotted, withheld, snap, snap_facts,
                 [{"task_id": e["task_id"], "piece_key": e["piece_key"], "chars": len(e["text"])}
                  for e in used_examples])


def _head(task_id, fsv, publish_on, goal, audience, notes, pieces, voice, secret, rules, calendar=None,
          examples=None) -> str:
    out = [HEADER_SEP.join([f"Task {task_id}", f"facts {fsv}", f"publish {publish_on}"]), "",
           "Write marketing copy for the business below. Use only the facts listed here.",
           f"GOAL: {' '.join(goal.split())}"]
    if audience:
        out.append(f"AUDIENCE: {' '.join(audience.split())}")
    if notes:
        out.append(f"NOTES: {' '.join(notes.split())}")
    if calendar:
        out += ["CALENDAR:"] + [f"- {c}" for c in calendar]
    out += ["", f"WRITE {len(pieces)} PIECE{'S' if len(pieces) != 1 else ''}. Start each one with its marker line, exactly as shown:"]
    channel_rules = (rules or {}).get("channels") or {}
    for i, p in enumerate(pieces, 1):
        extra = []
        if p.get("kind"):
            extra.append(str(p["kind"]))
        limit = p.get("max_chars") or (channel_rules.get(p["channel"]) or {}).get("limit")
        if limit:
            extra.append(f"max {limit} characters")
        if p["channel"] in NO_MARKDOWN:
            extra.append("plain text, no markdown")
        out.append(marker(i, p["channel"]) + (f"   ({', '.join(extra)})" if extra else ""))
    rule_lines = []
    for ch in dict.fromkeys(p["channel"] for p in pieces):
        r = channel_rules.get(ch)
        if not r:
            continue
        bits = [f"max {r['limit']} characters"] if r.get("limit") else []
        if r.get("max_hashtags") is not None:
            bits.append(f"at most {r['max_hashtags']} hashtags")
        if r.get("note"):
            bits.append(str(r["note"]))
        if bits:
            rule_lines.append(f"- {ch}: " + "; ".join(bits))
    if rule_lines:
        out += ["", "CHANNEL RULES:"] + rule_lines
    v = scrub(voice or "", secret)
    if v:
        out += ["", "VOICE:", v]
    out += examples_block(examples or [])
    return "\n".join(out)


NOTE_WORDS = ("recommendation", "owner must confirm", "owner to confirm", "hold for human review")


def not_offered_lines(facts: list[dict]) -> list[str]:
    """"We do not offer airport pick-up" facts as one line: chatbots invent exactly these extras."""
    names = [" ".join(str(F.subject_ref(f) or "").split())[:60] for f in facts]
    names = [n for n in dict.fromkeys(names) if n]
    return ["We do NOT offer (never write or imply that we do): " + ", ".join(names[:30])[:700]] if names else []


def kit_rule_lines(rules: list[dict] | None) -> list[str]:
    """Owner-confirmed starter-kit rules (05 /rules?status=active) as two short lines for the chatbot:
    what every piece must say and what it must never say. Procedural notes are left out."""
    must, never = [], []
    for r in rules or []:
        if not isinstance(r, dict) or r.get("status") != "active":
            continue
        phrase = " ".join(str(r.get("phrase") or "").split())[:120]
        if not phrase:
            continue
        why = str(r.get("why") or "").lower()
        if r.get("kind") == "required_disclosure":
            if any(w in why or w in phrase.lower() for w in NOTE_WORDS):
                continue
            when = " ".join(str(r.get("why") or "").split())[:80]
            must.append(f'"{phrase}"' + (f" ({when})" if when else ""))
        elif r.get("kind") == "forbidden_phrase":
            never.append(f'"{phrase}"')
    out = []
    if must:
        out.append("Business rules, always: include " + "; ".join(dict.fromkeys(must))[:600])
    if never:
        out.append("Business rules, never write: " + ", ".join(list(dict.fromkeys(never))[:30])[:700])
    return out


def _tail() -> str:
    return "\n".join([
        "RULES:",
        "1. Copy every [[slot]] you use exactly as written, with both square brackets. A fact without a value "
        "(\"use the slot\") must only ever appear as its slot.",
        "2. Use only the FACTS: no invented prices, numbers, awards, offers, free extras, amenities, policies "
        "or distances. If you need one, write [[missing: what you need]].",
        "3. When you use a fact, put its \"must include\" text in the same piece, word for word.",
        "4. No preamble and no sign-off: start with the first marker line and stop after the last piece"
        + (" and its NOT IN FACTS list." if selfaudit.on() else "."),
        *([selfaudit.RULE] if selfaudit.on() else []),
    ])
