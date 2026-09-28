import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import net, safe_http
from app.main import app

client = TestClient(app)
KEY = {"X-API-Key": "test-key"}

V1 = """<html><head><title>Pricing</title><script>var t = 1;</script></head><body>
<nav>Home Pricing</nav><h1>Pricing</h1><p>Starter   $10 per month</p><p>Pro $30 per month</p>
<footer>(c) 2026</footer></body></html>"""
V2 = V1.replace("Pro $30", "Pro $25").replace("(c) 2026", "(c) 2027")


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "monitor.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["10.0.0.9"] if host == "internal.test" else ["93.184.215.14"])


def add(url="https://example.com/pricing", label="Acme pricing"):
    return client.post("/watches", json={"url": url, "label": label}, headers=KEY)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_watch_crud():
    r = add()
    assert r.status_code == 201
    watch = r.json()
    assert set(watch) == {
        "id", "url", "label", "created_at", "last_checked_at",
        "css", "xpath", "include_filters", "ignore_patterns", "trigger_text", "tag",
    }
    assert watch["last_checked_at"] is None
    assert watch["css"] is None and watch["include_filters"] == [] and watch["tag"] is None
    assert client.get("/watches").json() == [watch]
    assert client.delete(f"/watches/{watch['id']}", headers=KEY).status_code == 204
    assert client.get("/watches").json() == []
    assert client.delete(f"/watches/{watch['id']}", headers=KEY).status_code == 404


def test_writes_need_key():
    assert client.post("/watches", json={"url": "https://example.com"}).status_code == 401
    assert client.post("/watches", json={"url": "https://example.com"}, headers={"X-API-Key": "nope"}).status_code == 401
    assert client.delete("/watches/1").status_code == 401


def test_writes_disabled_without_server_key(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    r = add()
    assert r.status_code == 503
    assert "INTERNAL_API_KEY" in r.json()["detail"]


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://internal.test/", "file:///etc/passwd"])
def test_add_watch_ssrf_guard(url):
    assert add(url).status_code == 422


@respx.mock
def test_check_baseline_then_change():
    route = respx.get("https://example.com/pricing").mock(return_value=httpx.Response(200, html=V1))
    wid = add().json()["id"]

    first = client.post("/check", headers={"X-API-Key": "test-key"}).json()
    assert first == {"changed": [], "unchanged": 1, "errors": []}
    assert client.get("/watches").json()[0]["last_checked_at"] is not None

    assert client.post("/check", headers={"X-API-Key": "test-key"}).json()["unchanged"] == 1  # same content again

    route.mock(return_value=httpx.Response(200, html=V2))
    third = client.post("/check", headers={"X-API-Key": "test-key"}).json()
    assert third["unchanged"] == 0
    [change] = third["changed"]
    assert change["id"] == wid and change["label"] == "Acme pricing"
    assert "-Pro $30 per month" in change["diff"] and "+Pro $25 per month" in change["diff"]
    assert "2027" not in change["diff"]  # footer is ignored
    assert change["added_words"] == 1 and change["removed_words"] == 1

    assert client.post("/check", headers={"X-API-Key": "test-key"}).json()["changed"] == []  # new snapshot stored


@respx.mock
def test_check_one_bad_watch_does_not_fail_others():
    respx.get("https://example.com/pricing").mock(return_value=httpx.Response(200, html=V1))
    respx.get("https://down.example.com/").mock(side_effect=httpx.ConnectError("refused"))
    add()
    bad = add("https://down.example.com/", None).json()
    r = client.post("/check", headers={"X-API-Key": "test-key"}).json()
    assert r["unchanged"] == 1
    assert r["errors"] == [{"id": bad["id"], "url": "https://down.example.com/", "error": "ConnectError: refused"}]


@respx.mock
def test_check_blocks_redirect_to_private():
    respx.get("https://example.com/pricing").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    )
    add()
    [err] = client.post("/check", headers={"X-API-Key": "test-key"}).json()["errors"]
    assert "non-public" in err["error"]


