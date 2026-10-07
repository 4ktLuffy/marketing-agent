"""Zero-AI post templates: a price list, a rate card or a facts digest rendered from the confirmed facts.

Pure functions, no network and no model. The caller (main.py) queries the facts valid on the publish
date in the scope; this module keeps only PUBLIC ones, writes the lines from each fact's own
`value_text`, states a disclosure shared by every line once at the top, adds the owner-confirmed
kit disclosures and fits the channel's limit. Internal and restricted values never reach the text
and slots are never used. The rendered text then goes through the same checks and approval as any
pasted piece."""
import re

from . import channels as CH
from . import facts as F

TEMPLATES = ("price_list", "rate_card", "facts_digest")
TITLES = {"price_list": "Price list", "rate_card": "Rate card", "facts_digest": "Good to know"}
# used only when the platform rules (14) do not give a limit for the channel
DEFAULT_LIMITS = {"sms": 160, "telegram": 4096, "x": 280, "linkedin": 3000, "instagram": 2200,
                  "whatsapp": 4096, "threads": 500, "mastodon": 500, "google_business": 1500}
GUEST_KEYS = frozenset({"guests", "guest", "occupancy", "adults", "persons", "people", "pax"})
NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
NOTE_WORDS = ("recommendation", "owner must confirm", "owner to confirm", "hold for human review")
UNIT_LEAD = ("per", "a", "each", "for", "/")


def _clean(s) -> str:
    return " ".join(str(s or "").split())


def _natural(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s.lower())]


def _num(f: dict) -> float:
    try:
        return float(f.get("value"))
    except (TypeError, ValueError):
        return 0.0


def _disclosures(f: dict) -> list[str]:
    return [d for d in (_clean(x) for x in f.get("required_disclosures") or []) if d]


def _guest_cond(f: dict):
    hit = None
    for c in f.get("conditions") or []:
        if isinstance(c, dict) and str(c.get("key") or "").lower() in GUEST_KEYS:
            if hit or str(c.get("op") or "=") not in ("=", "=="):
                return None
            hit = c
    return hit


def _guest_label(v) -> str:
    try:
        n = int(float(v))
        return f"{NUMBER_WORDS[n]} guest{'' if n == 1 else 's'}" if 0 < n < len(NUMBER_WORDS) else f"{n} guests"
    except (TypeError, ValueError):
        return f"{v} guests"


def _other_conditions(f: dict) -> str:
    out = []
    for c in f.get("conditions") or []:
        if not isinstance(c, dict) or str(c.get("key") or "").lower() in GUEST_KEYS:
            continue
        v = c.get("value")
        out.append(f"{c.get('key')} {c.get('op') or '='} " + ("/".join(map(str, v)) if isinstance(v, list) else str(v)))
    return ", ".join(out)


def _strip(vt: str, shared: list[str]) -> str:
    """The value without the disclosures that are stated once at the top."""
    out = vt
    for d in shared:
        out = re.sub(rf"\s*,?\s*\b{re.escape(d)}\b", "", out, flags=re.I)
    out = out.strip(" ,;")
    return out or vt


def _header(shared: list[str], template: str) -> str:
    if not shared:
        return ""
    if len(shared) == 1 and shared[0].lower().startswith("per "):
        return f"{'Rates' if template == 'rate_card' else 'Prices'} {shared[0]}:"
    s = ", ".join(shared)
    return s[0].upper() + s[1:] + ":"


def _split_heads(values: list[str]):
    """["$125 per room per night", "$131 per room per night"] -> (["$125", "$131"], "per room per night")"""
    parts = [v.split() for v in values]
    if any(not p for p in parts):
        return values, ""
    n = 0
    while all(len(p) > n + 1 for p in parts) and len({p[-1 - n].lower() for p in parts}) == 1:
        n += 1
    tail = parts[0][len(parts[0]) - n:] if n else []
    i = next((k for k, t in enumerate(tail) if t.lower() in UNIT_LEAD), None)
    if i is None:
        return values, ""
    keep = len(tail) - i
    return [" ".join(p[:len(p) - keep]) for p in parts], " ".join(tail[i:])


# ---------- selection


def usable(facts: list[dict], day, scope: dict, subjects: list[str] | None = None) -> list[dict]:
    """Public facts valid on the day in the scope, optionally those whose subject names one of `subjects`."""
    want = [s.lower().strip() for s in subjects or [] if s and s.strip()]
    out = []
    for f in facts:
        if not isinstance(f, dict) or not f.get("key"):
            continue
        if (f.get("sensitivity") or "public") != "public" or F.classify(f, day, scope) is not None:
            continue
        if want and not any(w in F.subject_ref(f).lower() for w in want):
            continue
        out.append(f)
    return out


