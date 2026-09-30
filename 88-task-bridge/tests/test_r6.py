"""Sixth round (invented businesses, not the eval companies): offers named in their own words,
the saving of an expired offer, how-often values, dash-segmented identifiers, ratings in words on a
named scheme (and another site's rating), grades in words, award tiers, in-house professionals, and
place superlatives. Every rule has negative controls."""
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


# ---------- 1. an expired offer named in its own words, another order / form

BOILERS = [
    fact("boiler-service", "An annual boiler service costs £95.", "£95", subject={"kind": "service", "ref": "Boiler service"},
         fact_type="price", attribute="price", value=95, currency="GBP"),
    fact("winter-boiler-offer", "Winter offer: a boiler service for £75 instead of £95, 1 to 30 April 2031.", "£75",
         subject={"kind": "offer", "ref": "Winter boiler-servicing offer"}, fact_type="price", attribute="offer_price",
         value=75, currency="GBP", valid_from="2031-04-01", valid_to="2031-04-30"),
    fact("spring-radiator-offer", "Spring offer: radiator flush £40, 1 May to 30 June 2031.", "£40",
         subject={"kind": "offer", "ref": "Spring radiator flush offer"}, fact_type="price", attribute="offer_price",
         value=40, currency="GBP", valid_from="2031-05-01", valid_to="2031-06-30"),
]


@pytest.mark.parametrize("text", ["Our winter boiler deal is back!", "Subject: The boilers winter special is here"])
def test_expired_offer_named_in_its_own_words_blocks(text):
    assert ("conflict_or_expired", "winter-boiler-offer") in blocking(run(text, BOILERS))


@pytest.mark.parametrize("text", [
    "Winter is hard on your boiler, so book a service.",     # no offer word after the name
    "Our boiler deal is back!",                               # one word of the name only
    "Our spring radiator deal is on now.",                    # a valid offer, loosely named
    "A winter check keeps your boiler safe; ask about a deal.",   # name words far from the offer word
])
def test_offer_words_negative_controls(text):
    assert ("conflict_or_expired", "winter-boiler-offer") not in blocking(run(text, BOILERS))


# ---------- 2. the saving of an expired offer is that offer's, not the regular price's

@pytest.mark.parametrize("text", ["Save £20 on your boiler service this month.",
                                  "Book now and we'll take £20 off the usual boiler service price."])
def test_saving_of_an_expired_offer_is_the_offer(text):
    f = blocking(run(text, BOILERS))
    assert ("conflict_or_expired", "winter-boiler-offer") in f and ("conflict_or_expired", "boiler-service") not in f


def test_saving_amount_without_a_saving_word_is_not_the_offer():
    f = blocking(run("A £20 call-out covers the first visit to your boiler.", BOILERS))
    assert ("conflict_or_expired", "winter-boiler-offer") not in f


def test_saving_of_a_valid_offer_is_not_flagged():
    facts = BOILERS[:1] + [dict(BOILERS[1], valid_to="2031-05-31")]
    assert blocking(run("Save £20 on your boiler service this month.", facts)) == []


# ---------- 3. how often: a stale timetable's frequency, another than the valid one's

BOATS = [
    fact("timetable-low", "The low-season timetable has three departures a day to the lighthouse.",
         "three departures a day", subject={"kind": "service", "ref": "Lighthouse boat low-season timetable"},
         fact_type="hours", attribute="departures_per_day", value=3, valid_from="2031-05-01"),
    fact("timetable-high", "The high-season timetable had six departures a day to the lighthouse.",
         "six departures a day", subject={"kind": "service", "ref": "Lighthouse boat high-season timetable"},
         fact_type="hours", attribute="departures_per_day", value=6, valid_from="2030-06-01", valid_to="2031-04-30"),
]


@pytest.mark.parametrize("text", ["Six departures a day to the lighthouse!", "Boats leave on the hour, 6 times a day."])
def test_stale_frequency_blocks(text):
    assert ("conflict_or_expired", "timetable-high") in blocking(run(text, BOATS))


@pytest.mark.parametrize("text", ["Three departures a day to the lighthouse.", "Boats go three times a day.",
                                  "Six sailings a week in winter.",         # another period
                                  "Brush your teeth twice a day."])
def test_frequency_negative_controls(text):
    assert blocking(run(text, BOATS)) == []


def test_frequency_with_no_frequency_fact_is_silent():
    assert run("We walk the dogs twice a day.", BOILERS) == []


# ---------- 4. identifiers with dashes / a word between the scheme and "number"

KENNEL = [
    fact("kennel-licence", "Harbour Kennels holds council licence ID 2031-5567 for dog boarding.", "licensed for dog boarding",
         subject={"kind": "business", "ref": "Harbour Kennels"}, fact_type="certification", attribute="licence"),
    fact("club-member", "We are club member kennel no. 4412 of the Boarding Guild.", "Boarding Guild member",
         subject={"kind": "business", "ref": "Harbour Kennels"}, fact_type="credential", attribute="membership"),
]


@pytest.mark.parametrize("text,key", [("Our licence ID 2031-5576 is on the wall.", "kennel-licence"),
                                      ("Boarding Guild member kennel number 4421.", "club-member")])
def test_lookalike_segmented_identifier_blocks(text, key):
    assert ("conflict_or_expired", key) in blocking(run(text, KENNEL))


