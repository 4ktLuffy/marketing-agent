"""Versions, audit log and version-bound approval (Phase 1, P1.2)."""
import hashlib
import sqlite3

import pytest

from tests.test_main import API_ONLY, AUTH, OLD_SCHEMA, client, create, item_in, move


def h(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").strip().encode()).hexdigest()


def status(item_id, target, headers=AUTH, **kw):
    return client.post(f"/items/{item_id}/status", json={"status": target, **kw}, headers=headers)


def versions(item_id):
    r = client.get(f"/items/{item_id}/versions", headers=API_ONLY)
    assert r.status_code == 200, r.text
    return r.json()


def audit(item_id):
    r = client.get(f"/items/{item_id}/audit", headers=API_ONLY)
    assert r.status_code == 200, r.text
    return r.json()


def bound_in_review(body="Midweek Escape costs £180 per room."):
    return create(status="in_review", body=body, origin="task-bridge", require_bound_approval=True)


# ---------- hash, version 1, origin


def test_create_writes_version_1_and_an_audit_row():
    item = create(body="  Hello\r\nworld  ", image_url="http://x.test/a.png")
    assert item["body_sha256"] == h("Hello\nworld") and item["version"] == 1
    [v] = versions(item["id"])
    assert v == {"n": 1, "body": "Hello\r\nworld", "body_sha256": h("Hello\nworld"),
                 "image_url": "http://x.test/a.png", "video_url": None,
                 "created_by": "unknown", "created_at": item["created_at"]}
    [a] = audit(item["id"])
    assert a["action"] == "created" and a["to_status"] == "draft" and a["from_status"] is None
    assert a["body_sha256"] == item["body_sha256"] and a["actor"] == "unknown"


def test_canonical_hash_ignores_crlf_and_outer_whitespace_only():
    a = create(body="one\r\ntwo")
    b = create(body="one\ntwo")
    c = create(body="one\ntwo.")
    assert a["body_sha256"] == b["body_sha256"] != c["body_sha256"]


def test_origin_and_require_bound_are_stored():
    item = bound_in_review()
    assert item["origin"] == "task-bridge" and item["require_bound"] is True
    assert create()["require_bound"] is False


def test_origin_is_validated():
    r = client.post("/items", headers=AUTH, json={"title": "t", "channel": "x", "body": "b",
                                                    "origin": "x" * 41})
    assert r.status_code == 422


# ---------- PATCH: versions, audit, if_match


def test_body_patch_writes_a_new_version_and_audit_row():
    item = create()
    r = client.patch(f"/items/{item['id']}", headers={**AUTH, "X-Actor": "Alex"},
                     json={"body": "We launched v2."})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert r.json()["body_sha256"] == h("We launched v2.")
    vs = versions(item["id"])
    assert [v["n"] for v in vs] == [1, 2] and vs[1]["created_by"] == "Alex"
    assert vs[0]["body"] == "We launched." and vs[1]["body"] == "We launched v2."
    last = audit(item["id"])[-1]
    assert last["action"] == "edit" and last["actor"] == "Alex" and "body" in last["detail"]


def test_media_patch_is_a_new_version_but_a_reschedule_is_only_audited():
    item = create()
    client.patch(f"/items/{item['id']}", headers=AUTH, json={"image_url": "http://x.test/b.png"})
    assert [v["n"] for v in versions(item["id"])] == [1, 2]
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"scheduled_at": "2026-10-01T09:00:00Z"})
    assert r.json()["version"] == 2
    assert [a["action"] for a in audit(item["id"])] == ["created", "edit", "edit"]
    # the same body again is not a new version
    client.patch(f"/items/{item['id']}", headers=AUTH, json={"body": "We launched."})
    assert len(versions(item["id"])) == 2


def test_patch_if_match_mismatch_is_409_with_current_hash():
    item = create()
    r = client.patch(f"/items/{item['id']}", headers=AUTH,
                     json={"body": "new", "if_match_sha256": "0" * 64})
    assert r.status_code == 409
    assert r.json()["detail"] == {"message": "changed since you looked", "current_sha256": item["body_sha256"]}
    assert client.get(f"/items/{item['id']}").json()["body"] == "We launched."
    ok = client.patch(f"/items/{item['id']}", headers=AUTH,
                      json={"body": "new", "if_match_sha256": item["body_sha256"]})
    assert ok.status_code == 200 and ok.json()["body"] == "new"
    assert "if_match_sha256" not in ok.json()


@pytest.mark.parametrize("bad", ["abc", "G" * 64, "0" * 63, 5])
def test_patch_if_match_must_be_hex64(bad):
    item = create()
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"body": "new", "if_match_sha256": bad})
    assert r.status_code == 422


def test_frozen_field_409_still_works():
    item = item_in("approved")
    r = client.patch(f"/items/{item['id']}", headers=AUTH,
                     json={"body": "sneaky", "if_match_sha256": item["body_sha256"]})
    assert r.status_code == 409 and r.json()["detail"]["current"] == "approved"


# ---------- status: audit always, bound approval


