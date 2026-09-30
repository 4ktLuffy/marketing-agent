"""Facts page: the business's facts (05 v2), what needs the owner, starter kits and open questions.

Everything goes through 05 with INTERNAL_API_KEY. Adding or editing a fact makes a DRAFT; only
confirm / retire, a starter kit's apply and a kit rule's confirm / dismiss send FACT_OWNER_KEY
(`X-Owner-Key`), and only from here, server-side. The key is never rendered. Facts from the
brand profile (brand.yaml, "derived") are read-only; "turn into a scoped fact" copies one into a
new draft that supersedes it once confirmed.
"""
import re
from datetime import date, timedelta

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .backends import BackendError
from .brand_setup import friendly

KEY = re.compile(r"^[a-z0-9-]{2,60}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SUBJECT_KINDS = ["business", "site", "product", "variant", "plan", "service", "package", "menu_item",
                 "person", "policy", "offer"]
FACT_TYPES = ["price", "spec", "availability", "hours", "inclusion", "policy", "certification", "credential",
              "claim", "testimonial", "result", "event", "contact"]
BASES = ["", "per_unit", "per_person", "per_room", "per_night", "per_seat", "per_month", "per_year", "flat"]
SOURCE_KINDS = ["doc", "url", "certificate", "owner_statement"]
CLAIM_CLASSES = ["none", "comparative", "regulated_health", "regulated_food", "safety_cert", "origin",
                 "price_reference", "security", "ai_capability", "result"]
SENSITIVITY = {"public": "public", "internal": "internal: only a placeholder leaves",
               "restricted": "restricted: never leaves"}
RISKS = ["low", "medium", "high"]
OPS = ["=", "!=", "<", "<=", ">", ">=", "in", "not_in"]
DIMS = [("sites", "Sites or branches"), ("regions", "Regions"), ("channels", "Channels"),
        ("segments", "Customer groups"), ("plan_tiers", "Plan tiers"), ("variants", "Variants")]
DIM_WORD = {"sites": "branch", "regions": "region", "channels": "channel", "segments": "customers",
            "plan_tiers": "plan", "variants": "variant"}
SHOWS = ("all", "attention", "drafts", "expiring", "expired", "due", "questions")
MAX_FIELD = 2000
MAX_CONDITIONS = 20


def today() -> date:
    return date.today()


def scope_words(scope: dict | None) -> str:
    """{"sites": ["Quayside"], "channels": ["delivery"]} -> "only: Quayside branch; delivery channel"."""
    parts = [f"{' or '.join(str(v) for v in vals)} {DIM_WORD[d]}"
             for d, _ in DIMS if (vals := (scope or {}).get(d))]
    return "only: " + "; ".join(parts) if parts else "everywhere"


def _day(v) -> date | None:
    try:
        return date.fromisoformat(v) if isinstance(v, str) and DAY.match(v) else None
    except ValueError:
        return None


def fact_view(f: dict, day: date) -> dict:
    """One fact as the list shows it: status chips, what needs attention, scope in words."""
    st = str(f.get("status") or "draft")
    pending = st == "draft" or (f.get("latest_version") or 0) > (f.get("version") or 0)
    vto, rby = _day(f.get("valid_to")), _day(f.get("review_by"))
    chips, attention = [], []
    if st == "active":
        chips.append(("active", "ok"))
    elif st != "draft":
        chips.append((st, "bad" if st == "expired" else "muted"))
    if pending:
        chips.append(("draft: needs your confirmation", "warn"))
        attention.append("drafts")
    if st == "expired" or (st == "active" and vto and vto < day):
        attention.append("expired")
    elif st == "active" and vto and vto <= day + timedelta(days=30):
        chips.append((f"expires {vto.isoformat()}", "warn"))
        attention.append("expiring")
    if st == "active" and rby and rby <= day:
        chips.append(("due for review", "warn"))
        attention.append("due")
    subj = f.get("subject") or {}
    src = f.get("source") or {}
    return {"f": f, "key": f.get("key"), "status": st, "pending": pending, "chips": chips, "attention": attention,
            "derived": bool(f.get("derived")), "scope": scope_words(f.get("scope")),
            "sensitivity": f.get("sensitivity") or "internal",
            "subject": (subj.get("kind") or "business", subj.get("ref") or ""),
            "source": " · ".join(x for x in (src.get("kind"), src.get("ref")) if x),
            "can_confirm": pending and not f.get("derived"),
            "can_retire": st in ("active", "expired", "draft") and not f.get("derived")}


