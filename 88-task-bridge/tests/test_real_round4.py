"""Real-data round 4 (a lodge group and a beer distributor, mixed languages): clusters fixed IN GENERAL.
Invented businesses and wording (Cedar Mill Hotel, Rivermouth Brewing), every rule with negative controls.

a. a count of countable things an in-scope fact counts otherwise ("three function rooms" for two,
   "over 90 rooms" for 48); same noun and qualifier only; approximations and ceilings are left alone;
b. hours and minutes are one unit ("a two-hour kayak trip" against a 90-minute excursion);
c. a named room type the business has none of ("Lagoon Villa" among rooms and tents): wrong_scope;
d. a place said with "find us in", "visit us in", "based in" that no fact serves: wrong_scope;
e. a priced product line in a size no fact sells at all ("Kiboko 66cl" among 33cl and 50cl);
f. a size said with "in" ("Lager 0.0% in 1 litre bottles") is a use of that product's sizes;
g. "a crate of Pale Ale is 1,200 birr" says the unit "per crate".
"""
from datetime import date

import pytest

from app import evidence

from .data import fact

DAY = date(2026, 11, 10)


def room(key, ref, val, sites=("mill",)):
    return fact(key, f"{ref} at Cedar Mill Hotel: ${val} per room per night for two guests, breakfast included.",
                f"${val} per room per night", subject={"kind": "variant", "ref": ref}, fact_type="price",
                attribute="rate", value=val, unit="night", currency="USD", basis="per_room",
                conditions=[{"key": "guests", "op": "=", "value": 2}], sites=list(sites),
                required_disclosures=["per room per night"])


HOTEL = [
    fact("mill-rooms", "Cedar Mill Hotel has 48 rooms, most with a garden outlook.", "48 rooms",
         subject={"kind": "site", "ref": "Cedar Mill Hotel"}, fact_type="claim", value=48, unit="rooms", sites=["mill"]),
    fact("mill-function", "Two function rooms: the Atrium and the Terrace Suite.", None,
         subject={"kind": "service", "ref": "Function rooms"}, sites=["mill"]),
    fact("mill-deluxe", "Cedar Mill Hotel has 12 deluxe rooms on the top floor.", None,
         subject={"kind": "service", "ref": "Deluxe floor"}, sites=["mill"]),
    room("garden-2", "Garden Room", 90),
    room("terrace-2", "Terrace Room", 120),
    room("tent-2", "Glamping Tent", 70),
    room("loft-2", "Loft Room", 140),
    fact("mill-kayak", "Sunrise kayak trip: a 90-minute excursion from Cedar Mill Hotel.", "90 minutes",
         subject={"kind": "service", "ref": "Sunrise kayak trip"}, fact_type="spec", attribute="duration",
         value=90, unit="minutes", sites=["mill"]),
    fact("mill-other", "Cedar Mill Hotel Coast has 300 rooms.", "300 rooms",
         subject={"kind": "site", "ref": "Cedar Mill Hotel Coast"}, value=300, unit="rooms", sites=["coast"]),
]
SCOPE = {"sites": ["mill"]}


def run(text, facts=None, scope=None):
    return evidence.check_text(text, facts or HOTEL, DAY, scope or SCOPE, None)[0]


def blocking(findings):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"]]


# ===================================================================== a. counts of things

@pytest.mark.parametrize("text, key", [
    ("Hold your workshop in one of our three function rooms.", "mill-function"),
    ("With 5 function rooms we can host any event.", "mill-function"),
    ("Over 90 rooms, most with a garden outlook.", "mill-rooms"),
    ("Choose from 60 rooms in the heart of town.", "mill-rooms"),
    ("More than 48 rooms, most with a garden outlook.", "mill-rooms"),
    ("At least 50 rooms look onto the garden.", "mill-rooms"),
    ("Stay on the top floor, where all 20 deluxe rooms face the lake.", "mill-deluxe"),
])
def test_a_count_that_differs_from_the_fact_is_a_conflict(text, key):
    assert ("conflict_or_expired", key) in blocking(run(text)), text


@pytest.mark.parametrize("text", [
    "Hold your workshop in one of our two function rooms.",
    "Cedar Mill Hotel has 48 rooms, most with a garden outlook.",
    "Over 30 rooms, most with a garden outlook.",
    "At least 40 rooms look onto the garden.",
    "At least 48 rooms look onto the garden.",
    "48+ rooms, most with a garden outlook.",
    "Up to 60 rooms can be booked for a wedding block.",
    "Around 50 rooms are usually available in spring.",
    "All 12 deluxe rooms are on the top floor.",
    "The 20 minutes it takes to walk to town is pleasant.",
    "Book 2 rooms or more and we add a late checkout.",
])
def test_counts_that_agree_or_are_approximate_or_about_something_else_stay_quiet(text):
    assert not [x for x in blocking(run(text)) if x[0] == "conflict_or_expired"], text


def test_a_count_for_the_other_site_is_not_this_sites_conflict():
    assert ("conflict_or_expired", "mill-other") not in blocking(run("Our coast hotel has 300 rooms."))


# ===================================================================== b. hours are minutes

@pytest.mark.parametrize("text", [
    "Join the sunrise kayak trip: a two-hour paddle from Cedar Mill Hotel.",
    "The sunrise kayak trip lasts 3 hours.",
])
def test_a_duration_in_hours_is_compared_with_minutes(text):
    assert ("conflict_or_expired", "mill-kayak") in blocking(run(text)), text


