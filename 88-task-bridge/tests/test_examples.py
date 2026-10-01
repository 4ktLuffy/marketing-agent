"""Approved, exported pieces feed the next pack for the same channel ("write like these")."""
import copy

import pytest

from app import pack

from . import data
from .conftest import AUTH, BIKE_TASK, make_task, paste_and_submit

LI = "Autumn rides from our Porthleven shop: a hybrid for the day is [[day-hire-porthleven]] and every hire includes [[insurance]]."
IG = "E-bikes {{ebike-day}}, riders must be 16 or over. Pedal the harbour."
TWO = f"=== 1 LINKEDIN ===\n{LI}\n\n=== 2 INSTAGRAM ===\n{IG}"
HEAD = "APPROVED EXAMPLES (match this voice; facts may have changed, use only the FACTS below):"
ONE_LI = {**BIKE_TASK, "pieces": [{"key": "p1", "channel": "linkedin", "kind": "post"}]}


def export_task(client, stack, text=TWO, task=None):
    t = make_task(client, stack, task=task) if not stack.facts else client.post("/tasks", json=task or BIKE_TASK,
                                                                                headers=AUTH).json()
    out = paste_and_submit(client, t["id"], text)
    for p in out["pieces"]:
        stack.approve(p["calendar_item_id"])
    r = client.get(f"/tasks/{t['id']}/export", headers=AUTH)
    assert r.status_code == 200, r.text
    return t, out


