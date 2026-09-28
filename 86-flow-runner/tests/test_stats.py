"""Holdout assignment and the results interval, with a negative control (A/A) and a positive control."""
import random
import sqlite3
from datetime import timedelta

import pytest

from app import main, stats

from .conftest import AUTH, approved_flow, event

A_A_RUNS = 200


def test_holdout_share_over_10000_emails():
    emails = [f"user{i}@example.com" for i in range(10000)]
    for flow, pct in (("welcome", 15), ("winback", 15), ("onboarding", 20)):
        arms = [stats.arm_for("install-salt-1", flow, e, pct) for e in emails]
        share = arms.count("holdout") / len(arms)
        assert abs(share - pct / 100) < 0.01, (flow, share)   # 1 pt is about 2.8 standard errors
        assert arms == [stats.arm_for("install-salt-1", flow, e, pct) for e in emails]   # stable


def test_assignment_depends_on_salt_and_flow_not_on_case():
    e = [f"u{i}@example.com" for i in range(2000)]
    a = [stats.arm_for("s1", "welcome", x, 15) for x in e]
    assert a != [stats.arm_for("s2", "welcome", x, 15) for x in e]
    assert a != [stats.arm_for("s1", "winback", x, 15) for x in e]
    assert a == [stats.arm_for("s1", "welcome", x.upper(), 15) for x in e]
    assert set(stats.arm_for("s1", "welcome", x, 0) for x in e) == {"flow"}


def test_the_api_assigns_the_same_arm_on_every_call(client, clock):
    approved_flow(client)
    approved_flow(client, "winback")
    got = {}
    for i in range(300):
        email = f"u{i}@example.com"
        got[email] = event(client, "subscribed", email, consent=True)["entered"][0]["arm"]
        assert got[email] == stats.arm_for("test-salt", "welcome", email, 15)
    again = [event(client, "subscribed", e, consent=True) for e in list(got)[:50]]
    assert all(r["skipped"] == [{"flow": "welcome", "reason": "already entered this flow"}] for r in again)
    share = sum(a == "holdout" for a in got.values()) / 300
    assert 0.08 < share < 0.22


def test_salt_is_generated_once_per_install(client, monkeypatch):
    monkeypatch.delenv("FLOW_SALT")
    client.get("/health")
    with main.db() as conn:
        s1 = main.salt(conn)
        s2 = main.salt(conn)
    assert s1 == s2 and len(s1) == 32


def test_difference_interval_and_small_samples():
    d = stats.difference(150, 1000, 100, 1000, min_n=100)
    assert d["difference"] == 0.05 and d["significant"] and d["verdict"] == "flow higher"
    lo, hi = d["ci95"]
    assert lo < 0.05 < hi and round(hi - lo, 3) == 0.058
    small = stats.difference(5, 50, 1, 50, min_n=100)
    assert small["verdict"] == "not enough data" and small["ci95"] is None and not small["significant"]
    rare = stats.difference(2, 1000, 1, 1000, min_n=100)          # fewer than 5 events in an arm
    assert rare["verdict"] == "not enough data"
    assert stats.difference(0, 0, 0, 0, 100)["difference"] is None


# ---------- simulated runs through GET /flows/{name}/results


def simulate(clock, flow, n, p_flow, p_hold, rng, salt="test-salt", pct=15):
    """n contacts entered 5 days ago; each clicks with its arm's true rate."""
    entered = main.fmt(clock.t - timedelta(days=5))
    clicked_at = main.fmt(clock.t - timedelta(days=2))
    conn = sqlite3.connect(main.db_path())
    conn.execute("INSERT INTO flows (name, trigger, default_exits, holdout_pct, mode, created_at)"
                 " VALUES (?, 'subscribed', '[\"unsubscribed\"]', ?, 'aa', ?)", (flow, pct, entered))
    enr, evs = [], []
    for i in range(n):
        email = f"{flow}-{i}@sim.example"
        arm = stats.arm_for(salt, flow, email, pct)
        enr.append((flow, email, arm, entered))
        if rng.random() < (p_flow if arm == "flow" else p_hold):
            evs.append(("clicked", email, clicked_at, clicked_at))
    conn.executemany("INSERT INTO enrollments (flow, email, arm, entered_at, status) VALUES (?, ?, ?, ?, 'active')", enr)
    conn.executemany("INSERT INTO events (type, email, at, received_at) VALUES (?, ?, ?, ?)", evs)
    conn.commit()
    conn.close()


