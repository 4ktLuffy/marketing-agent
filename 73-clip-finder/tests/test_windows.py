from app import captions, scoring
from app.windows import Window, Word, build_windows, pick, sentences, snap, snap_all, thin


def talk(sentence_lengths, words_per_s=2.5, pause=0.6):
    """Words for sentences of the given word counts, evenly spaced, with a pause between."""
    words, t = [], 0.0
    for n, count in enumerate(sentence_lengths):
        for k in range(count):
            text = f"s{n}w{k}" + ("." if k == count - 1 else "")
            words.append(Word(text, round(t, 3), round(t + 0.3, 3)))
            t += 1 / words_per_s
        t += pause
    return words


def win(start, end, score=None, wid="w"):
    w = Window(wid, start, end, 0, 0, "", "")
    w.score = score
    return w


# ---------- sentences and windows


def test_sentences_split_on_full_stops_and_long_pauses():
    words = [Word("Hello", 0, 0.4), Word("there.", 0.5, 0.9), Word("And", 1.0, 1.2), Word("then", 1.3, 1.5),
             Word("silence", 3.0, 3.4), Word("ends?", 3.5, 3.9)]
    s = sentences(words)
    assert [x.text for x in s] == ["Hello there.", "And then", "silence ends?"]
    assert (s[1].first, s[1].last) == (2, 3)


def test_run_on_speech_is_cut_before_hard_max():
    words = [Word(f"w{i}", i * 0.4, i * 0.4 + 0.3) for i in range(200)]  # 80 s, no punctuation
    assert all(s.end - s.start <= 25.5 for s in sentences(words))


def test_windows_are_whole_sentences_within_min_and_max():
    words = talk([10] * 30)  # ~4.6 s per sentence
    ws = build_windows(words, 20, 60)
    assert ws
    starts = {s.start for s in sentences(words)}
    ends = {s.end for s in sentences(words)}
    for w in ws:
        assert 20 <= w.duration <= 60
        assert w.start in starts and w.end in ends
        assert w.text.startswith("s") and w.text.endswith(".")


def test_opening_is_the_first_three_seconds():
    words = talk([20] * 10)
    w = build_windows(words, 20, 60)[0]
    assert w.opening and w.text.startswith(w.opening)
    assert len(w.opening.split()) == 8  # 2.5 words/s over 3 s, starting at 0


def test_thin_spaces_starts_caps_and_numbers_ids():
    words = talk([6] * 80)
    ws = build_windows(words, 20, 60)
    kept = thin(ws, stride_s=10, cap=12)
    assert len(kept) == 12
    assert [w.id for w in kept] == [f"w{i}" for i in range(1, 13)]
    assert [w.start for w in kept] == sorted(w.start for w in kept)


def test_too_short_source_has_no_windows():
    assert build_windows(talk([5]), 20, 60) == []


# ---------- selection


def test_pick_takes_best_non_overlapping_in_time_order():
    ws = [win(0, 30, 90, "a"), win(20, 50, 95, "b"), win(40, 70, 80, "c"), win(100, 130, 50, "d"),
          win(60, 90, None, "e")]
    chosen = pick(ws, 3)
    # b (95) wins; a and c overlap it; d is next; e was never scored.
    assert [w.id for w in chosen] == ["b", "d"]
    assert [w.id for w in pick(ws, 1)] == ["b"]


def test_pick_respects_max_clips_and_min_score():
    ws = [win(i * 40, i * 40 + 30, 50 + i, f"w{i}") for i in range(6)]
    assert len(pick(ws, 2)) == 2
    assert [w.id for w in pick(ws, 2)] == ["w4", "w5"]
    assert [w.id for w in pick(ws, 10, min_score=54)] == ["w4", "w5"]


# ---------- snapping


def snap_case():
    words = [Word("before.", 0.0, 0.5), Word("First", 3.0, 3.4), Word("word", 3.5, 3.9),
             Word("last.", 30.0, 30.5), Word("After", 33.0, 33.4)]
    w = Window("w1", 3.0, 30.5, 1, 3, "First word last.", "First word")
    return words, w


def test_snap_prefers_scene_cuts_within_range():
    words, w = snap_case()
    start, end, how = snap(w, words, cuts=[2.0, 31.2], silences=[], media_s=40, max_s=60)
    assert (start, end) == (2.0, 31.2) and how == {"start": "scene", "end": "scene"}


def test_snap_ignores_far_cuts_and_uses_silences():
    words, w = snap_case()
    start, end, how = snap(w, words, cuts=[0.9, 32.5], silences=[(0.6, 2.95), (30.55, 32.9)],
                           media_s=40, max_s=60)
    assert how == {"start": "silence", "end": "silence"}
    assert 2.7 < start < 3.0 and 30.5 < end < 31.0


