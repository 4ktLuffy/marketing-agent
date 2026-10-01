"""Owner-confirmed starter-kit rules are applied by /check (so 88 blocks on them); draft rules and
procedural notes are not."""
from .test_facts_v2 import client, env, owner  # noqa: F401  (fixtures)


def confirm_all(owner, kit, keep=lambda r: True):
    client.post(f"/starter-kits/{kit}/apply", headers=owner)
    rules = client.get("/rules", params={"kit": kit}).json()["rules"]
    for r in rules:
        if keep(r):
            assert client.post(f"/rules/{r['id']}/confirm", headers=owner).status_code == 200
    return rules


def check(text, channel="telegram"):
    return client.post("/check", json={"text": text, "channel": channel}).json()


def errors(body, rule):
    return [v for v in body["violations"] if v["rule"] == rule and v["severity"] == "error"]


def test_draft_kit_rules_do_nothing(owner):
    client.post("/starter-kits/ethiopia-alcohol/apply", headers=owner)
    body = check("Heineken - official beer of the festival.")
    assert not errors(body, "kit_forbidden") and not errors(body, "kit_disclosure")


def test_confirmed_alcohol_rules_block(owner):
    confirm_all(owner, "ethiopia-alcohol")
    body = check("Heineken - official beer of the festival. 2,080 birr per crate.")
    assert errors(body, "kit_forbidden") and body["ok"] is False
    assert any("under 21" in v["detail"] for v in errors(body, "kit_disclosure"))


def test_under_21_warning_satisfies_the_rule(owner):
    confirm_all(owner, "ethiopia-alcohol")
    for text in ["Heineken 33cl: 2,080 birr per crate. Selling to persons under 21 is prohibited.",
                 "Heineken 33cl: 2,080 birr per crate. No sales to under-21s."]:
        assert not errors(check(text), "kit_disclosure"), text


def test_procedural_notes_are_never_demanded_in_copy(owner):
    confirm_all(owner, "ethiopia-alcohol")
    body = check("Heineken 33cl: 2,080 birr per crate. Selling to persons under 21 is prohibited.")
    assert not any("Hold for human review" in v["detail"] or "Amharic" in v["detail"] for v in body["violations"])


def test_rate_disclosure_only_when_a_price_is_mentioned(owner):
    confirm_all(owner, "hospitality")
    assert not errors(check("Wake up above the lake.", "facebook"), "kit_disclosure")
    assert errors(check("Lake rooms are $111 tonight.", "facebook"), "kit_disclosure")


def test_dismissed_rules_are_ignored(owner):
    rules = confirm_all(owner, "ethiopia-alcohol", keep=lambda r: False)
    for r in rules:
        client.post(f"/rules/{r['id']}/dismiss", headers=owner)
    assert check("Heineken - official beer of the festival.")["ok"] is True
