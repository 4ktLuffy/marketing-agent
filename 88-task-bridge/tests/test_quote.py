"""POST /quote: exact Decimal sums from public facts valid on the day, with disclosures."""
from .conftest import AUTH
from .data import fact

H = {"X-Internal-Quote": "yes", **AUTH}


def price(key, ref, value, cur="USD", unit="night", **kw):
    return {**fact(key, f"{ref}: {value}", f"{value} {cur} per {unit}", subject={"kind": "variant", "ref": ref},
                   fact_type="price", attribute="rate", value=value, unit=unit, currency=cur, **kw), "version": 1}


def setup(stack):
    stack.facts = [
        price("room", "Brand B Room", 75, basis="per_room", required_disclosures=["per room per night, 2 sharing"]),
        price("crate", "Yirga coffee", 17.5, unit="crate", required_disclosures=["VAT excluded"]),
        price("kora-room", "Local Room", 1700, cur="XKR", basis="per_room"),
        price("tour-rate", "Operator rate", 55, basis="per_room", sensitivity="internal"),
        price("old-room", "Old Room", 60, valid_to="2026-08-31"),
        price("secret", "Cost", 9, sensitivity="restricted"),
        price("person", "Tour", 20, unit="person", basis="per_person"),
    ]


def post(client, lines, headers=AUTH, **kw):
    return client.post("/quote", json={"scope": {}, "publish_on": "2026-10-06", "lines": lines, **kw}, headers=headers)


def test_rooms_times_nights_and_disclosures(client, stack):
    setup(stack)
    r = post(client, [{"fact_key": "room", "quantity": 20, "nights": 3}])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["total"] == 4500 and out["currency"] == "USD"
    ln = out["lines"][0]
    assert (ln["unit_price"], ln["quantity"], ln["nights"], ln["line_total"]) == (75, 20, 3, 4500)
    assert ln["disclosures"] == ["per room per night, 2 sharing"] and ln["subject"] == "Brand B Room"
    assert "Brand B Room: 20 × 3 nights × $75 = $4,500" in out["text"]
    assert "Total: $4,500" in out["text"] and "per room per night, 2 sharing" in out["text"]
    assert "confidential" not in out["text"]


def test_crates_decimals_and_total_of_several_lines(client, stack):
    setup(stack)
    out = post(client, [{"fact_key": "crate", "quantity": 3}, {"fact_key": "person", "quantity": 1, "guests": 4}]).json()
    assert out["lines"][0]["line_total"] == 52.5
    assert out["lines"][1]["line_total"] == 80            # per person: x4 guests
    assert out["total"] == 132.5
    assert "VAT excluded" in out["text"] and "Total: $132.50" in out["text"]


def test_kora_total_text(client, stack):
    setup(stack)
    out = post(client, [{"fact_key": "kora-room", "quantity": 20, "nights": 2}]).json()
    assert out["total"] == 68000 and "= 68,000 kora" in out["text"] and out["currency"] == "XKR"


def test_internal_fact_refused_by_default_and_without_header(client, stack):
    setup(stack)
    r = post(client, [{"fact_key": "tour-rate", "quantity": 1}])
    assert r.status_code == 422 and "internal" in r.text
    assert post(client, [{"fact_key": "tour-rate", "quantity": 1}], allow_internal=True).status_code == 422   # no header
    assert post(client, [{"fact_key": "tour-rate", "quantity": 1}], headers=H).status_code == 422             # no flag


def test_internal_allowed_with_flag_and_header_is_marked_confidential(client, stack):
    setup(stack)
    r = post(client, [{"fact_key": "tour-rate", "quantity": 10, "nights": 2}], headers=H, allow_internal=True)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["total"] == 1100 and out["confidential"] is True
    assert out["text"].splitlines()[0] == "confidential: do not post publicly"


def test_restricted_never(client, stack):
    setup(stack)
    r = post(client, [{"fact_key": "secret", "quantity": 1}], headers=H, allow_internal=True)
    assert r.status_code == 422


def test_mixed_currency_is_422(client, stack):
    setup(stack)
    r = post(client, [{"fact_key": "room", "quantity": 1}, {"fact_key": "kora-room", "quantity": 1}])
    assert r.status_code == 422 and "mixed currencies" in r.text
    assert post(client, [{"fact_key": "kora-room", "quantity": 1}], currency="USD").status_code == 422


def test_expired_or_unknown_fact_refused(client, stack):
    setup(stack)
    r = post(client, [{"fact_key": "old-room", "quantity": 2}])
    assert r.status_code == 422 and "expired" in r.text
    assert post(client, [{"fact_key": "nope", "quantity": 2}]).status_code == 422


def test_quote_needs_key_and_lines(client, stack):
    setup(stack)
    assert client.post("/quote", json={"lines": [{"fact_key": "room", "quantity": 1}]}).status_code == 401
    assert post(client, []).status_code == 422
    assert post(client, [{"fact_key": "room", "quantity": 0}]).status_code == 422


def test_quote_total_agrees_with_check(client, stack):
    """The text a quote returns passes the arithmetic check as a match."""
    setup(stack)
    out = post(client, [{"fact_key": "room", "quantity": 20, "nights": 3}]).json()
    sentence = "20 rooms for 3 nights at $75 = $4,500."
    r = client.post("/check", json={"text": sentence, "publish_on": "2026-10-06"}, headers=AUTH).json()
    assert out["total"] == 4500
    assert not any(f["label"] == "conflict_or_expired" for f in r["findings"])
