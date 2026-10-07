"""Real-data round (a hotel with many room rates, internal tour-operator rates, an expired safari-style
price): eight clusters fixed IN GENERAL. Invented business and wording (Driftwood Cove Lodge), every
rule with negative controls.

a. "Dear partner," / "Hi partners!" is an address, not a partnership claim ("official Google Partner" is);
b. a subject / heading line that only names a product does not use its expired price fact;
c. disclosures in other words: "$120 a night for the room" = "per room per night", "breakfast on us" =
   "breakfast included" (an amount alone, or "per room" alone, is not);
d. a product name the business does not have ("Mango Loft Room"): same head noun as its products, a
   word none of them uses; real names, near names, generic words and Title Case headings are fine;
e. breakfast said of a room whose facts do not give it while the other rooms' do;
f. invented third-party awards (Travellers' Choice, Booking.com review award, Michelin, Condé Nast);
g. an explicit occupancy ("for one guest") beats the word Single in a room's name;
h. "our net rate for the X" is X's internal rate: typed out it is slot_blocked.
"""
from datetime import date

import pytest

from app import evidence

from .data import fact

DAY = date(2026, 11, 10)
DISC = ["per room per night", "breakfast included"]


def rate(key, ref, val, guests, kind="variant", tour=False, breakfast=True, end=None):
    return fact(key, f"{ref}: ${val} per room per night for {guests} guest(s)"
                + (", breakfast included" if breakfast else ", breakfast not confirmed") + ".",
                f"${val} per room per night", subject={"kind": kind, "ref": ref}, fact_type="price",
                attribute="tour rate" if tour else "rack rate", value=val, unit="night", currency="USD",
                basis="per_room", valid_to=end,
                conditions=[{"key": "guests", "op": "=", "value": guests}],
                required_disclosures=[] if tour else (DISC if breakfast else DISC[:1]),
                sensitivity="internal" if tour else "public")


ROOMS = ["Garden Room", "Sea View Room", "Terrace Room", "Loft Room"]
SUITES = ["Cabana Suite", "Reef Suite", "Dune Suite", "Harbour Suite"]
HOTEL = []
for i, ref in enumerate(ROOMS + SUITES):
    slug = ref.lower().replace(" ", "-")
    base = 100 + 25 * i
    HOTEL += [rate(f"{slug}-2", ref, base, 2, breakfast=ref != "Cabana Suite"),
              rate(f"{slug}-1", ref, base - 20, 1, breakfast=ref != "Cabana Suite")]
HOTEL += [
    rate("garden-room-2-tour", "Garden Room", 84, 2, tour=True),
    fact("dive-from", "Dive packages from $200 per person.", "from $200 per person",
         subject={"kind": "offer", "ref": "Dive packages"}, fact_type="price", attribute="rate", value=200,
         currency="USD", basis="per_person", claim_class="price_reference", valid_to="2026-11-12",
         required_disclosures=["per person"]),
    fact("coves", "Driftwood Cove Lodge sits above a small cove with a reef.", None,
         subject={"kind": "site", "ref": "Driftwood Cove Lodge"}, fact_type="claim"),
    fact("farm", "The kitchen cooks with vegetables from the lodge garden.", None,
         subject={"kind": "business", "ref": "Driftwood Cove Lodge"}, fact_type="claim"),
]
PAST = DAY.replace(day=20)       # publish after dive-from ends


def run(text, facts=None, day=PAST, used=None):
    return evidence.check_text(text, facts or HOTEL, day, {}, used)[0]


def blocking(findings):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"]]


def labels(findings):
    return [f["label"] for f in findings if f["blocking"]]


# ===================================================================== a. address, not a claim

@pytest.mark.parametrize("text", [
    "Dear partner,", "Hi partners!", "Hello, partner.", "Dear valued partners,", "Hey partner, quick note.",
    "Thanks, partner!", "Partners, welcome to the cove.", "Dear Coastline Tours partner,",
    "Good morning partner,",
])
def test_partner_as_address_is_no_claim(text):
    assert blocking(run(text)) == []


@pytest.mark.parametrize("text", [
    "We are an official Google Partner.",
    "Hello from Driftwood Cove Lodge, a certified Google Partner.",
    "Driftwood Cove Lodge is a Gold Partner of Tideline Travel.",
    "We are a certified partner of Coastline Tours.",
])
def test_real_partner_claims_still_need_a_fact(text):
    assert any(lab == "no_source" for lab, _ in blocking(run(text))), text


# ===================================================================== b. a heading that names a product

def test_subject_line_naming_the_product_does_not_use_its_expired_price():
    assert blocking(run("Subject: Dive packages for your guests")) == []
    assert blocking(run("Headline: Dive packages at the cove")) == []


@pytest.mark.parametrize("text", [
    "Subject: Dive packages from $200 per person",           # a value
    "Subject: Dive packages on offer this winter",           # an offer word
    "Subject: Save on our dive packages",
])
def test_heading_with_an_offer_claim_is_still_checked(text):
    assert ("conflict_or_expired", "dive-from") in blocking(run(text)), text


