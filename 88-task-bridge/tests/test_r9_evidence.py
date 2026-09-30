"""Round 9 evidence rules (invented businesses, not the eval companies).

Disclosures said in other words (not included = extra / separately; age limits; periods in other
units; halves and quarters of a year; "-ation" nouns; one clipped or compound word), a claim word
about the reader's own people, identifiers written in digit groups, rating numbers in words and
ratings given by people, benefits said before a comma / waived / by two object words / only free out
of scope / joined by "and", restricted facts never offering anything, unitless capacities, "N for the
price of N-1" with "of", an out-of-scope subject named with the task's own scope word, a register's
grade under another name, a qualification a fact says is not held, and an in-house facility or a
dedicated person no fact names. Every rule has negative controls."""
from datetime import date

import pytest

from app import evidence

from .data import fact

DAY = date(2031, 5, 12)


def run(text, facts, scope=None):
    findings, _ = evidence.check_text(text, facts, DAY, scope or {})
    return findings


def blocking(findings):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"]]


def labels(findings):
    return [(f["label"], f["fact_key"]) for f in findings]


def expired_offer(key, ref, value_text, text):
    return fact(key, text, value_text, subject={"kind": "offer", "ref": ref}, fact_type="price", value=0,
                currency="GBP", valid_from="2031-01-01", valid_to="2031-04-30")


# ======================================================================= disclosures in other words

@pytest.mark.parametrize("disc,text,ok", [
    # not part of the price
    ("strings not included", "The Arden cello is £1,200 (strings extra).", True),
    ("strings not included", "The Arden cello is £1,200, strings sold separately.", True),
    ("strings not included", "The Arden cello is £1,200, with strings charged separately.", True),
    ("strings not included", "The Arden cello is £1,200 excluding strings.", True),
    ("strings not included", "The Arden cello is £1,200; strings cost extra.", True),
    ("strings not included", "The Arden cello is £1,200, strings included.", False),
    ("strings not included", "The Arden cello is £1,200 with an extra set of strings free.", False),
    ("worn soles cost extra", "Boot resoling is £45, with an extra charge for worn soles.", True),
    ("worn soles cost extra", "Boot resoling is £45 for worn soles.", False),
    # age limits
    ("for children under 12 years", "Junior climbing is £9 for children younger than twelve years.", True),
    ("for children under 12 years", "Junior climbing is £9 for children below the age of 12 years.", True),
    ("for children under 12 years", "Junior climbing is £9 for children over 12 years.", False),
    ("for children under 12 years", "Junior climbing is £9 for children aged 12 years.", False),
    # a period in other units
    ("when booked within 14 days", "Book within a fortnight to get the £9 rate.", True),
    ("when booked within 14 days", "Book within two weeks to get the £9 rate.", True),
    ("when booked within 14 days", "Book within a month to get the £9 rate.", False),
    ("when claimed within 30 days", "Claim within one month to get the £9 refund.", True),
    ("when claimed within 30 days", "Claim within a year to get the £9 refund.", False),
    # halves and quarters of a year
    ("July to December 2029 sample of 340 members", "88% of 340 members agreed in the second half of 2029.", True),
    ("July to December 2029 sample of 340 members", "88% agreed (340 members, H2 2029).", True),
    ("July to December 2029 sample of 340 members", "88% of 340 members agreed in the first half of 2029.", False),
    ("April to June 2029 cohort of 75 apprentices", "In Q2 2029, 75 apprentices sat the test.", True),
    ("April to June 2029 cohort of 75 apprentices", "In Q3 2029, 75 apprentices sat the test.", False),
    # "-ation" nouns and one clipped / compound word
    ("after home consultation", "The kitchen refit is from £4,000; price confirmed after a home consult.", True),
    ("after home consultation", "The kitchen refit is from £4,000; price confirmed without a home visit.", False),
    ("based on 40 night guests", "The party package is £900 for 40 nighttime guests.", True),
    ("based on 40 night guests", "The party package is £900 for 40 guests.", False),
    ("for kittens under 4 months", "The starter pack is £30 for kits younger than four months.", True),
    ("for kittens under 4 months", "The starter pack is £30 for cats younger than four months.", False),
])
def test_disclosure_said_in_other_words(disc, text, ok):
    assert evidence.disclosure_said(disc, text) is ok


