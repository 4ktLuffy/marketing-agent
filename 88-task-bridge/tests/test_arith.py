"""Arithmetic check: a stated total, percentage change or saving the numbers do not support."""
from datetime import date

import pytest

from app import arith

from .conftest import AUTH
from .data import fact

DAY = date(2026, 11, 10)

FACTS = [
    fact("harar-room", "Harar Room: 1,700 birr per room per night.", "1,700 birr per room per night",
         subject={"kind": "variant", "ref": "Harar Room"}, fact_type="price", attribute="rate", value=1700,
         unit="night", currency="ETB", basis="per_room", valid_from="2026-09-01"),
    fact("harar-room-old", "Harar Room: 1,600 birr per room per night.", "1,600 birr per room per night",
         subject={"kind": "variant", "ref": "Harar Room"}, fact_type="price", attribute="rate", value=1600,
         unit="night", currency="ETB", basis="per_room", valid_to="2026-08-31", status="expired"),
    fact("coffee-crate", "A crate of Yirga coffee is 1,700 birr.", "1,700 birr per crate",
         subject={"kind": "product", "ref": "Yirga coffee"}, fact_type="price", attribute="price", value=1700,
         unit="crate", currency="ETB"),
    fact("flight", "A tasting flight is $9.", "$9 per flight", subject={"kind": "service", "ref": "Tasting flight"},
         fact_type="price", value=9, unit="flight", currency="USD"),
    fact("city-tour", "City tour: $75 per guest.", "$75 per person", subject={"kind": "service", "ref": "City tour"},
         fact_type="price", attribute="price", value=75, unit="person", currency="USD", basis="per_person"),
]
KINDS = lambda out: [(f["label"], f["blocking"]) for f in out]


def run(text, facts=FACTS):
    return arith.check(text, facts, DAY, {})


@pytest.mark.parametrize("text, detail", [
    ("20 crates of Yirga coffee cost 32,000 birr.", "20 × 1,700 birr = 34,000 birr, not 32,000"),
    ("Order 20 crates of Yirga coffee = 32,000 birr.", "34,000 birr, not 32,000"),
    ("20 rooms for 3 nights at $75 = $5,000.", "20 × 3 nights × $75 = $4,500, not $5,000"),
    ("Ten Harar Room rooms for 2 nights come to 30,000 birr.", "34,000 birr, not 30,000"),
])
def test_wrong_total_is_a_strong_conflict(text, detail):
    out = run(text)
    assert KINDS(out) == [("conflict_or_expired", True)], out
    assert detail in out[0]["detail"]


def test_wrong_total_names_the_fact():
    out = run("20 crates of Yirga coffee cost 32,000 birr.")
    assert out[0]["fact_key"] == "coffee-crate" and out[0]["quote"] == "1,700 birr per crate"


@pytest.mark.parametrize("text, shown", [
    ("20 crates of Yirga coffee cost 34,000 birr.", "20 × 1,700 birr = 34,000 birr"),
    ("20 rooms for 3 nights at $75 = $4,500.", "20 × 3 nights × $75 = $4,500"),
    ("5 Harar Room rooms for 2 nights cost 17,000 birr.", "5 × 2 nights × 1,700 birr = 17,000 birr"),
    ("4 guests on the City tour come to $300.", "4 × $75 = $300"),
    ("20 crates of Yirga coffee cost about 34k birr.", "34,000 birr"),
])
def test_correct_total_is_a_match_showing_the_sum(text, shown):
    out = run(text)
    assert KINDS(out) == [("match", False)], out
    assert shown in out[0]["detail"]


def test_rounding_to_the_currency_unit_is_not_a_conflict():
    f = [fact("tea", "Tea is $3.33 a box.", "$3.33 per box", subject={"kind": "product", "ref": "Tea"}, fact_type="price",
              value=3.33, unit="box", currency="USD")]
    assert KINDS(run("3 boxes of Tea cost $10.", f)) == [("match", False)]          # 9.99 rounds to 10
    assert KINDS(run("3 boxes of Tea cost $10.50.", f)) == [("conflict_or_expired", True)]
    assert KINDS(run("3 crates of Yirga coffee cost 5.1k birr.")) == [("match", False)]


