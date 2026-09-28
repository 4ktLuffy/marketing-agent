"""Calls to other services: llm-gateway (03), claim-checker (44), content-calendar (19) and the
send path through listmonk-bridge (63). Every URL comes from env; an empty URL means "not installed".
"""
import os

import httpx


class ServiceError(RuntimeError):
    pass


def _url(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or "").strip().rstrip("/")


def _key_headers() -> dict:
    key = os.environ.get("INTERNAL_API_KEY")
    return {"X-API-Key": key} if key else {}


def _call(method: str, url: str, what: str, timeout: float, headers: dict | None = None, **kw) -> dict:
    try:
        r = httpx.request(method, url, headers={**_key_headers(), **(headers or {})}, timeout=timeout, **kw)
    except httpx.HTTPError as exc:
        raise ServiceError(f"{what} unreachable ({type(exc).__name__})") from exc
    if r.status_code >= 300:
        raise ServiceError(f"{what} returned HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as exc:
        raise ServiceError(f"{what} returned no JSON") from exc


# ---------- drafting (03 + 44)


def write_sequence(goal: str, audience: str | None, offer: str | None, emails: int) -> list[dict]:
    """The email_sequence prompt (the same one 57 uses): [{day, subject, preview, body, cta_text}]."""
    base = _url("GATEWAY_URL")
    if not base:
        raise ServiceError("GATEWAY_URL is not set: drafting needs the llm-gateway (03)")
    out = _call("POST", f"{base}/v1/run", "llm-gateway", float(os.environ.get("GATEWAY_TIMEOUT", "300")),
                headers={"X-Caller": "86 email flows"}, json={"prompt": "email_sequence",
                      "vars": {"goal": goal, "audience": audience, "offer": offer, "emails": emails}})
    emails_out = (out.get("output") or {}).get("emails") if isinstance(out.get("output"), dict) else None
    if not isinstance(emails_out, list) or not emails_out:
        raise ServiceError("llm-gateway returned no emails")
    return emails_out


def check_claims(text: str, context: str) -> list[str]:
    """Sentences the claim checker (44) could not support. Empty when CLAIMS_URL is not set."""
    base = _url("CLAIMS_URL")
    if not base:
        return []
    res = _call("POST", f"{base}/verify", "claim-checker", float(os.environ.get("CLAIMS_TIMEOUT", "600")),
                json={"text": text, "context": context[:20000]})
    flagged = [c.get("claim", "") for c in res.get("claims", []) if not c.get("supported")]
    return [c for c in flagged if c] + [u for u in res.get("unsupported", []) if u not in flagged]


# ---------- review (19)


def calendar_url() -> str:
    return _url("CALENDAR_URL")


def create_review_item(title: str, body: str, notes: str | None) -> int:
    item = _call("POST", f"{calendar_url()}/items", "content-calendar", 30,
                 json={"title": title[:120], "channel": "email_flow", "status": "in_review",
                       "body": body, "notes": notes})
    if not isinstance(item.get("id"), int):
        raise ServiceError("content-calendar returned no item id")
    return item["id"]


def get_item(item_id: int) -> dict:
    return _call("GET", f"{calendar_url()}/items/{item_id}", "content-calendar", 30)


# ---------- sending (63)


def bridge_send(to: str, subject: str, body_markdown: str, idempotency_key: str) -> dict:
    """STUB. The real send path: listmonk-bridge (63) POST /tx.

    63 has no transactional send today (it only creates draft campaigns), so this endpoint does
    not exist yet and every real send fails with 404 and is recorded as failed, never retried.
    It is only reached with DRY_RUN=false, an approved flow version and room under the daily cap.
    """
    base = _url("NEWSLETTER_URL")
    if not base:
        raise ServiceError("NEWSLETTER_URL is not set: there is no send path")
    return _call("POST", f"{base}/tx", "listmonk-bridge", 30,
                 json={"to": to, "subject": subject, "body_markdown": body_markdown,
                       "idempotency_key": idempotency_key})
