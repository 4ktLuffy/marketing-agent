"""Calls to other services. Every URL comes from env; an empty URL means "not installed".

Required: brand service (05: facts, profile summary, brand check, fact changes) and content
calendar (19: review items). Optional: platform rules (14), claim checker (44, only with
MODEL_CHECK=auto), lead hub (80, for the blockers list). A missing optional service is skipped
with a note; nothing here ever raises on an optional service being absent.
"""
import os

import httpx

ORIGIN = "task-bridge"


class ServiceError(RuntimeError):
    pass


def _url(name: str) -> str:
    return (os.environ.get(name) or "").strip().rstrip("/")


def brand_url() -> str:
    return _url("BRAND_URL")


def rules_url() -> str:
    return _url("RULES_URL")


def calendar_url() -> str:
    return _url("CALENDAR_URL")


def claims_url() -> str:
    return _url("CLAIMS_URL")


def leads_url() -> str:
    return _url("LEADS_URL")


def model_check_on() -> bool:
    return (os.environ.get("MODEL_CHECK") or "off").strip().lower() == "auto" and bool(claims_url())


def _timeout(name: str = "SERVICE_TIMEOUT", default: float = 15.0) -> float:
    try:
        return max(1.0, float(os.environ.get(name) or default))
    except ValueError:
        return default


def _key_headers() -> dict:
    key = os.environ.get("INTERNAL_API_KEY")
    return {"X-API-Key": key} if key else {}


def _call(method: str, url: str, what: str, timeout: float | None = None, ok_404: bool = False, **kw):
    try:
        r = httpx.request(method, url, headers={**_key_headers(), "X-Caller": "88 task bridge"},
                          timeout=timeout or _timeout(), **kw)
    except httpx.HTTPError as exc:
        raise ServiceError(f"{what} unreachable ({type(exc).__name__})") from exc
    if ok_404 and r.status_code == 404:
        return None
    if r.status_code >= 300:
        detail = ""
        try:
            d = r.json().get("detail")
            detail = f": {d if isinstance(d, str) else (d or {}).get('message', '')}"[:200] if d else ""
        except (ValueError, AttributeError):
            pass
        raise ServiceError(f"{what} returned HTTP {r.status_code}{detail}", ) from None
    try:
        return r.json()
    except ValueError as exc:
        raise ServiceError(f"{what} returned no JSON") from exc


def _need(base: str, name: str, what: str) -> str:
    if not base:
        raise ServiceError(f"{name} is not set: {what} is required")
    return base


# ---------- 05 brand service


def scope_params(scope: dict, at: str, max_sensitivity: str = "internal") -> list[tuple[str, str]]:
    names = {"sites": "site", "regions": "region", "channels": "channel", "segments": "segment",
             "plan_tiers": "plan_tier", "variants": "variant"}
    params = [("at", at), ("max_sensitivity", max_sensitivity)]
    for dim, p in names.items():
        for v in (scope or {}).get(dim) or []:
            params.append((p, str(v)))
    return params


def query_facts(scope: dict, at: str, max_sensitivity: str = "internal") -> dict:
    base = _need(brand_url(), "BRAND_URL", "the brand service (05)")
    out = _call("GET", f"{base}/facts/query", "brand service", params=scope_params(scope, at, max_sensitivity))
    if not isinstance(out, dict) or not isinstance(out.get("facts"), list):
        raise ServiceError("brand service returned no facts")
    out.setdefault("excluded", [])
    out.setdefault("fact_set_version", "fs-unknown")
    return out


def all_facts() -> list[dict]:
    """Every fact 05 has (any status, scope or sensitivity), for scope and expiry checks."""
    base = _need(brand_url(), "BRAND_URL", "the brand service (05)")
    out = _call("GET", f"{base}/facts/v2", "brand service")
    facts = out.get("facts") if isinstance(out, dict) else None
    if not isinstance(facts, list):
        raise ServiceError("brand service returned no facts")
    return [f for f in facts if isinstance(f, dict) and isinstance(f.get("key"), str)]


def profile_summary() -> tuple[str | None, str | None]:
    """(summary, note). The voice is optional: an error is a note, not a failure."""
    base = brand_url()
    if not base:
        return None, "voice skipped: BRAND_URL is not set"
    try:
        out = _call("GET", f"{base}/profile/summary", "brand service")
    except ServiceError as exc:
        return None, f"voice skipped: {exc}"
    s = out.get("summary") if isinstance(out, dict) else None
    return (s if isinstance(s, str) else None), None


