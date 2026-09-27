import math
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main
from app.main import DEFAULT_CADENCE, HOOK_STYLES, app, cosine, jaccard, ngrams, plan_slots, words

KEY = "test-key"
AUTH = {"X-API-Key": KEY}
OLLAMA = "http://ollama.test:11434"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "engine.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("OLLAMA_URL", OLLAMA)
    for k in ("MIN_ATOMS", "MIN_DECISIONS", "NGRAM_MAX", "EMBED_MAX", "NOVELTY_DAYS"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def ollama():
    """Ollama is down unless a test gives the route a side effect."""
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{OLLAMA}/api/embed")
        route.side_effect = httpx.ConnectError("connection refused")
        yield route


client = TestClient(app)

PILLAR = {
    "title": "Why remote teams need a coffee ritual", "brief": "Pillar essay on shared breaks",
    "audience": "people-ops leads at remote companies", "channels": ["linkedin", "x", "instagram"],
    "month": "2026-10", "promo_max": 0.2,
}
KINDS = ("claim", "story", "faq", "tip", "stat", "objection", "quote")


def make_pillar(**kw):
    r = client.post("/pillars", json=PILLAR | kw, headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def atoms(n, verified=True, promo_every=0, start=0):
    return [{"kind": KINDS[i % len(KINDS)], "text": f"Atom number {i} says something specific about rituals",
             "verified": verified, "evidence": f"f{i}" if i % 2 else None,
             "promo": bool(promo_every) and i % promo_every == 0} for i in range(start, start + n)]


def add_atoms(pid, items):
    r = client.post(f"/pillars/{pid}/atoms", json={"atoms": items}, headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def plan(pid, **body):
    return client.post(f"/pillars/{pid}/plan", json=body, headers=AUTH)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


# ---------- auth


@pytest.mark.parametrize("path,body", [
    ("/pillars", PILLAR),
    ("/pillars/1/atoms", {"atoms": atoms(1)}),
    ("/pillars/1/plan", {}),
    ("/slots/1/status", {"status": "dropped"}),
    ("/novelty/register", {"item_id": 1, "channel": "x", "text": "hello"}),
    ("/pillars/1/outcomes", {"item_id": 1, "decision": "approved"}),
    ("/pillars/1/resume", {}),
])
def test_writes_need_the_key(path, body):
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, json=body, headers={"X-API-Key": "wrong"}).status_code == 401


def test_writes_refused_when_service_has_no_key(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.post("/pillars", json=PILLAR, headers=AUTH).status_code == 503


def test_reads_and_check_need_no_key():
    p = make_pillar()
    assert client.get("/pillars").status_code == 200
    assert client.get(f"/pillars/{p['id']}/slots").status_code == 200
    assert client.post("/novelty/check", json={"text": "hi there", "channel": "x"}).status_code == 200


# ---------- pillars


def test_create_and_get_pillar():
    p = make_pillar(source_text="long essay", source_url="https://example.com/essay")
    assert p["status"] == "active" and p["channels"] == ["linkedin", "x", "instagram"]
    assert p["atoms"] == 0 and p["slots"] == {"planned": 0, "drafted": 0, "dropped": 0}
    got = client.get(f"/pillars/{p['id']}").json()
    assert got["source_text"] == "long essay" and got["promo_max"] == 0.2
    listed = client.get("/pillars").json()
    assert [x["id"] for x in listed] == [p["id"]] and "source_text" not in listed[0]
    assert client.get("/pillars?month=2026-11").json() == []


@pytest.mark.parametrize("bad", [
    {"channels": []}, {"channels": ["myspace"]}, {"channels": ["x", "x"]}, {"month": "2026-13"},
    {"month": "October"}, {"promo_max": 1.5}, {"title": "  "}, {"source_url": "ftp://x"},
])
def test_pillar_validation(bad):
    assert client.post("/pillars", json=PILLAR | bad, headers=AUTH).status_code == 422


@pytest.mark.parametrize("path", ["/pillars/999", "/pillars/0", "/pillars/99999999999999999999999"])
def test_missing_pillar_is_404(path):
    assert client.get(path).status_code == 404


# ---------- atoms


def test_atoms_store_and_filter_and_skip_duplicates():
    p = make_pillar()
    out = add_atoms(p["id"], atoms(3) + atoms(2, verified=False, start=10))
    assert out["added"] == 5 and out["verified_total"] == 3 and out["min_atoms"] == 12
    again = add_atoms(p["id"], [atoms(1)[0] | {"text": "  ATOM number 0 says something specific   about rituals"}])
    assert again["added"] == 0 and len(again["skipped"]) == 1
    all_ = client.get(f"/pillars/{p['id']}/atoms").json()
    assert len(all_) == 5 and {a["kind"] for a in all_} >= {"claim", "story"}
    assert len(client.get(f"/pillars/{p['id']}/atoms?verified=true").json()) == 3
    assert len(client.get(f"/pillars/{p['id']}/atoms?verified=false").json()) == 2


@pytest.mark.parametrize("bad", [
    {"kind": "insight"}, {"verified": "yes"}, {"text": ""}, {"promo": "no"},
])
def test_atom_validation(bad):
    p = make_pillar()
    r = client.post(f"/pillars/{p['id']}/atoms", json={"atoms": [atoms(1)[0] | bad]}, headers=AUTH)
    assert r.status_code == 422


def test_atoms_for_missing_pillar_404():
    assert client.post("/pillars/42/atoms", json={"atoms": atoms(1)}, headers=AUTH).status_code == 404
    assert client.get("/pillars/42/atoms").status_code == 404


# ---------- planning via the API


def test_plan_refuses_a_thin_pillar():
    p = make_pillar()
    add_atoms(p["id"], atoms(11) + atoms(5, verified=False, start=50))
    r = plan(p["id"])
    assert r.status_code == 422
    assert "11 verified atoms" in r.json()["detail"] and "at least 12" in r.json()["detail"]


def test_min_atoms_env(monkeypatch):
    monkeypatch.setenv("MIN_ATOMS", "3")
    p = make_pillar()
    add_atoms(p["id"], atoms(3))
    assert plan(p["id"], weeks=1).status_code == 200


def test_plan_counts_match_cadence_and_persist():
    p = make_pillar()
    add_atoms(p["id"], atoms(30, promo_every=5))
    r = plan(p["id"], start_date="2026-10-05", weeks=4)
    assert r.status_code == 200, r.text
    out = r.json()
    want = {ch: DEFAULT_CADENCE[ch] * 4 for ch in PILLAR["channels"]}
    assert out["requested"] == want and out["planned"] == want
    assert out["unfilled"] == {ch: 0 for ch in PILLAR["channels"]}
    assert len(out["slots"]) == sum(want.values()) == 52
    s = out["slots"][0]
    assert set(s) >= {"id", "date", "time_utc", "channel", "format", "atom_id", "hook_style", "pillar_id",
                      "status", "calendar_item_id", "atom"}
    assert s["status"] == "planned" and s["atom"]["text"].startswith("Atom number")
    assert all("2026-10-05" <= x["date"] <= "2026-11-01" for x in out["slots"])
    stored = client.get(f"/pillars/{p['id']}/slots").json()
    assert [x["id"] for x in stored] == [x["id"] for x in out["slots"]]
    assert len(client.get(f"/pillars/{p['id']}/slots?channel=x").json()) == 28
    assert client.get(f"/pillars/{p['id']}").json()["slots"]["planned"] == 52


def test_plan_start_defaults_to_the_pillar_month_and_cadence_overrides():
    p = make_pillar()
    add_atoms(p["id"], atoms(20))
    out = plan(p["id"], weeks=2, cadence={"x": 2, "instagram": 0}).json()
    assert out["start_date"] == "2026-10-01" and out["end_date"] == "2026-10-14"
    assert out["planned"] == {"linkedin": 6, "x": 4, "instagram": 0}


@pytest.mark.parametrize("body,status", [
    ({"cadence": {"blog": 1}}, 422),       # not a channel of this pillar
    ({"cadence": {"x": 8}}, 422),          # more than one a day
    ({"weeks": 0}, 422), ({"weeks": 9}, 422),
    ({"start_date": "next monday"}, 422),
])
def test_plan_validation(body, status):
    p = make_pillar()
    add_atoms(p["id"], atoms(20))
    assert plan(p["id"], **body).status_code == status


def test_plan_missing_pillar_404():
    assert plan(77).status_code == 404


def test_plan_reports_unfilled_when_atoms_run_out(monkeypatch):
    # 52 slots wanted. Spaced same-channel reuse (2 uses, 14 days apart) fills 48 from 12 atoms;
    # without reuse (the old rule) only 36 (3 channels each).
    p = make_pillar()
    add_atoms(p["id"], atoms(12))
    out = plan(p["id"], start_date="2026-10-05").json()
    assert sum(out["planned"].values()) == 48
    assert sum(out["unfilled"].values()) == 4
    monkeypatch.setattr(main, "REUSE_PER_CHANNEL", 1)
    out = plan(p["id"], start_date="2026-10-05").json()
    assert sum(out["planned"].values()) == 36
    assert sum(out["unfilled"].values()) == 16


def test_replan_is_deterministic_and_keeps_drafted_slots():
    p = make_pillar()
    add_atoms(p["id"], atoms(30))
    first = plan(p["id"], start_date="2026-10-05").json()["slots"]
    again = plan(p["id"], start_date="2026-10-05").json()["slots"]
    key = lambda s: (s["date"], s["time_utc"], s["channel"], s["format"], s["atom_id"], s["hook_style"])
    assert [key(s) for s in first] == [key(s) for s in again]

    drafted = again[:5]
    for s in drafted:
        r = client.post(f"/slots/{s['id']}/status", json={"status": "drafted", "calendar_item_id": 100 + s["id"]},
                        headers=AUTH)
        assert r.status_code == 200 and r.json()["calendar_item_id"] == 100 + s["id"]
    dropped = again[5]
    client.post(f"/slots/{dropped['id']}/status", json={"status": "dropped", "reason": "near duplicate"},
                headers=AUTH)

    out = plan(p["id"], start_date="2026-10-05", seed=7).json()
    all_slots = client.get(f"/pillars/{p['id']}/slots").json()
    by_status = Counter(s["status"] for s in all_slots)
    assert by_status["drafted"] == 5 and by_status["dropped"] == 1
    for s in drafted:  # untouched
        got = client.get(f"/slots/{s['id']}").json()
        assert got["status"] == "drafted" and got["atom_id"] == s["atom_id"]
    # the drafted slots still count toward the channel totals and the constraints
    live = [s for s in all_slots if s["status"] in ("planned", "drafted")]
    per_channel = Counter(s["channel"] for s in live)
    assert per_channel == Counter({ch: DEFAULT_CADENCE[ch] * 4 for ch in PILLAR["channels"]})
    assert sum(out["kept_drafted"].values()) == 5
    assert_constraints(live, promo_ids=set(), promo_max=0.2)


# ---------- planner properties (pure function)


def assert_constraints(slots, promo_ids, promo_max):
    channels_of, dates_on = {}, {}
    for s in slots:
        channels_of.setdefault(s["atom_id"], set()).add(s["channel"])
        dates_on.setdefault((s["atom_id"], s["channel"]), []).append(date.fromisoformat(s["date"]))
    for aid, chs in channels_of.items():
        assert len(chs) <= main.MAX_CHANNELS_PER_ATOM, f"atom {aid} on {len(chs)} channels"
    for (aid, ch), ds in dates_on.items():   # same-channel reuse: capped and spaced out
        assert len(ds) <= main.REUSE_PER_CHANNEL, f"atom {aid} used {len(ds)} times on {ch}"
        ds.sort()
        assert all((b - a).days >= main.REUSE_GAP_DAYS for a, b in zip(ds, ds[1:])), f"atom {aid} reused on {ch} too soon"
    pairs = [(s["atom_id"], s["hook_style"]) for s in slots]
    assert len(pairs) == len(set(pairs)), "an (atom, hook) pair repeats"
    days = [(s["channel"], s["date"]) for s in slots]
    assert len(days) == len(set(days)), "two posts on one channel on one day"
    for ch in {s["channel"] for s in slots}:
        on = [s for s in slots if s["channel"] == ch]
        promo = sum(1 for s in on if s["atom_id"] in promo_ids)
        assert promo <= promo_max * len(on) + 1e-9, f"{ch}: {promo}/{len(on)} promo"


def fake_atoms(n, promo_every=0):
    return [{"id": i + 1, "promo": bool(promo_every) and i % promo_every == 0} for i in range(n)]


ALL = list(DEFAULT_CADENCE)


@pytest.mark.parametrize("seed", [0, 1, 42, 2026])
@pytest.mark.parametrize("channels,n_atoms,promo_every,promo_max", [
    (ALL, 60, 4, 0.2),
    (["linkedin", "x", "instagram", "blog", "email", "video"], 30, 3, 0.2),
    (["x"], 40, 2, 0.1),
    (["linkedin", "facebook", "threads", "mastodon"], 20, 0, 0.0),
])
def test_planner_properties(seed, channels, n_atoms, promo_every, promo_max):
    atoms_ = fake_atoms(n_atoms, promo_every)
    cadence = {ch: DEFAULT_CADENCE[ch] for ch in channels}
    start = date(2026, 10, 5)
    slots, unfilled = plan_slots(channels=channels, cadence=cadence, start=start, weeks=4, atoms=atoms_,
                                 kept=[], promo_max=promo_max, seed=seed)
    per_channel = Counter(s["channel"] for s in slots)
    for ch in channels:
        assert per_channel[ch] + unfilled.get(ch, 0) == cadence[ch] * 4
    if 3 * n_atoms >= sum(cadence.values()) * 4 and promo_every == 0:
        assert not unfilled
    assert_constraints(slots, {a["id"] for a in atoms_ if a["promo"]}, promo_max)
    # every slot is inside the window, formats belong to the channel, hooks are valid
    assert all(start <= date.fromisoformat(s["date"]) < start + timedelta(days=28) for s in slots)
    assert all(s["hook_style"] in HOOK_STYLES for s in slots)
    assert all(s["format"] in ("post", "thread", "carousel_text", "blog", "email", "video_script") for s in slots)


def test_planner_fills_every_slot_with_enough_non_promo_atoms():
    channels = ["linkedin", "x", "instagram", "blog", "email", "video"]
    cadence = {ch: DEFAULT_CADENCE[ch] for ch in channels}
    slots, unfilled = plan_slots(channels=channels, cadence=cadence, start=date(2026, 10, 5), weeks=4,
                                 atoms=fake_atoms(30, promo_every=6), kept=[], promo_max=0.2, seed=3)
    assert not unfilled and len(slots) == 68  # 17 a week at the default cadence


def test_planner_is_deterministic_per_seed():
    args = dict(channels=ALL, cadence=DEFAULT_CADENCE, start=date(2026, 10, 5), weeks=4,
                atoms=fake_atoms(50, 5), kept=[], promo_max=0.2)
    a, _ = plan_slots(seed=11, **args)
    b, _ = plan_slots(seed=11, **args)
    c, _ = plan_slots(seed=12, **args)
    assert a == b and a != c


def test_planner_prefers_weekdays_and_spreads_hooks():
    slots, _ = plan_slots(channels=["linkedin", "instagram", "blog"], cadence={"linkedin": 3, "instagram": 3, "blog": 1},
                          start=date(2026, 10, 5), weeks=4, atoms=fake_atoms(40), kept=[], promo_max=0.2, seed=1)
    assert all(date.fromisoformat(s["date"]).weekday() < 5 for s in slots)
    for ch in ("linkedin", "instagram"):
        assert len({s["hook_style"] for s in slots if s["channel"] == ch}) >= 4
    x7, _ = plan_slots(channels=["x"], cadence={"x": 7}, start=date(2026, 10, 5), weeks=2,
                       atoms=fake_atoms(20), kept=[], promo_max=0.2, seed=1)
    assert Counter(s["format"] for s in x7) == {"post": 12, "thread": 2}


def test_planner_respects_kept_slots():
    kept = [{"channel": "linkedin", "date": "2026-10-05", "atom_id": 1, "hook_style": "question", "promo": False},
            {"channel": "x", "date": "2026-10-06", "atom_id": 1, "hook_style": "story", "promo": False},
            {"channel": "instagram", "date": "2026-10-07", "atom_id": 1, "hook_style": "benefit", "promo": False}]
    slots, _ = plan_slots(channels=["linkedin", "x", "instagram"], cadence={"linkedin": 3, "x": 7, "instagram": 3},
                          start=date(2026, 10, 5), weeks=1, atoms=fake_atoms(10), kept=kept, promo_max=0.2, seed=0)
    assert all(s["atom_id"] != 1 for s in slots)  # atom 1 is used up (3 channels)
    assert ("linkedin", "2026-10-05") not in {(s["channel"], s["date"]) for s in slots}
    assert Counter(s["channel"] for s in slots) == {"linkedin": 2, "x": 6, "instagram": 2}


# ---------- slot status


def planned_slot():
    p = make_pillar()
    add_atoms(p["id"], atoms(20))
    return p, plan(p["id"], weeks=1).json()["slots"][0]


def test_slot_status_transitions():
    _, s = planned_slot()
    r = client.post(f"/slots/{s['id']}/status", json={"status": "drafted", "calendar_item_id": 5}, headers=AUTH)
    assert r.json()["status"] == "drafted" and r.json()["calendar_item_id"] == 5
    r = client.post(f"/slots/{s['id']}/status", json={"status": "dropped", "reason": "D2 twice"}, headers=AUTH)
    assert r.json()["status"] == "dropped" and r.json()["reason"] == "D2 twice" and r.json()["calendar_item_id"] == 5
    r = client.post(f"/slots/{s['id']}/status", json={"status": "drafted"}, headers=AUTH)
    assert r.status_code == 409


def test_planned_slot_records_calendar_item_but_never_goes_back():
    _, s = planned_slot()
    r = client.post(f"/slots/{s['id']}/status", json={"status": "planned", "calendar_item_id": 7}, headers=AUTH)
    assert r.status_code == 200 and r.json()["status"] == "planned" and r.json()["calendar_item_id"] == 7
    r = client.post(f"/slots/{s['id']}/status", json={"status": "drafted"}, headers=AUTH)
    assert r.json()["status"] == "drafted" and r.json()["calendar_item_id"] == 7
    assert client.post(f"/slots/{s['id']}/status", json={"status": "planned"}, headers=AUTH).status_code == 409
    client.post(f"/slots/{s['id']}/status", json={"status": "dropped"}, headers=AUTH)
    assert client.post(f"/slots/{s['id']}/status", json={"status": "planned"}, headers=AUTH).status_code == 409


def test_slot_status_validation_and_404():
    _, s = planned_slot()
    assert client.post(f"/slots/{s['id']}/status", json={"status": "idea"}, headers=AUTH).status_code == 422
    assert client.post(f"/slots/{s['id']}/status", json={"status": "drafted", "calendar_item_id": "5"},
                       headers=AUTH).status_code == 422
    assert client.post("/slots/999/status", json={"status": "dropped"}, headers=AUTH).status_code == 404
    assert client.get("/slots/999").status_code == 404


def test_plan_refused_while_paused(monkeypatch):
    monkeypatch.setenv("MIN_DECISIONS", "1")
    p, _ = planned_slot()
    client.post(f"/pillars/{p['id']}/outcomes", json={"item_id": 1, "decision": "rejected"}, headers=AUTH)
    r = plan(p["id"])
    assert r.status_code == 409 and "paused" in r.json()["detail"]


# ---------- novelty: text helpers


def test_words_and_ngrams():
    assert words("Don't STOP, believin'!") == ["dont", "stop", "believin"]
    assert len(ngrams(words("one two three four five six"))) == 2
    assert ngrams(words("too short")) == {("too", "short")}
    assert jaccard({1, 2}, {2, 3}) == pytest.approx(1 / 3)
    assert cosine([1, 0, 0], [0.6, 0.8, 0]) == pytest.approx(0.6)
    assert cosine([1, 1], [0, 0]) == 0.0


BASE = ("Our Tuesday coffee break keeps a remote team talking about more than tickets, "
        "and it only takes fifteen minutes on a video call with a mug in hand.")
PARAPHRASE = ("Our Tuesday coffee break keeps a remote team close: a short call, a warm mug, "
              "and talk that has nothing to do with work.")
UNRELATED = "Shipping to Canada now takes three days, and tracking links arrive by email the same afternoon."


def register(item_id, text, channel="linkedin", created_at=None):
    body = {"item_id": item_id, "channel": channel, "text": text}
    if created_at:
        body["created_at"] = created_at
    r = client.post("/novelty/register", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def check(text, channel="linkedin", **kw):
    r = client.post("/novelty/check", json={"text": text, "channel": channel} | kw)
    assert r.status_code == 200, r.text
    return r.json()


def test_identical_text_is_not_novel():
    register(1, BASE)
    out = check(BASE)
    assert out["novel"] is False and out["closest"]["item_id"] == 1
    assert out["closest"]["score_ngram"] == 1.0
    assert any("5-gram" in r for r in out["reasons"]) and any("first 8 words" in r for r in out["reasons"])


def test_paraphrase_with_the_same_opening_is_flagged():
    register(1, BASE)
    out = check(PARAPHRASE)
    assert out["novel"] is False
    assert out["closest"]["score_ngram"] < 0.30
    assert out["reasons"] == ["same first 8 words as item 1"]


def test_unrelated_text_is_novel():
    register(1, BASE)
    out = check(UNRELATED)
    assert out["novel"] is True and out["reasons"] == [] and out["compared"] == 1


def test_ollama_down_reports_embedding_not_checked():
    assert register(1, BASE)["embedded"] is False
    out = check(UNRELATED)
    assert out["embedding_checked"] is False and out["closest"]["score_embed"] is None


def test_channel_scope_exclude_and_window():
    register(1, BASE, channel="x")
    register(2, BASE, channel="linkedin")
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    register(3, UNRELATED, channel="linkedin", created_at=old)
    assert check(UNRELATED)["novel"] is True             # item 3 is older than 90 days
    out = check(BASE, exclude_item_id=2)
    assert out["novel"] is True and out["compared"] == 0  # only x has it now
    assert check(BASE, exclude_item_id=2, any_channel=True)["closest"]["item_id"] == 1
    assert check(BASE, channel="X")["closest"]["item_id"] == 1  # channel is case-insensitive


def test_register_upserts_on_item_id():
    register(1, BASE)
    register(1, UNRELATED)
    assert check(BASE)["novel"] is True


def test_novelty_validation():
    assert client.post("/novelty/check", json={"text": "", "channel": "x"}).status_code == 422
    assert client.post("/novelty/register", json={"item_id": "a", "channel": "x", "text": "t"},
                       headers=AUTH).status_code == 422
    assert client.post("/novelty/register", json={"item_id": 1, "channel": "x", "text": "t", "created_at": "soon"},
                       headers=AUTH).status_code == 422


def vectors(mapping):
    def handler(request):
        import json
        body = json.loads(request.content)
        assert body["model"] == "qwen3-embedding:0.6b"
        return httpx.Response(200, json={"embeddings": [mapping[t] for t in body["input"]]})
    return handler


def test_embedding_cosine_with_mocked_ollama(ollama):
    ollama.side_effect = vectors({BASE: [1.0, 0.0, 0.0], UNRELATED: [0.6, 0.8, 0.0], PARAPHRASE: [0.9, 0.1, 0.1]})
    assert register(1, BASE)["embedded"] is True
    out = check(UNRELATED)
    assert out["embedding_checked"] is True and out["novel"] is True
    assert out["closest"]["score_embed"] == pytest.approx(0.6)
    ollama.side_effect = vectors({BASE: [1.0, 0.0, 0.0], "Totally new words here for a start": [0.9, 0.1, 0.1]})
    out = check("Totally new words here for a start")
    expected = 0.9 / math.sqrt(0.9**2 + 0.1**2 + 0.1**2)
    assert out["closest"]["score_embed"] == pytest.approx(expected, abs=1e-4)
    assert out["novel"] is False and out["reasons"] == [f"embedding cosine {expected:.2f} with item 1 (max 0.85)"]


def test_embeddings_are_cached(ollama):
    ollama.side_effect = vectors({BASE: [1.0, 0.0], UNRELATED: [0.0, 1.0]})
    register(1, BASE)
    check(UNRELATED)
    calls = ollama.call_count
    check(UNRELATED)
    assert ollama.call_count == calls  # both vectors came from SQLite


def test_embed_threshold_env(ollama, monkeypatch):
    monkeypatch.setenv("EMBED_MAX", "0.5")
    ollama.side_effect = vectors({BASE: [1.0, 0.0, 0.0], UNRELATED: [0.6, 0.8, 0.0]})
    register(1, BASE)
    assert check(UNRELATED)["novel"] is False


def test_ollama_nonsense_counts_as_down(ollama):
    ollama.side_effect = lambda request: httpx.Response(200, json={"embeddings": []})
    register(1, BASE)
    assert check(UNRELATED)["embedding_checked"] is False


# ---------- outcomes and the stop rule


def decide(pid, decisions, start=1):
    out = None
    for i, d in enumerate(decisions, start=start):
        r = client.post(f"/pillars/{pid}/outcomes", json={"item_id": i, "decision": d}, headers=AUTH)
        assert r.status_code == 200, r.text
        out = r.json()
    return out


def test_health_empty():
    p = make_pillar()
    h = client.get(f"/pillars/{p['id']}/health").json()
    assert h["decided"] == 0 and h["approved_clean_rate"] is None and h["paused"] is False


def test_no_pause_before_min_decisions():
    p = make_pillar()
    h = decide(p["id"], ["rejected"] * 9)
    assert h["decided"] == 9 and h["rejected_rate"] == 1.0 and h["paused"] is False


def test_pause_on_reject_rate_above_a_quarter():
    p = make_pillar()
    h = decide(p["id"], ["approved"] * 7 + ["rejected"] * 3)  # 30% rejected
    assert h["paused"] is True and "rejected 30%" in h["reason"]
    assert client.get(f"/pillars/{p['id']}").json()["status"] == "paused"


def test_rejected_rate_at_the_limit_is_fine():
    p = make_pillar()
    h = decide(p["id"], ["approved"] * 6 + ["rejected"] * 2 + ["edited"] * 0 + ["approved"] * 2)  # 2/10
    assert h["decided"] == 10 and h["rejected_rate"] == 0.2 and h["paused"] is False
    p2 = make_pillar()
    h = decide(p2["id"], ["approved"] * 6 + ["rejected"] * 2)  # 2/8 = exactly 25%, but under MIN_DECISIONS
    h = decide(p2["id"], ["approved"] * 2 + ["edited"] * 1 + ["rejected"] * 1, start=9)  # 3/12 = 25%
    assert h["decided"] == 12 and h["rejected_rate"] == 0.25 and h["paused"] is False


def test_pause_on_low_clean_approval():
    p = make_pillar()
    h = decide(p["id"], ["approved"] * 4 + ["edited"] * 6)  # 40% clean, 0% rejected
    assert h["paused"] is True and "approved unedited 40%" in h["reason"]
    p2 = make_pillar()
    h = decide(p2["id"], ["approved"] * 5 + ["edited"] * 5)  # exactly 50% is fine
    assert h["paused"] is False and h["approved_clean_rate"] == 0.5


def test_first_decision_per_item_counts():
    p = make_pillar()
    decide(p["id"], ["rejected"])
    r = client.post(f"/pillars/{p['id']}/outcomes", json={"item_id": 1, "decision": "approved"}, headers=AUTH).json()
    assert r["recorded"] is False and r["counted_decision"] == "rejected" and r["decided"] == 1


def test_resume_starts_a_new_window():
    p = make_pillar()
    decide(p["id"], ["rejected"] * 10)
    r = client.post(f"/pillars/{p['id']}/resume", headers=AUTH).json()
    assert r["paused"] is False and r["decided"] == 0
    h = decide(p["id"], ["approved"] * 3, start=100)
    assert h["decided"] == 3 and h["paused"] is False
    assert client.get(f"/pillars/{p['id']}").json()["status"] == "active"


def test_outcome_validation_and_404():
    p = make_pillar()
    assert client.post(f"/pillars/{p['id']}/outcomes", json={"item_id": 1, "decision": "maybe"},
                       headers=AUTH).status_code == 422
    assert client.post("/pillars/9/outcomes", json={"item_id": 1, "decision": "approved"},
                       headers=AUTH).status_code == 404
    assert client.get("/pillars/9/health").status_code == 404
    assert client.post("/pillars/9/resume", headers=AUTH).status_code == 404


def test_replan_returns_the_calendar_items_of_removed_slots():
    p = make_pillar()
    add_atoms(p["id"], atoms(30))
    first = plan(p["id"], start_date="2026-10-05").json()
    assert first["removed_calendar_item_ids"] == []
    s1, s2, s3 = first["slots"][:3]
    for s, item in ((s1, 501), (s2, 502)):   # planned slots that got their idea item
        client.post(f"/slots/{s['id']}/status", json={"status": "planned", "calendar_item_id": item}, headers=AUTH)
    client.post(f"/slots/{s3['id']}/status", json={"status": "drafted", "calendar_item_id": 503}, headers=AUTH)
    again = plan(p["id"], start_date="2026-10-05").json()
    assert again["removed_calendar_item_ids"] == [501, 502]   # not the drafted one, not slots without an item
    assert plan(p["id"], start_date="2026-10-05").json()["removed_calendar_item_ids"] == []


# ---------- experiments (45): arms on slots


from app.main import assign_arms  # noqa: E402

CAMPAIGNS = "http://campaigns.test"


def hook_exp(eid=7, channels=("x",), variable="hook_style", values=("question", "fact_led")):
    return {"id": eid, "variable": variable, "channels": list(channels), "status": "running",
            "arms": [{"label": "A", "value": values[0], "brief": None},
                     {"label": "B", "value": values[1], "brief": "open with a number"}]}


def test_arms_balanced_by_weekday_hour_and_overall():
    slots, _ = plan_slots(channels=["x", "linkedin"], cadence={"x": 7, "linkedin": 3}, start=date(2026, 10, 5),
                          weeks=4, atoms=fake_atoms(40), kept=[], promo_max=0.2, seed=3, experiments=[hook_exp()])
    xs = [s for s in slots if s["channel"] == "x"]
    assert all(s["experiment"]["id"] == 7 for s in xs)
    assert all("experiment" not in s for s in slots if s["channel"] == "linkedin")
    total = Counter(s["experiment"]["arm"] for s in xs)
    assert abs(total["A"] - total["B"]) <= 1
    for wd in range(7):
        c = Counter(s["experiment"]["arm"] for s in xs if date.fromisoformat(s["date"]).weekday() == wd)
        assert abs(c["A"] - c["B"]) <= 1, (wd, c)
    # the hook is forced by the arm, and an (atom, hook) pair still never repeats
    assert all(s["hook_style"] == ("question" if s["experiment"]["arm"] == "A" else "fact_led") for s in xs)
    assert len({(s["atom_id"], s["hook_style"]) for s in slots}) == len(slots)
    # the order is random, not always A first
    firsts = set()
    for seed in range(8):
        sl, _ = plan_slots(channels=["x"], cadence={"x": 7}, start=date(2026, 10, 5), weeks=1, atoms=fake_atoms(20),
                           kept=[], promo_max=0.2, seed=seed, experiments=[hook_exp()])
        firsts.add(sl[0]["experiment"]["arm"])
    assert firsts == {"A", "B"}


def test_arms_count_kept_slots_and_split_two_experiments():
    kept = [{"channel": "x", "date": "2026-10-05", "time_utc": "09:00", "atom_id": 1, "hook_style": "question",
             "promo": False, "experiment_id": 7, "arm": "A"}]
    sl, _ = plan_slots(channels=["x"], cadence={"x": 1}, start=date(2026, 10, 5), weeks=1, atoms=fake_atoms(20),
                       kept=kept, promo_max=0.2, seed=0, experiments=[hook_exp()])
    assert sl == []   # the kept slot fills the week
    slots = [{"date": f"2026-10-{5 + i:02d}", "time_utc": "09:00", "channel": "x", "format": "post"} for i in range(7)]
    assign_arms(slots, [hook_exp(7), hook_exp(9, values=("story", "how_to")), hook_exp(11)], [], seed=1)
    assert Counter(s["experiment"]["id"] for s in slots) == {7: 4, 9: 3}   # at most 2 per channel
    fmt = [{"date": "2026-10-05", "time_utc": "09:00", "channel": "x", "format": "post"} for _ in range(4)]
    assign_arms(fmt, [hook_exp(3, variable="format", values=("post", "thread"))], [], seed=1)
    assert sorted(s["format"] for s in fmt) == ["post", "post", "thread", "thread"]
    tm = [{"date": "2026-10-05", "time_utc": "09:00", "channel": "x", "format": "post"} for _ in range(2)]
    assign_arms(tm, [hook_exp(4, variable="time", values=("08:00", "18:30"))], [], seed=1)
    assert sorted(s["time_utc"] for s in tm) == ["08:00", "18:30"]


def test_plan_assigns_arms_and_reports_them_to_45(monkeypatch, ollama):
    monkeypatch.setenv("CAMPAIGNS_URL", CAMPAIGNS)
    import respx as _respx
    router = _respx.mock(assert_all_called=False)
    with router:
        router.post(f"{OLLAMA}/api/embed").side_effect = httpx.ConnectError("down")
        router.get(f"{CAMPAIGNS}/experiments").mock(return_value=httpx.Response(200, json=[hook_exp()]))
        assign = router.post(f"{CAMPAIGNS}/experiments/7/assign").mock(
            return_value=httpx.Response(200, json={"experiment_id": 7, "status": "running"}))
        p = make_pillar(channels=["linkedin", "x"])
        add_atoms(p["id"], atoms(30))
        r = plan(p["id"], start_date="2026-10-05", weeks=2)
        assert r.status_code == 200, r.text
        out = r.json()
        xs = [s for s in out["slots"] if s["channel"] == "x"]
        assert all(s["experiment_id"] == 7 and s["arm"] in ("A", "B") for s in xs)
        assert xs[0]["experiment"]["variable"] == "hook_style"
        assert out["experiments"][0]["assigned"] == {"A": 7, "B": 7} and out["experiments_error"] is None
        sent = __import__("json").loads(assign.calls[0].request.content)
        assert sorted(x["slot_id"] for x in sent["slots"]) == sorted(s["id"] for s in xs)
        assert assign.calls[0].request.headers["X-API-Key"] == KEY
        assert router.routes[1].calls[0].request.url.params["status"] == "approved,running"
        # a re-plan tells 45 which slots went away
        old_ids = sorted(s["id"] for s in xs)
        again = plan(p["id"], start_date="2026-10-05", weeks=2).json()
        assert sorted(__import__("json").loads(assign.calls[1].request.content)["remove_slot_ids"]) == old_ids
        # the slot as the drafter reads it
        new_x = next(s for s in again["slots"] if s["channel"] == "x")
        got = client.get(f"/slots/{new_x['id']}").json()
        assert got["experiment"] == {"id": 7, "variable": "hook_style", "arm": got["arm"],
                                     "value": "question" if got["arm"] == "A" else "fact_led",
                                     "brief": None if got["arm"] == "A" else "open with a number"}


def test_plan_without_45_or_with_45_refusing(monkeypatch):
    p = make_pillar(channels=["x"])
    add_atoms(p["id"], atoms(20))
    out = plan(p["id"], weeks=1).json()          # CAMPAIGNS_URL unset: no experiments at all
    assert out["experiments"] == [] and all(s["experiment"] is None for s in out["slots"])
    monkeypatch.setenv("CAMPAIGNS_URL", CAMPAIGNS)
    import respx as _respx
    with _respx.mock(assert_all_called=False) as router:
        router.get(f"{CAMPAIGNS}/experiments").mock(return_value=httpx.Response(200, json=[hook_exp()]))
        router.post(f"{CAMPAIGNS}/experiments/7/assign").mock(return_value=httpx.Response(409, json={"detail": "stopped"}))
        out = plan(p["id"], weeks=1).json()
        assert all(s["experiment_id"] is None for s in out["slots"])
        assert "409" in out["experiments"][0]["error"]
    with _respx.mock(assert_all_called=False) as router:
        router.get(f"{CAMPAIGNS}/experiments").mock(side_effect=httpx.ConnectError("down"))
        out = plan(p["id"], weeks=1).json()
        assert out["experiments"] == [] and "ConnectError" in out["experiments_error"]
