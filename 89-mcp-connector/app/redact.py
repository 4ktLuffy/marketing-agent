"""Keep internal and restricted fact values out of everything a tool returns.

The task bridge fills slots with real values, including internal ones (the approved partner rate),
because the person in the control room needs the finished text. A tool result goes to a hosted
chatbot, so before any text leaves this connector every value, value text and sentence of a
non-public fact is replaced with its slot `[[key]]`, and quotes from non-public facts are dropped.
If the connector cannot learn which facts are non-public, it returns no text at all (fail closed).
"""
import re
from dataclasses import dataclass, field


def _is_public(f: dict) -> bool:
    return (f.get("sensitivity") or "").strip().lower() == "public"


def _number_forms(v) -> list[str]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return []
    forms = set()
    if float(v).is_integer():
        n = int(v)
        if abs(n) < 10:  # a lone digit is too common to hide without wrecking the text
            return []
        forms |= {str(n), f"{n:,}", f"{n:.2f}", f"{n:,.2f}"}
    else:
        forms |= {f"{v:g}", f"{v:.2f}", f"{v:,.2f}"}
    return sorted(forms, key=len, reverse=True)


@dataclass
class Redactor:
    private_keys: set = field(default_factory=set)
    _subs: list = field(default_factory=list)  # (compiled pattern, replacement)

    @classmethod
    def from_facts(cls, facts: list[dict]) -> "Redactor":
        r = cls()
        literal: list[tuple[str, str]] = []
        for f in facts:
            if not isinstance(f, dict) or not isinstance(f.get("key"), str) or _is_public(f):
                continue
            key = f["key"]
            r.private_keys.add(key)
            slot = f"[[{key}]]"
            for s in (f.get("text"), f.get("value_text")):
                if isinstance(s, str) and len(s.strip()) >= 2:
                    literal.append((s.strip(), slot))
            if isinstance(f.get("value"), str) and len(f["value"].strip()) >= 3:
                literal.append((f["value"].strip(), slot))
            for form in _number_forms(f.get("value")):
                r._subs.append((re.compile(rf"(?<![\w.,]){re.escape(form)}(?![\d])"), slot))
        # longest first, so a fact sentence is replaced whole before its value inside it
        words = [(re.compile(re.escape(s), re.IGNORECASE), slot)
                 for s, slot in sorted(literal, key=lambda x: len(x[0]), reverse=True)]
        r._subs = words + r._subs
        return r

    def text(self, s):
        if not isinstance(s, str):
            return s
        for pat, slot in self._subs:
            s = pat.sub(slot, s)
        return s

    def finding(self, x: dict) -> dict:
        key = x.get("fact_key")
        private = key in self.private_keys
        return {
            "sentence": self.text(x.get("sentence")),
            "label": x.get("label"),
            "blocking": bool(x.get("blocking")),
            "fact_key": key,
            "quote": None if private else self.text(x.get("quote")),
            "why": self.text(x.get("detail")),
        }
