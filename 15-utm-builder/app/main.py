"""UTM builder: consistent, validated campaign links."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="utm-builder")

# One naming scheme for every link, so analytics groups them correctly.
ALLOWED_MEDIUMS = {
    "social", "paid_social", "email", "cpc", "display", "affiliate",
    "referral", "organic", "video", "sms", "qr",
}
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")


def normalize(value: str) -> str:
    """Lowercase, spaces and underscores-runs to single separators, strip junk."""
    value = value.strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9_\-.]", "", value)
    return re.sub(r"-{2,}", "-", value).strip("-")


class BuildRequest(BaseModel):
    url: str
    source: str
    medium: str
    campaign: str
    term: str | None = None
    content: str | None = None


class ParseRequest(BaseModel):
    url: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/conventions")
def conventions():
    return {
        "allowed_mediums": sorted(ALLOWED_MEDIUMS),
        "rules": [
            "all values lowercase",
            "spaces become '-'",
            "only a-z 0-9 _ - . kept",
            "existing non-utm query params are preserved",
        ],
    }


@app.post("/build")
def build(req: BuildRequest):
    parts = urlsplit(req.url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HTTPException(422, "url must be absolute http(s)")

    medium = normalize(req.medium)
    if medium not in ALLOWED_MEDIUMS:
        raise HTTPException(
            422, f"medium '{medium}' not allowed; use one of {sorted(ALLOWED_MEDIUMS)}"
        )

    params = {
        "utm_source": normalize(req.source),
        "utm_medium": medium,
        "utm_campaign": normalize(req.campaign),
    }
    if req.term:
        params["utm_term"] = normalize(req.term)
    if req.content:
        params["utm_content"] = normalize(req.content)
    empty = [k for k, v in params.items() if not v]
    if empty:
        raise HTTPException(422, f"empty after normalizing: {empty}")

    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in UTM_KEYS]
    query = urlencode(kept + list(params.items()))
    url = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", query, parts.fragment))
    return {"url": url, "params": params}


@app.post("/parse")
def parse(req: ParseRequest):
    parts = urlsplit(req.url.strip())
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    utm = {k: v for k, v in pairs if k in UTM_KEYS}
    other = [(k, v) for k, v in pairs if k not in UTM_KEYS]
    base = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(other), parts.fragment))
    return {"base_url": base, "params": utm}
