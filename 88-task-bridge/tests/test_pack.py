"""The pack: what leaves the business, and the preview that says so."""
import re

from app import pack

from . import data
from .conftest import AUTH, BIKE_TASK, make_task


def fact_lines(text):
    return [line for line in text.split("\n") if line.startswith("- [[")]


def test_pack_has_no_internal_value_and_no_restricted_fact(client, stack):
    stack.summary = ("Brand: Tidewater Bike Hire - bikes by the sea\nTone: friendly\n"
                     f"Key messages: hotel guests pay the {data.INTERNAL_SENTINEL}\n"
                     f"Internal: {data.RESTRICTED_SENTINEL}\nDon't: say cheap")
    t = make_task(client, stack)
    p = t["pack"]
    assert data.INTERNAL_SENTINEL not in p and "13.37" not in p
    assert data.RESTRICTED_SENTINEL not in p and "9.91" not in p and "supplier-cost" not in p
    assert "[[partner-rate]]" in p and "use the slot, don't write a value" in p
    assert "Tone: friendly" in p and "Don't: say cheap" in p          # the rest of the voice stays
    prev = t["share_preview"]
    assert prev["slotted"] == ["partner-rate"]
    withheld = {w["key"]: w["reason"] for w in prev["withheld"]}
    assert withheld["supplier-cost"] == "restricted"
    assert withheld["partner-rate"] == "internal"
    assert withheld["day-hire-falmouth"] == "out_of_scope"
    assert withheld["summer-saver"] == "expired"                      # valid today, expired on publish day
    snap = {s["key"]: s for s in t["snapshot"]}
    assert set(snap) == {"day-hire-porthleven", "ebike-day", "insurance", "partner-rate", "no-cheapest"}
    assert snap["partner-rate"] == {"key": "partner-rate", "version": 1, "slot": "[[partner-rate]]",
                                    "sensitivity": "internal"}


def test_restricted_fact_served_by_mistake_still_never_goes_out(client, stack, mock):
    """Defence in depth: even if 05 returned a restricted fact, the pack leaves it out."""
    facts = data.business("bikes")
    t = make_task(client, stack, facts)
    assert "supplier-cost" not in t["pack"]
    built = pack.build("T-TEST01", "fs-1-x", data.PUBLISH, "goal words", None, None,
                       [{"key": "p1", "channel": "linkedin"}], {"sites": ["porthleven"]}, facts, [], facts, None,
                       None, 8000)
    assert data.RESTRICTED_SENTINEL not in built.text and data.INTERNAL_SENTINEL not in built.text
    assert {"key": "supplier-cost", "reason": "restricted"} in built.withheld


def test_preview_is_exactly_the_fact_lines_of_the_pack(client, stack):
    t = make_task(client, stack)
    lines = fact_lines(t["pack"])
    public = [x for x in lines if "(use the slot, don't write a value)" not in x]
    slot_only = [x for x in lines if x not in public]
    assert t["share_preview"]["sent"] == public
    assert [re.match(r"- \[\[([a-z0-9-]+)\]\]", x).group(1) for x in slot_only] == t["share_preview"]["slotted"]
    for line in t["share_preview"]["sent"]:
        assert line in t["pack"].split("\n")
    assert t["share_preview"]["chars"] == len(t["pack"])


def test_pack_header_markers_rules_and_disclosures(client, stack):
    t = make_task(client, stack)
    p = t["pack"]
    first = p.split("\n")[0]
    assert re.fullmatch(r"Task T-[A-Z2-7]{6} · facts fs-\d+-[0-9a-f]{8} · publish 2026-10-06", first)
    assert first.split(" ")[1] == t["id"]
    assert "=== 1 LINKEDIN ===" in p and "=== 2 INSTAGRAM ===" in p
    assert "- linkedin: max 3000 characters" in p and "at most 30 hashtags" in p
    assert '(must include: "riders must be 16 or over")' in p
    assert "[[missing: what you need]]" in p and "No preamble" in p
    assert "GOAL: Autumn weekday hires" in p and "AUDIENCE: families on holiday" in p