def test_snap_never_cuts_into_the_neighbouring_words():
    words = [Word("prev.", 0, 2.8), Word("First", 3.0, 3.4), Word("last.", 25.0, 25.5), Word("next", 25.6, 26)]
    w = Window("w1", 3.0, 25.5, 1, 2, "First last.", "First")
    start, end, _ = snap(w, words, cuts=[2.0, 27.0], silences=[], media_s=40, max_s=60)
    assert start >= 2.8 and end <= 25.6


def test_snap_keeps_max_length():
    words, w = snap_case()
    start, end, _ = snap(w, words, cuts=[1.6, 31.9], silences=[], media_s=40, max_s=28)
    assert end - start <= 28.0001


def test_snap_pads_without_cuts_or_silences():
    words, w = snap_case()
    start, end, how = snap(w, words, [], [], media_s=40, max_s=60)
    assert how == {"start": "pad", "end": "pad"}
    assert start < 3.0 and end > 30.5


# ---------- captions


def test_caption_lines_are_short_and_clip_relative():
    words = talk([12, 12])
    lines = captions.lines(words, 1.0, 12.0)
    assert lines and lines[0].start >= 0
    for ln in lines:
        assert len(ln.words) <= captions.MAX_WORDS
        assert ln.end <= 11.0 + 1e-6
    ass = captions.ass(lines, "Arial")
    assert ass.count("Dialogue:") == len(lines) and "{\\k" in ass and "PlayResY: 1920" in ass
    srt = captions.srt(lines)
    assert srt.startswith("1\n00:00:0") and "-->" in srt


def test_caption_text_cannot_inject_ass_overrides():
    words = [Word("{\\an8\\pos(0,0)}hack", 0, 0.5), Word("ok", 0.6, 1.0)]
    out = captions.ass(captions.lines(words, 0, 2), "Arial")
    event = out.split("Dialogue:")[1]
    assert "\\an8" not in event and "\\pos" not in event and event.count("{") == 2


# ---------- scoring answers are checked in code


def scored_window():
    return Window("w1", 0, 30, 0, 5, "Nobody on a remote team drinks coffee together. That is the problem.",
                  "Nobody on a remote team")


def test_apply_clamps_and_weighs_scores():
    w = scored_window()
    n = scoring.apply([w], {"scores": [{"id": "w1", "hook": 12, "standalone": 8, "payoff": -3, "quotable": "7",
                                        "title": "Remote teams and coffee", "hook_line": "That is the problem."}]})
    assert n == 1 and w.scores == {"hook": 10, "standalone": 8, "payoff": 0, "quotable": 7}
    assert w.score == round(10 * (0.35 * 10 + 0.25 * 8 + 0.15 * 7), 1)
    assert w.hook == "That is the problem." and w.title == "Remote teams and coffee"


def test_apply_replaces_invented_hook_and_title_numbers():
    w = scored_window()
    scoring.apply([w], {"scores": [{"id": "w1", "hook": 5, "standalone": 5, "payoff": 5, "quotable": 5,
                                    "title": "73% of remote teams skip coffee",
                                    "hook_line": "Coffee boosts output by half"}]})
    assert w.hook.startswith("Nobody on a remote team")
    assert "73" not in w.title and w.title.startswith("Nobody")


def test_apply_ignores_unknown_ids_and_bad_items():
    w = scored_window()
    assert scoring.apply([w], {"scores": [{"id": "w9", "hook": 9, "standalone": 9, "payoff": 9, "quotable": 9},
                                          "junk", {"id": "w1", "hook": "high"}]}) == 0
    assert w.score is None


def test_window_block_shows_opening_and_times():
    w = scored_window()
    w.start, w.end = 65, 108
    block = scoring.window_block([w])
    assert block.startswith("[w1] 01:05-01:48 (43 s)") and "FIRST 3 SECONDS: Nobody" in block


def test_snap_all_settles_overlap_between_neighbours():
    words = [Word("One.", 0.0, 0.5), Word("two.", 10.0, 10.4), Word("Three.", 10.5, 11.0), Word("four.", 20, 20.5)]
    a = Window("w1", 0.0, 10.4, 0, 1, "One. two.", "One.")
    b = Window("w2", 10.5, 20.5, 2, 3, "Three. four.", "Three.")
    (s1, e1, _), (s2, e2, _) = snap_all([a, b], words, [], [], media_s=30, max_s=60)
    assert e1 <= s2 and e1 >= 10.4 and s2 <= 10.5


def test_long_hook_is_cut_at_a_word_boundary():
    text = "When the host changes every week, the host picks the opening question, and suddenly the quiet " \
           "people have a reason to speak up in the meeting."
    w = Window("w1", 0, 30, 0, 5, text, "When the host")
    scoring.apply([w], {"scores": [{"id": "w1", "hook": 5, "standalone": 5, "payoff": 5, "quotable": 5,
                                    "title": "Rotate the host", "hook_line": text}]})
    assert len(w.hook) <= scoring.HOOK_MAX and text.startswith(w.hook) and text[len(w.hook)] == " "
