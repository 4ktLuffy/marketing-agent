"""Seventh round (invented businesses, not the eval companies).

Splitter: channel aliases chatbots use, a "Blog" heading on a website piece of kind blog, a
one-line "Blog: ..." piece, a title heading kept with its piece, a last short piece with no heading.
Evidence: pronouns are not part of a claim's identity, marks written "A/B marked", disclosures said in
other words, identifiers with slashes / acronym + number / a prefix family, grades in words, a tier
the scheme's fact does not name, a rating word before the scheme's name, who does every job, "N + 1
free" deals in words, a price an expired discount makes. Every rule has negative controls."""
from datetime import date

import pytest

from app import channels as CH
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


# ======================================================================= splitter

@pytest.mark.parametrize("alias,canon", [
    ("WhatsApp broadcast", "whatsapp"), ("WhatsApp message", "whatsapp"), ("Pinterest pin", "pinterest"),
    ("Idea pin", "pinterest"), ("FB post", "facebook"), ("Facebook post", "facebook"), ("GBP post", "google_business"),
    ("Google Business Profile", "google_business"), ("Google post", "google_business"), ("TikTok caption", "tiktok"),
    ("YouTube description", "youtube"), ("Threads post", "threads"), ("X post", "x"), ("Twitter", "x"),
    ("LinkedIn article", "linkedin"), ("Newsletter", "email"), ("Landing page", "website"), ("Blog post", "blog"),
    ("Flyer", "flyer"), ("Poster", "flyer"), ("SMS", "sms"), ("Text message", "sms"),
])
def test_channel_aliases(alias, canon):
    assert CH.canonical(alias) == canon


P_WA = [{"key": "a", "channel": "facebook"}, {"key": "b", "channel": "email"}, {"key": "c", "channel": "whatsapp"}]
FB = "Spring tune-ups are back at Fernside Cycles."
EM = "Subject: Your bike, ready for spring\nHi,\nBook a tune-up this week."
WA = "Tune-ups from Monday, reply YES to book a slot."


@pytest.mark.parametrize("head", ["WhatsApp broadcast:", "**WhatsApp message**", "### 3. WhatsApp", "=== 3 WHATSAPP ==="])
def test_whatsapp_headings(head):
    res = paste.split(f"**Facebook post**\n{FB}\n\n**Email**\n{EM}\n\n{head}\n{WA}", P_WA)
    assert {s["piece_key"]: s["text"] for s in res.split} == {"a": FB, "b": EM, "c": WA}, res.problems


def test_pin_heading_fills_a_pinterest_piece():
    pieces = [{"key": "a", "channel": "instagram"}, {"key": "b", "channel": "pinterest", "kind": "pin"}]
    res = paste.split(f"Instagram:\n{FB}\n\nPin:\n{WA}", pieces)
    assert {s["piece_key"]: s["text"] for s in res.split} == {"a": FB, "b": WA}, res.problems


BLOG = "Choosing a first road bike: frame size matters more than weight, so get fitted first."
P_WEB = [{"key": "a", "channel": "linkedin"}, {"key": "b", "channel": "email"},
         {"key": "c", "channel": "website", "kind": "blog"}]


@pytest.mark.parametrize("head", ["**3. Blog**", "=== 3 BLOG ===", "Blog:", "**Blog intro**"])
def test_blog_heading_fills_a_website_piece_of_kind_blog(head):
    res = paste.split(f"LinkedIn:\n{FB}\n\nEmail:\n{EM}\n\n{head}\n{BLOG}", P_WEB)
    assert {s["piece_key"]: s["text"] for s in res.split} == {"a": FB, "b": EM, "c": BLOG}, res.problems
    assert not res.problems


def test_blog_heading_goes_to_the_blog_piece_when_the_task_has_both():
    pieces = [{"key": "w", "channel": "website", "kind": "blog"}, {"key": "b", "channel": "blog", "kind": "post"}]
    res = paste.split(f"Website:\n{FB}\n\nBlog:\n{BLOG}", pieces)
    assert {s["piece_key"]: s["text"] for s in res.split} == {"w": FB, "b": BLOG}


