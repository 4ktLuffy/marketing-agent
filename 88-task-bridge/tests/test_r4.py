"""Fourth round of general rules (invented businesses, not the eval companies): the last piece
without a heading in the splitter; partner / accreditation tiers; identifiers with hyphenated
prefixes; conformity marks limited to another product line; "N-day" for a working-days fact; a
segment's price offered by the segment's name; "by a <credential> <profession>"; "<Place>'s
<adj>est". Every rule has negative controls."""
from datetime import date

import pytest

from app import evidence, paste

from .data import fact

DAY = date(2031, 5, 12)


def run(text, facts, scope=None):
    findings, _ = evidence.check_text(text, facts, DAY, scope or {})
    return findings


def blocking(findings):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"]]


def labels(findings):
    return [(f["label"], f["fact_key"]) for f in findings]


# ---------- 1. splitter: the last piece without a heading

IG, EM, SMS, GB, FB, X = ({"key": k, "channel": c} for k, c in (
    ("ig", "instagram"), ("em", "email"), ("sms", "sms"), ("gb", "google_business"), ("fb", "facebook"), ("x", "x")))
POST = "Fresh sourdough at the Quayside ovens from 7am. 🍞"
MAIL = "Subject: Your loyalty loaf\n\nHi,\n\nYour tenth loaf is on us this month.\n\nThanks,\nQuayside Bakery"
TEXT = "Quayside: tenth loaf free with your card this month. Reply STOP to opt out."


def got(res):
    return {s["piece_key"]: s["text"] for s in res.split}


def test_email_without_heading_starts_at_its_subject_line():
    res = paste.split(f"Here you go!\n\nInstagram\n{POST}\n\n{MAIL}", [IG, EM])
    assert got(res) == {"ig": POST, "em": MAIL}
    assert any("piece 2 (email) had no heading" in p and "Subject" in p for p in res.problems)


def test_text_after_the_email_sign_off_is_the_one_missing_piece():
    res = paste.split(f"1. Instagram\n{POST}\n\n2. Email\n{MAIL}\n\n{TEXT}", [IG, EM, SMS])
    assert got(res) == {"ig": POST, "em": MAIL, "sms": TEXT}
    assert any("piece 3 (sms) had no heading; the text after the email sign-off was used" in p for p in res.problems)


def test_sign_off_rule_is_not_limited_to_short_channels():
    gb = "Fresh sourdough every morning from 7am at our Quayside shop.\nOrder ahead online."
    res = paste.split(f"# Instagram\n{POST}\n\n# Email\n{MAIL}\n\n{gb}", [IG, EM, GB])
    assert got(res)["gb"] == gb and got(res)["em"] == MAIL


def test_email_by_subject_then_sms_after_its_sign_off():
    res = paste.split(f"Instagram:\n{POST}\n\n{MAIL}\n\n{TEXT}", [IG, EM, SMS])
    assert got(res) == {"ig": POST, "em": MAIL, "sms": TEXT}
    assert len([p for p in res.problems if "had no heading" in p]) == 2


def test_one_line_that_fits_the_only_missing_short_piece():
    fb = "Our ovens are on from 7am.\n\nSourdough, rye and seeded loaves all week."
    res = paste.split(f"Facebook:\n{fb}\n\n{TEXT}", [FB, SMS])
    assert got(res) == {"fb": fb, "sms": TEXT}
    assert any("fits a sms message" in p for p in res.problems)


def test_chatbot_sign_off_after_the_email_is_not_a_piece():
    res = paste.split(f"Instagram:\n{POST}\n\nEmail:\n{MAIL}\n\nLet me know if you'd like a shorter version!",
                      [IG, EM, SMS])
    assert "sms" not in got(res)
    assert any("piece 3 (sms) not found" in p for p in res.problems)


@pytest.mark.parametrize("last", [
    "Our ovens are on from 7am and we bake sourdough, rye, seeded and spelt loaves every single morning of the "
    "week, with pastries on Saturdays and a pizza night on Fridays from five.",       # longer than an sms
    "#sourdough #bakery #quayside",                                                      # hashtags of the post
    "Cheers, the Quayside team",                                                          # a letter sign-off
])
def test_short_shape_rule_negative_controls(last):
    res = paste.split(f"Facebook:\nOur ovens are on from 7am.\n\n{last}", [FB, SMS])
    assert "sms" not in got(res), got(res)


