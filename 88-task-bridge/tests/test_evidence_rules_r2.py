"""Second round of general evidence rules (invented businesses, not the eval companies):
graded identifiers (scheme + grade), benefits described without their offer's name, quality
superlatives / scores / staff credential claims. Every rule has negative controls."""
from datetime import date

import pytest

from app import evidence, facts as F

from .data import fact

DAY = date(2031, 5, 12)


def run(text, facts, scope=None):
    findings, used = evidence.check_text(text, facts, DAY, scope or {})
    return findings


def blocking(findings):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"]]


def labels(findings):
    return [(f["label"], f["fact_key"]) for f in findings]


# ---------- 1. graded identifiers: the same scheme with another grade conflicts

GRADED = [
    fact("lifeguard-award", "Every Seabright lifeguard holds a Level 3 Award in Pool Lifeguarding.",
         "Level 3 Award in Pool Lifeguarding", subject={"kind": "business", "ref": "Seabright Leisure lifeguards"},
         fact_type="credential", attribute="lifeguard_qualification", value="Level 3"),
    fact("kelvin-hqs", "The Kelvin heat pump has a provisional HQS band of B. No model is rated HQS A.",
         "HQS B (provisional)", subject={"kind": "product", "ref": "Kelvin heat pump"}, fact_type="certification",
         attribute="hqs_band", value="B", required_disclosures=["provisional"]),
    fact("drill-class", "The Torro drill is Class II double insulated.", "Class II double insulated",
         subject={"kind": "product", "ref": "Torro drill"}, fact_type="spec", attribute="insulation_class",
         value="Class II"),
    fact("fireline-rating", "Fireline board is classified Euroclass B-s2,d0 (not A1).", "Euroclass B-s2,d0",
         subject={"kind": "product", "ref": "Fireline board"}, fact_type="certification", value="B-s2,d0"),
    fact("reseller-tier", "Pemberton IT was a Tier 1 reseller until 2030.", "Tier 1 reseller",
         subject={"kind": "business", "ref": "Pemberton IT"}, fact_type="credential", value="Tier 1",
         valid_to="2030-12-31"),
]


@pytest.mark.parametrize("text,key", [
    ("Our lifeguards hold a Level 2 Pool Lifeguarding award.", "lifeguard-award"),
    ("The Kelvin heat pump is rated HQS A.", "kelvin-hqs"),
    ("The Torro drill is Class I insulated.", "drill-class"),
    ("Fireline board is Euroclass A1.", "fireline-rating"),              # the fact says "not A1"
    ("Fireline board is Euroclass B-s1,d0.", "fireline-rating"),         # same class, another suffix
    ("We are a Tier 1 reseller.", "reseller-tier"),                      # same grade, fact expired
])
def test_scheme_with_another_grade_or_expired_conflicts(text, key):
    f = run(text, GRADED)
    assert ("conflict_or_expired", key) in blocking(f), labels(f)
    assert ("match", key) not in labels(f)


@pytest.mark.parametrize("text,key", [
    ("Our lifeguards hold a Level 3 Pool Lifeguarding award.", "lifeguard-award"),
    ("The Torro drill is Class 2 insulated.", "drill-class"),            # roman II = 2
    ("Fireline board is Euroclass B.", "fireline-rating"),               # suffix left out
    ("Fireline board is Euroclass B-s2,d0.", "fireline-rating"),
])
def test_scheme_with_the_same_grade_matches(text, key):
    f = run(text, GRADED)
    assert blocking(f) == [] and ("match", key) in labels(f), labels(f)


def test_a_grade_counts_as_using_the_fact_so_its_disclosure_applies():
    f = run("The Kelvin is an HQS B heat pump.", GRADED)
    assert ("missing_disclosure", "kelvin-hqs") in blocking(f), labels(f)
    assert blocking(run("The Kelvin is an HQS B heat pump (provisional).", GRADED)) == []


def test_a_grade_conflict_overrides_a_keyword_match_on_the_same_fact():
    f = run("The Kelvin heat pump is HQS A certified.", GRADED)
    assert ("conflict_or_expired", "kelvin-hqs") in blocking(f)
    assert ("match", "kelvin-hqs") not in labels(f)


