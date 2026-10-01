"""Exact quotes: quantity × price (× nights) from confirmed price facts, in Decimal, with the facts'
own disclosures. A language model gets sums wrong; this one does not."""
from datetime import date
from decimal import Decimal

from . import facts as F
from .arith import fmt

CONFIDENTIAL = "confidential: do not post publicly"


class QuoteError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _refusal(key: str, known: dict, query_excluded: dict, day: date, scope: dict) -> str:
    f = known.get(key)
    if f is None:
        return f"fact {key!r} is unknown"
    r = F.classify(f, day, scope) or query_excluded.get(key)
    return f"fact {key!r} cannot be quoted: {r or 'not valid for this scope and date'}"


def build(lines: list[dict], by_key: dict, known: dict, excluded: dict, day: date, scope: dict,
          currency: str | None, internal_ok: bool) -> dict:
    """`by_key`: facts 05 returned for this scope and day; `known`: every fact (for the reason)."""
    out, total, cur, disclosures, notes, internal = [], Decimal(0), (currency or "").upper() or None, [], [], False
    for ln in lines:
        key = ln["fact_key"]
        f = by_key.get(key)
        if f is None or F.classify(f, day, scope) is not None:
            raise QuoteError(_refusal(key, known, excluded, day, scope))
        sens = f.get("sensitivity") or "public"
        if sens == "restricted":
            raise QuoteError(f"fact {key!r} is restricted and can never be quoted")
        if sens != "public":
            if not internal_ok:
                raise QuoteError(f"fact {key!r} is {sens}: only public facts can be quoted "
                                 "(allow_internal with the X-Internal-Quote header is for a confidential quote)")
            internal = True
        v = f.get("value")
        if f.get("fact_type") != "price" or isinstance(v, bool) or not isinstance(v, (int, float, Decimal)) or not f.get("currency"):
            raise QuoteError(f"fact {key!r} is not a price with a numeric value and a currency")
        c = str(f["currency"]).upper()
        if cur is None:
            cur = c
        if c != cur:
            raise QuoteError(f"mixed currencies ({cur} and {c}); quote one currency at a time")
        price = Decimal(str(v))
        qty = Decimal(ln["quantity"])
        nights = ln.get("nights")
        guests = ln.get("guests")
        mult = qty
        if nights:
            mult *= nights
        if guests and f.get("basis") == "per_person":
            mult *= guests
        elif guests:
            notes.append(f"{key}: guests ({guests}) noted, the price is not per person so it does not multiply")
        for c_ in f.get("conditions") or []:
            if guests and c_.get("key") == "guests" and c_.get("op") == "=" and c_.get("value") != guests:
                notes.append(f"{key}: the price is for {c_.get('value')} guests, not {guests}")
        if nights and f.get("unit") != "night" and f.get("basis") != "per_night":
            notes.append(f"{key}: nights given but the price is not per night; check the multiplication")
        line_total = price * mult
        total += line_total
        subj = F.subject_ref(f) or key
        parts = [f"{qty:g}"] + ([f"{nights} nights"] if nights else []) + ([f"{guests} guests"] if guests and f.get("basis") == "per_person" else [])
        sum_text = f"{subj}: {' × '.join(parts)} × {fmt(price, cur)} = {fmt(line_total, cur)}"
        disc = [str(d) for d in f.get("required_disclosures") or [] if str(d).strip()]
        for d in disc:
            if d not in disclosures:
                disclosures.append(d)
        out.append({"fact_key": key, "subject": subj, "unit_price": float(price) if price % 1 else int(price),
                    "quantity": int(qty), "nights": nights, "guests": guests,
                    "line_total": float(line_total) if line_total % 1 else int(line_total),
                    "value_text": f.get("value_text"), "disclosures": disc, "_text": sum_text})
    text_lines = [o.pop("_text") for o in out]
    text_lines.append(f"Total: {fmt(total, cur or '')}" if cur else "Total: 0")
    if disclosures:
        text_lines.append("Note: " + "; ".join(disclosures))
    if internal:
        text_lines.insert(0, CONFIDENTIAL)
        notes.append("includes an internal rate: " + CONFIDENTIAL)
    return {"lines": out, "total": float(total) if total % 1 else int(total), "currency": cur,
            "text": "\n".join(text_lines), "confidential": internal, "notes": notes}