def test_blog_heading_does_not_fill_a_website_piece_of_another_kind():
    pieces = [{"key": "a", "channel": "linkedin"}, {"key": "c", "channel": "website", "kind": "page"}]
    res = paste.split(f"LinkedIn:\n{FB}\n\nBlog:\n{BLOG}", pieces)
    assert "c" not in {s["piece_key"] for s in res.split if s["text"] == BLOG} or res.problems


def test_one_line_blog_piece_after_a_blank_line():
    res = paste.split(f"### LinkedIn\n{FB}\n\n### Email\n{EM}\n\nBlog: {BLOG}", P_WEB)
    assert {s["piece_key"]: s["text"] for s in res.split} == {"a": FB, "b": EM, "c": BLOG}, res.problems


@pytest.mark.parametrize("line", ["Website: www.fernside.example", "Email: hello@fernside.example",
                                  "Website: see the link"])
def test_contact_lines_are_not_one_line_pieces(line):
    res = paste.split(f"### LinkedIn\n{FB}\n\n### Email\n{EM}\n\n{line}", P_WEB)
    assert not any(s["piece_key"] == "c" for s in res.split)


def test_one_line_piece_ignored_when_a_heading_names_that_channel():
    body = f"### LinkedIn\n{FB}\n\nWebsite: our new booking page is live for spring tune-ups\n\n### Email\n{EM}\n\n### Website\n{BLOG}"
    res = paste.split(body, [{"key": "a", "channel": "linkedin"}, {"key": "b", "channel": "email"},
                             {"key": "c", "channel": "website"}])
    got = {s["piece_key"]: s["text"] for s in res.split}
    assert got["c"] == BLOG and "booking page" in got["a"]


def test_title_heading_is_kept_with_the_piece_it_starts():
    res = paste.split(f"=== 1 LINKEDIN ===\n{FB}\n\n=== 2 EMAIL ===\n{EM}\n\n# Choosing your first road bike\n{BLOG}",
                      P_WEB)
    got = {s["piece_key"]: s["text"] for s in res.split}
    assert got["c"].startswith("# Choosing your first road bike") and got["c"].endswith(BLOG)
    assert got["b"] == EM


def test_describing_heading_is_still_only_a_boundary():
    pieces = [{"key": "a", "channel": "facebook"}, {"key": "c", "channel": "google_business"},
              {"key": "b", "channel": "email"}]
    res = paste.split(f"Facebook:\n{FB}\n\n### Short update for Maps\n{WA}\n\nEmail:\n{EM}", pieces)
    assert {s["piece_key"]: s["text"] for s in res.split}["c"] == WA


@pytest.mark.parametrize("channel,max_chars", [("whatsapp", 500), ("pinterest", None), ("google-business", 750)])
def test_last_short_piece_without_heading_after_an_email(channel, max_chars):
    pieces = [{"key": "a", "channel": "facebook"}, {"key": "b", "channel": "email"},
              {"key": "c", "channel": channel, "max_chars": max_chars}]
    res = paste.split(f"**Facebook**\n{FB}\n\n**Email**\n{EM}\nFernside Cycles\n\n{WA}", pieces)
    assert {s["piece_key"]: s["text"] for s in res.split} == {"a": FB, "b": EM + "\nFernside Cycles", "c": WA}
    assert any("had no heading" in p for p in res.problems)


def test_last_piece_too_long_for_its_channel_is_not_guessed():
    pieces = [{"key": "a", "channel": "facebook"}, {"key": "b", "channel": "email"},
              {"key": "c", "channel": "whatsapp", "max_chars": 20}]
    res = paste.split(f"**Facebook**\n{FB}\n\n**Email**\n{EM}\n\n{WA}", pieces)
    assert not any(s["piece_key"] == "c" for s in res.split)


def test_warm_wishes_is_a_letter_sign_off():
    pieces = [{"key": "a", "channel": "facebook"}, {"key": "b", "channel": "email"},
              {"key": "c", "channel": "google_business"}]
    res = paste.split(f"**Facebook**\n{FB}\n\n**Email**\n{EM}\nWarm wishes,\nFernside Cycles\n\n{WA}\n\n{BLOG}", pieces)
    assert {s["piece_key"]: s["text"] for s in res.split}["c"] == f"{WA}\n\n{BLOG}"


