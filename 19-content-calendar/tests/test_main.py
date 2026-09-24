import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import TRANSITIONS, app

KEY = "test-key"
AUTH = {"X-API-Key": KEY}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "calendar.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)


client = TestClient(app)


def create(**kw):
    payload = {"title": "Launch post", "channel": "linkedin", "body": "We launched.", **kw}
    r = client.post("/items", json=payload, headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def move(item_id, status, note=None):
    body = {"status": status} | ({"note": note} if note else {})
    return client.post(f"/items/{item_id}/status", json=body, headers=AUTH)


def item_in(status, **kw):
    """Create an item and walk it along valid transitions into `status`."""
    paths = {
        "idea": ["idea"], "draft": ["draft"], "in_review": ["in_review"],
        "rejected": ["draft", "rejected"], "approved": ["in_review", "approved"],
        "published": ["in_review", "approved", "published"],
    }
    first, *rest = paths[status]
    item = create(status=first, **kw)
    for s in rest:
        assert move(item["id"], s).status_code == 200
    return client.get(f"/items/{item['id']}").json()


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


# ---------- create / read


def test_create_defaults_to_draft_with_all_fields():
    item = create()
    assert set(item) == {
        "id", "title", "channel", "body", "status", "scheduled_at", "published_at",
        "campaign", "link", "external_url", "notes", "created_at", "updated_at",
        "campaign_id", "short_url", "hook_style",
    }
    assert item["campaign_id"] is None and item["short_url"] is None
    assert isinstance(item["id"], int)
    assert item["status"] == "draft"
    assert item["scheduled_at"] is None and item["published_at"] is None
    assert item["created_at"].endswith("Z")
    assert client.get(f"/items/{item['id']}").json() == item


@pytest.mark.parametrize("status", ["idea", "draft", "in_review"])
def test_create_allowed_initial_statuses(status):
    assert create(status=status)["status"] == status


@pytest.mark.parametrize("status", ["approved", "published", "rejected", "bogus"])
def test_create_rejects_other_initial_statuses(status):
    r = client.post("/items", json={"title": "t", "channel": "x", "body": "b", "status": status},
                    headers=AUTH)
    assert r.status_code == 422


def test_create_rejects_blank_fields():
    r = client.post("/items", json={"title": " ", "channel": "x", "body": "b"}, headers=AUTH)
    assert r.status_code == 422


@pytest.mark.parametrize("given,stored", [
    ("2026-10-01T09:00:00", "2026-10-01T09:00:00Z"),          # naive = UTC
    ("2026-10-01T09:00:00Z", "2026-10-01T09:00:00Z"),
    ("2026-10-01T11:00:00+02:00", "2026-10-01T09:00:00Z"),
    ("2026-10-01T09:00:00.123456+00:00", "2026-10-01T09:00:00Z"),
])
def test_scheduled_at_normalized_to_utc_z(given, stored):
    assert create(scheduled_at=given)["scheduled_at"] == stored


def test_bad_scheduled_at_rejected():
    r = client.post("/items", json={"title": "t", "channel": "x", "body": "b",
                                    "scheduled_at": "next tuesday"}, headers=AUTH)
    assert r.status_code == 422


def test_get_missing_item_404():
    assert client.get("/items/999").status_code == 404


def test_list_filters():
    a = create(channel="linkedin", scheduled_at="2026-10-01T09:00:00Z")
    b = create(channel="x", scheduled_at="2026-10-05T09:00:00Z", status="in_review")
    c = create(channel="x")  # unscheduled
    ids = lambda r: [i["id"] for i in r.json()]
    assert ids(client.get("/items")) == [a["id"], b["id"], c["id"]]  # unscheduled last
    assert ids(client.get("/items?channel=x")) == [b["id"], c["id"]]
    assert ids(client.get("/items?status=in_review")) == [b["id"]]
    assert ids(client.get("/items?status=draft,in_review")) == [a["id"], b["id"], c["id"]]
    assert ids(client.get("/items?from=2026-10-02")) == [b["id"]]
    assert ids(client.get("/items?to=2026-10-01")) == [a["id"]]  # bare date = whole day
    assert ids(client.get("/items?from=2026-10-01T09:00:00Z&to=2026-10-05T09:00:00Z")) == [a["id"], b["id"]]


def test_list_rejects_unknown_status_and_bad_dates():
    assert client.get("/items?status=approved,nope").status_code == 422
    assert client.get("/items?from=yesterday").status_code == 422


# ---------- transitions


VALID = [(src, dst) for src, dsts in TRANSITIONS.items() for dst in dsts]
STATES = list(TRANSITIONS)
INVALID = [(s, d) for s in STATES for d in STATES if d not in TRANSITIONS[s]]


@pytest.mark.parametrize("src,dst", VALID)
def test_every_valid_transition(src, dst):
    item = item_in(src)
    r = move(item["id"], dst)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == dst


@pytest.mark.parametrize("src,dst", INVALID)
def test_every_invalid_transition_is_409_with_allowed_states(src, dst):
    item = item_in(src)
    r = move(item["id"], dst)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["current"] == src
    assert detail["allowed"] == TRANSITIONS[src]
    assert client.get(f"/items/{item['id']}").json()["status"] == src  # unchanged


def test_unknown_target_status_422():
    assert move(create()["id"], "shipped").status_code == 422


def test_status_on_missing_item_404():
    assert move(999, "in_review").status_code == 404


def test_note_appends_timestamped_lines():
    item = create()
    move(item["id"], "in_review", note="ready for Sam")
    notes = move(item["id"], "draft", note="tone too salesy").json()["notes"]
    lines = notes.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("[20") and "Z] draft -> in_review: ready for Sam" in lines[0]
    assert lines[1].endswith("in_review -> draft: tone too salesy")


def test_note_appends_to_existing_notes():
    item = create(notes="brief from marketing")
    notes = move(item["id"], "in_review", note="go").json()["notes"]
    assert notes.startswith("brief from marketing\n[")


def test_status_to_published_sets_published_at():
    item = item_in("approved")
    assert move(item["id"], "published").json()["published_at"].endswith("Z")


# ---------- patch


def test_patch_edits_allowed_fields():
    item = create(scheduled_at="2026-10-01T09:00:00Z")
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={
        "title": "New title", "body": "New body", "scheduled_at": "2026-10-02T10:00:00+01:00",
        "campaign": "launch", "link": "https://example.com", "notes": "n", "channel": "x",
    })
    assert r.status_code == 200
    got = r.json()
    assert got["title"] == "New title" and got["channel"] == "x"
    assert got["scheduled_at"] == "2026-10-02T09:00:00Z"
    assert got["campaign"] == "launch" and got["status"] == "draft"


