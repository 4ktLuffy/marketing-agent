"""Brand setup: a guided form that fills the brand profile (05) and the voice profile, so nobody
has to edit brand.yaml by hand.

Steps: 1 basics, 2 products, 3 facts, 4 rules, 5 voice interview, 6 review. Steps 1-4 save
through 05 `PUT /brand/editable` (05 validates every field; its errors are shown next to the
field). Step 5 sends the interview answers to the gateway's `voice_profile` prompt, shows the
result for review, and saves it with 05 `PUT /voice`. Every call is made here, server-side:
the keys never reach the browser.
"""
import re

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .backends import BackendError

STEPS = [(1, "Basics"), (2, "Products"), (3, "Facts"), (4, "Rules"), (5, "Voice"), (6, "Review")]
CACHE_NOTE = "The agent uses this within 60 s."   # 03 gateway: brand summary and facts cached 60 s
MAX_LINES = 200          # lines read from one textarea (05 enforces the real limits)
MAX_FIELD = 20000        # characters read from one field
MAX_ANSWER = 2000        # characters per interview answer
PRODUCT_FIELDS = {"name": "name", "one_line": "one-line description", "price": "price", "aliases": "other names"}
VOICE_LIST_FIELDS = ("do", "dont", "sample_lines")
VOICE_WORD_FIELDS = ("words_we_use", "words_we_avoid")


def lines(text: str) -> tuple[list[str], list[int]]:
    """Non-blank lines and their 1-based line numbers (to point an error at the right line)."""
    out, nums = [], []
    for i, ln in enumerate((text or "")[:MAX_FIELD].splitlines()[:MAX_LINES], 1):
        if ln.strip():
            out.append(ln.strip())
            nums.append(i)
    return out, nums


def words(text: str) -> list[str]:
    return [w.strip() for w in re.split(r"[,\n]", (text or "")[:MAX_FIELD]) if w.strip()]


def friendly(e: dict) -> str:
    msg = str(e.get("msg", "invalid")).removeprefix("Value error, ")
    return "required" if msg in ("String should have at least 1 character", "Field required") else msg


def humanize(errors: list, field_of, number_of=None) -> dict[str, list[str]]:
    """05/pydantic errors -> {form field: [message]}. `field_of(loc)` names the form field;
    `number_of(field, index)` turns a list index into the line or row the person sees."""
    out: dict[str, list[str]] = {}
    for e in errors:
        loc = [p for p in e.get("loc", []) if p != "body"]
        if not loc:
            continue
        msg = friendly(e)
        field = field_of(loc)
        idx = next((p for p in loc[1:] if isinstance(p, int)), None)
        if idx is not None and number_of:
            msg = f"{number_of(loc[0], idx)}: {msg}"
        out.setdefault(field, []).append(msg)
    return out


# ------------------------------------------------------------------ form -> 05 fields, per step

def parse_basics(form) -> tuple[dict, dict, dict]:
    f = {k: str(form.get(k, ""))[:1000] for k in ("name", "one_liner", "website", "audience_primary",
                                                    "audience_secondary", "tone")}
    body = {"name": f["name"], "one_liner": f["one_liner"], "website": f["website"],
            "audience": {"primary": f["audience_primary"], "secondary": f["audience_secondary"]},
            "voice": {"tone": f["tone"]}}
    return body, {}, {"values": f}


def product_rows(form) -> list[dict]:
    get = lambda k: [str(v)[:1000] for v in form.getlist(k)][:60]  # noqa: E731
    names, one_lines, prices, aliases = get("p_name"), get("p_one_line"), get("p_price"), get("p_aliases")
    removed = set(get("p_remove"))
    n = max(len(names), len(one_lines), len(prices), len(aliases))
    pad = lambda xs: xs + [""] * (n - len(xs))  # noqa: E731
    return [{"name": a, "one_line": b, "price": c, "aliases": d, "remove": str(i) in removed}
            for i, (a, b, c, d) in enumerate(zip(pad(names), pad(one_lines), pad(prices), pad(aliases)))]


def parse_products(form) -> tuple[dict, dict, dict]:
    rows = product_rows(form)
    products, row_of = [], []
    for i, r in enumerate(rows):
        if r["remove"] or not any(r[k].strip() for k in PRODUCT_FIELDS):
            continue
        products.append({"name": r["name"], "one_line": r["one_line"], "price": r["price"],
                         "aliases": words(r["aliases"])})
        row_of.append(i + 1)
    return {"products": products}, {}, {"rows": rows, "row_of": row_of}