# ======================================================================= evidence

MARKS = [fact("valve-marking", "Our Kestrel thermostatic valves are UKCA and CE marked (DoC TV-DOC-4410).",
              "UKCA and CE marked (DoC TV-DOC-4410)", subject={"kind": "product", "ref": "Kestrel thermostatic valve"},
              fact_type="certification", attribute="conformity_marking", claim_class="safety_cert")]


@pytest.mark.parametrize("text", ["They're UKCA and CE marked.", "They are UKCA/CE marked.",
                                  "It carries UKCA and CE marking.", "These valves are UKCA & CE marked."])
def test_marks_in_other_word_forms_match(text):
    f = run(text, MARKS)
    assert blocking(f) == [], labels(f)


@pytest.mark.parametrize("text", ["They're UKCA and FDA marked.", "They're UL Listed."])
def test_marks_the_fact_does_not_name_still_have_no_source(text):
    assert ("no_source", None) in blocking(run(text, MARKS))


GUARANTEE = [fact("frame-guarantee", "Every frame has a lifetime guarantee when registered within 60 days of purchase.",
                  "a lifetime frame guarantee", subject={"kind": "policy", "ref": "Frame guarantee"},
                  fact_type="policy", attribute="guarantee",
                  required_disclosures=["when registered within 60 days of purchase"])]


@pytest.mark.parametrize("text", [
    "Every frame has a lifetime frame guarantee; just register it within 60 days of purchase.",
    "Every frame has a lifetime frame guarantee (register within 60 days of your purchase).",
])
def test_disclosure_in_other_words_is_enough(text):
    f = run(text, GUARANTEE)
    assert ("missing_disclosure", "frame-guarantee") not in blocking(f), labels(f)


@pytest.mark.parametrize("text", [
    "Every frame has a lifetime frame guarantee, even if not registered within 60 days of purchase.",
    "Every frame has a lifetime frame guarantee; register within 90 days of purchase.",
    "Every frame has a lifetime frame guarantee.",
])
def test_disclosure_negated_or_changed_is_missing(text):
    assert ("missing_disclosure", "frame-guarantee") in blocking(run(text, GUARANTEE))


def test_disclosure_months_and_ranges():
    d = "based on 212 finishers, Mar 2030 to Feb 2031"
    assert evidence.disclosure_said(d, "Based on 212 finishers between March 2030 and February 2031.")
    assert evidence.disclosure_said(d, "212 finishers from March 2030 to February 2031 took part.")
    assert not evidence.disclosure_said(d, "Based on 212 finishers between March 2029 and February 2030.")
    assert not evidence.disclosure_said(d, "Based on 221 finishers between March 2030 and February 2031.")


@pytest.mark.parametrize("disc,text,ok", [
    ("excluding VAT", "£40 plus VAT", True),
    ("excluding VAT", "£40 including VAT", False),
    ("including VAT", "£40 excl. VAT", False),
    ("new members only", "open to new members", False),
])
def test_disclosure_polarity(disc, text, ok):
    assert evidence.disclosure_said(disc, text) is ok


IDS = [
    fact("doc", "Declaration of conformity KVD-QA-3318 covers every valve.", "declaration of conformity KVD-QA-3318",
         subject={"kind": "product", "ref": "Kestrel thermostatic valve"}, fact_type="certification",
         attribute="conformity", claim_class="safety_cert"),
    fact("rics", "Hollin Surveyors is regulated by RICS, RICS number 771204.", "regulated by RICS (RICS number 771204)",
         subject={"kind": "business", "ref": "Hollin Surveyors"}, fact_type="credential", attribute="rics_number"),
    fact("pefc", "Our birch is PEFC certified (licence code PEFC/16-37-2210).", "PEFC certified birch (licence PEFC-C-440918)",
         subject={"kind": "product", "ref": "Birch range"}, fact_type="certification", attribute="pefc"),
]


@pytest.mark.parametrize("text", ["Declaration of conformity KVD/QA/3381 is on file.",
                                  "Regulated by RICS, ID 7712/40.", "Our RICS number is 771-240.",
                                  "Our birch carries PEFC licence C-440981."])
