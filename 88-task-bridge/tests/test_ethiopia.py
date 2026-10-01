from datetime import date, timedelta

import pytest

from app.ethiopia import (MONTHS, ethiopian_time_warning, find_ambiguous_times, format_ec, from_ethiopian,
                          occasions, occasions_near, to_ethiopian)

KNOWN = [
    (date(2026, 9, 11), (2019, 1, 1, "Meskerem")),
    (date(2027, 1, 7), (2019, 4, 29, "Tahsas")),      # Genna
    (date(2027, 1, 19), (2019, 5, 11, "Tir")),         # Timket
    (date(2026, 9, 27), (2019, 1, 17, "Meskerem")),    # Meskel
    (date(2024, 9, 11), (2017, 1, 1, "Meskerem")),
    (date(2027, 9, 11), (2019, 13, 6, "Pagume")),      # leap Pagume 6 (2019 % 4 == 3)
    (date(2027, 9, 12), (2020, 1, 1, "Meskerem")),
    (date(2026, 9, 10), (2018, 13, 5, "Pagume")),
    (date(2027, 5, 2), (2019, 8, 24, "Miyazya")),      # Fasika
]


def test_month_names():
    assert len(MONTHS) == 13 and MONTHS[0] == "Meskerem" and MONTHS[12] == "Pagume"


@pytest.mark.parametrize("g,ec", KNOWN)
def test_known_pairs(g, ec):
    assert to_ethiopian(g) == ec
    assert from_ethiopian(*ec[:3]) == g


def test_round_trip_every_day_2026_2028():
    d = date(2026, 1, 1)
    prev = None
    while d <= date(2028, 12, 31):
        y, m, day, name = to_ethiopian(d)
        assert name == MONTHS[m - 1] and 1 <= m <= 13 and 1 <= day <= (6 if m == 13 else 30)
        assert from_ethiopian(y, m, day) == d
        if prev:  # consecutive days advance by exactly one Ethiopian day
            assert (from_ethiopian(*prev) + timedelta(days=1)) == d
        prev = (y, m, day)
        d += timedelta(days=1)


def test_pagume_length_and_invalid():
    assert from_ethiopian(2019, 13, 6) == date(2027, 9, 11)
    with pytest.raises(ValueError):
        from_ethiopian(2018, 13, 6)      # not a leap year
    for bad in [(2019, 14, 1), (2019, 0, 1), (2019, 1, 31), (2019, 1, 0)]:
        with pytest.raises(ValueError):
            from_ethiopian(*bad)


def test_format_ec():
    assert format_ec(date(2026, 9, 11)) == "1 Meskerem 2019 E.C."
    assert format_ec(date(2027, 1, 7)) == "29 Tahsas 2019 E.C."


def test_occasion_ec_labels_match_converter():
    occ = occasions()
    assert len(occ) >= 15
    for o in occ:
        assert o["ec"] == format_ec(o["date"]), o["name"]
        assert o["kind"] in ("public_holiday", "religious", "season", "fasting")
        assert isinstance(o["movable"], bool) and isinstance(o["verified"], bool)
        assert o["source"] and o["end"] >= o["date"]
        assert date(2026, 9, 1) <= o["date"] <= date(2027, 9, 30)


def test_fixed_holidays_verified_and_movable_ones_not():
    by = {o["name"]: o for o in occasions()}
    assert by["Genna (Christmas)"]["verified"] and not by["Genna (Christmas)"]["movable"]
    assert by["Timket (Epiphany)"]["date"] == date(2027, 1, 19)
    for n in ("Eid al-Fitr", "Eid al-Adha", "Mawlid", "Fasika (Easter)"):
        assert by[n]["movable"] and not by[n]["verified"]


def test_occasions_near_filtering():
    names = lambda d, **k: [o["name"] for o in occasions_near(d, **k)]
    assert "Genna (Christmas)" in names(date(2026, 12, 20))          # 18 days ahead
    assert "Genna (Christmas)" not in names(date(2026, 12, 20), days=10)
    assert "Genna (Christmas)" in names(date(2027, 1, 7), days=0)
    assert "Genna (Christmas)" not in names(date(2027, 1, 8))        # past, no look-back
    assert "Genna (Christmas)" in names(date(2027, 1, 8), past_days=3)
    # a running fast overlaps the window
    advent = [o for o in occasions_near(date(2026, 12, 15), days=1) if o["kind"] == "fasting"]
    assert advent and "lower beer demand" in advent[0]["notes"]
    assert occasions_near(date(2030, 1, 1)) == []
    # results are copies
    occasions_near(date(2027, 1, 1))[0]["name"] = "x"
    assert all(o["name"] != "x" for o in occasions())


def test_alcohol_sponsorship_note_on_holidays():
    for o in occasions():
        if o["kind"] == "public_holiday" and not o["name"].startswith("Eid"):
            assert "alcohol" in o["notes"].lower()


@pytest.mark.parametrize("text", [
    "Meet at 3:00 by the gate", "Dinner at 7:30.", "see you at 3 o'clock", "Doors open at 12:00",
    "Check-in 2:00 and checkout 11:00", "at 10:15 sharp", "three o'clock tea",
])
def test_time_warning_positive(text):
    note = ethiopian_time_warning(text)
    assert note and "6 hours" in note and "24-hour" in note
    assert find_ambiguous_times(text)


@pytest.mark.parametrize("text", [
    "Open 24/7", "3:00 pm", "3:00 PM sharp", "3:00pm", "3.00 p.m.", "15:00", "Check-in 14:00", "09:00", "08:30 to 17:00",
    "Call 0911 12:34:56", "Ratio 3:1", "Open 15:00 hrs", "Price 3.50 ETB", "Room 3", "on 2026-09-11", "no times here",
    "",
])
def test_time_warning_negative(text):
    assert ethiopian_time_warning(text) is None
