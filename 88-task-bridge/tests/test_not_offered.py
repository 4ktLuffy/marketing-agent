"""What the business says it does NOT offer: blocked when a chatbot offers it, and told to the chatbot."""
from datetime import date

from app import extras, pack

DAY = date(2026, 10, 14)


def fact(key, ref, text, **kw):
    return {"key": key, "subject": {"kind": "service", "ref": ref}, "fact_type": kw.pop("fact_type", "claim"), "text": text,
            "status": "active", "sensitivity": "public", "scope": {}, **kw}


NO_PICKUP = fact("no-pickup", "airport pick-up", "We do not offer airport pick-up.", fact_type="availability",
                 value=False, allowed_phrasing=["airport transfer", "shuttle"])
FACTS = [NO_PICKUP, fact("rooms", "Rooms", "Rooms are $75 per room per night, breakfast included.")]


def blocked(text):
    return [f for f in extras.check(text, FACTS, DAY, {}) if f["blocking"]]


def test_offering_what_is_not_offered_is_a_conflict():
    hit = blocked("Free airport pick-up for every guest.")
    assert hit and hit[0]["label"] == "conflict_or_expired" and hit[0]["fact_key"] == "no-pickup"
    assert blocked("Our shuttle meets every flight.")[0]["fact_key"] == "no-pickup"


def test_saying_it_is_not_offered_passes():
    assert not blocked("We don't offer airport pick-up, but taxis wait at the airport.")
    assert not blocked("Airport pick-up is not available.")


def test_a_not_offered_fact_never_supports_the_extra():
    # without the fact, the airport pick-up extra is caught; with it, it must still be caught
    assert blocked("Airport transfers are included.")


def test_pack_says_what_is_not_offered():
    assert pack.not_offered_lines([NO_PICKUP]) == ["We do NOT offer (never write or imply that we do): airport pick-up"]
    assert pack.not_offered_lines([]) == []


def test_a_variant_that_does_not_exist_is_a_conflict():
    def rate(key, ref, n):
        return {"key": key, "subject": {"kind": "variant", "ref": ref}, "fact_type": "price", "value": n, "currency": "USD",
                "status": "active", "sensitivity": "public", "scope": {}, "text": f"{ref}: ${n} per room per night."}
    facts = [rate("fam-garden", "Family Room (Garden View)", 248), rate("queen-lake", "Queen Room (Lake View)", 111),
             rate("queen-garden", "Queen Room (Garden View)", 99)]
    hit = extras.check("A Family Room (Lake View) is $248 per room per night.", facts, DAY, {})
    assert hit and hit[0]["label"] == "conflict_or_expired" and hit[0]["fact_key"] == "fam-garden"
    assert not extras.check("The Queen Room (Lake View) is $111 per room per night.", facts, DAY, {})
    assert not extras.check("Reception (Main Building) opens at 7am.", facts, DAY, {})


def test_a_product_not_carried_is_wrong_scope():
    beer = [{"key": "p-heineken", "subject": {"kind": "product", "ref": "Heineken 33cl"}, "fact_type": "price", "value": 2080,
             "status": "active", "sensitivity": "public", "scope": {}, "text": "Heineken 33cl: 2,080 birr per crate."}]
    hit = extras.unknown_products("Our Castel 33cl crates are on sale at 1,450 birr per crate.", beer)
    assert hit and hit[0]["label"] == "wrong_scope"
    assert not extras.unknown_products("Heineken 33cl is 2,080 birr per crate.", beer)
    assert not extras.unknown_products("We don't carry Castel 33cl.", beer)
    assert not extras.unknown_products("Each 33cl crate holds 24.", beer)
    assert not extras.unknown_products("Castel 33cl is popular.", FACTS)      # no sized products at all: no opinion


def test_common_words_before_a_size_are_not_brands():
    beer = [{"key": "p-heineken", "subject": {"kind": "product", "ref": "Heineken 33cl"}, "fact_type": "price", "value": 2080,
             "status": "active", "sensitivity": "public", "scope": {}, "text": "Heineken 33cl: 2,080 birr per crate."}]
    assert not extras.unknown_products("Other 33cl: Buckler 0.0% 1,535 / Sofi Malt 1,500 birr/crate.", beer)
    assert not extras.unknown_products("Premium 33cl options for your bar.", beer)
