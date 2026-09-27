from pathlib import Path

import yaml

import pytest

from evalsuite.voc_ab import (ROOT, bolted_on, countable, enforcement, first_sentence, has_phrase, metrics, opens_with,
                              pick_open_with, totals, write_b)

CASE = Path(__file__).parent.parent / "cases" / "voc" / "voc_ab.yaml"
CASE_V2 = CASE.with_name("voc_ab_v2.yaml")


@pytest.mark.parametrize("path", [CASE, CASE_V2], ids=lambda p: p.stem)
def test_case_file_is_complete(path):
    case = yaml.safe_load(path.read_text())
    ids = [t["id"] for t in case["topics"]]
    assert 8 <= len(ids) <= 12 and len(set(ids)) == len(ids)
    assert len({t["channel"] for t in case["topics"]}) >= 4
    assert case["samples"].endswith("northwind_sources.json")


def test_v2_topics_are_new():
    v1, v2 = (yaml.safe_load(p.read_text())["topics"] for p in (CASE, CASE_V2))
    assert not {t["id"] for t in v1} & {t["id"] for t in v2}
    assert not {t["topic"].lower() for t in v1} & {t["topic"].lower() for t in v2}


def test_samples_path_resolves_when_repos_are_side_by_side():
    case = yaml.safe_load(CASE.read_text())
    path = ROOT / case["samples"]
    if path.exists():  # only in the monorepo / after clone-all.sh
        assert "FICTIONAL EXAMPLE DATA" in path.read_text()[:200]


def test_has_phrase_word_for_word():
    assert has_phrase("You'll never run out. Promise.", "never run out")
    assert has_phrase("NEVER   run out", "never run out")
    assert not has_phrase("never ran out", "never run out")
    assert not has_phrase("never run outside", "never run out")


def test_countable_drops_phrases_the_a_side_also_sees():
    got = countable(["french press", "pile of bags", "pile of bags"], "Desk Blend is for drip and French press.")
    assert got == ["pile of bags"]


def test_metrics_and_totals():
    m = metrics("No “pile of bags” when you're away. Elevate your trip.", ["pile of bags"],
                ["pile of bags", "arrived late"], ["elevate"])
    assert m["given_hits"] == ["pile of bags"] and m["bank_hits"] == ["pile of bags"]
    assert m["generic_hits"] == ["Elevate"] and m["quotation_marks"] == 2
    t = totals([{"A": {"metrics": m, "check": [{"severity": "error"}]}}], "A")
    assert t["with_given_phrase"] == "1/1" and t["posts_with_quotation_marks"] == 1 and t["brand_errors"] == 1


def test_first_sentence_and_opens_with():
    assert first_sentence("  Never run out again! Pause any time.") == "Never run out again!"
    assert first_sentence("Away next week\nPause the box.") == "Away next week"
    assert opens_with("You'll NEVER   run out. Promise.", "never run out")
    assert not opens_with("Stock up today. You'll never run out.", "never run out")  # 2nd sentence only
    assert not opens_with("", "never run out")


def test_pick_open_with_skips_unusable_items():
    items = [{"text": "coffee"}, {"text": "Arrived late again. Third time."}, {"text": "Desk Blend"},
             {"text": "a " * 9}, {"text": "pause my subscription."}, {"text": "never run out"}]
    # 'coffee' is in the brand text, the quote spans two sentences, 'Desk Blend' is a product,
    # 9 words is too long; the trailing period is stripped.
    assert pick_open_with(items, "Northwind coffee. Desk Blend.") == "pause my subscription"
    assert pick_open_with([{"text": "Desk Blend"}], "Desk Blend") is None


class FakeLive:
    def __init__(self, texts):
        self.texts, self.calls = list(texts), []

    def run(self, prompt, vars_):
        self.calls.append(vars_)
        return {"output": {"posts": [{"channel": "x", "text": self.texts.pop(0)}]}}


def test_write_b_retries_once_with_feedback():
    live = FakeLive(["Stock up. Never run out.", "Never run out of beans mid-week."])
    b = write_b(live, {"topic": "t", "channels": "x", "open_with": "never run out"}, "x", "never run out")
    assert b["text"].startswith("Never run out of beans")
    assert b["open_with"] == {"phrase": "never run out", "first_try": False, "attempts": 2,
                              "missed_first_sentence": "Stock up.", "final": True}
    assert "open_with_feedback" not in live.calls[0] and '"Stock up."' in live.calls[1]["open_with_feedback"]
    ok = write_b(FakeLive(["Never run out. Ok."]), {"open_with": "never run out"}, "x", "never run out")
    assert ok["open_with"]["attempts"] == 1 and ok["open_with"]["final"]
    miss = write_b(FakeLive(["No.", "Still no."]), {"open_with": "never run out"}, "x", "never run out")
    rows = [{"B": ok}, {"B": miss}, {"B": write_b(FakeLive(["x" * 20]), {}, "x", None)}]
    assert enforcement(rows) == {"posts_with_open_with": 2, "first_try": "1/2", "after_retry": "1/2", "retries": 1}


def test_bolted_on_catches_pasted_labels_not_sentences():
    assert bolted_on("box arrived late We're sorry to hear it.", "box arrived late")
    assert bolted_on("pause my subscription | When you pause...", "pause my subscription")
    assert bolted_on("never run out with Northwind.", "never run out")  # lowercase paste
    assert bolted_on("☕ Coffee subscription: gifts made easy.", "coffee subscription")
    assert not bolted_on("Afternoon cup, and it's the best part of my day.", "afternoon cup")
    assert not bolted_on("Skipping a delivery is easy though! Log in.", "Skipping a delivery is easy though")
    assert not bolted_on("You'll never run out.", "never run out")  # not at the start
