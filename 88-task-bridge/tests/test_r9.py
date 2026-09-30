"""Round 9: the last piece of the task without a heading (rule (e) in paste.py)."""
import pytest

from app import paste

IG = {"key": "a", "channel": "instagram"}
FB = {"key": "f", "channel": "facebook"}
EM = {"key": "e", "channel": "email", "kind": "newsletter"}
NL = {"key": "n", "channel": "newsletter", "kind": "newsletter"}
FLY = {"key": "y", "channel": "flyer", "kind": "print", "max_chars": 300}
BLOG = {"key": "b", "channel": "blog", "kind": "post"}

IG_TEXT = "New season, new bikes 🚲\nOur Trail 200 is £1,240 this spring.\n#Cycling"
EM_TEXT = "Subject: Spring servicing\n\nHi there,\n\nBook your service before the rush.\nWe open at 8am."


def got(res):
    return {s["piece_key"]: s["text"] for s in res.split}


def test_last_paragraph_of_an_email_is_the_facebook_piece():
    fb = "Spring servicing is open at Wheelhouse Cycles: book a full service before the rush."
    res = paste.split(f"**Instagram**\n{IG_TEXT}\n\n**Email**\n{EM_TEXT}\n\n{fb}\n\nWant another version?",
                      [IG, {**EM, "kind": "email"}, FB])
    assert got(res)["f"] == fb and got(res)["e"] == EM_TEXT
    assert any("last paragraph" in p for p in res.problems)


def test_newsletter_starts_at_its_greeting():
    pin = "Spring wedding at the Mill? From £5,200 for 50 guests."
    letter = "Hello friends,\n\nSpring dates are open.\nCome and see the barn.\n\nTop tip: visit at golden hour."
    res = paste.split(f"**Facebook post**\nBig news from the Mill.\n\n**Pinterest pin**\n{pin}\n\n{letter}\n\nWant more?",
                      [FB, {"key": "p", "channel": "pinterest"}, NL])
    assert got(res)["p"] == pin and got(res)["n"] == letter
    assert any("greeting" in p for p in res.problems)


def test_multi_line_flyer_within_its_limit():
    flyer = "Trail 200 — £1,240 this spring.\nFree first service.\nWheelhouse Cycles, Otley"
    res = paste.split(f"Instagram:\n{IG_TEXT}\n\nFacebook:\nSpring is here.\nBikes are in.\n\n{flyer}", [IG, FB, FLY])
    assert got(res)["y"] == flyer


@pytest.mark.parametrize("tail", [
    "#Cycling #Otley #Spring",                         # hashtags stay with the post
    "Thanks,\nThe Wheelhouse team",                    # a letter closing
    "P.S. we are closed on Monday.",                   # a postscript
    "See you soon!",                                   # too short to be a piece
])
def test_final_paragraph_negative_controls(tail):
    res = paste.split(f"Instagram:\n{IG_TEXT}\n\nFacebook:\nSpring is here and the bikes are in.\n\n{tail}", [IG, FB, FLY])
    assert "y" not in got(res)


def test_too_long_for_the_flyer_is_not_guessed():
    long = "Spring is here. " * 30
    res = paste.split(f"Instagram:\n{IG_TEXT}\n\nFacebook:\nSpring is here.\n\n{long.strip()}", [IG, FB, FLY])
    assert "y" not in got(res)


def test_a_newsletter_without_a_greeting_is_not_guessed():
    res = paste.split(f"Facebook:\nBig news from the Mill.\n\nPinterest:\nSpring weddings from £5,200.\n\n"
                      "Spring dates are open and the barn is ready for you to visit.",
                      [FB, {"key": "p", "channel": "pinterest"}, NL])
    assert "n" not in got(res)


def test_only_the_last_piece_of_the_task():
    # the missing piece is in the middle: rule (e) does not guess
    res = paste.split(f"Instagram:\n{IG_TEXT}\n\nSpring is here and the bikes are in, come and see them.\n\n"
                      "Flyer:\nTrail 200 — £1,240.", [IG, FB, FLY])
    assert "f" not in got(res) or any("f" in p or "facebook" in p for p in res.problems)


def test_blog_is_never_one_leftover_paragraph():
    res = paste.split(f"Instagram:\n{IG_TEXT}\n\nFacebook:\nSpring is here.\n\n"
                      "Choosing a first road bike comes down to fit, budget and where you ride most.", [IG, FB, BLOG])
    assert "b" not in got(res)


# ---------- "Here's your X:" headings (a chatbot presenting each piece)

LI = {"key": "l", "channel": "linkedin"}
WEBP = {"key": "w", "channel": "website", "kind": "banner"}
EMAIL = {"key": "e", "channel": "email"}


