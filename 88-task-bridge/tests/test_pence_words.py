"""Pounds and pence in words: "nine pounds fifty" is £9.50, not £9."""
import pytest

from app import evidence as E


def money(text):
    return [v.key[1] for v in E.extract(text) if v.kind == "money"]


@pytest.mark.parametrize("text,want", [
    ("Nine pounds fifty buys a lesson.", ["9.5"]),
    ("Seven pound fifty a drop.", ["7.5"]),
    ("It costs twelve pounds ninety-nine.", ["12.99"]),
    ("Forty quid fifty is not how we'd say it, but eight quid twenty is.", ["40.5", "8.2"]),
])
def test_pence_after_pounds(text, want):
    assert money(text) == want


@pytest.mark.parametrize("text,want", [
    ("Five pounds twenty minutes later it was gone.", ["5"]),      # a unit follows: not pence
    ("Thirty-five quid per user.", ["35"]),
    ("Two pounds three times a week.", ["2"]),
    ("nine pounds of flour", []),                                    # a weight
])
def test_pence_negative_controls(text, want):
    assert money(text) == want
