"""Slots in the shapes chatbots rewrite them to, and when a slot is blocked."""
from datetime import date

import pytest

from app import slots

from . import data

DAY = date.fromisoformat(data.PUBLISH)
SCOPE = {"sites": ["porthleven"], "channels": ["linkedin"]}


def setup():
    facts = data.business("bikes")
    known = {f["key"]: f for f in facts}
    snap_keys = ("day-hire-porthleven", "ebike-day", "insurance", "partner-rate")
    snapshot = {k: dict(known[k]) for k in snap_keys}
    return snapshot, known


@pytest.mark.parametrize("written", [
    "[[day-hire-porthleven]]", r"\[\[day-hire-porthleven\]\]", "[ [day-hire-porthleven] ]", "{{day-hire-porthleven}}",
    "⟦day-hire-porthleven⟧", "【day-hire-porthleven】", "[day-hire-porthleven]", "[[Day_Hire_Porthleven]]",
    "[[day hire porthleven]]", "{{ DAY-HIRE-PORTHLEVEN }}", "[[ day-hire-porthleven ]]",
])
def test_slot_variants_are_filled(written):
    snapshot, known = setup()
    r = slots.fill(f"A day on a hybrid is {written}.", snapshot, known, DAY, SCOPE)
    assert r.text == "A day on a hybrid is £28 per day."
    assert r.used == ["day-hire-porthleven"] and r.blocked == []


def test_internal_fact_is_filled_here():
    snapshot, known = setup()
    r = slots.fill("Hotel guests: [[partner-rate]].", snapshot, known, DAY, SCOPE)
    assert data.INTERNAL_SENTINEL in r.text and r.blocked == []


def test_bare_brackets_only_for_snapshot_keys_and_links_untouched():
    snapshot, known = setup()
    text = "See [the map](https://x.test/map) and [link] and [1] and [insurance]."
    r = slots.fill(text, snapshot, known, DAY, SCOPE)
    assert r.text == ("See [the map](https://x.test/map) and [link] and [1] and "
                      "third-party insurance and a helmet.")
    assert r.blocked == []


@pytest.mark.parametrize("written,reason", [
    ("[[free-parking]]", "unknown"),
    ("[[supplier-cost]]", "restricted"),
    ("[[summer-saver]]", "expired"),                # valid today, expired at the publish date
    ("[[day-hire-falmouth]]", "out_of_scope"),
    ("[[missing: winter opening hours]]", "missing"),
    ("{{first_name}}", "unknown"),
])
def test_blocked_slots(written, reason):
    snapshot, known = setup()
    r = slots.fill(f"Great news. Book now: {written}. See you soon.", snapshot, known, DAY, SCOPE)
    assert len(r.blocked) == 1 and r.blocked[0]["reason"] == reason
    assert written in r.text                        # left as written for the reviewer
    f = r.findings[0]
    assert f["label"] == "slot_blocked" and f["blocking"] is True
    assert f["sentence"] == f"Book now: {written}."


def test_changed_fact_version_blocks_the_slot():
    snapshot, known = setup()
    known["ebike-day"] = {**known["ebike-day"], "version": 2, "value_text": "£49 per day"}
    r = slots.fill("E-bikes are [[ebike-day]].", snapshot, known, DAY, SCOPE)
    assert r.blocked[0]["reason"] == "changed" and "£49" not in r.text


def test_retired_fact_in_snapshot_blocks():
    snapshot, known = setup()
    del known["insurance"]
    r = slots.fill("Includes [[insurance]].", snapshot, known, DAY, SCOPE)
    assert r.blocked[0]["reason"] == "retired"


def test_scope_unspecified_blocks_a_site_fact_for_a_task_without_site():
    snapshot, known = setup()
    r = slots.fill("From [[day-hire-porthleven]].", {}, known, DAY, {"channels": ["linkedin"]})
    assert r.blocked[0]["reason"] == "scope_unspecified"
