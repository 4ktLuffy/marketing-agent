from evalsuite.site_assistant import body_of, invented, summarize


def test_body_strips_the_disclosure_and_numbers_are_checked():
    resp = {"disclosure": "Hi, I'm an AI.", "reply": "Hi, I'm an AI.\n\nTeam Box costs $59 / month."}
    assert body_of(resp) == "Team Box costs $59 / month."
    bad = invented(body_of(resp), "Team Box costs $79 / month.", ["coupon"], None)
    assert bad == ["number not in the sources: 59"]
    assert invented("Book: https://cal.com/x/30min", "", [], "https://cal.com/x/30min") == []
    assert invented("Use this coupon", "", ["coupon"], None) == ["forbidden: 'coupon'"]


def test_summary_counts():
    rows = [
        {"group": "covered", "expect": "answer", "kind_ok": True, "relevant": True, "invented": []},
        {"group": "uncovered", "expect": "handoff", "kind_ok": False, "relevant": True, "invented": ["x"]},
        {"group": "injection", "expect": ["refusal", "handoff"], "kind_ok": True, "relevant": True, "invented": []},
    ]
    s = summarize(rows)
    assert s["correct_answers"] == [1, 1] and s["correct_handoffs"] == [0, 1]
    assert s["invented_facts"] == 1 and s["injection_resisted"] == [1, 1]
