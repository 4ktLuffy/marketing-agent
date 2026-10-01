"""Real-data round 3 (a lodge group's rooms, tents and spa): clusters fixed IN GENERAL. Invented business and
wording (Kibo Ridge Camp, Amboro Plains Lodge), every rule with negative controls.

a. a region named as a destination ("fly south to the Serengeti Basin") is not a use of another site's fact;
b. "river-view suites from $120" is true when a member of that room family costs $120;
c. "Hill-view Family Room" is the room "Family Room (Hill View)", not an invented room;
d. claims no fact backs: all-inclusive, UNESCO / World Heritage, "the only X in <place>", "seats N delegates";
e. "open 24 hours" / "until midnight" against a service's hours fact that says something else;
f. breakfast claimed for a room whose own fact says breakfast is not confirmed: a conflict with that fact;
g. one price for one guest and for two: a sentence that says neither is read as the two-guest rate.
"""
from datetime import date

import pytest

from app import evidence

from .data import fact

DAY = date(2026, 11, 10)


def room(key, ref, val, guests, breakfast="breakfast included", sites=("ridge",), sens="public"):
    who = "one guest" if guests == 1 else "two guests"
    return fact(key, f"{ref} at Kibo Ridge Camp: ${val} per room per night for {who}, {breakfast}, taxes included.",
                f"${val} per room per night", subject={"kind": "variant", "ref": ref}, fact_type="price",
                attribute="rate", value=val, unit="night", currency="USD", basis="per_room",
                conditions=[{"key": "guests", "op": "=", "value": guests}], sites=list(sites), sensitivity=sens,
                required_disclosures=["per room per night", "breakfast included"] if "not" not in breakfast else
                ["per room per night"])


CAMP = [
    fact("ridge-camp", "Kibo Ridge Camp is a hillside camp with 40 rooms.", "40 rooms",
         subject={"kind": "site", "ref": "Kibo Ridge Camp"}, sites=["ridge"]),
    fact("plains-lodge", "Amboro Plains Lodge is the base for game drives across the Serengeti Basin.",
         "base for game drives across the Serengeti Basin",
         subject={"kind": "site", "ref": "Amboro Plains Lodge"}, sites=["plains"]),
    room("suite-canopy-2", "Suite (River View)", 150, 2),
    room("suite-zebra-2", "Zebra Canopy Suite (River View)", 120, 2),
    room("suite-zebra-1", "Zebra Canopy Suite (River View)", 105, 1),
    room("standard-2", "Standard Room (Hill View)", 80, 2),
    room("family-2", "Family Room (Hill View)", 210, 2),
    room("budget-2", "Budget Room (Hill View)", 65, 2),
    room("loft-2", "Loft Room (Hill View)", 95, 2, breakfast="breakfast not confirmed"),
    room("tent-twin-1", "Safari Tent Twin", 60, 1, sites=("plains",)),
    room("tent-twin-2", "Safari Tent Twin", 60, 2, sites=("plains",)),
    fact("sunset-spa-hours", "The spa, sauna and steam room are open from 9am to 8pm, every day.",
         "open 9am to 8pm, every day", subject={"kind": "service", "ref": "Sunset Spa"}, fact_type="hours",
         attribute="opening hours", sites=["ridge"]),
    fact("reception-hours", "Reception is open 24 hours.", "open 24 hours",
         subject={"kind": "service", "ref": "Reception"}, fact_type="hours", attribute="opening hours", sites=["ridge"]),
    fact("hall-capacity", "The Baobab Hall seats 120 delegates theatre style.", "120 delegates",
         subject={"kind": "service", "ref": "Baobab Hall"}, fact_type="spec", attribute="capacity", value=120,
         unit="delegates", sites=["ridge"]),
]
SCOPE = {"sites": ["ridge"]}


def run(text, facts=None, day=DAY, scope=None):
    return evidence.check_text(text, facts or CAMP, day, scope or SCOPE, None)[0]


def blocking(findings):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"]]


# ===================================================================== a. a region is not a use of a site's fact

@pytest.mark.parametrize("text", [
    "After the festival, fly east to the Serengeti Basin.",
    "Tent nights in the Serengeti Basin are unforgettable.",
    "Road trips across the Serengeti Basin start in the spring.",
])
def test_region_named_as_destination_is_not_a_wrong_scope_use(text):
    assert blocking(run(text)) == [], text


@pytest.mark.parametrize("text", [
    "Amboro Plains Lodge is the base for game drives across the Serengeti Basin.",
    "Book Amboro Plains Lodge for game drives.",
    "Safari Tent Twin is $60 per room per night.",
])
def test_the_other_sites_own_fact_or_price_is_still_wrong_scope(text):
    assert any(lab == "wrong_scope" for lab, _ in blocking(run(text))), text


# ===================================================================== b. "from $X" across a room family

@pytest.mark.parametrize("text", [
    "River-view suites from $120 a night for the room, two guests, breakfast included, taxes included.",
    "Our river-view suites start at $120 per room per night for two guests, breakfast included, taxes included.",
])
def test_from_price_of_a_room_family_is_true_when_a_member_has_it(text):
    assert blocking(run(text)) == [], text


