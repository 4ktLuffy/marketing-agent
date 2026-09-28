"""Topic segmentation (app/segments.py) on synthetic transcripts with known boundaries."""
import hashlib
import math
import random

import pytest

from app import main, segments
from app.segments import Segment, is_marker, merge_short, segment
from app.windows import Word, build_windows, sentences

TOPICS = [
    "coffee beans roast grinder brew aroma",
    "invoice payment overdue accountant tax receipt",
    "mountain hiking trail boots summit weather",
    "database index query latency cache shard",
    "garden tomato soil compost seeds water",
    "guitar chord string tuning rhythm melody",
]
FILLER = "we really think this matters for everyone here today"


def fake_embed(texts):
    """Bag of words hashed into 64 dims: texts that share topic words are similar."""
    out = []
    for t in texts:
        v = [0.0] * 64
        for w in t.lower().replace(".", " ").replace(",", " ").split():
            h = int(hashlib.md5(w.encode()).hexdigest(), 16)
            v[h % 64] += 1.0 if w not in FILLER.split() else 0.3
        out.append(v)
    return out


def broken_embed(texts):
    raise ConnectionError("ollama down")


def make_talk(points, per_point=5, markers=True, pause_at_boundary=0.0, seed=0, gap=0.4, words_per_s=2.5):
    """Words of a talk with `points` topics. Returns (words, true boundary times)."""
    rng = random.Random(seed)
    words, t, truth = [], 0.0, []
    ordinals = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth"]
    for p in range(points):
        topic = TOPICS[p % len(TOPICS)].split()
        if p:
            t += pause_at_boundary
            truth.append(round(t, 3))
        for k in range(per_point):
            body = [rng.choice(topic) for _ in range(4)] + rng.sample(FILLER.split(), 4)
            if k == 0 and markers and p:
                body = [ordinals[p] + ","] + body
            for n, w in enumerate(body):
                text = w + ("." if n == len(body) - 1 else "")
                words.append(Word(text, round(t, 3), round(t + 0.3, 3)))
                t += 1 / words_per_s
            t += gap
    return words, truth


def found_times(words, embed):
    sents = sentences(words)
    seg = segment(sents, embed)
    return [sents[i].start for i in seg.boundaries], seg


def pr(found, truth, tol=1.0):
    tp_f = sum(1 for f in found if any(abs(f - t) <= tol for t in truth))
    tp_t = sum(1 for t in truth if any(abs(f - t) <= tol for f in found))
    return (tp_f / len(found) if found else 1.0), (tp_t / len(truth) if truth else 1.0)


# ---------- markers


@pytest.mark.parametrize("text", [
    "Second, we deleted the grid.", "Here is the first one.", "The second lesson surprised me.",
    "Third, rotate who hosts.", "Number four is a little bit silly.", "Number 4 is silly.",
    "And the last one.", "So, to sum up, a fixed time.", "Finally, change one thing.",
    "Another thing we learned is this.", "Next, keep it short.", "Okay, moving on.",
    "Lesson three: ship early.", "Now, the next point is pricing.",
])
def test_markers_found(text):
    assert is_marker(text)


@pytest.mark.parametrize("text", [
    "The first time I tried it, it failed.", "Next week we launch.", "Last year we rebuilt it.",
    "We tried six of them.", "It was the second best call.", "One prospect told us.",
    "People want the first plan.", "Our second redesign changed eight things.",
])
def test_non_markers(text):
    assert not is_marker(text)


# ---------- boundaries: precision / recall on synthetic talks


def test_markers_alone_find_every_point_without_pauses_or_embeddings():
    words, truth = make_talk(5, markers=True)
    found, seg = found_times(words, None)
    assert seg.method == "pauses+markers"
    assert pr(found, truth) == (1.0, 1.0)


def test_embedding_drop_with_a_pause_finds_unmarked_points():
    words, truth = make_talk(5, markers=False, pause_at_boundary=1.8)
    found, seg = found_times(words, fake_embed)
    assert seg.method == "embeddings+pauses+markers"
    assert pr(found, truth) == (1.0, 1.0)


def test_embedding_drop_alone_never_makes_a_boundary():
    # documented design: with a 0.6B embedder the drop is a weak cue (measured), so it only supports
    words, _truth = make_talk(5, markers=False, pause_at_boundary=0.0)
    found, seg = found_times(words, fake_embed)
    assert found == []
    assert max(g["embed"] for g in seg.gaps) > 0.5          # the signal is there, just not enough alone


def test_negative_control_one_topic_no_markers_no_pauses():
    words, _ = make_talk(1, per_point=25, markers=False)
    found, _ = found_times(words, fake_embed)
    assert found == []
    found, _ = found_times(words, None)
    assert found == []


