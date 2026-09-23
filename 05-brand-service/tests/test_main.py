import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import SUMMARY_MAX, app

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "brand.yaml"
client = TestClient(app)


@pytest.fixture(autouse=True)
def brand(tmp_path, monkeypatch):
    path = tmp_path / "brand.yaml"
    shutil.copy(EXAMPLE, path)
    monkeypatch.setenv("BRAND_FILE", str(path))
    return path


def rules(body):
    return {v["rule"] for v in body["violations"]}


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_profile_returns_yaml_as_json():
    body = client.get("/profile").json()
    assert body["name"] == "Northwind Roasters"
    assert "guaranteed" in body["banned_phrases"]


def test_profile_missing_file_is_503(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAND_FILE", str(tmp_path / "nope.yaml"))
    r = client.get("/profile")
    assert r.status_code == 503
    assert "BRAND_FILE" in r.json()["detail"]


def test_profile_reloads_when_file_changes(brand):
    assert client.get("/profile").json()["name"] == "Northwind Roasters"
    brand.write_text("name: Other Co\nbanned_phrases: [cheap]\n")
    st = os.stat(brand)
    os.utime(brand, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))  # force a new mtime
    assert client.get("/profile").json()["name"] == "Other Co"


def test_summary_is_compact_and_useful():
    s = client.get("/profile/summary").json()["summary"]
    assert "Northwind Roasters" in s
    assert "Never say:" in s
    assert len(s) <= SUMMARY_MAX


def test_summary_is_truncated_for_huge_profile(brand):
    brand.write_text("name: Big\nkey_messages:\n" + "".join(f"  - message {i} " + "x" * 80 + "\n" for i in range(50)))
    s = client.get("/profile/summary").json()["summary"]
    assert len(s) <= SUMMARY_MAX and s.endswith("...")


def test_check_clean_text_ok():
    r = client.post("/check", json={"text": "Fresh Desk Blend, roasted Monday and at your door by Thursday."})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "violations": []}


def test_check_banned_phrase_is_error_case_insensitive():
    body = client.post("/check", json={"text": "The BEST IN THE WORLD coffee, Guaranteed."}).json()
    assert body["ok"] is False
    errors = [v for v in body["violations"] if v["rule"] == "banned_phrase"]
    assert {v["severity"] for v in errors} == {"error"}
    assert len(errors) == 2


def test_banned_phrase_does_not_match_inside_words():
    body = client.post("/check", json={"text": "A secure, curated box."}).json()
    assert body["ok"] is True


def test_check_missing_disclaimer_for_channel():
    body = client.post("/check", json={"text": "Try the Desk Blend.", "channel": "paid_social"}).json()
    assert body["ok"] is False
    assert "missing_disclaimer" in rules(body)


def test_check_disclaimer_present_passes():
    body = client.post("/check", json={"text": "Try the Desk Blend. #ad", "channel": "Paid_Social"}).json()
    assert body["ok"] is True


def test_channel_without_requirement_passes():
    body = client.post("/check", json={"text": "Try the Desk Blend.", "channel": "linkedin"}).json()
    assert body["ok"] is True


def test_warnings_do_not_fail_ok():
    # emojis from the example brand's whitelist, so only warnings remain
    text = "WOW THIS COFFEE IS SO GOOD!!! \u2615\U0001F331\U0001F342 Ships to the US."
    body = client.post("/check", json={"text": text}).json()
    assert body["ok"] is True
    assert {"too_many_emojis", "all_caps", "exclamation_marks"} <= rules(body)
    assert all(v["severity"] == "warn" for v in body["violations"])


def test_acronyms_and_joined_emoji_are_not_overcounted():
    # 3 caps words incl. allowed acronyms; one ZWJ family emoji + one flag = 2 emojis.
    text = "FAQ for US teams, NEW roast \U0001F468\u200D\U0001F469\u200D\U0001F467 \U0001F1FA\U0001F1F8"
    body = client.post("/check", json={"text": text}).json()
    # (these two emojis are outside the example brand's whitelist; that rule is tested separately)
    assert [v for v in body["violations"] if v["rule"] != "emoji_not_allowed"] == []


def test_check_requires_text():
    assert client.post("/check", json={"channel": "x"}).status_code == 422


def test_wildcard_banned_phrase_catches_variants():
    from app.main import contains_token
    assert contains_token("The BEST coffee in the world", "best * in the world")
    assert contains_token("best darn roasted coffee in the world", "best * in the world")
    assert contains_token("best in the world", "best * in the world")
    # more than three words in between is a different sentence, not the claim
    assert not contains_token("best for mornings, and the rest is in the world", "best * in the world")
    assert not contains_token("secure", "cure")


def test_banned_phrase_detail_quotes_matched_text():
    body = client.post("/check", json={"text": "The BEST coffee in the world"}).json()
    details = [v["detail"] for v in body["violations"] if v["rule"] == "banned_phrase"]
    assert details == ["contains banned phrase 'BEST coffee in the world' (rule: 'best * in the world')"]
    assert body["violations"][0]["match"] == "BEST coffee in the world"


def test_facts_combine_explicit_products_and_key_messages():
    facts = [f["text"] for f in client.get("/facts").json()["facts"]]
    assert any("30 days" in f for f in facts)                       # explicit fact
    assert "Desk Blend costs $18 / 340 g." in facts                   # derived from products
    assert "Roasted within 48 hours of shipping." in facts           # key message
    assert any("roasted to order" in f for f in facts)                # the brand one-liner
    ids = [f["id"] for f in client.get("/facts").json()["facts"]]
    assert len(ids) == len(set(ids))


def test_emoji_whitelist_flags_odd_emoji_and_accepts_variation_selector():
    body = client.post("/check", json={"text": "Fresh roast ☕️ 🩸"}).json()
    rules = [(v["rule"], v.get("match"), v["severity"]) for v in body["violations"]]
    assert ("emoji_not_allowed", "🩸", "error") in rules
    assert not any(m == "☕" for _, m, _ in rules)       # ☕️ (with VS16) is allowed
    assert body["ok"] is False


def test_summary_lists_allowed_emojis():
    assert "only these: ☕" in client.get("/profile/summary").json()["summary"]