def test_status_change_without_note_is_audited_with_actor_from_note_or_header():
    item = create(status="in_review")
    assert move(item["id"], "draft").status_code == 200                      # no note
    assert move(item["id"], "in_review", note="ready by Aster").status_code == 200
    assert status(item["id"], "approved", headers={**AUTH, "X-Actor": "Alex"}).status_code == 200
    rows = audit(item["id"])[1:]
    assert [(a["action"], a["from_status"], a["to_status"], a["actor"]) for a in rows] == [
        ("status", "in_review", "draft", "unknown"),
        ("status", "draft", "in_review", "Aster"),
        ("status", "in_review", "approved", "Alex"),
    ]
    assert rows[0]["detail"] is None and "ready by Aster" in rows[1]["detail"]
    assert all(a["body_sha256"] == item["body_sha256"] for a in rows)
    # the notes column is still written only when a note is given
    assert client.get(f"/items/{item['id']}").json()["notes"].count("\n") == 0


def test_bound_item_approve_without_hash_is_428():
    item = bound_in_review()
    r = status(item["id"], "approved")
    assert r.status_code == 428
    assert "expected_sha256" in r.json()["detail"]
    assert client.get(f"/items/{item['id']}").json()["status"] == "in_review"


def test_bound_item_approve_with_stale_hash_is_409():
    item = bound_in_review()
    seen = item["body_sha256"]
    client.patch(f"/items/{item['id']}", headers=AUTH, json={"body": "Midweek Escape costs £150 per room."})
    r = status(item["id"], "approved", expected_sha256=seen)
    assert r.status_code == 409
    assert r.json()["detail"]["message"] == "changed since you looked"
    assert r.json()["detail"]["current_sha256"] == h("Midweek Escape costs £150 per room.")
    assert client.get(f"/items/{item['id']}").json()["status"] == "in_review"


def test_bound_item_approve_with_right_hash():
    item = bound_in_review()
    r = status(item["id"], "approved", expected_sha256=item["body_sha256"])
    assert r.status_code == 200 and r.json()["status"] == "approved"
    last = audit(item["id"])[-1]
    assert last["to_status"] == "approved" and "bound" in last["detail"]


def test_bound_item_approve_with_expected_body_hashes_canonically():
    item = bound_in_review()
    assert status(item["id"], "approved", expected_body="Midweek Escape costs £999.").status_code == 409
    r = status(item["id"], "approved", expected_body="  Midweek Escape costs £180 per room.\r\n")
    assert r.status_code == 200


def test_legacy_unbound_item_approves_without_a_hash_but_checks_one_if_given():
    item = create(status="in_review")
    assert status(item["id"], "approved", expected_sha256="f" * 64).status_code == 409
    assert status(item["id"], "approved").status_code == 200


@pytest.mark.parametrize("bad", ["xyz", "A" * 64])
def test_expected_sha256_must_be_hex64(bad):
    item = bound_in_review()
    assert status(item["id"], "approved", expected_sha256=bad).status_code == 422


def test_approved_back_to_draft_needs_no_approver_key():
    item = item_in("approved")
    assert status(item["id"], "draft", headers=API_ONLY).status_code == 200


def test_approver_key_unset_refuses_approving_and_publishing(monkeypatch):
    item = item_in("approved")
    other = create(status="in_review")
    monkeypatch.delenv("APPROVER_KEY")
    r = status(other["id"], "approved")
    assert r.status_code == 503 and "APPROVER_KEY" in r.json()["detail"]
    assert client.post(f"/items/{item['id']}/published", headers=AUTH, json={}).status_code == 503
    assert status(item["id"], "published").status_code == 503
    assert status(other["id"], "rejected").status_code == 200               # other moves still work


def test_published_is_audited():
    item = item_in("approved")
    client.post(f"/items/{item['id']}/published", headers=AUTH, json={})
    assert audit(item["id"])[-1]["to_status"] == "published"


# ---------- keyed reads


def test_versions_and_audit_need_the_key_and_404_for_missing():
    item = create()
    for path in ("versions", "audit"):
        assert client.get(f"/items/{item['id']}/{path}").status_code == 401
        assert client.get(f"/items/999/{path}", headers=API_ONLY).status_code == 404


# ---------- migration


def test_old_schema_db_migrates_intact_with_version_1_backfilled(tmp_path, monkeypatch):
    from app import main
    path = tmp_path / "old-v.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO items (title, channel, body, status, created_at, updated_at) VALUES"
        " ('Old', 'linkedin', 'old body\r\n', 'in_review', '2020-01-01T00:00:00Z', '2020-01-02T00:00:00Z')")
    conn.execute(
        "INSERT INTO items (title, channel, body, status, created_at, updated_at) VALUES"
        " ('Old 2', 'x', 'second', 'approved', '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("DB_PATH", str(path))

    old = client.get("/items/1").json()
    assert old["title"] == "Old" and old["body"] == "old body\r\n" and old["status"] == "in_review"
    assert old["version"] == 1 and old["body_sha256"] == h("old body")
    assert old["origin"] is None and old["require_bound"] is False
    [v] = versions(1)
    assert v["n"] == 1 and v["body"] == "old body\r\n" and v["created_by"] == "migration"
    assert v["created_at"] == "2020-01-02T00:00:00Z"
    assert versions(2)[0]["body"] == "second"
    # the backfill runs once: a restart does not add a second version 1
    main._migrated.discard(str(path))
    client.get("/items")
    assert len(versions(1)) == 1
    # legacy item: unbound, approves without a hash
    assert status(1, "approved").status_code == 200
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(items)")}
    assert {"origin", "require_bound"} <= cols
