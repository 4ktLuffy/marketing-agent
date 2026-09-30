"""Third round of general rules (invented businesses, not the eval companies): heading synonyms
and position in the splitter, identifier numbers, staff credential claims, allergen lists, offers
named in part or by their benefit, and a valid price that shares words with an expired offer.
Every rule has negative controls."""
from datetime import date

import pytest

from app import channels, evidence, paste

from .data import fact

DAY = date(2031, 5, 12)


def run(text, facts, scope=None):
    findings, _ = evidence.check_text(text, facts, DAY, scope or {})
    return findings


def blocking(findings):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"]]


def labels(findings):
    return [(f["label"], f["fact_key"]) for f in findings]


# ---------- 1. splitter: synonyms, extra words, emoji, position

P3 = [{"key": "a", "channel": "facebook"}, {"key": "b", "channel": "email"}, {"key": "c", "channel": "google_business"}]
A, B, C = "Fresh loaves daily at the Mill Lane bakery.", "Subject: Bread club\n\nHello friends.", "Open 7am to 3pm."


@pytest.mark.parametrize("h1,h2,h3", [
    ("Facebook post:", "Email newsletter:", "Google Business Profile:"),
    ("### 📘 Facebook", "### ✉️ Email to members", "### 📍 GBP"),
    ("**Facebook post**", "**Newsletter**", "**Google post**"),
    ("FACEBOOK", "EMAIL", "GOOGLE"),
    ("1 · Facebook", "2 · Cold email", "3 · Google update"),
    ("💬 Facebook:", "📧 E-mail:", "📍 Google My Business:"),
])
def test_heading_synonyms_and_decorations(h1, h2, h3):
    res = paste.split(f"Here you go!\n\n{h1}\n{A}\n\n{h2}\n{B}\n\n{h3}\n{C}\n\nWant a shorter version?", P3)
    assert res.problems == [], res.problems
    assert {s["piece_key"]: s["text"] for s in res.split} == {"a": A, "b": B, "c": C}
    assert res.split[-1]["removed_post"] == "Want a shorter version?"


@pytest.mark.parametrize("task_channel,heading", [
    ("sms", "Text message:"), ("sms", "📱 SMS:"), ("website", "Landing page:"), ("website", "Product page:"),
    ("website", "Web page:"), ("instagram", "Instagram Reel:"), ("x", "X/Twitter post:"), ("blog", "Blog article:"),
    ("linkedin", "**LinkedIn post**"), ("google-business", "Google post:"),
])
def test_channel_aliases_fill_the_requested_piece(task_channel, heading):
    pieces = [{"key": "a", "channel": "facebook"}, {"key": "z", "channel": task_channel}]
    res = paste.split(f"Facebook:\n{A}\n\n{heading}\n{C}", pieces)
    assert res.problems == [], res.problems
    assert [s["text"] for s in res.split] == [A, C]


def test_canonical_channel_names():
    assert channels.canonical("google-business") == channels.canonical("Google Business Profile") == "google_business"
    assert channels.canonical("Newsletter") == "email" and channels.canonical("text message") == "sms"
    assert channels.canonical("carrier-pigeon") == "carrier_pigeon"


@pytest.mark.parametrize("line", [
    "Text us:",                          # a call to action, not a heading
    "Email us today",                    # words that are not heading words
    "Instagram and Facebook:",           # two requested channels: which one?
    "Our website:",                      # "our" is not a heading word on a plain line
])
def test_lines_that_are_not_headings_stay_text(line):
    pieces = [{"key": "a", "channel": "email"}, {"key": "b", "channel": "sms"},
              {"key": "c", "channel": "instagram"}, {"key": "d", "channel": "facebook"}, {"key": "e", "channel": "website"}]
    assert paste.parse_marker(line, {p["channel"] for p in pieces}, 5, standalone=True) is None


def test_unrequested_channel_heading_is_not_a_piece():
    # a bare "Instagram" heading when the task has no Instagram piece stays text of the email
    res = paste.split(f"Email:\n{B}\n\nInstagram\nFollow us for daily bakes.", [{"key": "b", "channel": "email"}])
    assert res.problems == [] and "Follow us" in res.split[0]["text"]