@pytest.mark.parametrize("text", [
    "20 crates of Yirga coffee are on their way.",                  # no computation
    "Our rooms start at 1,700 birr.",
    "We opened in 2019 and have 20 rooms; call +251 911 223344 for 1,000 birr deals.",
    "Book 3 nights for 2,000 birr at the Harar Room.",              # no qty-noun before a total
    "Order 20 crates today for 5,000 birr.",                        # no fact for 'crates' by subject... unit crate is unique
    "20 rooms at $75 each, 3 nights.",
    "A room for 1,700 birr a night.",
    "In 2026 our 12 guides cost nothing.",
    "2 guests for $150 per night.",
    "Four tasters come as a flight for nine dollars.",              # contents of a unit, not units
])
def test_no_computation_or_unrelated_numbers_say_nothing_wrong(text):
    assert [f for f in run(text) if f["blocking"]] == [], run(text)


def test_nothing_without_a_known_price_or_text_price():
    assert run("20 widgets for 32,000 birr.") == []
    assert run("20 rooms for 3 nights = 5,000 birr.") == []         # Harar not named, 'rooms' has no unit match


# (b) percentage change
def test_wrong_percentage_change_conflicts():
    out = run("Harar Room prices are up 10% this season.")
    assert KINDS(out) == [("conflict_or_expired", True)]
    assert "1,600 birr → 1,700 birr = +6.25%" in out[0]["detail"] and out[0]["fact_key"] == "harar-room"


def test_right_percentage_change_matches():
    out = run("Harar Room prices are up 6% this season.")
    assert KINDS(out) == [("match", False)] and "+6.25%" in out[0]["detail"]


def test_percentage_with_text_prices_and_direction():
    assert KINDS(run("Rooms went from 1,600 birr to 1,700 birr, up 10%.")) == [("conflict_or_expired", True)]
    assert KINDS(run("Rooms went from 1,600 birr to 1,700 birr, up 6.25%.")) == [("match", False)]
    assert KINDS(run("Rooms went from 1,700 birr to 1,600 birr, down 6%.")) == [("match", False)]
    assert KINDS(run("Rooms went from 1,700 birr to 1,600 birr, up 6%.")) == [("conflict_or_expired", True)]


@pytest.mark.parametrize("text", [
    "Harar Room: 10% off for groups.",                               # a discount, not a change
    "We are 10% up on last year's bookings.",                        # no price words, no fact pair
    "Yirga coffee prices up 10% this year.",                         # about something with no change on record
    "Save 10% and prices are fixed.",
])
def test_percentage_negatives(text):
    assert [f for f in run(text) if f["blocking"]] == []


# (c) saving
def test_saving_amount_and_percent_arithmetic():
    assert KINDS(run("Was 2,000 birr, now 1,700 birr: save 300 birr.")) == [("match", False)]
    out = run("Was 2,000 birr, now 1,700 birr: save 500 birr.")
    assert KINDS(out) == [("conflict_or_expired", True)] and "300 birr, not 500" in out[0]["detail"]
    assert KINDS(run("Was $100, now $75, so 25% off.")) == [("match", False)]
    assert KINDS(run("Was $100, now $75, so 20% off.")) == [("conflict_or_expired", True)]


def test_saving_negatives():
    assert run("Save 20% on all rooms.") == []
    assert run("Save $300 when you book.") == []
    assert [f for f in run("20% off, was $100 each plus $20 shipping.") if f["blocking"]] == []


# integration: /check and submit carry the arithmetic findings
def test_check_endpoint_includes_arith_findings(client, stack):
    stack.facts = [dict(f, version=1) for f in FACTS]
    r = client.post("/check", json={"text": "20 crates of Yirga coffee cost 32,000 birr.", "publish_on": "2026-11-10"},
                    headers=AUTH).json()
    assert r["blocked"] is True
    assert any(f["label"] == "conflict_or_expired" and "34,000" in f["detail"] for f in r["findings"])
    ok = client.post("/check", json={"text": "20 crates of Yirga coffee cost 34,000 birr.", "publish_on": "2026-11-10"},
                     headers=AUTH).json()
    assert any(f["label"] == "match" and "arithmetic" in f["detail"] for f in ok["findings"])
    assert not any(f["label"] == "conflict_or_expired" and "arithmetic" in f["detail"] for f in ok["findings"])


