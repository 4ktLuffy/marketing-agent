"""Placement checks: phrase woven vs pasted on, quoted, invented customers. No network.

Cases are written in a bakery / bike-shop / garden domain on purpose: none of these phrases is
in the Northwind sample data or the 23 voc_ab topics, so the checks are not tuned to the eval.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.placement import bolted_on, check_post, first_sentence, invented_customers, opens_with, quoted

KEY = "test-key"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "voc.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)


@pytest.mark.parametrize("text,phrase,reason", [
    ("chain keeps slipping We fixed that with a new cassette.", "chain keeps slipping", "no_join"),
    ("chain keeps slipping We fixed that.", "chain keeps slipping", "lowercase_start"),
    ("Chain keeps slipping | Here is the fix.", "chain keeps slipping", "separator_after"),
    ("Chain keeps slipping: here is the fix.", "chain keeps slipping", "separator_after"),
    ("Chain keeps slipping — here is the fix.", "chain keeps slipping", "separator_after"),
    ("Chain keeps slipping\nHere is the fix.", "chain keeps slipping", "separator_after"),
    ("🚲 Chain keeps slipping 🔧 Here is the fix.", "chain keeps slipping", "no_join"),
    ("Leaves turning yellow. Water less in winter.", "leaves turning yellow", "fragment"),
    ("Leaves turning yellow? Water less in winter.", "leaves turning yellow", "fragment"),
    ("Winter tips — leaves turning yellow and what to do.", "leaves turning yellow", "separator_before"),
    ("Here is what to do about Leaves Turning Yellow in winter.", "leaves turning yellow", "capitalized_mid_sentence"),
    ("Leaves turning yellow, our new plant food is here for spring.", "leaves turning yellow", "dangling_opener"),
    ("Chain keeps slipping, the shop is open until six.", "chain keeps slipping", "dangling_opener"),
])
def test_bolted_on_catches_labels(text, phrase, reason):
    assert opens_with(text, phrase)
    assert reason in bolted_on(text, phrase)


@pytest.mark.parametrize("text,phrase", [
    ("If your chain keeps slipping after a wet ride, bring it in and we'll look.", "chain keeps slipping"),
    ("Chain keeps slipping on the big climbs? A worn cassette is the usual cause.", "chain keeps slipping"),
    ("Nobody wants leaves turning yellow in January, so water less in winter.", "leaves turning yellow"),
    ("Leaves turning yellow, drooping stems, dry soil: all signs of the same thing.", "leaves turning yellow"),
    ("Leaves turning yellow, and nothing you try helps? Water less.", "leaves turning yellow"),
    ("The bread doesn't rise, it just sits there.", "the bread doesn't rise"),  # a clause: a comma splice, not a label
    ("🌱 Leaves turning yellow is usually a watering problem.", "leaves turning yellow"),
    ("The bread doesn't rise the second day. Here is why.", "the bread doesn't rise"),  # a clause of its own
    ("You'll never run out.", "never run out"),
])
def test_bolted_on_passes_woven_sentences(text, phrase):
    assert opens_with(text, phrase)
    assert bolted_on(text, phrase) == []


def test_bolted_on_is_empty_when_the_phrase_is_absent():
    assert bolted_on("Nothing to see here.", "chain keeps slipping") == []


def test_first_sentence_and_opens_with():
    assert first_sentence("  Never run out again! Pause any time.") == "Never run out again!"
    assert first_sentence("Away next week\nPause the box.") == "Away next week"
    assert opens_with("You’ll NEVER   run out. Promise.", "never run out")
    assert not opens_with("Stock up today. You'll never run out.", "never run out")


def test_quoted():
    assert quoted('If "chain keeps slipping" sounds familiar, come in.', "chain keeps slipping")
    assert quoted("“Chain keeps slipping?” We hear you.", "chain keeps slipping")
    assert not quoted("If your chain keeps slipping, come in.", "chain keeps slipping")


@pytest.mark.parametrize("text", [
    "Our customer Sarah switched to the tubeless kit last spring.",
    "Tom K., a longtime subscriber, never waits for parts.",
    "Maria in Denver says the kit changed her commute.",
    "The team at Acme Tech rides to work every day.",
    "Our small crew from Brightpath Labs loves the Friday ride.",
    "A customer told us the brakes finally stopped squealing.",
    "One of our subscribers wrote in last week about it.",
])
def test_invented_customers_flagged(text):
    assert invented_customers(text, "Northwind Bikes tubeless kit")


@pytest.mark.parametrize("text,allowed", [
    ("Our customers tell us the brakes are quieter now.", ""),          # plural, no identity
    ("Subscribers can pause any time from their account.", ""),
    ("The team at Northwind Bikes tunes every wheel by hand.", "Northwind Bikes"),  # the brand itself
    ("Riders like Maria from Denver say it best.", "Maria from Denver wrote a review"),  # in a real source
    ("We ship on Monday. Your box arrives by Friday.", ""),
    ("Introducing our newest team member to the family.", ""),  # no comma: not "Name, our customer"
])
def test_invented_customers_not_flagged(text, allowed):
    assert invented_customers(text, allowed) == []


def test_check_post_feedback_covers_every_problem():
    bad = check_post('"chain keeps slipping" | Our customer Sarah fixed it.', "chain keeps slipping")
    assert not bad["ok"] and bad["bolted_on"] and bad["quoted"] and bad["invented_customers"]
    fb = bad["feedback"]
    assert "label" in fb and "quotation marks" in fb and "Sarah" in fb
    miss = check_post("Bring your bike in. If the chain keeps slipping we fix it.", "chain keeps slipping")
    assert miss["opens_with"] is False and not miss["ok"] and "first sentence contains" in miss["feedback"]
    good = check_post("If your chain keeps slipping, bring it in.", "chain keeps slipping")
    assert good["ok"] and good["feedback"] == "" and good["bolted_reasons"] == []
    no_phrase = check_post("A plain post about bikes.", None)
    assert no_phrase["ok"] and no_phrase["opens_with"] is None and no_phrase["bolted_on"] is None


def test_placement_endpoint_allows_names_from_stored_sources():
    client = TestClient(app)
    r = client.post("/sources/import", headers={"X-API-Key": KEY},
                    json=[{"kind": "review", "source_ref": "r1", "text": "The team at Brightpath loves it."}])
    assert r.status_code == 200
    body = {"text": "If your chain keeps slipping, the team at Brightpath knows the fix.", "phrase": "chain keeps slipping"}
    out = client.post("/placement/check", json=body).json()
    assert out["ok"] and out["invented_customers"] == []
    out = client.post("/placement/check", json=body | {"text": body["text"].replace("Brightpath", "Acme")}).json()
    assert not out["ok"] and out["invented_customers"][0]["kind"] == "named_organization"
    assert client.post("/placement/check", json={"text": ""}).status_code == 422


def test_capital_fix_repairs_a_lowercase_start_only():
    from app.placement import capitalize_start
    assert capitalize_start("chain keeps slipping? not anymore.") == "Chain keeps slipping? not anymore."
    assert capitalize_start("🚲 chain keeps slipping") == "🚲 Chain keeps slipping"
    assert capitalize_start("Fine as is.") == "Fine as is." and capitalize_start("") == ""
    fixed = check_post("chain keeps slipping on wet days is usually the cassette.", "chain keeps slipping")
    assert fixed["ok"] and fixed["repaired"] and fixed["text"].startswith("Chain keeps")
    assert fixed["raw_bolted_reasons"] == ["lowercase_start"] and fixed["bolted_reasons"] == []
    still = check_post("chain keeps slipping We fixed it.", "chain keeps slipping")  # capital fix is not enough
    assert not still["ok"] and still["bolted_reasons"] == ["no_join"]
