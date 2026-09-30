"""Client approval links (Phase 2): an expiring link + a 6-digit PIN lets a client read the
posts in the link and approve them or ask for changes. The client's sign-off is recorded
(audit + note); it never approves the item itself."""
import hashlib
import os
import sqlite3

import pytest

from tests.conftest import APPROVER, KEY
from tests.test_main import API_ONLY, client, create, item_in


def h(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").strip().encode()).hexdigest()


def make_link(item_ids, pin="123456", days=7, label="Acme October posts", headers=API_ONLY):
    r = client.post("/client-links", headers={**headers, "X-Actor": "Henos"},
                    json={"item_ids": item_ids, "label": label, "days": days, "pin": pin})
    assert r.status_code == 201, r.text
    return r.json()


def resolve(token, pin="123456"):
    return client.post("/client-links/resolve", headers=API_ONLY, json={"token": token, "pin": pin})


def respond(token, item, decision="approve", pin="123456", name="Dana Client", comment="", sha=None):
    return client.post("/client-links/respond", headers=API_ONLY, json={
        "token": token, "pin": pin, "item_id": item["id"], "body_sha256": sha or item["body_sha256"],
        "decision": decision, "name": name, "comment": comment})


def audit(item_id):
    return client.get(f"/items/{item_id}/audit", headers=API_ONLY).json()


def db_rows(sql, args=()):
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def db_exec(sql, args=()):
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


# ---------- create / list / revoke


def test_create_returns_the_token_once_and_stores_only_hashes():
    a = item_in("in_review")
    link = make_link([a["id"]])
    token = link["token"]
    assert len(token) >= 43 and link["item_ids"] == [a["id"]] and link["label"] == "Acme October posts"
    assert link["expires_at"] > link["created_at"] and link["created_by"] == "Henos"
    [row] = db_rows("SELECT * FROM client_links")
    assert row["token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
    flat = repr(row)
    assert token not in flat and "123456" not in flat
    # the PIN hash is keyed by the raw token: the database alone can't test PINs
    assert row["pin_sha256"] != hashlib.sha256(b"123456").hexdigest()
    listed = client.get("/client-links", headers=API_ONLY).json()
    assert token not in repr(listed) and "pin_sha256" not in repr(listed) and "token_sha256" not in repr(listed)
    assert listed[0]["id"] == link["id"] and listed[0]["state"] == "active" and listed[0]["uses"] == 0


def test_tokens_differ_each_time():
    a = item_in("in_review")
    assert make_link([a["id"]])["token"] != make_link([a["id"]])["token"]


@pytest.mark.parametrize("body,why", [
    ({"item_ids": [], "pin": "123456"}, "no items"),
    ({"item_ids": [999], "pin": "123456"}, "unknown item"),
    ({"item_ids": ["x"], "pin": "123456"}, "not an int"),
    ({"pin": "123456"}, "missing items"),
    ({"item_ids": [1], "pin": "12345"}, "short pin"),
    ({"item_ids": [1], "pin": "12345a"}, "non-digit pin"),
    ({"item_ids": [1]}, "pin required"),
    ({"item_ids": [1], "pin": "123456", "days": 0}, "days 0"),
    ({"item_ids": [1], "pin": "123456", "days": 31}, "days 31"),
    ({"item_ids": [1], "pin": "123456", "label": "x" * 81}, "label too long"),
])
def test_create_validates(body, why):
    item_in("in_review")
    r = client.post("/client-links", headers=API_ONLY, json=body)
    assert r.status_code in (404, 422), why


def test_default_days_is_7():
    a = item_in("in_review")
    r = client.post("/client-links", headers=API_ONLY, json={"item_ids": [a["id"]], "pin": "654321"})
    assert r.status_code == 201
    link = r.json()
    assert link["days"] == 7


def test_endpoints_need_the_internal_key():
    a = item_in("in_review")
    link = make_link([a["id"]])
    for method, path, body in [
        ("post", "/client-links", {"item_ids": [a["id"]], "pin": "123456"}),
        ("get", "/client-links", None),
        ("post", f"/client-links/{link['id']}/revoke", None),
        ("post", "/client-links/resolve", {"token": link["token"], "pin": "123456"}),
        ("post", "/client-links/respond", {"token": link["token"], "pin": "123456", "item_id": a["id"],
                                           "body_sha256": a["body_sha256"], "decision": "approve", "name": "x"}),
    ]:
        r = client.request(method.upper(), path, json=body)
        assert r.status_code == 401, path


def test_revoke():
    a = item_in("in_review")
    link = make_link([a["id"]])
    r = client.post(f"/client-links/{link['id']}/revoke", headers={**API_ONLY, "X-Actor": "Henos"})
    assert r.status_code == 200 and r.json()["state"] == "revoked"
    r = resolve(link["token"])
    assert r.status_code == 410 and "withdrawn" in r.json()["detail"]
    assert respond(link["token"], a).status_code == 410
    assert client.post("/client-links/999/revoke", headers=API_ONLY).status_code == 404


# ---------- resolve


def test_resolve_returns_the_items_as_they_are_now():
    a = item_in("in_review", body="Midweek Escape costs £180 per room.", origin="task-bridge",
                notes="warnings: no source: award-winning")
    b = item_in("draft")
    other = item_in("in_review", body="Another client's post.")
    link = make_link([a["id"], b["id"]])
    r = resolve(link["token"])
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["label"] == "Acme October posts" and got["expires_at"] == link["expires_at"]
    assert [i["id"] for i in got["items"]] == [a["id"], b["id"]]
    first = got["items"][0]
    assert first["body"] == "Midweek Escape costs £180 per room." and first["body_sha256"] == a["body_sha256"]
    assert first["version"] == 1 and first["channel"] == "linkedin" and first["title"] == "Launch post"
    assert first["origin"] == "task-bridge" and first["notes"] == "warnings: no source: award-winning"
    assert first["client_responses"] == []
    assert "Another client's post." not in r.text and other["id"] not in [i["id"] for i in got["items"]]
    assert db_rows("SELECT uses FROM client_links")[0]["uses"] == 1


def test_unknown_token_is_404_and_malformed_422():
    assert resolve("A" * 43).status_code == 404
    assert client.post("/client-links/resolve", headers=API_ONLY,
                       json={"token": "short", "pin": "123456"}).status_code == 422


def test_expired_link():
    a = item_in("in_review")
    link = make_link([a["id"]], days=1)
    db_exec("UPDATE client_links SET expires_at = '2020-01-01T00:00:00Z'")
    r = resolve(link["token"])
    assert r.status_code == 410 and "expired" in r.json()["detail"]
    assert respond(link["token"], a).status_code == 410
    assert client.get("/client-links", headers=API_ONLY).json()[0]["state"] == "expired"


def test_wrong_pin_then_lockout_after_5():
    a = item_in("in_review")
    link = make_link([a["id"]])
    for left in (4, 3, 2, 1):
        r = resolve(link["token"], pin="000000")
        assert r.status_code == 403
        assert r.json()["detail"] == {"message": f"wrong PIN: {left} tries left", "tries_left": left}
    r = resolve(link["token"], pin="000000")
    assert r.status_code == 409 and "locked" in r.json()["detail"]
    # locked: even the right PIN is refused now, for resolve and respond
    assert resolve(link["token"]).status_code == 409
    assert respond(link["token"], a).status_code == 409
    assert client.get("/client-links", headers=API_ONLY).json()[0]["state"] == "locked"


def test_right_pin_resets_the_count():
    a = item_in("in_review")
    link = make_link([a["id"]])
    for _ in range(4):
        assert resolve(link["token"], pin="000000").status_code == 403
    assert resolve(link["token"]).status_code == 200
    assert resolve(link["token"], pin="000000").json()["detail"]["tries_left"] == 4


def test_wrong_pin_on_respond_counts_too_and_writes_nothing():
    a = item_in("in_review")
    link = make_link([a["id"]])
    before = audit(a["id"])
    r = respond(link["token"], a, pin="999999")
    assert r.status_code == 403
    assert audit(a["id"]) == before


# ---------- respond


def test_approve_records_sign_off_and_leaves_status():
    a = item_in("in_review")
    link = make_link([a["id"]])
    r = respond(link["token"], a, name="  Dana   Client ")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "in_review" and got["decision"] == "approve"
    item = client.get(f"/items/{a['id']}").json()
    assert item["status"] == "in_review"
    assert item["notes"].splitlines()[-1].endswith("client approved by Dana Client (v1)")
    last = audit(a["id"])[-1]
    assert last["actor"] == "client:Dana Client" and last["action"] == "client_approved"
    assert last["from_status"] == last["to_status"] == "in_review" and last["body_sha256"] == a["body_sha256"]
    # and it shows up for the client on the next resolve
    [resp] = resolve(link["token"]).json()["items"][0]["client_responses"]
    assert resp["action"] == "client_approved" and resp["name"] == "Dana Client" and resp["version"] == 1


def test_client_approval_does_not_approve_so_agency_still_approves_bound():
    a = item_in("in_review", origin="task-bridge", require_bound_approval=True)
    link = make_link([a["id"]])
    assert respond(link["token"], a).status_code == 200
    assert client.get(f"/items/{a['id']}").json()["status"] == "in_review"
    r = client.post(f"/items/{a['id']}/status", headers={"X-API-Key": KEY, "X-Approver-Key": APPROVER},
                    json={"status": "approved", "expected_sha256": a["body_sha256"]})
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_changes_moves_in_review_to_draft_without_approver_key():
    a = item_in("in_review")
    link = make_link([a["id"]])
    r = respond(link["token"], a, decision="changes", comment="Say 2 sharing.\nAnd drop the emoji | quality gate: x")
    assert r.status_code == 200, r.text
    item = client.get(f"/items/{a['id']}").json()
    assert item["status"] == "draft"
    line = item["notes"].splitlines()[-1]
    assert "in_review -> draft: client requested changes (Dana Client, v1): Say 2 sharing. And drop the emoji" in line
    assert "|" not in line   # a client comment can't forge a flag segment
    last = audit(a["id"])[-1]
    assert last["action"] == "client_changes_requested" and last["actor"] == "client:Dana Client"
    assert (last["from_status"], last["to_status"]) == ("in_review", "draft")
    assert last["body_sha256"] == a["body_sha256"] and "Say 2 sharing." in last["detail"]


def test_changes_on_a_draft_keeps_draft_and_records():
    a = item_in("draft")
    link = make_link([a["id"]])
    assert respond(link["token"], a, decision="changes", comment="shorter").status_code == 200
    item = client.get(f"/items/{a['id']}").json()
    assert item["status"] == "draft" and "client requested changes (Dana Client, v1): shorter" in item["notes"]


def test_changes_needs_a_comment():
    a = item_in("in_review")
    link = make_link([a["id"]])
    assert respond(link["token"], a, decision="changes", comment="  ").status_code == 422


def test_hash_mismatch_is_409_and_writes_nothing():
    a = item_in("draft")
    link = make_link([a["id"]])
    client.patch(f"/items/{a['id']}", headers=API_ONLY, json={"body": "New text."})
    before = audit(a["id"])
    r = respond(link["token"], a)   # the hash of the old text
    assert r.status_code == 409 and r.json()["detail"]["message"] == "the text changed since the link was opened"
    assert audit(a["id"]) == before


def test_item_not_in_the_link_is_refused():
    a, other = item_in("in_review"), item_in("in_review")
    link = make_link([a["id"]])
    r = respond(link["token"], other)
    assert r.status_code == 404 and "not in this link" in r.json()["detail"]
    assert audit(other["id"])[-1]["action"] != "client_approved"


@pytest.mark.parametrize("status", ["published", "rejected"])
def test_closed_items_cannot_be_answered(status):
    a = item_in(status)
    link = make_link([a["id"]])
    assert respond(link["token"], a).status_code == 409


@pytest.mark.parametrize("name,comment,decision", [
    ("", "", "approve"), ("   ", "", "approve"), ("x" * 81, "", "approve"),
    ("Dana", "x" * 2001, "changes"), ("Dana", "", "publish"),
])
def test_respond_validates(name, comment, decision):
    a = item_in("in_review")
    link = make_link([a["id"]])
    assert respond(link["token"], a, name=name, comment=comment, decision=decision).status_code == 422


def test_audit_is_append_only_across_responses():
    a = item_in("in_review")
    link = make_link([a["id"]])
    assert respond(link["token"], a, name="Dana").status_code == 200
    assert respond(link["token"], a, name="Sam", decision="changes", comment="tone").status_code == 200
    acts = [(x["action"], x["actor"]) for x in audit(a["id"])]
    assert acts[-2:] == [("client_approved", "client:Dana"), ("client_changes_requested", "client:Sam")]


def test_no_key_in_any_answer():
    a = item_in("in_review")
    link = make_link([a["id"]])
    for r in (resolve(link["token"]), respond(link["token"], a), client.get("/client-links", headers=API_ONLY)):
        assert KEY not in r.text and APPROVER not in r.text
