#!/usr/bin/env python3
"""Validate the held-out company fixtures (companies-v6 copy: only the must_not_flag count rule differs) against _dev/phase1-contracts.md (sections 1, 4, 5).

Checks the fixtures themselves, not any checker: facts parse with the full Fact v2 shape and allowed
enums; every draft has an expected file; every `contains` / `must_not_flag` substring really occurs in
its draft; every `fact_key` exists; and the traps are real (the expired fact is invalid at publish_on,
the wrong-scope fact is out of the task scope, the missing disclosure is really absent, clean drafts
only use slots that are valid, in scope and public).

Usage: python validate_fixtures.py [companies_dir]     (stdlib + PyYAML; exit 1 on any problem)
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

import yaml

TODAY = dt.date(2026, 9, 29)
DIMS = ["sites", "regions", "channels", "segments", "plan_tiers", "variants"]
FACT_KEYS = ["key", "subject", "fact_type", "attribute", "value", "unit", "currency", "value_text", "basis",
             "conditions", "scope", "valid_from", "valid_to", "review_by", "source", "claim_class",
             "requires_evidence", "evidence_ref", "consent_ref", "required_disclosures", "allowed_phrasing",
             "forbidden_phrasing", "owner", "risk", "sensitivity", "status", "supersedes_key", "text"]
SUBJECT_KINDS = {"business", "site", "product", "variant", "plan", "service", "package", "menu_item", "person",
                 "policy", "offer"}
FACT_TYPES = {"price", "spec", "availability", "hours", "inclusion", "policy", "certification", "credential",
              "claim", "testimonial", "result", "event", "contact"}
BASES = {"per_unit", "per_person", "per_room", "per_night", "per_seat", "per_month", "per_year", "flat", None}
SOURCE_KINDS = {"doc", "url", "certificate", "owner_statement"}
CLAIM_CLASSES = {"none", "comparative", "regulated_health", "regulated_food", "safety_cert", "origin",
                 "price_reference", "security", "ai_capability", "result"}
SENSITIVITY = {"public", "internal", "restricted"}
STATUSES = {"active", "expired"}  # contracts §5: fixtures are active or expired
LABELS = {"match", "review", "conflict_or_expired", "wrong_scope", "no_source", "missing_disclosure",
          "forbidden_phrase", "slot_blocked"}
BLOCKING = {"conflict_or_expired", "wrong_scope", "missing_disclosure", "forbidden_phrase", "slot_blocked",
            "no_source"}
DRAFTS = ["01-clean", "02-paraphrase", "03-expired", "04-wrong-scope", "05-missing-disclosure", "06-invented"]
KEY_RE = re.compile(r"^[a-z0-9-]{2,60}$")
SLOT_RES = [re.compile(r"\\\[\\\[([a-z0-9-]+)\\\]\\\]"), re.compile(r"(?<!\\)\[\[([a-z0-9-]+)\]\]"),
            re.compile(r"\{\{([a-z0-9-]+)\}\}")]


def _date(v):
    if v is None:
        return None
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


def valid_on(f, day):
    vf, vt = _date(f["valid_from"]), _date(f["valid_to"])
    return f["status"] == "active" and (vf is None or vf <= day) and (vt is None or day <= vt)


def scope_ok(f, task_scope):
    """Contracts §1 scope match: returns 'ok' | 'out_of_scope' | 'scope_unspecified'."""
    for d in DIMS:
        fv = f["scope"][d]
        if not fv:
            continue
        tv = task_scope.get(d) or []
        if not tv:
            return "scope_unspecified"
        if not all(t in fv for t in tv):
            return "out_of_scope"
    return "ok"


def slots(text):
    found = set()
    for rx in SLOT_RES:
        found.update(rx.findall(text))
    return found


def check_company(cdir: Path, errs: list[str]) -> dict:
    name = cdir.name
    e = lambda m: errs.append(f"{name}: {m}")  # noqa: E731
    facts_doc = yaml.safe_load((cdir / "facts.yaml").read_text())
    task = yaml.safe_load((cdir / "task.yaml").read_text())
    comp = facts_doc.get("company") or {}
    for k in ("name", "type", "today"):
        if k not in comp:
            e(f"company.{k} missing")
    if str(comp.get("today")) != "2026-09-29":
        e("company.today must be 2026-09-29")
    facts = facts_doc.get("facts") or []
    if not 7 <= len(facts) <= 10:
        e(f"{len(facts)} facts (want 7-10)")
    by_key = {}
    for i, f in enumerate(facts):
        fk = f.get("key", f"#{i}")
        missing = [k for k in FACT_KEYS if k not in f]
        if missing:
            e(f"fact {fk} missing keys {missing}")
            continue
        if not KEY_RE.match(str(fk)):
            e(f"fact key {fk!r} not a slug")
        if fk in by_key:
            e(f"duplicate fact key {fk}")
        by_key[fk] = f
        checks = [("subject.kind", f["subject"].get("kind"), SUBJECT_KINDS), ("fact_type", f["fact_type"], FACT_TYPES),
                  ("basis", f["basis"], BASES), ("source.kind", f["source"].get("kind"), SOURCE_KINDS),
                  ("claim_class", f["claim_class"], CLAIM_CLASSES), ("sensitivity", f["sensitivity"], SENSITIVITY),
                  ("status", f["status"], STATUSES)]
        for label, val, allowed in checks:
            if val not in allowed:
                e(f"fact {fk}: {label}={val!r} not allowed")
        if not f["subject"].get("ref") or not f["source"].get("ref"):
            e(f"fact {fk}: subject.ref / source.ref empty")
        if set(f["scope"]) != set(DIMS) or not all(isinstance(f["scope"][d], list) for d in DIMS):
            e(f"fact {fk}: scope must have exactly the six list dimensions")
        if isinstance(f.get("value_text"), str) and len(f["value_text"]) > 200:
            e(f"fact {fk}: value_text over 200 characters (05 refuses it)")
        for c in f["conditions"]:
            if set(c) != {"key", "op", "value"}:
                e(f"fact {fk}: condition {c} needs key/op/value")
            elif c["op"] not in ("=", "!=", "<", "<=", ">", ">=", "in", "not_in"):
                e(f"fact {fk}: condition op {c['op']!r} is not in the contract (= != < <= > >= in not_in)")
        for k in ("required_disclosures", "allowed_phrasing", "forbidden_phrasing"):
            if not isinstance(f[k], list):
                e(f"fact {fk}: {k} must be a list")
        for k in ("text", "value_text"):
            if not (isinstance(f[k], str) and f[k].strip()):
                e(f"fact {fk}: {k} empty")
        try:
            _date(f["valid_from"]), _date(f["valid_to"]), _date(f["review_by"])
        except ValueError as ex:
            e(f"fact {fk}: bad date {ex}")
        if f["supersedes_key"] and f["supersedes_key"] not in {x.get("key") for x in facts}:
            e(f"fact {fk}: supersedes unknown key {f['supersedes_key']}")

    # task
    for k in ("goal", "pieces", "scope", "publish_on"):
        if k not in task:
            e(f"task.{k} missing")
    publish = _date(task["publish_on"])
    if not (publish.year == 2026 and publish.month == 10):
        e("publish_on must be in October 2026")
    if not 2 <= len(task["pieces"]) <= 3:
        e("task needs 2-3 pieces")
    for p in task["pieces"]:
        if not {"key", "channel", "kind"} <= set(p):
            e(f"piece {p} needs key/channel/kind")
    tscope = task["scope"]
    if set(tscope) != set(DIMS):
        e("task.scope must have the six dimensions")

    # company-level trap coverage
    sens = {f["sensitivity"] for f in by_key.values()}
    for s in ("internal", "restricted"):
        if s not in sens:
            e(f"no {s} fact")
    if not any(not valid_on(f, publish) and (f["status"] == "expired" or _date(f["valid_to"])) for f in by_key.values()):
        e("no expired fact")
    if not any(valid_on(f, TODAY) and not valid_on(f, publish) for f in by_key.values()):
        e("no fact that is valid today but expired by publish_on")
    if not any(f["sensitivity"] == "public" and valid_on(f, publish) and scope_ok(f, tscope) == "out_of_scope"
               for f in by_key.values()):
        e("no scope trap (a valid public fact that is out of the task scope)")

    # drafts
    ddir = cdir / "drafts"
    txts = sorted(p.stem for p in ddir.glob("*.txt"))
    if txts != DRAFTS:
        e(f"drafts are {txts}, want {DRAFTS}")
    secret_values = [f["value_text"] for f in by_key.values() if f["sensitivity"] != "public"]
    labels_seen = {}
    for stem in txts:
        draft = (ddir / f"{stem}.txt").read_text()
        exp_path = ddir / f"{stem}.expected.yaml"
        if not exp_path.exists():
            e(f"{stem}: no expected file")
            continue
        raw = exp_path.read_text()
        if not re.search(r"^#\s*WHY:", raw, re.M):
            e(f"{stem}: expected file has no '# WHY:' comment")
        exp = yaml.safe_load(raw)
        if set(exp) != {"blocked", "findings", "must_not_flag"}:
            e(f"{stem}: expected keys {sorted(exp)}")
            continue
        for sv in secret_values:
            if sv in draft:
                e(f"{stem}: internal/restricted value {sv!r} appears in draft")
        blocking = False
        for fd in exp["findings"]:
            lab, key, sub = fd.get("label"), fd.get("fact_key"), fd.get("contains")
            if lab not in LABELS:
                e(f"{stem}: bad label {lab}")
            blocking |= lab in BLOCKING
            if not sub or sub not in draft:
                e(f"{stem}: contains {sub!r} not in draft")
            if key is not None and key not in by_key:
                e(f"{stem}: fact_key {key} not in facts.yaml")
            labels_seen.setdefault(stem, set()).add(lab)
            if key in by_key:
                f = by_key[key]
                if lab == "conflict_or_expired" and valid_on(f, publish) and str(f["value_text"]) in sub:
                    e(f"{stem}: {key} is still valid at publish_on yet labelled expired")
                if lab == "wrong_scope" and scope_ok(f, tscope) == "ok":
                    e(f"{stem}: {key} is in scope yet labelled wrong_scope")
                if lab == "missing_disclosure":
                    if not f["required_disclosures"]:
                        e(f"{stem}: {key} has no required_disclosures")
                    if any(d.lower() in draft.lower() for d in f["required_disclosures"]):
                        e(f"{stem}: disclosure for {key} is actually present")
                if lab == "forbidden_phrase" and not any(p.lower() in sub.lower() for p in f["forbidden_phrasing"]):
                    e(f"{stem}: no forbidden phrase of {key} inside {sub!r}")
        if bool(exp["blocked"]) != blocking:
            e(f"{stem}: blocked={exp['blocked']} but blocking findings={blocking}")
        mnf = exp["must_not_flag"]
        # companies-v6: negative controls are the point of this set, so 01/02 list at least 6 true sentences
        if stem in ("01-clean", "02-paraphrase") and len(mnf) < 6:
            e(f"{stem}: must_not_flag should list at least 6 sentences")
        for s in mnf:
            if s not in draft:
                e(f"{stem}: must_not_flag {s!r} not in draft")
        if stem in ("01-clean", "02-paraphrase"):
            if exp["blocked"] or any(fd["label"] in BLOCKING for fd in exp["findings"]):
                e(f"{stem}: negative control must not block")
            for sk in slots(draft):
                f = by_key.get(sk)
                if f is None:
                    e(f"{stem}: slot [[{sk}]] is not a fact key")
                elif not (valid_on(f, publish) and scope_ok(f, tscope) == "ok" and f["sensitivity"] == "public"):
                    e(f"{stem}: slot [[{sk}]] is not valid/in-scope/public at publish_on")
                else:
                    for d in f["required_disclosures"]:
                        if d.lower() not in draft.lower() and d.lower() not in f["value_text"].lower():
                            e(f"{stem}: slot [[{sk}]] used without disclosure {d!r}")
    want = {"03-expired": "conflict_or_expired", "04-wrong-scope": "wrong_scope",
            "05-missing-disclosure": "missing_disclosure"}
    for stem, lab in want.items():
        if lab not in labels_seen.get(stem, set()):
            e(f"{stem}: expected at least one {lab} finding")
    if not labels_seen.get("06-invented", set()) & {"no_source", "conflict_or_expired"}:
        e("06-invented: expected a no_source or conflict_or_expired finding")
    return {"facts": len(facts), "drafts": len(txts)}


def main(argv):
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent
    companies = sorted(p for p in root.iterdir() if p.is_dir() and (p / "facts.yaml").exists())
    errs: list[str] = []
    totals = {"facts": 0, "drafts": 0}
    for c in companies:
        try:
            r = check_company(c, errs)
            totals = {k: totals[k] + r[k] for k in totals}
        except Exception as ex:  # a parse error or a missing file is a fixture error, not a crash
            errs.append(f"{c.name}: {type(ex).__name__}: {ex}")
    for m in errs:
        print("FAIL", m)
    print(f"{len(companies)} companies, {totals['facts']} facts, {totals['drafts']} drafts, {len(errs)} problems")
    return 1 if errs or not companies else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