def new_task(client, task=None):
    r = client.post("/tasks", json=task or ONE_LI, headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def test_no_examples_when_nothing_was_approved(client, stack):
    make_task(client, stack)
    t = new_task(client)
    assert t["share_preview"]["examples"] == [] and HEAD not in t["pack"]


def test_not_exported_means_not_an_example(client, stack):
    t0 = make_task(client, stack)
    out = paste_and_submit(client, t0["id"], TWO)
    stack.approve(out["pieces"][0]["calendar_item_id"])     # approved but never exported
    t = new_task(client)
    assert t["share_preview"]["examples"] == []


def test_same_channel_example_appears_after_voice(client, stack):
    t0, out = export_task(client, stack)
    t = new_task(client)
    ex = t["share_preview"]["examples"]
    assert [(e["task_id"], e["piece_key"]) for e in ex] == [(t0["id"], "p1")]
    quoted = out["pieces"][0]["filled_text"]
    assert ex[0]["chars"] == len(quoted) and f'"{quoted}"' in t["pack"]
    assert t["pack"].index("VOICE:") < t["pack"].index(HEAD) < t["pack"].index("FACTS (use only")
    assert HEAD not in client.post("/tasks", json={**ONE_LI, "pieces": [{"key": "p1", "channel": "email"}]},
                                   headers=AUTH).json()["pack"]


def test_same_channel_ranks_before_family_and_max_two_newest_first(client, stack):
    export_task(client, stack)                                              # linkedin + instagram
    export_task(client, stack, text=f"=== 1 LINKEDIN ===\nHybrids ride free of worry, said Dee.\n\n"
                                    f"=== 2 INSTAGRAM ===\nHarbour days with a bike, all good.")
    export_task(client, stack, text=f"=== 1 LINKEDIN ===\nThird post about the harbour.\n\n"
                                    f"=== 2 INSTAGRAM ===\nThird gram of the harbour.")
    t = new_task(client)
    ex = t["share_preview"]["examples"]
    assert len(ex) == 2
    assert [e["piece_key"] for e in ex] == ["p1", "p1"]            # linkedin ones, not the instagram family ones
    assert "Third post" in t["pack"] and "Hybrids ride free" in t["pack"] and HEAD in t["pack"]
    assert t["pack"].index("Third post") < t["pack"].index("Hybrids ride free")      # newest first


def test_channel_family_is_the_fallback(client, stack):
    export_task(client, stack)
    t = new_task(client, {**ONE_LI, "pieces": [{"key": "p1", "channel": "facebook", "kind": "post"}]})
    assert {e["piece_key"] for e in t["share_preview"]["examples"]} == {"p1", "p2"}
    assert pack.example_rank("linkedin", ["linkedin"]) == 0 and pack.example_rank("instagram", ["linkedin"]) == 1
    assert pack.example_rank("email", ["linkedin"]) is None


def test_example_with_an_expired_price_is_skipped(client, stack):
    export_task(client, stack, text=f"=== 1 LINKEDIN ===\n{LI}", task=ONE_LI)
    stack.fact("day-hire-porthleven")["valid_to"] = "2026-10-01"      # the £28 is gone by the next publish date
    t = new_task(client)
    assert t["share_preview"]["examples"] == [] and HEAD not in t["pack"]
    assert "£28" not in t["pack"]


def test_internal_value_never_appears_in_an_example(client, stack):
    stack.facts = data.business("bikes")
    t0 = client.post("/tasks", json=ONE_LI, headers=AUTH).json()
    # a typed-out internal value: the piece is blocked, but even a piece that is approved later must not leak
    out = paste_and_submit(client, t0["id"], f"=== 1 LINKEDIN ===\nHotel guests get the {data.INTERNAL_SENTINEL} on every hire.")
    assert out["pieces"][0]["blocked"]
    # an exported piece that now carries an internal value (a fact became internal after export) is skipped
    t1, out1 = export_task(client, stack, text="=== 1 LINKEDIN ===\nEvery ride includes third-party insurance and a helmet.",
                           task=ONE_LI)
    stack.fact("insurance")["sensitivity"] = "internal"
    t = new_task(client)
    assert t["share_preview"]["examples"] == []
    assert data.INTERNAL_SENTINEL not in t["pack"] and "third-party insurance" not in t["pack"]


def test_examples_with_slots_or_internal_values_are_dropped_by_pack_build():
    secret = [{"key": "k", "value_text": "£13.37 partner rate", "value": 13.37, "sensitivity": "internal"}]
    assert not pack.example_safe("Ask about [[k]] today.", secret)
    assert not pack.example_safe("We give a £13.37 partner rate.", secret)
    assert pack.example_safe("A calm ride along the harbour.", secret)


def test_current_task_is_excluded():
    class Conn:  # the query is by task id; the current task's own export is never a candidate
        pass
    from app.db import approved_examples
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("CREATE TABLE exports (id INTEGER PRIMARY KEY, task_id TEXT, manifest TEXT);"
                    "CREATE TABLE pieces (task_id TEXT, piece_key TEXT, channel TEXT, filled_text TEXT, filled_sha256 TEXT);"
                    "INSERT INTO exports VALUES (1, 'T-A', '{\"pieces\":[{\"piece_key\":\"p1\",\"sha256\":\"h\"}]}');"
                    "INSERT INTO pieces VALUES ('T-A', 'p1', 'linkedin', 'Hello there.', 'h');")
    assert len(approved_examples(c)) == 1
    assert approved_examples(c, exclude_task="T-A") == []


def test_text_changed_since_approval_is_not_an_example(client, stack):
    import sqlite3, os
    t0, _ = export_task(client, stack)
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.execute("UPDATE pieces SET filled_sha256 = 'other' WHERE task_id = ?", (t0["id"],))
    conn.commit()
    assert new_task(client)["share_preview"]["examples"] == []


def test_long_example_is_cut_at_a_sentence_end():
    s = "A calm ride along the harbour. " * 30
    out = pack.trim_example(s)
    assert len(out) <= 400 and out.endswith("harbour.") and pack.trim_example("x" * 500) is None


def test_long_example_in_a_real_task(client, stack):
    export_task(client, stack, text="=== 1 LINKEDIN ===\n" + ("Ride along the quiet harbour front. " * 25), task=ONE_LI)
    ex = new_task(client)["share_preview"]["examples"]
    assert len(ex) == 1 and ex[0]["chars"] <= 400


def test_budget_respected_examples_never_cost_a_fact(client, stack, monkeypatch):
    export_task(client, stack)
    base = {}
    for mode in ("off", "on"):
        monkeypatch.setenv("PACK_EXAMPLES", mode)
        monkeypatch.setenv("PACK_TARGET_CHARS", "1000")
        base[mode] = new_task(client)
    assert base["on"]["share_preview"]["sent"] == base["off"]["share_preview"]["sent"]
    # sweep the target: the facts sent never depend on the examples, the hard cap always holds,
    # and the examples are the first thing to go when it is tight
    lengths = list(range(1000, 3000, 100))
    saw_dropped = saw_kept = False
    for target in lengths:
        monkeypatch.setenv("PACK_TARGET_CHARS", str(max(1000, target)))
        monkeypatch.setenv("PACK_EXAMPLES", "off")
        off = new_task(client)
        monkeypatch.setenv("PACK_EXAMPLES", "on")
        on = new_task(client)
        assert on["share_preview"]["sent"] == off["share_preview"]["sent"]
        assert on["share_preview"]["slotted"] == off["share_preview"]["slotted"]
        if on["share_preview"]["examples"]:
            saw_kept = True
            assert len(on["pack"]) <= max(max(1000, target), len(off["pack"]))
        else:
            saw_dropped = True
    assert saw_kept and saw_dropped
    monkeypatch.setenv("PACK_TARGET_CHARS", "1000")
    monkeypatch.setenv("PACK_MAX_CHARS", "2000")
    assert len(new_task(client)["pack"]) <= 2000


def test_off_switch(client, stack, monkeypatch):
    export_task(client, stack)
    monkeypatch.setenv("PACK_EXAMPLES", "off")
    t = new_task(client)
    assert t["share_preview"]["examples"] == [] and HEAD not in t["pack"]
    assert client.get("/health").json()["pack_examples"] is False
    monkeypatch.setenv("PACK_EXAMPLES", "on")
    assert new_task(client)["share_preview"]["examples"]


def test_snapshot_and_slots_unchanged_by_examples(client, stack, monkeypatch):
    export_task(client, stack)
    on = new_task(client)
    monkeypatch.setenv("PACK_EXAMPLES", "off")
    off = new_task(client)
    assert on["share_preview"]["examples"] and not off["share_preview"]["examples"]
    for k in ("sent", "slotted", "withheld"):
        assert on["share_preview"][k] == off["share_preview"][k]
    assert on["snapshot"] == off["snapshot"] and on["fact_set_version"] == off["fact_set_version"]