def test_pack_is_capped_and_drops_least_relevant_facts_first(client, stack, monkeypatch):
    facts = data.business("bikes")
    for i in range(60):
        facts.append(data.fact(f"filler-{i:02d}", f"Filler statement number {i} about the harbour café opposite. " * 3,
                               f"filler value {i}", subject={"kind": "site", "ref": "Harbour café"}))
    t = make_task(client, stack, facts)
    assert len(t["pack"]) <= 8000
    withheld = {w["key"]: w["reason"] for w in t["share_preview"]["withheld"]}
    too_long = [k for k, r in withheld.items() if r == "too_long"]
    assert too_long and all(k.startswith("filler-") for k in too_long)
    for k in ("day-hire-porthleven", "ebike-day"):                 # relevant to the goal: kept
        assert f"[[{k}]]" in t["pack"]
    assert t["share_preview"]["sent"] == [x for x in fact_lines(t["pack"]) if "use the slot" not in x]


def test_smaller_cap_from_env(client, stack, monkeypatch):
    monkeypatch.setenv("PACK_MAX_CHARS", "2000")
    t = make_task(client, stack, task={**BIKE_TASK, "notes": "Mention the coast path. " * 30})
    assert len(t["pack"]) <= 2000
    assert any(w["reason"] == "too_long" for w in t["share_preview"]["withheld"])


def test_pack_endpoint_and_text_form(client, stack):
    t = make_task(client, stack)
    r = client.get(f"/tasks/{t['id']}/pack", headers=AUTH)
    assert r.json()["pack"] == t["pack"] and r.json()["pack_sha256"] == t["pack_sha256"]
    r = client.get(f"/tasks/{t['id']}/pack", params={"format": "txt"}, headers=AUTH)
    assert r.text == t["pack"] and r.headers["content-type"].startswith("text/plain")


def test_neutral_description_never_carries_the_value():
    f = data.fact("secret", "x", "£99 off", subject={"kind": "offer", "ref": "£99 off deal"}, attribute="discount",
                  value=99, currency="GBP", sensitivity="internal")
    assert "99" not in pack.neutral(f)
    assert pack.neutral(data.business("bikes")[5]) == "the approved partner rate for Hotel partners"


def test_task_validation(client, stack):
    stack.facts = data.business("bikes")
    bad = [
        {**BIKE_TASK, "publish_on": "06/10/2026"},
        {**BIKE_TASK, "pieces": []},
        {**BIKE_TASK, "pieces": [{"key": "p1", "channel": "x"}, {"key": "p1", "channel": "x"}]},
        {**BIKE_TASK, "goal": "x" * 2001},
        {**BIKE_TASK, "scope": {"planets": ["mars"]}},
    ]
    for body in bad:
        assert client.post("/tasks", json=body, headers=AUTH).status_code == 422, body


def test_pack_never_exceeds_the_cap(client, stack, monkeypatch):
    facts = data.business("bikes") + [data.fact(f"extra-{i}", "Something long about the harbour. " * 4, f"value {i}")
                                      for i in range(40)]
    for cap in (2000, 3000, 5000, 8000):
        monkeypatch.setenv("PACK_MAX_CHARS", str(cap))
        t = make_task(client, stack, facts, task={**BIKE_TASK, "notes": "Keep it short. " * 20})
        assert len(t["pack"]) <= cap and t["share_preview"]["chars"] == len(t["pack"])


def test_task_too_long_for_the_cap_is_refused(client, stack, monkeypatch):
    monkeypatch.setenv("PACK_MAX_CHARS", "2000")
    stack.facts = data.business("bikes")
    r = client.post("/tasks", json={**BIKE_TASK, "goal": "g" * 1500, "notes": "n" * 1500}, headers=AUTH)
    assert r.status_code == 422 and "longer than the pack limit" in r.text