def test_lookalike_identifiers_with_other_separators_conflict(text):
    assert any(lab == "conflict_or_expired" for lab, _ in blocking(run(text, IDS))), labels(run(text, IDS))


@pytest.mark.parametrize("text", ["Declaration of conformity KVD/QA/3318 is on file.", "Regulated by RICS, ID 7712/04.",
                                  "Our RICS number is 771-204.", "Our birch carries PEFC licence C-440918.",
                                  "Batch C-440981 ships on Monday.", "Call 0161 771 240 to book."])
def test_same_identifier_or_unrelated_number_does_not_block(text):
    f = run(text, IDS)
    assert blocking(f) == [], labels(f)


GRADES = [fact("helmet-class", "The Aero helmet is certified as a Class 1 protective device.", "Class I protective device",
               subject={"kind": "product", "ref": "Aero helmet"}, fact_type="certification", value="Class I",
               attribute="protection_class", claim_class="safety_cert"),
          fact("coach-grade", "Mira Hale is a British Cycling coach, grade B.", "British Cycling coach, grade B",
               subject={"kind": "person", "ref": "Mira Hale (head coach)"}, fact_type="credential", value="B",
               attribute="coach_grade")]


@pytest.mark.parametrize("text,key", [("Subject: Class Two protection for every ride", "helmet-class"),
                                      ("The Aero helmet is certified as a Class Three device.", "helmet-class"),
                                      ("Ride with a grade four coach like Mira.", "coach-grade")])
def test_grade_in_words_conflicts(text, key):
    assert ("conflict_or_expired", key) in blocking(run(text, GRADES))


@pytest.mark.parametrize("text", ["The Aero helmet is a Class One device.", "Park in our class two bay.",
                                  "Grade six pupils ride free on Saturdays.", "Mira coaches at grade B."])
def test_grade_in_words_negative_controls(text):
    f = run(text, GRADES)
    assert not any(k in ("helmet-class", "coach-grade") and lab == "conflict_or_expired" for lab, k in blocking(f)), labels(f)


TIERS = [fact("iip", "Hollin Surveyors holds Investors in People accreditation (standard v7).",
              "Investors in People accredited (standard v7)", subject={"kind": "business", "ref": "Hollin Surveyors"},
              fact_type="certification", attribute="iip", value="v7", claim_class="safety_cert")]


@pytest.mark.parametrize("text", ["We're Investors Platinum accredited.", "Subject: Investors Gold service, every time"])
def test_tier_the_scheme_fact_does_not_name_conflicts(text):
    assert ("conflict_or_expired", "iip") in blocking(run(text, TIERS))


@pytest.mark.parametrize("text", ["We're Investors in People accredited.", "Gold standard service from Hollin Surveyors.",
                                  "Our Platinum package includes a drone survey."])
def test_tier_negative_controls(text):
    assert ("conflict_or_expired", "iip") not in blocking(run(text, TIERS))


RATED = [fact("care-rating", "Birchfield House was rated Good by the Care Inspectorate in 2030.",
              "rated Good by the Care Inspectorate (2030)", subject={"kind": "site", "ref": "Birchfield House"},
              fact_type="certification", value="Good", attribute="inspection_rating")]


@pytest.mark.parametrize("text", ["Birchfield House is rated Excellent by the Care Inspectorate.",
                                  "Subject: Our Excellent Care Inspectorate rating"])
def test_rating_word_on_the_named_body_conflicts(text):
    assert ("conflict_or_expired", "care-rating") in blocking(run(text, RATED))


@pytest.mark.parametrize("text", ["Birchfield House has a Good Care Inspectorate rating.",
                                  "Read our latest Care Inspectorate rating online.",
                                  "Birchfield House is rated Good by the Care Inspectorate."])
def test_rating_word_negative_controls(text):
    assert blocking(run(text, RATED)) == []


SERVICE = [fact("mechanic", "Tomas Reyes is our Cytech-qualified mechanic.", "Cytech-qualified mechanic",
                subject={"kind": "person", "ref": "Tomas Reyes"}, fact_type="credential", attribute="qualification"),
           fact("tune-price", "A full tune-up costs £65.", "£65", subject={"kind": "service", "ref": "Full tune-up"},
                fact_type="price", attribute="price", value=65, currency="GBP")]