def group(views: list[dict]) -> list[dict]:
    """Group by subject; facts needing attention first inside each group, groups with any first."""
    groups: dict[tuple, list] = {}
    for v in views:
        groups.setdefault(v["subject"], []).append(v)
    out = [{"kind": k, "ref": r, "facts": sorted(fs, key=lambda v: (not v["attention"], v["key"] or ""))}
           for (k, r), fs in groups.items()]
    return sorted(out, key=lambda g: (not any(v["attention"] for v in g["facts"]), g["kind"], g["ref"].lower()))


# ------------------------------------------------------------------ the form

def _scalar(v: str):
    v = v.strip()
    if v == "":
        return None
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if re.fullmatch(r"-?\d{1,15}", v):
        return int(v)
    if re.fullmatch(r"-?\d{1,15}\.\d{1,6}", v):
        return float(v)
    return v[:200]


def _list(text: str, sep: str = ",") -> list[str]:
    parts = re.split(r"\n" if sep == "\n" else r"[,\n]", (text or "")[:MAX_FIELD])
    return [p.strip() for p in parts if p.strip()]


def blank_values() -> dict:
    return {"key": "", "subject_kind": "business", "subject_ref": "", "fact_type": "claim", "text": "",
            "value_text": "", "value": "", "unit": "", "currency": "", "valid_from": "", "valid_to": "",
            "sensitivity": "internal", "attribute": "", "basis": "", "review_by": "", "source_kind": "",
            "source_ref": "", "claim_class": "none", "required_disclosures": "", "allowed_phrasing": "",
            "forbidden_phrasing": "", "owner": "", "risk": "low", "requires_evidence": False, "evidence_ref": "",
            "supersedes_key": "", **{f"scope_{d}": "" for d, _ in DIMS}}


def fact_values(f: dict) -> tuple[dict, list[dict]]:
    """A Fact (05) -> form values and condition rows."""
    v = blank_values()
    subj, src, scope = f.get("subject") or {}, f.get("source") or {}, f.get("scope") or {}
    s = lambda x: "" if x is None else str(x)  # noqa: E731
    v.update(key=s(f.get("key")), subject_kind=s(subj.get("kind")) or "business", subject_ref=s(subj.get("ref")),
             fact_type=s(f.get("fact_type")) or "claim", text=s(f.get("text")), value_text=s(f.get("value_text")),
             value=s(f.get("value")), unit=s(f.get("unit")), currency=s(f.get("currency")),
             valid_from=s(f.get("valid_from")), valid_to=s(f.get("valid_to")),
             sensitivity=s(f.get("sensitivity")) or "internal", attribute=s(f.get("attribute")),
             basis=s(f.get("basis")), review_by=s(f.get("review_by")), source_kind=s(src.get("kind")),
             source_ref=s(src.get("ref")), claim_class=s(f.get("claim_class")) or "none",
             required_disclosures="\n".join(f.get("required_disclosures") or []),
             allowed_phrasing="\n".join(f.get("allowed_phrasing") or []),
             forbidden_phrasing="\n".join(f.get("forbidden_phrasing") or []),
             owner=s(f.get("owner")), risk=s(f.get("risk")) or "low",
             requires_evidence=bool(f.get("requires_evidence")), evidence_ref=s(f.get("evidence_ref")),
             supersedes_key=s(f.get("supersedes_key")),
             **{f"scope_{d}": ", ".join(scope.get(d) or []) for d, _ in DIMS})
    conds = [{"key": s(c.get("key")), "op": s(c.get("op")) or "=",
              "value": ", ".join(s(x) for x in c["value"]) if isinstance(c.get("value"), list) else s(c.get("value"))}
             for c in f.get("conditions") or [] if isinstance(c, dict)]
    return v, conds


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-")


