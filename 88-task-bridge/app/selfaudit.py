"""The chatbot's own audit: after its pieces it lists every sentence it wrote that the FACTS do not back
("NOT IN FACTS:"). The rules catch wrong values; only the writer knows what it made up in its own words
("There's always tea waiting for visitors"). The list is cut off the pasted answer, never published,
and each listed sentence found in a piece is a blocking no_source finding.
"""
import os
import re

RULE = '5. Then write "NOT IN FACTS:" and copy, one per line, each sentence you wrote that the FACTS do not back (or "none").'
HEAD = re.compile(r"^\s*(?:\*\*|__|#{1,6}\s*)?\s*not\s+in\s+(?:the\s+)?facts\s*(?:\*\*|__)?\s*[:\-–—]?\s*(?:\*\*|__)?\s*(?P<rest>.*)$",
                  re.I)
SLOT = re.compile(r"\[\[[^\]]*\]\]|\{\{[^}]*\}\}")
_BULLET = re.compile(r"^\s*(?:[-•*–—]|\d{1,2}[.)])\s*")
_NONE = re.compile(r"^\s*(?:none|nothing|n/?a|no sentences?|-)\s*[.!]?\s*$", re.I)


def on() -> bool:
    return os.environ.get("PACK_SELF_AUDIT", "off").lower() in ("on", "1", "true", "yes")


def _clean(line: str) -> str:
    line = _BULLET.sub("", line).strip().strip('"“”‘’\'*_').strip()
    return " ".join(line.split())


def extract(text: str) -> tuple[str, list[str]]:
    """(the answer without its audit section, the sentences it listed)."""
    lines = (text or "").split("\n")
    at = None
    for i in range(len(lines) - 1, -1, -1):
        if HEAD.match(lines[i]):
            at = i
            break
    if at is None:
        return text, []
    listed = []
    for raw in [HEAD.match(lines[at]).group("rest")] + lines[at + 1:]:
        c = _clean(raw)
        if c and not _NONE.match(c):
            listed.append(c)
    return "\n".join(lines[:at]).rstrip(), listed


def _norm(s: str) -> str:
    return " ".join(re.findall(r"[\w$£€%.,'-]+", s.lower())).strip(" .,")


def findings(piece_text: str, listed: list[str], sentences: list[str]) -> list[dict]:
    """A blocking no_source finding on each piece sentence the chatbot listed (whole or in part)."""
    out, done = [], set()
    for item in listed:
        # a slot the chatbot copied ("[[partner-rate]]") is a value in the checked text: match the rest
        parts = [_norm(x) for x in SLOT.split(item)]
        parts = [x for x in parts if len(x) >= 12]
        if not parts:
            continue
        for s in sentences:
            ns = _norm(s)
            if len(ns) >= 12 and s not in done and (all(x in ns for x in parts) or (len(parts) == 1 and ns in parts[0])):
                done.add(s)
                out.append({"sentence": s, "label": "no_source", "fact_key": None, "quote": None, "blocking": True,
                            "detail": "the chatbot itself listed this as not backed by the facts: remove it, or add it "
                                      "as a fact if it's true"})
    return out
