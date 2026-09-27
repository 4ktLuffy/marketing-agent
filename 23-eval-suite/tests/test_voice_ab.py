from pathlib import Path

import yaml

from evalsuite.voice_ab import answers_text, hits, metrics, sentence_target, sentences, verdict

CASE = Path(__file__).parent.parent / "cases" / "voice" / "voice_ab.yaml"


def test_case_file_is_complete():
    case = yaml.safe_load(CASE.read_text())
    assert len(case["answers"]) == 10 and all(r["q"] and r["a"] for r in case["answers"])
    ids = [t["id"] for t in case["topics"]]
    assert len(ids) >= 8 and len(set(ids)) == len(ids)
    assert len({t["channel"] for t in case["topics"]}) >= 4
    assert "Q: Who do you talk to?" in answers_text(case)


def test_sentences_ignore_links_tags_and_emoji():
    s = sentences("Fresh beans. At your desk by Friday! ☕ #wfh https://x.example.com/a\nNew line here")
    assert [len(x) for x in s] == [2, 5, 3]


def test_sentence_target_reads_profile_or_defaults():
    assert sentence_target({"sentence_style": "Short, mostly under 12 words"}) == 12
    assert sentence_target({"sentence_style": "plain", "do": ["Sentences under 10 words"]}) == 10
    assert sentence_target({}) == 15


def test_hits_whole_words_with_inflections():
    text = "Elevating your morning, unlock flavour. Unlocked. In today's fast-paced world, leverage."
    assert hits(text, ["elevate", "unlock", "in today's fast-paced", "leverage"]) == \
        ["Elevating", "unlock", "Unlocked", "In today's fast-paced", "leverage"]
    assert hits("Harnessed? No: the harness shop, elevator.", ["elevate"]) == []


def test_metrics_counts():
    m = metrics("It's good. It turns up! Seamless.", {"words_we_avoid": ["seamless"],
                                                     "sentence_style": "under 2 words"}, ["tapestry"])
    assert m["sentences"] == 3 and m["over_target"] == 1 and m["avoid_hits"] == ["Seamless"]
    assert m["generic_hits"] == [] and m["exclamations"] == 1


def test_verdict_needs_both_orders():
    assert verdict(1, 1, 2) == "B"      # B at 1, then B at 2: picked B both times
    assert verdict(2, 2, 1) == "B"
    assert verdict(1, 2, 1) == "A"
    assert verdict(1, 1, 1) == "tie"    # always position 1: position bias, not a preference
