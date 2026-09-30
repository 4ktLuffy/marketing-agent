"""Zero-model end-to-end eval of the Phase 1 task bridge (plan: Verification step 2).

For every company in `cases/companies/*` it starts fresh local services with uvicorn on free
ports and temp data (05 brand service, 14 platform rules, 19 content calendar, 88 task bridge),
imports the company's facts, makes one task per draft, pastes and submits each draft, scores the
findings against `drafts/NN-*.expected.yaml` (contracts §5), and walks the `01-clean` task through
approve (right hash, missing hash, stale hash), export, retire a used fact, reconcile, export again.

It only measures: nothing in 05/14/19/88 or the fixtures is changed. No model, no Docker, no
network beyond 127.0.0.1.

    cd 23-eval-suite
    python -m evalsuite.task_bridge                      # all companies
    python -m evalsuite.task_bridge --only kestrel-valves
    python -m evalsuite.task_bridge --out-stem task-bridge-zero-model-2026-09-29

Optional model check (plan: Verification step 3), off unless asked for: `--claims-gateway URL`
also starts 44 (claim checker) per company against that already-running gateway (03, e.g. on
local Ollama) and runs 88 with MODEL_CHECK=auto, CLAIMS_URL=44. Without it nothing changes.

    python -m evalsuite.task_bridge --cases companies-v3 --claims-gateway http://127.0.0.1:8147 \
        --verifier-model mkt-writer --out-stem task-bridge-model-check-v3
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SUITE = HERE.parent
REPO = SUITE.parent
COMPANIES = SUITE / "cases" / "companies"
RESULTS = SUITE / "results"

SERVICES = {"brand": "05-brand-service", "rules": "14-platform-rules",
            "calendar": "19-content-calendar", "bridge": "88-task-bridge"}
API_KEY = "eval-internal-key"
OWNER_KEY = "eval-owner-key"
APPROVER_KEY = "eval-approver-key"

BLOCKING_LABELS = {"conflict_or_expired", "wrong_scope", "missing_disclosure", "forbidden_phrase",
                   "slot_blocked", "no_source"}
# Plan gate: "0 misses on number/scope/expiry/forbidden-phrase/disclosure traps".
GATE_TRAP_LABELS = {"conflict_or_expired", "wrong_scope", "forbidden_phrase", "missing_disclosure"}
NEGATIVE_CONTROLS = ("01-clean", "02-paraphrase")
# Set by --claims-gateway: {"gateway": url, "model": name|None, "timeout": seconds}. None = zero-model.
MODEL_CHECK: dict | None = None


# ---------------------------------------------------------------- text helpers (pure)

_TRANS = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def norm(s: str | None) -> str:
    """Compare form: casefold, straight quotes/dashes, no markdown emphasis, single spaces."""
    t = (s or "").translate(_TRANS)
    t = re.sub(r"\*\*|__|`", "", t)
    return " ".join(t.casefold().split())


def _spans(hay: str, needle: str) -> list[tuple[int, int]]:
    out, i = [], hay.find(needle) if needle else -1
    while i >= 0:
        out.append((i, i + len(needle)))
        i = hay.find(needle, i + 1)
    return out


def sentence_hits(sentence: str, target: str, texts: list[str]) -> str | None:
    """How a reported sentence covers an expected substring, or None.

    "strict": the substring is inside the sentence (contracts §5).
    "overlap": the sentence and the substring overlap in the pasted or the filled text; this
    catches a checker that cuts sentences at a different place than the fixture author did
    (e.g. `contains` spans two short sentences)."""
    s, t = norm(sentence), norm(target)
    if not s or not t:
        return None
    if t in s:
        return "strict"
    for text in texts:
        h = norm(text)
        for a0, a1 in _spans(h, s):
            for b0, b1 in _spans(h, t):
                if a0 < b1 and b0 < a1:
                    return "overlap"
    return None


def score_draft(expected: dict, findings: list[dict], texts: list[str], piece_blocked: bool | None = None) -> dict:
    """Score one draft's findings (all pieces) against its expected file (contracts §5).

    `texts` = the pasted text and the filled piece texts (both are used to place sentences).
    `piece_blocked` = 88's own `blocked` (any piece); evidence-only blocking is computed here."""
    exp_findings = expected.get("findings") or []
    must_not = expected.get("must_not_flag") or []
    hits, misses = [], []
    for e in exp_findings:
        best = None
        for f in findings:
            if f.get("label") != e["label"]:
                continue
            if e.get("fact_key") and f.get("fact_key") != e["fact_key"]:
                continue
            mode = sentence_hits(f.get("sentence") or "", e["contains"], texts)
            if mode and (best is None or (mode == "strict" and best[1] != "strict")):
                best = (f, mode)
        if best:
            hits.append({"expected": e, "mode": best[1], "reported": best[0]})
        else:
            near = [f for f in findings if sentence_hits(f.get("sentence") or "", e["contains"], texts)]
            misses.append({"expected": e, "reported_on_sentence": near,
                           "same_label_elsewhere": [f for f in findings if f.get("label") == e["label"]]})
    false_warnings, must_not_violations = [], []
    for f in findings:
        if not f.get("blocking"):
            continue
        sent = f.get("sentence") or ""
        bad = [m for m in must_not if sentence_hits(sent, m, texts)]
        if bad:
            must_not_violations.append({"reported": f, "must_not_flag": bad})
            continue
        if not any(sentence_hits(sent, e["contains"], texts) for e in exp_findings):
            false_warnings.append({"reported": f})
    evidence_blocked = any(f.get("blocking") for f in findings)
    exp_blocked = bool(expected.get("blocked"))
    return {
        "expected_blocked": exp_blocked,
        "evidence_blocked": evidence_blocked,
        "piece_blocked": piece_blocked,
        "blocked_right": (piece_blocked if piece_blocked is not None else evidence_blocked) == exp_blocked,
        "hits": hits, "misses": misses,
        "false_warnings": false_warnings, "must_not_flag_violations": must_not_violations,
        "reviews": sum(1 for f in findings if f.get("label") == "review"),
        "blocking_findings": sum(1 for f in findings if f.get("blocking")),
    }


