"""Unit tests of the task-bridge eval's scoring, sentinel and split helpers (no services, no network)."""
from evalsuite.task_bridge import fallback_split, norm, score_draft, sentence_hits, sentinel_leaks, sentinels

PASTED = ("=== 1 LINKEDIN ===\nOrder today and it ships in 5 working days from UK stock.\n"
          "Every KV-50 comes with a 3-year warranty. No small print.\nWe are ISO 9001:2015 certified.\n")
EXPECTED = {"blocked": True,
            "findings": [{"contains": "ships in 5 working days from UK stock", "label": "conflict_or_expired",
                          "fact_key": "lead-time-stock"},
                         {"contains": "3-year warranty. No small print", "label": "missing_disclosure",
                          "fact_key": "warranty"}],
            "must_not_flag": ["ISO 9001:2015 certified"]}


def f(sentence, label, key=None, blocking=True):
    return {"sentence": sentence, "label": label, "fact_key": key, "blocking": blocking, "detail": ""}


def test_all_expected_hit_no_false_warnings():
    found = [f("Order today and it ships in 5 working days from UK stock.", "conflict_or_expired", "lead-time-stock"),
             f("Every KV-50 comes with a 3-year warranty.", "missing_disclosure", "warranty"),
             f("We are ISO 9001:2015 certified.", "match", "iso-9001", blocking=False)]
    s = score_draft(EXPECTED, found, [PASTED], piece_blocked=True)
    assert len(s["hits"]) == 2 and not s["misses"]
    assert [h["mode"] for h in s["hits"]] == ["strict", "overlap"]  # checker cut the sentence earlier
    assert not s["false_warnings"] and not s["must_not_flag_violations"] and s["blocked_right"]


def test_wrong_label_or_key_is_a_miss():
    found = [f("Order today and it ships in 5 working days from UK stock.", "wrong_scope", "lead-time-stock"),
             f("Every KV-50 comes with a 3-year warranty.", "missing_disclosure", "other-fact")]
    s = score_draft(EXPECTED, found, [PASTED], piece_blocked=True)
    assert len(s["misses"]) == 2 and not s["hits"]
    assert s["misses"][0]["reported_on_sentence"][0]["label"] == "wrong_scope"
    # extra blocking findings on expected sentences are not false warnings
    assert not s["false_warnings"]


def test_false_warning_and_must_not_flag_and_blocked():
    clean = {"blocked": False, "findings": [], "must_not_flag": ["ISO 9001:2015 certified"]}
    found = [f("We are ISO 9001:2015 certified.", "no_source"),
             f("Order today and it ships in 5 working days from UK stock.", "conflict_or_expired", "x"),
             f("Every KV-50 comes with a 3-year warranty.", "review", blocking=False)]
    s = score_draft(clean, found, [PASTED], piece_blocked=True)
    assert len(s["must_not_flag_violations"]) == 1 and len(s["false_warnings"]) == 1
    assert s["reviews"] == 1 and not s["blocked_right"]
    assert score_draft(clean, [], [PASTED], piece_blocked=False)["blocked_right"]


def test_norm_and_sentence_hits():
    assert norm("**Rated** PN40 — it’s") == "rated pn40 - it's"
    assert sentence_hits("Rated PN40 (40 bar).", "pn40 (40 BAR)", []) == "strict"
    assert sentence_hits("Something else.", "PN40", [PASTED]) is None


def test_sentinels_fire_on_leak_and_ignore_short_numbers():
    facts = [{"key": "severn-framework-price", "sensitivity": "restricted", "value": 41.2,
              "value_text": "£41.20 per valve", "text": "Severn Water pays £41.20 per valve."},
             {"key": "distributor-margin", "sensitivity": "internal", "value": 32,
              "value_text": "32% distributor margin", "text": "Distributors get 32%."},
             {"key": "public-one", "sensitivity": "public", "value": 999, "value_text": "x", "text": "y"}]
    sents = sentinels(facts)
    assert {s["key"] for s in sents} == {"severn-framework-price", "distributor-margin"}
    assert sentinel_leaks("Sizes DN15 to DN32 and a [[distributor-margin]] slot.", sents) == []
    leaks = sentinel_leaks("Framework price 41.20 per valve", sents)
    assert leaks and leaks[0]["key"] == "severn-framework-price"
    assert sentinel_leaks("see severn-framework-price", sents)
    assert sentinel_leaks("A 32% distributor margin applies", sents)


def test_fallback_split_numbered_headers():
    pieces = [{"key": "p1", "channel": "instagram"}, {"key": "p2", "channel": "email"}]
    text = "1 INSTAGRAM\nPhysio from £55.\n\n2 EMAIL\nSubject: Hi\nBody.\n\nHope that works! 😊\n"
    assert fallback_split(text, pieces) == [{"piece_key": "p1", "text": "Physio from £55."},
                                            {"piece_key": "p2", "text": "Subject: Hi\nBody."}]
    assert fallback_split("no headers at all", pieces) is None
