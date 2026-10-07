"""Invented extras (amenities, offers, policies, claims, distances, capacities) a chatbot adds."""
from datetime import date

import pytest

from app import extras

DAY = date(2026, 10, 9)


def fact(key, text, **kw):
    return {"key": key, "subject": {"kind": "service", "ref": kw.pop("ref", key)}, "fact_type": "claim", "text": text,
            "value_text": kw.pop("value_text", None), "status": "active", "sensitivity": kw.pop("sensitivity", "public"),
            "scope": {"sites": [], "regions": [], "channels": [], "segments": [], "plan_tiers": [], "variants": []}, **kw}


FACTS = [fact("rooms", "Rooms are $75 per room per night for two guests, breakfast included."),
         fact("boat", "Boat ride on the lake: a 90-minute excursion.", value_text="90 minutes"),
         fact("halls", "Two conference halls: the Cedar Hall and the Birch Room.")]


def blocked(text, facts=FACTS):
    return [f for f in extras.check(text, facts, DAY, {}) if f["blocking"]]


@pytest.mark.parametrize("text", [
    "Every room has free Wi-Fi.",
    "All rooms are air-conditioned.",
    "Kids under 12 stay free when sharing with parents.",
    "Free airport pick-up for stays of two nights.",
    "Our halls seat up to 120 delegates.",
    "The hall comes with a projector for your slides.",
    "Book 3 nights and get the 4th free this month.",
    "Free cancellation up to 48 hours before arrival.",
    "Free delivery on orders over 10 crates.",
    "We are an eco-certified lodge.",
    "Just 15 minutes from the airport.",
    "Enjoy sunset drinks at our rooftop bar.",
])
def test_invented_extras_block(text):
    assert blocked(text), text


@pytest.mark.parametrize("text", [
    "Rooms are $75 per room per night for two guests, breakfast included.",
    "The boat ride is 90 minutes from the lodge jetty.",        # the 90-minute duration is a fact
    "There is no Wi-Fi in the tents, so you can switch off.",  # negated
    "Do you need an airport transfer?",                         # a question
    "Two conference halls: the Cedar Hall and the Birch Room.",
])
def test_true_or_safe_lines_pass(text):
    assert not blocked(text), text


def test_a_fact_saying_it_makes_it_true():
    facts = FACTS + [fact("wifi", "Free Wi-Fi in every room."), fact("transfer", "Airport transfers on request.")]
    assert not blocked("Every room has free Wi-Fi.", facts)
    assert not blocked("Ask us about an airport transfer.", facts)


def test_internal_or_expired_facts_do_not_support():
    facts = FACTS + [fact("wifi", "Free Wi-Fi in every room.", sensitivity="internal"),
                     fact("parking", "Free parking.", valid_to="2026-09-01")]
    assert blocked("Every room has free Wi-Fi.", facts) and blocked("Free parking for guests.", facts)


def test_vague_distance_is_only_review():
    got = extras.check("We are a short drive from the airport.", FACTS, DAY, {})
    assert got and not got[0]["blocking"] and got[0]["label"] == "review"


@pytest.mark.parametrize("text", [
    "Order 10 crates and get 1 crate free.",
    "We collect your empty bottles and crates free of charge.",
    "We are eco-certified and carbon-neutral, so your stay leaves no footprint.",   # 'no' later is not a negation of it
    "Our garden is certified child-safe.",
    "Pay within 30 days on our trade credit terms.",
    "Hall hire is free for groups of 20.",
    "Download our mobile app to order.",
])
def test_more_invented_extras_block(text):
    assert blocked(text), text


def test_alcohol_free_does_not_support_free_offers():
    facts = FACTS + [fact("brand-f", "Brand F 0.0% is an alcohol-free beer.")]
    assert blocked("Order 10 crates and get 1 crate free.", facts)


@pytest.mark.parametrize("text", [
    "Serving Riverton since 1998.",
    "Our crates are cheaper than any supermarket.",
    "We'll beat any other distributor's price.",
    "The largest beer distributor in the south.",
    "Lake views guaranteed from every room.",
])
def test_invented_claims_block(text):
    assert blocked(text), text


@pytest.mark.parametrize("text", [
    "Book by 30 September 2026 for October.",       # a date, not a founding claim
    "Our largest room is the Family Suite.",        # within the business's own range
])
def test_claim_negatives(text):
    assert not blocked(text), text


