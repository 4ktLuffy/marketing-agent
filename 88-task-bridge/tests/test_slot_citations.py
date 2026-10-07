"""Chatbots cite their source after a sentence ("… sauna. [[cave-spa]]"): a citation, not a slot to fill."""
from datetime import date

from app import slots

DAY = date(2026, 10, 16)


def fact(key, text, value_text=None, sensitivity="public"):
    return {"key": key, "subject": {"kind": "service", "ref": key}, "fact_type": "claim", "text": text,
            "value_text": value_text, "status": "active", "sensitivity": sensitivity, "scope": {}, "version": 1}


SPA = fact("cave-spa", "The Cave Spa has a steam room and a sauna.")
VIEWS = fact("views", "The lodge overlooks Lake Lumo and Lake Ora from a cliff.")
VOL = fact("vol", "Brand B 33cl: 38,450 crates sold.", "38,450 crates", sensitivity="internal")
RATE = fact("rate", "Queen Room: $111 per room per night.", "$111 per room per night")
SNAP = {f["key"]: f for f in (SPA, VIEWS, VOL, RATE)}


def run(text):
    return slots.fill(text, SNAP, SNAP, DAY, {})


def test_trailing_citations_are_removed_not_blocked():
    r = run("Relax in the Cave Spa with a steam room and sauna. [[cave-spa]]")
    assert r.text == "Relax in the Cave Spa with a steam room and sauna." and not r.findings and r.used == ["cave-spa"]
    r = run("Brand B 33cl is our best seller (July to September 2026 sales). [[cave-spa]] [[vol]]")
    assert r.text == "Brand B 33cl is our best seller (July to September 2026 sales)." and "38,450" not in r.text


def test_inline_citation_after_words_that_say_the_fact():
    r = run("From a cliff the lodge overlooks Lake Lumo and Lake Ora [[views]], so bring a camera.")
    assert "[[views]]" not in r.text and not r.findings


def test_a_content_slot_is_still_filled_or_blocked():
    assert run("A Queen Room is [[rate]], breakfast included.").text == "A Queen Room is $111 per room per night, breakfast included."
    r = run("Our spa: [[cave-spa]]")
    assert r.findings and r.findings[0]["label"] == "slot_blocked"


def test_the_pack_form_copied_back_is_not_doubled():
    assert run("Queen Room (Lake View): [[rate]] = $111 for two guests.").text == \
        "Queen Room (Lake View): $111 per room per night for two guests."
    assert run("Queen Room: [[rate]] = $99 for two guests.").text == "Queen Room: $111 per room per night = $99 for two guests."


def test_citations_between_sentences_and_on_bullets():
    r = run("Brand B 33cl is our best seller (July to September 2026 sales). [[cave-spa]] [[vol]] Brand F is alcohol-free.")
    assert r.text == "Brand B 33cl is our best seller (July to September 2026 sales). Brand F is alcohol-free." and not r.findings
    r = run("- Relax in the Cave Spa with steam room and sauna [[cave-spa]]\n- Lake views")
    assert "[[cave-spa]]" not in r.text and not r.findings
    assert run("- See all room types here: [[cave-spa]]").findings          # a label: the slot is the content