# ---------------------------------------------------------------- sentinels (pure)

def _num_forms(v) -> list[str]:
    if isinstance(v, bool) or v is None:
        return []
    if isinstance(v, (int, float)):
        forms = {str(v)}
        if isinstance(v, float):
            forms |= {f"{v:.2f}", f"{v:g}", f"{v:,.2f}"}
        forms |= {f"{v:,}"}
        # short numbers ("32") collide with ordinary text (DN32); keep only distinctive forms
        return sorted(f for f in forms if len(f.replace(",", "").replace(".", "")) >= 3)
    return []


def sentinels(facts: list[dict]) -> list[dict]:
    """Strings that must never be in a pack: internal values/text, restricted key/values/text."""
    out = []
    for f in facts:
        sens = f.get("sensitivity")
        if sens not in ("internal", "restricted"):
            continue
        cands = [("value_text", f.get("value_text")), ("text", f.get("text"))]
        cands += [("value", s) for s in _num_forms(f.get("value"))]
        if sens == "restricted":
            cands.append(("key", f.get("key")))
        for kind, s in cands:
            if s and str(s).strip():
                out.append({"key": f["key"], "sensitivity": sens, "kind": kind, "string": str(s)})
    return out


def sentinel_leaks(text: str, sents: list[dict]) -> list[dict]:
    low = (text or "").casefold()
    leaks = []
    for s in sents:
        needle = s["string"].casefold()
        if s["kind"] == "key":
            if re.search(r"(?<![a-z0-9-])" + re.escape(needle) + r"(?![a-z0-9-])", low):
                leaks.append(s)
        elif needle in low:
            leaks.append(s)
    return leaks


# ---------------------------------------------------------------- fallback splitter (pure)

_SIGNOFF = re.compile(r"^\s*(?:let me know|feel free|hope|i hope|want me|would you like|shall i|do you want|"
                      r"happy to|-{3,})", re.I)


def fallback_split(text: str, pieces: list[dict]) -> list[dict] | None:
    """Our own split, used only when 88 reports split problems: a short line that names a piece's
    channel (after stripping #, *, =, numbering, 'Post'/'Piece') starts that piece. Preamble
    before the first header and a trailing sign-off paragraph are dropped. None if the headers
    do not give every piece exactly once, in order."""
    lines = text.replace("\r\n", "\n").split("\n")
    heads = []
    want = [p["channel"].lower() for p in pieces]
    for i, line in enumerate(lines):
        s = re.sub(r"[#*=_]", " ", line).strip().lower()
        s = re.sub(r"^(?:post|piece|part)?\s*\d{0,2}\s*[.):\-–—]*\s*", "", s)
        s = s.rstrip(":").strip()
        if len(s) > 30:
            continue
        for j, ch in enumerate(want):
            alias = {"email": ("email", "e-mail", "newsletter")}.get(ch, (ch,))
            if any(s == a or s.startswith(a + " ") for a in alias):
                heads.append((i, j))
                break
    if [j for _, j in heads] != list(range(len(pieces))):
        return None
    out = []
    for n, (i, j) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        body = lines[i + 1:end]
        if n + 1 == len(heads):
            paras = "\n".join(body).rstrip().split("\n\n")
            if len(paras) > 1 and _SIGNOFF.match(paras[-1].strip()):
                paras = paras[:-1]
            body = "\n\n".join(paras).split("\n")
        out.append({"piece_key": pieces[j]["key"], "text": "\n".join(body).strip()})
    return out