def test_fallback_when_embeddings_fail_uses_pauses_and_markers():
    words, truth = make_talk(4, markers=False, pause_at_boundary=2.0)
    found, seg = found_times(words, broken_embed)
    assert seg.method == "pauses+markers"
    assert "embeddings unavailable (ConnectionError)" in seg.error
    assert pr(found, truth) == (1.0, 1.0)


def test_precision_recall_over_many_synthetic_talks():
    tp_prec, tp_rec = [], []
    for seed in range(12):
        rng = random.Random(seed)
        words, truth = make_talk(rng.randint(3, 6), per_point=rng.randint(4, 7), markers=rng.random() < 0.5,
                                 pause_at_boundary=rng.choice([0.0, 1.0, 1.8]), seed=seed)
        if not truth:
            continue
        found, _ = found_times(words, fake_embed)
        p, r = pr(found, truth)
        tp_prec.append(p)
        tp_rec.append(r)
    # unmarked talks without a pause are misses by design (see above); everything found is right
    assert sum(tp_prec) / len(tp_prec) == 1.0
    assert sum(tp_rec) / len(tp_rec) >= 0.6


def test_boundaries_keep_min_segment_distance_and_are_deterministic():
    words, _ = make_talk(5, per_point=2, markers=True)       # ~7 s points: closer than MIN_SEGMENT_S
    a, seg = found_times(words, fake_embed)
    b, _ = found_times(words, fake_embed)
    assert a == b
    edges = [0.0] + a
    assert all(y - x >= segments.MIN_SEGMENT_S for x, y in zip(edges, edges[1:]))


def test_empty_and_tiny_transcripts():
    assert segment([], fake_embed).segments == []
    words, _ = make_talk(1, per_point=2, markers=False)
    seg = segment(sentences(words), fake_embed)
    assert len(seg.segments) == 1 and seg.method == "pauses+markers"   # < 4 sentences: no embeddings


def test_block_similarity_and_depth():
    sim = [None, 0.9, 0.5, 0.9]
    assert segments.depths(sim) == [0.0, 0.0, 0.8, 0.0]
    emb = [[1, 0], [1, 0], [0, 1], [0, 1]]
    assert segments.block_similarity(emb, 2)[2] == 0.0


# ---------- windows inside segments


def test_windows_never_cross_a_segment_and_may_cover_a_whole_one():
    words, truth = make_talk(4, per_point=12, markers=True)     # ~44 s points
    sents = sentences(words)
    seg = segment(sents, None)
    segs = merge_short(seg, sents, 20)
    wins = build_windows(words, 20, 60, segs)
    assert wins
    for w in wins:
        assert not any(w.start < t < w.end - 0.01 for t in truth), (w.start, w.end)
    whole = {(round(s.start, 2), round(s.end, 2)) for s in segs}
    assert whole & {(round(w.start, 2), round(w.end, 2)) for w in wins}
    # without segments the same talk has windows over two points
    assert any(any(w.start < t < w.end - 0.01 for t in truth) for w in build_windows(words, 20, 60))


def test_long_segment_is_split_at_sentence_ends():
    words, _ = make_talk(1, per_point=40, markers=False)        # ~150 s, one point
    sents = sentences(words)
    segs = merge_short(segment(sents, None), sents, 20)
    wins = build_windows(words, 20, 60, segs)
    assert len(segs) == 1 and len(wins) > 5
    ends = {s.end for s in sents}
    assert all(20 <= w.duration <= 60 and w.end in ends for w in wins)


def _sents_at(times):
    from app.windows import Sentence
    return [Sentence(a, b, i, i, f"s{i}") for i, (a, b) in enumerate(times)]


def test_merge_short_merges_across_the_weaker_boundary_only():
    sents = _sents_at([(0, 10), (10, 30), (30, 36), (36, 60)])
    seg = segments.Segmentation(
        [], "x", gaps=[{"before": 1, "score": 0.9}, {"before": 2, "score": 0.55}, {"before": 3, "score": 0.6}],
        boundaries=[1, 2, 3])
    got = merge_short(seg, sents, 20)
    # 30-36 (6 s) joins 10-30 across 0.55; 0-10 sits behind a strong 0.9 boundary: stays alone
    assert [(s.start, s.end) for s in got] == [(0, 10), (10, 36), (36, 60)]


def test_short_segment_between_strong_boundaries_stays_alone_and_has_no_window():
    sents = _sents_at([(0, 25), (25, 35), (35, 60)])
    seg = segments.Segmentation([], "x", gaps=[{"before": 1, "score": 0.9}, {"before": 2, "score": 0.95}],
                                boundaries=[1, 2])
    got = merge_short(seg, sents, 20)
    assert [(s.start, s.end) for s in got] == [(0, 25), (25, 35), (35, 60)]
    assert isinstance(got[0], Segment)


# ---------- the service


