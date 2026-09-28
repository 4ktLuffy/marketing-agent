"""Arm assignment and the difference between two rates. Pure functions, no I/O.

Assignment is deterministic: the same install salt, flow and email always give the same arm,
so a replayed or repeated event can never move a contact to the other arm.
"""
import hashlib
import math

Z95 = 1.959963984540054  # two-sided 95 %


def arm_for(salt: str, flow: str, email: str, holdout_pct: float) -> str:
    """'holdout' for about holdout_pct % of emails, 'flow' for the rest."""
    digest = hashlib.sha256(f"{salt}|{flow}|{email.strip().lower()}".encode()).digest()
    u = int.from_bytes(digest[:8], "big") / 2**64  # uniform in [0, 1)
    return "holdout" if u < holdout_pct / 100 else "flow"


def rate(x: int, n: int) -> float | None:
    return round(x / n, 4) if n else None


def difference(x_flow: int, n_flow: int, x_hold: int, n_hold: int, min_n: int) -> dict:
    """Flow rate minus holdout rate with a 95 % interval (two proportions, normal
    approximation, unpooled). Under min_n contacts in either arm, or fewer than 5 contacts
    with (or without) the outcome in either arm, the approximation is poor: no interval."""
    out = {"flow_rate": rate(x_flow, n_flow), "holdout_rate": rate(x_hold, n_hold),
           "difference": None, "ci95": None, "significant": False, "verdict": "not enough data"}
    small = min(n_flow, n_hold) < min_n or min(x_flow, x_hold, n_flow - x_flow, n_hold - x_hold) < 5
    if not n_flow or not n_hold:
        return out
    p1, p2 = x_flow / n_flow, x_hold / n_hold
    out["difference"] = round(p1 - p2, 4)
    if small:
        return out
    se = math.sqrt(p1 * (1 - p1) / n_flow + p2 * (1 - p2) / n_hold)
    lo, hi = p1 - p2 - Z95 * se, p1 - p2 + Z95 * se
    out["ci95"] = [round(lo, 4), round(hi, 4)]
    out["significant"] = lo > 0 or hi < 0
    out["verdict"] = ("flow higher" if lo > 0 else "flow lower" if hi < 0 else
                      "no clear difference (the interval includes 0)")
    return out
