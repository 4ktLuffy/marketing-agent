"""Required disclosures written in an equivalent way still count ("plus VAT" = "excl. VAT")."""
from app import evidence

from .test_r5 import DAY  # noqa: F401  (same fixed day as the other rule tests)
from .data import fact


def labels(text, facts):
    findings, _ = evidence.check_text(text, facts, DAY, {})
    return [f["label"] for f in findings if f.get("blocking")]


PRICE = fact("site-price", "The Studio website package costs £1,800 excluding VAT.", "£1,800",
             value=1800, currency="GBP", fact_type="price", required_disclosures=["excl. VAT"],
             subject={"kind": "package", "ref": "Studio website package"})
GROSS = fact("gross-price", "The Grove hamper costs £60 including VAT.", "£60", value=60, currency="GBP",
             fact_type="price", required_disclosures=["incl. VAT"], subject={"kind": "product", "ref": "Grove hamper"})


def test_vat_disclosure_equivalents_are_accepted():
    for ok in ("The Studio website package is £1,800 plus VAT.", "The Studio website package is £1,800 excluding VAT.",
               "The Studio website package is £1,800 ex VAT.", "The Studio website package is £1,800 + VAT."):
        assert "missing_disclosure" not in labels(ok, [PRICE]), ok
    for ok in ("The Grove hamper is £60 including VAT.", "The Grove hamper is £60 inc. VAT.", "The Grove hamper is £60 incl VAT."):
        assert "missing_disclosure" not in labels(ok, [GROSS]), ok


def test_the_opposite_or_no_vat_note_still_fails():
    assert "missing_disclosure" in labels("The Studio website package is £1,800.", [PRICE])
    assert "missing_disclosure" in labels("The Studio website package is £1,800 including VAT.", [PRICE])
    assert "missing_disclosure" in labels("The Grove hamper is £60 plus VAT.", [GROSS])
