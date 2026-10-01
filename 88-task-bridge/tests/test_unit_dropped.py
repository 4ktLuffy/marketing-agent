"""real-7: the odd line out in a price list drops the unit its neighbours say ("Walia 33cl is 1,300 birr.")."""
from datetime import date

from app import evidence as E

DAY = date(2026, 10, 12)


def price(key, ref, n):
    return {"key": key, "subject": {"kind": "product", "ref": ref}, "fact_type": "price", "value": n, "currency": "ETB",
            "unit": "crate", "basis": "per_unit", "value_text": f"{n:,} birr per crate", "status": "active",
            "sensitivity": "public", "required_disclosures": ["per crate"], "valid_from": "2026-08-17",
            "text": f"{ref}: {n:,} birr per crate.", "scope": {}}


FACTS = [price("p-heineken", "Heineken 33cl", 2080), price("p-walia", "Walia 33cl", 1300), price("p-harar", "Harar 50cl", 1590)]


def missing(text):
    fs, _ = E.check_text(text, FACTS, DAY, {})
    return sorted({f["fact_key"] for f in fs if f["label"] == "missing_disclosure"})


def test_odd_line_out_is_flagged():
    assert missing("Heineken 33cl is 2,080 birr per crate.\nWalia 33cl is 1,300 birr.\nHarar 50cl is 1,590 birr per crate.") == ["p-walia"]


def test_unit_said_another_way_passes():
    assert missing("Heineken 33cl is 2,080 birr per crate.\nWalia 33cl is 1,300 birr/crate.") == []
    assert missing("Heineken 33cl is 2,080 birr per crate.\nA crate of Walia 33cl is 1,300 birr.") == []


def test_a_general_line_covers_every_price():
    assert missing("All prices are per crate.\nHeineken 33cl is 2,080 birr per crate.\nWalia 33cl is 1,300 birr.") == []


def test_no_other_line_says_it_leaves_the_piece_rule():
    # nobody says "per crate" on a price line: the piece-wide rule decides (and flags both)
    assert missing("Heineken 33cl is 2,080 birr.\nWalia 33cl is 1,300 birr.") == ["p-heineken", "p-walia"]


def room(key, ref, n):
    return {"key": key, "subject": {"kind": "variant", "ref": ref}, "fact_type": "price", "value": n, "currency": "USD",
            "unit": "night", "basis": "per_room", "value_text": f"${n} per room per night", "status": "active",
            "sensitivity": "public", "required_disclosures": ["per room per night"],
            "text": f"{ref}: ${n} per room per night for two guests.", "scope": {}}


def test_a_room_name_does_not_say_per_room():
    facts = [room("r-twin", "Standard Twin Room (Lake View)", 75), room("r-queen", "Queen Room (Lake View)", 111)]
    fs, _ = E.check_text("The Queen Room (Lake View) is $111 per room per night.\nA Standard Twin Room (Lake View) is $75 for two guests.",
                         facts, DAY, {})
    assert {f["fact_key"] for f in fs if f["label"] == "missing_disclosure"} == {"r-twin"}
    fs, _ = E.check_text("The Queen Room (Lake View) is $111 per room per night.\nA Standard Twin Room (Lake View) is $75 a night per room.",
                         facts, DAY, {})
    assert not [f for f in fs if f["label"] == "missing_disclosure"]


def test_a_list_of_room_names_sharing_a_price():
    facts = [room("r-std-king", "Standard King Room (Lake View)", 75), room("r-std-twin", "Standard Twin Room (Lake View)", 75),
             room("r-twin", "Twin Room (Lake View)", 131)]
    fs, _ = E.check_text("Standard King or Twin Room (Lake View): $75 per room per night.", facts, DAY, {})
    assert not [f for f in fs if f.get("blocking")], fs
    fs, _ = E.check_text("Twin Room (Lake View): $75 per room per night.", facts, DAY, {})
    assert [f for f in fs if f["label"] == "conflict_or_expired"]


def test_room_lists_as_chatbots_write_them():
    facts = [room("r-std-king", "Standard King Room (Lake View)", 75), room("r-std-twin", "Standard Twin Room (Lake View)", 75),
             room("r-twin", "Twin Room (Lake View)", 131), room("r-qg", "Queen Room (Garden View)", 99)]
    for t in ["Standard King or Twin (Lake View), $75 per room per night for two guests.",
              "Rates: Queen Room (Garden View) $99 per room per night for two guests; Standard King Room (Lake View) $75 per room per night for two guests."]:
        fs, _ = E.check_text(t, facts, DAY, {})
        assert not [f for f in fs if f.get("blocking")], (t, fs)


def test_slash_unit_counts_as_the_disclosure():
    sms = "Heineken 33cl 2,080 birr/crate. Harar 50cl 1,590 birr/crate. Walia 33cl 1,300 birr."
    assert missing(sms) == ["p-walia"]
    assert missing("Heineken 33cl 2,080 birr/crate. Harar 50cl 1,590 birr/crate.") == []


def test_breakfast_promised_for_every_room():
    pres = {**room("r-pres", "Presidential Suite", 480), "text": "Presidential Suite: $480 per room per night, breakfast not confirmed."}
    std = {**room("r-std", "Standard King Room (Lake View)", 75), "text": "Standard King Room (Lake View): $75 per room per night, breakfast included."}
    for t in ["Breakfast is in every rate.", "Our rooms feature lake views and include breakfast."]:
        fs, _ = E.check_text(t, [pres, std], DAY, {})
        assert [f for f in fs if f["label"] == "conflict_or_expired" and f["fact_key"] == "r-pres"], t
    fs, _ = E.check_text("Breakfast is in every rate.", [std], DAY, {})
    assert not [f for f in fs if f.get("blocking")]


def test_slash_separated_items_keep_their_own_prices():
    def crate(key, ref, n):
        return {"key": key, "subject": {"kind": "product", "ref": ref}, "fact_type": "price", "value": n, "currency": "ETB",
                "unit": "crate", "value_text": f"{n:,} birr per crate", "status": "active", "sensitivity": "public",
                "text": f"{ref}: {n:,} birr per crate.", "scope": {}}
    facts = [crate("b", "Buckler 0.0% 33cl", 1535), crate("s", "Sofi Malt 33cl", 1500)]
    fs, _ = E.check_text("Buckler 0.0% 33cl 1,535 / Sofi Malt 1,500 birr/crate.", facts, DAY, {})
    assert not [f for f in fs if f["label"] == "conflict_or_expired"], fs
