"""Deterministic lead scoring from a YAML rules file: score 0-100, grade A-D, reasons."""
import os
import re
from pathlib import Path

import yaml

CATEGORIES = ("fit", "intent", "engagement")
TEXT_FIELDS = {"message", "enrichment", "industry", "size"}
VALUE_FIELDS = {"source", "utm_source", "utm_medium", "utm_campaign"}
BOOL_FIELDS = {"has_company_domain", "has_consent"}
DEFAULT_RULES = Path(__file__).resolve().parent.parent / "config" / "scoring.yaml"


class RulesError(ValueError):
    pass


def rules_path() -> str:
    return os.environ.get("SCORING_RULES") or str(DEFAULT_RULES)


def load_rules(path: str | None = None) -> dict:
    """Read and validate the rules file; a broken file is an error, never a silent 0."""
    try:
        raw = yaml.safe_load(Path(path or rules_path()).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RulesError(f"cannot read scoring rules: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("rules"), list):
        raise RulesError("scoring rules need a `rules` list")
    caps = {c: int((raw.get("caps") or {}).get(c, 100)) for c in CATEGORIES}
    grades = {"A": 70, "B": 45, "C": 25} | {k: int(v) for k, v in (raw.get("grades") or {}).items()}
    if not grades["A"] >= grades["B"] >= grades["C"]:
        raise RulesError("grades must satisfy A >= B >= C")
    rules = []
    for i, r in enumerate(raw["rules"]):
        if not isinstance(r, dict):
            raise RulesError(f"rule {i} is not a mapping")
        field, cat = r.get("field"), r.get("category")
        if cat not in CATEGORIES:
            raise RulesError(f"rule {r.get('id', i)}: category must be one of {CATEGORIES}")
        if field not in TEXT_FIELDS | VALUE_FIELDS | BOOL_FIELDS | {"messages"}:
            raise RulesError(f"rule {r.get('id', i)}: unknown field {field!r}")
        if not isinstance(r.get("points"), int) or isinstance(r.get("points"), bool):
            raise RulesError(f"rule {r.get('id', i)}: points must be an integer")
        if field in TEXT_FIELDS | VALUE_FIELDS and not r.get("any"):
            raise RulesError(f"rule {r.get('id', i)}: field {field} needs `any`")
        rules.append({"id": str(r.get("id") or f"rule{i}"), "category": cat, "field": field,
                      "any": [str(k) for k in r.get("any") or []], "min": r.get("min"),
                      "is": r.get("is", True), "points": r["points"],
                      "reason": str(r.get("reason") or r.get("id") or f"rule {i}")})
    return {"caps": caps, "grades": grades, "rules": rules}


def _keyword_hits(text: str, keywords: list[str]) -> list[str]:
    hits = []
    for k in keywords:
        # Whole words: "cv" must not match "cvs", "call" must not match "recall".
        if re.search(r"(?<![\w-])" + re.escape(k.lower()) + r"(?![\w-])", text):
            hits.append(k)
    return hits


def grade_for(score: int, grades: dict) -> str:
    if score >= grades["A"]:
        return "A"
    if score >= grades["B"]:
        return "B"
    if score >= grades["C"]:
        return "C"
    return "D"


def score_lead(facts: dict, rules: dict | None = None) -> dict:
    """facts: message, enrichment, industry, size (text); source, utm_* (str); messages (int);
    has_company_domain, has_consent (bool). Returns {score, grade, reasons, categories}."""
    rules = rules or load_rules()
    totals = {c: 0 for c in CATEGORIES}
    reasons = []
    for r in rules["rules"]:
        f = r["field"]
        matched, detail = False, None
        if f in TEXT_FIELDS:
            hits = _keyword_hits((facts.get(f) or "").lower(), r["any"])
            matched, detail = bool(hits), hits
        elif f in VALUE_FIELDS:
            v = facts.get(f) or []
            values = {str(x).strip().lower() for x in (v if isinstance(v, (list, tuple, set)) else [v])}
            hits = sorted(values & {a.lower() for a in r["any"]})
            matched, detail = bool(hits), hits
        elif f in BOOL_FIELDS:
            matched = bool(facts.get(f)) is bool(r["is"])
        elif f == "messages":
            matched = int(facts.get("messages") or 0) >= int(r["min"] or 1)
        if matched:
            totals[r["category"]] += r["points"]
            reasons.append({"rule": r["id"], "category": r["category"], "points": r["points"],
                            "reason": r["reason"], "matched": detail})
    capped = {c: max(min(totals[c], rules["caps"][c]), -100) for c in CATEGORIES}
    score = max(0, min(100, sum(capped.values())))
    return {"score": score, "grade": grade_for(score, rules["grades"]), "reasons": reasons,
            "categories": capped}