def test_bare_heading_needs_a_blank_line_before_it():
    assert paste.parse_marker("EMAIL", {"email"}, 2, standalone=False) is None
    assert paste.parse_marker("EMAIL", {"email"}, 2, standalone=True) is not None
    assert paste.parse_marker("Text", {"sms"}, 2, standalone=True) is None      # weak alias alone


def test_first_piece_without_heading_is_assigned_by_position_with_a_note():
    res = paste.split(f"{A}\n\nEmail:\n{B}\n\nGoogle post:\n{C}", P3)
    assert {s["piece_key"]: s["text"] for s in res.split} == {"a": A, "b": B, "c": C}
    assert any("piece 1 (facebook) had no heading" in p for p in res.problems)


def test_missing_heading_after_a_separator_is_assigned_by_position_with_a_note():
    res = paste.split(f"Facebook:\n{A}\n\nEmail:\n{B}\n\n---\n\n{C}", P3)
    assert {s["piece_key"]: s["text"] for s in res.split} == {"a": A, "b": B, "c": C}
    assert any("piece 3 (google_business) had no heading" in p for p in res.problems)


def test_unrecognised_decorated_heading_is_a_position_boundary():
    res = paste.split(f"Facebook:\n{A}\n\n### Local listing blurb for Maps\n{C}\n\nEmail:\n{B}",
                      [{"key": "a", "channel": "facebook"}, {"key": "c", "channel": "google_business"},
                       {"key": "b", "channel": "email"}])
    assert {s["piece_key"]: s["text"] for s in res.split} == {"a": A, "c": C, "b": B}
    assert any("piece 2 (google_business) had no heading" in p for p in res.problems)


def test_no_guess_when_two_pieces_lack_headings_or_there_is_no_boundary():
    b_plain = "Hello friends, the bread club is back."     # no "Subject:" line to start the email at
    res = paste.split(f"Facebook:\n{A}\n\n{b_plain}\n\n{C}", P3)       # no separator to split at
    assert any("not found" in p for p in res.problems)
    assert [s["piece_key"] for s in res.split] == ["a"]
    res = paste.split(f"Facebook:\n{A}\n\n---\n\n{b_plain}\n\n---\n\n{C}", P3)   # two missing in a row
    assert any("piece 2 (email) not found" in p for p in res.problems)


def test_no_headings_but_separators_in_task_order_with_a_note():
    res = paste.split(f"{A}\n\n---\n\n{B}\n\n---\n\n{C}", P3)
    assert [s["text"] for s in res.split] == [A, B, C]
    assert any("task order" in p for p in res.problems)
    res = paste.split(f"{A}\n\n---\n\n{B}", P3)                  # 2 parts for 3 pieces: no guess
    assert res.split == [] and any("no piece markers" in p for p in res.problems)


def test_heading_number_that_contradicts_the_channel_is_a_problem():
    res = paste.split(f"1. Email:\n{B}\n\n2. Facebook:\n{A}\n\n3. Google:\n{C}", P3)
    assert any("marker 1 says email but piece 1 is facebook" in p for p in res.problems)


def test_repeated_channel_heading_keeps_its_text():
    pieces = [{"key": "a", "channel": "facebook"}]
    res = paste.split(f"Facebook post:\n{A}\n\nFacebook:\nSecond part.", pieces)
    assert "Second part." not in "".join(s["text"] for s in res.split) or res.problems
    assert any("no facebook piece left" in p for p in res.problems)


# ---------- 2. identifier numbers

IDS = [
    fact("gas-reg", "Hearthside Heating is registered with the Flame Safety Register, registration no. 558213.",
         "Flame Safety Register member (registration no. 558213)", subject={"kind": "business", "ref": "Hearthside Heating"},
         fact_type="certification", claim_class="safety_cert"),
    fact("iso-cert", "Hearthside Heating is ISO 9001 certified, certificate QA 20417.", "ISO 9001 certified (certificate QA 20417)",
         subject={"kind": "business", "ref": "Hearthside Heating"}, fact_type="certification", claim_class="safety_cert"),
    fact("old-licence", "Hearthside held waste carrier licence WCL-7781 until 2030.", "waste carrier licence WCL-7781",
         subject={"kind": "business", "ref": "Hearthside Heating"}, fact_type="certification", valid_to="2030-12-31"),
    fact("boiler-model", "The Vireo KB-240 boiler is 94% efficient.", "94% efficient",
         subject={"kind": "product", "ref": "Vireo KB-240 boiler"}, fact_type="spec", value=94, unit="percent"),
]


