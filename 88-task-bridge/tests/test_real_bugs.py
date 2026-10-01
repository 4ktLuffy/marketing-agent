"""Three checker bugs found on real business data (a hotel with ~35 public room rates and ~35 internal
tour-operator rates; a beer distributor with an internal sales volume). Every rule has negative controls.

1. a price said next to a subject's name is that subject's price: "Our Twin Room (Lake View) is just $99
   for two" is a wrong price when only another room costs $99. Occupancy ("for two" / "single") is
   respected, the most specific name wins, each name owns the price next to it, and a from-price that
   is the lowest of all rooms ("rooms from $71") stays fine;
2. an internal (or restricted) value written out ("special rate of $95", "38,450 crates") is blocked
   as slot_blocked: it may only travel as a [[slot]]; a public fact with the same value makes it
   ambiguous (review, not a block); a slot filled in the same sentence is fine;
3. "Best eco-lodge in Ethiopia, voted by our guests!" is an invented ranking / vote with no fact.
"""
from datetime import date

import pytest

from app import evidence

from .data import fact

DAY = date(2026, 10, 6)


def room(key, ref, val, guests, tour=False):
    return fact(key, f"{ref}: ${val} per room per night for {guests} guest(s).", f"${val} per room per night",
                subject={"kind": "variant", "ref": ref}, fact_type="price", attribute="room rate", value=val,
                unit="night", currency="USD", basis="per_room",
                conditions=[{"key": "guests", "op": "=", "value": guests}]
                + ([{"key": "segment", "op": "=", "value": "tour operators"}] if tour else []),
                sensitivity="internal" if tour else "public")


HOTEL = [
    room("qg2", "Queen Room (Garden View)", 99, 2), room("qg1", "Queen Room (Garden View)", 71, 1),
    room("tl2", "Twin Room (Lake View)", 131, 2), room("tl1", "Twin Room (Lake View)", 104, 1),
    room("sq2", "Standard Queen Room (Lake View)", 120, 2), room("q2", "Queen Room", 75, 2),
    room("ql2", "Queen Room (Lake View)", 125, 2),
    room("t-ql2", "Queen Room (Lake View)", 95, 2, tour=True),       # internal tour-operator rate
    room("t-tl2", "Twin Room (Lake View)", 111, 2, tour=True),
]


def run(text, facts=None, slot_used=None):
    return evidence.check_text(text, facts or HOTEL, DAY, {}, slot_used)[0]


def blocking(findings):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"]]


def labels(findings):
    return [(f["label"], f["fact_key"]) for f in findings]


# ================================================================ 1. the price of the room that is named

def test_other_rooms_price_is_a_wrong_price():
    f = run("Our Twin Room (Lake View) is just $99 for two.")
    assert blocking(f) == [("conflict_or_expired", "tl2")]
    assert "Twin Room (Lake View) is $131 per room per night for two guests" in f[0]["detail"]


def test_occupancy_is_respected():
    # $104 is the Twin Lake View single rate: right for one, wrong for two
    assert blocking(run("Our Twin Room (Lake View) is $104 for one guest.")) == []
    f = run("Our Twin Room (Lake View) is $104 for two.")
    assert blocking(f) == [("conflict_or_expired", "tl2")] and "$131" in f[0]["detail"]
    f = run("Single travellers pay $131 in the Twin Room (Lake View).")
    assert blocking(f) == [("conflict_or_expired", "tl1")] and "$104" in f[0]["detail"]


def test_correct_price_for_the_named_room_matches():
    assert labels(run("Our Twin Room (Lake View) is $131 for two.")) == [("match", "tl2")]
    assert labels(run("Queen Room (Garden View): $99 for two guests.")) == [("match", "qg2")]
    assert labels(run("Queen Room (Garden View): $71 for one.")) == [("match", "qg1")]


def test_near_exact_name_in_another_order():
    f = run("Our Lake View Twin Room is just $99 for two.")
    assert blocking(f) == [("conflict_or_expired", "tl2")]


def test_two_subjects_tie_each_value_to_the_nearest_name():
    assert blocking(run("Queen Room (Garden View) is $99 and Twin Room (Lake View) is $131, both for two.")) == []
    f = run("Queen Room (Garden View) is $131 and Twin Room (Lake View) is $99, both for two.")
    assert sorted(blocking(f)) == [("conflict_or_expired", "qg2"), ("conflict_or_expired", "tl2")]


def test_the_most_specific_name_wins():
    # "Queen Room" is $75; "Standard Queen Room (Lake View)" is $120: $75 is not the standard queen's price
    f = run("Standard Queen Room (Lake View) is $75 for two.")
    assert blocking(f) == [("conflict_or_expired", "sq2")] and "$120" in f[0]["detail"]
    assert labels(run("Standard Queen Room (Lake View) is $120 for two.")) == [("match", "sq2")]
    assert labels(run("Queen Room is $75 for two.")) == [("match", "q2")]


def test_no_subject_named_is_not_checked():
    assert blocking(run("Rooms are $99 for two.")) == []
    assert blocking(run("Book a room at $131 a night.")) == []


def test_from_price_across_rooms_stays_fine():
    f = run("Rooms from $71 a night, with lake and garden views.")
    assert blocking(f) == [] and labels(f) == [("match", "qg1")]
    f = run("Twin Room (Lake View) sleeps two, and our rooms start from $71.")
    assert blocking(f) == []


def test_a_price_in_a_sentence_of_unpriced_words_is_unchanged():
    # another item's price next to a name that is not a room ("Puppy Pamper £45" style): not tied
    f = run("Garden View rooms are lovely. The Queen Room (Garden View) is $99 for two and breakfast is extra.")
    assert blocking(f) == []