def test_aa_negative_control(client, clock):
    """Both arms have the same true click rate (10 %). The results endpoint must call the
    difference significant in at most about 5 % of runs; the test requires >= 90 % not significant."""
    rng = random.Random(20260928)
    client.get("/health")
    verdicts = []
    for r in range(A_A_RUNS):
        simulate(clock, f"aa-{r}", 2000, 0.10, 0.10, rng)
        res = client.get(f"/flows/aa-{r}/results", headers=AUTH).json()
        assert res["mode"] == "aa" and "A/A" in res["notes"][0]
        verdicts.append(res["compare"]["clicked"])
    not_sig = sum(not v["significant"] for v in verdicts) / A_A_RUNS
    computed = sum(v["ci95"] is not None for v in verdicts)
    print(f"\nA/A: {not_sig:.1%} of {A_A_RUNS} runs not significant ({computed} with an interval)")
    assert computed == A_A_RUNS            # enough data in every run: the check really ran
    assert not_sig >= 0.90


def test_positive_control_detects_a_real_5_point_lift(client, clock):
    """Flow arm 15 % vs holdout 10 %, 6000 contacts (about 900 in the holdout): detected."""
    rng = random.Random(7)
    client.get("/health")
    hits, runs = 0, 20
    for r in range(runs):
        simulate(clock, f"lift-{r}", 6000, 0.15, 0.10, rng)
        c = client.get(f"/flows/lift-{r}/results", headers=AUTH).json()["compare"]["clicked"]
        hits += c["significant"] and c["difference"] > 0
    print(f"\npositive control: +5 pt lift detected in {hits} of {runs} runs")
    assert hits / runs >= 0.9


def test_small_lift_with_small_n_is_not_enough_data(client, clock):
    rng = random.Random(3)
    client.get("/health")
    simulate(clock, "tiny", 300, 0.15, 0.10, rng)     # about 45 in the holdout
    c = client.get("/flows/tiny/results", headers=AUTH).json()["compare"]["clicked"]
    assert c["verdict"] == "not enough data" and not c["significant"]


def test_results_count_sends_outcomes_after_entry_and_no_opens(client, clock, monkeypatch):
    monkeypatch.setenv("FLOW_HOLDOUT_PCT", "50")
    approved_flow(client)
    arms = {}
    event(client, "purchased", "u0@example.com", consent=True, at="2026-08-30T08:00:00Z")   # before entry
    for i in range(10):
        arms[i] = event(client, "subscribed", f"u{i}@example.com", consent=True)["entered"][0]["arm"]
    client.post("/tick", headers=AUTH)
    clock.advance(hours=3)
    event(client, "clicked", "u1@example.com")
    event(client, "purchased", "u2@example.com")
    event(client, "unsubscribed", "u3@example.com")
    res = client.get("/flows/welcome/results?days=30", headers=AUTH).json()
    a = res["arms"]
    assert a["flow"]["entered"] + a["holdout"]["entered"] == 10
    assert a["flow"]["emails_sent"] == a["flow"]["entered"] and a["holdout"]["emails_sent"] == 0
    assert a[arms[1]]["clicked"] == 1 and a[arms[2]]["purchased"] == 1 and a[arms[3]]["unsubscribed"] == 1
    assert a[arms[0]]["purchased"] == (1 if arms[2] == arms[0] else 0)   # u0's purchase was before entry
    assert "open" not in str(res["arms"]).lower()
    assert res["compare"]["clicked"]["verdict"] == "not enough data"
    assert client.get("/flows/nope/results", headers=AUTH).status_code == 404