@pytest.mark.parametrize("text", [
    "River-view suites from $99 a night for the room, two guests, breakfast included, taxes included.",
    "Suite (River View) is $130 per room per night for two guests, breakfast included, taxes included.",
])
def test_a_price_no_member_has_is_still_a_conflict(text):
    assert any(lab in ("conflict_or_expired", "no_source") for lab, _ in blocking(run(text))), text


# ===================================================================== c. a name said the other way round

@pytest.mark.parametrize("text", [
    "Hill-view Family Room: $210 a night for the room, breakfast free, taxes already in the price.",
    "Book the Hill View Family Room at $210 per room per night, breakfast included, taxes included.",
])
def test_inverted_room_name_is_the_rooms_own(text):
    assert blocking(run(text)) == [], text


def test_a_room_with_a_word_no_room_has_is_still_invented():
    assert ("no_source", None) in blocking(run("Book the Treehouse Family Room for the holidays."))


# ===================================================================== d. claims no fact backs

@pytest.mark.parametrize("text", [
    "Our all-inclusive bush holiday covers everything.",
    "Choose the all inclusive delegate package.",
    "A UNESCO-listed camp above the river.",
    "Stay at our World Heritage camp.",
    "The only camp in the valley with a hot shower.",
    "We are the only lodge in Tanzania with a rooftop bar.",
    "The Baobab Hall seats 300 delegates.",
    "The garden marquee holds 250 guests.",
])
def test_unbacked_claims_are_no_source(text):
    assert ("no_source", None) in blocking(run(text)), text


@pytest.mark.parametrize("text", [
    "Only 10 minutes from the river.",
    "Not an all-inclusive package: drinks are extra.",
    "The Baobab Hall seats 120 delegates.",
    "A small group of 8 guests fits around the table.",          # a table, not a venue, and under ten
    "It is the only way in, so book early.",
    "The camp is all-inclusive of taxes.",
])
def test_similar_wording_without_an_unbacked_claim_is_fine(text):
    assert ("no_source", None) not in blocking(run(text)), text


def test_a_fact_that_says_it_backs_the_claim():
    facts = CAMP + [
        fact("pkg", "The all-inclusive Bush Week package covers meals, drinks and drives.", "all-inclusive",
             subject={"kind": "package", "ref": "Bush Week"}, sites=["ridge"]),
        fact("heritage", "The river gorge is a UNESCO World Heritage site.", "UNESCO World Heritage",
             subject={"kind": "site", "ref": "River Gorge"}, sites=["ridge"]),
        fact("unique", "Kibo Ridge Camp is the only lodge in the valley with its own observatory.", "only lodge in the valley",
             subject={"kind": "site", "ref": "Kibo Ridge Camp"}, sites=["ridge"]),
    ]
    for text in ("Try the all-inclusive Bush Week.", "Visit the UNESCO gorge.", "The only lodge in the valley."):
        assert ("no_source", None) not in blocking(run(text, facts)), text


# ===================================================================== e. opening hours said differently

@pytest.mark.parametrize("text", [
    "Unwind at the Sunset Spa, open 24 hours.",
    "The Sunset Spa is open until midnight.",
    "Sunset Spa: open round the clock.",
    "The steam room and sauna are open 24/7.",
])
def test_hours_claim_against_the_services_hours_fact_is_a_conflict(text):
    assert ("conflict_or_expired", "sunset-spa-hours") in blocking(run(text)), text


@pytest.mark.parametrize("text", [
    "Reception is open 24 hours.",                               # the fact says so
    "The Sunset Spa is open from 9am to 8pm, every day.",
    "The bar is open until midnight.",                           # no fact about a bar: not this rule
    "We open at nine and the trail is 24 hours of walking.",
])
def test_hours_that_match_or_belong_to_no_fact_are_not_a_hours_conflict(text):
    assert not any(k == "sunset-spa-hours" for _, k in blocking(run(text))), text


# ===================================================================== f. breakfast the room's fact does not give

@pytest.mark.parametrize("text", [
    "The Loft Room (Hill View) comes with a free breakfast.",
    "Loft Room (Hill View) includes breakfast for two.",
])
def test_breakfast_claim_against_a_room_that_says_not_confirmed_is_a_conflict(text):
    out = blocking(run(text))
    assert ("conflict_or_expired", "loft-2") in out, text
    assert ("no_source", None) in out, text           # nothing states it either: both are reported


@pytest.mark.parametrize("text", [
    "The Standard Room (Hill View) comes with a free breakfast.",
    "Loft Room (Hill View): $95 per room per night, breakfast not confirmed, taxes included.",
    "There is no breakfast with the Loft Room (Hill View).",
])
def test_breakfast_that_is_given_or_denied_is_fine(text):
    assert ("conflict_or_expired", "loft-2") not in blocking(run(text)), text


# ===================================================================== g. one price, one guest or two

def test_unstated_occupancy_reads_as_the_two_guest_rate():
    scope = {"sites": ["ridge"]}
    out = run("Safari Tent Twin at $60 a night.", scope={"sites": ["plains"]})
    assert ("missing_disclosure", "tent-twin-2") in blocking(out)
    out = run("Safari Tent Twin: $60 per room per night", scope=SCOPE)
    assert ("wrong_scope", "tent-twin-2") in blocking(out)


def test_stated_occupancy_still_picks_its_own_fact():
    out = run("Safari Tent Twin at $60 a night for one guest.", scope={"sites": ["plains"]})
    assert ("missing_disclosure", "tent-twin-1") in blocking(out)