def parse_form(form, key_fixed: str | None = None) -> tuple[dict, dict, dict, list[dict]]:
    """Form -> (Fact body for 05, errors {field: [msg]}, values to re-show, condition rows)."""
    get = lambda k, n=MAX_FIELD: str(form.get(k, "") or "")[:n]  # noqa: E731
    v = blank_values()
    for k in v:
        if k != "requires_evidence":
            v[k] = get(k).strip() if k not in ("required_disclosures", "allowed_phrasing", "forbidden_phrasing") else get(k)
    v["requires_evidence"] = form.get("requires_evidence") == "yes"
    v["currency"] = v["currency"].upper()
    errors: dict[str, list[str]] = {}
    err = lambda f, m: errors.setdefault(f, []).append(m)  # noqa: E731

    if key_fixed:
        v["key"] = key_fixed
    elif not v["key"]:
        v["key"] = slug(" ".join(x for x in (v["subject_ref"], v["attribute"] or v["fact_type"]) if x))
    if not KEY.match(v["key"]):
        err("key", "2 to 60 characters: small letters, digits and dashes (like weekday-rate)")
    if not v["text"]:
        err("text", "required: the fact as one plain sentence")
    elif len(v["text"]) > 500:
        err("text", "at most 500 characters")
    for name, allowed in (("subject_kind", SUBJECT_KINDS), ("fact_type", FACT_TYPES), ("basis", BASES),
                          ("sensitivity", list(SENSITIVITY)), ("claim_class", CLAIM_CLASSES), ("risk", RISKS),
                          ("source_kind", ["", *SOURCE_KINDS])):
        if v[name] not in allowed:
            err(name, "pick one from the list")
    for name in ("valid_from", "valid_to", "review_by"):
        if v[name] and _day(v[name]) is None:
            err(name, "a date like 2026-10-01")
    if _day(v["valid_from"]) and _day(v["valid_to"]) and v["valid_from"] > v["valid_to"]:
        err("valid_to", "must not be before 'valid from'")
    if v["currency"] and not re.fullmatch(r"[A-Z]{3}", v["currency"]):
        err("currency", "three letters, like GBP")
    if v["source_ref"] and not v["source_kind"]:
        err("source_kind", "say what kind of source it is")
    if v["supersedes_key"] and not KEY.match(v["supersedes_key"]):
        v["supersedes_key"] = ""
    for name, n in (("subject_ref", 120), ("value_text", 200), ("unit", 30), ("attribute", 60), ("owner", 80),
                    ("source_ref", 300), ("evidence_ref", 200), ("value", 200)):
        if len(v[name]) > n:
            err(name, f"at most {n} characters")

    keys, ops, cvals = (form.getlist(n)[:MAX_CONDITIONS] for n in ("c_key", "c_op", "c_value"))
    conds, rows = [], []
    for i in range(max(len(keys), len(ops), len(cvals))):
        ck = str(keys[i] if i < len(keys) else "").strip()[:60]
        op = str(ops[i] if i < len(ops) else "=").strip()
        cv = str(cvals[i] if i < len(cvals) else "").strip()[:500]
        rows.append({"key": ck, "op": op, "value": cv})
        if not ck and not cv:
            continue
        if not ck:
            err("conditions", f"Condition {i + 1}: name what it depends on (like guests)")
            continue
        if op not in OPS:
            err("conditions", f"Condition {i + 1}: pick a comparison")
            continue
        val = [_scalar(x) for x in _list(cv)] if op in ("in", "not_in") else _scalar(cv)
        conds.append({"key": ck, "op": op, "value": val})

    scope = {}
    for d, _ in DIMS:
        vals = _list(v[f"scope_{d}"])
        if any(len(x) > 80 for x in vals) or len(vals) > 50:
            err(f"scope_{d}", "at most 50 names of 80 characters")
        scope[d] = vals[:50]
    lines = lambda k, n: [x[:n] for x in _list(v[k], "\n")]  # noqa: E731
    body = {
        "key": v["key"], "subject": {"kind": v["subject_kind"], "ref": v["subject_ref"]},
        "fact_type": v["fact_type"], "attribute": v["attribute"] or None, "value": _scalar(v["value"]),
        "unit": v["unit"] or None, "currency": v["currency"] or None, "value_text": v["value_text"] or None,
        "basis": v["basis"] or None, "conditions": conds, "scope": scope,
        "valid_from": v["valid_from"] or None, "valid_to": v["valid_to"] or None, "review_by": v["review_by"] or None,
        "source": {"kind": v["source_kind"], "ref": v["source_ref"]} if v["source_kind"] else None,
        "claim_class": v["claim_class"], "requires_evidence": v["requires_evidence"],
        "evidence_ref": v["evidence_ref"] or None,
        "required_disclosures": lines("required_disclosures", 200)[:10],
        "allowed_phrasing": lines("allowed_phrasing", 120)[:20],
        "forbidden_phrasing": lines("forbidden_phrasing", 120)[:20],
        "owner": v["owner"] or None, "risk": v["risk"], "sensitivity": v["sensitivity"],
        "supersedes_key": v["supersedes_key"] or None, "text": v["text"],
    }
    return body, errors, v, rows