def test_candidate_windows_topic_mode_and_old_mode(monkeypatch):
    words, truth = make_talk(4, per_point=12, markers=True)
    monkeypatch.setenv("EMBED_MODEL", "")                      # no embedder: pauses + markers
    job = {"notes": []}
    topic = main.candidate_windows(words, 20, 60, job)
    assert job["segments"]["method"] == "pauses+markers" and job["segments"]["count"] == 4
    assert all(not any(w.start < t < w.end - 0.01 for t in truth) for w in topic)
    assert [w.id for w in topic] == [f"w{i}" for i in range(1, len(topic) + 1)]
    monkeypatch.setenv("TOPIC_WINDOWS", "false")
    job = {"notes": []}
    old = main.candidate_windows(words, 20, 60, job)
    assert "segments" not in job
    assert any(any(w.start < t < w.end - 0.01 for t in truth) for w in old)


def test_candidate_windows_notes_embedding_failure(monkeypatch):
    words, _ = make_talk(4, per_point=12, markers=True)
    monkeypatch.setattr(main, "embedder", lambda: broken_embed)
    job = {"notes": []}
    assert main.candidate_windows(words, 20, 60, job)
    assert any("embeddings unavailable" in n for n in job["notes"])


def test_candidate_windows_falls_back_when_no_segment_holds_a_window(monkeypatch):
    monkeypatch.setenv("EMBED_MODEL", "")
    # ~11 s points behind strong boundaries (marker + long pause): none may be merged, none holds 20 s
    words, _ = make_talk(6, per_point=3, markers=True, pause_at_boundary=2.0)
    job = {"notes": []}
    wins = main.candidate_windows(words, 20, 60, job)
    assert wins and any("span two points" in n for n in job["notes"])


def test_embedder_config(monkeypatch):
    monkeypatch.setenv("EMBED_MODEL", "")
    assert main.embedder() is None
    monkeypatch.delenv("EMBED_MODEL")
    assert callable(main.embedder())


def test_ollama_embedder_checks_the_shape(monkeypatch):
    class R:
        def __init__(self, n):
            self.n = n

        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[1.0, 0.0]] * self.n}

    calls = []

    class C:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json):
            calls.append((url, json["model"], len(json["input"])))
            return R(len(json["input"]))

    monkeypatch.setattr(segments.httpx, "Client", C)
    out = segments.ollama_embedder("http://o:11434/", "m")(["a"] * 70)
    assert len(out) == 70 and calls == [("http://o:11434/api/embed", "m", 64), ("http://o:11434/api/embed", "m", 6)]
    monkeypatch.setattr(R, "json", lambda self: {"embeddings": [[1.0]]})
    with pytest.raises(ValueError):
        segments.ollama_embedder("http://o", "m")(["a", "b"])
    assert math.isclose(segments._cos([1, 0], [1, 0]), 1.0)


def test_merge_prefers_a_boundary_without_a_marker():
    sents = _sents_at([(0, 30), (30, 40), (40, 70)])
    seg = segments.Segmentation(
        [], "x", gaps=[{"before": 1, "score": 0.5, "marker": 1.0}, {"before": 2, "score": 0.6, "marker": 0.0}],
        boundaries=[1, 2])
    # 30-40 is a tail of the point that follows, not the start of a new point: merge to the right
    assert [(s.start, s.end) for s in merge_short(seg, sents, 20)] == [(0, 30), (30, 70)]


def test_a_short_whole_point_is_kept_and_gets_one_whole_window():
    words, truth = make_talk(3, per_point=12, markers=True)
    sents = sentences(words)
    # a ~17 s point (>= 0.75 x 20 s) between two long ones, opened and closed by markers
    seg = segments.Segmentation([], "x", gaps=[{"before": 12, "score": 0.5, "marker": 1.0},
                                               {"before": 17, "score": 0.5, "marker": 1.0}],
                                boundaries=[12, 17])
    segs = merge_short(seg, sents, 20)
    assert len(segs) == 3 and 15 <= segs[1].duration < 20
    wins = build_windows(words, 20, 60, segs, whole_min_s=15)
    mid = [w for w in wins if segs[1].start <= w.start and w.end <= segs[1].end]
    assert [(w.start, w.end) for w in mid] == [(segs[1].start, segs[1].end)]
    assert not [w for w in build_windows(words, 20, 60, segs) if segs[1].start <= w.start and w.end <= segs[1].end]


def test_scoring_mode_default_is_score(monkeypatch):
    monkeypatch.delenv("SCORING_MODE", raising=False)
    assert main.scoring_mode() == "score"
    monkeypatch.setenv("SCORING_MODE", "rank")
    assert main.scoring_mode() == "rank"
    monkeypatch.setenv("SCORING_MODE", "bogus")
    assert main.scoring_mode() == "score"