# real-7: offers in a frame ("X is included", "complimentary X", "every guest receives X", weekday events)
@pytest.mark.parametrize("text", [
    "Binoculars are available to borrow at reception for every guest.",
    "Bridal suite decorations are included at no extra charge.",
    "Complimentary tea and coffee breaks are served in the halls all day.",
    "Every guest receives a printed checklist booklet of the birds.",
    "A wildlife talk is given every Friday evening in the lounge.",
    "Dance the evening away to a live band on Saturdays in the garden.",
    "We also design and print your first bar menu at no cost.",
    "Order 10 crates and get an extra crate free.",
    "Our order line answers until midnight every night.",
    "Every hotel account has a named account manager.",
    "There is a photography hide by the lake.",
    "It is the only lodge in southern Portugal with Cabin rooms.",
])
def test_framed_extras_block(text):
    assert blocked(text), text


@pytest.mark.parametrize("text", [
    "Breakfast is included.",
    "Rates are per room per night, and breakfast is included.",
    "Net contract rates for tour operators are available on request.",
    "Keeping your home pest-free this season.",
    "Is there an alcohol-free option?",
    "Subscribe for a new bookkeeping tip every Tuesday.",
    "Sample prices: rooms are $75 per room per night.",
    "The boat ride is included with the lake excursion.",
    "Wi-Fi is not available in the halls.",
])
def test_framed_extras_negative_controls(text):
    assert not blocked(text), text


def test_framed_extra_backed_by_a_fact_passes():
    facts = FACTS + [fact("binoculars", "Binoculars can be borrowed free at reception.")]
    assert not blocked("Binoculars are available to borrow at reception.", facts)
    facts = FACTS + [fact("talk", "A wildlife talk is held every Friday at 7pm.")]
    assert not blocked("A wildlife talk is given every Friday evening.", facts)


# real-8: service promises in plain statements, flagged only when their own words are absent from the facts
@pytest.mark.parametrize("text", [
    "Our merchandiser visits weekly to arrange your shelves.",
    "We lend chilled storage tubs for your wedding day.",
    "Our team trains your shop staff on stock rotation.",
    "A sunrise canoe paddle on Lake Lumo is arranged privately for newlyweds.",
    "Guided night walks with a naturalist are offered after dinner.",
])
def test_service_promises_block(text):
    assert blocked(text), text


@pytest.mark.parametrize("text", [
    "Reply to this email and we'll arrange your first collection.",
    "Reply with your quantities and we'll send a quote.",
    "Genna is near, and we are thinking of you.",
    "Settling in takes time, and every child is different.",
    "Thank you for your honest feedback, and we are sorry your breakfast was slow.",
    "We hold ISO 27001 certification, so security is covered by an independent standard.",
    "Our team can't wait to welcome you to the lake.",
    "We offer a 90-minute boat ride on the lake.",
])
def test_service_promise_negative_controls(text):
    assert not blocked(text), text


def test_service_promise_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("PROMISE_CHECK", "off")
    assert not blocked("Our merchandiser visits weekly to arrange your shelves.")


@pytest.mark.parametrize("text", [
    "Picture your team restored by our world-class spa.",
    "Expert local guides through our Omo Tours partnership.",
    "Picture your team energized by morning swims in crystal lakes.",
    "We host retreats of every size.",
])
def test_small_model_additions_block(text):
    assert blocked(text), text


def test_swimming_pool_is_not_lake_swimming():
    assert not blocked("Swimming, volleyball and mini basketball are open all day.")


@pytest.mark.parametrize("text", [
    "Full range. One partner. One delivery. No middleman.",
    "Boat rides and hikes, all 90 mins to 4 hours away.",
])
def test_held_out_trial_additions_block(text):
    assert blocked(text), text


def test_a_real_excursion_length_is_not_a_distance():
    assert not blocked("The boat ride on the lake is a 90-minute excursion.")


@pytest.mark.parametrize("text", [
    "Limited availability for the holidays.",
    "Last spots filling fast!",
    "Book NOW before it's gone!",
    "Your competitors are stocking now.",
    "Maximise every shelf and tap with our range.",
])
def test_round5_urgency_rivals_and_taps_block(text):
    assert blocked(text), text
