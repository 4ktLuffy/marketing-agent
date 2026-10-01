"""The chatbot's own NOT IN FACTS list: cut off the answer, and each listed sentence blocks."""
from app import pack, selfaudit


def test_extract_cuts_the_list_and_reads_it():
    text = "=== 1 FACEBOOK ===\nCome to the lake.\nThere's always tea waiting for visitors.\n\nNOT IN FACTS:\n- There's always tea waiting for visitors."
    answer, listed = selfaudit.extract(text)
    assert "NOT IN FACTS" not in answer and answer.endswith("waiting for visitors.")
    assert listed == ["There's always tea waiting for visitors."]


def test_none_and_markdown_forms():
    assert selfaudit.extract("Copy.\n\n**NOT IN FACTS:** none")[1] == []
    assert selfaudit.extract("Copy.\n## Not in facts\n1. \"A DJ plays on Saturdays.\"")[1] == ["A DJ plays on Saturdays."]
    assert selfaudit.extract("Copy without a list.") == ("Copy without a list.", [])


def test_findings_match_whole_or_part_of_a_sentence():
    sents = ["Come to the lake.", "There's always tea waiting for visitors, and a DJ on Saturdays."]
    got = selfaudit.findings("", ["There's always tea waiting for visitors"], sents)
    assert [g["sentence"] for g in got] == [sents[1]] and got[0]["blocking"]
    assert selfaudit.findings("", ["tea"], sents) == []          # too short to match safely


def test_pack_asks_for_the_list_only_when_switched_on(monkeypatch):
    # off by default: in a real trial the lists were all filler ("Order now!"), never an invented offer
    assert "NOT IN FACTS" not in pack._tail()
    monkeypatch.setenv("PACK_SELF_AUDIT", "on")
    assert "NOT IN FACTS" in pack._tail()
