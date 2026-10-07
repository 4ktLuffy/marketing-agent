"""A slot's value is not repeated when the writer already typed its tail after the slot."""
import pytest

from app.slots import _trim_overlap


@pytest.mark.parametrize("value,before,after,want", [
    ("1,700 kora per crate", "Brand B: ", " per crate", "1,700 kora"),
    ("$111 per room per night", "only ", " per room per night, breakfast", "$111"),
    ("$111 per room per night", "only $", " for two", "111 per room per night"),
    ("24 x 33cl bottles", "holds ", " bottles each", "24 x 33cl"),
])
def test_overlap_is_dropped(value, before, after, want):
    assert _trim_overlap(value, before, after) == want


@pytest.mark.parametrize("value,before,after", [
    ("1,700 kora per crate", "Brand B: ", ", order now"),        # nothing repeated
    ("5 minutes", "a ", " minute walk"),                          # different word
    ("$111 per room per night", "only ", " per roommate"),        # not a whole-word repeat
    ("per crate", "", " per crate"),                              # never empties the value
])
def test_no_overlap_keeps_value(value, before, after):
    assert _trim_overlap(value, before, after) == value
