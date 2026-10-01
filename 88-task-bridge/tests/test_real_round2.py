"""Real-data round 2 (a beer distributor's crates, a dairy here): clusters fixed IN GENERAL. Invented
business and wording (Lakeside Dairy Cooperative, Bahir Dar), every rule with negative controls.

a. a product named with a size / variant it does not come in ("Meadow Milk 2L" when it is sold in 1L);
b. a pack count that contradicts the spec fact ("a 500g tray holds 10 pots", "(10 x 1kg)");
c. a service / delivery claim for a place no fact names (scope is one site);
d. a delivery-time promise ("delivery in 45 minutes") when no fact mentions delivery;
e. a line above a list that says the unit of its prices ("Tray prices this week:") discloses it for the lines;
f. Amharic money: ብር is birr, ዶላር is dollars, the number before or after.
"""
from datetime import date

import pytest

from app import evidence

from .data import fact

DAY = date(2026, 11, 10)


def price(key, ref, val, status="active", sens="public", end=None, disc=True):
    return fact(key, f"{ref}: {val} birr per tray" + (", old price." if status == "expired" else "."),
                f"{val} birr per tray", subject={"kind": "product", "ref": ref}, fact_type="price",
                attribute="price", value=val, unit="tray", currency="ETB", basis="per_unit", sites=["bahirdar"],
                status=status, valid_to=end, sensitivity=sens, required_disclosures=["per tray"] if disc else [])


DAIRY = [
    fact("coop", "Lakeside Dairy Cooperative supplies yoghurt, milk and honey to shops in Bahir Dar.",
         subject={"kind": "business", "ref": "Lakeside Dairy Cooperative"}, sites=["bahirdar"]),
    price("yog-500", "Sunrise Yoghurt 500g", 60),
    price("yog-1k", "Sunrise Yoghurt 1kg", 110),
    price("yog-500-old", "Sunrise Yoghurt 500g", 55, status="expired", end="2026-09-30"),
    price("milk-1l", "Meadow Milk 1L", 45),
    price("honey-250", "Dessie Honey 250g", 180),
    price("yog-500-trade", "Sunrise Yoghurt 500g", 40, sens="internal"),
    fact("tray-500", "A 500g tray holds 12 pots.", "12 pots", subject={"kind": "product", "ref": "500g tray"},
         fact_type="spec", attribute="pots_per_tray", value=12, unit="pots", sites=["bahirdar"]),
    fact("tray-1k", "A 1kg tray holds 6 pots.", "6 pots", subject={"kind": "product", "ref": "1kg tray"},
         fact_type="spec", attribute="pots_per_tray", value=6, unit="pots", sites=["bahirdar"]),
]
SCOPE = {"sites": ["bahirdar"]}


def run(text, facts=None, day=DAY):
    return evidence.check_text(text, facts or DAIRY, day, SCOPE, None)[0]


def blocking(findings):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"]]


def labels(findings):
    return [f["label"] for f in findings if f["blocking"]]


# ===================================================================== a. a size the product does not come in

@pytest.mark.parametrize("text", [
    "Meadow Milk 2L is now on the shelf.",
    "Stock up on Sunrise Yoghurt 250g this week.",
    "We also pack Dessie Honey 500g for bigger shops.",
    "New: Meadow Milk 500ml.",
    "Try the Sunrise Yoghurt 6-pack.",
])
def test_size_the_product_does_not_come_in_is_wrong_scope(text):
    assert ("wrong_scope", None) in blocking(run(text)), text


@pytest.mark.parametrize("text", [
    "Sunrise Yoghurt 500g is on the shelf.",
    "Sunrise Yoghurt 1kg is on the shelf.",
    "Meadow Milk 1 L is on the shelf.",
    "Meadow Milk 1000ml is on the shelf.",
    "Dessie Honey 250g makes a good gift.",
    "Sunrise Yoghurt comes in 500g and 1kg.",                   # sizes not attached to the product
    "We keep Dessie Honey, 250g jars and all.",
    "Shops like 500g pots for the counter.",                    # a size with no product
    "There is no Meadow Milk 2L, only the 1L bottle.",          # negated
])
def test_existing_or_unattached_sizes_are_fine(text):
    assert blocking(run(text)) == [], text


def test_detail_names_the_sizes_that_exist():
    out = [f for f in run("Meadow Milk 2L is now on the shelf.") if f["label"] == "wrong_scope"]
    assert out and "1L" in out[0]["detail"]


# ===================================================================== b. pack count against the spec

@pytest.mark.parametrize("text", [
    "A 500g tray holds 10 pots.",
    "Each 1kg tray has 12 pots.",
    "Our trays hold 6 pots of 500g.",
    "Order the 1kg tray of 8 pots.",
    "Shelf-ready: 10 x 500g pots.",
])
def test_pack_count_against_the_spec_is_a_conflict(text):
    out = blocking(run(text))
    assert any(lab == "conflict_or_expired" and key in ("tray-500", "tray-1k") for lab, key in out), text


@pytest.mark.parametrize("text", [
    "A 500g tray holds 12 pots.",
    "Each 1kg tray holds 6 pots, and a 500g tray holds 12 pots.",
    "Shelf-ready: 12 x 500g pots.",
    "Order 10 pots of 500g for your counter.",                  # no tray said: an order, not the pack
    "Our trays hold pots.",
])
def test_pack_count_that_fits_the_spec_is_fine(text):
    assert blocking(run(text)) == [], text