def test_no_guess_when_two_pieces_are_missing_after_the_sign_off():
    res = paste.split(f"Instagram:\n{POST}\n\n{MAIL}\n\n{TEXT}", [IG, EM, SMS, X])
    assert "sms" not in got(res) and "x" not in got(res)
    assert any("piece 3 (sms) not found" in p for p in res.problems)
    assert any("piece 4 (x) not found" in p for p in res.problems)


def test_subject_line_right_under_a_heading_is_not_moved():
    res = paste.split(f"LinkedIn:\nSubject: Autumn loaves\n\nFresh rye this week.", [{"key": "li", "channel": "linkedin"}, EM])
    assert "em" not in got(res)


def test_sign_off_without_a_blank_line_after_the_name_splits_nothing():
    mail = "Subject: Hello\n\nHi,\n\nNew loaves.\n\nThanks,\nQuayside Bakery\nReply STOP to opt out."
    res = paste.split(f"Instagram:\n{POST}\n\nEmail:\n{mail}", [IG, EM, SMS])
    assert "sms" not in got(res) and got(res)["em"] == mail


# ---------- 2. partner / accreditation tiers

TIERS = [
    fact("pay-partner", "Harbourline Studio is a Paystack-Nova Silver Partner (partner ID PN-SP-3310).",
         "Paystack-Nova Silver Partner (partner ID PN-SP-3310)", subject={"kind": "business", "ref": "Harbourline Studio"},
         fact_type="certification", claim_class="comparative"),
    fact("green-mark", "Harbourline Studio holds EcoDesk Bronze accreditation, certificate ED-B-77120.",
         "EcoDesk Bronze accreditation (certificate ED-B-77120)", subject={"kind": "business", "ref": "Harbourline Studio"},
         fact_type="certification", claim_class="comparative"),
]


@pytest.mark.parametrize("text,key", [
    ("We're a Paystack-Nova Diamond Partner.", "pay-partner"),
    ("Harbourline holds EcoDesk Gold accreditation.", "green-mark"),
])
def test_another_tier_of_the_same_scheme_conflicts(text, key):
    f = run(text, TIERS)
    assert ("conflict_or_expired", key) in blocking(f), labels(f)
    assert ("match", key) not in labels(f)


@pytest.mark.parametrize("text,label", [
    ("We're a Paystack-Nova Silver Partner.", "match"),                 # the fact's own tier
    ("Harbourline holds EcoDesk Bronze accreditation.", "match"),
    ("We're a Stripeline Gold Partner.", "no_source"),                  # another scheme: no fact names it
])
def test_tier_negative_controls(text, label):
    f = run(text, TIERS)
    assert label in [x[0] for x in labels(f)], labels(f)
    assert "conflict_or_expired" not in [x[0] for x in labels(f)], labels(f)


# ---------- 3. identifiers with a hyphenated prefix, no "certified" in the sentence

def test_hyphenated_identifier_with_other_digits_conflicts():
    f = run("Our EcoDesk certificate number is ED-B-77102, if procurement asks.", TIERS)
    assert ("conflict_or_expired", "green-mark") in blocking(f), labels(f)


@pytest.mark.parametrize("text", [
    "Our EcoDesk certificate number is ED-B-77120, if procurement asks.",
    "Quote partner ID PN-SP-3310 at checkout.",
    "Room ED-12 is on the first floor.",
])
def test_hyphenated_identifier_negative_controls(text):
    f = run(text, TIERS)
    assert not [x for x in f if x["label"] == "conflict_or_expired"], labels(f)


# ---------- 4. a conformity mark that covers another product line

MARKS = [
    fact("mark-hinges", "Corvolan SoftClose hinges are UKCA and CE marked. Bespoke cabinets are not marked.",
         "UKCA and CE marked", subject={"kind": "product", "ref": "Corvolan SoftClose hinges"},
         fact_type="certification", attribute="conformity_marking", claim_class="safety_cert", variants=["hardware"]),
    fact("cabinet-lead", "Bespoke cabinets are made to order in 6 weeks.", "6 weeks",
         subject={"kind": "product", "ref": "Bespoke cabinets"}, fact_type="spec", value=6, unit="weeks",
         variants=["cabinets"]),
]
CAB = {"variants": ["cabinets"]}


@pytest.mark.parametrize("text", [
    "Subject: UKCA-marked cabinets from Corvolan",
    "Every cabinet we build is CE marked.",
])
def test_mark_limited_to_another_line_is_wrong_scope(text):
    f = run(text, MARKS, CAB)
    assert ("wrong_scope", "mark-hinges") in blocking(f), labels(f)