def _is_price(f: dict) -> bool:
    return str(f.get("fact_type") or "").lower() in ("price", "rate") and bool(_clean(f.get("value_text")))


def _digest_text(f: dict) -> str | None:
    vt = _clean(f.get("value_text"))
    if vt:
        return vt
    if F.phrases(f.get("forbidden_phrasing")):      # a guard-rail fact, not copy
        return None
    return _clean(f.get("text")) or None


# ---------- lines


def _price_lines(fs: list[dict], shared: list[str], group: bool, bullet: str):
    """[(sort key, line, [facts used])] for price facts."""
    lines, used = [], set()
    if group:
        buckets: dict[tuple, list[dict]] = {}
        for f in fs:
            g = _guest_cond(f)
            if g is not None and F.subject_ref(f):
                key = (F.subject_ref(f).lower(), _other_conditions(f), tuple(_disclosures(f)),
                       f.get("attribute"), f.get("basis"), f.get("currency"), f.get("unit"))
                buckets.setdefault(key, []).append(f)
        for members in buckets.values():
            labels = [_guest_label(_guest_cond(m)["value"]) for m in members]
            if len(members) < 2 or len(set(labels)) != len(labels):
                continue
            members.sort(key=lambda m: _num({"value": _guest_cond(m)["value"]}))
            labels = [_guest_label(_guest_cond(m)["value"]) for m in members]
            heads, tail = _split_heads([_strip(_clean(m.get("value_text")), shared) for m in members])
            body = " / ".join(f"{h} {lab}" for h, lab in zip(heads, labels))
            extra = [d for d in _disclosures(members[0]) if d not in shared]
            cond = _other_conditions(members[0])
            line = f"{bullet}{F.subject_ref(members[0])}: {body}" + (f", {tail}" if tail else "")
            line += f" ({cond})" if cond else ""
            line += f" ({'; '.join(extra)})" if extra else ""
            lines.append((_natural(F.subject_ref(members[0])) + [0.0], line, [m["key"] for m in members]))
            used.update(m["key"] for m in members)
    for f in fs:
        if f["key"] in used:
            continue
        ref = F.subject_ref(f) or _clean(f.get("attribute")) or f["key"]
        g = _guest_cond(f)
        label = ref + (f" ({_guest_label(g['value'])})" if g else "")
        cond = _other_conditions(f)
        extra = [d for d in _disclosures(f) if d not in shared]
        line = f"{bullet}{label}: {_strip(_clean(f.get('value_text')), shared)}"
        line += f" ({cond})" if cond else ""
        line += f" ({'; '.join(extra)})" if extra else ""
        lines.append((_natural(ref) + [_num(f)], line, [f["key"]]))
    lines.sort(key=lambda t: t[0])
    return lines


def _digest_lines(fs: list[dict], bullet: str):
    by_subject: dict[str, list[str]] = {}
    keys: dict[str, list[str]] = {}
    for f in fs:
        t = _digest_text(f)
        if not t:
            continue
        extra = _disclosures(f)
        ref = F.subject_ref(f)
        txt = t if not extra or all(d.lower() in t.lower() for d in extra) else f"{t} ({'; '.join(extra)})"
        by_subject.setdefault(ref, []).append(txt)
        keys.setdefault(ref, []).append(f["key"])
    out = []
    for ref in sorted(by_subject, key=lambda r: _natural(r)):
        for txt, k in zip(by_subject[ref], keys[ref]):
            line = f"{bullet}{ref}: {txt}" if ref and ref.lower() not in txt.lower() else f"{bullet}{txt}"
            out.append((_natural(ref) + [txt.lower()], line, [k]))
    return out


# ---------- kit rules


def kit_disclosures(rules: list[dict] | None) -> tuple[list[str], list[str]]:
    """(required disclosure phrases, forbidden phrases) of the owner-confirmed starter-kit rules."""
    must, never = [], []
    for r in rules or []:
        if not isinstance(r, dict) or r.get("status") != "active":
            continue
        phrase = _clean(r.get("phrase"))
        if not phrase:
            continue
        if r.get("kind") == "required_disclosure":
            why = str(r.get("why") or "").lower()
            if any(w in why or w in phrase.lower() for w in NOTE_WORDS):
                continue
            must.append(phrase)
        elif r.get("kind") == "forbidden_phrase":
            never.append(phrase)
    return list(dict.fromkeys(must)), list(dict.fromkeys(never))


# ---------- render


def _plain(s: str, no_md: bool) -> str:
    s = _clean(s)
    return re.sub(r"^#+\s*", "", s.replace("**", "").replace("__", "")) if no_md else s


