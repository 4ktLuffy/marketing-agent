"""Real-data round 5 (a lodge group and a drinks distributor): clusters fixed IN GENERAL.
Invented businesses and wording (Harbour Inn, Reedbank Brewing), every rule with negative controls.

a. amounts in words in kora ("one thousand six hundred kora") are read like "1,600 kora";
c. "each guest", "every person", "a head" after a price are per person;
d. an expired non-price fact named by a distinctive subject ("our Rock Pool Spa"): conflict_or_expired;
e. another SITE named outright by its full subject: wrong_scope (a bare region word stays review);
f. a score on a review platform ("rate us 4.9 on TripAdvisor") with no rating fact: no_source;
g. a branch of the business in a town no fact has ("Our Hillcrest hotel", "Harbour Inn Northgate has 90 rooms"),
   whose numbers are not the real site's;
h. a size no fact sells said as new / as cans, with a price: wrong_scope;
i. a duration is the duration of the subject the sentence names, as a price is;
j. "seven days a week" = "every day".
"""
from datetime import date

import pytest

from app import evidence

from .data import fact

DAY = date(2026, 11, 10)


def cruise(key, ref, minutes, status="active", valid_to=None, sites=("bayside",)):
    return fact(key, f"{ref}: a {minutes}-minute outing from Harbour Inn Bayside.", f"{minutes} minutes",
                subject={"kind": "service", "ref": ref}, fact_type="spec", attribute="duration", value=minutes,
                unit="minutes", sites=list(sites), status=status, valid_to=valid_to)


INN = [
    fact("bayside-site", "Harbour Inn Bayside has 60 rooms facing the harbour.", "60 rooms",
         subject={"kind": "site", "ref": "Harbour Inn Bayside"}, value=60, unit="rooms", sites=["bayside"]),
    fact("hilltop-site", "Harbour Inn Hilltop is a quiet lodge on the cliff road.", None,
         subject={"kind": "site", "ref": "Harbour Inn Hilltop"}, sites=["hilltop"]),
    fact("rock-spa", "The Rock Pool Spa has a steam room and a sauna.", None,
         subject={"kind": "service", "ref": "The Rock Pool Spa"}, status="expired", valid_to="2026-11-01",
         sites=["bayside"]),
    fact("gym-pool", "Gym, pool and spa treatments are open to guests.", None,
         subject={"kind": "service", "ref": "Gym and pool"}, sites=["bayside"]),
    fact("beach-hours", "The beach club is open all day, every day.", "open all day, every day",
         subject={"kind": "service", "ref": "Beach club"}, fact_type="hours", sites=["bayside"]),
    cruise("harbour-tour", "Harbour Tour", 120),
    cruise("dolphin-cruise", "Dolphin Cruise", 90),
    cruise("cliff-walk", "Cliff Walk", 120, status="expired", valid_to="2026-11-01"),
    fact("day-cruise", "Day cruise tickets are $40 per person.", "$40 per person",
         subject={"kind": "service", "ref": "Day cruise tickets"}, fact_type="price", attribute="ticket", value=40,
         currency="USD", basis="per_person", required_disclosures=["per person"], sites=["bayside"]),
    fact("room-night", "Garden Room: 3,000 kora per night.", "3,000 kora per night",
         subject={"kind": "variant", "ref": "Garden Room"}, fact_type="price", attribute="rate", value=3000,
         currency="XKR", unit="night", basis="per_room", required_disclosures=["per night"], sites=["bayside"]),
]
SCOPE = {"sites": ["bayside"]}


def run(text, facts=None, scope=None):
    return evidence.check_text(text, facts or INN, DAY, scope or SCOPE, None)[0]


def blocking(findings):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"]]


# ===================================================================== a. kora amounts in words

BREW = [
    fact("lager-33", "Reed Lager 33cl (24 x 33cl): 1,700 kora per crate.", "1,700 kora per crate",
         subject={"kind": "product", "ref": "Reed Lager 33cl"}, fact_type="price", attribute="price", value=1700,
         unit="crate", currency="XKR", required_disclosures=["per crate"], sites=["town"]),
    fact("lager-33-old", "Reed Lager 33cl (24 x 33cl): 1,600 kora per crate.", "1,600 kora per crate",
         subject={"kind": "product", "ref": "Reed Lager 33cl"}, fact_type="price", attribute="price", value=1600,
         unit="crate", currency="XKR", required_disclosures=["per crate"], sites=["town"], status="expired",
         valid_to="2026-11-01"),
    fact("stout-50", "Dark Stout 50cl (20 x 50cl): 2,010 kora per crate.", "2,010 kora per crate",
         subject={"kind": "product", "ref": "Dark Stout 50cl"}, fact_type="price", attribute="price", value=2010,
         unit="crate", currency="XKR", required_disclosures=["per crate"], sites=["town"]),
    fact("stout-50-old", "Dark Stout 50cl (20 x 50cl): 1,935 kora per crate.", "1,935 kora per crate",
         subject={"kind": "product", "ref": "Dark Stout 50cl"}, fact_type="price", attribute="price", value=1935,
         unit="crate", currency="XKR", required_disclosures=["per crate"], sites=["town"], status="expired",
         valid_to="2026-11-01"),
    fact("depot", "Reedbank Brewing Depot sells to bars and shops in Kembata.", None,
         subject={"kind": "site", "ref": "Reedbank Depot Kembata"}, sites=["town"]),
]