def test_presenter_lines_start_pieces():
    text = ("Here's your LinkedIn post:\n\nPrinting before the rush? We can help.\n\n"
            "Here's your email:\n\nSubject: Autumn cards\n\nHello,\nCards are ready in a day.\n\nKind regards,\nPrint Co\n\n"
            "And here's a short banner for the website:\n\n500 cards for £29 plus VAT.\n\nShall I write another?")
    res = paste.split(text, [LI, EMAIL, WEBP])
    g = got(res)
    assert g["l"] == "Printing before the rush? We can help."
    assert g["e"].startswith("Subject: Autumn cards") and g["e"].endswith("Print Co")
    assert g["w"] == "500 cards for £29 plus VAT."
    assert not res.problems


@pytest.mark.parametrize("line", [
    "Here are the LinkedIn and email versions:",       # two channels: which one?
    "Here's why LinkedIn works for printers: people buy from people.",   # not a whole-line heading
    "Here's the plan:",                                # no channel
])
def test_presenter_negative_controls(line):
    assert paste._presenter_marker(line, {("linkedin", None), ("email", None)}, 2) is None


def test_single_piece_presenter_stays_a_preamble():
    res = paste.split("Here's a LinkedIn post for you:\n\nCards in a day.", [LI])
    assert got(res) == {"l": "Cards in a day."} and res.split[0]["removed_pre"]


@pytest.mark.parametrize("line", ["And the email version:", "Also, your LinkedIn post:", "Finally, the email:"])
def test_and_the_x_presenter(line):
    assert paste._presenter_marker(line, {("linkedin", None), ("email", None)}, 2) is not None


def test_and_the_x_needs_a_whole_line_heading():
    assert paste._presenter_marker("And the email went out on time: great.", {("email", None), ("x", None)}, 2) is None


# ---------- "Option N" headings with a legend; kind words in headings

THREE = [{"key": "l", "channel": "linkedin"}, {"key": "e", "channel": "email"}, {"key": "x", "channel": "x"}]


def test_options_follow_the_legend():
    text = ("Here you go: Option 1 is the email, Option 2 for LinkedIn and Option 3 for X.\n\n"
            "Option 1\nSubject: Hello\n\nHi there,\nWe back up your files.\n\n"
            "Option 2\nOur team keeps your laptops patched.\n\nOption 3\nPatched laptops, fewer headaches.")
    res = paste.split(text, THREE)
    g = got(res)
    assert g["e"].startswith("Subject: Hello") and g["l"].startswith("Our team") and g["x"].startswith("Patched")
    assert any("Option" in p for p in res.problems)


@pytest.mark.parametrize("text", [
    # alternatives of one piece, no legend
    "Option 1\nOur team keeps laptops patched.\n\nOption 2\nPatched laptops.\n\nOption 3\nFewer headaches.",
    # legend misses a channel
    "Option 1 is LinkedIn and Option 2 is email.\n\nOption 1\nA.\n\nOption 2\nB.\n\nOption 3\nC.",
    # numbering doesn't match the pieces
    "Option 1 LinkedIn, Option 2 email, Option 3 X.\n\nOption 1\nA.\n\nOption 3\nC.",
])
def test_options_negative_controls(text):
    assert paste.split(text, THREE).split == []


def test_kind_word_after_channel_is_a_heading():
    pieces = [{"key": "w", "channel": "whatsapp"}, {"key": "b", "channel": "website", "kind": "banner"}]
    res = paste.split("WhatsApp message —\nMoving soon? We can help.\n\nWebsite banner —\nMoves handled with care.", pieces)
    assert got(res) == {"w": "Moving soon? We can help.", "b": "Moves handled with care."}


# ---------- "Channel — text" on one line

def test_channel_dash_text_starts_a_piece():
    pieces = [{"key": "l", "channel": "linkedin"}, {"key": "e", "channel": "email"}, {"key": "x", "channel": "x"}]
    text = ("Sure thing.\n\nLinkedIn — Our Team plan is £29 per user.\nIt includes sequences.\n\n"
            "Email — Subject: A CRM you'll use\nHi there, try it free.\n\nX — Try it free for 14 days.")
    res = paste.split(text, pieces)
    g = got(res)
    assert g == {"l": "Our Team plan is £29 per user.\nIt includes sequences.",
                 "e": "Subject: A CRM you'll use\nHi there, try it free.", "x": "Try it free for 14 days."}
    assert not res.problems


def test_title_after_dash_stays_as_first_line():
    pieces = [{"key": "i", "channel": "instagram"}, {"key": "g", "channel": "google_business"}]
    res = paste.split("Instagram — Flowers today 💐\nOrder by 1pm.\n\nGoogle Business — Sundays\nOpen 10 to 2.", pieces)
    assert got(res) == {"i": "Flowers today 💐\nOrder by 1pm.", "g": "Sundays\nOpen 10 to 2."}


@pytest.mark.parametrize("line", ["Text — call us on 0161 000 000", "x — marks the spot for our shop",
                                  "Web — see our page"])
def test_dash_inline_negative_controls(line):
    chans = {("sms", None), ("x", None), ("website", None)}
    assert paste._dash_inline(line, chans) is None