def test_body_line_naming_the_expired_offer_is_still_checked():
    assert ("conflict_or_expired", "dive-from") in blocking(run("Dive packages from $200 per person, book now."))


def test_heading_naming_a_real_offer_is_still_checked():
    # an offer by its own name (a bundle, a saver) in a heading is an expired-offer hit, "is back" too
    bundle = fact("bundle", "The Reef Bundle: two dives and a boat trip for $90.", "two dives and a boat trip for $90",
                  subject={"kind": "offer", "ref": "Reef Bundle"}, fact_type="price", attribute="rate", value=90,
                  currency="USD", valid_to="2026-11-12")
    assert ("conflict_or_expired", "bundle") in blocking(run("Subject: The Reef Bundle is back", HOTEL + [bundle]))
    # a plain product's heading that says "back" is an offer claim too
    assert ("conflict_or_expired", "dive-from") in blocking(run("Subject: Dive packages are back"))


# ===================================================================== c. disclosures in other words

# a fact is "used without its disclosures" only when NONE of its required disclosures is in the piece, so
# each wording is tested alone (the other disclosure absent)
@pytest.mark.parametrize("text", [
    "The Garden Room is $100 a night for the room, for two people.",
    "The Garden Room: $100 a night for the whole room, for two guests.",
    "Pay $100 for the room, per night, for two people in the Garden Room.",
    "$100 per room, per night for two in the Garden Room.",
    "In the Garden Room, $100 a night you get the whole room for two.",
    "The Garden Room is $100 nightly per room for two people.",
    "The Garden Room is $100 for the whole room a night for two people.",
])
def test_room_night_said_otherwise(text):
    assert blocking(run(text)) == [], text


@pytest.mark.parametrize("text", [
    "The Garden Room is $100 for two people, breakfast on us.",
    "The Garden Room is $100 for two people, breakfast is on the house.",
    "The Garden Room is $100 for two people, with complimentary breakfast.",
    "The Garden Room is $100 for two people, breakfast thrown in.",
    "The Garden Room is $100 for two people and free breakfast.",
])
def test_breakfast_said_otherwise(text):
    assert blocking(run(text)) == [], text


@pytest.mark.parametrize("text", [
    "The Garden Room is $100 a night for two people.",                        # no room unit
    "The Garden Room is $100 per room for two people.",                       # no night
    "The Garden Room is $100 a night for two people, breakfast extra.",       # breakfast the other way
    "The Garden Room is $100 per person per night for two people.",
])
def test_partial_disclosures_still_block(text):
    assert ("missing_disclosure", "garden-room-2") in blocking(run(text)), text


def test_disclosure_equivalents_directly():
    assert evidence.disclosure_said("per room per night", "$90 a night for the whole room")
    assert evidence.disclosure_said("per room per night", "per night, per room")
    assert evidence.disclosure_said("breakfast included", "breakfast on the house")
    assert not evidence.disclosure_said("per room per night", "$90 a night")
    assert not evidence.disclosure_said("breakfast included", "breakfast is not included")


# ===================================================================== d. a product name that does not exist

@pytest.mark.parametrize("text", [
    "Book the Mango Loft Room for a weekend.",
    "Our Baobab Penthouse Suite has its own plunge pool.",
    "Stay in the Cabin VIP Suite tonight.",
    "Guests love the Lagoon Terrace Room.",
])
def test_invented_product_name_needs_a_source(text):
    f = run(text)
    assert blocking(f) and blocking(f)[0] == ("no_source", None), text


@pytest.mark.parametrize("text", [
    "Book the Sea View Room for a weekend.",
    "Our Cabana Suite is lovely.",
    "The Sea Room is a favourite.",                     # near a real name: every word is one of its words
    "Our suites all face the water.",                    # generic
    "Ask for a sea-view room when you book.",
    "Book Your Dream Suite Today",                       # Title Case heading
    "There is no Mango Loft Room here, but the Loft Room is lovely.",
    "We have a spa and a pool.",
    "Driftwood Cove Lodge has a suite for every mood.",
])
def test_real_generic_and_near_names_are_fine(text):
    assert blocking(run(text)) == [], text


def test_invented_name_needs_a_family_of_products():
    # only two priced subjects share "room": not a family, so no name is judged
    two = [f for f in HOTEL if f["subject"]["ref"] in ("Garden Room", "Loft Room") or f["subject"]["kind"] != "variant"]
    assert blocking(run("Book the Mango Loft Room for a weekend.", two)) == []


# ===================================================================== e. breakfast the facts do not give

def test_breakfast_said_of_a_room_whose_facts_do_not_give_it():
    f = run("The Cabana Suite is $200 per room per night, breakfast included.")
    assert ("no_source", None) in blocking(f)
    f = run("Enjoy free breakfast in the Cabana Suite.")
    assert ("no_source", None) in blocking(f)