@respx.mock
def test_diff_is_capped():
    route = respx.get("https://example.com/pricing").mock(
        return_value=httpx.Response(200, html="".join(f"<p>line {i}</p>" for i in range(2000)))
    )
    add()
    client.post("/check", headers={"X-API-Key": "test-key"})
    route.mock(return_value=httpx.Response(200, html="".join(f"<p>row {i}</p>" for i in range(2000))))
    [change] = client.post("/check", headers={"X-API-Key": "test-key"}).json()["changed"]
    assert len(change["diff"]) < 4100
    assert change["diff"].endswith("(diff truncated)")


def test_ipv6_forms_embedding_private_ipv4_are_blocked():
    from app.net import _is_public
    for addr in ("64:ff9b::7f00:1", "2002:7f00:1::", "::127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::a9fe:a9fe"):
        assert not _is_public(addr), addr
    assert _is_public("64:ff9b::808:808")      # NAT64 of a public address (8.8.8.8) is fine


def test_check_requires_key():
    assert client.post("/check").status_code == 401


# --- per-watch selectors and filters ---------------------------------------------------------

import sqlite3  # noqa: E402

URL = "https://example.com/pricing"


def check_now():
    return client.post("/check", headers=KEY).json()


def add_with(**fields):
    r = client.post("/watches", json={"url": URL, "label": "Acme", **fields}, headers=KEY)
    assert r.status_code == 201, r.text
    return r.json()


def serve(route, html):
    route.mock(return_value=httpx.Response(200, html=html))


def pricing_page(pro="$30", left=3, updated="2026-09-01"):
    return f"""<html><head><title>Acme pricing</title><style>.x{{}}</style></head><body>
<header><a href="/">Acme</a> <span>Sign in</span></header>
<nav><a>Home</a> <a>Product</a> <a>Pricing</a></nav>
<main>
  <h1>Simple pricing</h1>
  <p class="urgency">Only {left} left!</p>
  <div id="plans">
    <div class="plan"><h2>Starter</h2><p class="price">$10 / month</p><script>track()</script></div>
    <div class="plan"><h2>Pro</h2><p class="price">Pro {pro} / month</p><p>Only {left} left!</p></div>
    <div class="plan"><h2>Enterprise</h2><p class="price">Contact us</p></div>
    <p class="meta">Last updated: {updated}</p>
  </div>
</main>
<footer>(c) 2026 Acme Inc.</footer>
</body></html>"""


@respx.mock
def test_pricing_page_css_and_ignore_patterns():
    route = respx.get(URL)
    serve(route, pricing_page())
    wid = add_with(css="#plans", ignore_patterns=[r"only \d+ left", "last updated"])["id"]
    assert check_now() == {"changed": [], "unchanged": 1, "errors": []}  # baseline

    serve(route, pricing_page(left=2, updated="2026-09-02"))  # noise only
    assert check_now() == {"changed": [], "unchanged": 1, "errors": []}

    serve(route, pricing_page(pro="$25", left=1, updated="2026-09-03"))
    r = check_now()
    [change] = r["changed"]
    assert change["id"] == wid and "trigger" not in change
    assert "-Pro $30 / month" in change["diff"] and "+Pro $25 / month" in change["diff"]
    assert "left" not in change["diff"].lower() and "updated" not in change["diff"].lower()
    assert "track()" not in change["diff"]
    assert change["added_words"] == 1 and change["removed_words"] == 1


@respx.mock
def test_css_selects_only_matched_blocks():
    route = respx.get(URL)
    serve(route, pricing_page())
    add_with(css=".price")
    check_now()
    serve(route, pricing_page(left=2, updated="2026-09-02"))  # outside the selection
    assert check_now()["unchanged"] == 1
    serve(route, pricing_page(pro="$25"))
    [change] = check_now()["changed"]
    assert change["diff"].splitlines()[2:] == [
        "@@ -1,3 +1,3 @@", " $10 / month", "-Pro $30 / month", "+Pro $25 / month", " Contact us",
    ]


@respx.mock
def test_css_keeps_nav_and_footer_inside_selection():
    route = respx.get(URL)
    serve(route, "<body><footer>Plan: Pro $30</footer></body>")
    add_with(css="footer")
    check_now()
    serve(route, "<body><footer>Plan: Pro $25</footer></body>")
    [change] = check_now()["changed"]
    assert "+Plan: Pro $25" in change["diff"]


