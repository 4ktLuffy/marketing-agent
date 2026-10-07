"""AI-sheen lint: warnings (never errors) for phrases that read like generic AI text."""
import pytest

from app import ai_sheen

from .test_main import client


@pytest.mark.parametrize("text,label", [
    ("Delve into the flavours of our new blend.", "delve"),
    ("Elevate your mornings with fresh coffee.", "elevate your"),
    ("Unlock the power of a better brew.", "unlock the …"),
    ("In today's fast-paced world, coffee matters.", "in today's fast-paced world"),
    ("Our lodge is nestled in the hills above the lake.", "nestled in"),
    ("It's not just a coffee, it's a ritual.", "it's not just …"),
    ("Look no further for your weekend escape.", "look no further"),
    ("We're thrilled to announce our new menu.", "we're thrilled to announce"),
])
def test_ai_phrases_are_flagged(text, label):
    got = ai_sheen.lint(text)
    assert got and got[0]["severity"] == "warn" and got[0]["rule"] == "ai_sheen"


@pytest.mark.parametrize("text", [
    "Fresh Desk Blend, roasted Monday and at your door by Thursday.",
    "Our rooms look over Lake Marlow and Lake Ashby.",
    "Book now: call +44 20 7946 0958.",
    "The delivery van leaves at 8 — call before then.",          # one em dash is fine
    "We unlocked the gate at 6am for the early boat ride.",     # 'unlock' without the cliché
])
def test_plain_copy_is_not_flagged(text):
    assert ai_sheen.lint(text) == []


def test_em_dash_overuse():
    text = "Fresh — roasted — delivered — every week."
    assert any("em dash" in v["detail"] for v in ai_sheen.lint(text))


def test_at_most_three_findings():
    text = "Delve in. Elevate your day. Unleash flavour. A seamless, game-changing, cutting-edge blend."
    assert len(ai_sheen.lint(text)) == 3


def test_check_endpoint_warns_but_stays_ok(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    monkeypatch.setenv("BRAND_FILE", str(__import__("pathlib").Path(__file__).parents[1] / "config" / "brand.yaml"))
    body = client.post("/check", json={"text": "Elevate your mornings with Desk Blend."}).json()
    assert body["ok"] is True
    assert any(v["rule"] == "ai_sheen" and v["severity"] == "warn" for v in body["violations"])


def test_extra_phrases_from_the_brand():
    assert ai_sheen.lint("A truly bespoke box.", ["bespoke"])[0]["match"].lower() == "bespoke"
