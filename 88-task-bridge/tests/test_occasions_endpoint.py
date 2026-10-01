from .conftest import AUTH


def test_off_by_default(client, monkeypatch):
    monkeypatch.delenv("OCCASIONS", raising=False)
    out = client.get("/occasions", headers=AUTH).json()
    assert out["occasions"] == [] and "OCCASIONS" in out["note"]


def test_ethiopia_occasions_near_a_date(client, monkeypatch):
    monkeypatch.setenv("OCCASIONS", "ethiopia")
    out = client.get("/occasions", params={"on": "2026-12-20", "days": 45}, headers=AUTH).json()
    names = [o["name"] for o in out["occasions"]]
    assert out["on_ec"].endswith("2019 E.C.") and any("Genna" in n for n in names) and any("Timket" in n for n in names)
    assert all(o["date"] >= "2026-12-17" for o in out["occasions"] if o["kind"] == "public_holiday")
    assert "6 hours" in out["note"]


def test_needs_key(client, monkeypatch):
    monkeypatch.setenv("OCCASIONS", "ethiopia")
    assert client.get("/occasions").status_code == 401


def test_pack_has_calendar_when_on(client, stack, monkeypatch):
    from .conftest import BIKE_TASK, make_task
    monkeypatch.setenv("OCCASIONS", "ethiopia")
    t = make_task(client, stack, task={**BIKE_TASK, "publish_on": "2027-01-05"})
    assert "CALENDAR:" in t["pack"] and "E.C." in t["pack"] and "Genna" in t["pack"] and "24-hour" in t["pack"]


def test_pack_unchanged_when_off(client, stack, monkeypatch):
    from .conftest import make_task
    monkeypatch.delenv("OCCASIONS", raising=False)
    assert "CALENDAR:" not in make_task(client, stack)["pack"]