def test_patch_can_clear_schedule():
    item = create(scheduled_at="2026-10-01T09:00:00Z")
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"scheduled_at": None})
    assert r.json()["scheduled_at"] is None


def test_patch_cannot_change_status():
    item = create()
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"status": "approved"})
    assert r.status_code == 422
    assert client.get(f"/items/{item['id']}").json()["status"] == "draft"


def test_patch_cannot_blank_title():
    r = client.patch(f"/items/{create()['id']}", headers=AUTH, json={"title": ""})
    assert r.status_code == 422


def test_patch_content_of_approved_item_is_409_but_reschedule_is_ok():
    item = item_in("approved")
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"body": "sneaky edit"})
    assert r.status_code == 409
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"scheduled_at": "2026-11-01T08:00:00Z"})
    assert r.status_code == 200 and r.json()["scheduled_at"] == "2026-11-01T08:00:00Z"


def test_patch_missing_item_404():
    assert client.patch("/items/999", headers=AUTH, json={"title": "x"}).status_code == 404


# ---------- published


def test_mark_published():
    item = item_in("approved")
    r = client.post(f"/items/{item['id']}/published", headers=AUTH,
                    json={"external_url": "https://linkedin.com/p/1"})
    assert r.status_code == 200
    got = r.json()
    assert got["status"] == "published"
    assert got["external_url"] == "https://linkedin.com/p/1"
    assert got["published_at"].endswith("Z")


def test_mark_published_without_body():
    item = item_in("approved")
    r = client.post(f"/items/{item['id']}/published", headers=AUTH)
    assert r.status_code == 200 and r.json()["external_url"] is None


@pytest.mark.parametrize("status", ["idea", "draft", "in_review", "rejected", "published"])
def test_mark_published_requires_approved(status):
    item = item_in(status)
    r = client.post(f"/items/{item['id']}/published", headers=AUTH, json={})
    assert r.status_code == 409
    assert r.json()["detail"]["current"] == status


