"""General evidence rules (invented businesses, not the eval companies): claim identity,
ratings / awards / superlatives, non-numeric facts, alphanumeric values, a disclosure per
sentence. Every rule has negative controls that must stay quiet."""
from datetime import date

import pytest

from app import evidence

from .data import fact

DAY = date(2031, 5, 12)


def run(text, facts, scope=None):
    findings, used = evidence.check_text(text, facts, DAY, scope or {})
    return findings


def blocking(findings):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"]]


def labels(findings):
    return [(f["label"], f["fact_key"]) for f in findings]


# ---------- 1. claim identity: the same standard, number or named body

CERTS = [
    fact("iso-45001", "Northgate Lifts is ISO 45001:2018 certified.", "ISO 45001:2018 certified",
         subject={"kind": "business", "ref": "Northgate Lifts"}, fact_type="certification", claim_class="safety_cert"),
    fact("soc2-t1", "Tallyroll completed a SOC 2 Type I audit.", "SOC 2 Type I report",
         subject={"kind": "business", "ref": "Tallyroll"}, fact_type="certification", claim_class="security"),
    fact("shopify-partner", "We are a Shopify Plus Partner.", "Shopify Plus Partner",
         subject={"kind": "business", "ref": "Pinecrest Studio"}, fact_type="certification"),
    fact("ul-recognized", "The LX driver is a UL Recognized component.", "UL Recognized component",
         subject={"kind": "product", "ref": "LX driver"}, fact_type="certification", claim_class="safety_cert"),
    fact("trustmark", "We were TrustMark registered until 2030.", "TrustMark registered",
         subject={"kind": "business", "ref": "Northgate Lifts"}, fact_type="credential",
         valid_to="2030-12-31"),
    fact("gas-safe-leeds", "Our Leeds engineers are Gas Safe registered.", "Gas Safe registered",
         subject={"kind": "site", "ref": "Leeds branch"}, fact_type="credential", sites=["leeds"]),
]


@pytest.mark.parametrize("text", [
    "Northgate Lifts is ISO 9001 certified.",            # another standard of the same body
    "Tallyroll holds a SOC 2 Type II report.",           # Type II is not Type I
    "We are a Google Premier Partner.",                  # another named body
    "The LX driver is UL Listed.",                       # Listed is not Recognized
    "Northgate Lifts is an NHS approved supplier.",
])
def test_claim_naming_another_identifier_has_no_source(text):
    f = run(text, CERTS)
    assert ("no_source", None) in blocking(f), labels(f)
    assert not any(x["label"] == "match" and x["fact_key"] in ("iso-45001", "soc2-t1", "shopify-partner",
                                                                "ul-recognized") for x in f)


@pytest.mark.parametrize("text,key", [
    ("Northgate Lifts is ISO 45001 certified.", "iso-45001"),
    ("Our safety system is ISO 45001:2018 certified.", "iso-45001"),
    ("Tallyroll completed a SOC 2 Type I audit this year.", "soc2-t1"),
    ("Pinecrest Studio is a Shopify Plus Partner.", "shopify-partner"),
    ("The LX driver is UL Recognized.", "ul-recognized"),
])
def test_claim_naming_the_same_identifier_matches(text, key):
    f = run(text, CERTS)
    assert blocking(f) == [] and ("match", key) in labels(f), labels(f)


def test_identity_claim_expired_or_out_of_scope():
    assert ("conflict_or_expired", "trustmark") in blocking(run("We are TrustMark registered.", CERTS))
    f = run("All our engineers are Gas Safe registered.", CERTS, {"sites": ["york"]})
    assert ("wrong_scope", "gas-safe-leeds") in blocking(f)
    assert blocking(run("All our engineers are Gas Safe registered.", CERTS, {"sites": ["leeds"]})) == []


@pytest.mark.parametrize("text", [
    "Registered users get the report by email.",
    "Northgate Lifts Ltd is registered in England and Wales.",
    "Our delivery partner collects parcels at 5pm.",
    "We partnered with local schools this spring.",
])
def test_everyday_registered_and_partner_are_not_claims(text):
    assert blocking(run(text, CERTS)) == []


# ---------- 2. ratings, awards, superlatives, rankings

REVIEWS = [
    fact("google-rating", "Rated 4.8 out of 5 from 1,200 Google reviews (May 2031).", "rated 4.8 from 1,200 Google reviews",
         subject={"kind": "business", "ref": "Forno Rosso"}, fact_type="result", claim_class="result"),
    fact("pizza-award", "Winner, Best Pizza, Leeds Food Awards 2030.", "Best Pizza, Leeds Food Awards 2030",
         subject={"kind": "menu_item", "ref": "Margherita"}, fact_type="credential", claim_class="comparative"),
    fact("pizza-price", "A margherita costs £11.", "£11", subject={"kind": "menu_item", "ref": "Margherita"},
         fact_type="price", value=11, currency="GBP"),
]


