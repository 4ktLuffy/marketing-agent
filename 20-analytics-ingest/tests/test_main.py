import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

KEY = "test-key"
AUTH = {"X-API-Key": KEY, "content-type": "text/csv"}
EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "analytics.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)


client = TestClient(app)


def upload(text, source="generic", headers=AUTH):
    return client.post(f"/upload?source={source}", content=text.encode(), headers=headers)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


# ---------- upload


def test_generic_upload_case_insensitive_headers_and_defaults():
    csv = "Date,CHANNEL,Clicks,Sessions\n2026-09-01,email,10,8\n2026-09-01,cpc,,5\n"
    r = upload(csv)
    assert r.status_code == 200 and r.json() == {"rows_imported": 2}
    k = client.get("/kpis?from=2026-09-01&to=2026-09-01").json()
    assert k["totals"]["clicks"] == 10 and k["totals"]["sessions"] == 13
    assert k["totals"]["impressions"] == 0 and k["totals"]["spend"] == 0


def test_sample_files_import():
    assert upload((EXAMPLES / "generic.csv").read_text()).json() == {"rows_imported": 56}
    assert upload((EXAMPLES / "ga4.csv").read_text(), source="ga4").json() == {"rows_imported": 12}


def test_ga4_preset_maps_columns_and_skips_comments():
    csv = (
        "# Traffic acquisition\n# Start date: 20260901\n\n"
        "Date,Session default channel group,Sessions,Engaged sessions,Key events\n"
        '20260901,Organic Search,"1,200",600,7\n'
        "20260902,Direct,300,150,2\n"
    )
    assert upload(csv, "ga4").json() == {"rows_imported": 2}
    k = client.get("/kpis?from=2026-09-01&to=2026-09-02").json()
    assert k["totals"]["sessions"] == 1500 and k["totals"]["conversions"] == 9
    assert {c["channel"] for c in k["by_channel"]} == {"Organic Search", "Direct"}


def test_ga4_alternative_headers():
    csv = "Date,Default channel group,Sessions,Conversions\n2026-09-01,Email,50,5\n"
    assert upload(csv, "ga4").json() == {"rows_imported": 1}
    assert client.get("/kpis?from=2026-09-01&to=2026-09-01").json()["totals"]["cvr"] == 0.1


def test_reupload_replaces_same_date_channel_source():
    upload("date,channel,sessions\n2026-09-01,email,10\n")
    upload("date,channel,sessions\n2026-09-01,email,25\n")
    k = client.get("/kpis?from=2026-09-01&to=2026-09-01").json()
    assert k["totals"]["sessions"] == 25


def test_duplicate_rows_in_one_file_are_summed():
    upload("date,channel,clicks\n2026-09-01,cpc,10\n2026-09-01,cpc,5\n")
    assert client.get("/kpis?from=2026-09-01&to=2026-09-01").json()["totals"]["clicks"] == 15


def test_bad_rows_422_with_row_numbers_and_nothing_imported():
    csv = (
        "date,channel,clicks\n"
        "2026-09-01,email,10\n"
        "09/02/2026,email,3\n"      # line 3: bad date
        "2026-09-03,,4\n"           # line 4: no channel
        "2026-09-04,cpc,lots\n"     # line 5: not a number
        "2026-09-05,cpc,-1\n"       # line 6: negative
    )
    r = upload(csv)
    assert r.status_code == 422
    assert [e["row"] for e in r.json()["detail"]["rows"]] == [3, 4, 5, 6]
    assert client.get("/kpis?from=2026-09-01&to=2026-09-05").json()["totals"]["clicks"] == 0


def test_missing_required_column_422():
    r = upload("day,channel,clicks\n2026-09-01,email,1\n")
    assert r.status_code == 422 and "date" in r.json()["detail"]


def test_empty_and_header_only_uploads_422():
    assert upload("").status_code == 422
    assert upload("date,channel\n").status_code == 422


def test_unknown_source_422():
    assert upload("date,channel\n2026-09-01,x\n", source="adobe").status_code == 422


def test_upload_needs_api_key():
    csv = "date,channel\n2026-09-01,x\n"
    assert upload(csv, headers={"content-type": "text/csv"}).status_code == 401
    assert upload(csv, headers={"X-API-Key": "nope"}).status_code == 401