# ---------- due


def test_due_boundary_and_order():
    later = item_in("approved", scheduled_at="2026-10-01T09:00:00Z")
    exact = item_in("approved", scheduled_at="2026-10-01T08:00:00+00:00")
    earlier = item_in("approved", scheduled_at="2026-10-01T07:59:59Z")
    item_in("in_review", scheduled_at="2026-09-01T00:00:00Z")  # not approved
    item_in("approved")  # approved but unscheduled
    ids = lambda r: [i["id"] for i in r.json()]
    assert ids(client.get("/due?now=2026-10-01T08:00:00Z")) == [earlier["id"], exact["id"]]
    assert ids(client.get("/due?now=2026-10-01T07:59:58Z")) == []
    # naive and offset "now" are handled as UTC instants; an unencoded "+" is tolerated
    assert ids(client.get("/due", params={"now": "2026-10-01T10:00:00+02:00"})) == [earlier["id"], exact["id"]]
    assert ids(client.get("/due?now=2026-10-01T10:00:00+02:00")) == [earlier["id"], exact["id"]]
    assert ids(client.get("/due?now=2026-10-01T09:00:00")) == [earlier["id"], exact["id"], later["id"]]


def test_due_defaults_to_now():
    past = item_in("approved", scheduled_at="2020-01-01T00:00:00Z")
    item_in("approved", scheduled_at="2999-01-01T00:00:00Z")
    assert [i["id"] for i in client.get("/due").json()] == [past["id"]]


def test_due_excludes_published():
    item = item_in("published", scheduled_at="2020-01-01T00:00:00Z")
    assert item["status"] == "published"
    assert client.get("/due").json() == []


def test_due_bad_now_422():
    assert client.get("/due?now=soon").status_code == 422


# ---------- auth


WRITES = [
    ("post", "/items", {"title": "t", "channel": "x", "body": "b"}),
    ("patch", "/items/1", {"title": "t"}),
    ("post", "/items/1/status", {"status": "in_review"}),
    ("post", "/items/1/published", {}),
]


@pytest.mark.parametrize("method,path,body", WRITES)
def test_writes_need_api_key(method, path, body):
    create()
    assert getattr(client, method)(path, json=body).status_code == 401
    assert getattr(client, method)(path, json=body, headers={"X-API-Key": "wrong"}).status_code == 401


@pytest.mark.parametrize("method,path,body", WRITES)
def test_writes_503_when_key_not_configured(method, path, body, monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert getattr(client, method)(path, json=body, headers=AUTH).status_code == 503


def test_reads_need_no_key():
    create()
    assert client.get("/items").status_code == 200
    assert client.get("/items/1").status_code == 200
    assert client.get("/due").status_code == 200


# ---------- phase 2: campaign_id, short_url, migration


def test_create_with_campaign_id_and_filter():
    a = create(campaign_id=7, channel="linkedin")
    b = create(campaign_id=7, channel="x", status="in_review")
    create(campaign_id=8, channel="x")
    create(channel="x")  # no campaign
    assert a["campaign_id"] == 7
    ids = lambda r: [i["id"] for i in r.json()]
    assert ids(client.get("/items?campaign_id=7")) == [a["id"], b["id"]]
    assert ids(client.get("/items?campaign_id=7&channel=x")) == [b["id"]]
    assert ids(client.get("/items?campaign_id=7&status=draft")) == [a["id"]]
    assert ids(client.get("/items?campaign_id=7&status=in_review&channel=x")) == [b["id"]]
    assert client.get("/items?campaign_id=999").json() == []
    assert len(client.get("/items").json()) == 4  # no filter: unchanged


def test_list_campaign_id_must_be_int():
    assert client.get("/items?campaign_id=launch").status_code == 422


@pytest.mark.parametrize("bad", ["7", "launch", 1.5, 0, -3, True])
def test_create_rejects_non_int_campaign_id(bad):
    r = client.post("/items", json={"title": "t", "channel": "x", "body": "b", "campaign_id": bad},
                    headers=AUTH)
    assert r.status_code == 422


def test_create_accepts_null_campaign_id():
    assert create(campaign_id=None)["campaign_id"] is None


def test_patch_sets_and_clears_campaign_id():
    item = create()
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"campaign_id": 3})
    assert r.status_code == 200 and r.json()["campaign_id"] == 3
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"campaign_id": None})
    assert r.status_code == 200 and r.json()["campaign_id"] is None


