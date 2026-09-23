from fastapi.testclient import TestClient

from app.main import RULES, app

client = TestClient(app)


def validate(channel, text):
    r = client.post("/validate", json={"channel": channel, "text": text})
    assert r.status_code == 200, r.text
    return r.json()


def rule_ids(body):
    return {v["rule"] for v in body["violations"]}


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_rules_lists_every_channel():
    body = client.get("/rules").json()
    assert set(body["channels"]) == {
        "x", "linkedin", "instagram", "facebook", "threads", "mastodon", "email_subject",
        "google_ads_headline", "google_ads_description", "meta_description",
    }
    assert body["channels"]["x"]["limit"] == 280


def test_short_post_is_ok():
    body = validate("linkedin", "Fresh beans, roasted Monday. #specialtycoffee #wfh")
    assert body == {"ok": True, "channel": "linkedin", "length": 50, "limit": 3000,
                    "hashtags": 2, "violations": []}


def test_x_too_long_fails():
    body = validate("x", "a" * 281)
    assert body["ok"] is False
    assert "too_long" in rule_ids(body)


def test_x_counts_urls_as_23():
    url = "https://northwind-roasters.example.com/blog/a-very-long-path-about-coffee?utm_source=x"
    body = validate("X", "Read this " + url)
    assert body["channel"] == "x"
    assert body["length"] == len("Read this ") + 23
    # 257 chars + one long URL = exactly 280 on X; Threads counts the URL in full.
    text = "b" * 256 + " " + url
    assert validate("x", text)["ok"] is True
    assert validate("threads", text)["length"] == len(text)


def test_emoji_counts_as_one_code_point():
    assert validate("threads", "\u2615\u2615")["length"] == 2


def test_instagram_hashtag_cap():
    ok = validate("instagram", " ".join(f"#tag{i}" for i in range(30)))
    assert ok["ok"] is True and ok["hashtags"] == 30
    bad = validate("instagram", " ".join(f"#tag{i}" for i in range(31)))
    assert bad["ok"] is False
    assert "too_many_hashtags" in rule_ids(bad)


def test_hashtag_count_ignores_anchors_and_entities():
    assert validate("linkedin", "See page#section and &#38; but #real")["hashtags"] == 1


def test_email_subject_warns_above_60_and_fails_above_78():
    warn = validate("email_subject", "s" * 61)
    assert warn["ok"] is True
    assert warn["violations"][0]["rule"] == "above_recommended"
    assert warn["violations"][0]["severity"] == "warn"
    assert validate("email_subject", "s" * 79)["ok"] is False


def test_meta_description_warns_when_short():
    body = validate("meta_description", "Coffee subscription.")
    assert body["ok"] is True
    assert rule_ids(body) == {"below_recommended"}


def test_google_ads_headline_limit():
    assert validate("google_ads_headline", "h" * 30)["ok"] is True
    assert validate("google_ads_headline", "h" * 31)["ok"] is False


def test_empty_text_fails():
    body = validate("facebook", "   ")
    assert body["ok"] is False
    assert "empty" in rule_ids(body)


def test_unknown_channel_is_422_and_lists_valid():
    r = client.post("/validate", json={"channel": "myspace", "text": "hi"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert all(c in detail for c in RULES)