@respx.mock
def test_xpath_elements_and_text_nodes():
    route = respx.get(URL)
    serve(route, pricing_page())
    add_with(xpath="//p[@class='price']/text()")
    check_now()
    serve(route, pricing_page(pro="$25", left=1))
    [change] = check_now()["changed"]
    assert "-Pro $30 / month" in change["diff"] and "+Pro $25 / month" in change["diff"]
    assert "left" not in change["diff"]


@respx.mock
def test_css_then_xpath_dedupes_elements():
    route = respx.get(URL)
    serve(route, "<body><h1>Title</h1><p id='a'>Alpha</p><p>Beta</p></body>")
    add_with(css="#a", xpath="//p | //h1")
    check_now()
    serve(route, "<body><h1>Title</h1><p id='a'>Alpha</p><p>Gamma</p></body>")
    [change] = check_now()["changed"]
    # lines are Alpha (css), Title, Beta (xpath, document order): #a is not repeated
    assert change["diff"].splitlines()[2:] == ["@@ -2,2 +2,2 @@", " Title", "-Beta", "+Gamma"]
    assert change["added_words"] == 1 and change["removed_words"] == 1


@respx.mock
def test_selector_matching_nothing_is_an_error_and_keeps_snapshot():
    route = respx.get(URL)
    serve(route, pricing_page())
    wid = add_with(css="#plans")["id"]
    check_now()
    serve(route, "<html><body><p>Maintenance</p></body></html>")
    r = check_now()
    assert r["errors"] == [{"id": wid, "url": URL, "error": "css/xpath matched nothing on the page"}]
    serve(route, pricing_page(pro="$25"))  # compared against the snapshot from before the outage
    [change] = check_now()["changed"]
    assert "+Pro $25 / month" in change["diff"]


@respx.mock
def test_include_filters():
    route = respx.get(URL)
    serve(route, pricing_page())
    add_with(include_filters=[r"\$\d+", "contact"])
    check_now()
    serve(route, pricing_page(left=2, updated="2026-09-02"))
    assert check_now()["unchanged"] == 1
    serve(route, pricing_page(pro="$25"))
    [change] = check_now()["changed"]
    assert change["diff"].splitlines()[2:] == [
        "@@ -1,3 +1,3 @@", " $10 / month", "-Pro $30 / month", "+Pro $25 / month", " Contact us",
    ]


@respx.mock
def test_ignore_patterns_whole_page():
    route = respx.get(URL)
    serve(route, pricing_page())
    add_with(ignore_patterns=[r"ONLY \d+ LEFT", r"^last updated:"])  # case-insensitive
    check_now()
    serve(route, pricing_page(left=2, updated="2026-09-02"))
    assert check_now() == {"changed": [], "unchanged": 1, "errors": []}


@respx.mock
def test_trigger_text_appeared_disappeared_and_quiet():
    route = respx.get(URL)
    base = "<body><h1>Pro</h1><p>Pro $30</p><p>Only 3 left</p></body>"
    serve(route, base)
    add_with(trigger_text=r"sold out|discount")
    check_now()

    serve(route, base.replace("Only 3 left", "Only 2 left"))  # other lines only
    assert check_now() == {"changed": [], "unchanged": 1, "errors": []}

    serve(route, base.replace("Only 3 left", "SOLD OUT"))
    [change] = check_now()["changed"]
    assert change["trigger"] == "appeared" and "+SOLD OUT" in change["diff"]

    serve(route, base)
    [change] = check_now()["changed"]
    assert change["trigger"] == "disappeared" and "-SOLD OUT" in change["diff"]

    assert check_now()["changed"] == []


@respx.mock
def test_tag_filter():
    respx.get(URL).mock(return_value=httpx.Response(200, html=V1))
    a = add_with(tag="svc-78")
    add_with(tag="other")
    add_with()
    assert client.get("/watches", params={"tag": "svc-78"}).json() == [a]
    assert len(client.get("/watches").json()) == 3
    assert client.get("/watches", params={"tag": "nope"}).json() == []


def test_new_fields_round_trip():
    w = add_with(css="#plans", xpath="//h1", include_filters=["\\$"], ignore_patterns=["left"],
                 trigger_text="sale", tag="t")
    assert {k: w[k] for k in ("css", "xpath", "include_filters", "ignore_patterns", "trigger_text", "tag")} == {
        "css": "#plans", "xpath": "//h1", "include_filters": ["\\$"], "ignore_patterns": ["left"],
        "trigger_text": "sale", "tag": "t",
    }
    assert client.get("/watches").json() == [w]


