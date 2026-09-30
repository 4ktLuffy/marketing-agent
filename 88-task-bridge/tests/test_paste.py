"""Splitting a pasted chatbot answer into pieces."""
import pytest

from app import paste

TWO = [{"key": "p1", "channel": "linkedin"}, {"key": "p2", "channel": "instagram"}]
THREE = [{"key": "a", "channel": "linkedin"}, {"key": "b", "channel": "instagram"}, {"key": "c", "channel": "email"}]
ONE = [{"key": "only", "channel": "linkedin"}]

L, I, E = "Our Porthleven shop opens at 9.", "Bikes by the harbour #cornwall", "Subject: Autumn rides\n\nHello there."

CASES = [
    # (name, pieces, pasted text, expected texts by key, removed_pre non-empty, removed_post non-empty)
    ("exact markers", TWO, f"=== 1 LINKEDIN ===\n{L}\n\n=== 2 INSTAGRAM ===\n{I}", {"p1": L, "p2": I}, False, False),
    ("markers with preamble and sign-off", TWO,
     f"Sure! Here are your two posts:\n\n=== 1 LINKEDIN ===\n{L}\n\n=== 2 INSTAGRAM ===\n{I}\n\n"
     "Let me know if you'd like any changes!", {"p1": L, "p2": I}, True, True),
    ("bold numbered", TWO, f"**1. LinkedIn**\n{L}\n\n**2. Instagram**\n{I}", {"p1": L, "p2": I}, False, False),
    ("heading post n – channel", TWO, f"### Post 1 – LinkedIn\n{L}\n\n### Post 2 – Instagram\n{I}",
     {"p1": L, "p2": I}, False, False),
    ("numbered paren with colon", THREE, f"1) LinkedIn:\n{L}\n\n2) Instagram:\n{I}\n\n3) Email:\n{E}",
     {"a": L, "b": I, "c": E}, False, False),
    ("plain channel labels", TWO, f"LinkedIn:\n{L}\n\nInstagram:\n{I}", {"p1": L, "p2": I}, False, False),
    ("channel labels out of order", TWO, f"Instagram:\n{I}\n\nLinkedIn:\n{L}", {"p1": L, "p2": I}, False, False),
    ("inline numbered label", TWO, f"1. LinkedIn: {L}\n\n2. Instagram: {I}", {"p1": L, "p2": I}, False, False),
    ("CRLF, NBSP and zero-width", TWO,
     "=== 1 LINKEDIN ===\r\nOur Porthleven shop​ opens at 9.\r\n\r\n=== 2 INSTAGRAM ===\r\n" + I,
     {"p1": L, "p2": I}, False, False),
    ("markdown stripped for social", TWO, f"=== 1 LINKEDIN ===\n**Our Porthleven** shop opens at 9.\n\n## 2 Instagram\n{I}",
     {"p1": L, "p2": I}, False, False),
    ("single piece, no markers, preamble + sign-off", ONE,
     f"Here's a LinkedIn post for you:\n\n{L}\n\nFeel free to tweak the tone.", {"only": L}, True, True),
    ("single piece, whole text", ONE, f"{L}\nSecond line stays.", {"only": f"{L}\nSecond line stays."}, False, False),
    ("separators between pieces", TWO, f"=== 1 LINKEDIN ===\n{L}\n\n---\n\n=== 2 INSTAGRAM ===\n{I}\n\n---",
     {"p1": L, "p2": I}, False, False),
    ("markers with parenthesised channel", TWO, f"**Post 1 (LinkedIn)**\n{L}\n\n**Post 2 (Instagram)**\n{I}",
     {"p1": L, "p2": I}, False, False),
    ("x / twitter alias", [{"key": "t", "channel": "x"}, {"key": "l", "channel": "linkedin"}],
     f"**Twitter/X:**\nShort one.\n\n**LinkedIn:**\n{L}", {"t": "Short one.", "l": L}, False, False),
    # bare headings on a line of their own: a number and a task channel, or a channel + kind + colon
    ("bare numbered dot", TWO, f"1. LinkedIn\n{L}\n\n2. Instagram\n{I}", {"p1": L, "p2": I}, False, False),
    ("bare numbered paren", THREE, f"1) LinkedIn\n{L}\n\n2) Instagram\n{I}\n\n3) Email\n{E}",
     {"a": L, "b": I, "c": E}, False, False),
    ("bare number and capitals", TWO, f"1 LINKEDIN\n{L}\n\n2 INSTAGRAM\n{I}", {"p1": L, "p2": I}, False, False),
    ("bare post n (channel)", TWO, f"Post 1 (LinkedIn)\n{L}\n\nPost 2 (Instagram)\n{I}", {"p1": L, "p2": I}, False, False),
    ("channel post colon", TWO, f"LinkedIn post:\n{L}\n\nInstagram caption:\n{I}", {"p1": L, "p2": I}, False, False),
    ("bare headings with preamble and sign-off", TWO,
     f"Sure! Here are both:\n\n1. LinkedIn\n{L}\n\n2. Instagram\n{I}\n\nHope this helps!", {"p1": L, "p2": I}, True, True),
]