def parse_facts(form) -> tuple[dict, dict, dict]:
    facts, fnums = lines(str(form.get("facts", "")))
    msgs, mnums = lines(str(form.get("key_messages", "")))
    values = {"facts": str(form.get("facts", ""))[:MAX_FIELD], "key_messages": str(form.get("key_messages", ""))[:MAX_FIELD]}
    return {"facts": facts, "key_messages": msgs}, {}, {"values": values, "nums": {"facts": fnums, "key_messages": mnums}}


def parse_rules(form) -> tuple[dict, dict, dict]:
    raw = {k: str(form.get(k, ""))[:MAX_FIELD] for k in ("banned_phrases", "emoji_allowed", "emoji_max",
                                                         "allowed_domains", "disclaimers")}
    local: dict[str, list[str]] = {}
    banned, bnums = lines(raw["banned_phrases"])
    domains, dnums = lines(raw["allowed_domains"])
    emoji = [e for e in re.split(r"[\s,]+", raw["emoji_allowed"]) if e]
    emoji_max = None
    if raw["emoji_max"].strip():
        try:
            emoji_max = int(raw["emoji_max"].strip())
        except ValueError:
            local["emoji_max"] = ["a whole number, like 2 (blank = no limit)"]
    disclaimers = {}
    for i, ln in enumerate(raw["disclaimers"].splitlines()[:MAX_LINES], 1):
        if not ln.strip():
            continue
        channel, sep, rest = ln.partition(":")
        opts = [o.strip() for o in rest.split(",") if o.strip()]
        if not sep or not channel.strip() or not opts:
            local.setdefault("disclaimers", []).append(f"Line {i}: write it as channel: text, other text")
            continue
        disclaimers[channel.strip().lower()] = opts
    body = {"banned_phrases": banned, "allowed_domains": domains, "required_disclaimers": disclaimers,
            "emoji_policy": {"allowed": emoji, "max_per_post": emoji_max}}
    return body, local, {"values": raw, "nums": {"banned_phrases": bnums, "allowed_domains": dnums}}


PARSERS = {1: parse_basics, 2: parse_products, 3: parse_facts, 4: parse_rules}


def field_of(loc: list) -> str:
    top = str(loc[0])
    if top == "audience":
        return f"audience_{loc[1]}" if len(loc) > 1 else "audience_primary"
    if top == "voice":
        return "tone"
    if top == "emoji_policy":
        return "emoji_max" if len(loc) > 1 and loc[1] == "max_per_post" else "emoji_allowed"
    if top == "required_disclaimers":
        return "disclaimers"
    return top


def number_of_for(step: int, extra: dict):
    def number_of(field, idx):
        if step == 2:
            row_of = extra.get("row_of") or []
            return f"Row {row_of[idx] if idx < len(row_of) else idx + 1}"
        nums = (extra.get("nums") or {}).get(field) or []
        return f"Line {nums[idx] if idx < len(nums) else idx + 1}"
    return number_of


def product_errors(errors: list, row_of: list[int]) -> tuple[dict[int, list[str]], list[str]]:
    per_row: dict[int, list[str]] = {}
    general = []
    for e in errors:
        loc = [p for p in e.get("loc", []) if p != "body"]
        msg = friendly(e)
        if len(loc) >= 2 and loc[0] == "products" and isinstance(loc[1], int):
            row = row_of[loc[1]] if loc[1] < len(row_of) else loc[1] + 1
            col = PRODUCT_FIELDS.get(str(loc[2]), str(loc[2])) if len(loc) > 2 else ""
            per_row.setdefault(row, []).append(f"{col}: {msg}" if col else msg)
        else:
            general.append(msg)
    return per_row, general


# ------------------------------------------------------------------ doc -> form values

def basics_values(b: dict) -> dict:
    return {"name": b["name"], "one_liner": b["one_liner"], "website": b["website"],
            "audience_primary": b["audience"]["primary"], "audience_secondary": b["audience"]["secondary"],
            "tone": b["voice"]["tone"]}


def rows_from(b: dict, blank: int = 1) -> list[dict]:
    rows = [{"name": p["name"], "one_line": p["one_line"], "price": p.get("price") or "",
             "aliases": ", ".join(p.get("aliases") or []), "remove": False} for p in b["products"]]
    return rows + [{"name": "", "one_line": "", "price": "", "aliases": "", "remove": False} for _ in range(blank)]