@pytest.mark.parametrize("text", [
    "Voted the best café in Harrogate.",
    "Rated 4.9 stars by our guests.",
    "Our 5-star rated rooms are waiting.",
    "Join 5,000+ happy customers today.",
    "Trusted by more than 300 businesses.",
    "The UK's leading supplier of oak flooring.",
    "An award-winning bathroom showroom.",
    "The #1 choice for weekend breaks.",
    "The most popular café in Harrogate.",
    "Yorkshire's favourite tearoom.",
    "Top-rated plumbers near you.",
])
def test_unsupported_ratings_and_rankings_have_no_source(text):
    f = run(text, [])
    assert ("no_source", None) in blocking(f), labels(f)


@pytest.mark.parametrize("text,key", [
    ("Rated 4.8 by our diners.", "google-rating"),
    ("1,200+ Google reviews and counting.", "google-rating"),
    ("Try our award-winning margherita.", "pizza-award"),
    ("Voted the best pizza in Leeds.", "pizza-award"),
])
def test_rating_or_award_with_a_fact_saying_the_same_matches(text, key):
    f = run(text, REVIEWS)
    assert blocking(f) == [] and ("match", key) in labels(f), labels(f)


def test_rating_with_another_number_has_no_source():
    assert ("no_source", None) in blocking(run("Rated 4.9 by our diners.", REVIEWS))
    assert ("no_source", None) in blocking(run("Voted the best burger in Leeds.", REVIEWS))


@pytest.mark.parametrize("text", [
    "The best way to find us is by train.", "Best before 12 May.", "Best wishes, the team",
    "The garden is at its best in June.", "We do our best to reply within a day.", "All the best for the new year!",
    "Safety is our number one priority.", "Rain leading to delays on the ring road.", "Best,",
    "Our best-value room for two.", "Stars come out over the bay at night.", "The valve is rated 5 bar at 20 °C.",
    "Our chef's favourite dish is on the menu.",
])
def test_ordinary_best_and_friends_are_not_claims(text):
    assert blocking(run(text, REVIEWS)) == []


def test_own_range_superlative_is_review_not_blocking():
    f = run("Our most popular pizza is the margherita.", REVIEWS)
    assert blocking(f) == [] and ("review", None) in labels(f)
    assert ("no_source", None) in blocking(run("Our pizza is the most popular in Leeds.", []))


# ---------- 3. non-numeric facts: inclusions, features, offers, allergens

GYM = [
    fact("basic-price", "Basic membership costs £25 a month: gym floor and classes, no sauna.", "£25 a month",
         subject={"kind": "plan", "ref": "Basic membership"}, fact_type="price", value=25, currency="GBP",
         plan_tiers=["basic"]),
    fact("sauna-access", "Premium members get unlimited sauna access.", "unlimited sauna access",
         subject={"kind": "plan", "ref": "Premium membership"}, fact_type="inclusion", attribute="sauna",
         plan_tiers=["premium"]),
    fact("kids-swim", "Free swim lessons for kids during the Easter holidays.", "free swim lessons for kids",
         subject={"kind": "offer", "ref": "Easter swim offer"}, fact_type="inclusion", attribute="free_extra",
         valid_from="2031-04-01", valid_to="2031-04-20"),
    fact("couch-5k", "Couch to 5K is a 12-week coaching course.", "a 12-week coaching course",
         subject={"kind": "service", "ref": "Couch to 5K"}, fact_type="spec", value=12, unit="weeks",
         plan_tiers=["premium"]),
    fact("parking", "Free parking for all members.", "free parking", subject={"kind": "policy", "ref": "Parking"},
         fact_type="inclusion"),
    fact("loaf-v1", "The seeded loaf contained wheat and sesame (old recipe).", "contains wheat and sesame",
         subject={"kind": "menu_item", "ref": "Seeded loaf"}, fact_type="spec", attribute="allergens",
         status="expired", valid_to="2031-03-31"),
    fact("loaf-v2", "The seeded loaf contains wheat and soya.", "contains wheat and soya",
         subject={"kind": "menu_item", "ref": "Seeded loaf"}, fact_type="spec", attribute="allergens",
         valid_from="2031-04-01"),
]
BASIC = {"plan_tiers": ["basic"]}


def test_out_of_scope_inclusion_used_by_its_words():
    f = run("Basic members get unlimited sauna access.", GYM, BASIC)
    assert ("wrong_scope", "sauna-access") in blocking(f)


