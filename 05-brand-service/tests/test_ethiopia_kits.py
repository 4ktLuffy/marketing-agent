from pathlib import Path

from app.facts_store import load_starter_kits

KIT_DIR = Path(__file__).resolve().parent.parent / "config" / "starter-kits"


def kits():
    return load_starter_kits()


def test_kits_load_and_validate():
    k = kits()
    assert "ethiopia-alcohol" in k and "ethiopia-hospitality" in k
    for name in ("ethiopia-alcohol", "ethiopia-hospitality"):
        assert k[name]["forbidden_phrasing"] and k[name]["required_disclosures"] and k[name]["fact_types"]


def test_alcohol_required_warning_english_and_no_invented_amharic():
    kit = kits()["ethiopia-alcohol"]
    texts = [d["text"] for d in kit["required_disclosures"]]
    assert "Selling to persons under 21 is prohibited." in texts
    raw = (KIT_DIR / "ethiopia-alcohol.yaml").read_text(encoding="utf-8")
    assert not any("ሀ" <= c <= "፿" for c in raw), "no Ethiopic script: Amharic wording must come from the owner"
    assert any("Amharic" in t and "confirm" in t for t in texts)
    assert any("human review" in t for t in texts)


def test_alcohol_forbidden_phrases_cover_article_60():
    by = {p["phrase"]: p for p in kits()["ethiopia-alcohol"]["forbidden_phrasing"]}
    for p in ("healthy", "good for you", "energy", "nutritious", "boosts", "lottery", "raffle", "prize draw",
              "official beer of", "proud sponsor of", "kids", "drink and drive", "billboard"):
        assert p in by, p
    assert "Art. 60(5)" in by["lottery"]["why"]
    assert "Art. 60(3)" in by["official beer of"]["why"]
    assert "Art. 60(4)" in by["tv advert"]["why"]
    # rules resting on unverified reading are flagged
    for p in ("healthy", "kids", "drink and drive", "raffle", "happy hour"):
        assert "needs legal check" in by[p]["why"], p
    assert "needs legal check" not in by["lottery"]["why"]


def test_hospitality_kit():
    kit = kits()["ethiopia-hospitality"]
    by = {p["phrase"].lower(): p for p in kit["forbidden_phrasing"]}
    assert {"star", "5-star", "award-winning"} <= set(by)
    disc = " | ".join(d["text"] for d in kit["required_disclosures"])
    assert "15% VAT" in disc and "10% service charge" in disc
    assert "foreign passport" in disc and "finance team" in disc and "24-hour" in disc
