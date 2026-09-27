"""Form-tool webhooks: check the per-source secret, map the form's fields to a lead.

Secrets: WEBHOOK_SECRETS="tally=...,typeform=...,n8n=...". A request is accepted when
  - header X-Webhook-Secret equals the source's secret, or
  - Tally-Signature is base64(HMAC-SHA256(secret, raw body))           (Tally), or
  - Typeform-Signature is "sha256=" + base64(HMAC-SHA256(secret, raw body)) (Typeform).
"""
import base64
import hashlib
import hmac
import os
import re


def secrets() -> dict[str, str]:
    out = {}
    for part in os.environ.get("WEBHOOK_SECRETS", "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip().lower()] = v.strip()
    return out


def verify(source: str, body: bytes, headers) -> bool:
    secret = secrets().get(source.lower())
    if not secret:
        return False
    given = headers.get("x-webhook-secret")
    if given and hmac.compare_digest(given.encode(), secret.encode()):
        return True
    digest = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    tally = headers.get("tally-signature")
    if tally and hmac.compare_digest(tally.encode(), digest.encode()):
        return True
    tf = headers.get("typeform-signature")
    if tf and hmac.compare_digest(tf.encode(), f"sha256={digest}".encode()):
        return True
    return False


# Field names/labels, lower-cased, matched as whole words: first match wins.
ROLES = {
    "consent": ("consent", "i agree", "agree", "permission", "contact me", "gdpr"),
    "email": ("email", "e-mail", "work email"),
    "company_domain": ("website", "company website", "domain", "company domain", "company url"),
    "message": ("message", "how can we help", "question", "comments", "details", "what do you need",
                "tell us", "enquiry", "inquiry"),
    "transcript_ref": ("transcript_ref", "transcript", "conversation id"),
    "name": ("name", "full name", "your name", "first name"),
}


def _role(label: str) -> str | None:
    lab = label.strip().lower()
    for role, names in ROLES.items():
        for n in names:
            if lab == n or re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", lab):
                if role == "name" and "company" in lab:
                    return None  # "Company name" is not the person's name
                return role
    return None


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, list):
        return any(_truthy(x) for x in v)
    return str(v).strip().lower() in ("true", "yes", "1", "on", "checked", "accepted", "y")


def _pairs(payload: dict) -> list[tuple[str, object]]:
    """(label, value) pairs from Tally, Typeform or a flat JSON object (n8n, generic)."""
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("fields"), list):  # Tally
        out = []
        for f in data["fields"]:
            value = f.get("value")
            if f.get("type") == "CHECKBOXES" and isinstance(value, list) and f.get("options"):
                chosen = {o.get("id"): o.get("text") for o in f["options"]}
                value = [chosen.get(v, v) for v in value]
            out.append((str(f.get("label") or f.get("key") or ""), value))
        return out
    fr = payload.get("form_response")
    if isinstance(fr, dict):  # Typeform
        titles = {q.get("id"): q.get("title", "") for q in (fr.get("definition") or {}).get("fields", [])}
        out = []
        for a in fr.get("answers") or []:
            field = a.get("field") or {}
            label = titles.get(field.get("id")) or field.get("ref") or ""
            t = a.get("type")
            value = a.get(t) if t else None
            if isinstance(value, dict):
                value = value.get("label") or value.get("labels")
            out.append((str(label), value))
        for k, v in (fr.get("hidden") or {}).items():
            out.append((str(k), v))
        return out
    return [(str(k), v) for k, v in payload.items()]


def map_fields(payload: dict) -> dict:
    lead: dict = {}
    consent_label = None
    utm = {}
    for label, value in _pairs(payload):
        low = label.strip().lower()
        if low.startswith("utm_") and value:
            utm[low[4:]] = str(value)[:200]
            continue
        if isinstance(value, dict) and low == "consent":  # generic: already {text, at}
            lead["consent"] = value
            continue
        role = _role(label)
        if role is None or role in lead:
            continue
        if role == "consent":
            if _truthy(value):
                consent_label = label if len(label) > 12 else f"{label}: {value}"
                lead["consent"] = {"text": consent_label[:1000], "at": None}
            continue
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        if value is None or str(value).strip() == "":
            continue
        lead[role] = str(value).strip()
    if utm:
        lead["utm"] = utm
    return lead