def brand_check(text: str, channel: str) -> tuple[list[dict], str | None]:
    base = brand_url()
    if not base:
        return [], "brand check skipped: BRAND_URL is not set"
    try:
        out = _call("POST", f"{base}/check", "brand check", json={"text": text, "channel": channel})
    except ServiceError as exc:
        return [], f"brand check skipped: {exc}"
    v = out.get("violations") if isinstance(out, dict) else None
    return [x for x in v or [] if isinstance(x, dict)], None


def fact_changes(since: int) -> dict:
    base = _need(brand_url(), "BRAND_URL", "the brand service (05)")
    out = _call("GET", f"{base}/facts/changes", "brand service", params={"since": since})
    if not isinstance(out, dict) or not isinstance(out.get("changes"), list):
        raise ServiceError("brand service returned no changes")
    return out


def open_questions() -> list[dict]:
    base = brand_url()
    if not base:
        return []
    try:
        out = _call("GET", f"{base}/questions", "brand service", params={"status": "open"}, ok_404=True)
    except ServiceError:
        return []
    items = out.get("questions") if isinstance(out, dict) else out
    return [q for q in items or [] if isinstance(q, dict) and q.get("status", "open") == "open"]


# ---------- 14 platform rules (optional)


def channel_rules() -> tuple[dict | None, str | None]:
    base = rules_url()
    if not base:
        return None, "channel rules skipped: RULES_URL is not set"
    try:
        return _call("GET", f"{base}/rules", "platform rules"), None
    except ServiceError as exc:
        return None, f"channel rules skipped: {exc}"


def validate(channel: str, text: str) -> tuple[list[dict], str | None]:
    base = rules_url()
    if not base:
        return [], "platform check skipped: RULES_URL is not set"
    try:
        out = _call("POST", f"{base}/validate", "platform rules", json={"channel": channel, "text": text})
    except ServiceError as exc:
        if "HTTP 422" in str(exc):
            return [], f"platform check skipped: no rules for channel {channel}"
        return [], f"platform check skipped: {exc}"
    return [x for x in (out or {}).get("violations") or [] if isinstance(x, dict)], None


# ---------- 19 content calendar


def create_item(title: str, channel: str, body: str, status: str, notes: str, campaign: str) -> dict:
    base = _need(calendar_url(), "CALENDAR_URL", "the content calendar (19)")
    item = _call("POST", f"{base}/items", "content calendar", timeout=30,
                 json={"title": title[:120], "channel": channel, "body": body, "status": status,
                       "notes": notes, "campaign": campaign, "origin": ORIGIN, "require_bound_approval": True})
    if not isinstance(item, dict) or not isinstance(item.get("id"), int):
        raise ServiceError("content calendar returned no item id")
    return item


def get_item(item_id: int) -> dict:
    base = _need(calendar_url(), "CALENDAR_URL", "the content calendar (19)")
    return _call("GET", f"{base}/items/{item_id}", "content calendar", timeout=30)


def item_audit(item_id: int) -> list[dict] | None:
    base = calendar_url()
    try:
        out = _call("GET", f"{base}/items/{item_id}/audit", "content calendar", timeout=30, ok_404=True)
    except ServiceError:
        return None
    return out if isinstance(out, list) else None


def set_status(item_id: int, status: str, note: str) -> dict:
    """Moves that need no approver key (to draft, to rejected). 19 refuses approve/publish here."""
    base = _need(calendar_url(), "CALENDAR_URL", "the content calendar (19)")
    return _call("POST", f"{base}/items/{item_id}/status", "content calendar", timeout=30,
                 json={"status": status, "note": note})


def add_note(item_id: int, note: str) -> dict:
    base = _need(calendar_url(), "CALENDAR_URL", "the content calendar (19)")
    return _call("POST", f"{base}/items/{item_id}/notes", "content calendar", timeout=30, json={"note": note})


# ---------- 44 claim checker (optional, MODEL_CHECK=auto)


def model_check(text: str, fact_lines: list[str]) -> tuple[list[dict], str | None]:
    """Claims the checker could not support: [{claim, reasons, evidence}]. Only when MODEL_CHECK=auto."""
    if not model_check_on():
        return [], None
    try:
        out = _call("POST", f"{claims_url()}/verify", "claim checker", timeout=_timeout("CLAIMS_TIMEOUT", 120),
                    json={"text": text, "facts": fact_lines[:200]})
    except ServiceError as exc:
        return [], f"model check skipped: {exc}"
    claims = [c for c in (out or {}).get("claims") or [] if isinstance(c, dict) and not c.get("supported", True)]
    return claims, None


# ---------- 80 lead hub (optional)


def leads_waiting() -> int | None:
    base = leads_url()
    if not base:
        return None
    try:
        out = _call("GET", f"{base}/stats", "lead hub")
    except ServiceError:
        return None
    replies = (out or {}).get("replies") or {}
    return sum(int(replies.get(k) or 0) for k in ("drafted", "needs_human", "approved"))