# ---------------------------------------------------------------- services

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stack:
    """05, 14, 19, 88 as uvicorn subprocesses on 127.0.0.1 with temp data."""

    def __init__(self, tmp: Path, company: dict):
        self.tmp, self.company = tmp, company
        self.services = dict(SERVICES, **({"claims": "44-claim-checker"} if MODEL_CHECK else {}))
        self.ports = {k: free_port() for k in self.services}
        self.urls = {k: f"http://127.0.0.1:{p}" for k, p in self.ports.items()}
        self.procs: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, Path] = {}

    def env(self, name: str) -> dict:
        base = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")}
        base["PYTHONDONTWRITEBYTECODE"] = "1"
        if name == "brand":
            brand = {"name": self.company["name"], "website": "https://example.invalid"}
            (self.tmp / "brand.yaml").write_text(yaml.safe_dump(brand), encoding="utf-8")
            base.update(INTERNAL_API_KEY=API_KEY, FACT_OWNER_KEY=OWNER_KEY,
                        FACTS_DB=str(self.tmp / "facts.sqlite"), BRAND_FILE=str(self.tmp / "brand.yaml"),
                        BRAND_OVERRIDES_FILE=str(self.tmp / "brand.overrides.json"),
                        VOICE_FILE=str(self.tmp / "voice.json"), FACTS_TODAY=self.company["today"])
        elif name == "rules":
            pass
        elif name == "calendar":
            base.update(INTERNAL_API_KEY=API_KEY, APPROVER_KEY=APPROVER_KEY, DB_PATH=str(self.tmp / "calendar.sqlite"))
        elif name == "bridge":
            base.update(INTERNAL_API_KEY=API_KEY, DB_PATH=str(self.tmp / "tasks.sqlite"),
                        BRAND_URL=self.urls["brand"], CALENDAR_URL=self.urls["calendar"],
                        RULES_URL=self.urls["rules"], LEADS_URL="", MODEL_CHECK="off", CLAIMS_URL="",
                        GATEWAY_URL="", RECONCILE_MIN="0", RATE_PER_MIN="1000")
            if MODEL_CHECK:
                base.update(MODEL_CHECK="auto", CLAIMS_URL=self.urls["claims"],
                            CLAIMS_TIMEOUT=str(MODEL_CHECK["timeout"]))
        elif name == "claims":
            base.update(INTERNAL_API_KEY=API_KEY, GATEWAY_URL=MODEL_CHECK["gateway"], BRAND_URL=self.urls["brand"],
                        KB_URL="", REQUEST_TIMEOUT=str(MODEL_CHECK["timeout"]))
            if MODEL_CHECK.get("model"):
                base["VERIFIER_MODEL"] = MODEL_CHECK["model"]
        return base

    def start(self):
        for name, folder in self.services.items():
            log = self.tmp / f"{name}.log"
            self.logs[name] = log
            self.procs[name] = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                 "--port", str(self.ports[name]), "--log-level", "warning"],
                cwd=str(REPO / folder), env=self.env(name), stdout=log.open("w"), stderr=subprocess.STDOUT)
        import httpx
        deadline = time.time() + 30
        for name in self.services:
            while True:
                try:
                    if httpx.get(self.urls[name] + "/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if self.procs[name].poll() is not None or time.time() > deadline:
                    raise RuntimeError(f"{name} did not start:\n{self.logs[name].read_text()[-2000:]}")
                time.sleep(0.2)

    def stop(self):
        for p in self.procs.values():
            p.terminate()
        for p in self.procs.values():
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


# ---------------------------------------------------------------- one company

def _rec(resp) -> dict:
    try:
        body = resp.json()
    except ValueError:
        body = resp.text[:2000]
    return {"status": resp.status_code, "body": body}


def canonical_sha(body: str) -> str:
    return hashlib.sha256(body.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def run_company(slug: str) -> dict:
    import httpx
    folder = COMPANIES / slug
    facts_doc = yaml.safe_load((folder / "facts.yaml").read_text(encoding="utf-8"))
    task_doc = yaml.safe_load((folder / "task.yaml").read_text(encoding="utf-8"))
    company = facts_doc["company"]
    facts = facts_doc["facts"]
    out: dict = {"company": slug, "name": company["name"], "type": company.get("type"),
                 "today": company["today"], "publish_on": task_doc["publish_on"], "integration": [], "drafts": {}}
    with tempfile.TemporaryDirectory(prefix=f"tb-{slug}-") as tmpd:
        stack = Stack(Path(tmpd), company)
        stack.start()
        try:
            H = {"X-API-Key": API_KEY}
            OH = {**H, "X-Owner-Key": OWNER_KEY}
            AH = {**H, "X-Approver-Key": APPROVER_KEY}
            c = httpx.Client(timeout=60 if not MODEL_CHECK else 4 * MODEL_CHECK["timeout"] + 60)
            B, BR, CAL = stack.urls["bridge"], stack.urls["brand"], stack.urls["calendar"]

            # 2. import facts
            r = c.post(BR + "/facts/v2/import", headers=OH, json={"facts": facts, "confirm": True})
            out["import"] = _rec(r)
            if r.status_code != 200:
                out["integration"].append({"step": "import", "request": "POST /facts/v2/import confirm=true",
                                           "response": _rec(r), "expected": "200"})
                return out
            got = {f["key"]: f.get("status") for f in c.get(BR + "/facts/v2", headers=H).json().get("facts", [])}
            want = {f["key"]: f.get("status", "active") for f in facts}
            out["status_after_import"] = {k: {"fixture": want[k], "served": got.get(k)} for k in want}
            mism = {k: v for k, v in out["status_after_import"].items() if v["fixture"] != v["served"]}
            if mism:
                out["integration"].append({"step": "import statuses", "request": "GET /facts/v2",
                                           "response": mism, "expected": "fixture statuses"})

            sents = sentinels(facts)
            out["sentinels_checked"] = len(sents)
            # negative control: the sentinel check must fire on a text that has a restricted/internal string
            out["sentinel_negative_control"] = bool(sents) and bool(sentinel_leaks("x " + sents[0]["string"], sents))

            drafts = sorted(p for p in (folder / "drafts").glob("*.txt"))
            first_task = None
            for dp in drafts:
                name = dp.stem
                pasted = dp.read_text(encoding="utf-8")
                expected = yaml.safe_load(dp.with_suffix(".expected.yaml").read_text(encoding="utf-8"))
                d: dict = {"steps": []}
                r = c.post(B + "/tasks", headers=H, json=task_doc)
                if r.status_code != 201:
                    d["error"] = {"request": "POST /tasks", "response": _rec(r), "expected": "201"}
                    out["drafts"][name] = d
                    continue
                task = r.json()
                tid = task["id"]
                d["task_id"] = tid
                sp = task.get("share_preview") or {}
                pack_text = task.get("pack") or ""
                leaks = sentinel_leaks(pack_text, sents)
                sent_missing = [ln for ln in sp.get("sent") or [] if ln not in pack_text]
                preview_leaks = sentinel_leaks("\n".join(sp.get("sent") or []), sents)
                d["pack"] = {"chars": len(pack_text), "preview_chars": sp.get("chars"),
                             "sent": len(sp.get("sent") or []), "slotted": sp.get("slotted"),
                             "withheld": sp.get("withheld"), "leaks": leaks, "preview_leaks": preview_leaks,
                             "sent_lines_missing_from_pack": sent_missing,
                             "fact_set_version": task.get("fact_set_version")}
                if first_task is None:
                    first_task = {"id": tid, "pack": pack_text, "share_preview": sp, "snapshot": task.get("snapshot")}

                # 4. paste, split, submit
                r = c.post(B + f"/tasks/{tid}/paste", headers=H, json={"text": pasted, "provider": "chatgpt"})
                d["paste"] = {"status": r.status_code}
                if r.status_code != 201:
                    d["error"] = {"request": "POST paste", "response": _rec(r), "expected": "201"}
                    out["drafts"][name] = d
                    continue
                pj = r.json()
                draft_id = pj["draft_id"]
                split = pj.get("split") or []
                d["paste"].update(problems=pj.get("problems"), pieces=[s["piece_key"] for s in split],
                                  removed=[{"piece_key": s["piece_key"], "pre": s.get("removed_pre"),
                                            "post": s.get("removed_post")} for s in split])
                split_problems = list(pj.get("problems") or [])
                joined = norm("\n".join(s["text"] for s in split))
                lost = [x for x in [e["contains"] for e in expected.get("findings") or []] +
                        list(expected.get("must_not_flag") or []) if norm(x) not in joined]
                if lost:
                    split_problems.append({"content_lost_in_split": lost})
                d["split_problems"] = split_problems
                # 88 assigned every requested piece and nothing was lost: its problems are only notes
                # ("piece 3 had no heading; the text after … was used"). Score 88's own split.
                requested = {str(p.get("key")) for p in task_doc["pieces"]}
                assigned = {s["piece_key"] for s in split if str(s.get("text") or "").strip()}
                d["split_notes_only"] = bool(split_problems) and not lost and requested <= assigned
                if split_problems:
                    # Notes only: confirm 88's own split as a person would ("looks right"); otherwise
                    # fall back to our own header split.
                    manual = ([{"piece_key": x["piece_key"], "text": x["text"]} for x in split]
                              if d["split_notes_only"] else fallback_split(pasted, task_doc["pieces"]))
                    if manual is None:
                        d["split_failure"] = "88 reported split problems and our own header split could not assign pieces"
                        out["drafts"][name] = d
                        continue
                    r = c.post(B + f"/tasks/{tid}/drafts/{draft_id}/split", headers=H, json={"pieces": manual})
                    d["manual_split"] = {"status": r.status_code, "pieces": manual}
                    if r.status_code != 201:
                        d["split_failure"] = {"request": "POST split", "response": _rec(r), "expected": "201"}
                        out["drafts"][name] = d
                        continue
                    draft_id = r.json()["draft_id"]
                t0 = time.monotonic()
                r = c.post(B + f"/tasks/{tid}/submit", headers=H, json={"draft_id": draft_id})
                d["submit_seconds"] = round(time.monotonic() - t0, 2)
                d["submit_status"] = r.status_code
                if r.status_code != 200:
                    d["error"] = {"request": "POST submit", "response": _rec(r), "expected": "200"}
                    out["drafts"][name] = d
                    continue
                sj = r.json()
                findings = []
                for p in sj["pieces"]:
                    for f in p.get("findings") or []:
                        findings.append({**f, "piece_key": p["piece_key"]})
                d["pieces"] = [{"piece_key": p["piece_key"], "blocked": p["blocked"], "status": p.get("status"),
                                "calendar_item_id": p.get("calendar_item_id"), "filled_text": p["filled_text"],
                                "filled_sha256": p["filled_sha256"], "checks": p.get("checks"),
                                "notes": p.get("notes")} for p in sj["pieces"]]
                d["findings"] = findings
                texts = [pasted] + [p["filled_text"] for p in sj["pieces"]]
                d["score"] = score_draft(expected, findings, texts, any(p["blocked"] for p in sj["pieces"]))
                d["non_evidence_errors"] = [
                    {"piece_key": p["piece_key"], **v} for p in sj["pieces"]
                    for v in (p.get("checks") or {}).get("brand", []) + (p.get("checks") or {}).get("platform", [])
                    if v.get("severity") == "error"]
                out["drafts"][name] = d

            out["pack"] = {"task_id": first_task and first_task["id"],
                           "chars": first_task and len(first_task["pack"]),
                           "share_preview": first_task and first_task["share_preview"],
                           "snapshot": first_task and first_task["snapshot"],
                           "text": first_task and first_task["pack"]}

            # 6. lifecycle on the 01-clean task
            clean = out["drafts"].get("01-clean") or {}
            if clean.get("pieces"):
                out["lifecycle"] = lifecycle(c, stack, clean, H, OH, AH)
            else:
                out["lifecycle"] = {"skipped": "01-clean was not submitted"}
        finally:
            stack.stop()
            out["service_log_tail"] = {k: v.read_text()[-1500:] for k, v in stack.logs.items()
                                       if v.exists() and v.read_text().strip()}
    return out


def lifecycle(c, stack, clean: dict, H, OH, AH) -> dict:
    B, BR, CAL = stack.urls["bridge"], stack.urls["brand"], stack.urls["calendar"]
    tid = clean["task_id"]
    steps = []

    def step(name, resp, expect, ok=None, **extra):
        rec = {"step": name, "status": resp.status_code, "expected": expect,
               "ok": (resp.status_code == expect) if ok is None else ok, **extra}
        if not rec["ok"] or resp.status_code >= 400:
            rec["response"] = _rec(resp)["body"]
        steps.append(rec)
        return rec

    pieces = clean["pieces"]
    items = {}
    for p in pieces:
        r = c.get(CAL + f"/items/{p['calendar_item_id']}", headers=H)
        items[p["piece_key"]] = r.json()
        it = items[p["piece_key"]]
        steps.append({"step": f"item {p['piece_key']} created", "status": r.status_code, "expected": 200,
                      "ok": r.status_code == 200 and it.get("status") == "in_review" and it.get("require_bound") is True
                      and it.get("origin") == "task-bridge" and it.get("body_sha256") == p["filled_sha256"],
                      "item_status": it.get("status"), "require_bound": it.get("require_bound"),
                      "origin": it.get("origin"), "body_sha256_matches_filled": it.get("body_sha256") == p["filled_sha256"]})
    if any(it.get("status") != "in_review" for it in items.values()):
        return {"steps": steps, "stopped": "a clean piece is not in_review (blocked), so it cannot be approved"}

    stale_key = pieces[-1]["piece_key"]
    for p in pieces:
        iid, it = p["calendar_item_id"], items[p["piece_key"]]
        url = CAL + f"/items/{iid}/status"
        if p["piece_key"] == pieces[0]["piece_key"]:
            step(f"approve {p['piece_key']} without a hash", c.post(url, headers=AH, json={"status": "approved"}), 428)
        if p["piece_key"] == stale_key:
            old = it["body_sha256"]
            edited = it["body"] + "\n\n(edited after review)"
            step(f"edit {p['piece_key']} (new version, if_match current hash)",
                 c.patch(CAL + f"/items/{iid}", headers=H, json={"body": edited, "if_match_sha256": old}), 200)
            step(f"approve {p['piece_key']} with the stale hash of the text the reviewer saw",
                 c.post(url, headers=AH, json={"status": "approved", "expected_sha256": old}), 409)
            step(f"edit {p['piece_key']} back to the checked text",
                 c.patch(CAL + f"/items/{iid}", headers=H, json={"body": it["body"],
                                                                 "if_match_sha256": canonical_sha(edited)}), 200)
            r = c.get(CAL + f"/items/{iid}/versions", headers=H)
            steps.append({"step": f"versions of {p['piece_key']}", "status": r.status_code, "expected": 200,
                          "ok": r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) == 3,
                          "versions": len(r.json()) if r.status_code == 200 and isinstance(r.json(), list) else None})
        step(f"approve {p['piece_key']} with body_sha256", c.post(url, headers=AH, json={
            "status": "approved", "expected_sha256": it["body_sha256"]}), 200)

    r = c.get(B + f"/tasks/{tid}/export", headers=H, params={"format": "txt"})
    rec = step("export after approval", r, 200)
    if r.status_code == 200:
        body = r.text
        sane = {"x_export_sha256_matches": r.headers.get("X-Export-Sha256") == hashlib.sha256(body.encode()).hexdigest()}
        manifest = None
        if "--- MANIFEST ---" in body:
            try:
                manifest = json.loads(body.split("--- MANIFEST ---", 1)[1])
            except ValueError:
                manifest = None
        sane["manifest_parsed"] = manifest is not None
        if manifest:
            mp = {m["piece_key"]: m for m in manifest.get("pieces", [])}
            sane["task_id"] = manifest.get("task_id") == tid
            sane["all_pieces"] = set(mp) == {p["piece_key"] for p in pieces}
            sane["hashes"] = all(mp.get(p["piece_key"], {}).get("sha256") == p["filled_sha256"] for p in pieces)
            sane["texts_in_export"] = all(p["filled_text"] in body for p in pieces)
            sane["approved_items"] = all(mp.get(p["piece_key"], {}).get("item_id") == p["calendar_item_id"]
                                         and mp.get(p["piece_key"], {}).get("status") == "approved" for p in pieces)
            sane["fact_set_version"] = bool(manifest.get("fact_set_version"))
            sane["facts_listed"] = len(manifest.get("facts") or [])
            sane["disclosures"] = manifest.get("disclosures")
            sane["checklist_lines"] = len(manifest.get("checklist") or [])
        rec["manifest_check"] = sane
        rec["ok"] = rec["ok"] and all(v for k, v in sane.items() if isinstance(v, bool))

    # retire a public fact that a piece used
    t = c.get(B + f"/tasks/{tid}", headers=H).json()
    snap = {s["key"]: s for s in t.get("snapshot") or []}
    used = [k for p in t.get("piece_state") or [] for k in p.get("used_facts") or []]
    cand = [k for k in dict.fromkeys(used) if k in snap and snap[k].get("sensitivity") == "public"] or \
           [k for k in dict.fromkeys(used) if k in snap]
    if not cand:
        steps.append({"step": "pick a used fact to retire", "ok": False, "used_facts": used,
                      "detail": "no piece reports a used fact from the pack snapshot"})
        return {"steps": steps}
    key = cand[0]
    step(f"retire used fact {key}", c.post(BR + f"/facts/v2/{key}/retire", headers=OH), 200, retired=key)
    r = c.post(B + "/reconcile", headers=H)
    rj = r.json() if r.status_code == 200 else {}
    moved = [m for m in rj.get("moved") or [] if m.get("task_id") == tid]
    step("reconcile", r, 200, ok=r.status_code == 200 and {m["piece_key"] for m in moved} == {p["piece_key"] for p in pieces},
         moved=moved, errors=rj.get("errors"))
    back = {}
    for p in pieces:
        back[p["piece_key"]] = c.get(CAL + f"/items/{p['calendar_item_id']}", headers=H).json().get("status")
    steps.append({"step": "items back in draft", "status": "draft", "expected": "draft", "ok": all(v == "draft" for v in back.values()), "statuses": back})
    r = c.get(B + f"/tasks/{tid}/export", headers=H, params={"format": "txt"})
    step("export after retire + reconcile", r, 409)
    return {"steps": steps, "retired": key}


# ---------------------------------------------------------------- aggregate + report

def summarise(companies: list[dict]) -> dict:
    per_label: dict[str, dict] = {}
    tot = {"expected": 0, "hits": 0, "misses": 0, "false_warnings": 0, "must_not_flag_violations": 0,
           "blocked_right": 0, "drafts": 0, "split_problem_drafts": 0, "split_failures": 0, "errors": 0}
    gate_trap_misses, no_source_misses, clean_review_over, neg_blocking = [], [], [], []
    for co in companies:
        for name, d in co.get("drafts", {}).items():
            tot["drafts"] += 1
            if d.get("split_problems"):
                tot["split_problem_drafts"] += 1
            if d.get("split_failure"):
                tot["split_failures"] += 1
            s = d.get("score")
            if not s:
                tot["errors"] += 1
                continue
            for h in s["hits"]:
                pl = per_label.setdefault(h["expected"]["label"], {"expected": 0, "hit": 0})
                pl["expected"] += 1
                pl["hit"] += 1
            for m in s["misses"]:
                lab = m["expected"]["label"]
                pl = per_label.setdefault(lab, {"expected": 0, "hit": 0})
                pl["expected"] += 1
                (gate_trap_misses if lab in GATE_TRAP_LABELS else no_source_misses).append((co["company"], name, lab))
            tot["expected"] += len(s["hits"]) + len(s["misses"])
            tot["hits"] += len(s["hits"])
            tot["misses"] += len(s["misses"])
            tot["false_warnings"] += len(s["false_warnings"])
            tot["must_not_flag_violations"] += len(s["must_not_flag_violations"])
            tot["blocked_right"] += int(s["blocked_right"])
            if name == "01-clean" and s["reviews"] > 1:
                clean_review_over.append((co["company"], s["reviews"]))
            if name in NEGATIVE_CONTROLS and (s["blocking_findings"] or s["piece_blocked"]):
                neg_blocking.append((co["company"], name, s["blocking_findings"], s["piece_blocked"]))
    leaks = [(co["company"], n) for co in companies for n, d in co.get("drafts", {}).items()
             if (d.get("pack") or {}).get("leaks") or (d.get("pack") or {}).get("preview_leaks")]
    missing_sent = [(co["company"], n) for co in companies for n, d in co.get("drafts", {}).items()
                    if (d.get("pack") or {}).get("sent_lines_missing_from_pack")]
    life_fail = [(co["company"], s["step"]) for co in companies
                 for s in (co.get("lifecycle") or {}).get("steps", []) if not s.get("ok")]
    life_skip = [(co["company"], co["lifecycle"].get("skipped") or co["lifecycle"].get("stopped"))
                 for co in companies if (co.get("lifecycle") or {}).get("skipped") or (co.get("lifecycle") or {}).get("stopped")]
    gate = {
        "G1_trap_misses_0": {"pass": not gate_trap_misses, "misses": gate_trap_misses,
                             "labels": sorted(GATE_TRAP_LABELS)},
        "G1b_no_source_misses (not named in the plan gate, reported)": {"misses": no_source_misses},
        "G2_clean_reviews_le_1": {"pass": not clean_review_over, "over": clean_review_over},
        "G3_negative_controls_0_blocking": {"pass": not neg_blocking, "blocking": neg_blocking},
        "pack_no_sentinel_leak": {"pass": not leaks, "leaks": leaks},
        "pack_contains_sent_lines": {"pass": not missing_sent, "missing": missing_sent},
        "lifecycle_steps": {"pass": not life_fail and not life_skip, "failed": life_fail, "skipped": life_skip},
    }
    verdict = all(v.get("pass", True) for v in gate.values())
    return {"totals": tot, "per_label": per_label, "gate": gate, "verdict": "PASS" if verdict else "FAIL"}


def _cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ⏎ ")


def render_md(result: dict) -> str:
    S = result["summary"]
    mc = result.get("model_check")
    mode = (f"MODEL_CHECK=auto → 44 via gateway {mc['gateway']} (model {mc.get('model') or 'gateway default'})"
            if mc else "MODEL_CHECK=off, CLAIMS_URL empty · no Docker, no model calls")
    L = [f"# Task bridge — {'model check' if mc else 'zero-model'} end-to-end ({result['date']})", "",
         f"Runner: `23-eval-suite/evalsuite/task_bridge.py` · run at {result['run_at']} · services 05/14/19/88"
         f"{'/44' if mc else ''} on 127.0.0.1 with temp data · {mode}.", "",
         f"## Gate verdict: **{S['verdict']}**", ""]
    for k, v in S["gate"].items():
        mark = "" if "pass" not in v else ("PASS" if v["pass"] else "FAIL")
        detail = {kk: vv for kk, vv in v.items() if kk != "pass"}
        L.append(f"- **{k}** {mark} — `{json.dumps(detail, ensure_ascii=False)}`")
    t = S["totals"]
    L += ["", "## Overall", "",
          f"- Drafts: {t['drafts']} · expected findings {t['expected']} · hit {t['hits']} · missed {t['misses']}",
          f"- False warnings {t['false_warnings']} · blocking findings on `must_not_flag` sentences "
          f"{t['must_not_flag_violations']} · `blocked` right {t['blocked_right']}/{t['drafts']}",
          f"- Drafts with split problems {t['split_problem_drafts']} · split failures {t['split_failures']} · "
          f"errors {t['errors']}", "", "| Label | expected | hit |", "|---|---|---|"]
    for lab, v in sorted(S["per_label"].items()):
        L.append(f"| {lab} | {v['expected']} | {v['hit']} |")
    for co in result["companies"]:
        L += ["", f"## {co['company']} ({co.get('type')}) · publish {co['publish_on']}", ""]
        pk = co.get("pack") or {}
        sp = pk.get("share_preview") or {}
        first = next(iter(co.get("drafts", {}).values()), {}).get("pack", {})
        L.append(f"Pack {pk.get('chars')} chars · sent {len(sp.get('sent') or [])} lines · slotted "
                 f"{sp.get('slotted')} · withheld {[(w.get('key'), w.get('reason')) for w in sp.get('withheld') or []]} · "
                 f"sentinels checked {co.get('sentinels_checked')} (negative control fires: "
                 f"{co.get('sentinel_negative_control')}) · leaks {first.get('leaks')} · "
                 f"sent lines missing from pack {first.get('sent_lines_missing_from_pack')}")
        for i in co.get("integration") or []:
            L.append(f"- Integration: `{_cell(json.dumps(i, ensure_ascii=False))[:600]}`")
        L += ["", "| Draft | expected blocked | 88 blocked | hit | missed | false warn | must_not_flag hit | reviews | split |",
              "|---|---|---|---|---|---|---|---|---|"]
        for name, d in co.get("drafts", {}).items():
            s = d.get("score")
            if not s:
                L.append(f"| {name} | error: {_cell(json.dumps(d.get('error') or d.get('split_failure'), ensure_ascii=False))[:300]} "
                         "| | | | | | | |")
                continue
            split = "manual" if d.get("manual_split") else ("problems" if d.get("split_problems") else "ok")
            L.append(f"| {name} | {s['expected_blocked']} | {s['piece_blocked']}"
                     f"{'' if s['blocked_right'] else ' ✗'} | {len(s['hits'])} | {len(s['misses'])} | "
                     f"{len(s['false_warnings'])} | {len(s['must_not_flag_violations'])} | {s['reviews']} | {split} |")
        # details
        for name, d in co.get("drafts", {}).items():
            s = d.get("score")
            if not s:
                continue
            for m in s["misses"]:
                e = m["expected"]
                L.append(f"- **MISS** {name}: expected `{e['label']}`/{e.get('fact_key')} on “{e['contains']}”. "
                         f"88 on that sentence: "
                         + (", ".join(f"`{f['label']}`/{f.get('fact_key')} ({_cell(f.get('detail'))[:160]})"
                                      for f in m["reported_on_sentence"]) or "nothing")
                         + (f"; same label elsewhere: " + "; ".join(
                             f"“{_cell(f['sentence'])[:120]}” {f.get('fact_key')}" for f in m["same_label_elsewhere"])
                            if m["same_label_elsewhere"] else ""))
            for fw in s["false_warnings"]:
                f = fw["reported"]
                L.append(f"- **FALSE WARNING** {name} [{f.get('piece_key')}]: “{_cell(f['sentence'])[:200]}” → "
                         f"`{f['label']}`/{f.get('fact_key')} ({_cell(f.get('detail'))[:200]}); expected: no blocking finding")
            for v in s["must_not_flag_violations"]:
                f = v["reported"]
                L.append(f"- **MUST-NOT-FLAG** {name} [{f.get('piece_key')}]: “{_cell(f['sentence'])[:200]}” → "
                         f"`{f['label']}`/{f.get('fact_key')} ({_cell(f.get('detail'))[:200]}); protected: {v['must_not_flag']}")
            for h in s["hits"]:
                if h["mode"] == "overlap":
                    L.append(f"- note {name}: hit by overlap only (88 sentence “{_cell(h['reported']['sentence'])[:120]}” "
                             f"vs expected “{h['expected']['contains']}”)")
            if d.get("split_problems"):
                L.append(f"- split {name}: {_cell(json.dumps(d['split_problems'], ensure_ascii=False))[:400]}")
            if d.get("non_evidence_errors"):
                L.append(f"- brand/platform errors {name}: {_cell(json.dumps(d['non_evidence_errors'], ensure_ascii=False))[:300]}")
        lc = co.get("lifecycle") or {}
        L += ["", "Approval / export / reconcile (01-clean):", ""]
        if lc.get("skipped") or lc.get("stopped"):
            L.append(f"- {lc.get('skipped') or lc.get('stopped')}")
        for s in lc.get("steps", []):
            extra = {k: v for k, v in s.items() if k not in ("step", "ok", "status", "expected")}
            L.append(f"- {'ok' if s.get('ok') else '**FAIL**'} · {s['step']} → {s.get('status')} "
                     f"(expected {s.get('expected')}) {_cell(json.dumps(extra, ensure_ascii=False))[:400] if extra else ''}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", help="company slug (repeatable)")
    ap.add_argument("--out-stem", default=f"task-bridge-zero-model-{dt.date.today().isoformat()}")
    ap.add_argument("--cases", default="companies", help="folder under cases/ (e.g. companies-v2, held out)")
    ap.add_argument("--claims-gateway", default="", help="optional model check: a running 03 gateway URL for 44")
    ap.add_argument("--verifier-model", default="", help="VERIFIER_MODEL for 44 (default: the gateway's MODEL)")
    ap.add_argument("--claims-timeout", type=float, default=300, help="CLAIMS_TIMEOUT for 88 and 44 (seconds)")
    a = ap.parse_args(argv)
    global COMPANIES, MODEL_CHECK
    if a.claims_gateway:
        MODEL_CHECK = {"gateway": a.claims_gateway.rstrip("/"), "model": a.verifier_model or None,
                       "timeout": a.claims_timeout}
    COMPANIES = SUITE / "cases" / a.cases
    slugs = sorted(p.name for p in COMPANIES.iterdir() if p.is_dir() and (p / "facts.yaml").exists())
    if a.only:
        slugs = [s for s in slugs if s in a.only]
    companies = []
    for slug in slugs:
        print(f"== {slug}", flush=True)
        try:
            companies.append(run_company(slug))
        except Exception as exc:  # record, keep going
            companies.append({"company": slug, "fatal": repr(exc), "drafts": {}})
            print(f"   fatal: {exc!r}", flush=True)
    result = {"model_check": MODEL_CHECK, "date": dt.date.today().isoformat(), "run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "companies": companies}
    result["summary"] = summarise(companies)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{a.out_stem}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (RESULTS / f"{a.out_stem}.md").write_text(render_md(result), encoding="utf-8")
    t = result["summary"]["totals"]
    print(f"verdict {result['summary']['verdict']} · expected {t['expected']} hit {t['hits']} miss {t['misses']} "
          f"false warnings {t['false_warnings']} must_not_flag {t['must_not_flag_violations']} "
          f"blocked right {t['blocked_right']}/{t['drafts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