@pytest.mark.parametrize("text", [
    "Take your swimming to the next level.",
    "Parking is on Level 2 of the multi-storey.",          # generic scheme word, nothing ties it to a fact
    "Enjoy a first-class welcome at reception.",
    "Our HQS team replies within a day.",                  # scheme with no grade
])
def test_scheme_words_without_a_tied_grade_are_quiet(text):
    assert blocking(run(text, GRADED)) == []


def test_fact_grades_come_from_value_not_from_what_the_fact_says_it_is_not():
    g = {(x.scheme, x.grade, x.suffix) for x in evidence.fact_grades(GRADED[1])}
    assert g == {("HQS", "B", "")}
    assert {(x.scheme, x.grade, x.suffix) for x in evidence.fact_grades(GRADED[3])} == {("euroclass", "B", "-s2,d0")}
    # a price or result fact carries no scheme even when its wording has "grade 4"
    assert evidence.fact_grades(fact("res", "90% reached grade 4.", "90% reached grade 4", fact_type="result",
                                     value=90, unit="%")) == []


def test_negated_value_in_a_fact_sentence_is_not_a_match():
    lamp = [fact("lamp-ip", "The Orla lamp is IP54 rated (not IP67).", "IP54",
                 subject={"kind": "product", "ref": "Orla lamp"}, fact_type="spec", attribute="ingress_protection")]
    f = run("The Orla lamp is IP67 rated.", lamp)
    assert ("match", "lamp-ip") not in labels(f)
    assert ("conflict_or_expired", "lamp-ip") in blocking(f), labels(f)


# ---------- 2. an offer described without its name

STUDIO = [
    fact("taster-offer", "New members got a free 45-minute taster class until 30 April 2031.", "a free 45-minute taster class",
         subject={"kind": "offer", "ref": "Spring taster offer"}, fact_type="price", attribute="free_class",
         valid_from="2031-03-01", valid_to="2031-04-30"),
    fact("members-parking", "Members park free in the Kiln Street car park.", "free on-site parking",
         subject={"kind": "service", "ref": "Parking"}, fact_type="inclusion"),
    fact("drop-in", "A drop-in class costs £12.", "£12 per class", subject={"kind": "service", "ref": "Drop-in class"},
         fact_type="price", value=12, currency="GBP"),
    fact("towel-hire", "Towel hire costs £2.", "£2 towel hire", subject={"kind": "service", "ref": "Towel hire"},
         fact_type="price", value=2, currency="GBP"),
]


@pytest.mark.parametrize("text", [
    "Your first class is on us.",
    "Try a taster class free this month.",
    "Your first class is free when you join.",
    "Complimentary taster class for new members.",
])
def test_benefit_whose_object_is_only_an_expired_offer_conflicts(text):
    f = run(text, STUDIO)
    assert ("conflict_or_expired", "taster-offer") in blocking(f), labels(f)


def test_free_thing_no_fact_mentions_has_no_source():
    f = run("Every member gets a free smoothie after class.", STUDIO)
    assert ("no_source", None) in blocking(f), labels(f)


@pytest.mark.parametrize("text", [
    "Free parking for all members.",                       # a valid fact gives parking as a benefit
    "Parking is free for members.",
    "Feel free to ask our coaches anything.",
    "You are free to pause your membership.",
    "Count on us for friendly coaching.",
    "Our cafe menu is gluten-free.",
    "We still have free spaces in the 7am class.",
    "There is no free towel service, towel hire is £2.",   # negated
    "Towels are £2 to hire.",
])
def test_ordinary_free_and_on_us_are_quiet(text):
    assert blocking(run(text, STUDIO)) == [], labels(run(text, STUDIO))


def test_offer_heads():
    assert evidence.offer_heads(STUDIO[0]) == {"class"}
    assert evidence.offer_heads(fact("x", "", "Sunday delivery at no extra charge")) == {"delivery"}
    assert evidence.offer_heads(fact("y", "", "15% off the Harbour Loaf")) == {"loaf"}
    assert evidence.offer_heads(STUDIO[2]) == set()


