"""Ranking by comparison (app/ranking.py): model answers checked in code, the tournament,
distinct ranks, deterministic tie-breaks."""
import pytest

from app import ranking, scoring
from app.windows import Window


def win(i, text, start=None, dur=30.0):
    start = i * 10.0 if start is None else start
    return Window(f"w{i}", start, start + dur, 0, 0, text, text[:30])


def ids(ws):
    return [w.id for w in ws]


# ---------- code features


@pytest.mark.parametrize("text,hook,standalone", [
    ("Why do remote coffee calls fail? Nobody plans them.", 2, 1),          # question + contrast
    ("We doubled attendance in a month. It was easy.", 1, 1),               # number word
    ("The moment you add an agenda, it stops being a coffee call. Then", 0, 1),
    ("The moment you add an agenda, it's not a coffee call anymore.", 1, 1),  # contrast word
    ("Here is the first one. A coffee break only works if it is weekly.", 1, 0),  # "one" counts; "Here" dangles
    ("And that is why it worked for 3 teams.", 1, 0),                        # digit, dangling "and"
    ("So, um, where was I.", 0, 0),
])
def test_features_hook_and_dangling_start(text, hook, standalone):
    f = ranking.features(win(1, text))
    assert (f["hook"], f["standalone"]) == (hook, standalone)


def test_length_fit():
    assert ranking.features(win(1, "A b.", dur=30))["length_fit"] == 1.0
    assert ranking.features(win(1, "A b.", dur=55))["length_fit"] == 0.5
    assert ranking.features(win(1, "A b.", dur=15))["length_fit"] == 0.5


# ---------- one batch


def test_order_from_ignores_unknown_and_repeated_ids_and_appends_missing_by_features():
    b = [win(1, "So this is it."), win(2, "Why does it fail? Nobody plans it."), win(3, "We tried."),
         win(4, "It works.")]
    for w in b:
        w.features = ranking.features(w)
    order, n = ranking.order_from(b, {"ranking": ["w3", "[w1]", "w9", "w3", 7]})
    assert n == 2
    # model's w3, w1 first; then the rest by features: w2 (hook 2, standalone) before w4 (dangling "it")
    assert ids(order) == ["w3", "w1", "w2", "w4"]


def test_notes_are_kept_and_checked(monkeypatch):
    b = [win(1, "Nobody came to the random slots. Monday at ten doubled it."), win(2, "Cameras on, no agenda.")]
    ranking.apply_notes(b, {"notes": [
        {"id": "w1", "hook": "strong contrast", "standalone": "clear", "payoff": "number", "quotable": "yes",
         "title": "Attendance up 300%", "hook_line": "Nobody came to the random slots.", "reason": "best of the two"},
        {"id": "w1", "hook": "a second note for w1 is ignored"},
    ]})
    w1, w2 = b
    assert w1.criteria == {"hook": "strong contrast", "standalone": "clear", "payoff": "number", "quotable": "yes"}
    assert w1.hook == "Nobody came to the random slots."
    assert w1.title == "Nobody came to the random slots. Monday at ten"  # 300 is not in the window
    assert w1.reason == "best of the two"
    # no note for w2: opening words, no invented reason
    assert w2.criteria == {} and w2.title == "Cameras on, no agenda." and w2.reason is None


# ---------- the tournament


def fake_gateway(prefer, calls):
    """A model that ranks by a hidden preference (higher = better) and writes notes."""
    def run(vars_, timeout, prompt=scoring.PROMPT):
        assert prompt == ranking.PROMPT
        calls.append(vars_)
        batch = [line.split("]")[0][1:] for line in vars_["windows"].splitlines() if line.startswith("[w")]
        order = sorted(batch, key=lambda i: -prefer[i])
        out = {"ranking": order}
        if vars_.get("notes"):
            out["notes"] = [{"id": i, "hook": f"note {i}", "title": f"Title {i}", "hook_line": "", "reason": "r"}
                            for i in batch]
        return out
    return run


def test_tournament_finds_the_global_order_with_distinct_ranks(monkeypatch):
    ws = [win(i, f"Sentence number {i} is here.") for i in range(1, 15)]  # 14 windows
    prefer = {w.id: p for w, p in zip(ws, [3, 9, 1, 4, 12, 2, 7, 13, 5, 0, 6, 11, 8, 10])}
    calls = []
    monkeypatch.setattr(scoring, "run_prompt", fake_gateway(prefer, calls))
    stats = ranking.rank(ws, 6, 10)
    # round 1: 3 even batches (4, 5, 5) with notes; round 2: 6 finalists, one batch, order only
    assert [len(c["windows"].split("\n\n")) for c in calls] == [4, 5, 5, 6]
    assert [bool(c.get("notes")) for c in calls] == [True, True, True, False]
    assert stats["rounds"] == 2 and stats["failed_batches"] == 0 and stats["scored"] == 14
    by_rank = sorted(ws, key=lambda w: w.rank)
    # the 6 finalists (top 2 of each batch) in the model's order. A tournament is not a full
    # sort: w13 (6th best overall) lost to w12 and w14 in its batch, w4 (7th) went on instead.
    assert ids(by_rank[:6]) == ["w8", "w5", "w12", "w14", "w2", "w4"]
    # the 3rd places of the 3 batches (w1, w7, w13) tie on (round, place): equal features, so
    # time order decides, not the model's hidden preference
    assert ids(by_rank[6:9]) == ["w1", "w7", "w13"]
    assert sorted(w.rank for w in ws) == list(range(1, 15))
    assert len({w.score for w in ws}) == 14 and by_rank[0].score == 100.0
    assert by_rank[0].criteria == {"hook": "note w8"} and by_rank[0].title == "Title w8"