def test_disclosure_in_other_words_does_not_block_the_piece():
    f = fact("cello", "The Arden cello costs £1,200; strings not included.", "£1,200", value=1200, currency="GBP",
             fact_type="price", subject={"kind": "product", "ref": "Arden cello"},
             required_disclosures=["strings not included"])
    assert not blocking(run("The Arden cello: £1,200 (strings extra).", [f]))
    assert ("missing_disclosure", "cello") in blocking(run("The Arden cello: £1,200.", [f]))


def test_clipped_word_needs_the_rest_said_exactly():
    # one clipped word is allowed only when at least two other words are said exactly
    assert not evidence.disclosure_said("puppies only", "pups welcome")
    assert not evidence.disclosure_said("for kittens under 4 months", "for kits under 6 months")


# ======================================================================= a claim word about the reader

CERT_FACTS = [fact("hours", "The Lathe Academy is open 9am to 5pm.", "9am to 5pm", subject={"kind": "business",
                                                                                          "ref": "Lathe Academy"})]


@pytest.mark.parametrize("text", [
    "Want your crew forklift-certified by spring?",
    "Get your whole workshop team certified this year.",
])
def test_certified_said_of_the_readers_people_does_not_block(text):
    f = run(text, CERT_FACTS)
    assert not blocking(f)
    assert any(x["label"] == "review" for x in f)


@pytest.mark.parametrize("text", [
    "Your certified welding instructor will meet you at 9am.",     # the business's person
    "Your lathe is certified to the latest standard.",              # a claim about what is sold
    "Our tutors are certified.",
])
def test_certified_about_the_business_still_needs_a_fact(text):
    assert ("no_source", None) in blocking(run(text, CERT_FACTS))


# ======================================================================= identifiers in digit groups

REG = [fact("brx", "Hollow Oak Roofing is a BRX member, member number 44-80213, and holds licence KLM-4-20981.",
            "BRX member number 44-80213, licence KLM-4-20981", fact_type="credential",
            subject={"kind": "business", "ref": "Hollow Oak Roofing"})]


@pytest.mark.parametrize("text", [
    "Our BRX member number is 44/80231.",
    "Membership number: 44.82013",
    "We hold licence KLM/4/29081.",
    "Licence KLM:420.918 applies to all our work.",
])
def test_grouped_identifier_with_other_digits_conflicts(text):
    assert ("conflict_or_expired", "brx") in blocking(run(text, REG))


@pytest.mark.parametrize("text", [
    "Our BRX member number is 44/80213.",
    "We hold licence KLM/4/20981.",
])
def test_grouped_identifier_with_the_same_digits_matches(text):
    f = run(text, REG)
    assert not blocking(f) and ("match", "brx") in labels(f)


@pytest.mark.parametrize("text", [
    "We registered on 12/03/2026 and have grown since.",     # a date
    "Call our registration line on 0113-496-0213.",          # a phone number
    "Our certificate course costs £120.50 per person.",      # a price
])
def test_dates_phones_and_prices_are_not_identifiers(text):
    assert not [x for x in run(text, REG) if x["fact_key"] == "brx" and x["blocking"]]


# ======================================================================= ratings

HYGIENE = [fact("hygiene", "The Pickled Pear kitchen holds food hygiene rating 4 (Good).", "food hygiene rating 4 (Good)",
                fact_type="credential", claim_class="regulated_food", subject={"kind": "business", "ref": "Pickled Pear"})]
REVIEWS = [fact("reviews", "The Pickled Pear is rated 4.8 stars from 300 Google reviews.", "4.8 stars from 300 Google reviews",
                fact_type="result", claim_class="result", subject={"kind": "business", "ref": "Pickled Pear"})]


def test_rating_number_in_words_that_differs_conflicts():
    assert ("conflict_or_expired", "hygiene") in blocking(run("Our kitchen is rated Five for food hygiene.", HYGIENE))


def test_rating_number_in_words_that_agrees_matches():
    f = run("Our kitchen is rated four for food hygiene.", HYGIENE)
    assert not blocking(f) and ("match", "hygiene") in labels(f)


def test_a_rating_given_by_people_is_not_a_scheme_rating():
    assert ("no_source", None) in blocking(run("Rated 4 stars by over 200 diners.", HYGIENE))


def test_a_rating_given_by_people_matches_a_review_fact():
    f = run("Rated 4.8 stars by our diners in Google reviews.", REVIEWS)
    assert not blocking(f) and ("match", "reviews") in labels(f)