def test_patch_rejects_bad_campaign_id():
    item = create()
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"campaign_id": "3"})
    assert r.status_code == 422


def test_patch_campaign_id_allowed_on_approved_item():
    item = item_in("approved")
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"campaign_id": 4})
    assert r.status_code == 200 and r.json()["campaign_id"] == 4


def test_patch_cannot_set_short_url():
    item = create()
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"short_url": "https://s/x"})
    assert r.status_code == 422


@pytest.mark.parametrize("status", ["idea", "draft", "in_review"])
def test_patch_body_allowed_before_approval(status):
    item = item_in(status)
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"body": "revised body"})
    assert r.status_code == 200, r.text
    assert r.json()["body"] == "revised body" and r.json()["status"] == status


@pytest.mark.parametrize("status", ["approved", "published"])
@pytest.mark.parametrize("field", ["title", "channel", "body", "link"])
def test_patch_content_locked_after_approval(status, field):
    item = item_in(status)
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={field: "changed"})
    assert r.status_code == 409
    assert client.get(f"/items/{item['id']}").json()[field] == item[field]


def test_mark_published_with_short_url():
    item = item_in("approved")
    r = client.post(f"/items/{item['id']}/published", headers=AUTH,
                    json={"external_url": "https://linkedin.com/p/1", "short_url": "https://s.example/abc"})
    assert r.status_code == 200
    got = r.json()
    assert got["short_url"] == "https://s.example/abc"
    assert got["external_url"] == "https://linkedin.com/p/1"
    assert client.get(f"/items/{item['id']}").json()["short_url"] == "https://s.example/abc"


def test_mark_published_short_url_only():
    item = item_in("approved")
    r = client.post(f"/items/{item['id']}/published", headers=AUTH, json={"short_url": "https://s/x"})
    assert r.status_code == 200
    assert r.json()["short_url"] == "https://s/x" and r.json()["external_url"] is None


def test_mark_published_short_url_still_requires_approved():
    item = item_in("draft")
    r = client.post(f"/items/{item['id']}/published", headers=AUTH, json={"short_url": "https://s/x"})
    assert r.status_code == 409
    assert client.get(f"/items/{item['id']}").json()["short_url"] is None


OLD_SCHEMA = """
CREATE TABLE items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    channel TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    scheduled_at TEXT,
    published_at TEXT,
    campaign TEXT,
    link TEXT,
    external_url TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX items_status_scheduled ON items (status, scheduled_at);
"""


