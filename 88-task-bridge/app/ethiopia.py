"""Ethiopian calendar, occasions and clock-time helpers. Pure functions, no network, no I/O beyond
reading the bundled occasions file. Dates are conversions by the standard JDN algorithm; the
Gregorian dates of movable occasions (Fasika, Islamic holidays, Irreecha) can still be tentative,
see the `verified` flag on each occasion."""
from __future__ import annotations

import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

MONTHS = ("Meskerem", "Tikimt", "Hidar", "Tahsas", "Tir", "Yekatit", "Megabit", "Miyazya", "Ginbot",
          "Sene", "Hamle", "Nehase", "Pagume")
# ICU-style JDN offset: 1 Meskerem of Ethiopian year y is JDN offset + 365*y + y//4 + 0 (year 1 = 1724221)
_ETHIOPIC_EPOCH_JDN = 1723856
_GREGORIAN_ORDINAL_TO_JDN = 1721425  # date.toordinal() + this = JDN (proleptic Gregorian)
DATA_FILE = Path(__file__).resolve().parent / "data" / "ethiopia_occasions.yaml"


def _jdn(d: date) -> int:
    return d.toordinal() + _GREGORIAN_ORDINAL_TO_JDN


def is_ethiopian_leap(year: int) -> bool:
    return year % 4 == 3


def to_ethiopian(d: date) -> tuple[int, int, int, str]:
    """(year, month, day, month_name) in the Ethiopian calendar."""
    r = (_jdn(d) - _ETHIOPIC_EPOCH_JDN) % 1461
    n = r % 365 + 365 * (r // 1460)
    year = 4 * ((_jdn(d) - _ETHIOPIC_EPOCH_JDN) // 1461) + r // 365 - r // 1460
    month = n // 30 + 1
    day = n % 30 + 1
    return year, month, day, MONTHS[month - 1]


def from_ethiopian(y: int, m: int, d: int) -> date:
    """Gregorian date of an Ethiopian (year, month, day). Raises ValueError for an impossible date."""
    if not 1 <= m <= 13:
        raise ValueError(f"Ethiopian month must be 1..13, got {m}")
    last = (6 if is_ethiopian_leap(y) else 5) if m == 13 else 30
    if not 1 <= d <= last:
        raise ValueError(f"day {d} is outside 1..{last} for month {m} of {y} E.C.")
    jdn = _ETHIOPIC_EPOCH_JDN + 365 * y + y // 4 + 30 * m + d - 31
    return date.fromordinal(jdn - _GREGORIAN_ORDINAL_TO_JDN)


def format_ec(d: date) -> str:
    """'29 Tahsas 2019 E.C.'"""
    y, _, day, name = to_ethiopian(d)
    return f"{day} {name} {y} E.C."


# --- occasions ------------------------------------------------------------------------------

KINDS = ("public_holiday", "religious", "season", "fasting")


@lru_cache(maxsize=1)
def _load() -> tuple[dict, ...]:
    raw = yaml.safe_load(DATA_FILE.read_text(encoding="utf-8")) or {}
    out = []
    for o in raw.get("occasions", []):
        start = o["date"] if isinstance(o["date"], date) else date.fromisoformat(str(o["date"]))
        end = o.get("end")
        end = start if end is None else (end if isinstance(end, date) else date.fromisoformat(str(end)))
        if o["kind"] not in KINDS:
            raise ValueError(f"occasion {o['name']}: unknown kind {o['kind']}")
        out.append({"name": o["name"], "date": start, "end": end, "ec": o.get("ec") or format_ec(start),
                    "kind": o["kind"], "movable": bool(o["movable"]), "verified": bool(o["verified"]),
                    "source": o.get("source", ""), "notes": o.get("notes", "")})
    return tuple(sorted(out, key=lambda o: (o["date"], o["name"])))


def occasions() -> list[dict]:
    """Every bundled occasion, oldest first (copies, safe to mutate)."""
    return [dict(o) for o in _load()]


def occasions_near(d: date, days: int = 21, past_days: int = 0) -> list[dict]:
    """Occasions that overlap the window [d - past_days, d + days] (a fast that is already running
    counts). Each: name, date, end, ec, kind, movable, verified, source, notes."""
    lo, hi = d - timedelta(days=past_days), d + timedelta(days=days)
    return [dict(o) for o in _load() if o["date"] <= hi and o["end"] >= lo]


# --- Ethiopian clock time -------------------------------------------------------------------

_SUFFIX = r"(?!\s*(?:[ap]\.?\s?m\b|hrs?\b|hours?\b|h\b))"
# H:MM with H = 1..12 and no leading zero (09:00 and 15:00 read as 24-hour), not part of a longer
# number, date, ratio or "24/7"; followed by no am/pm/hrs marker.
_COLON = re.compile(r"(?<![\d:/.\-])(?:[1-9]|1[0-2]):[0-5]\d(?![\d:])" + _SUFFIX, re.I)
_OCLOCK = re.compile(r"\b(?:[1-9]|1[0-2]|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
                     r"\s*o['’]?\s?clock\b", re.I)

EAT_NOTE = ("Ethiopian clock time runs 6 hours off international time (3:00 can mean 09:00 or 21:00). "
            "Write times in 24-hour format, e.g. 14:00, or ask the owner which convention is meant.")


def find_ambiguous_times(text: str) -> list[str]:
    """The clock-time snippets in `text` that have no am/pm and are not clearly 24-hour."""
    return [m.group(0).strip() for rx in (_COLON, _OCLOCK) for m in rx.finditer(text)]


def ethiopian_time_warning(text: str) -> str | None:
    """A note when the text has an ambiguous clock time ("at 3:00", "3 o'clock"), else None."""
    found = find_ambiguous_times(text)
    if not found:
        return None
    return f"Ambiguous time {', '.join(repr(f) for f in found)}. {EAT_NOTE}"
