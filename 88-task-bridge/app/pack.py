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
import re
from dataclasses import dataclass

from . import facts as F

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


def relevance(f: dict, task_text: str, scope: dict) -> int:
    tw = F.content_words(task_text)
    score = 0
    ref_words = F.content_words(F.subject_ref(f))
    if ref_words & tw:
        score += 3
    type_words = F.content_words(" ".join(str(f.get(k) or "").replace("_", " ") for k in ("fact_type", "attribute")))
    if type_words & tw:
        score += 2
    fs, ts = F.norm_scope(f.get("scope")), F.norm_scope(scope)
    if any(fs[d] and ts[d] and set(ts[d]) <= set(fs[d]) for d in F.DIMENSIONS):
        score += 2
    if F.content_words(f.get("text") or "") & tw:
        score += 1
    if F.subject_kind(f) == "business":
        score += 1
    return score


def public_line(f: dict) -> str:
    vt = " ".join(str(f.get("value_text") or "").split())
    txt = " ".join(str(f.get("text") or "").split())
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


def build(task_id: str, fact_set_version: str, publish_on: str, goal: str, audience: str | None,
          notes: str | None, pieces: list[dict], scope: dict, query_facts: list[dict], excluded: list[dict],
          all_facts: list[dict], voice: str | None, rules: dict | None, max_chars: int) -> Built:
    day = F.parse_day(publish_on)
    withheld = [{"key": e.get("key"), "reason": e.get("reason")} for e in excluded if e.get("key")]
    seen_withheld = {w["key"] for w in withheld}
    usable = []
    for f in query_facts:
        # Defence in depth: 05 already filtered, but a restricted or stale fact never goes out.
        r = F.classify(f, day, scope)
        if r:
            if f["key"] not in seen_withheld:
                withheld.append({"key": f["key"], "reason": r})
                seen_withheld.add(f["key"])
            continue
        usable.append(f)
    secret = [f for f in all_facts + query_facts if (f.get("sensitivity") or "public") != "public"]

    task_text = " ".join([goal, audience or "", notes or ""] + [f"{p['channel']} {p.get('kind') or ''}" for p in pieces])
    order = sorted(range(len(usable)), key=lambda i: (-relevance(usable[i], task_text, scope), i))
    ranked = [usable[i] for i in order]

    tail = _tail()
    facts_title = "FACTS (use only these. Write the [[slot]] where the value goes, or the value exactly as written):"
    empty = "- (no facts for this task: do not state prices, dates, numbers or certifications)"
    fixed = len(tail) + len(facts_title) + 5 + len(empty)
    head = _head(task_id, fact_set_version, publish_on, goal, audience, notes, pieces, voice, secret, rules)
    if len(head) + fixed > max_chars:       # the voice goes first, the task itself cannot
        head = _head(task_id, fact_set_version, publish_on, goal, audience, notes, pieces, None, secret, rules)
    if len(head) + fixed > max_chars:
        raise PackTooLong(f"goal, notes and pieces alone are longer than the pack limit ({max_chars} characters)")
    budget = max_chars - len(head) - fixed
    lines, sent, slotted, snap, snap_facts = [], [], [], [], []
    for f in ranked:
        internal = (f.get("sensitivity") or "public") == "internal"
        line = internal_line(f) if internal else public_line(f)
        if not internal and any(_carries(line, g) for g in secret):
            withheld.append({"key": f["key"], "reason": "contains_internal_value"})
            continue
        if len(line) + 1 > budget:
            withheld.append({"key": f["key"], "reason": "too_long"})
            continue
        budget -= len(line) + 1
        lines.append(line)
        (slotted if internal else sent).append(f["key"] if internal else line)
        if internal:
            withheld.append({"key": f["key"], "reason": "internal"})
        snap.append({"key": f["key"], "version": f.get("version"), "slot": f"[[{f['key']}]]",
                     "sensitivity": f.get("sensitivity") or "public"})
        snap_facts.append(f)
    body = facts_title + "\n" + ("\n".join(lines) if lines else empty)
    text = f"{head}\n\n{body}\n\n{tail}"
    return Built(text, sent, slotted, withheld, snap, snap_facts)


def _head(task_id, fsv, publish_on, goal, audience, notes, pieces, voice, secret, rules) -> str:
    out = [HEADER_SEP.join([f"Task {task_id}", f"facts {fsv}", f"publish {publish_on}"]), "",
           "Write marketing copy for the business below. Use only the facts listed here.",
           f"GOAL: {' '.join(goal.split())}"]
    if audience:
        out.append(f"AUDIENCE: {' '.join(audience.split())}")
    if notes:
        out.append(f"NOTES: {' '.join(notes.split())}")
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
    return "\n".join(out)


def _tail() -> str:
    return "\n".join([
        "RULES:",
        "1. Copy every [[slot]] you use exactly as written, with both square brackets. A fact without a value "
        "(\"use the slot\") must only ever appear as its slot.",
        "2. Do not invent numbers, prices, dates, awards, certifications or other claims. If you need a fact that "
        "is not listed, write [[missing: what you need]] instead.",
        "3. When you use a fact, put its \"must include\" text in the same piece, word for word.",
        "4. No preamble and no sign-off: start with the first marker line and stop after the last piece.",
    ])