@pytest.mark.parametrize("text", ["Our licence ID 2031-5567 is on the wall.", "Boarding Guild member kennel number 4412.",
                                  "Call 0113-496-0000 to book.", "Opening in 2031 with 20 kennels.",
                                  "Our licence ID 55-1234-9876 is new."])       # another shape: not a look-alike
def test_identifier_negative_controls(text):
    assert blocking(run(text, KENNEL)) == []


# ---------- 5. ratings in words on a named scheme; another site's rating; grades in words; tiers

HOLIDAY = [
    fact("hqa-harbour", "Harbour Lodge is rated Commended by the Holiday Quality Association (HQA).",
         "rated Commended by the HQA", subject={"kind": "site", "ref": "Harbour Lodge"}, fact_type="certification",
         attribute="hqa_rating", value="Commended", sites=["harbour"]),
    fact("hqa-cliff", "Cliff Lodge is rated Highly Commended by the Holiday Quality Association (HQA).",
         "rated Highly Commended by the HQA", subject={"kind": "site", "ref": "Cliff Lodge"}, fact_type="certification",
         attribute="hqa_rating", value="Highly Commended", sites=["cliff"]),
]
HARBOUR = {"sites": ["harbour"]}


def test_rating_word_off_the_schemes_scale_conflicts():
    f = run("Harbour Lodge is rated Exceptional by the HQA.", HOLIDAY, HARBOUR)
    assert ("conflict_or_expired", "hqa-harbour") in blocking(f)


def test_other_sites_rating_is_wrong_scope():
    f = run("Harbour Lodge is rated Highly Commended by the Holiday Quality Association.", HOLIDAY, HARBOUR)
    assert ("wrong_scope", "hqa-cliff") in blocking(f)
    assert ("wrong_scope", "hqa-cliff") in blocking(run("Subject: Highly Commended stays at Harbour Lodge", HOLIDAY, HARBOUR))


@pytest.mark.parametrize("text", ["Harbour Lodge is rated Commended by the HQA.",
                                  "Guests rated Exceptional our breakfasts.",        # no scheme named
                                  "A Commended stay at Harbour Lodge.",               # its own rating
                                  "Harbour Lodge is not rated Exceptional by the HQA."])
def test_rating_word_negative_controls(text):
    assert blocking(run(text, HOLIDAY, HARBOUR)) == []


FARRIERS = [
    fact("farrier-stage", "All our farriers hold Guild Stage Two (none holds Stage Three).", "Guild Stage Two",
         subject={"kind": "business", "ref": "Anvil Farriers"}, fact_type="credential", attribute="guild_stage"),
    fact("shoe-kit", "The Rover Stage 2 shoeing kit is £45.", "Rover Stage 2 kit", subject={"kind": "product", "ref": "Rover kit"},
         fact_type="spec", attribute="model"),
    fact("green-award", "Anvil Farriers holds the Green Yard Silver award.", "Green Yard Silver award",
         subject={"kind": "business", "ref": "Anvil Farriers"}, fact_type="certification", attribute="award"),
]


def test_grade_in_words_conflicts_and_matches():
    assert ("conflict_or_expired", "farrier-stage") in blocking(run("Our lead farrier is Guild Stage Three qualified.", FARRIERS))
    assert blocking(run("Every farrier holds Guild Stage Two.", FARRIERS)) == []


def test_award_tier_conflicts_and_matches():
    assert ("conflict_or_expired", "green-award") in blocking(run("We hold the Green Yard Gold award.", FARRIERS))
    assert blocking(run("We hold the Green Yard Silver award.", FARRIERS)) == []


# ---------- 6. in-house / on-site professionals, place superlatives, "<Place>'s number one"

STAFF = [
    fact("nutrition", "Our in-house nutritionist plans every menu.", "in-house nutritionist",
         subject={"kind": "business", "ref": "Fit Kitchen"}, fact_type="claim", attribute="staff"),
    fact("level3", "Every chef holds the Level 3 Award in Food Safety.", "Level 3 Award in Food Safety",
         subject={"kind": "business", "ref": "Fit Kitchen chefs"}, fact_type="credential", attribute="qualification"),
]


@pytest.mark.parametrize("text", ["Every plan is checked by our in-house dietitian.", "Our on-site physiotherapist helps.",
                                  "Menus reviewed by our in-house microbiologist."])
def test_in_house_professional_with_no_fact_is_no_source(text):
    assert ("no_source", None) in blocking(run(text, STAFF))


@pytest.mark.parametrize("text", ["Our in-house nutritionist plans every menu.", "Free on-site parking for diners.",
                                  "Everything is baked in our in-house bakery.", "An on-site cafe is open daily."])
def test_in_house_negative_controls(text):
    assert blocking(run(text, STAFF)) == []


@pytest.mark.parametrize("text", ["The fastest delivery on the whole coast.", "Ashby's number one meal kitchen."])
def test_place_superlatives_need_their_own_fact(text):
    assert ("no_source", None) in blocking(run(text, STAFF))


def test_number_one_supported_by_a_fact_that_says_it():
    facts = STAFF + [fact("voted", "Voted Ashby's number one meal kitchen, Ashby Food Awards 2030.",
                          "Ashby's number one meal kitchen", fact_type="result", attribute="award")]
    assert blocking(run("Ashby's number one meal kitchen.", facts)) == []


@pytest.mark.parametrize("text", ["The latest news on the coast.", "Our number one priority is you."])
def test_superlative_negative_controls(text):
    assert blocking(run(text, STAFF)) == []