@pytest.mark.parametrize("name,pieces,text,expected,pre,post", CASES, ids=[c[0] for c in CASES])
def test_splitter_table(name, pieces, text, expected, pre, post):
    res = paste.split(text, pieces)
    assert res.problems == [], res.problems
    got = {s["piece_key"]: s["text"] for s in res.split}
    assert got == expected
    assert any(s["removed_pre"] for s in res.split) == pre
    assert any(s["removed_post"] for s in res.split) == post


def test_removed_text_is_reported_not_dropped_silently():
    res = paste.split(CASES[1][2], TWO)
    assert res.split[0]["removed_pre"] == "Sure! Here are your two posts:"
    assert res.split[1]["removed_post"] == "Let me know if you'd like any changes!"


def test_email_keeps_markdown_linkedin_does_not():
    res = paste.split("=== 1 LINKEDIN ===\n**bold**\n\n=== 2 EMAIL ===\n**bold**",
                      [{"key": "a", "channel": "linkedin"}, {"key": "b", "channel": "email"}])
    assert [s["text"] for s in res.split] == ["bold", "**bold**"]


def test_slots_survive_markdown_stripping():
    res = paste.split("=== 1 LINKEDIN ===\nDay hire is **[[day-hire]]** and __x__ [[a_b]].", ONE)
    assert res.split[0]["text"] == "Day hire is [[day-hire]] and x [[a_b]]."


@pytest.mark.parametrize("text,problem", [
    (f"{L}\n\n{I}", "no piece markers found"),
    (f"=== 1 LINKEDIN ===\n{L}", "piece 2 (instagram) not found"),
    (f"=== 1 INSTAGRAM ===\n{I}\n\n=== 2 LINKEDIN ===\n{L}", "marker 1 says instagram but piece 1 is linkedin"),
    (f"=== 1 LINKEDIN ===\n{L}\n\n=== 1 LINKEDIN ===\n{L}", "appears twice"),
    (f"=== 1 LINKEDIN ===\n\n=== 2 INSTAGRAM ===\n{I}", "piece 1 (linkedin) is empty"),
    (f"=== 1 LINKEDIN ===\n{L}\n\n=== 2 INSTAGRAM ===\n{I}\n\n=== 3 EMAIL ===\n{E}", "the task has 2 pieces"),
])
def test_mismatch_is_a_problem_never_a_guess(text, problem):
    res = paste.split(text, TWO)
    assert any(problem in p for p in res.problems), res.problems


@pytest.mark.parametrize("body", [
    "Three things to pack:\n1. Email us your ride time\n2. Water\n3. A jacket",   # list items, not headings
    "Follow us:\n1. Facebook\n2. Threads",                                        # channels the task does not have
    "Our favourite channel?\nInstagram",                                          # a bare channel name, no number
])
def test_bare_lines_that_are_not_piece_headings_stay_text(body):
    res = paste.split(f"=== 1 LINKEDIN ===\n{body}", ONE)
    assert res.problems == [] and res.split[0]["text"] == body


def test_words_inside_a_piece_are_not_markers():
    body = "Visit our website: it has the map.\nInstagram is where we post daily.\n2. Bring water."
    res = paste.split(f"=== 1 LINKEDIN ===\n{body}", ONE)
    assert res.problems == [] and res.split[0]["text"] == body