def field_of(loc: list) -> str:
    """05 422 loc -> form field."""
    loc = [p for p in loc if p != "body"]
    if not loc:
        return "general"
    top = str(loc[0])
    if top == "subject":
        return "subject_kind" if len(loc) > 1 and loc[1] == "kind" else "subject_ref"
    if top == "source":
        return "source_kind" if len(loc) > 1 and loc[1] == "kind" else "source_ref"
    if top == "scope":
        return f"scope_{loc[1]}" if len(loc) > 1 else "general"
    if top in ("conditions",):
        return "conditions"
    return top


def errors_from(e: BackendError) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for x in e.errors:
        out.setdefault(field_of(list(x.get("loc") or [])), []).append(friendly(x))
    if not out:
        out["general"] = [e.detail]
    return out


# ------------------------------------------------------------------ routes

def register(app, page, current, csrf, B):
    s = app.state.settings

    def not_installed(request, session):
        return page(request, "facts.html", session, not_installed=True, nav="more", groups=[], counts={},
                    questions=[], show="all", owner_ready=bool(s.fact_owner_key))

    async def listing(request, session, show="all", status=200, done=None, error=None):
        show = show if show in SHOWS else "all"
        try:
            data = await B().facts_v2()
            facts = [f for f in data.get("facts") or [] if isinstance(f, dict)]
            err = error
        except BackendError as e:
            facts, err = [], error or f"The brand service (05) did not answer: {e.detail}"
        try:
            questions = await B().questions("open")
        except BackendError:
            questions = []
        day = today()
        views = [fact_view(f, day) for f in facts]
        counts = {k: sum(1 for v in views if k in v["attention"]) for k in ("drafts", "expiring", "expired", "due")}
        counts["questions"] = len(questions)
        counts["attention"] = sum(1 for v in views if v["attention"])
        if show == "attention":
            views = [v for v in views if v["attention"]]
        elif show in ("drafts", "expiring", "expired", "due"):
            views = [v for v in views if show in v["attention"]]
        elif show == "questions":
            views = []
        return page(request, "facts.html", session, status, nav="more", groups=group(views), counts=counts,
                    questions=questions, show=show, error=err, done=done, total=len(facts),
                    owner_ready=bool(s.fact_owner_key), sensitivity=SENSITIVITY)

    @app.get("/facts", response_class=HTMLResponse)
    async def facts(request: Request, show: str = "all", done: str = "", session=Depends(current)):
        if not s.brand_url:
            return not_installed(request, session)
        return await listing(request, session, show[:20], done=done[:200] or None)

    def edit_page(request, session, status=200, **ctx):
        ctx.setdefault("errors", {})
        return page(request, "fact_edit.html", session, status, nav="more", subject_kinds=SUBJECT_KINDS,
                    fact_types=FACT_TYPES, bases=BASES, source_kinds=SOURCE_KINDS, claim_classes=CLAIM_CLASSES,
                    sensitivity=SENSITIVITY, risks=RISKS, ops=OPS, dims=DIMS, **ctx)

    def with_blank_row(rows: list[dict]) -> list[dict]:
        return [*rows, {"key": "", "op": "=", "value": ""}] if len(rows) < MAX_CONDITIONS else rows

    @app.get("/facts/new", response_class=HTMLResponse)
    async def fact_new(request: Request, session=Depends(current)):
        if not s.brand_url:
            return not_installed(request, session)
        return edit_page(request, session, mode="new", values=blank_values(), conds=with_blank_row([]))

    async def save(request, session, key: str | None):
        form = await request.form()
        body, errors, values, rows = parse_form(form, key)
        ctx = dict(mode="edit" if key else "new", values=values)
        if form.get("action") == "add_condition":
            return edit_page(request, session, conds=with_blank_row(rows), **ctx)
        if errors:
            return edit_page(request, session, 422, errors=errors, conds=rows or with_blank_row([]), **ctx)
        try:
            saved = await (B().fact_update(key, body) if key else B().fact_create(body))
        except BackendError as e:
            if e.status == 422:
                return edit_page(request, session, 422, errors=errors_from(e), conds=rows or with_blank_row([]), **ctx)
            msg = e.detail if e.status == 409 else f"Not saved: the brand service (05) said {e.detail}"
            return edit_page(request, session, 409 if e.status == 409 else 502, errors={"general": [msg]},
                             conds=rows or with_blank_row([]), **ctx)
        k = saved.get("key") if isinstance(saved, dict) else None
        return RedirectResponse(f"/facts?done=saved:{k or body['key']}", status_code=303)

    @app.post("/facts/new", response_class=HTMLResponse)
    async def fact_create(request: Request, session=Depends(csrf)):
        if not s.brand_url:
            return not_installed(request, session)
        return await save(request, session, None)

    @app.get("/facts/{key}/edit", response_class=HTMLResponse)
    async def fact_edit(request: Request, key: str, session=Depends(current)):
        if not KEY.match(key):
            raise HTTPException(404, "no such fact")
        if not s.brand_url:
            return not_installed(request, session)
        try:
            f = await B().fact_v2(key)
        except BackendError as e:
            return page(request, "error.html", session, e.status if e.status == 404 else 502, error=str(e), nav="more")
        if f.get("derived"):
            return RedirectResponse("/facts?done=derived", status_code=303)
        versions = [x for x in f.get("versions") or [] if isinstance(x, dict)]
        latest = versions[-1].get("data") if versions and isinstance(versions[-1].get("data"), dict) else f
        values, conds = fact_values({**latest, "key": key})
        return edit_page(request, session, mode="edit", values=values, conds=with_blank_row(conds), fact=f,
                         versions=versions)

    @app.post("/facts/{key}/edit", response_class=HTMLResponse)
    async def fact_update(request: Request, key: str, session=Depends(csrf)):
        if not KEY.match(key):
            raise HTTPException(404, "no such fact")
        return await save(request, session, key)

    async def owner_action(request, session, call, done: str):
        if not s.brand_url:
            return not_installed(request, session)
        try:
            await call()
        except BackendError as e:
            code = e.status if e.status in (403, 404, 409, 503) else 502
            return await listing(request, session, status=code, error=f"Not done: {e.detail}")
        return RedirectResponse(f"/facts?done={done}", status_code=303)

    @app.post("/facts/{key}/confirm")
    async def fact_confirm(request: Request, key: str, session=Depends(csrf)):
        if not KEY.match(key):
            raise HTTPException(404, "no such fact")
        return await owner_action(request, session, lambda: B().fact_confirm(key), f"confirmed:{key}")

    @app.post("/facts/{key}/retire")
    async def fact_retire(request: Request, key: str, session=Depends(csrf)):
        if not KEY.match(key):
            raise HTTPException(404, "no such fact")
        return await owner_action(request, session, lambda: B().fact_retire(key), f"retired:{key}")

    @app.post("/facts/{key}/convert", response_class=HTMLResponse)
    async def fact_convert(request: Request, key: str, session=Depends(csrf)):
        """A derived (brand.yaml) fact -> a new draft that supersedes it; then edit its scope."""
        if not KEY.match(key):
            raise HTTPException(404, "no such fact")
        if not s.brand_url:
            return not_installed(request, session)
        try:
            f = await B().fact_v2(key)
        except BackendError as e:
            return await listing(request, session, status=e.status if e.status == 404 else 502, error=f"Not done: {e.detail}")
        if not f.get("derived"):
            return RedirectResponse(f"/facts/{key}/edit", status_code=303)
        new_key = f"{key[:52].rstrip('-')}-scoped"
        values, conds = fact_values({**f, "key": new_key, "supersedes_key": key})
        body, errors, _, _ = parse_form(_Form({**values, "c_key": [c["key"] for c in conds],
                                               "c_op": [c["op"] for c in conds], "c_value": [c["value"] for c in conds]}))
        body["sensitivity"] = f.get("sensitivity") or "public"
        try:
            await B().fact_create(body)
        except BackendError as e:
            if e.status != 409:   # 409: that draft exists already -> just open it
                return await listing(request, session, status=502 if e.status != 422 else 422, error=f"Not done: {e.detail}")
        return RedirectResponse(f"/facts/{new_key}/edit", status_code=303)

    # ---- starter kits and their rules
    @app.get("/facts/kits", response_class=HTMLResponse)
    async def kits(request: Request, kit: str = "", session=Depends(current)):
        if not s.brand_url:
            return not_installed(request, session)
        try:
            all_kits, err = await B().starter_kits(), None
        except BackendError as e:
            all_kits, err = [], f"The brand service (05) did not answer: {e.detail}"
        chosen = next((k for k in all_kits if k.get("id") == kit), None)
        try:
            rules = await B().kit_rules()
        except BackendError:
            rules = []
        return page(request, "facts_kits.html", session, nav="more", kits=all_kits, kit=chosen, rules=rules,
                    error=err, owner_ready=bool(s.fact_owner_key), applied=None)

    @app.post("/facts/kits/{kit_id}/apply", response_class=HTMLResponse)
    async def kit_apply(request: Request, kit_id: str, session=Depends(csrf)):
        if not re.fullmatch(r"[a-z0-9-]{2,40}", kit_id):
            raise HTTPException(404, "no such kit")
        if not s.brand_url:
            return not_installed(request, session)
        try:
            await B().starter_kit_apply(kit_id)
        except BackendError as e:
            code = e.status if e.status in (403, 404, 503) else 502
            try:
                all_kits = await B().starter_kits()
            except BackendError:
                all_kits = []
            return page(request, "facts_kits.html", session, code, nav="more", kits=all_kits, rules=[],
                        kit=next((k for k in all_kits if k.get("id") == kit_id), None),
                        error=f"Not applied: {e.detail}", owner_ready=bool(s.fact_owner_key), applied=None)
        return RedirectResponse(f"/facts/kits?kit={kit_id}#rules", status_code=303)

    @app.post("/facts/rules/{rid}/{action}")
    async def kit_rule(request: Request, rid: int, action: str, session=Depends(csrf)):
        if action not in ("confirm", "dismiss"):
            raise HTTPException(404, "not found")
        try:
            await B().kit_rule_set(rid, action)
        except BackendError as e:
            code = e.status if e.status in (403, 404, 503) else 502
            return page(request, "error.html", session, code, error=f"Not done: {e.detail}", nav="more")
        return RedirectResponse("/facts/kits#rules", status_code=303)

    # ---- open questions
    @app.post("/facts/questions/{qid}/{action}")
    async def question(request: Request, qid: int, action: str, session=Depends(csrf)):
        if action not in ("answer", "dismiss"):
            raise HTTPException(404, "not found")
        form = await request.form()
        answer = str(form.get("answer", "")).strip()
        if action == "answer" and not answer:
            return await listing(request, session, "questions", 422, error="Write an answer first (or dismiss the question).")
        if len(answer) > 2000:
            return await listing(request, session, "questions", 422, error="An answer is at most 2000 characters.")
        try:
            await (B().question_answer(qid, answer) if action == "answer" else B().question_dismiss(qid))
        except BackendError as e:
            return await listing(request, session, "questions", e.status if e.status in (404, 409) else 502,
                                 error=f"Not done: {e.detail}")
        return RedirectResponse(f"/facts?show=questions&done={action}ed", status_code=303)


class _Form(dict):
    """A dict that answers like a form (get / getlist), to reuse parse_form for a conversion."""

    def getlist(self, k):
        v = self.get(k)
        return v if isinstance(v, list) else ([] if v is None else [v])

    def get(self, k, default=None):
        v = super().get(k, default)
        return ("yes" if v else "") if k == "requires_evidence" and isinstance(v, bool) else v