def test_migrates_old_schema_db(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO items (title, channel, body, status, scheduled_at, campaign, created_at, updated_at)"
        " VALUES ('Old post', 'linkedin', 'old body', 'approved', '2020-01-01T00:00:00Z', 'legacy',"
        " '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("DB_PATH", str(path))

    old = client.get("/items/1").json()
    assert old["title"] == "Old post" and old["campaign"] == "legacy"
    assert old["campaign_id"] is None and old["short_url"] is None
    assert [i["id"] for i in client.get("/due").json()] == [1]
    assert client.get("/items?campaign_id=1").json() == []

    # old rows keep working through the whole flow
    r = client.patch("/items/1", headers=AUTH, json={"campaign_id": 5})
    assert r.status_code == 200 and r.json()["campaign_id"] == 5
    r = client.post("/items/1/published", headers=AUTH, json={"short_url": "https://s/old"})
    assert r.status_code == 200 and r.json()["short_url"] == "https://s/old"
    new = create(campaign_id=5)
    assert [i["id"] for i in client.get("/items?campaign_id=5").json()] == [1, new["id"]]

    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(items)")}
    assert {"campaign_id", "short_url", "hook_style"} <= cols
    assert old["hook_style"] is None


def test_migration_is_idempotent(tmp_path, monkeypatch):
    from app import main
    path = tmp_path / "old2.sqlite"
    sqlite3.connect(path).executescript(OLD_SCHEMA)
    monkeypatch.setenv("DB_PATH", str(path))
    create(campaign_id=1)
    main._migrated.discard(str(path))  # simulate a restart
    assert client.get("/items?campaign_id=1").json()[0]["campaign_id"] == 1


def test_migration_is_safe_under_parallel_requests(tmp_path, monkeypatch):
    # regression: two first requests raced to migrate an old database; one crashed
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from app import main as m
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.close()
    monkeypatch.setenv("DB_PATH", str(path))
    m._migrated.clear()
    def hit(_):
        with m.db() as c:
            return c.execute("SELECT 1").fetchone()[0]
    with ThreadPoolExecutor(8) as pool:
        assert list(pool.map(hit, range(16))) == [1] * 16


def test_huge_or_negative_id_is_404_not_500():
    # A hosted model once sent its tool-call id; the digits made an id beyond SQLite's 64 bits.
    for bad in ("76706164840234535352", "-1", "0"):
        assert client.get(f"/items/{bad}").status_code == 404
        assert client.post(f"/items/{bad}/status", json={"status": "draft"}, headers=AUTH).status_code == 404


# ---------- hook_style (which opening a post uses; 45 /insights/hooks learns from it)


def test_create_with_hook_style_and_filter():
    a = create(hook_style="question", channel="x")
    b = create(hook_style=" Fact_Led ", channel="linkedin")
    c = create()
    assert a["hook_style"] == "question" and b["hook_style"] == "fact_led"
    assert c["hook_style"] is None
    assert client.get(f"/items/{a['id']}").json()["hook_style"] == "question"
    ids = lambda r: [i["id"] for i in r.json()]
    assert ids(client.get("/items?hook_style=question")) == [a["id"]]
    assert ids(client.get("/items?hook_style=fact_led&channel=linkedin")) == [b["id"]]
    assert ids(client.get("/items?hook_style=fact_led&channel=x")) == []
    assert ids(client.get("/items?hook_style=story")) == []
    assert len(client.get("/items").json()) == 3


def test_blank_hook_style_is_null():
    assert create(hook_style="  ")["hook_style"] is None


def test_hook_style_too_long_422():
    r = client.post("/items", headers=AUTH,
                    json={"title": "t", "channel": "x", "body": "b", "hook_style": "q" * 41})
    assert r.status_code == 422


@pytest.mark.parametrize("status", ["idea", "draft", "in_review"])
def test_patch_hook_style_before_approval(status):
    item = item_in(status)
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"hook_style": "story"})
    assert r.status_code == 200 and r.json()["hook_style"] == "story"
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"hook_style": None})
    assert r.status_code == 200 and r.json()["hook_style"] is None


@pytest.mark.parametrize("status", ["approved", "published"])
def test_patch_hook_style_frozen_after_approval(status):
    item = item_in(status, hook_style="benefit")
    r = client.patch(f"/items/{item['id']}", headers=AUTH, json={"hook_style": "story"})
    assert r.status_code == 409
    assert client.get(f"/items/{item['id']}").json()["hook_style"] == "benefit"


def test_migration_adds_hook_style_to_db_that_already_has_campaign_id(tmp_path, monkeypatch):
    # a database from the phase-2 release has campaign_id/short_url but no hook_style
    path = tmp_path / "phase2.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute("ALTER TABLE items ADD COLUMN campaign_id INTEGER")
    conn.execute("ALTER TABLE items ADD COLUMN short_url TEXT")
    conn.close()
    monkeypatch.setenv("DB_PATH", str(path))
    item = create(hook_style="how_to", campaign_id=2)
    assert client.get(f"/items/{item['id']}").json()["hook_style"] == "how_to"
    assert [i["id"] for i in client.get("/items?hook_style=how_to").json()] == [item["id"]]


def test_approving_and_publishing_need_the_approver_key(monkeypatch):
    monkeypatch.setenv("APPROVER_KEY", "boss")
    item = create(status="in_review")
    assert move(item["id"], "approved").status_code == 403                      # internal key only
    ok = client.post(f"/items/{item['id']}/status", json={"status": "approved"},
                     headers={**AUTH, "X-Approver-Key": "boss"})
    assert ok.status_code == 200
    assert client.post(f"/items/{item['id']}/published", json={}, headers=AUTH).status_code == 403
    assert client.post(f"/items/{item['id']}/published", json={},
                       headers={**AUTH, "X-Approver-Key": "boss"}).status_code == 200
    other = create(status="in_review")
    assert move(other["id"], "rejected").status_code == 200                     # other moves: no approver key