@pytest.mark.parametrize("text", [
    "The Cabana Suite is $200 per room per night, breakfast not confirmed.",
    "The Cabana Suite is $200 per room per night, without breakfast.",
    "The Garden Room is $100 per room per night, breakfast included, for two guests.",
    "Breakfast is included in every Reef Suite stay.",      # the Reef Suite carries it
    "The Cabana Suite is $200 per room per night, for two guests.",
])
def test_breakfast_controls(text):
    assert ("no_source", None) not in blocking(run(text)), text


def test_breakfast_needs_other_rooms_that_carry_it():
    # nobody's facts mention breakfast: nothing to compare with
    plain = [fact(f"r{i}", f"{ref}: $90 per room per night.", "$90 per room per night",
                  subject={"kind": "variant", "ref": ref}, fact_type="price", value=90, currency="USD",
                  required_disclosures=["per room per night"])
             for i, ref in enumerate(["Garden Room", "Sea View Room", "Terrace Room", "Loft Room"])]
    assert ("no_source", None) not in blocking(run("The Loft Room is $90 per room per night, breakfast included.", plain))


# ===================================================================== f. invented third-party awards

@pytest.mark.parametrize("text", [
    "Proud winners of the TripAdvisor Travellers' Choice award.",
    "Rated a Travellers' Choice stay.",
    "Booking.com Traveller Review Award 2026 winner.",
    "Our restaurant is Michelin-starred.",
    "A Michelin Guide listed kitchen.",
    "As featured in Condé Nast Traveller.",
    "Named on the Conde Nast Gold List.",
    "Certificate of Excellence holders for five years.",
])
def test_invented_third_party_award_is_no_source(text):
    assert ("no_source", None) in blocking(run(text)), text


@pytest.mark.parametrize("text", [
    "Book direct or through Booking.com.",
    "Read our reviews on TripAdvisor.",
    "We serve Michelin tyres on the golf buggies.",
    "Follow us on Instagram.",
])
def test_mentioning_the_platform_is_not_an_award(text):
    assert blocking(run(text)) == [], text


def test_award_named_by_a_fact_is_supported():
    facts = HOTEL + [fact("ta", "Driftwood Cove Lodge won TripAdvisor Travellers' Choice 2026.", None,
                          subject={"kind": "business", "ref": "Driftwood Cove Lodge"}, fact_type="result",
                          claim_class="result")]
    assert blocking(run("A TripAdvisor Travellers' Choice winner for 2026.", facts)) == []
    # a different award is still unsourced
    assert ("no_source", None) in blocking(run("Booking.com Traveller Review Award winner.", facts))


# ===================================================================== g. explicit occupancy beats "Single"

def single_hotel():
    f = [rate("bs-1", "Bungalow Single", 83, 1), rate("bs-2", "Bungalow Single", 92, 2, end="2026-11-16"),
         rate("tw-1", "Tent Twin", 56, 1), rate("tw-2", "Tent Twin", 70, 2)]
    f += [rate(f"x{i}", ref, 120 + i, 2) for i, ref in enumerate(["Garden Room", "Sea View Room", "Loft Room", "Terrace Room"])]
    return f


def test_explicit_one_guest_cue_with_single_in_the_name():
    f = run("A Bungalow Single for one guest is $92 per room per night, breakfast included.", single_hotel())
    assert ("conflict_or_expired", "bs-1") in blocking(f)
    f = run("A Tent Twin for one guest is $70 per room per night, breakfast included.", single_hotel())
    assert ("conflict_or_expired", "tw-1") in blocking(f)


def test_single_in_the_name_is_not_a_cue_and_right_prices_pass():
    assert blocking(run("A Bungalow Single for one guest is $83 per room per night, breakfast included.",
                        single_hotel())) == []
    assert blocking(run("The Tent Twin for two guests is $70 per room per night, breakfast included.",
                        single_hotel())) == []
    # no cue said: the name alone is not an occupancy, the expired $92 is what is wrong
    f = run("The Bungalow Single is $92 per room per night, breakfast included.", single_hotel())
    assert ("conflict_or_expired", "bs-2") in blocking(f)
    assert ("conflict_or_expired", "bs-1") not in blocking(f)


# ===================================================================== h. a net rate is the internal one

def test_net_rate_typed_out_is_the_internal_rate():
    f = run("Our net rate for the Garden Room is $84.")
    assert blocking(f) == [("slot_blocked", "garden-room-2-tour")]
    f = run("Our trade rate for the Garden Room for two guests is $84 per room per night.")
    assert ("slot_blocked", "garden-room-2-tour") in blocking(f)


def test_net_rate_controls():
    # the public rate is still fine, and a slot-filled net rate may travel
    assert blocking(run("The Garden Room is $100 per room per night, breakfast included, for two guests.")) == []
    f = run("Our net rate for the Garden Room is $84 per room per night.", used=["garden-room-2-tour"])
    assert ("slot_blocked", "garden-room-2-tour") not in blocking(f)