# ===================================================================== c. a place no fact names

@pytest.mark.parametrize("text", [
    "We supply shops in Jimma and Hawassa.",
    "Now we deliver to Debre Tabor as well.",
    "Our yoghurt is available across Sekota and Lalibela.",
    "Delivering in Bahir Dar, Gondar and Dessie every week.",
    "Bars in Adama can order from us now.",
])
def test_service_claim_for_an_unnamed_place_is_wrong_scope(text):
    assert ("wrong_scope", None) in blocking(run(text)), text


@pytest.mark.parametrize("text", [
    "We supply shops in Bahir Dar.",
    "We deliver to Bahir Dar every morning.",
    "Lakeside Dairy Cooperative supplies Dessie Honey to shops.",     # a place word that is a product
    "Dessie Honey is back on the shelf in Bahir Dar.",
    "We do not deliver to Gondar yet.",                               # negated
    "Do you deliver to Jimma?",                                       # a question
    "Welcome, Hawassa readers, thanks for following.",                # no service verb
    "Abebe Kebede opened the first shop in Bahir Dar.",               # a person
])
def test_served_place_product_name_or_no_service_claim_is_fine(text):
    assert blocking(run(text)) == [], text


def test_places_are_no_question_when_no_fact_is_scoped():
    plain = [fact("milk", "Meadow Milk is sold in 1L bottles.", None,
                  subject={"kind": "product", "ref": "Meadow Milk"})]
    assert blocking(run("We deliver to Jimma and Hawassa.", plain)) == []


# ===================================================================== d. delivery-time promise

@pytest.mark.parametrize("text", [
    "Fresh yoghurt, delivery in 45 minutes.",
    "Your order is delivered within 2 hours.",
    "A 30-minute delivery promise for every shop.",
    "We deliver in under an hour.",
])
def test_delivery_time_promise_without_a_fact_is_no_source(text):
    assert ("no_source", None) in blocking(run(text)), text


@pytest.mark.parametrize("text", [
    "Open from 7am every day.",
    "Reply within 30 minutes and we confirm.",                      # not a delivery time
    "The yoghurt keeps for 30 days in the fridge.",
])
def test_other_times_are_no_delivery_promise(text):
    assert "no_source" not in labels(run(text)), text


def test_a_fact_about_delivery_decides_instead():
    facts = DAIRY + [fact("deliv", "We deliver to shops in Bahir Dar on weekday mornings.", None,
                          subject={"kind": "service", "ref": "Delivery"}, sites=["bahirdar"])]
    assert "no_source" not in labels(run("Delivery in 2 hours for shops in Bahir Dar.", facts))


# ===================================================================== e. a heading that says the unit

LIST = "\n• Sunrise Yoghurt 500g: 60 birr\n• Sunrise Yoghurt 1kg: 110 birr\nMessage us to order."


@pytest.mark.parametrize("head", ["Tray prices this week:", "Hello shops, the new tray prices are here.",
                                  "Prices per tray:", "Our prices are per tray:"])
def test_list_under_a_unit_heading_is_disclosed(head):
    assert "missing_disclosure" not in labels(run(head + LIST)), head


@pytest.mark.parametrize("head", ["Prices this week:", "Hello shops, new prices are here.", "Crate prices this week:"])
def test_list_without_the_unit_heading_still_needs_it(head):
    assert "missing_disclosure" in labels(run(head + LIST)), head


def test_a_price_outside_a_list_under_a_heading_still_needs_it():
    assert "missing_disclosure" in labels(run("Tray prices this week:\nSunrise Yoghurt 500g is 60 birr today."))


# ===================================================================== f. Amharic money

def test_amharic_price_matches_before_or_after_the_word():
    for text in ("Sunrise Yoghurt 500g: 60 ብር per tray", "Sunrise Yoghurt 500g: ብር 60 per tray",
                 "Sunrise Yoghurt 500g በ60 ብር per tray"):
        out = run(text)
        assert ("match", "yog-500") in [(f["label"], f["fact_key"]) for f in out], text
        assert blocking(out) == [], text


def test_amharic_old_price_is_expired():
    assert ("conflict_or_expired", "yog-500-old") in blocking(run("Sunrise Yoghurt 500g: 55 ብር per tray"))


def test_amharic_internal_price_is_slot_blocked():
    assert any(lab == "slot_blocked" for lab, _ in blocking(run("Sunrise Yoghurt 500g: 40 ብር per tray"))), "internal"


def test_amharic_dollars_read_as_usd():
    vals = evidence.extract("ዶላር 25 and 30 ዶላር")
    assert [v.key for v in vals] == [("money", "25", "USD"), ("money", "30", "USD")]
    assert [v.key for v in evidence.extract("1,700 ብር 2,080 ብር")] == [("money", "1700", "ETB"), ("money", "2080", "ETB")]


@pytest.mark.parametrize("text", ["ብር", "ብር ብር ነው", "ዶላር በብዛት", "ብር 2026 ዓ.ም", "ስለ ወተት እና እርጎ ነው።"])
def test_ethiopic_text_without_a_price_does_not_crash(text):
    run(text)


def test_nothing_changes_without_ethiopic_script():
    assert [v.key for v in evidence.extract("60 birr, USD 5 and £3")] == [
        ("money", "60", "ETB"), ("money", "5", "USD"), ("money", "3", "GBP")]
