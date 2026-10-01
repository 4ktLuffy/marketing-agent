"""Stateless /check runs the same brand (05) and channel (14) checks as a submitted piece."""
from . import data
from .conftest import AUTH


def body(text="Hybrids are £28 per day at Porthleven.", channel="linkedin"):
    return {"text": text, "scope": {"sites": ["porthleven"]}, "publish_on": data.PUBLISH, "channel": channel}


def test_brand_error_blocks_check(client, stack):
    stack.facts = data.business("bikes")
    stack.brand_violations = [{"rule": "kit_forbidden", "severity": "error", "detail": "'official beer of' is not allowed"}]
    out = client.post("/check", json=body(), headers=AUTH).json()
    assert out["blocked"] is True and out["checks"]["brand"][0]["rule"] == "kit_forbidden"


def test_brand_warning_does_not_block(client, stack):
    stack.facts = data.business("bikes")
    stack.brand_violations = [{"rule": "ai_sheen", "severity": "warn", "detail": "'elevate your' reads like AI"}]
    out = client.post("/check", json=body(), headers=AUTH).json()
    assert out["checks"]["brand"] and not any(f["blocking"] for f in out["findings"])


def test_channel_limit_checked(client, stack):
    stack.facts = data.business("bikes")
    out = client.post("/check", json=body("x " * 200, "x"), headers=AUTH).json()
    assert out["blocked"] is True and out["checks"]["platform"][0]["rule"] == "too_long"