# ================================================================ 2. internal values written out

def test_internal_rate_written_out_is_blocked():
    f = run("Tour operators: ask for our special rate of $95.")
    assert blocking(f) == [("slot_blocked", "t-ql2")]
    assert f[0]["detail"] == "internal value written out: t-ql2 must stay a [[slot]]"


def test_internal_rate_of_the_named_room_is_blocked():
    f = run("Twin Room (Lake View) for tour operators is $111 for two.")
    assert blocking(f) == [("slot_blocked", "t-tl2")]


def test_internal_value_the_same_as_a_public_one_is_review():
    facts = HOTEL + [room("t-qg2", "Queen Room (Garden View)", 99, 2, tour=True)]
    f = run("Tour operators: ask for our special rate of $99.", facts)
    assert blocking(f) == [] and [x["label"] for x in f] == ["review"] and "internal fact" in f[0]["detail"]
    # the public room named with its own price is a plain match
    assert labels(run("Queen Room (Garden View) is $99 for two.", facts)) == [("match", "qg2")]


def test_a_slot_filled_internal_value_is_fine():
    text = "Tour operators: ask for our special rate of $95 per room per night."
    assert blocking(run(text, slot_used=["t-ql2"])) == []
    assert blocking(run(text)) == [("slot_blocked", "t-ql2")]


BEER = [
    fact("best-seller-volume", "Harar 33cl: 38,450 crates sold July to September 2026 (internal).", "38,450 crates",
         subject={"kind": "product", "ref": "Harar 33cl"}, fact_type="result", value=38450, unit="crates",
         sensitivity="internal"),
    fact("crate-price", "A crate of Harar 33cl is 480 birr.", "480 birr", subject={"kind": "product", "ref": "Harar 33cl"},
         fact_type="price", value=480, unit="birr"),
    fact("secret-margin", "Our margin on Harar is 31 points.", "31 points", fact_type="result", value=31, unit="points",
         sensitivity="restricted"),
]


@pytest.mark.parametrize("text", [
    "We sold 38,450 crates of Harar this quarter",
    "We sold 38450 crates of Harar this quarter.",
    "Over 38,450 pints of Harar poured.",
])
def test_internal_quantity_written_out_is_blocked(text):
    f = run(text, BEER)
    assert blocking(f) == [("slot_blocked", "best-seller-volume")]
    assert "must stay a [[slot]]" in f[0]["detail"]


def test_restricted_quantity_written_out_is_blocked():
    f = run("Our margin on Harar is 31 points.", BEER)
    assert blocking(f) == [("slot_blocked", "secret-margin")]


def test_quantity_controls():
    assert blocking(run("We sold 12 crates of Harar this week.", BEER)) == []
    assert blocking(run("Since 2026 we sold Harar across Addis.", BEER)) == []
    # a public fact has the same number: not secret
    beer = BEER + [fact("pub-crates", "Every pallet holds 38,450 crates? No: the depot stores 38,450 crates.",
                        "38,450 crates", subject={"kind": "site", "ref": "Depot"}, fact_type="spec", value=38450,
                        unit="crates")]
    assert blocking(run("We sold 38,450 crates of Harar this quarter", beer)) == []
    # the slot is filled here
    assert blocking(run("We sold 38,450 crates of Harar this quarter", BEER, ["best-seller-volume"])) == []


# ================================================================ 3. invented superlatives / awards / votes

@pytest.mark.parametrize("text", [
    "Best eco-lodge in Ethiopia, voted by our guests!",
    "Best eco-lodge in Ethiopia.",
    "Top safari camp in Kenya.",
    "Voted by our guests, we are a lake favourite.",
    "Voted by over 2,000 travellers.",
    "Our award-winning lodge sits on the lake.",
    "Number 1 lodge in Gondar.",
])
def test_invented_ranking_needs_a_fact(text):
    f = run(text)
    assert any(x["label"] == "no_source" and x["blocking"] for x in f), f


def test_best_eco_lodge_gets_both_claims():
    f = run("Best eco-lodge in Ethiopia, voted by our guests!")
    assert len([x for x in f if x["label"] == "no_source"]) == 2


@pytest.mark.parametrize("text", [
    "Best-kept secret on the lake.",
    "Our best rooms face the lake.",
    "Our best rooms in the lodge face the lake.",
    "Best time to visit is October.",
    "Best wishes from the team at the lodge.",
    "Best of luck with your trip.",
    "We voted to add a second boat.",
    "The committee voted in a new manager.",
    "Top up your room credit at the desk.",
])
def test_ordinary_best_top_voted_is_not_a_claim(text):
    assert blocking(run(text)) == [], run(text)


def test_a_fact_that_says_it_supports_it():
    facts = HOTEL + [fact("vote", "Voted best eco-lodge in Ethiopia by guests in the 2026 Travel Poll.",
                          "Voted best eco-lodge in Ethiopia", fact_type="result", claim_class="result",
                          allowed_phrasing=["Best eco-lodge in Ethiopia"])]
    f = run("Best eco-lodge in Ethiopia.", facts)
    assert blocking(f) == []


def test_business_health_words_and_place_highlights():
    from app import evidence as E
    assert E._business_sense("At 1,700 birr per crate it still moves fast and keeps margins healthy.", "healthy")
    assert not E._business_sense("Sofi Malt is a healthy drink for your family.", "healthy")
    import re
    best = [rx for name, rx in E.CLAIM_PATTERNS if name == "best"][0] if hasattr(E, "CLAIM_PATTERNS") else None
    if best:
        assert not re.search(best, "Our packages hit the best of Arba Minch.")
