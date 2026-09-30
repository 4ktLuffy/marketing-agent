"""Evidence labels: one test per label, and clean paraphrases that must raise nothing blocking."""
from datetime import date

import pytest

from app import evidence, slots

from . import data

PUBLISH = date.fromisoformat(data.PUBLISH)
TODAY = date.fromisoformat(data.TODAY)
SCOPES = {
    "bikes": {"sites": ["porthleven"], "channels": ["linkedin"]},
    "saas": {"plan_tiers": ["pro"], "channels": ["linkedin"]},
    "bakery": {"regions": ["truro"], "channels": ["instagram"]},
}


def check(text, biz="bikes", day=PUBLISH, scope=None, slot_used=None):
    findings, used = evidence.check_text(text, data.business(biz), day, scope or SCOPES[biz], slot_used)
    return findings


def labels(findings, blocking_only=False):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"] or not blocking_only]


def blocking(findings):
    return [f for f in findings if f["blocking"]]


# ---------- negative controls: clean drafts and truthful paraphrases, 3 businesses

CLEAN = {
    "bikes": [
        "Autumn is a lovely time to ride the coast path from our Porthleven shop. A hybrid bike for the whole day "
        "is £28, and every hire comes with third-party insurance and a helmet. Fancy less effort on the hills? "
        "E-bikes are £45 per day (riders must be 16 or over). Book at the shop or give us a call.",
        "Hybrid day hire at Porthleven: 28 GBP.\nE-bike day hire: £45.00 a day, riders must be 16 or over.\n"
        "Insurance and a helmet are included with every bike.",
    ],
    "saas": [
        "Quillstack Pro is £12 per user per month, billed annually. Your data sits with a team that is ISO 27001 "
        "certified. Try any plan free for 14 days before you decide. We back it with 99.9% monthly uptime, see our SLA.",
        "Scheduling shouldn't eat your week. With Quillstack Pro at £12 a user each month (billed annually) your "
        "whole team books meetings in one place. Start with a fourteen-day trial.",
    ],
    "bakery": [
        "Our award-winning Greenbank Sourdough is £4.50 a loaf. Free delivery on orders over £25 within Truro. "
        "Pop in between 7am and 3pm, Tuesday to Saturday.",
        "Fresh bread every morning from 7am. The Greenbank Sourdough won Gold at the 2025 Cornwall Baking Awards, "
        "and a loaf is still just £4.50.",
    ],
}


@pytest.mark.parametrize("biz,text", [(b, t) for b, ts in CLEAN.items() for t in ts])
def test_clean_paraphrase_has_no_blocking_finding(biz, text):
    findings = check(text, biz)
    assert blocking(findings) == [], blocking(findings)
    assert sum(f["label"] == "review" for f in findings) <= 1


def test_clean_draft_with_slots_is_checked_on_the_filled_text():
    facts = data.business("bikes")
    known = {f["key"]: f for f in facts}
    snap = {k: known[k] for k in ("day-hire-porthleven", "ebike-day", "insurance", "partner-rate")}
    text = ("Hybrids are [[day-hire-porthleven]] at Porthleven. E-bikes are {{ebike-day}} (riders must be 16 or "
            "over). Hotel guests get [[partner-rate]].")
    fill = slots.fill(text, snap, known, PUBLISH, SCOPES["bikes"])
    findings, used = evidence.check_text(fill.text, facts, PUBLISH, SCOPES["bikes"], fill.used)
    assert blocking(findings) == []
    assert {"day-hire-porthleven", "ebike-day", "partner-rate"} <= set(used)
    assert ("match", "partner-rate") in labels(findings)


# ---------- one test per label


def test_match_quotes_the_fact():
    f = check("Hybrid day hire is £28.")
    assert labels(f) == [("match", "day-hire-porthleven")]
    assert f[0]["quote"] == "£28 per day" and f[0]["blocking"] is False


def test_wrong_scope_value_from_another_site():
    f = check("Hybrid day hire is £32.")
    assert ("wrong_scope", "day-hire-falmouth") in labels(f, True)
    assert "falmouth" in f[0]["detail"]


def test_wrong_scope_claim_phrase_from_another_plan():
    f = check("Quillstack Pro now includes single sign-on.", "saas")
    assert ("wrong_scope", "sso") in labels(f, True)


def test_wrong_scope_region_offer():
    f = check("Free delivery on orders over £25.", "bakery", scope={"regions": ["falmouth"], "channels": ["instagram"]})
    assert labels(f, True) == [("wrong_scope", "free-delivery-truro")]