def test_mark_in_scope_matches():
    f = run("Subject: UKCA-marked hinges for your kitchen", MARKS, {"variants": ["hardware"]})
    assert not blocking(f), labels(f)


def test_mark_word_without_an_acronym_is_not_a_claim():
    f = run("We marked the handles for you and mark every drawer.", MARKS, CAB)
    assert not blocking(f), labels(f)


# ---------- 5. "N-day" for a fact that says N working days

TURN = [
    fact("proof-turnaround", "Printed proofs are sent in 3 working days from artwork approval.", "3 working days",
         subject={"kind": "service", "ref": "Printed proofs"}, fact_type="spec", attribute="turnaround", value=3,
         unit="working_days", required_disclosures=["from artwork approval"]),
]


def test_n_day_uses_the_working_days_fact_and_its_disclosure():
    f = run("Subject: 3-day printed proofs", TURN)
    assert ("missing_disclosure", "proof-turnaround") in blocking(f), labels(f)


@pytest.mark.parametrize("text", [
    "Subject: 3-day proofs, from artwork approval",     # disclosure present
    "Our 3-day festival pass is back.",                 # another thing: not tied to the fact
    "Subject: 5-day printed proofs",                    # another number: not this fact's use
])
def test_n_day_negative_controls(text):
    f = run(text, TURN)
    assert ("missing_disclosure", "proof-turnaround") not in labels(f), labels(f)


# ---------- 6. a segment's price offered by the segment's name

SEG = [
    fact("member-price", "Club members pay £18 per class.", "£18 per class", subject={"kind": "service", "ref": "Spin class (members)"},
         fact_type="price", value=18, currency="GBP", segments=["members"]),
    fact("public-price", "Drop-in spin classes cost £24.", "£24 per class", subject={"kind": "service", "ref": "Spin class"},
         fact_type="price", value=24, currency="GBP", segments=["public"]),
]
PUB = {"segments": ["public"]}


@pytest.mark.parametrize("text", [
    "Member pricing is open to everyone this month.",
    "Get members' rates on every class.",
])
def test_segment_price_by_name_is_wrong_scope(text):
    f = run(text, SEG, PUB)
    assert ("wrong_scope", "member-price") in blocking(f), labels(f)


@pytest.mark.parametrize("text,scope", [
    ("Member pricing is open to everyone this month.", {"segments": ["members"]}),   # the task is for members
    ("Public pricing starts at £24.", PUB),
    ("Our members love the new bikes.", PUB),                                       # no price word
    ("No member pricing needed: drop in for £24.", PUB),                            # negated
])
def test_segment_price_negative_controls(text, scope):
    f = run(text, SEG, scope)
    assert ("wrong_scope", "member-price") not in labels(f), labels(f)


# ---------- 7. "by a <credential> <profession>"

STAFF = [
    fact("firm-reg", "Ledgerwise is a registered bookkeeping practice.", "registered bookkeeping practice",
         subject={"kind": "business", "ref": "Ledgerwise"}, fact_type="certification"),
]


@pytest.mark.parametrize("text", [
    "Every payroll run is checked by a chartered accountant.",
    "Your books are reviewed by a certified bookkeeping specialist.",
    "All audits are signed by an accredited auditor.",
])
def test_work_done_by_a_credentialed_person_is_a_staff_claim(text):
    f = run(text, STAFF)
    assert ("no_source", None) in blocking(f), labels(f)


@pytest.mark.parametrize("text", [
    "Returns must be checked by a chartered accountant before filing.",
    "If you need it, your plan can be reviewed by a chartered accountant.",
    "Covered when installed by a qualified engineer.",
    "You may be seen by a registered nurse.",
])
def test_by_a_credential_condition_negative_controls(text):
    f = run(text, STAFF)
    assert not [x for x in f if x["label"] == "no_source"], labels(f)


# ---------- 8. "<Place>'s <adj>est"

def test_place_possessive_superlative_needs_a_fact():
    f = run("Subject: Bristol's friendliest bookkeepers", STAFF)
    assert ("no_source", None) in blocking(f), labels(f)


@pytest.mark.parametrize("text", [
    "Read Bristol's latest small-business news.",
    "The studio's guest room is free on Fridays.",
    "Ledgerwise's interest in your books is real.",
])
def test_place_possessive_superlative_negative_controls(text):
    f = run(text, STAFF)
    assert not blocking(f), labels(f)