def test_upload_503_without_configured_key(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert upload("date,channel\n2026-09-01,x\n").status_code == 503


# ---------- KPIs


def seed():
    upload(
        "date,channel,impressions,clicks,sessions,conversions,spend\n"
        # previous period 2026-08-29..2026-08-31
        "2026-08-30,cpc,1000,50,40,4,100\n"
        # current period 2026-09-01..2026-09-03
        "2026-09-01,cpc,2000,100,80,5,150\n"
        "2026-09-02,email,0,20,20,2,0\n"
        "2026-09-03,organic,0,0,50,0,0\n"
    )


def test_kpis_totals_ratios_and_channels():
    seed()
    k = client.get("/kpis?from=2026-09-01&to=2026-09-03&compare=false").json()
    assert k["period"] == {"from": "2026-09-01", "to": "2026-09-03"}
    t = k["totals"]
    assert (t["impressions"], t["clicks"], t["sessions"], t["conversions"], t["spend"]) == (2000, 120, 150, 7, 150)
    assert t["ctr"] == 0.06 and t["cvr"] == round(7 / 150, 4) and t["cpa"] == round(150 / 7, 2)
    assert [c["channel"] for c in k["by_channel"]] == ["cpc", "organic", "email"]  # by sessions
    organic = k["by_channel"][1]
    assert organic["ctr"] is None and organic["cpa"] is None and organic["cvr"] == 0
    email = k["by_channel"][2]
    assert email["ctr"] is None and email["cpa"] == 0
    assert k["previous"] is None and k["delta_pct"] is None


def test_kpis_compare_uses_preceding_period_of_same_length():
    seed()
    k = client.get("/kpis?from=2026-09-01&to=2026-09-03&compare=true").json()
    assert k["previous"]["period"] == {"from": "2026-08-29", "to": "2026-08-31"}
    assert k["previous"]["clicks"] == 50
    d = k["delta_pct"]
    assert d["clicks"] == 140.0  # 50 -> 120
    assert d["spend"] == 50.0
    assert d["cpa"] == round((150 / 7 - 25) / 25 * 100, 1)


def test_delta_null_when_previous_is_zero():
    upload("date,channel,clicks,sessions\n2026-09-01,email,10,0\n")
    k = client.get("/kpis?from=2026-09-01&to=2026-09-01").json()
    assert k["previous"]["clicks"] == 0
    assert all(v is None for v in k["delta_pct"].values())


def test_cvr_falls_back_to_clicks_without_sessions():
    upload("date,channel,clicks,conversions\n2026-09-01,cpc,50,5\n")
    assert client.get("/kpis?from=2026-09-01&to=2026-09-01").json()["totals"]["cvr"] == 0.1


def test_empty_period_has_null_ratios():
    k = client.get("/kpis?from=2026-01-01&to=2026-01-07").json()
    assert k["totals"]["sessions"] == 0 and k["totals"]["ctr"] is None
    assert k["by_channel"] == []


def test_kpis_source_filter():
    upload("date,channel,sessions\n2026-09-01,Email,10\n")
    upload("Date,Default channel group,Sessions\n20260901,Email,99\n", "ga4")
    assert client.get("/kpis?from=2026-09-01&to=2026-09-01").json()["totals"]["sessions"] == 109
    assert client.get("/kpis?from=2026-09-01&to=2026-09-01&source=ga4").json()["totals"]["sessions"] == 99


def test_kpis_bad_dates_422():
    assert client.get("/kpis?from=2026/09/01&to=2026-09-02").status_code == 422
    assert client.get("/kpis?from=2026-09-05&to=2026-09-01").status_code == 422


def test_kpis_default_period_is_last_7_days():
    k = client.get("/kpis").json()
    from datetime import date
    f, t = date.fromisoformat(k["period"]["from"]), date.fromisoformat(k["period"]["to"])
    assert (t - f).days == 6



# ---------- campaigns


def kpis(q):
    r = client.get(f"/kpis?{q}")
    assert r.status_code == 200, r.text
    return r.json()


def test_generic_campaign_column_case_insensitive_and_optional_cells():
    csv = ("date,channel,Campaign,sessions,clicks\n"
           "2026-09-01,email,spring,10,4\n"
           "2026-09-01,email,,5,1\n"          # empty cell -> ""
           "2026-09-01,cpc, autumn ,7,3\n")    # trimmed
    assert upload(csv).json() == {"rows_imported": 3}
    k = kpis("from=2026-09-01&to=2026-09-01&compare=false")
    assert k["totals"]["sessions"] == 22  # all campaigns summed
    assert k["by_campaign"] == [
        {"campaign": "spring", "sessions": 10, "clicks": 4, "conversions": 0, "spend": 0},
        {"campaign": "autumn", "sessions": 7, "clicks": 3, "conversions": 0, "spend": 0},
    ]
    assert kpis("from=2026-09-01&to=2026-09-01&campaign=autumn")["totals"]["sessions"] == 7


def test_campaign_filter_is_exact_and_applies_everywhere():
    upload(
        "date,channel,campaign,clicks,sessions,conversions,spend\n"
        "2026-08-31,cpc,spring,10,8,1,20\n"     # previous period
        "2026-09-01,cpc,spring,30,20,2,40\n"
        "2026-09-01,email,spring,5,5,1,0\n"
        "2026-09-01,cpc,spring-sale,99,99,9,99\n"
        "2026-09-01,cpc,,1,1,0,1\n"
    )
    k = kpis("from=2026-09-01&to=2026-09-01&campaign=spring")
    assert k["totals"]["clicks"] == 35 and k["totals"]["sessions"] == 25
    assert k["totals"]["conversions"] == 3 and k["totals"]["spend"] == 40
    assert {c["channel"]: c["clicks"] for c in k["by_channel"]} == {"cpc": 30, "email": 5}
    assert k["previous"]["clicks"] == 10 and k["delta_pct"]["clicks"] == 250.0
    assert "by_campaign" not in k
    assert kpis("from=2026-09-01&to=2026-09-01&campaign=Spring")["totals"]["clicks"] == 0
    assert kpis("from=2026-09-01&to=2026-09-01&campaign=nope")["by_channel"] == []
    everything = kpis("from=2026-09-01&to=2026-09-01")
    assert everything["totals"]["clicks"] == 135
    assert [c["campaign"] for c in everything["by_campaign"]] == ["spring-sale", "spring"]


def test_empty_campaign_param_means_no_filter():
    upload("date,channel,campaign,sessions\n2026-09-01,email,a,1\n2026-09-01,email,,2\n")
    k = kpis("from=2026-09-01&to=2026-09-01&campaign=")
    assert k["totals"]["sessions"] == 3 and "by_campaign" in k


def test_by_campaign_respects_source_and_period():
    upload("date,channel,campaign,sessions\n2026-09-01,email,a,1\n2026-09-05,email,b,2\n")
    upload("Date,Default channel group,Session campaign,Sessions\n20260901,Email,c,4\n", "ga4")
    assert [c["campaign"] for c in kpis("from=2026-09-01&to=2026-09-01")["by_campaign"]] == ["c", "a"]
    assert [c["campaign"] for c in kpis("from=2026-09-01&to=2026-09-01&source=generic")["by_campaign"]] == ["a"]


def test_by_campaign_empty_list_without_campaigns():
    upload("date,channel,sessions\n2026-09-01,email,1\n")
    assert kpis("from=2026-09-01&to=2026-09-01")["by_campaign"] == []


def test_campaign_is_part_of_the_upsert_key():
    upload("date,channel,campaign,sessions\n2026-09-01,email,a,10\n2026-09-01,email,b,20\n")
    upload("date,channel,campaign,sessions\n2026-09-01,email,a,15\n")  # replaces only a
    k = kpis("from=2026-09-01&to=2026-09-01")
    assert k["totals"]["sessions"] == 35
    upload("date,channel,sessions\n2026-09-01,email,5\n")  # campaign "" is its own row
    assert kpis("from=2026-09-01&to=2026-09-01")["totals"]["sessions"] == 40
    upload("date,channel,sessions\n2026-09-01,email,6\n")  # ...and is replaced by re-upload
    assert kpis("from=2026-09-01&to=2026-09-01")["totals"]["sessions"] == 41


def test_rows_differing_only_by_campaign_are_not_summed():
    csv = "date,channel,campaign,clicks\n2026-09-01,cpc,a,1\n2026-09-01,cpc,b,2\n2026-09-01,cpc,a,3\n"
    assert upload(csv).json() == {"rows_imported": 2}
    assert {c["campaign"]: c["clicks"] for c in kpis("from=2026-09-01&to=2026-09-01")["by_campaign"]} == {"a": 4, "b": 2}


@pytest.mark.parametrize("header", ["Session campaign", "Session manual campaign name"])
def test_ga4_campaign_columns(header):
    csv = f"Date,Session default channel group,{header},Sessions\n20260901,Paid Social,spring,40\n20260901,Direct,(direct),9\n"
    assert upload(csv, "ga4").json() == {"rows_imported": 2}
    assert kpis("from=2026-09-01&to=2026-09-01&campaign=spring")["totals"]["sessions"] == 40


def test_ga4_without_campaign_column_is_blank_campaign():
    upload("Date,Default channel group,Sessions\n20260901,Email,5\n", "ga4")
    k = kpis("from=2026-09-01&to=2026-09-01")
    assert k["totals"]["sessions"] == 5 and k["by_campaign"] == []


def test_ga4_prefers_session_campaign_over_manual_name():
    csv = "Date,Default channel group,Session manual campaign name,Session campaign,Sessions\n20260901,Email,manual,auto,5\n"
    upload(csv, "ga4")
    assert [c["campaign"] for c in kpis("from=2026-09-01&to=2026-09-01")["by_campaign"]] == ["auto"]


def test_bad_row_in_campaign_file_still_rejects_whole_upload():
    r = upload("date,channel,campaign,clicks\n2026-09-01,cpc,a,1\n2026-09-02,,b,2\n")
    assert r.status_code == 422 and r.json()["detail"]["rows"][0]["row"] == 3
    assert kpis("from=2026-09-01&to=2026-09-02")["totals"]["clicks"] == 0


def test_example_generic_has_campaigns_and_same_totals():
    assert upload((EXAMPLES / "generic.csv").read_text()).json() == {"rows_imported": 56}
    k = kpis("from=2026-09-14&to=2026-09-20&source=generic")
    assert k["totals"]["sessions"] == 7804 and k["totals"]["clicks"] == 4952
    camps = {c["campaign"]: c for c in k["by_campaign"]}
    assert set(camps) == {"spring-launch", "brand-search"}
    spring = kpis("from=2026-09-14&to=2026-09-20&campaign=spring-launch")
    assert {c["channel"] for c in spring["by_channel"]} == {"paid_social", "email"}
    assert spring["totals"]["sessions"] == camps["spring-launch"]["sessions"]


# ---------- migration of a database created before campaigns


OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    date TEXT NOT NULL,
    channel TEXT NOT NULL,
    source TEXT NOT NULL,
    impressions INTEGER NOT NULL,
    clicks INTEGER NOT NULL,
    sessions INTEGER NOT NULL,
    conversions REAL NOT NULL,
    spend REAL NOT NULL,
    PRIMARY KEY (date, channel, source)
);
"""


def make_old_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.executemany("INSERT INTO metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
        ("2026-09-01", "email", "generic", 100, 10, 8, 1.5, 0.0),
        ("2026-09-01", "cpc", "generic", 200, 20, 16, 2.0, 12.5),
        ("2026-09-01", "Email", "ga4", 0, 0, 30, 3.0, 0.0),
    ])
    conn.commit()
    conn.close()


def test_migrates_old_schema_and_keeps_data(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite"
    make_old_db(path)
    monkeypatch.setenv("DB_PATH", str(path))

    k = kpis("from=2026-09-01&to=2026-09-01&compare=false")
    t = k["totals"]
    assert (t["impressions"], t["clicks"], t["sessions"], t["conversions"], t["spend"]) == (300, 30, 54, 6.5, 12.5)
    assert k["by_campaign"] == []  # old rows have campaign ""

    conn = sqlite3.connect(path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(metrics)")]
    pk = [r[1] for r in sorted(conn.execute("PRAGMA table_info(metrics)"), key=lambda r: r[5]) if r[5]]
    assert "campaign" in cols and pk == ["date", "channel", "campaign", "source"]
    assert conn.execute("SELECT COUNT(*) FROM metrics WHERE campaign = ''").fetchone()[0] == 3
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'metrics_new'").fetchone() is None
    conn.close()

    # old key still upserts (campaign ""), new campaign rows sit beside it
    upload("date,channel,sessions\n2026-09-01,email,9\n")
    upload("date,channel,campaign,sessions\n2026-09-01,email,spring,4\n")
    k = kpis("from=2026-09-01&to=2026-09-01")
    assert k["totals"]["sessions"] == 9 + 16 + 30 + 4
    assert k["by_campaign"] == [{"campaign": "spring", "sessions": 4, "clicks": 0, "conversions": 0, "spend": 0}]


def test_migration_runs_once_and_is_idempotent(tmp_path, monkeypatch):
    from app import main
    path = tmp_path / "old2.sqlite"
    make_old_db(path)
    monkeypatch.setenv("DB_PATH", str(path))
    assert kpis("from=2026-09-01&to=2026-09-01")["totals"]["sessions"] == 54
    main._migrated.discard(str(path))  # simulate a restart on the migrated file
    assert kpis("from=2026-09-01&to=2026-09-01")["totals"]["sessions"] == 54


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


# ---------- label: store rows under another source name (e.g. synced from Umami)


def test_label_stores_rows_under_label_and_keeps_manual_rows():
    csv = "date,channel,campaign,sessions\n2026-09-01,email,spring,10\n"
    assert upload(csv).json() == {"rows_imported": 1}
    r = client.post("/upload?source=generic&label=umami",
                    content="date,channel,campaign,sessions\n2026-09-01,email,spring,7\n".encode(),
                    headers=AUTH)
    assert r.status_code == 200 and r.json() == {"rows_imported": 1}
    # same (date, channel, campaign) but a different source: the manual row is not replaced
    assert kpis("from=2026-09-01&to=2026-09-01")["totals"]["sessions"] == 17
    assert kpis("from=2026-09-01&to=2026-09-01&source=generic")["totals"]["sessions"] == 10
    k = kpis("from=2026-09-01&to=2026-09-01&source=umami")
    assert k["totals"]["sessions"] == 7
    assert k["by_campaign"] == [{"campaign": "spring", "sessions": 7, "clicks": 0, "conversions": 0, "spend": 0}]
    assert kpis("from=2026-09-01&to=2026-09-01&source=umami&campaign=spring")["totals"]["sessions"] == 7


def test_label_reupload_replaces_only_labelled_rows():
    post = lambda q, body: client.post(f"/upload?{q}", content=body.encode(), headers=AUTH)
    post("source=generic&label=umami", "date,channel,sessions\n2026-09-01,email,7\n")
    post("source=generic&label=umami", "date,channel,sessions\n2026-09-01,email,9\n")
    post("source=generic", "date,channel,sessions\n2026-09-01,email,3\n")
    assert kpis("from=2026-09-01&to=2026-09-01&source=umami")["totals"]["sessions"] == 9
    assert kpis("from=2026-09-01&to=2026-09-01")["totals"]["sessions"] == 12


def test_label_keeps_preset_chosen_by_source():
    csv = "Date,Default channel group,Sessions\n20260901,Email,5\n"  # GA4 headers
    r = client.post("/upload?source=ga4&label=ga4-shop", content=csv.encode(), headers=AUTH)
    assert r.status_code == 200
    assert kpis("from=2026-09-01&to=2026-09-01&source=ga4-shop")["totals"]["sessions"] == 5
    assert kpis("from=2026-09-01&to=2026-09-01&source=ga4")["totals"]["sessions"] == 0


@pytest.mark.parametrize("label", ["", "Umami", "um ami", "a" * 33, "um.ami", "umami%2F.."])
def test_bad_label_422_and_nothing_stored(label):
    r = client.post(f"/upload?source=generic&label={label}",
                    content=b"date,channel,sessions\n2026-09-01,email,5\n", headers=AUTH)
    assert r.status_code == 422
    assert kpis("from=2026-09-01&to=2026-09-01")["totals"]["sessions"] == 0


def test_label_upload_still_needs_api_key():
    r = client.post("/upload?source=generic&label=umami",
                    content=b"date,channel\n2026-09-01,x\n", headers={"content-type": "text/csv"})
    assert r.status_code == 401


@pytest.mark.parametrize("source", ["Umami", "a b", "x" * 33])
def test_kpis_bad_source_422(source):
    assert client.get(f"/kpis?from=2026-09-01&to=2026-09-01&source={source}").status_code == 422


def test_kpis_unknown_label_is_empty():
    upload("date,channel,sessions\n2026-09-01,email,5\n")
    assert kpis("from=2026-09-01&to=2026-09-01&source=nothing-here")["totals"]["sessions"] == 0