def facts_values(b: dict) -> dict:
    return {"facts": "\n".join(b["facts"]), "key_messages": "\n".join(b["key_messages"])}


def rules_values(b: dict) -> dict:
    ep = b["emoji_policy"]
    return {"banned_phrases": "\n".join(b["banned_phrases"]), "emoji_allowed": " ".join(ep["allowed"]),
            "emoji_max": "" if ep["max_per_post"] is None else str(ep["max_per_post"]),
            "allowed_domains": "\n".join(b["allowed_domains"]),
            "disclaimers": "\n".join(f"{k}: {', '.join(v)}" for k, v in b["required_disclaimers"].items())}


def voice_values(p: dict | None) -> dict:
    p = p or {}
    return {"summary": p.get("summary", ""), "sentence_style": p.get("sentence_style", ""),
            **{k: "\n".join(p.get(k) or []) for k in VOICE_LIST_FIELDS},
            **{k: ", ".join(p.get(k) or []) for k in VOICE_WORD_FIELDS}}


def voice_from_form(form) -> tuple[dict, dict]:
    raw = {k: str(form.get(k, ""))[:MAX_FIELD] for k in ("summary", "sentence_style", *VOICE_LIST_FIELDS,
                                                         *VOICE_WORD_FIELDS)}
    prof = {"summary": raw["summary"].strip(), "sentence_style": raw["sentence_style"].strip(),
            **{k: lines(raw[k])[0] for k in VOICE_LIST_FIELDS}, **{k: words(raw[k]) for k in VOICE_WORD_FIELDS}}
    return prof, raw


def interview_text(questions: list[str], answers: list[str]) -> str:
    return "\n\n".join(f"Q: {q}\nA: {a.strip()}" for q, a in zip(questions, answers) if a.strip())


# ------------------------------------------------------------------ routes