def brew(text):
    return evidence.check_text(text, BREW, DAY, {"sites": ["town"]}, None)[0]


@pytest.mark.parametrize("text, key", [
    ("A crate of Reed Lager 33cl was one thousand six hundred kora per crate.", "lager-33-old"),
    ("Dark Stout 50cl is one thousand nine hundred and thirty-five kora per crate.", "stout-50-old"),
    ("Dark Stout 50cl: one thousand nine hundred thirty-five kora per crate.", "stout-50-old"),
])
def test_a_an_old_price_in_words_in_kora_is_the_expired_facts(text, key):
    assert ("conflict_or_expired", key) in blocking(brew(text)), text


@pytest.mark.parametrize("text", [
    "Reed Lager 33cl is one thousand seven hundred kora per crate.",
    "Dark Stout 50cl: two thousand and ten kora per crate.",
    "Dark Stout 50cl: two thousand ten kora per crate.",
])
def test_a_the_current_price_in_words_is_a_match(text):
    assert blocking(brew(text)) == [], text


def test_a_a_price_in_words_that_is_not_the_products_is_a_conflict_with_the_product():
    out = blocking(brew("Reed Lager 33cl is one thousand eight hundred forty-five kora per crate."))
    assert ("conflict_or_expired", "lager-33") in out


def test_a_the_whole_amount_in_words_is_read():
    assert [v.key for v in evidence.extract("Pay one thousand four hundred thirty-five kora.")] == [("money", "1435", "XKR")]


# ===================================================================== c. per person said otherwise

@pytest.mark.parametrize("text", [
    "Day cruise tickets are $40 for each guest.",
    "Day cruise tickets are $40 for every person.",
    "Day cruise tickets: $40 a head.",
    "Day cruise tickets cost forty dollars each person.",
    "Day cruise tickets cost $40 a person.",
])
def test_c_per_person_in_other_words_is_the_disclosure(text):
    assert not [x for x in blocking(run(text)) if x[0] == "missing_disclosure"], text


@pytest.mark.parametrize("text", [
    "Day cruise tickets are $40.",
    "Day cruise tickets are $40 each way.",
])
def test_c_a_price_with_no_basis_still_needs_it(text):
    assert ("missing_disclosure", "day-cruise") in blocking(run(text)), text


# ===================================================================== d. an expired fact named by its subject

@pytest.mark.parametrize("text", [
    "Unwind in our Rock Pool Spa after your swim.",
    "Do try the rock pool spa with its sauna and steam room.",
    "Relax in the Rock Pool Spa before dinner.",
    "Meet friends at the Rock Pool Spa, a quiet hour away from the beach.",
])
def test_d_an_expired_fact_named_by_a_distinctive_subject_is_a_conflict(text):
    assert ("conflict_or_expired", "rock-spa") in blocking(run(text)), text


@pytest.mark.parametrize("text", [
    "Spa treatments are open to guests.",
    "Our gym, pool and spa are open to guests.",
    "We have no Rock Pool Spa.",
    "Rock Pool Spa reviews are still coming in.",
    "The rock pool by the beach is lovely.",
])
def test_d_generic_words_and_negations_do_not_name_the_expired_fact(text):
    assert ("conflict_or_expired", "rock-spa") not in blocking(run(text)), text


# ===================================================================== e. another site named outright

@pytest.mark.parametrize("text", [
    "Harbour Inn Hilltop is the place for a quiet night.",
    "Book a night at Harbour Inn Hilltop, high above the town.",
])
def test_e_another_sites_full_name_is_wrong_scope(text):
    assert ("wrong_scope", "hilltop-site") in blocking(run(text)), text


@pytest.mark.parametrize("text", [
    "The hilltop views are lovely at sunset.",
    "Unlike Harbour Inn Hilltop, we are right on the water.",
    "Harbour Inn Bayside is right on the harbour.",
])
def test_e_a_bare_region_word_or_a_negation_or_this_site_is_not_flagged(text):
    assert ("wrong_scope", "hilltop-site") not in blocking(run(text)), text


# ===================================================================== f. platform ratings

@pytest.mark.parametrize("text", [
    "Guests rate us 4.9 on TripAdvisor.",
    "We hold 9.4 on Booking.com.",
    "Our guests score us 4.8 on Google.",
    "Averaging 4.7 on Google Reviews since we opened.",
    "4.9 on TripAdvisor!",
])
def test_f_a_platform_score_with_no_rating_fact_is_no_source(text):
    assert ("no_source", None) in blocking(run(text)), text