@pytest.mark.parametrize("fields, needle", [
    ({"css": "div[["}, "invalid css selector"),
    ({"css": "p::text"}, "invalid css selector"),
    ({"xpath": "//p[@"}, "invalid xpath"),
    ({"xpath": "nosuchfn(//p)"}, "invalid xpath"),
    ({"include_filters": ["(unclosed"]}, "include_filters: invalid regex"),
    ({"ignore_patterns": ["a" * 301]}, "longer than 300"),
    ({"ignore_patterns": ["x"] * 21}, "at most 20"),
    ({"trigger_text": "[z-a]"}, "trigger_text: invalid regex"),
    ({"tag": "t" * 101}, "tag is longer than 100"),
])
def test_validation_422(fields, needle):
    r = client.post("/watches", json={"url": URL, **fields}, headers=KEY)
    assert r.status_code == 422
    assert needle in r.json()["detail"]
    assert client.get("/watches").json() == []


@respx.mock
def test_old_database_is_migrated(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite"
    monkeypatch.setenv("DB_PATH", str(path))
    with closing_conn(path) as conn:
        conn.execute("""CREATE TABLE watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, label TEXT,
            created_at TEXT NOT NULL, last_checked_at TEXT, snapshot TEXT)""")
        conn.execute("INSERT INTO watches (url, label, created_at, snapshot) VALUES (?, ?, ?, ?)",
                     (URL, "Legacy", "2026-09-01T00:00:00+00:00", "Pricing\nStarter $10 per month\nPro $30 per month"))
        conn.commit()
    [w] = client.get("/watches").json()
    assert w["label"] == "Legacy" and w["css"] is None and w["include_filters"] == [] and w["tag"] is None
    respx.get(URL).mock(return_value=httpx.Response(200, html=V2))
    [change] = check_now()["changed"]
    assert "-Pro $30 per month" in change["diff"] and "+Pro $25 per month" in change["diff"]
    assert client.get("/watches", params={"tag": "x"}).json() == []


def closing_conn(path):
    from contextlib import closing
    return closing(sqlite3.connect(path))


def test_selected_lines_string_results_and_encoding():
    from app.main import selected_lines
    html = "<html><body><h1>Café  Pro</h1><p>a</p><p>b</p></body></html>".encode("latin-1")
    assert selected_lines(html, "latin-1", None, "string(//h1)") == ["Café Pro"]
    assert selected_lines(html, "latin-1", None, "count(//p)") == ["2"]
    assert selected_lines(html, "latin-1", "h1", None) == ["Café Pro"]
    assert selected_lines(html, "latin-1", "table", "//table") is None


def test_selected_lines_xml_declaration_with_header_charset():
    from app.main import selected_lines
    html = b'<?xml version="1.0" encoding="utf-8"?><html><body><h1>Pro</h1></body></html>'
    assert selected_lines(html, "utf-8", "h1", None) == ["Pro"]


@respx.mock
def test_snapshots_need_key_and_list_checked_text_by_tag():
    respx.get("https://example.com/pricing").mock(return_value=httpx.Response(200, html=V1))
    assert client.get("/snapshots").status_code == 401
    a = client.post("/watches", json={"url": "https://example.com/pricing", "label": "Acme · pricing",
                                      "tag": "competitor:1"}, headers=KEY).json()
    add()  # untagged
    assert client.get("/snapshots", headers=KEY).json() == []  # nothing checked yet: no text
    client.post("/check", headers=KEY)
    tagged = client.get("/snapshots", params={"tag": "competitor:1"}, headers=KEY).json()
    assert [s["id"] for s in tagged] == [a["id"]]
    assert tagged[0]["text"] == "Pricing\nStarter $10 per month\nPro $30 per month"
    assert tagged[0]["label"] == "Acme · pricing" and tagged[0]["last_checked_at"]
    assert len(client.get("/snapshots", headers=KEY).json()) == 2
    assert client.get("/snapshots", params={"tag": "nope"}, headers=KEY).json() == []