def register(app, page, current, csrf, B):
    def render(request, session, step: int, status: int = 200, **ctx):
        htmx = bool(request.headers.get("HX-Request"))
        name = "_brand_step.html" if htmx and request.method == "POST" else "brand.html"
        if htmx and request.method == "POST":
            status = 200   # htmx swaps only 2xx answers; errors and messages are in the fragment
        return page(request, name, session, status, step=step, steps=STEPS, nav="more", note=CACHE_NOTE, **ctx)

    async def load_doc():
        try:
            return (await B().brand_editable()), None
        except BackendError as e:
            return None, f"The brand service (05) did not answer: {e.detail}"

    async def step_ctx(step: int) -> dict:
        if step in (1, 2, 3, 4):
            doc, err = await load_doc()
            if doc is None:
                return {"error": err}
            b = doc["brand"]
            ctx = {"doc": doc}
            if step == 1:
                ctx["values"] = basics_values(b)
            elif step == 2:
                ctx["rows"] = rows_from(b)
            elif step == 3:
                ctx["values"] = facts_values(b)
            else:
                ctx["values"] = rules_values(b)
            return ctx
        if step == 5:
            try:
                qs, voice = await B().voice_questions(), await B().voice()
            except BackendError as e:
                return {"error": f"The brand service (05) did not answer: {e.detail}"}
            return {"questions": qs, "answers": [""] * len(qs), "voice": voice}
        comp_summary = []
        for call in (B().brand_completeness, B().brand_summary, B().brand_editable):
            try:
                comp_summary.append(await call())
            except BackendError as e:
                comp_summary.append(e)
        comp, summary, doc = comp_summary
        errors = [x.detail for x in comp_summary if isinstance(x, BackendError)]
        return {"completeness": None if isinstance(comp, BackendError) else comp,
                "summary": None if isinstance(summary, BackendError) else summary,
                "doc": None if isinstance(doc, BackendError) else doc,
                "error": f"The brand service (05) did not answer: {errors[0]}" if errors else None}

    @app.get("/brand", response_class=HTMLResponse)
    async def brand_home(request: Request, session=Depends(current)):
        try:
            comp, err = await B().brand_completeness(), None
        except BackendError as e:
            comp, err = None, f"The brand service (05) did not answer: {e.detail}"
        return page(request, "brand.html", session, step=0, steps=STEPS, nav="more", note=CACHE_NOTE,
                    completeness=comp, error=err)

    @app.get("/brand/step/{step}", response_class=HTMLResponse)
    async def brand_step(request: Request, step: int, saved: int = 0, session=Depends(current)):
        if step not in dict(STEPS):
            raise HTTPException(404, "no such step")
        return render(request, session, step, saved=bool(saved), **(await step_ctx(step)))

    @app.post("/brand/step/{step}", response_class=HTMLResponse)
    async def brand_save(request: Request, step: int, session=Depends(csrf)):
        if step not in PARSERS:
            raise HTTPException(404, "no such step")
        form = await request.form()
        body, local, extra = PARSERS[step](form)
        ctx = {"values": extra.get("values"), "rows": extra.get("rows")}
        if step == 2 and form.get("action") == "add":      # one more blank row, nothing saved
            ctx["rows"] = [*extra["rows"], {"name": "", "one_line": "", "price": "", "aliases": "", "remove": False}]
            return render(request, session, step, **ctx)
        if local:
            return render(request, session, step, 422, errors=local, **ctx)
        try:
            doc = await B().brand_save(body)
        except BackendError as e:
            if e.status != 422:
                return render(request, session, step, 502, error=f"Not saved: the brand service (05) said {e.detail}", **ctx)
            if step == 2:
                per_row, general = product_errors(e.errors, extra["row_of"])
                return render(request, session, step, 422, row_errors=per_row,
                              errors={"products": general} if general else {}, **ctx)
            return render(request, session, step, 422, errors=humanize(e.errors, field_of, number_of_for(step, extra)), **ctx)
        if not request.headers.get("HX-Request"):
            return RedirectResponse(f"/brand/step/{step}?saved=1", status_code=303)
        b = doc["brand"]
        fresh = {1: {"values": basics_values(b)}, 2: {"rows": rows_from(b)}, 3: {"values": facts_values(b)},
                 4: {"values": rules_values(b)}}[step]
        return render(request, session, step, saved=True, doc=doc, **fresh)

    # ---- step 5: interview -> gateway voice_profile -> review -> 05 PUT /voice
    @app.post("/brand/voice/generate", response_class=HTMLResponse)
    async def voice_generate(request: Request, session=Depends(csrf)):
        form = await request.form()
        try:
            qs = await B().voice_questions()
        except BackendError as e:
            return render(request, session, 5, 502, error=f"The brand service (05) did not answer: {e.detail}")
        answers = [str(form.get(f"a{i}", ""))[:MAX_ANSWER] for i in range(len(qs))]
        ctx = {"questions": qs, "answers": answers}
        if sum(1 for a in answers if a.strip()) < 3:
            return render(request, session, 5, 422, errors={"answers": ["Answer at least 3 questions (more is better)."]}, **ctx)
        try:
            profile = await B().voice_generate(interview_text(qs, answers))
        except BackendError as e:
            return render(request, session, 5, 502, error=f"The model could not write the profile: {e.detail}", **ctx)
        return render(request, session, 5, review=voice_values(profile), **ctx)

    @app.post("/brand/voice/save", response_class=HTMLResponse)
    async def voice_save(request: Request, session=Depends(csrf)):
        form = await request.form()
        prof, raw = voice_from_form(form)
        try:
            saved = await B().voice_save(prof)
        except BackendError as e:
            if e.status != 422:
                return render(request, session, 5, 502, review=raw, error=f"Not saved: the brand service (05) said {e.detail}")
            errs = humanize(e.errors, lambda loc: str(loc[0]), lambda f, i: f"Line {i + 1}")
            return render(request, session, 5, 422, review=raw, errors=errs)
        if not request.headers.get("HX-Request"):
            return RedirectResponse("/brand/step/5?saved=1", status_code=303)
        return render(request, session, 5, saved=True, voice=saved)

    @app.post("/brand/reset")
    async def brand_reset(request: Request, session=Depends(csrf)):
        form = await request.form()
        if form.get("confirm") != "yes":
            return render(request, session, 6, 422, reset_error="Tick the box to confirm the reset.",
                          **(await step_ctx(6)))
        try:
            await B().brand_reset()
        except BackendError as e:
            return render(request, session, 6, 502, reset_error=f"Not reset: {e.detail}", **(await step_ctx(6)))
        return RedirectResponse("/brand/step/6?saved=1", status_code=303)