def test_expired_offer_used_by_its_wording_not_its_name():
    f = run("Bring the kids: free swim lessons all week!", GYM, BASIC)
    assert ("conflict_or_expired", "kids-swim") in blocking(f)


def test_in_scope_fact_that_says_no_x_conflicts_with_a_sentence_offering_x():
    f = run("Basic membership now comes with the sauna.", GYM, BASIC)
    assert ("conflict_or_expired", "basic-price") in blocking(f)
    # r5 tiers: "sauna" alone (an attribute term) only hints at the Premium fact: never a block
    assert ("wrong_scope", "sauna-access") not in blocking(f)
    assert blocking(run("Basic membership has no sauna, but the classes are included.", GYM, BASIC)) == []


def test_superseded_value_differs_from_the_valid_one():
    assert ("conflict_or_expired", "loaf-v1") in blocking(run("Our seeded loaf contains wheat and sesame.", GYM, BASIC))
    assert blocking(run("Our seeded loaf contains wheat and soya.", GYM, BASIC)) == []


def test_a_shared_number_or_generic_word_does_not_tie_a_sentence_to_a_fact():
    for text in ("Twelve weeks is a good time to build a habit.", "Free parking for all members.",
                 "Our classes run every day of the week.", "Kids love our holiday classes.",
                 "Members can bring a friend for free."):
        assert blocking(run(text, GYM, BASIC)) == [], (text, labels(run(text, GYM, BASIC)))
    # the number next to a distinctive word of the fact's subject does tie it
    assert ("wrong_scope", "couch-5k") in blocking(run("Our Couch to 5K course runs for 12 weeks.", GYM, BASIC))


# ---------- 4. alphanumeric values

SPECS = [
    fact("beacon-ip", "The Beacon lamp is IP67 rated.", "IP67", subject={"kind": "product", "ref": "Beacon lamp"},
         fact_type="spec", attribute="ingress_rating"),
    fact("bolt-sizes", "Anchor bolts come in M8 to M16.", "M8 to M16", subject={"kind": "product", "ref": "Anchor bolts"},
         fact_type="spec", attribute="sizes"),
    fact("sink-grade", "The Delta sink is 316L stainless steel.", "316L stainless steel",
         subject={"kind": "product", "ref": "Delta sink"}, fact_type="spec", attribute="material"),
    fact("heater-power", "The Ember heater has 2 x 1.5 kW elements.", "2 x 1.5 kW",
         subject={"kind": "product", "ref": "Ember heater"}, fact_type="spec", attribute="power"),
]


@pytest.mark.parametrize("text,key", [
    ("The Beacon lamp is IP65 rated.", "beacon-ip"), ("Anchor bolts now in M20.", "bolt-sizes"),
    ("The Delta sink is 304L stainless steel.", "sink-grade"), ("The Ember heater has 2 x 2 kW elements.", "heater-power"),
])
def test_same_family_code_with_another_value_conflicts(text, key):
    assert ("conflict_or_expired", key) in blocking(run(text, SPECS))


@pytest.mark.parametrize("text", [
    "The Beacon lamp is IP67 rated.", "Anchor bolts in M10 and M12.", "A 316L stainless steel sink.",
    "The Ember heater has 2 x 1.5 kW elements.", "A B2B range, launching in Q3.", "Model KV-50B ships from our depot.",
])
def test_same_code_in_range_or_unrelated_codes_do_not_block(text):
    assert blocking(run(text, SPECS)) == [], labels(run(text, SPECS))


def test_codes_are_extracted_as_values():
    keys = [v.key for v in evidence.extract("IP67, PN16, M12, DN50 and 316L; 2 x 1.5 kW")]
    assert keys == [("code", "IP#", "67"), ("code", "PN#", "16"), ("code", "M#", "12"), ("code", "DN#", "50"),
                    ("code", "#L", "316"), ("qty", "2x1.5", "kw")]


# ---------- 5. a missing disclosure is reported on every sentence that uses the fact

TOURS = [fact("boat-trip", "The seal boat trip costs £18 per adult; under-5s go free.", "£18 per adult",
              subject={"kind": "service", "ref": "Seal boat trip"}, fact_type="price", value=18, currency="GBP",
              required_disclosures=["under-5s go free"])]


def test_missing_disclosure_on_every_sentence_one_blocking_decision():
    text = "Subject: Seal trips from £18\nSee the seals for £18 per adult.\nBook the £18 trip today."
    f = run(text, TOURS)
    miss = [x["sentence"] for x in f if x["label"] == "missing_disclosure"]
    assert miss == ["Subject: Seal trips from £18", "See the seals for £18 per adult.", "Book the £18 trip today."]
    assert evidence.blocked(f) is True
    assert blocking(run(text + "\nUnder-5s go free.", TOURS)) == []
