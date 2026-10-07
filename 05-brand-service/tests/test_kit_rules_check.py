"""Owner-confirmed starter-kit rules are applied by /check (so 88 blocks on them); draft rules and
procedural notes are not."""
import shutil
from pathlib import Path

import pytest

from .test_facts_v2 import client, env, owner  # noqa: F401  (fixtures)

KITS_SRC = Path(__file__).resolve().parent.parent / "config" / "starter-kits"

SPIRITS_DEMO = """\
label: Spirits demo (made-up kit for these tests)
description: Alcohol advertising hygiene. All public copy held for human review; under-21 warning on every ad.
fact_types:
  - fact_type: spec
    subject_kind: product
    attributes: [abv_percent, volume]
    example: "The lager is 4.8% ABV, 330 ml bottle."
claim_classes: [regulated_health, price_reference]
forbidden_phrasing:
  - phrase: healthy
    why: Health claims about alcohol must not be made.
    claim_class: regulated_health
  - phrase: good for you
    why: Health claims about an alcoholic drink are misleading.
    claim_class: regulated_health
  - phrase: official beer of
    why: Sponsorship of public events is restricted.
  - phrase: happy hour
    why: Public drink offers are promotional alcohol advertising; hold for review.
    claim_class: price_reference
required_disclosures:
  - when: any alcohol advertisement or public post
    text: Selling to persons under 21 is prohibited.
  - when: non-Latin alcohol copy (owner must confirm the wording; it is not supplied by this kit)
    text: Owner to confirm the non-Latin wording of the under-21 warning.
  - when: any alcohol copy is drafted (recommendation, not a statutory text)
    text: Draft only. Hold for human review; do not auto-publish.
"""


@pytest.fixture(autouse=True)
def kits_dir(tmp_path, monkeypatch):
    d = tmp_path / "kits"
    d.mkdir()
    (d / "spirits-demo.yaml").write_text(SPIRITS_DEMO, encoding="utf-8")
    shutil.copy(KITS_SRC / "hospitality.yaml", d / "hospitality.yaml")
    monkeypatch.setenv("STARTER_KITS_DIR", str(d))
    return d


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
    client.post("/starter-kits/spirits-demo/apply", headers=owner)
    body = check("Brand A lager - official beer of the festival.")
    assert not errors(body, "kit_forbidden") and not errors(body, "kit_disclosure")


def test_confirmed_alcohol_rules_block(owner):
    confirm_all(owner, "spirits-demo")
    body = check("Brand A lager - official beer of the festival. £2,080 per crate.")
    assert errors(body, "kit_forbidden") and body["ok"] is False
    assert any("under 21" in v["detail"] for v in errors(body, "kit_disclosure"))


def test_under_21_warning_satisfies_the_rule(owner):
    confirm_all(owner, "spirits-demo")
    for text in ["Brand A lager 33cl: £2,080 per crate. Selling to persons under 21 is prohibited.",
                 "Brand A lager 33cl: £2,080 per crate. No sales to under-21s."]:
        assert not errors(check(text), "kit_disclosure"), text


def test_procedural_notes_are_never_demanded_in_copy(owner):
    confirm_all(owner, "spirits-demo")
    body = check("Brand A lager 33cl: £2,080 per crate. Selling to persons under 21 is prohibited.")
    assert not any("Hold for human review" in v["detail"] or "non-Latin" in v["detail"] for v in body["violations"])


def test_rate_disclosure_only_when_a_price_is_mentioned(owner):
    confirm_all(owner, "hospitality")
    assert not errors(check("Wake up above the lake.", "facebook"), "kit_disclosure")
    assert errors(check("Lake rooms are $111 tonight.", "facebook"), "kit_disclosure")


def test_dismissed_rules_are_ignored(owner):
    rules = confirm_all(owner, "spirits-demo", keep=lambda r: False)
    for r in rules:
        client.post(f"/rules/{r['id']}/dismiss", headers=owner)
    assert check("Brand A lager - official beer of the festival.")["ok"] is True