def test_a_rating_of_another_thing_with_another_number_is_not_this_fact():
    # no word of the hygiene fact's topic: not "the same rating with another number"
    assert ("conflict_or_expired", "hygiene") not in blocking(run("Rated Five by our regulars!", HYGIENE))


# ======================================================================= benefits described loosely

NAILS = [expired_offer("wax-dip", "Spring Hands", "free paraffin wax dip with every manicure",
                       "Spring Hands: a free paraffin wax dip with every manicure, until 30 April."),
         expired_offer("waste-bonus", "Garden Bonus", "free garden waste collection",
                       "Garden Bonus: free garden waste collection with any hedge trim, until 30 April.")]


@pytest.mark.parametrize("text,key", [
    ("Paraffin wax dip, free with every manicure this month!", "wax-dip"),        # before a comma
    ("Book a hedge trim and we'll waive the garden waste fee.", "waste-bonus"),          # waived
    ("Book a trim this month and your garden waste pickup is on us.", "waste-bonus"),     # two object words
])
def test_benefit_of_an_expired_offer_said_loosely(text, key):
    assert ("conflict_or_expired", key) in blocking(run(text, NAILS))


@pytest.mark.parametrize("text", [
    "We'll waive the booking fee for new clients.",       # waived, but nothing an offer gives; never no_source
    "We won't waive the garden waste fee.",               # negated
    "Our garden is lovely this month, free to visit.",    # "free to": not a benefit
])
def test_benefit_negative_controls(text):
    assert not [x for x in blocking(run(text, NAILS)) if x[1] in ("wax-dip", "waste-bonus")]
    assert ("no_source", None) not in blocking(run(text, NAILS))


EYES = [fact("student-check", "Student eye checks are free for full-time students only.", "free",
             fact_type="price", value=0, currency="GBP", subject={"kind": "service", "ref": "Student eye check"},
             segments=["students"]),
        fact("adult-check", "An adult eye check costs £30.", "£30", fact_type="price", value=30, currency="GBP",
             subject={"kind": "service", "ref": "Adult eye check"}, segments=["adults"])]


def test_free_thing_only_an_out_of_scope_fact_gives_free_is_wrong_scope():
    for text in ("Eye checks at Brightsight are free.", "Book your free eye check today."):
        assert ("wrong_scope", "student-check") in blocking(run(text, EYES, {"segments": ["adults"]}))


def test_free_thing_in_its_own_scope_is_fine():
    assert not blocking(run("Book your free eye check today.", EYES, {"segments": ["students"]}))


def test_free_thing_nothing_to_do_with_the_zero_price_fact():
    assert ("wrong_scope", "student-check") not in blocking(run("Free parking behind the shop.", EYES,
                                                                {"segments": ["adults"]}))


REPAIRS = [fact("repairs", "Every bike we sell comes with free repairs for a year.", "free repairs for a year",
                fact_type="inclusion", subject={"kind": "policy", "ref": "Bike aftercare"}),
           fact("visit-fee", "Care Co pays £22 per home visit.", "£22 per home visit", fact_type="price", value=22,
                currency="GBP", subject={"kind": "service", "ref": "Home visits"}, sensitivity="restricted")]


def test_free_things_joined_by_and_one_of_them_offered():
    assert ("no_source", None) not in blocking(run("New bikes come with free fixes and repairs.", REPAIRS))


def test_free_things_joined_by_and_none_offered():
    assert ("no_source", None) in blocking(run("New bikes come with free fixes and polishing.", REPAIRS))


def test_what_only_a_restricted_fact_says_is_not_offered():
    assert ("no_source", None) in blocking(run("Every service includes a free home visit.", REPAIRS))


def test_a_public_fact_does_offer_it():
    facts = REPAIRS + [fact("home", "We offer free home visits in town.", "free home visits",
                            fact_type="inclusion", subject={"kind": "policy", "ref": "Home visits"})]
    assert ("no_source", None) not in blocking(run("Every service includes a free home visit.", facts))


# ======================================================================= capacities and deals

HALL = [fact("loft", "The Loft holds 60 standing guests; it is used for summer evenings only.", "60 standing guests",
             fact_type="spec", value=60, unit="standing_guests", subject={"kind": "site", "ref": "The Loft"},
             variants=["summer-evening"]),
        fact("terrace", "The Rooftop Terrace is open for summer evening parties.", "summer evening parties",
             fact_type="availability", subject={"kind": "site", "ref": "Rooftop Terrace"}, variants=["summer-evening"])]