@pytest.mark.parametrize("text,key", [
    ("We are ISO 9001 certified (certificate QA 20471).", "iso-cert"),            # transposed digits
    ("Hearthside is registered, registration no. 558231.", "gas-reg"),            # context number
    ("Certified since 2019, certificate QA-99999.", "iso-cert"),                  # same shape, other digits
    ("We hold waste carrier licence WCL-7781.", "old-licence"),                   # the identifier of an expired fact
])
def test_other_identifier_of_the_same_shape_conflicts(text, key):
    f = run(text, IDS)
    assert ("conflict_or_expired", key) in blocking(f), labels(f)
    assert ("match", key) not in labels(f)


@pytest.mark.parametrize("text", [
    "We are ISO 9001 certified (certificate QA 20417).",
    "Flame Safety Register member, registration no. 558213.",
    "Book the Vireo KB-420 boiler service.",        # a model name of a spec fact is not an identifier
    "Call 01632 960123 to book.",                     # a phone number is not
    "Open since 2012 with 1500 happy homes.",
])
def test_same_identifier_or_other_numbers_do_not_conflict(text):
    f = run(text, IDS)
    assert not [x for x in f if x["label"] == "conflict_or_expired"], labels(f)


# ---------- 3. staff credential claims

STAFF = [
    fact("studio-accreditation", "Stillwater Pilates is accredited by the Movement Studios Guild.",
         "accredited by the Movement Studios Guild", subject={"kind": "business", "ref": "Stillwater Pilates"},
         fact_type="certification", claim_class="safety_cert"),
    fact("instructor-cert", "Every Stillwater instructor is a certified Pilates instructor (Level 4 Mat and Reformer).",
         "certified Pilates instructors", subject={"kind": "person", "ref": "Stillwater instructors"},
         fact_type="credential"),
    fact("software-partner", "Stillwater is a BookWell certified partner studio.", "BookWell certified partner",
         subject={"kind": "business", "ref": "Stillwater Pilates"}, fact_type="credential"),
]


@pytest.mark.parametrize("text", [
    "Our instructors are also registered physiotherapists.",
    "Every class plan is designed by our chartered sports scientists and physiotherapists.",
    "Sessions are reviewed by our in-house registered dietitian.",
    "Every instructor on our team is a BookWell Certified Movement Expert.",   # a partner fact is not staff
    "Subject: Taught by licensed osteopaths",
    "All our coaches are fully qualified.",
])
def test_staff_credential_claims_need_a_staff_fact(text):
    f = run(text, STAFF)
    assert ("no_source", None) in blocking(f), labels(f)
    assert ("match", "software-partner") not in labels(f)


@pytest.mark.parametrize("text", [
    "Our instructors are certified Pilates instructors.",          # a fact says so
    "Our registered office is on Quay Street.",
    "Registered users can book online.",
    "Our trainers are friendly and patient.",
    "Stillwater Pilates is accredited by the Movement Studios Guild.",
    "We are a BookWell certified partner studio.",                  # the business, not the staff
])
def test_staff_claim_negative_controls(text):
    f = run(text, STAFF)
    assert not [x for x in f if x["label"] == "no_source"], labels(f)


# ---------- 4. allergen / ingredient lists use the allergen fact

ALLERGY = [
    fact("flapjack-allergens", "The Moorland Flapjack contains oats (gluten), butter (milk) and hazelnuts.",
         "contains oats (gluten), butter (milk) and hazelnuts", subject={"kind": "product", "ref": "Moorland Flapjack"},
         fact_type="spec", attribute="allergens", claim_class="regulated_food",
         required_disclosures=["made in a kitchen that also handles sesame"]),
    fact("flapjack-price", "The Moorland Flapjack costs £2.40.", "£2.40", subject={"kind": "product", "ref": "Moorland Flapjack"},
         fact_type="price", value=2.4, currency="GBP"),
]


