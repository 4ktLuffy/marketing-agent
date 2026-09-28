"""Performance-ranked examples: the ranking (app/performance.py), the endpoint's fallback,
and a simulation against a naive "most clicks" pick (the negative control).

Run `python -m tests.test_performance` from the service folder to print the simulation
numbers.
"""
import random
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import performance as perf
from app.main import app

KEY = "test-key"
AUTH = {"X-API-Key": KEY}
CAMPAIGNS = "http://campaigns.test"
NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)

client = TestClient(app)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "learning.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("GATEWAY_URL", "http://gateway.test")
    monkeypatch.setenv("CAMPAIGNS_URL", CAMPAIGNS)


class FixedNow(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def post(item_id, clicks, days=10, channel="linkedin", hook=None):
    return {"item_id": item_id, "channel": channel, "clicks": clicks, "hook_style": hook, "posted_at": ago(days)}


def background(n=8, clicks=6, start=100, channel="linkedin"):
    """n ordinary posts of a channel: the baseline a winner has to beat."""
    return [post(start + i, clicks, days=10 + i, channel=channel) for i in range(n)]


# ---------- ranking


def test_poisson_tail():
    assert perf.poisson_sf(0, 5) == 1.0
    assert perf.poisson_sf(1, 5) == pytest.approx(1 - 2.718281828 ** -5, rel=1e-6)
    assert perf.poisson_sf(20, 6) < 1e-4
    assert perf.poisson_sf(3000, 2500) < 1e-15  # large rates do not underflow to 1.0


def test_clear_winners_ranked_by_clicks_and_ordinary_posts_not_picked():
    posts = background() + [post(1, 40, hook="question"), post(2, 25, hook="story")]
    texts = {1: "Big win text", 2: "Second win story", **{p["item_id"]: f"plain {p['item_id']}" for p in posts[:8]}}
    picked, stats = perf.rank(posts, texts, 3, NOW)
    assert [p["item_id"] for p in picked] == [1, 2]  # the 8 ordinary posts never pass
    assert stats["passed_evidence"] == 2


def test_minimum_evidence_rules():
    texts = {1: "winner"}
    # too few posts in the channel to know what "typical" is
    assert perf.rank(background(n=3) + [post(1, 40)], texts, 3, NOW)[0] == []
    # clear lead but under min_clicks
    assert perf.rank(background(n=8, clicks=0) + [post(1, 4)], texts, 3, NOW)[0] == []
    # above min_clicks but within the noise of the channel (median 6, 10 clicks)
    assert perf.rank(background() + [post(1, 10)], texts, 3, NOW)[0] == []
    # not approved in 46 -> never an example, but it still counts for the baseline
    assert perf.rank(background() + [post(1, 40)], {}, 3, NOW)[0] == []


def test_recency_window_and_decay():
    texts = {1: "old winner", 2: "fresh winner", 3: "too fresh", 4: "too old"}
    posts = background() + [post(1, 40, days=80), post(2, 30, days=5), post(3, 90, days=1),
                            post(4, 90, days=120)]
    picked, _ = perf.rank(posts, texts, 3, NOW)
    # 40 * 0.5**(80/45) ~ 11.7 < 30 * 0.5**(5/45) ~ 27.7; unsettled (1 day) and >90 days excluded
    assert [p["item_id"] for p in picked] == [2, 1]


def test_diversity_one_per_hook_style_and_no_near_duplicates():
    texts = {1: "Remote teams need a fixed coffee time every Monday",
             2: "Another question post about something else entirely",
             3: "Remote teams need a fixed coffee time every Monday morning",
             4: "A story about beans in the mail"}
    posts = background() + [post(1, 50, hook="question"), post(2, 45, hook="question"),
                            post(3, 40, hook="story"), post(4, 30, hook="story")]
    picked, _ = perf.rank(posts, texts, 3, NOW)
    # 2: same hook style as 1; 3: near-duplicate text of 1 (different style); 4: story, distinct
    assert [p["item_id"] for p in picked] == [1, 4]


def test_channels_have_separate_baselines():
    posts = (background(n=6, clicks=40, start=100, channel="linkedin")
             + background(n=6, clicks=2, start=200, channel="x")
             + [post(1, 45, channel="linkedin"), post(2, 15, channel="x")])
    picked, _ = perf.rank(posts, {1: "li", 2: "x post"}, 3, NOW)
    # 45 on a channel whose posts get 40 is ordinary; 15 where posts get 2 is not
    assert [p["item_id"] for p in picked] == [2]


# ---------- endpoint


def ev(item_id, text, channel="linkedin", decision="approved"):
    r = client.post("/events", json={"item_id": item_id, "channel": channel, "decision": decision,
                                     "draft": text}, headers=AUTH)
    assert r.status_code == 201


def insights(posts):
    return httpx.Response(200, json={"posts": posts, "errors": []})


@respx.mock
def test_endpoint_performance_first_then_filled_by_approval(monkeypatch):
    monkeypatch.setattr("app.main.datetime", FixedNow)
    ev(1, "The post that performed")
    ev(50, "Recently approved, no clicks yet")
    ev(51, "Another approved one")
    route = respx.get(f"{CAMPAIGNS}/insights/posts").mock(
        return_value=insights(background() + [post(1, 40, hook="question")]))
    r = client.get("/examples?by=performance&k=3&channel=linkedin")
    assert r.status_code == 200
    body = r.json()
    assert [e["basis"] for e in body] == ["performance", "approval", "approval"]
    assert body[0] | {} == {"text": "The post that performed", "channel": "linkedin", "decision": "approved",
                            "item_id": 1, "basis": "performance", "clicks": 40, "hook_style": "question"}
    assert [e["item_id"] for e in body[1:]] == [51, 50]  # the winner is not repeated
    assert r.headers["X-Examples-Basis"] == "mixed"
    assert route.calls[0].request.url.params["channel"] == "linkedin"
    assert route.calls[0].request.headers["X-API-Key"] == KEY


@respx.mock
def test_endpoint_falls_back_when_no_evidence_or_45_down():
    ev(1, "approved one")
    respx.get(f"{CAMPAIGNS}/insights/posts").mock(return_value=insights([]))
    r = client.get("/examples?by=performance")
    assert [e["basis"] for e in r.json()] == ["approval"]
    assert r.headers["X-Examples-Basis"] == "approval"
    assert "0 of 0 settled posts" in r.headers["X-Examples-Note"]

    respx.get(f"{CAMPAIGNS}/insights/posts").mock(side_effect=httpx.ConnectError("refused"))
    r = client.get("/examples?by=performance")
    assert r.status_code == 200 and [e["text"] for e in r.json()] == ["approved one"]
    assert r.headers["X-Examples-Note"].startswith("fell back to approval examples: campaign service unreachable")

    respx.get(f"{CAMPAIGNS}/insights/posts").mock(return_value=httpx.Response(500))
    assert client.get("/examples?by=performance").headers["X-Examples-Basis"] == "approval"


def test_endpoint_rejects_unknown_basis():
    assert client.get("/examples?by=clicks").status_code == 422


def test_default_is_still_approval_and_never_calls_45():
    ev(1, "approved")
    with respx.mock(assert_all_mocked=True):  # any call to 45 would fail here
        r = client.get("/examples")
    assert r.json() == [{"text": "approved", "channel": "linkedin", "decision": "approved"}]
    assert "X-Examples-Basis" not in r.headers


# ---------- simulation: noisy clicks, evidence rule vs naive max clicks

N_POSTS = 20
BASE_RATE = 8.0


def world(rng: random.Random, n_good: int, lift: float):
    """True clicks-per-post rates; the first n_good posts are truly better."""
    return [BASE_RATE * (lift if i < n_good else 1.0) for i in range(N_POSTS)]


def draw(rng, rates, days, dispersion=None):
    """One observed click count per post: Poisson, or overdispersed (Gamma-Poisson with
    shape `dispersion`: same mean, more spread, as real click data tends to be)."""
    out = []
    for i, lam in enumerate(rates):
        if dispersion:
            lam = rng.gammavariate(dispersion, lam / dispersion)
        # Poisson by inversion (Knuth is fine for these small rates)
        L, k, p = pow(2.718281828459045, -lam), 0, 1.0
        while True:
            p *= rng.random()
            if p <= L:
                break
            k += 1
        out.append(post(i, k, days=days[i], hook=["question", "story", "fact_led", "how_to", "benefit", "contrarian"][i % 6]))
    return out


def simulate(n_good: int, lift: float, runs: int = 400, k: int = 2, dispersion=None, seed: int = 7):
    rng = random.Random(seed)
    texts = {i: f"post number {i} " + " ".join(f"w{i}x{j}" for j in range(8)) for i in range(N_POSTS)}
    good = set(range(n_good))
    res = {"ours_any": 0, "ours_picked": 0, "ours_good": 0, "ours_stable": 0.0,
           "naive_good": 0, "naive_picked": 0, "naive_stable": 0.0}
    for _ in range(runs):
        rates = world(rng, n_good, lift)
        days = [rng.uniform(3, 60) for _ in range(N_POSTS)]
        a, b = draw(rng, rates, days, dispersion), draw(rng, rates, days, dispersion)  # two noisy looks
        pa, _ = perf.rank(a, texts, k, NOW)
        pb, _ = perf.rank(b, texts, k, NOW)
        na, nb = perf.naive_top(a, texts, k), perf.naive_top(b, texts, k)
        res["ours_any"] += bool(pa)
        res["ours_picked"] += len(pa)
        res["ours_good"] += sum(p["item_id"] in good for p in pa)
        res["naive_picked"] += len(na)
        res["naive_good"] += sum(p["item_id"] in good for p in na)
        sa, sb = {p["item_id"] for p in pa}, {p["item_id"] for p in pb}
        # stability: the two looks agree (both abstain counts as agreement: same examples)
        res["ours_stable"] += 1.0 if not sa and not sb else len(sa & sb) / len(sa | sb)
        ta, tb = {p["item_id"] for p in na}, {p["item_id"] for p in nb}
        res["naive_stable"] += len(ta & tb) / len(ta | tb)
    return {
        "claims_a_winner": res["ours_any"] / runs,
        "precision": res["ours_good"] / res["ours_picked"] if res["ours_picked"] else None,
        "stability": res["ours_stable"] / runs,
        "naive_claims_a_winner": 1.0,
        "naive_precision": res["naive_good"] / res["naive_picked"],
        "naive_stability": res["naive_stable"] / runs,
    }


def test_simulation_null_world_rarely_claims_a_winner_naive_always_does():
    """All 20 posts equally good: any "winner" is luck. Negative control: the naive pick
    always names two, and they change from one look to the next."""
    s = simulate(n_good=0, lift=1.0)
    assert s["claims_a_winner"] <= 0.15
    assert s["naive_stability"] <= 0.25
    assert s["stability"] >= 0.85


def test_simulation_real_winners_found():
    """3 of 20 posts truly get 3x the clicks: ours names them, nearly never a wrong one.
    (Two looks agree about as often as the naive pick's, ~0.55: no stability gain here.)"""
    s = simulate(n_good=3, lift=3.0)
    assert s["precision"] >= 0.95
    assert s["claims_a_winner"] >= 0.9


def test_simulation_overdispersed_clicks_weak_but_better_than_naive():
    """Clicks more spread out than Poisson (Gamma-Poisson, shape 2). Known weakness: in an
    all-equal world ours still names a lucky post in ~40 % of runs (naive: 100 %); with
    real winners its picks are right more often than the naive pick's."""
    null = simulate(n_good=0, lift=1.0, dispersion=2.0)
    real = simulate(n_good=3, lift=3.0, dispersion=2.0)
    assert null["claims_a_winner"] <= 0.5
    assert real["precision"] >= real["naive_precision"] + 0.1


if __name__ == "__main__":
    for name, kw in [("null (all equal)", dict(n_good=0, lift=1.0)),
                     ("3 of 20 at 2x", dict(n_good=3, lift=2.0)),
                     ("3 of 20 at 3x", dict(n_good=3, lift=3.0)),
                     ("null, overdispersed", dict(n_good=0, lift=1.0, dispersion=2.0)),
                     ("3 of 20 at 3x, overdispersed", dict(n_good=3, lift=3.0, dispersion=2.0))]:
        s = simulate(**kw)
        print(f"{name:32s} " + "  ".join(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}" for k, v in s.items()))