def test_submit_blocks_a_wrong_total(client, stack):
    from .conftest import make_task
    stack.facts = [dict(f, version=1) for f in FACTS]
    t = client.post("/tasks", json={"goal": "Coffee order", "pieces": [{"key": "p1", "channel": "email"}],
                                    "scope": {}, "publish_on": "2026-11-10"}, headers=AUTH)
    assert t.status_code == 201, t.text
    tid = t.json()["id"]
    d = client.post(f"/tasks/{tid}/paste", json={"text": "=== 1 EMAIL ===\n20 crates of Yirga coffee cost 32,000 birr.",
                                                 "provider": "chatgpt"}, headers=AUTH).json()
    s = client.post(f"/tasks/{tid}/submit", json={"draft_id": d["draft_id"]}, headers=AUTH)
    assert s.status_code == 200, s.text
    piece = s.json()["pieces"][0] if isinstance(s.json(), dict) and "pieces" in s.json() else s.json()[0]
    assert piece["blocked"] is True
    assert any(f["label"] == "conflict_or_expired" and "34,000" in f["detail"] for f in piece["findings"])


# --- regression: numbers in names and pack sizes are not counts (found on the eval gate)
from datetime import date as _date

_FX = [
    {"key": "mot", "subject": {"kind": "service", "ref": "Class 4 MOT"}, "fact_type": "price", "value": 45, "currency": "GBP",
     "value_text": "£45", "status": "active", "sensitivity": "public", "scope": {}, "text": "A Class 4 MOT costs £45."},
    {"key": "bike", "subject": {"kind": "product", "ref": "Tempo 3 hybrid"}, "fact_type": "price", "value": 1200, "currency": "GBP",
     "value_text": "£1,200", "status": "active", "sensitivity": "public", "scope": {}, "text": "The Tempo 3 hybrid costs £1,200."},
    {"key": "cartons", "subject": {"kind": "product", "ref": "Printed cartons"}, "fact_type": "price", "value": 640, "currency": "GBP",
     "value_text": "£640 for 1,000 cartons", "status": "active", "sensitivity": "public", "scope": {}, "text": "1,000 printed cartons cost £640 plus VAT."},
    {"key": "harar", "subject": {"kind": "product", "ref": "Harar 33cl"}, "fact_type": "price", "value": 1700, "currency": "ETB",
     "unit": "crate", "basis": "per_unit", "value_text": "1,700 birr per crate", "status": "active", "sensitivity": "public",
     "scope": {}, "text": "Harar 33cl: 1,700 birr per crate."},
]


@pytest.mark.parametrize("text", [
    "A Class 4 MOT at our garage costs £45.",
    "The Tempo 3 hybrid costs £1,200 in every size.",
    "1,000 printed cartons cost £640 plus VAT.",
    "A crate of 24 Harar 33cl bottles is 1,700 birr.",
])
def test_names_and_pack_sizes_are_not_counts(text):
    assert not [f for f in arith.check(text, _FX, _date(2026, 10, 5), {}) if f["blocking"]]


def test_real_wrong_total_still_blocks():
    got = arith.check("20 crates of Harar 33cl cost 32,000 birr.", _FX, _date(2026, 10, 5), {})
    assert got and got[0]["blocking"] and "34,000" in got[0]["detail"]


def test_spelled_out_compound_count_is_read_whole():
    from app import evidence as E
    assert [str(x[0]) for x in E._count_things("The lodge has two hundred and three rooms.")] == ["203"]
    assert [str(x[0]) for x in E._count_things("The lodge has three conference halls.")] == ["3"]


def test_count_is_tested_against_the_subject_named_before_it():
    from datetime import date
    from app import evidence as E

    def spec(key, ref, n):
        return {"key": key, "subject": {"kind": "product", "ref": ref}, "fact_type": "spec", "value": n, "unit": "bottles",
                "value_text": f"{n} bottles", "text": f"A {ref} holds {n} returnable bottles.", "status": "active",
                "sensitivity": "public", "scope": {}}
    ok = [spec("crate-33cl", "33cl crate", 24), spec("crate-50cl", "50cl crate", 20)]
    bad = E.count_findings("A 33cl crate holds 20 returnable bottles.", ok)
    assert [f["fact_key"] for f in bad] == ["crate-33cl"]
    assert [f["fact_key"] for f in E.count_findings("A 50cl crate holds 18 returnable bottles.", ok)] == ["crate-50cl"]
    assert not E.count_findings("A 33cl crate holds 24 returnable bottles, and a 50cl crate holds 20 returnable bottles.", ok)


