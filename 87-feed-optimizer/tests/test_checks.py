"""The checks that decide whether a proposed title or description is kept. Negative controls:
each kind of invented fact must be rejected; a correct rewrite of the row's own words must pass."""
import pytest

from app.checks import Context, check, front_coverage, rule_title

SHIRT = {"id": "S1", "title": "Oxford shirt", "description": "Button-down collar shirt with a chest pocket.",
         "brand": "Harbour Lane", "color": "Navy", "size": "M", "material": "Cotton", "gender": "male",
         "age_group": "adult", "product_type": "Apparel > Shirts", "price": "49.00 GBP", "gtin": "5012345678900"}
MUG = {"id": "M1", "title": "Mug", "description": "Hand-thrown mug, holds 300 ml.", "brand": "Kiln & Co",
       "color": "", "size": "", "material": "Stoneware", "product_type": "Home > Kitchen > Mugs"}
BRANDS = {"Harbour Lane", "Kiln & Co", "Brightwave"}


def codes(text, row=SHIRT, kind="title"):
    return {r["code"] for r in check(text, Context(row, BRANDS), kind)}


def test_correct_reordering_of_existing_attributes_is_kept():
    assert codes("Harbour Lane Men's Oxford Shirt - Navy, Size M, Cotton") == set()
    assert codes("Harbour Lane Cotton Oxford Shirt, Navy, M") == set()
    assert codes("Harbour Lane Button-Down Oxford Shirt in Navy Cotton") == set()


@pytest.mark.parametrize("title", [
    "Harbour Lane Men's Oxford Shirt - Blue, Size M",           # colour not in the row
    "Harbour Lane Men's Oxford Shirt - Navy, Linen",            # material not in the row
    "Harbour Lane Men's Oxford Shirt - Navy, Size L",           # size not in the row
    "Harbour Lane Men's Oxford Shirt - Navy, Large",
    "Harbour Lane Women's Oxford Shirt - Navy",                 # gender not in the row
])
def test_invented_attribute_is_rejected(title):
    assert codes(title) & {"colour", "material", "size", "gender"}


@pytest.mark.parametrize("title", ["Harbour Lane Oxford Shirt 2-Pack - Navy", "Harbour Lane 100% Cotton Oxford Shirt",
                                   "Harbour Lane Oxford Shirt, Two Pack", "Harbour Lane Oxford Shirt Size 42"])
def test_number_not_in_row_is_rejected(title):
    assert "number" in codes(title)


def test_number_in_row_is_kept():
    assert "number" not in codes("Kiln & Co Stoneware Mug, 300 ml", MUG)
    assert "number" not in codes("Kiln & Co Stoneware Mug 300ml", MUG)


def test_price_gtin_numbers_do_not_count_as_evidence():
    assert "number" in codes("Harbour Lane Oxford Shirt 49.00")
    assert codes("Harbour Lane Oxford Shirt £49") & {"price", "number"}


@pytest.mark.parametrize("title", ["Harbour Lane Oxford Shirt - Free Shipping", "Best Harbour Lane Oxford Shirt",
                                   "Harbour Lane Oxford Shirt SALE", "Harbour Lane Oxford Shirt - Buy Now",
                                   "Harbour Lane Oxford Shirt 20% off"])
def test_promo_phrase_is_rejected(title):
    assert "promo" in codes(title)


def test_other_products_brand_is_rejected():
    assert "brand_other" in codes("Brightwave Oxford Shirt - Navy")
    assert "brand_other" in codes("Kiln & Co Oxford Shirt - Navy")
    assert "brand_other" in codes("Harbour Lane Oxford Shirt for iPhone")


def test_all_caps_symbols_and_length():
    assert "all_caps" in codes("HARBOUR LANE OXFORD SHIRT NAVY")
    assert "all_caps" in codes("Harbour Lane OXFORD Shirt")
    assert "symbols" in codes("Harbour Lane Oxford Shirt!")
    assert "too_long" in codes("Harbour Lane Oxford Shirt " + "navy " * 30)
    assert "empty" in codes("  ")


def test_claims_need_the_row():
    assert "claim" in codes("Harbour Lane Organic Cotton Oxford Shirt")
    assert "claim" in codes("Kiln & Co Microwave Safe Stoneware Mug", MUG)
    # The row says it: kept.
    row = MUG | {"description": "Hand-thrown mug. Dishwasher and microwave safe."}
    assert "claim" not in codes("Kiln & Co Stoneware Mug, Microwave Safe", row)


def test_brand_words_are_not_attribute_claims():
    row = MUG | {"brand": "Black Oak Pottery"}
    assert codes("Black Oak Pottery Stoneware Mug", row) == set()


def test_structured_field_beats_the_original_title():
    row = SHIRT | {"title": "Oxford Shirt Blue"}           # misleading original: color says Navy
    assert "colour" in codes("Harbour Lane Oxford Shirt - Blue", row)
    title, warnings = rule_title(row)
    assert "Blue" not in title and "Navy" in title and warnings


def test_without_the_field_the_row_text_counts():
    row = MUG | {"description": "A white stoneware mug."}
    assert "colour" not in codes("Kiln & Co White Stoneware Mug", row)
    assert "colour" in codes("Kiln & Co Blue Stoneware Mug", row)


def test_brand_missing_and_unrelated():
    assert "brand_missing" in codes("Men's Oxford Shirt - Navy")
    assert "unrelated" in codes("Harbour Lane Travel Mug - Navy")


def test_description_rules():
    ok = "Harbour Lane oxford shirt in navy cotton with a button-down collar and a chest pocket."
    assert codes(ok, kind="description") == set()
    assert "html" in codes("<p>Harbour Lane shirt</p>", kind="description")
    assert "url" in codes("See https://example.com", kind="description")
    assert "too_long" in codes("cotton " * 800, kind="description")
    assert "number" in codes("Our shirt has 4 buttons.", kind="description")


def test_rule_title_uses_only_row_words_and_passes_the_check():
    title, warnings = rule_title(SHIRT)
    assert title == "Harbour Lane Men's Oxford shirt - Navy, Size M, Cotton"
    assert check(title, Context(SHIRT, BRANDS)) == [] and warnings == []
    spam = SHIRT | {"title": "OXFORD SHIRT - FREE SHIPPING!!! BEST PRICE"}
    t2, _ = rule_title(spam)
    assert "FREE" not in t2.upper().split() and "!" not in t2 and check(t2, Context(spam, BRANDS)) == []
    t3, _ = rule_title(MUG)
    assert t3 == "Kiln & Co Mug - Stoneware"


def test_rule_title_adds_product_type_to_a_short_title():
    row = MUG | {"title": "Aurora", "product_type": "Home > Lighting > Table Lamps", "material": ""}
    assert rule_title(row)[0] == "Kiln & Co Aurora Table Lamp"


def test_rule_title_stays_under_150():
    row = SHIRT | {"title": "Oxford shirt " + "with a long story " * 12}
    t, _ = rule_title(row)
    assert len(t) <= 150 and t.startswith("Harbour Lane")


def test_front_coverage():
    assert front_coverage("Harbour Lane Oxford Shirt - Navy, Size M, Cotton", SHIRT) == (4, 4)
    assert front_coverage("Oxford Shirt " + "x" * 70 + " Harbour Lane Navy M Cotton", SHIRT) == (0, 4)
