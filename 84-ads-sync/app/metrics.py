"""Pure functions: derived metrics, pacing and alert rules. No I/O, so every number the
service reports can be tested with fixed inputs.

Conventions:
- A ratio whose denominator is 0 is None ("not defined"), never 0 or infinity.
- CPL is spend / conversions: the cost per lead when the conversion actions are leads (the
  default on Meta), the cost per acquisition otherwise. The API calls it `cpl` and says so.
- Money is rounded to 2 decimals, ratios to 4, only when reported; sums use full precision.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

COUNTS = ("spend", "impressions", "clicks", "conversions", "revenue")
# Monday .. Sunday. Weekend spend is usually lower (B2B); override per budget.
DEFAULT_WEEKDAY_WEIGHTS = (1.0, 1.0, 1.0, 1.0, 1.0, 0.5, 0.5)


def ratio(num: float, den: float, digits: int) -> float | None:
    return round(num / den, digits) if den else None


def derived(t: dict) -> dict:
    """Totals -> totals plus ctr, cpc, cpm, cpl, roas, cvr (computed here, never taken from an API)."""
    spend = float(t.get("spend") or 0)
    imp, clicks = int(t.get("impressions") or 0), int(t.get("clicks") or 0)
    conv, rev = float(t.get("conversions") or 0), float(t.get("revenue") or 0)
    return {
        "spend": round(spend, 2), "impressions": imp, "clicks": clicks,
        "conversions": clean(conv), "revenue": round(rev, 2),
        "ctr": ratio(clicks, imp, 4),
        "cpc": ratio(spend, clicks, 2),
        "cpm": ratio(spend * 1000, imp, 2),
        "cpl": ratio(spend, conv, 2),
        "roas": ratio(rev, spend, 2),
        "cvr": ratio(conv, clicks, 4),
    }


def clean(n: float) -> float | int:
    n = round(float(n), 2)
    return int(n) if n.is_integer() else n


def add(acc: dict, row: dict) -> dict:
    for k in COUNTS:
        acc[k] = acc.get(k, 0) + (row.get(k) or 0)
    return acc


def delta_pct(cur: float | None, prev: float | None) -> float | None:
    """Relative change in percent; None when either side is missing or the previous is 0."""
    if cur is None or prev is None or prev == 0:
        return None
    return round((cur - prev) / prev * 100, 1)


# ---------- pacing


def month_bounds(month: str) -> tuple[date, date]:
    y, m = int(month[:4]), int(month[5:7])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def day_weight(d: date, weighting: str, weights: tuple | list | None) -> float:
    if weighting == "linear":
        return 1.0
    w = weights or DEFAULT_WEEKDAY_WEIGHTS
    return float(w[d.weekday()])


def expected_fraction(month: str, as_of: date, weighting: str = "linear", weights=None) -> tuple[float, int, int]:
    """Share of the month's budget that should be spent by the END of `as_of`.

    Returns (fraction, elapsed_days, days_in_month). Linear: elapsed / days. Weekday: the
    weights of days 1..as_of over the weights of the whole month. Before the month: 0; after: 1.
    """
    first, last = month_bounds(month)
    days = (last - first).days + 1
    if as_of < first:
        return 0.0, 0, days
    if as_of >= last:
        return 1.0, days, days
    elapsed = (as_of - first).days + 1
    all_days = [first + timedelta(i) for i in range(days)]
    total = sum(day_weight(d, weighting, weights) for d in all_days)
    done = sum(day_weight(d, weighting, weights) for d in all_days[:elapsed])
    return (done / total if total else elapsed / days), elapsed, days


def pacing(budget: float, spend_to_date: float, month: str, as_of: date,
           weighting: str = "linear", weights=None) -> dict:
    frac, elapsed, days = expected_fraction(month, as_of, weighting, weights)
    expected = budget * frac
    return {
        "budget": round(budget, 2),
        "spend_to_date": round(spend_to_date, 2),
        "expected_to_date": round(expected, 2),
        "pace": ratio(spend_to_date, expected, 3),           # 1.0 = exactly on plan
        "elapsed_days": elapsed, "days_in_month": days,
        "expected_fraction": round(frac, 4),
        # Same pace for the rest of the month (weighted days count as in the plan).
        "projected_month_spend": round(spend_to_date / frac, 2) if frac else None,
        "remaining_budget": round(budget - spend_to_date, 2),
    }


# ---------- alert rules


def pacing_alerts(key: str, p: dict, over_pct: float, under_pct: float, min_days: int) -> list[dict]:
    out = []
    pace = p["pace"]
    if pace is None or p["elapsed_days"] < 1:
        return out
    if pace >= 1 + over_pct / 100:
        out.append({"rule": "overspend", "campaign": key, "severity": "high" if pace >= 1 + 2 * over_pct / 100 else "medium",
                    "value": pace, "threshold": round(1 + over_pct / 100, 3),
                    "message": f"{key}: spent {p['spend_to_date']:.2f} of a {p['budget']:.2f} budget by day "
                               f"{p['elapsed_days']} of {p['days_in_month']}; plan was {p['expected_to_date']:.2f} "
                               f"({round((pace - 1) * 100)}% over). Same pace ends the month at {p['projected_month_spend']:.2f}."})
    elif p["elapsed_days"] >= min_days and pace <= 1 - under_pct / 100:
        out.append({"rule": "underspend", "campaign": key, "severity": "low",
                    "value": pace, "threshold": round(1 - under_pct / 100, 3),
                    "message": f"{key}: spent {p['spend_to_date']:.2f} of a {p['budget']:.2f} budget by day "
                               f"{p['elapsed_days']} of {p['days_in_month']}; plan was {p['expected_to_date']:.2f} "
                               f"({round((1 - pace) * 100)}% under)."})
    return out


def efficiency_alerts(key: str, window: dict, days: int, cpl_target: float | None,
                      roas_target: float | None) -> list[dict]:
    """window: totals over the last `days` days (spend, conversions, revenue)."""
    out = []
    d = derived(window)
    if cpl_target and d["cpl"] is not None and d["cpl"] > cpl_target:
        out.append({"rule": "cpl_above_target", "campaign": key, "severity": "medium",
                    "value": d["cpl"], "threshold": cpl_target,
                    "message": f"{key}: CPL {d['cpl']:.2f} over the last {days} days ({d['spend']:.2f} spend, "
                               f"{d['conversions']} conversions) is above the target {cpl_target:.2f}."})
    if roas_target and d["spend"] > 0 and (d["roas"] or 0) < roas_target:
        out.append({"rule": "roas_below_target", "campaign": key, "severity": "medium",
                    "value": d["roas"] or 0, "threshold": roas_target,
                    "message": f"{key}: ROAS {d['roas'] or 0:.2f} over the last {days} days ({d['revenue']:.2f} revenue "
                               f"on {d['spend']:.2f} spend) is below the target {roas_target:.2f}."})
    return out


def zero_conversion_alert(key: str, daily: dict[str, dict], as_of: date, n_days: int,
                          min_spend: float) -> dict | None:
    """Spend on each of the last n_days (ending as_of) and no conversion on any of them.

    `daily`: {YYYY-MM-DD: totals}. A missing day (no row) breaks the streak: we do not know
    that nothing converted, only that nothing was reported."""
    spend, streak = 0.0, 0
    for i in range(n_days):
        t = daily.get((as_of - timedelta(i)).isoformat())
        if not t or (t.get("spend") or 0) <= 0 or (t.get("conversions") or 0) > 0:
            return None
        spend += t["spend"]
        streak += 1
    if spend < min_spend:
        return None
    return {"rule": "spend_no_conversions", "campaign": key, "severity": "high",
            "value": round(spend, 2), "threshold": n_days,
            "message": f"{key}: {spend:.2f} spent over the last {streak} days with 0 conversions. "
                       "Check the conversion tracking (pixel / conversion action) and the landing page."}