WINTER = {"variants": ["winter-evening"]}


@pytest.mark.parametrize("text", [
    "Party with up to 60 guests in The Loft.",
    "The Loft sleeps 60 for your staff retreat.",
])
def test_unitless_capacity_of_an_out_of_scope_site(text):
    assert ("wrong_scope", "loft") in blocking(run(text, HALL, WINTER))


def test_capacity_in_scope_matches():
    assert not blocking(run("Party with up to 60 guests in The Loft.", HALL, {"variants": ["summer-evening"]}))


def test_out_of_scope_subject_named_with_the_tasks_own_scope_word():
    assert ("wrong_scope", "terrace") in blocking(run("A winter party on the Rooftop Terrace.", HALL, WINTER))


@pytest.mark.parametrize("text", [
    "Ask us about the Rooftop Terrace.",               # named, nothing of the task's scope: review only
    "The Rooftop Terrace has lovely views.",
])
def test_out_of_scope_subject_without_a_clash_does_not_block(text):
    assert ("wrong_scope", "terrace") not in blocking(run(text, HALL, WINTER))


PAINT = [expired_offer("paint-deal", "Paint Swap", "second tin free",
                       "Paint Swap: buy one tin of paint and get a second tin of equal or lower value free.")]


@pytest.mark.parametrize("text", [
    "Two tins of emulsion for the price of one.",
    "Grab two tins for the price of one this week!",
])
def test_price_of_deal_is_the_expired_offer(text):
    assert ("conflict_or_expired", "paint-deal") in blocking(run(text, PAINT))


@pytest.mark.parametrize("text", [
    "Two tins of emulsion for £30.",
    "Three tins for the price of one.",
])
def test_other_prices_are_not_that_deal(text):
    assert ("conflict_or_expired", "paint-deal") not in blocking(run(text, PAINT))


# ======================================================================= grades, denied names, roles

QFS = [fact("qfs", "Brookline Heat is registered with the QFS as a Certified Installer; QFS grades are Trainee "
            "Installer and Certified Installer.", "QFS Certified Installer", fact_type="credential",
            claim_class="safety_cert", subject={"kind": "business", "ref": "Brookline Heat"})]


def test_grade_name_the_register_does_not_have_conflicts():
    assert ("conflict_or_expired", "qfs") in blocking(run("We're QFS Elite Installers.", QFS))


@pytest.mark.parametrize("text", [
    "We're a QFS Certified Installer.",
    "Contact the QFS Head Office for the register.",     # another head noun: not a grade
])
def test_grade_name_negative_controls(text):
    assert ("conflict_or_expired", "qfs") not in blocking(run(text, QFS))


DIPLOMA = [fact("tutor", "Mara Quill holds the Foundation Diploma in Pastry (not the Advanced Diploma).",
                "Foundation Diploma in Pastry", fact_type="credential", subject={"kind": "person", "ref": "Mara Quill"})]


def test_qualification_the_fact_denies_conflicts():
    assert ("conflict_or_expired", "tutor") in blocking(run("She holds the Advanced Diploma in Pastry.", DIPLOMA))


@pytest.mark.parametrize("text", [
    "She holds the Foundation Diploma in Pastry.",
    "She holds the Foundation Diploma, not the Advanced Diploma.",
])
def test_qualification_negative_controls(text):
    assert ("conflict_or_expired", "tutor") not in blocking(run(text, DIPLOMA))


@pytest.mark.parametrize("text", [
    "Fresh loaves daily from our in-house bakery.",
    "Every client gets a dedicated account manager.",
    "Your personal chauffeur will meet you at the door.",
])
def test_facility_or_dedicated_person_without_a_fact_is_a_soft_no_source(text):
    f = run(text, CERT_FACTS)
    assert any(x["label"] == "no_source" for x in f) and not blocking(f)


def test_facility_or_dedicated_person_named_by_a_fact():
    facts = CERT_FACTS + [fact("bakery", "Our bread comes from our own in-house bakery.", "in-house bakery",
                               subject={"kind": "business", "ref": "Lathe Academy"}),
                          fact("am", "Every client has an account manager.", "account manager",
                               subject={"kind": "business", "ref": "Lathe Academy"})]
    for text in ("Fresh loaves daily from our in-house bakery.", "Every client gets a dedicated account manager."):
        assert not any(x["label"] == "no_source" for x in run(text, facts))
