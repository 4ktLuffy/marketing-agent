"""Phrases that make copy read like generic AI text. LinkedIn and readers penalise it; a free chatbot
writes it by default. Warnings only (never blocks): the reviewer decides. A brand can switch the
lint off (`ai_sheen: off` in brand.yaml) or add its own phrases (`ai_sheen_extra`)."""
import re

_PHRASES = [
    (r"\bdelv(?:e|es|ing)\b", "delve"),
    (r"\btapestry\b", "tapestry"),
    (r"\belevat(?:e|es|ing) (?:your|the|our)\b", "elevate your"),
    (r"\bunlock(?:s|ing)? (?:the power|the potential|your|new)\b", "unlock the …"),
    (r"\bunleash(?:es|ing)?\b", "unleash"),
    (r"\bharness(?:es|ing)? the power\b", "harness the power"),
    (r"\bseamless(?:ly)?\b", "seamless"),
    (r"\bgame[- ]chang(?:er|ing)\b", "game-changer"),
    (r"\bcutting[- ]edge\b", "cutting-edge"),
    (r"\brevolutioni[sz](?:e|es|ing)\b", "revolutionise"),
    (r"\bin today'?s (?:fast[- ]paced|digital|ever[- ]changing|modern) (?:world|landscape|age)\b", "in today's fast-paced world"),
    (r"\blook no further\b", "look no further"),
    (r"\bembark on (?:a|an|your) (?:journey|adventure)\b", "embark on a journey"),
    (r"\b(?:is|stands as) a testament to\b", "a testament to"),
    (r"\bnavigat(?:e|ing) the (?:complexities|landscape|world)\b", "navigate the landscape"),
    (r"\bwe(?:'re| are) (?:thrilled|excited|delighted) to announce\b", "we're thrilled to announce"),
    (r"\bit'?s not just (?:a|an|about)\b", "it's not just …"),
    (r"\bnot just (?:a|an) [\w-]+(?: [\w-]+)?,? (?:it'?s|but) (?:a|an)\b", "not just X, it's Y"),
    (r"\bimagine a world\b", "imagine a world"),
    (r"\bpicture this\b", "picture this"),
    (r"\bnestled (?:in|among|between|amid)\b", "nestled in"),
    (r"\bhidden gem\b", "hidden gem"),
    (r"\bwhether you'?re an? [\w -]{2,30} or an? [\w -]{2,30}\b", "whether you're X or Y"),
]
_COMPILED = [(re.compile(p, re.I), label) for p, label in _PHRASES]
EM_DASH = re.compile(r"\s—\s|—")
MAX_FINDINGS = 3


def lint(text: str, extra: list[str] | None = None) -> list[dict]:
    """Up to MAX_FINDINGS warnings: {rule, detail, severity, match}."""
    out, seen = [], set()
    pats = _COMPILED + [(re.compile(r"\b" + re.escape(x.strip()) + r"\b", re.I), x.strip())
                        for x in (extra or []) if x and x.strip()]
    for rx, label in pats:
        m = rx.search(text)
        if m and label not in seen:
            seen.add(label)
            out.append({"rule": "ai_sheen", "severity": "warn", "match": m.group(0),
                        "detail": f"'{m.group(0)}' reads like generic AI text; say it plainly in your own words"})
        if len(out) >= MAX_FINDINGS:
            return out
    dashes = len(EM_DASH.findall(text))
    words = max(1, len(text.split()))
    if dashes >= 3 and dashes * 60 > words:
        out.append({"rule": "ai_sheen", "severity": "warn", "match": "—",
                    "detail": f"{dashes} em dashes in {words} words; readers see heavy em dashes as AI style"})
    return out[:MAX_FINDINGS]