@pytest.mark.parametrize("text", [
    "Find us on Google Maps.",
    "We opened in 2019 and joined Booking.com in 2021.",
    "Follow us on Facebook for 3 posts a week.",
    "Check in is at 2 on Fridays.",
])
def test_f_ordinary_mentions_of_a_platform_are_not_scores(text):
    assert ("no_source", None) not in blocking(run(text)), text


def test_f_a_rating_fact_backs_the_score():
    facts = INN + [fact("rating", "Guests rate Harbour Inn Bayside 4.9 on TripAdvisor.", "4.9 on TripAdvisor",
                        subject={"kind": "site", "ref": "Harbour Inn Bayside rating"}, fact_type="result",
                        sites=["bayside"])]
    assert ("no_source", None) not in blocking(run("Guests rate us 4.9 on TripAdvisor.", facts))
    assert ("no_source", None) in blocking(run("Guests rate us 4.9 on Google.", facts))


# ===================================================================== g. a branch in a town with none

@pytest.mark.parametrize("text", [
    "Our Hillcrest hotel has a rooftop bar.",
    "Harbour Inn Northgate has 90 rooms.",
    "Harbour Inn Northmoor sleeps 300 guests.",
    "Come and see our new Eastbridge branch.",
])
def test_g_a_branch_in_another_town_is_wrong_scope(text):
    assert ("wrong_scope", None) in blocking(run(text)), text


def test_g_its_numbers_are_not_tied_to_the_real_site():
    out = blocking(run("Harbour Inn Northgate has 90 rooms."))
    assert out == [("wrong_scope", None)], out
    assert not [x for x in out if x[0] == "conflict_or_expired"]


@pytest.mark.parametrize("text", [
    "Harbour Inn Bayside has 60 rooms facing the harbour.",
    "Our Bayside hotel is by the harbour.",
    "We are not planning a Hillcrest hotel.",
    "Is there a Northgate hotel near you?",
    "Our Harbour Inn guests love the pool.",
])
def test_g_the_real_site_negations_questions_and_other_words_are_not_flagged(text):
    assert ("wrong_scope", None) not in blocking(run(text)), text


def test_g_a_depot_in_a_town_the_distributor_does_not_serve():
    assert ("wrong_scope", None) in blocking(brew("Order Reed Lager 33cl from our new Northgate depot."))
    assert ("wrong_scope", None) not in blocking(brew("Order Reed Lager 33cl from our Kembata depot."))


# ===================================================================== h. a size no fact sells

@pytest.mark.parametrize("text", [
    "New 25cl cans, 24 per pack, 1,500 kora per crate.",
    "Introducing 66cl bottles at 2,300 kora per crate.",
    "Now in 1 litre bottles: 2,400 kora per crate.",
])
def test_h_a_priced_size_no_fact_sells_is_wrong_scope_even_without_a_product_name(text):
    assert ("wrong_scope", None) in blocking(brew(text)), text


@pytest.mark.parametrize("text", [
    "New 33cl crates are in stock: Reed Lager 33cl is 1,700 kora per crate.",
    "New arrivals every week, with 24 bottles in a crate.",
    "The new 25cl measuring cups are in the gift shop.",
])
def test_h_known_sizes_and_unpriced_mentions_are_not_flagged(text):
    assert ("wrong_scope", None) not in blocking(brew(text)), text


# ===================================================================== i. a duration belongs to the subject named

@pytest.mark.parametrize("text", [
    "Cliff Walk, 120 min from the inn.",
    "The Cliff Walk is a two-hour outing.",
])
def test_i_a_duration_of_the_named_expired_subject_is_its_conflict(text):
    assert ("conflict_or_expired", "cliff-walk") in blocking(run(text)), text


def test_i_the_named_subjects_duration_is_not_another_subjects():
    out = blocking(run("Dolphin Cruise, 120 min from the quay."))
    assert ("conflict_or_expired", "dolphin-cruise") in out


@pytest.mark.parametrize("text", [
    "Dolphin Cruise, 90 min from the quay.",
    "Harbour Tour: 2 hours on the water.",
    "Dolphin Cruise (90 min) or the Harbour Tour (120 min), your choice.",
    "Take a 90 min outing on the water.",
])
def test_i_the_right_duration_two_subjects_or_none_are_quiet(text):
    assert not [x for x in blocking(run(text)) if x[0] == "conflict_or_expired"], text


# ===================================================================== j. seven days a week is every day

@pytest.mark.parametrize("text", [
    "The beach club is open throughout the day, seven days a week.",
    "Beach club: open all day, 7 days a week.",
])
def test_j_seven_days_a_week_is_every_day(text):
    assert blocking(run(text)) == [], text


def test_j_another_number_of_days_a_week_still_conflicts():
    facts = INN + [fact("club-days", "The kids club runs 5 days a week.", "5 days a week",
                        subject={"kind": "service", "ref": "Kids club"}, fact_type="hours", attribute="days",
                        value=5, unit="days", sites=["bayside"])]
    assert ("conflict_or_expired", "club-days") in blocking(run("The kids club runs 6 days a week.", facts))