@pytest.mark.parametrize("text", ["A qualified mechanic checks every bike before it leaves.",
                                  "Every repair is signed off personally by a mechanic.",
                                  "Each wheel is built start to finish by a single master wheelwright craftsman.",
                                  "Every one of our mechanics is Cytech qualified."])
def test_who_does_every_job_needs_a_fact(text):
    assert ("no_source", None) in blocking(run(text, SERVICE)), labels(run(text, SERVICE))


@pytest.mark.parametrize("text", ["Tomas is our Cytech-qualified mechanic.", "Every bike is checked by our team.",
                                  "A mechanic can answer your questions.", "A full tune-up costs £65."])
def test_who_does_every_job_negative_controls(text):
    f = run(text, SERVICE)
    assert blocking(f) == [], labels(f)


def test_who_does_every_job_supported_by_a_team_fact():
    facts = SERVICE + [fact("team", "Every bike we service is checked by a Cytech-qualified mechanic.",
                            "every bike checked by a Cytech-qualified mechanic",
                            subject={"kind": "policy", "ref": "Workshop checks"}, fact_type="policy", attribute="checks")]
    assert blocking(run("A qualified mechanic checks every bike before it leaves.", facts)) == []


DEALS = [fact("ride-deal", "Spring Ride Pass: pay for 5 guided rides and get the 6th free, 1 to 30 April 2031.",
              "pay for 5 rides, get the 6th free", subject={"kind": "offer", "ref": "Spring Ride Pass"},
              fact_type="price", attribute="free_ride", value=1, valid_from="2031-04-01", valid_to="2031-04-30"),
         fact("ride-price", "A guided ride costs £18.", "£18", subject={"kind": "service", "ref": "Guided ride"},
              fact_type="price", attribute="price", value=18, currency="GBP")]


@pytest.mark.parametrize("text", ["Pay for five, ride six!", "One free ride when you book five.",
                                  "Book 5 rides and your 6th is on us."])
def test_expired_n_plus_one_deal_in_words_conflicts(text):
    assert ("conflict_or_expired", "ride-deal") in blocking(run(text, DEALS))


@pytest.mark.parametrize("text", ["Book 5 rides and get a free water bottle.", "We book 5 to 6 weeks ahead.",
                                  "A guided ride costs £18.", "Book two rides for three friends."])
def test_n_plus_one_negative_controls(text):
    assert ("conflict_or_expired", "ride-deal") not in blocking(run(text, DEALS))


SALE = [fact("frame-price", "The Ridgeway frame costs £800.", "£800", subject={"kind": "product", "ref": "Ridgeway frame"},
             fact_type="price", attribute="price", value=800, currency="GBP"),
        fact("easter-sale", "Easter Sale: 20% off frames, 1 to 20 April 2031.", "20% off frames",
             subject={"kind": "offer", "ref": "Easter Sale"}, fact_type="price", attribute="discount", value=20,
             unit="%", valid_from="2031-04-01", valid_to="2031-04-20")]


def test_price_an_expired_discount_makes_is_that_offers():
    assert ("conflict_or_expired", "easter-sale") in blocking(run("That brings the Ridgeway frame down to £640.", SALE))


def test_other_price_is_still_a_conflict_with_the_price():
    assert blocking(run("The Ridgeway frame is £700.", SALE)) == [("conflict_or_expired", "frame-price")]


DELIVERY = [fact("local-delivery", "Free delivery within 10 miles of the shop.", "free delivery",
                 subject={"kind": "policy", "ref": "Delivery"}, fact_type="price", attribute="delivery_fee", value=0,
                 regions=["local"], required_disclosures=["within 10 miles"]),
            fact("returns", "Returns are accepted within 30 days of delivery.", "30-day returns",
                 subject={"kind": "policy", "ref": "Returns"}, fact_type="policy", attribute="returns")]


def test_keyword_said_by_an_out_of_scope_fact_wins_over_a_shared_word():
    f = run("Free delivery on every frame order.", DELIVERY, {"regions": ["national"]})
    assert ("wrong_scope", "local-delivery") in blocking(f), labels(f)
