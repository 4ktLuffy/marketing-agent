"""Pure math: derived metrics, pacing (month boundaries, weekday weighting) and alert rules."""
from datetime import date

import pytest

from app import metrics as M


def test_derived_metrics_and_zero_denominators():
    d = M.derived({"spend": 100, "impressions": 10000, "clicks": 200, "conversions": 4, "revenue": 350})
    assert d["ctr"] == 0.02 and d["cpc"] == 0.5 and d["cpm"] == 10.0
    assert d["cpl"] == 25.0 and d["roas"] == 3.5 and d["cvr"] == 0.02
    z = M.derived({"spend": 50, "impressions": 0, "clicks": 0, "conversions": 0, "revenue": 0})
    assert z["ctr"] is None and z["cpc"] is None and z["cpl"] is None and z["roas"] == 0.0
    nothing = M.derived({})
    assert nothing["roas"] is None and nothing["spend"] == 0


def test_delta_pct():
    assert M.delta_pct(110, 100) == 10.0
    assert M.delta_pct(90, 100) == -10.0
    assert M.delta_pct(5, 0) is None and M.delta_pct(None, 3) is None and M.delta_pct(3, None) is None


@pytest.mark.parametrize("month,days", [("2026-02", 28), ("2028-02", 29), ("2026-04", 30), ("2026-12", 31)])
def test_month_lengths(month, days):
    first, last = M.month_bounds(month)
    assert first.day == 1 and last.day == days


def test_linear_pacing_mid_month():
    p = M.pacing(3000, 2000, "2026-09", date(2026, 9, 15))
    assert p["expected_to_date"] == 1500.0 and p["pace"] == 1.333
    assert p["elapsed_days"] == 15 and p["days_in_month"] == 30
    assert p["projected_month_spend"] == 4000.0 and p["remaining_budget"] == 1000.0


def test_pacing_month_boundaries():
    # First day: 1/31 of the budget; last day and after: all of it; before the month: nothing.
    assert M.expected_fraction("2026-10", date(2026, 10, 1))[0] == pytest.approx(1 / 31)
    assert M.expected_fraction("2026-10", date(2026, 10, 31))[:2] == (1.0, 31)
    assert M.expected_fraction("2026-10", date(2026, 11, 3))[0] == 1.0
    assert M.expected_fraction("2026-10", date(2026, 9, 30))[:2] == (0.0, 0)
    p = M.pacing(1000, 0, "2026-10", date(2026, 9, 30))
    assert p["pace"] is None and p["projected_month_spend"] is None
    # February in a leap year: day 14 of 29.
    assert M.expected_fraction("2028-02", date(2028, 2, 14))[0] == pytest.approx(14 / 29)


def test_weekday_weighting():
    # September 2026 starts on a Tuesday. Weights Mon..Fri 1, Sat/Sun 0.5: 22 weekdays + 8 weekend days = 26.
    frac, elapsed, days = M.expected_fraction("2026-09", date(2026, 9, 6), "weekday")
    # Tue 1 .. Sun 6: 4 weekdays (1,2,3,4) + Sat 5 + Sun 6 = 4 + 1 = 5 of 26.
    assert (elapsed, days) == (6, 30) and frac == pytest.approx(5 / 26)
    lin = M.expected_fraction("2026-09", date(2026, 9, 6))[0]
    assert lin == pytest.approx(6 / 30) and frac < lin  # a weekend just passed: less was expected
    # Custom weights: nothing on weekends.
    f2 = M.expected_fraction("2026-09", date(2026, 9, 6), "weekday", [1, 1, 1, 1, 1, 0, 0])[0]
    assert f2 == pytest.approx(4 / 22)
    assert M.expected_fraction("2026-09", date(2026, 9, 30), "weekday")[0] == 1.0


def test_pacing_alerts_over_under_and_min_days():
    over = M.pacing(3000, 3310, "2026-09", date(2026, 9, 27))
    a = M.pacing_alerts("autumn-launch", over, 15, 25, 3)
    assert [x["rule"] for x in a] == ["overspend"] and a[0]["severity"] == "medium"
    assert "3310.00" in a[0]["message"] and "2700.00" in a[0]["message"]
    way_over = M.pacing(1000, 2000, "2026-09", date(2026, 9, 15))
    assert M.pacing_alerts("x", way_over, 15, 25, 3)[0]["severity"] == "high"
    on_plan = M.pacing(3000, 2750, "2026-09", date(2026, 9, 27))
    assert M.pacing_alerts("x", on_plan, 15, 25, 3) == []
    under = M.pacing(3000, 1000, "2026-09", date(2026, 9, 20))
    assert [x["rule"] for x in M.pacing_alerts("x", under, 15, 25, 3)] == ["underspend"]
    early = M.pacing(3000, 0, "2026-09", date(2026, 9, 2))  # day 2: too early to call underspend
    assert M.pacing_alerts("x", early, 15, 25, 3) == []
    # Exactly at the threshold counts (>=).
    edge = M.pacing(3000, 2300, "2026-09", date(2026, 9, 20))  # expected 2000 -> pace 1.15
    assert [x["rule"] for x in M.pacing_alerts("x", edge, 15, 25, 3)] == ["overspend"]


def test_efficiency_alerts():
    w = {"spend": 910, "conversions": 35, "revenue": 1000}
    a = M.efficiency_alerts("autumn-launch", w, 7, cpl_target=20, roas_target=2)
    assert [x["rule"] for x in a] == ["cpl_above_target", "roas_below_target"]
    assert a[0]["value"] == 26.0 and a[1]["value"] == 1.1
    assert M.efficiency_alerts("x", w, 7, cpl_target=30, roas_target=1) == []
    # No conversions: CPL is undefined, so no CPL alert (the zero-conversion rule covers it).
    assert M.efficiency_alerts("x", {"spend": 100, "conversions": 0}, 7, 20, None) == []
    # No spend: ROAS undefined, no alert.
    assert M.efficiency_alerts("x", {"spend": 0}, 7, None, 2) == []


def test_zero_conversion_streak():
    daily = {"2026-09-25": {"spend": 20, "conversions": 0}, "2026-09-26": {"spend": 20, "conversions": 0},
             "2026-09-27": {"spend": 20, "conversions": 0}, "2026-09-24": {"spend": 20, "conversions": 1}}
    a = M.zero_conversion_alert("meta:2", daily, date(2026, 9, 27), 3, 0)
    assert a and a["value"] == 60.0 and a["rule"] == "spend_no_conversions"
    assert M.zero_conversion_alert("meta:2", daily, date(2026, 9, 27), 4, 0) is None   # the 24th converted
    assert M.zero_conversion_alert("meta:2", daily, date(2026, 9, 27), 3, 100) is None  # below min spend
    gap = dict(daily)
    del gap["2026-09-26"]  # no row that day: unknown, not "zero"
    assert M.zero_conversion_alert("meta:2", gap, date(2026, 9, 27), 3, 0) is None
    paused = dict(daily, **{"2026-09-26": {"spend": 0, "conversions": 0}})
    assert M.zero_conversion_alert("meta:2", paused, date(2026, 9, 27), 3, 0) is None
