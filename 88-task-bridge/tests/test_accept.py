"""Accepting a finding ("it's there, in other words"): only missing_disclosure, only on the text seen,
recorded, and the item moves to review only when nothing else blocks it."""
from .conftest import AUTH, make_task, paste_and_submit

PASTE = ("=== 1 LINKEDIN ===\nHybrids are [[day-hire-porthleven]]. E-bikes are £45 a day, for riders aged sixteen and up."
         "\n\n=== 2 INSTAGRAM ===\nHybrids are [[day-hire-porthleven]] at Porthleven.")


def submitted(client, stack, text=PASTE):
    t = make_task(client, stack)
    out = paste_and_submit(client, t["id"], text)
    p1 = out["pieces"][0]
    return t["id"], p1


def md_index(p):
    return next(i for i, f in enumerate(p["findings"]) if f["label"] == "missing_disclosure" and f["blocking"])


def accept(client, tid, p, i=None, sha=None, **kw):
    body = {"finding": md_index(p) if i is None else i, "expected_sha256": sha or p["filled_sha256"],
            "by": "Maya Okafor", "note": "the age limit is there as 'riders aged sixteen and up'", **kw}
    return client.post(f"/tasks/{tid}/pieces/{p['piece_key']}/accept", json=body, headers=AUTH)


def test_accept_moves_a_piece_to_review_and_records_it(client, stack):
    tid, p = submitted(client, stack)
    assert p["blocked"] and p["status"] == "draft"
    r = accept(client, tid, p)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["blocked"] is False and out["status"] == "in_review"
    assert out["finding"]["accepted"]["by"] == "Maya Okafor" and out["finding"]["blocking"] is False
    item = stack.items[p["calendar_item_id"]]
    assert item["status"] == "in_review" and "accepted a missing_disclosure finding" in item["notes"]
    task = client.get(f"/tasks/{tid}", headers=AUTH).json()
    ev = [e for e in task["events"] if e["kind"] == "finding_accepted"]
    assert len(ev) == 1 and ev[0]["detail"]["sha256"] == p["filled_sha256"] and ev[0]["detail"]["moved_to"] == "in_review"
    piece = next(x for x in task["piece_state"] if x["piece_key"] == "p1")
    assert piece["state"] == "in_review" and piece["blocked"] is False


def test_the_export_manifest_lists_the_accepted_finding(client, stack):
    tid, p = submitted(client, stack)
    assert accept(client, tid, p).status_code == 200
    for iid in list(stack.items):
        stack.approve(iid)
    r = client.get(f"/tasks/{tid}/export?format=md", headers=AUTH)
    assert r.status_code == 200, r.text
    assert "Maya Okafor accepted a missing_disclosure finding" in r.text
    import json, re
    manifest = json.loads(re.search(r"```json\n(.*?)\n```", r.text, re.S).group(1))
    acc = manifest["accepted_findings"]["p1"]
    assert acc[0]["label"] == "missing_disclosure" and acc[0]["by"] == "Maya Okafor"


def test_stale_hash_is_refused(client, stack):
    tid, p = submitted(client, stack)
    r = accept(client, tid, p, sha="0" * 64)
    assert r.status_code == 409 and r.json()["detail"]["current_sha256"] == p["filled_sha256"]
    assert stack.items[p["calendar_item_id"]]["status"] == "draft"


def test_a_wrong_price_can_never_be_accepted(client, stack):
    tid, p = submitted(client, stack, PASTE.replace("£45 a day", "£40 a day"))
    i = next(i for i, f in enumerate(p["findings"]) if f["blocking"] and f["label"] != "missing_disclosure")
    r = accept(client, tid, p, i=i)
    assert r.status_code == 422
    assert stack.items[p["calendar_item_id"]]["status"] == "draft"


def test_a_non_blocking_finding_cannot_be_accepted(client, stack):
    tid, p = submitted(client, stack)
    i = next(i for i, f in enumerate(p["findings"]) if not f["blocking"])
    assert accept(client, tid, p, i=i).status_code == 422


def test_accepting_twice_is_refused(client, stack):
    tid, p = submitted(client, stack)
    assert accept(client, tid, p).status_code == 200
    r = accept(client, tid, p)
    assert r.status_code == 409 and r.json()["detail"]["message"] == "already accepted"


def test_still_blocked_by_another_finding_stays_in_draft(client, stack):
    tid, p = submitted(client, stack, PASTE.replace("Hybrids are [[day-hire-porthleven]]. E-bikes",
                                                    "Hybrids are £20 per day. E-bikes"))
    r = accept(client, tid, p)
    assert r.status_code == 200 and r.json()["blocked"] is True and r.json()["status"] == "draft"
    assert stack.items[p["calendar_item_id"]]["status"] == "draft"
    assert not any(c["status"] == "in_review" for c in stack.status_calls)


def test_a_brand_error_still_blocks(client, stack):
    stack.brand_violations = [{"rule": "banned_word", "severity": "error", "detail": "no 'cheap'"}]
    tid, p = submitted(client, stack)
    r = accept(client, tid, p)
    assert r.status_code == 200 and r.json()["blocked"] is True
    assert stack.items[p["calendar_item_id"]]["status"] == "draft"


def test_needs_key_and_a_reason(client, stack):
    tid, p = submitted(client, stack)
    url = f"/tasks/{tid}/pieces/p1/accept"
    body = {"finding": md_index(p), "expected_sha256": p["filled_sha256"], "by": "Maya", "note": "it is there"}
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json={**body, "note": ""}, headers=AUTH).status_code == 422
    assert client.post(url, json={**body, "by": ""}, headers=AUTH).status_code == 422
    assert client.post(url, json={**body, "finding": 99}, headers=AUTH).status_code == 404
    assert client.post(f"/tasks/{tid}/pieces/p9/accept", json=body, headers=AUTH).status_code == 404
