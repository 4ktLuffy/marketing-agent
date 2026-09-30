"""Fact slots: find them in pasted text (in the shapes chatbots rewrite them to) and fill them.

The pack writes `[[weekday-rate]]`. Chatbots come back with `\\[\\[weekday-rate\\]\\]` (escaped
markdown), `[ [weekday-rate] ]`, `{{Weekday_Rate}}`, `⟦weekday rate⟧`, `【weekday-rate】` or even
`[weekday-rate]`. Keys compare case-insensitively with `-`, `_` and space equal. A bare `[k]` is
a slot only when k is a key of the task's snapshot (so `[link]` or `[1]` stay text).

A slot is filled with the snapshot fact's value_text. It is blocked (left as written, with a
slot_blocked finding) when the key is unknown, the fact is restricted, expired or not valid at the
publish date, out of scope, or changed since the pack. Internal facts are filled here: their value
never left the business, only the slot did.
"""
import re
from dataclasses import dataclass
from datetime import date

from . import facts as F

_INNER = r"[^\[\]{}\n\\⟦⟧【】]{1,80}?"
SLOT_RE = re.compile(
    r"\\?\[\s*\\?\[\s*(?P<a>" + _INNER + r")\s*\\?\]\s*\\?\]"      # [[k]]  \[\[k\]\]  [ [k] ]
    r"|\{\{\s*(?P<b>" + _INNER + r")\s*\}\}"                       # {{k}}
    r"|⟦\s*(?P<c>" + _INNER + r")\s*⟧"                              # ⟦k⟧
    r"|【\s*(?P<d>" + _INNER + r")\s*】"                            # 【k】
    r"|(?<![\[\w\\])\\?\[\s*(?P<e>[A-Za-z0-9][A-Za-z0-9 _-]{0,59}?)\s*\\?\](?![\](:])"  # bare [k]
)
MISSING_RE = re.compile(r"^\s*(?:missing|todo|tbc|tbd)\s*[:\-–]\s*(.+)$", re.I)


def norm_key(raw: str) -> str:
    k = raw.strip().lower()
    k = re.sub(r"[\s_]+", "-", k)
    return re.sub(r"-{2,}", "-", k).strip("-")


@dataclass
class Hit:
    start: int
    end: int
    raw: str
    key: str          # normalised
    inner: str        # as written
    bare: bool


def find(text: str, bare_keys: set[str]) -> list[Hit]:
    out = []
    for m in SLOT_RE.finditer(text):
        inner = next(g for g in (m.group("a"), m.group("b"), m.group("c"), m.group("d"), m.group("e")) if g is not None)
        bare = m.group("e") is not None
        key = norm_key(inner)
        if bare and key not in bare_keys:
            continue
        out.append(Hit(m.start(), m.end(), m.group(0), key, inner.strip(), bare))
    return out


def slot_keys(text: str, bare_keys: set[str]) -> list[str]:
    return [h.key for h in find(text, bare_keys)]


REASON_TEXT = {
    "unknown": "no fact has this key",
    "missing": "the chatbot marked a fact as missing",
    "restricted": "restricted fact: never used in copy",
    "expired": "expired at the publish date",
    "not_yet_valid": "not valid yet at the publish date",
    "out_of_scope": "fact is for another scope",
    "scope_unspecified": "fact is only for a narrower scope than this task names",
    "superseded": "fact was superseded",
    "retired": "fact was retired",
    "draft": "fact is not confirmed",
    "changed": "fact changed since the pack was made",
    "no_value": "fact has no value text to insert",
}


@dataclass
class FillResult:
    text: str
    used: list[str]                  # fact keys filled
    blocked: list[dict]              # {key, raw, reason, detail, pos}
    findings: list[dict]


def fill(text: str, snapshot: dict[str, dict], known: dict[str, dict], day: date, scope: dict,
         allow_unsnapshotted: bool = True) -> FillResult:
    """snapshot: key -> fact as it was in the pack (with "version"). known: key -> the fact as 05
    has it now (any status). A slot is filled only when the current fact is usable on `day` in
    `scope` and (for snapshot facts) still the same version."""
    bare_keys = set(snapshot) if snapshot else {k for k, f in known.items() if F.classify(f, day, scope) is None}
    hits = find(text, bare_keys)
    parts, used, blocked, pos, cursor = [], [], [], 0, 0
    for h in hits:
        parts.append(text[cursor:h.start])
        pos += h.start - cursor
        cursor = h.end
        reason, detail, value, key = _decide(h, snapshot, known, day, scope, allow_unsnapshotted)
        if reason is None:
            parts.append(value)
            used.append(key)
            pos += len(value)
        else:
            parts.append(h.raw)
            blocked.append({"key": key, "raw": h.raw, "reason": reason, "detail": detail, "pos": pos})
            pos += len(h.raw)
    parts.append(text[cursor:])
    filled = "".join(parts)
    findings = [{
        "sentence": F.sentence_at(filled, b["pos"]), "label": "slot_blocked",
        "fact_key": b["key"] if b["reason"] not in ("unknown", "missing") else None,
        "quote": None, "blocking": True, "detail": f"{b['raw']}: {b['detail']}",
    } for b in blocked]
    return FillResult(filled, list(dict.fromkeys(used)), blocked, findings)


def _decide(h: Hit, snapshot, known, day, scope, allow_unsnapshotted):
    m = MISSING_RE.match(h.inner)
    if m:
        what = " ".join(m.group(1).split())[:120]
        return "missing", f"missing fact: {what}", None, f"missing:{what}"
    key = h.key
    current = known.get(key)
    snap = snapshot.get(key)
    if current is None and snap is None:
        return "unknown", REASON_TEXT["unknown"], None, key
    if current is None:          # in the pack but 05 no longer has it
        return "retired", REASON_TEXT["retired"], None, key
    reason = F.classify(current, day, scope)
    if reason:
        extra = f" ({F.scope_text(current)})" if reason in ("out_of_scope", "scope_unspecified") else ""
        return reason, REASON_TEXT.get(reason, reason) + extra, None, key
    if snap is not None and snap.get("version") is not None and current.get("version") is not None \
            and snap["version"] != current["version"]:
        return "changed", f"{REASON_TEXT['changed']} (v{snap['version']} -> v{current['version']})", None, key
    if snap is None and not allow_unsnapshotted:
        return "unknown", "not in this task's pack", None, key
    value = ((snap or current).get("value_text") or "").strip()
    if not value:
        return "no_value", REASON_TEXT["no_value"], None, key
    return None, "", value, key