def render(template: str, channel: str, facts: list[dict], day, scope: dict, kit_rules: list[dict] | None = None,
           limit: int | None = None, title: str | None = None, intro: str | None = None,
           subjects: list[str] | None = None, group_by: str | None = None, query_notes: list[str] | None = None) -> dict:
    """{"text", "parts", "facts_used", "chars", "notes"}. `parts` has one text per message when the
    channel's limit made the list split (text is then the parts joined by a blank line)."""
    notes = list(query_notes or [])
    canon = CH.canonical(channel)
    no_md = CH.no_markdown(channel)
    bullet = "• " if no_md else "- "
    limit = limit or DEFAULT_LIMITS.get(canon)
    fs = usable(facts, day, scope, subjects)
    if template == "facts_digest":
        rows = _digest_lines(fs, bullet)
        shared: list[str] = []
    else:
        fs = [f for f in fs if _is_price(f)]
        shared = [d for d in _disclosures(fs[0]) if all(d in _disclosures(f) for f in fs)] if fs else []
        rows = _price_lines(fs, shared, template == "rate_card" or group_by == "subject", bullet)
    if not rows:
        what = {"price_list": "price", "rate_card": "rate", "facts_digest": "public"}[template]
        notes.append(f"no {what} facts are valid on {day.isoformat()} in this scope"
                     + (" for those subjects" if subjects else "") + "; nothing was rendered")
        return {"text": "", "parts": [], "facts_used": [], "chars": 0, "notes": notes}

    must, never = kit_disclosures(kit_rules)
    head_title = _plain(title if title is not None else TITLES[template], no_md)
    head_intro = _plain(intro or "", no_md)
    header = _header(shared, template)
    body_text = "\n".join(r[1] for r in rows)
    kit = [m for m in must if m.lower() not in body_text.lower()]
    if template != "facts_digest":
        notes.append(f"{len(rows)} lines from {sum(len(r[2]) for r in rows)} facts")

    forbidden = never + [p for f in fs for p in F.phrases(f.get("forbidden_phrasing"))]
    low = (body_text + " " + head_title + " " + head_intro).lower()
    for p in dict.fromkeys(forbidden):
        if p.lower() in low:
            notes.append(f'warning: the text contains the forbidden phrase "{p}"; the checker will block it')

    def assemble(chosen, cont=False):
        top = []
        if head_title:
            top.append(head_title + (" (continued)" if cont else ""))
        if head_intro and not cont:
            top.append(head_intro)
        block = ([header] if header else []) + [r[1] for r in chosen]
        text = "\n".join(top + ([""] if top else []) + block)
        return text + ("\n\n" + "\n".join(kit) if kit else "")

    def fits(t):
        return not limit or len(t) <= limit

    full = assemble(rows)
    used_all = [k for r in rows for k in r[2]]
    if fits(full) and canon != "sms":
        return {"text": full, "parts": [full], "facts_used": used_all, "chars": len(full), "notes": notes}

    if canon == "sms":
        # a compact single message: "Prices per crate: Brand A 33cl: 2,080 kora; ..." as many lines as fit
        compact_rows = [r[1][len(bullet):] for r in rows]
        lead = (header + " ") if header else ""
        tail = ("\n" + "\n".join(kit)) if kit else ""
        chosen, used = [], []
        for r, c in zip(rows, compact_rows):
            cand = lead + "; ".join(chosen + [c]) + tail
            if len(cand) > limit:
                break
            chosen.append(c)
            used += r[2]
        if not chosen:
            notes.append(f"nothing fits in {limit} characters with the required text; use another channel")
            return {"text": "", "parts": [], "facts_used": [], "chars": 0, "notes": notes}
        text = lead + "; ".join(chosen) + tail
        left = [r[1][len(bullet):].split(":")[0] for r in rows[len(chosen):]]
        if left:
            notes.append(f"compact version for {limit} characters: {len(left)} of {len(rows)} lines left out ("
                         + ", ".join(left)[:300] + "); send them as a second message or use another channel")
        else:
            notes.append(f"compact version for {limit} characters")
        return {"text": text, "parts": [text], "facts_used": used, "chars": len(text), "notes": notes}

    # a long list: split at line ends, each message with the header and the required text
    parts, cur, used_parts = [], [], []
    for r in rows:
        trial = assemble(cur + [r], cont=bool(parts))
        if cur and not fits(trial):
            parts.append(assemble(cur, cont=bool(parts)))
            cur = []
        cur.append(r)
    parts.append(assemble(cur, cont=bool(parts)))
    if any(not fits(p) for p in parts):
        notes.append(f"a single line is longer than the {limit}-character limit; shorten the title or intro")
    notes.append(f"over the {limit}-character limit: split into {len(parts)} messages, each with its header")
    text = "\n\n".join(parts)
    return {"text": text, "parts": parts, "facts_used": used_all, "chars": len(text), "notes": notes}