@pytest.mark.parametrize("text", [
    "Our Moorland Flapjack contains oats, butter and hazelnuts.",
    "Subject: Contains hazelnuts and butter, and plenty of crunch",
    "Ingredients: oats, butter, hazelnuts, golden syrup.",
])
def test_allergen_list_without_the_disclosure_is_missing_it(text):
    f = run(text, ALLERGY)
    assert ("missing_disclosure", "flapjack-allergens") in blocking(f), labels(f)


@pytest.mark.parametrize("text", [
    "Our Moorland Flapjack contains oats, butter and hazelnuts. Made in a kitchen that also handles sesame.",
    "The Moorland Flapjack costs £2.40.",
    "Oat and hazelnut heaven, baked daily.",                    # names things, lists nothing
    "It does not contain hazelnuts or butter.",                 # a negated list is not this fact's list
])
def test_allergen_negative_controls(text):
    f = run(text, ALLERGY)
    assert ("missing_disclosure", "flapjack-allergens") not in blocking(f), labels(f)


# ---------- 5. offers named in part or by their benefit, 6. valid price sharing words with an expired offer

OFFERS = [
    fact("club-price", "The Ridgeway Club plan (2 rounds a week and a 20-bucket range card) costs £95 a month.",
         "£95 a month", subject={"kind": "plan", "ref": "Ridgeway Club plan"}, fact_type="price", value=95,
         currency="GBP", required_disclosures=["12-month term"]),
    fact("spring-waiver", "Spring Swing Start: no locker fee (normally £40) for new members, March to April.",
         "no locker fee (normally £40)", subject={"kind": "offer", "ref": "Spring Swing Start"}, fact_type="price",
         value=40, currency="GBP", valid_from="2031-03-01", valid_to="2031-04-30", claim_class="price_reference"),
    fact("county-bursary", "Juniors in the county can get up to £150 off lessons through the Green Fairways junior bursary.",
         "up to £150 off lessons", subject={"kind": "offer", "ref": "County Green Fairways junior bursary"},
         fact_type="price", attribute="bursary", value=150, currency="GBP", valid_to="2031-05-01"),
    fact("range-upgrade", "Winter Range Boost: a free upgrade to a 40-bucket range card.", "free upgrade to a 40-bucket range card",
         subject={"kind": "offer", "ref": "Winter Range Boost"}, fact_type="price", valid_to="2031-02-28"),
]


@pytest.mark.parametrize("text,key", [
    ("Juniors may qualify for the Green Fairways bursary too.", "county-bursary"),
    ("Join this week with no locker fee.", "spring-waiver"),
    ("Your locker fee is waived when you join.", "spring-waiver"),
])
def test_offer_named_in_part_or_by_benefit_is_checked(text, key):
    f = run(text, OFFERS)
    assert ("conflict_or_expired", key) in blocking(f), labels(f)


@pytest.mark.parametrize("text", [
    "The Ridgeway Club plan gives you a 20-bucket range card and 2 rounds a week for £95 a month, 12-month term.",
    "Lockers are available at reception.",
    "Our greens are the Green Keeper's pride.",                   # capitalised words that are not the name
    "Fairways and greens are open daily.",
])
def test_valid_fact_words_do_not_match_an_expired_offer(text):
    f = run(text, OFFERS)
    assert not [x for x in f if x["label"] == "conflict_or_expired"], labels(f)


def test_naming_the_expired_offer_still_flags_it():
    f = run("With Winter Range Boost, the Ridgeway Club plan gets a 40-bucket range card for £95 a month, 12-month term.",
            OFFERS)
    assert ("conflict_or_expired", "range-upgrade") in blocking(f), labels(f)


@pytest.mark.parametrize("text", [
    "Parts are covered when fitted by a qualified installer.",
    "Any repair must be signed off by an accredited engineer.",
])
def test_a_credential_condition_is_not_a_staff_claim(text):
    f = run(text, STAFF)
    assert not [x for x in f if x["label"] == "no_source"], labels(f)