def test_same_place_in_different_batches_is_broken_by_features(monkeypatch):
    # two batches of 3; windows placed 3rd in each batch tie on (round, place)
    ws = [win(1, "It went fine."), win(2, "Why it works? Nobody knows."), win(3, "We met weekly."),
          win(4, "And then more."), win(5, "What failed? Two things."), win(6, "The call was short.")]
    prefer = {"w1": 0, "w2": 9, "w3": 5, "w4": 1, "w5": 8, "w6": 6}
    monkeypatch.setattr(scoring, "run_prompt", fake_gateway(prefer, []))
    ranking.rank(ws, 3, 10)
    by_rank = sorted(ws, key=lambda w: w.rank)
    # w1 and w4 are both 3rd in their batch and both open on a dangling word ("It", "And"):
    # equal features, so the earlier start comes first
    assert ids(by_rank)[-2:] == ["w1", "w4"]


def test_failed_batch_falls_back_to_features_and_total_failure_raises(monkeypatch):
    ws = [win(i, t) for i, t in enumerate(["So yes.", "Why? Nobody knows.", "We met.", "It was ok."], 1)]

    def broken(vars_, timeout, prompt=scoring.PROMPT):
        raise scoring.ScoringFailed("gateway 503: down")
    monkeypatch.setattr(scoring, "run_prompt", broken)
    with pytest.raises(scoring.ScoringFailed, match="no batch could be ranked"):
        ranking.rank(ws, 6, 10)

    ws2 = [win(i, t) for i, t in enumerate(["So yes.", "Why? Nobody knows.", "We met.", "It was ok."], 1)] \
        + [win(i, t, start=100 + i * 10) for i, t in [(5, "Good one."), (6, "Fine."), (7, "Also fine.")]]
    # batch 1 (w1-w4): two useless answers -> feature order; batch 2 (w5-w7) fine; final round fine
    good = iter([{"ranking": []}, {"ranking": []}, {"ranking": ["w6", "w5", "w7"]}, {"ranking": ["w6", "w2", "w5"]}])
    monkeypatch.setattr(scoring, "run_prompt", lambda v, t, prompt=None: next(good))
    stats = ranking.rank(ws2, 4, 10)
    assert stats["failed_batches"] == 1  # first batch: ordered by features, not an error for the job
    assert len({w.rank for w in ws2}) == 7


def test_split_is_even_and_never_leaves_tiny_batches():
    ws = [win(i, "x.") for i in range(31)]
    assert [len(b) for b in ranking.split(ws, 6)] == [5, 5, 5, 5, 5, 6]
    assert [len(b) for b in ranking.split(ws[:4], 3)] == [4]
    assert [len(b) for b in ranking.split(ws[:7], 6)] == [3, 4]
    assert [len(b) for b in ranking.split(ws[:2], 6)] == [2]


def test_rank_is_deterministic_given_the_same_answers(monkeypatch):
    def make():
        return [win(i, f"Point {i} matters.") for i in range(1, 9)]
    prefer = {f"w{i}": p for i, p in zip(range(1, 9), [2, 2, 2, 2, 5, 5, 5, 5])}
    monkeypatch.setattr(scoring, "run_prompt", fake_gateway(prefer, []))
    a, b = make(), make()
    ranking.rank(a, 4, 10)
    ranking.rank(b, 4, 10)
    assert [w.rank for w in a] == [w.rank for w in b]


def test_negative_control_absolute_scores_tie_where_ranking_does_not(monkeypatch):
    """The failure that motivated this: identical absolute scores for two windows make the
    pick depend on the start time alone; ranking always separates them."""
    a, b = win(1, "Rotate the host."), win(2, "Mail the same beans.")
    same = {"hook": 6, "standalone": 4, "payoff": 5, "quotable": 7, "title": "t", "hook_line": "", "reason": "same"}
    scoring.apply([a, b], {"scores": [{"id": "w1", **same}, {"id": "w2", **same}]})
    assert a.score == b.score
    c, d = win(1, "Rotate the host."), win(2, "Mail the same beans.")
    monkeypatch.setattr(scoring, "run_prompt", lambda v, t, prompt=None: {"ranking": ["w2", "w1"]})
    ranking.rank([c, d], 6, 10)
    assert (c.rank, d.rank) == (2, 1) and c.score != d.score
