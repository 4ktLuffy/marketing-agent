"""The one outside call: the llm-gateway (03) with the feed_title prompt. Its URL comes from env;
empty means "not installed", and every title is then the rule-based one."""
import json
import os

import httpx

from .checks import EVIDENCE_FIELDS


class ServiceError(RuntimeError):
    pass


def gateway_url() -> str:
    return (os.environ.get("GATEWAY_URL") or "").strip().rstrip("/")


def _key_headers() -> dict:
    key = os.environ.get("INTERNAL_API_KEY")
    return {"X-API-Key": key} if key else {}


def model_input(row: dict) -> dict:
    """What the model sees: the product's own descriptive fields. Never the price, id, gtin,
    links or unknown columns (custom labels often hold words like "sale")."""
    out = {f: str(row[f]).strip() for f in EVIDENCE_FIELDS if str(row.get(f) or "").strip()}
    if len(out.get("description", "")) > 2000:
        out["description"] = out["description"][:2000]
    return out


def propose(row: dict, with_description: bool) -> dict:
    """{"title", "description"} from the feed_title prompt. Raises ServiceError."""
    base = gateway_url()
    if not base:
        raise ServiceError("GATEWAY_URL is not set")
    body = {"prompt": "feed_title",
            "vars": {"product": json.dumps(model_input(row), ensure_ascii=False),
                     "with_description": "yes" if with_description else None}}
    try:
        r = httpx.post(f"{base}/v1/run", json=body, headers={**_key_headers(), "X-Caller": "87 feed optimizer"},
                       timeout=float(os.environ.get("GATEWAY_TIMEOUT", "300")))
    except httpx.HTTPError as exc:
        raise ServiceError(f"llm-gateway unreachable ({type(exc).__name__})") from exc
    if r.status_code >= 300:
        raise ServiceError(f"llm-gateway returned HTTP {r.status_code}")
    try:
        out = r.json().get("output")
    except ValueError as exc:
        raise ServiceError("llm-gateway returned no JSON") from exc
    if not isinstance(out, dict) or not isinstance(out.get("title"), str):
        raise ServiceError("llm-gateway returned no title")
    return {"title": out["title"], "description": out.get("description") if isinstance(out.get("description"), str) else ""}