@pytest.mark.parametrize("text", [
    "Join the sunrise kayak trip: a 1.5-hour paddle from Cedar Mill Hotel.",
    "The sunrise kayak trip lasts 90 minutes.",
    "The sunrise kayak trip lasts 1.5 hours.",
])
def test_the_same_duration_in_other_units_is_a_match(text):
    assert blocking(run(text)) == [], text


# ===================================================================== c. a room type the business lacks

@pytest.mark.parametrize("text", [
    "Our new Lagoon Villa sleeps six for $300 per room per night.",
    "Treat yourself to the Royal Penthouse for $450 per room per night.",
    "Sleep in a Reed Cottage by the water.",
])
def test_a_named_room_type_no_fact_has_is_wrong_scope(text):
    assert ("wrong_scope", None) in blocking(run(text)), text


@pytest.mark.parametrize("text", [
    "Book a Garden Room for two guests at $90 per room per night.",
    "Our Glamping Tent is $70 per room per night for two guests, breakfast included.",
    "We do not have a Lagoon Villa.",
    "Lagoon Villa Resort Spa Day Offers Explained",
])
def test_known_types_negations_and_headings_are_not_flagged_as_unknown_types(text):
    assert ("wrong_scope", None) not in blocking(run(text)), text


def test_a_type_one_fact_does_name_is_known():
    facts = HOTEL + [fact("mill-villa", "The Lagoon Villa sleeps six.", None,
                          subject={"kind": "variant", "ref": "Lagoon Villa"}, sites=["mill"])]
    assert ("wrong_scope", None) not in blocking(run("Our new Lagoon Villa sleeps six.", facts))


# ===================================================================== d. a place no fact serves

@pytest.mark.parametrize("text", [
    "Find us in Kampala too.",
    "Come and visit us in Kigali this spring.",
    "We are based in Nairobi and the coast.",
])
def test_a_place_said_with_find_us_is_checked_against_the_places_served(text):
    assert ("wrong_scope", None) in blocking(run(text)), text


@pytest.mark.parametrize("text", [
    "Find us in the mill at the end of the lane.",
    "Visit us in spring for the garden.",
    "We are based in the Cedar Mill.",
])
def test_ordinary_find_us_and_visit_us_sentences_are_not_places(text):
    assert ("wrong_scope", None) not in blocking(run(text)), text


# ===================================================================== beer: sizes and units

BREW = [
    fact("lager-33", "Pale Lager 33cl (24 x 33cl): 1,700 birr per crate.", "1,700 birr per crate",
         subject={"kind": "product", "ref": "Pale Lager 33cl"}, fact_type="price", attribute="price",
         value=1700, unit="crate", currency="ETB", required_disclosures=["per crate"], sites=["town"]),
    fact("stout-50", "Black Stout 50cl (20 x 50cl): 2,000 birr per crate.", "2,000 birr per crate",
         subject={"kind": "product", "ref": "Black Stout 50cl"}, fact_type="price", attribute="price",
         value=2000, unit="crate", currency="ETB", required_disclosures=["per crate"], sites=["town"]),
    fact("zero-33", "Lager 0.0% 33cl (24 x 33cl): 1,500 birr per crate.", "1,500 birr per crate",
         subject={"kind": "product", "ref": "Lager 0.0% 33cl"}, fact_type="price", attribute="price",
         value=1500, unit="crate", currency="ETB", required_disclosures=["per crate"], sites=["town"]),
]


def brew(text):
    return evidence.check_text(text, BREW, DAY, {"sites": ["town"]}, None)[0]


@pytest.mark.parametrize("text", [
    "Kiboko 66cl (12 x 66cl): 1,900 birr per crate.",
    "Kiboko 1 litre is 2,400 birr per crate.",
])
def test_e_a_priced_line_in_a_size_no_fact_has_is_wrong_scope(text):
    assert ("wrong_scope", None) in blocking(brew(text)), text


@pytest.mark.parametrize("text", [
    "Pale Lager 33cl (24 x 33cl): 1,700 birr per crate.",
    "Black Stout 50cl (20 x 50cl): 2,000 birr per crate.",
    "Our bottles come in 66cl bars of soap in the gift shop.",
    "Kiboko is a name we like, and 66cl is a lot of beer.",
])
def test_known_sizes_and_unpriced_or_nameless_mentions_are_not_flagged(text):
    assert ("wrong_scope", None) not in blocking(brew(text)), text


def test_f_a_size_said_with_in_is_a_use_of_the_products_sizes():
    out = blocking(brew("Lager 0.0% in 1 litre bottles is 2,100 birr per crate."))
    assert ("wrong_scope", None) in out
    assert ("wrong_scope", None) not in blocking(brew("Lager 0.0% in 33cl bottles is 1,500 birr per crate."))


@pytest.mark.parametrize("text", [
    "A crate of Pale Lager 33cl is 1,700 birr.",
    "Black Stout 50cl is 2,000 birr for a crate of twenty bottles.",
    "One crate costs 1,700 birr for Pale Lager 33cl.",
])
def test_g_a_price_said_as_the_price_of_a_crate_says_per_crate(text):
    assert not [x for x in blocking(brew(text)) if x[0] == "missing_disclosure"], text


def test_g_a_bare_price_still_needs_its_unit():
    assert [x for x in blocking(brew("Pale Lager 33cl is 1,700 birr.")) if x[0] == "missing_disclosure"]
    assert [x for x in blocking(brew("The crate is red. Pale Lager 33cl is 1,700 birr.")) if x[0] == "missing_disclosure"]