def test_publish_date_expiry_valid_today_expired_on_publish_day():
    text = "Save with 20% off weekday hires."
    assert labels(check(text, day=TODAY)) == [("match", "summer-saver")]
    f = check(text, day=PUBLISH)
    assert labels(f, True) == [("conflict_or_expired", "summer-saver")]
    assert "2026-09-30" in f[0]["detail"]


def test_conflict_value_differs_from_the_same_subject():
    f = check("E-bike day hire is now £40, riders must be 16 or over.")
    assert labels(f, True) == [("conflict_or_expired", "ebike-day")]
    assert f[0]["quote"] == "£45 per day"


def test_expired_offer_named_without_a_value():
    f = check("Grab a Harvest Box while you still can.", "bakery")
    assert labels(f, True) == [("conflict_or_expired", "harvest-box")]


def test_deadline_before_publish_date():
    f = check("Our autumn deal ends 1 October.")
    assert any(x["label"] == "conflict_or_expired" and "before the publish date" in x["detail"] for x in f)


def test_no_source_claim_keyword():
    f = check("We are the only certified bike hire in town.")
    assert labels(f, True) == [("no_source", None)] and f[0]["blocking"] is True


def test_no_source_price():
    f = check("Parking next to the shop costs £5.")
    assert labels(f, True) == [("no_source", None)]


def test_forbidden_phrase():
    f = check("We are the cheapest in Cornwall!")
    assert labels(f, True) == [("forbidden_phrase", "no-cheapest")]
    assert f[0]["detail"] == "no price survey"


def test_forbidden_phrase_security_claim():
    f = check("Quillstack is SOC 2 compliant.", "saas")
    assert ("forbidden_phrase", "iso") in labels(f, True)


def test_allowed_phrasing_exempts_only_the_named_subject():
    assert blocking(check("The award-winning Greenbank Sourdough is back.", "bakery")) == []
    f = check("Try our award-winning croissants.", "bakery")
    assert labels(f, True) == [("forbidden_phrase", "no-award-generic")]


def test_missing_disclosure_by_value_and_by_slot():
    f = check("E-bikes are £45 per day.")
    assert labels(f, True) == [("missing_disclosure", "ebike-day")]
    assert "riders must be 16 or over" in f[-1]["detail"]
    f = check("Pro is £12 per user per month.", "saas")
    assert ("missing_disclosure", "pro-price") in labels(f, True)
    f = check("Hotel guests get £13.37 partner rate.", slot_used=["partner-rate"])
    assert blocking(f) == []


def test_review_is_never_blocking():
    f = check("Most loops take about 3 hours.")
    assert labels(f) == [("review", None)] and f[0]["blocking"] is False
    f = check("Google Calendar sync is coming along nicely.", "saas")
    assert labels(f) == [("review", "calendar-beta")] and not blocking(f)


def test_money_formats_compare_by_amount_and_currency():
    keys = {v.key for t in ("£1,200", "1200 GBP", "£1.2k", "1,200 pounds", "GBP 1200.00") for v in evidence.extract(t)}
    assert keys == {("money", "1200", "GBP")}
    assert evidence.extract("$1,200")[0].key == ("money", "1200", "USD")
    assert evidence.extract("50p")[0].key == ("money", "0.5", "GBP")
    assert evidence.extract("4.50")== []                            # bare numbers are not claims


def test_restricted_value_is_never_a_match():
    f = check("Each bike costs us £9.91 supplier cost.")
    assert ("match", "supplier-cost") not in labels(f)
    assert blocking(f)


def test_sentence_split_keeps_prices_and_abbreviations():
    from app.facts import sentence_spans
    text = "Dr. Osei sees you at 9am. Loaves are £4.50 each! Open e.g. Tue.\nNew line here"
    parts = [text[a:b] for a, b in sentence_spans(text)]
    assert parts == ["Dr. Osei sees you at 9am.", "Loaves are £4.50 each!", "Open e.g. Tue.", "New line here"]


@pytest.mark.parametrize("text", ["We approved the new route map.", "Open 7 days a week.", "The fun never ends.",
                                  "Call us on 01326 555 123.", "Established in 2012.", "Our team is made up of locals."])
def test_everyday_sentences_do_not_block(text):
    assert blocking(check(text)) == []


@pytest.mark.parametrize("text", ["FDA-approved frames.", "Approved by the council.", "Clinically approved saddles."])
def test_approval_claims_need_a_source(text):
    assert labels(check(text), True) == [("no_source", None)]