# ---------- 3. quality superlatives, scores, staff credential claims

RESULTS = [
    fact("survey-reliable", "Voted the most reliable courier in the 2030 Wexbridge Parcel Survey.",
         "the most reliable courier in the 2030 Wexbridge Parcel Survey", subject={"kind": "business", "ref": "Skylark Couriers"},
         fact_type="result", claim_class="comparative"),
    fact("avg-rating", "Average rating 4.8 out of 5 from 210 reviews.", "4.8 out of 5 from 210 reviews",
         subject={"kind": "business", "ref": "Skylark Couriers"}, fact_type="result", claim_class="result"),
    fact("driver-cred", "All drivers are qualified HGV drivers with a CPC card.", "qualified HGV drivers",
         subject={"kind": "business", "ref": "Skylark Couriers drivers"}, fact_type="credential"),
]


@pytest.mark.parametrize("text", [
    "Harbour Row's most trusted courier.",
    "It is one of the most efficient vans in the region.",
    "The county's most secure parcel lockers.",
    "Customers rate us 4.6 out of 5.",
    "Customers give us 4.8/5 on average.",                 # only the same number is supported
    "Every Skylark dispatcher is a qualified accountant.",
    "All our packers are fully qualified.",
    "Each courier is licensed.",
])
def test_unsupported_superlatives_scores_and_staff_credentials_have_no_source(text):
    f = run(text, RESULTS[:1] + RESULTS[2:])
    assert ("no_source", None) in blocking(f), labels(f)


@pytest.mark.parametrize("text,key", [
    ("Skylark is the most reliable courier in Wexbridge.", "survey-reliable"),
    ("Customers rate us 4.8 out of 5.", "avg-rating"),
    ("All our drivers are qualified HGV drivers.", "driver-cred"),
])
def test_superlative_score_or_credential_with_a_fact_saying_it_matches(text, key):
    f = run(text, RESULTS)
    assert blocking(f) == [] and ("match", key) in labels(f), labels(f)


@pytest.mark.parametrize("text", [
    "Most of our customers book online.",
    "We reply within two hours at most.",
    "Read the most recent service update.",
    "Most people choose next-day delivery.",
    "You will most likely get a text first.",
    "2 out of 5 parcels arrive before noon.",              # a count of things, not a score
    "Collections restart on 4/5/2031.",
    "We only follow up qualified leads.",
    "If you qualified for a refund, we will email you.",
    "Every parcel is tracked.",
])
def test_ordinary_most_scores_and_qualified_are_quiet(text):
    f = run(text, RESULTS)
    assert not [x for x in f if x["blocking"] and x["label"] == "no_source" and x["fact_key"] is None
                and ("most" in x["detail"] or "out of" in x["detail"] or "qualified" in x["detail"])], labels(f)


def test_own_range_most_is_review_only():
    f = run("Our most advanced van yet.", RESULTS)
    assert blocking(f) == [] and ("review", None) in labels(f), labels(f)


# ---------- 4. "excl." does not split a sentence; a disclosure naming a stale fact is not a use

def test_disclosure_naming_a_stale_fact_is_not_a_mention():
    kit = [
        fact("kit-price", "A starter kit costs £40, excl. VAT and delivery surcharge.", "£40",
             subject={"kind": "product", "ref": "Starter kit"}, fact_type="price", value=40, currency="GBP",
             required_disclosures=["excl. VAT and delivery surcharge"]),
        fact("surcharge", "The delivery surcharge was 6% until March.", "6%", subject={"kind": "policy", "ref": "Delivery surcharge"},
             fact_type="price", value=6, unit="%", valid_to="2031-03-31"),
    ]
    text = "Starter kits are £40, excl. VAT and delivery surcharge."
    assert len(F.sentence_spans(text)) == 1
    f = run(text, kit)
    assert blocking(f) == [] and not [x for x in f if x["label"] == "review"], labels(f)
    assert ("conflict_or_expired", "surcharge") in blocking(run("The delivery surcharge is 6%.", kit))
