"""Experiments: lifecycle, assignment, the look schedule, and the verdict rule in simulation."""
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pytest
import respx
from fastapi.testclient import TestClient

from app import main  # noqa: F401  (import order: main includes the experiments router)
from app import experiments as ex
from app.expstats import analyze, dispersion, hdi

KEY = "test-key"
APPROVER = "approver-key"
AUTH = {"X-API-Key": KEY}
APPROVE = {**AUTH, "X-Approver-Key": APPROVER}
SHORT = "http://shortener.test"
ENGINE = "http://engine.test"
T0 = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)   # a Monday

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "campaigns.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("APPROVER_KEY", APPROVER)
    monkeypatch.setenv("SHORTENER_URL", SHORT)
    monkeypatch.setenv("ENGINE_URL", ENGINE)
    set_now(monkeypatch, T0)


def set_now(monkeypatch, dt):
    monkeypatch.setattr(ex, "now", lambda: dt)


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as r:
        yield r


def body(**kw):
    return {"hypothesis": "Because question hooks got more replies, we believe they earn more clicks on X.",
            "variable": "hook_style", "channels": ["X"],
            "arms": [{"value": "question"}, {"value": "fact_led", "brief": "open with a number"}], **kw}


def create(**kw):
    r = client.post("/experiments", json=body(**kw), headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def approve(eid):
    r = client.post(f"/experiments/{eid}/status", json={"status": "approved", "by": "Alex"}, headers=APPROVE)
    assert r.status_code == 200, r.text
    return r.json()


def slots(n, start_id=1, day0=T0):
    out = []
    for i in range(n):
        d = (day0 + timedelta(days=i // 2)).date().isoformat()
        out.append({"slot_id": start_id + i, "arm": "AB"[i % 2], "channel": "x", "date": d,
                    "time_utc": "09:15" if i % 2 else "15:30", "item_id": 100 + start_id + i})
    return out


# ---------- lifecycle


def test_create_shape_and_defaults():
    e = create(created_by="agent")
    assert e["status"] == "proposed" and e["channels"] == ["x"] and e["metric"] == "clicks_72h"
    assert e["min_posts_per_arm"] == 12 and e["max_weeks"] == 8 and e["rope"] == 0.15
    assert e["arms"] == [{"label": "A", "value": "question", "brief": None},
                         {"label": "B", "value": "fact_led", "brief": "open with a number"}]
    assert e["assigned"] == {"A": 0, "B": 0} and e["looks"] == [] and e["created_by"] == "agent"


@pytest.mark.parametrize("patch, msg", [
    ({"arms": [{"value": "question"}]}, "exactly 2 arms"),
    ({"arms": [{"value": "a"}, {"value": "b"}, {"value": "c"}], "variable": "cta"}, "exactly 2 arms"),
    ({"arms": [{"value": "question"}, {"value": "shouting"}]}, "not one of"),
    ({"arms": [{"value": "question"}, {"value": "Question"}]}, "must differ"),
    ({"variable": "colour"}, "unknown variable"),
    ({"metric": "impressions"}, "clicks_72h"),
    ({"variable": "time", "arms": [{"value": "09:00"}, {"value": "late"}]}, "HH:MM"),
    ({"channels": []}, "at least 1"),
])
def test_create_validation(patch, msg):
    r = client.post("/experiments", json=body(**patch), headers=AUTH)
    assert r.status_code == 422 and msg in r.text


def test_writes_need_key():
    assert client.post("/experiments", json=body()).status_code == 401


def test_approval_needs_approver_key():
    e = create()
    r = client.post(f"/experiments/{e['id']}/status", json={"status": "approved"}, headers=AUTH)
    assert r.status_code == 403
    a = approve(e["id"])
    assert a["status"] == "approved" and a["approved_by"] == "Alex" and a["approved_at"]


def test_transitions():
    e = create()
    r = client.post(f"/experiments/{e['id']}/status", json={"status": "running"}, headers=AUTH)
    assert r.status_code == 409 and r.json()["detail"]["allowed"] == ["approved", "stopped"]
    r = client.post(f"/experiments/{e['id']}/status", json={"status": "decided"}, headers=AUTH)
    assert r.status_code == 422
    stopped = client.post(f"/experiments/{e['id']}/status", json={"status": "stopped", "reason": "not now"},
                          headers=AUTH).json()
    assert stopped["status"] == "stopped" and stopped["stop_reason"] == "not now"


def test_same_test_twice_is_refused_while_open_and_after_no_difference():
    e = create()
    r = client.post("/experiments", json=body(arms=[{"value": "fact_led"}, {"value": "question"}]), headers=AUTH)
    assert r.status_code == 409 and r.json()["detail"]["experiment_id"] == e["id"]
    # other channel: fine
    assert client.post("/experiments", json=body(channels=["linkedin"]), headers=AUTH).status_code == 201
    with ex.edb() as conn:
        conn.execute("UPDATE experiments SET status = 'decided', decision = 'no_practical_difference' WHERE id = ?",
                     (e["id"],))
    r = client.post("/experiments", json=body(), headers=AUTH)
    assert r.status_code == 409 and r.json()["detail"]["decision"] == "no_practical_difference"
    with ex.edb() as conn:   # a winner may be replicated
        conn.execute("UPDATE experiments SET decision = 'winner', winner_arm = 'A' WHERE id = ?", (e["id"],))
    assert client.post("/experiments", json=body(), headers=AUTH).status_code == 201


def test_assign_starts_approved_experiment_and_upserts():
    e = create()
    r = client.post(f"/experiments/{e['id']}/assign", json={"slots": slots(2)}, headers=AUTH)
    assert r.status_code == 409   # proposed: not approved by a person yet
    approve(e["id"])
    r = client.post(f"/experiments/{e['id']}/assign", json={"slots": slots(4)}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running" and r.json()["by_arm"] == {"A": 2, "B": 2}
    assert r.json()["started_at"] == "2026-10-05T08:00:00Z"
    again = client.post(f"/experiments/{e['id']}/assign",
                        json={"slots": [dict(slots(1)[0], arm="B")], "remove_slot_ids": [4]}, headers=AUTH).json()
    assert again["by_arm"] == {"A": 1, "B": 2} and again["removed"] == 1
    rows = client.get(f"/experiments/{e['id']}/assignments").json()
    one = next(r for r in rows if r["slot_id"] == 1)
    assert one["block"] == "mon-pm" and one["arm"] == "B" and rows[0]["block"] == "mon-am"
    bad = dict(slots(1)[0], channel="linkedin")
    assert client.post(f"/experiments/{e['id']}/assign", json={"slots": [bad]}, headers=AUTH).status_code == 422


def test_at_most_two_running_per_channel():
    ids = []
    for arms in (["question", "fact_led"], ["story", "how_to"], ["benefit", "contrarian"]):
        e = create(arms=[{"value": arms[0]}, {"value": arms[1]}])
        approve(e["id"])
        ids.append(e["id"])
    for eid in ids[:2]:
        assert client.post(f"/experiments/{eid}/status", json={"status": "running"}, headers=AUTH).status_code == 200
    r = client.post(f"/experiments/{ids[2]}/assign", json={"slots": slots(2)}, headers=AUTH)
    assert r.status_code == 409 and "2 running" in r.text


# ---------- looks and the analysis


def links_for(clicks_by_item, published):
    return [{"slug": f"s{i}", "url": f"https://ex.com/p?utm_content={i}", "clicks": c + 3,
             "clicks_window": c, "created_at": published[i]} for i, c in clicks_by_item.items()]


def running(monkeypatch, n=24, **kw):
    e = create(**kw)
    approve(e["id"])
    s = slots(n)
    client.post(f"/experiments/{e['id']}/assign", json={"slots": s}, headers=AUTH)
    return e, s


def test_no_peeking_before_the_first_look(monkeypatch, mock):
    e, _ = running(monkeypatch)
    set_now(monkeypatch, T0 + timedelta(days=6, hours=23))
    a = client.get(f"/experiments/{e['id']}/analysis").json()
    assert a["decision"] == "not_due" and a["look"] == 0 and "lift_hdi" not in a
    assert a["next_look_at"] == "2026-10-12T08:00:00Z"
    r = client.post(f"/experiments/{e['id']}/decide", headers=AUTH)
    assert r.status_code == 409 and r.json()["detail"]["next_look_at"] == "2026-10-12T08:00:00Z"
    assert not mock.calls   # no data was even read


def test_winner_at_first_look_counts_only_closed_windows(monkeypatch, mock):
    e, s = running(monkeypatch, n=30, min_posts_per_arm=6)
    rng = np.random.default_rng(3)
    clicks, published = {}, {}
    for i, sl in enumerate(s):
        item = sl["item_id"]
        clicks[item] = int(rng.poisson(4 if sl["arm"] == "A" else 12))
        published[item] = (T0 + timedelta(hours=6 * i)).isoformat()   # the last ones close after day 7
    mock.get(f"{SHORT}/links").mock(return_value=httpx.Response(200, json=links_for(clicks, published)))
    set_now(monkeypatch, T0 + timedelta(days=7, hours=1))
    a = client.get(f"/experiments/{e['id']}/analysis").json()
    assert a["due"] is True and a["look"] == 1 and a["cutoff"] == "2026-10-12T08:00:00Z"
    assert a["arms"]["A"]["pending"] + a["arms"]["B"]["pending"] > 0          # 72 h not over yet
    assert a["arms"]["A"]["posts"] + a["arms"]["B"]["posts"] + a["arms"]["A"]["pending"] + a["arms"]["B"]["pending"] == 30
    assert a["decision"] == "winner" and a["winner"] == "B" and a["winner_value"] == "fact_led"
    assert a["lift_hdi"][0] > 0.15 and "winner: fact_led over question" in a["summary"]
    assert mock.calls[0].request.url.params["window_hours"] == "72"
    d = client.post(f"/experiments/{e['id']}/decide", headers=AUTH).json()
    assert d["status"] == "decided" and d["experiment"]["decision"] == "winner"
    assert d["experiment"]["winner"] == {"label": "B", "value": "fact_led"}
    assert d["experiment"]["loser"] == {"label": "A", "value": "question"}
    # recorded: the same verdict comes back, nothing is recomputed
    again = client.get(f"/experiments/{e['id']}/analysis").json()
    assert again["recorded"] and again["decision"] == "winner" and again["lift_hdi"] == d["lift_hdi"]
    assert client.post(f"/experiments/{e['id']}/decide", headers=AUTH).status_code == 409


def test_continue_then_inconclusive_at_max_weeks(monkeypatch, mock):
    e, s = running(monkeypatch, n=8, max_weeks=2)
    clicks = {sl["item_id"]: 5 for sl in s}
    published = {sl["item_id"]: T0.isoformat() for sl in s}
    mock.get(f"{SHORT}/links").mock(return_value=httpx.Response(200, json=links_for(clicks, published)))
    set_now(monkeypatch, T0 + timedelta(days=7))
    d = client.post(f"/experiments/{e['id']}/decide", headers=AUTH).json()
    assert d["decision"] == "continue" and d["status"] == "running" and not d["enough_posts"]  # 4 < 12 per arm
    assert d["experiment"]["next_look_at"] == "2026-10-19T08:00:00Z"
    set_now(monkeypatch, T0 + timedelta(days=30))   # missed looks: the last one (max_weeks) is evaluated
    d = client.post(f"/experiments/{e['id']}/decide", headers=AUTH).json()
    assert d["look"] == 2 and d["final"] and d["decision"] == "inconclusive" and d["status"] == "decided"


def test_item_ids_resolved_from_the_engine(monkeypatch, mock):
    e = create(min_posts_per_arm=4)
    approve(e["id"])
    s = [dict(x, item_id=None) for x in slots(8)]
    client.post(f"/experiments/{e['id']}/assign", json={"slots": s}, headers=AUTH)
    for x in s:
        mock.get(f"{ENGINE}/slots/{x['slot_id']}").mock(return_value=httpx.Response(
            200, json={"id": x["slot_id"], "status": "drafted", "calendar_item_id": 500 + x["slot_id"]}))
    clicks = {500 + x["slot_id"]: 3 for x in s}
    mock.get(f"{SHORT}/links").mock(return_value=httpx.Response(
        200, json=links_for(clicks, {i: T0.isoformat() for i in clicks})))
    set_now(monkeypatch, T0 + timedelta(days=7))
    a = client.get(f"/experiments/{e['id']}/analysis").json()
    assert a["arms"]["A"]["posts"] == 4 and a["arms"]["B"]["posts"] == 4
    assert client.get(f"/experiments/{e['id']}/assignments").json()[0]["item_id"] >= 501


def test_shortener_down_records_nothing(monkeypatch, mock):
    e, _ = running(monkeypatch)
    mock.get(f"{SHORT}/links").mock(return_value=httpx.Response(503))
    set_now(monkeypatch, T0 + timedelta(days=7))
    assert client.post(f"/experiments/{e['id']}/decide", headers=AUTH).status_code == 502
    assert client.get(f"/experiments/{e['id']}").json()["looks"] == []


# ---------- the statistics


def test_hdi_and_dispersion_basics():
    s = np.random.default_rng(0).normal(0, 1, 200000)
    lo, hi = hdi(s)
    assert abs(lo + 1.96) < 0.03 and abs(hi - 1.96) < 0.03
    assert dispersion([[5, 5, 5, 5], [5, 5, 5, 5]]) == 1.0
    assert dispersion([[0, 0, 20, 0, 0], [1, 30, 0, 0, 1]]) > 5


def test_deterministic_for_a_seed():
    a, b = [3, 5, 8, 1] * 4, [6, 9, 4, 12] * 4
    assert analyze(a, b, seed=7) == analyze(a, b, seed=7)


def test_no_verdict_below_min_posts_unless_final():
    a, b = [1] * 5, [30] * 5
    assert analyze(a, b, min_posts=12)["decision"] == "continue"
    assert analyze(a, b, min_posts=12, final=True)["decision"] == "inconclusive"


def test_no_practical_difference_with_lots_of_equal_data():
    a = list(np.random.default_rng(1).poisson(200, 40))
    b = list(np.random.default_rng(2).poisson(200, 40))
    assert analyze(a, b)["decision"] == "no_practical_difference"


def draw(rng, mean, n, k=None):
    """Clicks of n posts: Poisson, or negative binomial with shape k (overdispersed, like real posts)."""
    if k is None:
        return [int(x) for x in rng.poisson(mean, n)]
    return [int(x) for x in rng.negative_binomial(k, k / (k + mean), n)]


def run_looks(seed, mean_a, mean_b, per_week=4, weeks=8, k=None, min_posts=12):
    """An experiment as the service runs it: data arrives weekly, the rule is applied only at
    each weekly look, the last look is final."""
    rng = np.random.default_rng(seed)
    a, b = [], []
    for w in range(1, weeks + 1):
        a += draw(rng, mean_a, per_week, k)
        b += draw(rng, mean_b, per_week, k)
        r = analyze(a, b, min_posts=min_posts, final=w == weeks, seed=seed * 100 + w, draws=4000)
        if r["decision"] != "continue":
            return r["decision"], r["winner"]
    raise AssertionError("the final look always decides")


def naive_peek(seed, mean, n=32, k=None):
    """Negative control: look after EVERY post and stop at P(B > A) > 95% (or < 5%)."""
    rng = np.random.default_rng(seed)
    a, b = [], []
    for i in range(n):
        a += draw(rng, mean, 1, k)
        b += draw(rng, mean, 1, k)
        ra = rng.gamma(1 + sum(a), 1 / (1 + len(a)), 4000)
        rb = rng.gamma(1 + sum(b), 1 / (1 + len(b)), 4000)
        p = float(np.mean(rb > ra))
        if i >= 2 and (p > 0.95 or p < 0.05):
            return True
    return False


SEEDS = range(200)


def test_simulation_true_2x_lift_is_detected():
    # (a) 5 vs 10 clicks per post, 4 posts per arm a week, 8 weekly looks
    res = Counter(run_looks(s, 5, 10) for s in SEEDS)
    assert res[("winner", "B")] / len(SEEDS) >= 0.95, res
    assert res[("winner", "A")] == 0, res


def test_simulation_equal_arms_are_not_called_winners():
    # (b) equal arms: no_practical_difference or inconclusive in >= 95% of seeds, Poisson and
    # overdispersed (negative binomial, k=2) clicks alike
    for k in (None, 2):
        res = Counter(run_looks(s, 5, 5, k=k)[0] for s in SEEDS)
        assert res["winner"] / len(SEEDS) <= 0.05, (k, res)
        assert res["no_practical_difference"] + res["inconclusive"] >= 0.95 * len(SEEDS)


def test_simulation_weekly_looks_do_not_inflate_false_winners_like_naive_peeking():
    # (c) the naive rule (peek after every post, stop at 95%) is the negative control
    ours = sum(run_looks(s, 5, 5, k=2)[0] == "winner" for s in SEEDS) / len(SEEDS)
    naive = sum(naive_peek(s, 5, k=2) for s in SEEDS) / len(SEEDS)
    naive_poisson = sum(naive_peek(s, 5) for s in SEEDS) / len(SEEDS)
    assert ours <= 0.05
    assert naive_poisson >= 0.2 and naive >= 0.5, (naive_poisson, naive)