def test_percent_between_two_prices():
    from app import arith
    bad = arith._compare_findings("The Queen Room (Lake View) at $111 per night costs 25 percent more than the Queen Room (Garden View) at $99.")
    assert bad and bad[0]["label"] == "conflict_or_expired" and "12.1%" in bad[0]["detail"]
    ok = arith._compare_findings("The Queen Room (Lake View) at $111 costs 12 percent more than the Queen Room (Garden View) at $99.")
    assert ok and ok[0]["label"] == "match"
    cheap = arith._compare_findings("Harar 33cl at 1,700 birr is 18% cheaper than Heineken 33cl at 2,080 birr.")
    assert cheap and cheap[0]["label"] == "match"
    assert not arith._compare_findings("Book 25 percent more rooms than last year.")


def test_rounded_internal_value_is_still_blocked():
    from datetime import date
    from app import evidence as E
    vol = {"key": "best-seller-volume", "subject": {"kind": "product", "ref": "Harar 33cl"}, "fact_type": "result",
           "value": 38450, "unit": "crates", "value_text": "38,450 crates", "status": "active", "sensitivity": "internal",
           "text": "Harar 33cl: 38,450 crates sold July to September 2026 (internal).", "scope": {}}
    day = date(2026, 10, 14)
    for t in ["Harar 33cl sold more than 38,000 crates from July to September 2026.", "We sold 38,450 crates of Harar."]:
        fs, _ = E.check_text(t, [vol], day, {})
        assert any(f["label"] == "slot_blocked" for f in fs), t
    fs, _ = E.check_text("We have delivered 40,000 crates since we opened.", [vol], day, {})
    assert not any(f["label"] == "slot_blocked" for f in fs)


def test_per_room_total_does_not_multiply_guests():
    from app import arith
    ok = arith._sum_findings("Three nights in a Suite (Lake View) for two guests at $157 per room per night come to $471 in total.", [])
    assert ok and ok[0]["label"] == "match" and ok[0]["total_amount"] == "471"
    bad = arith._sum_findings("Three nights in a Suite (Lake View) for two guests at $157 per room per night come to $942 in total.", [])
    assert bad and bad[0]["label"] == "conflict_or_expired"
    pp = arith._sum_findings("Two guests at $40 per person come to $80.", [])
    assert pp and pp[0]["label"] == "match"


def test_nights_alone_make_a_total():
    from app import arith
    ok = arith._sum_findings("Three nights in a Family Room (Garden View) at $248 per room per night come to $744 in total.", [])
    assert ok and ok[0]["label"] == "match"
    bad = arith._sum_findings("Three nights in a Family Room (Garden View) at $248 per room per night come to $700 in total.", [])
    assert bad and bad[0]["label"] == "conflict_or_expired"


def test_a_changed_price_said_to_hold():
    from datetime import date
    from app import arith

    def p(key, n, **kw):
        return {"key": key, "subject": {"kind": "product", "ref": "Heineken 33cl"}, "fact_type": "price", "value": n,
                "currency": "ETB", "unit": "crate", "status": "active", "sensitivity": "public", "scope": {},
                "value_text": f"{n:,} birr per crate", **kw}
    facts = [p("h-now", 2080, valid_from="2026-08-17"), p("h-old", 1980, valid_to="2026-08-16")]
    bad = arith.check("Heineken 33cl holds at 2,080 birr per crate.", facts, date(2026, 10, 16), {})
    assert bad and bad[0]["label"] == "conflict_or_expired" and "1,980" in bad[0]["detail"]
    assert not [f for f in arith.check("Heineken 33cl is 2,080 birr per crate.", facts, date(2026, 10, 16), {})
                if f["label"] == "conflict_or_expired"]
