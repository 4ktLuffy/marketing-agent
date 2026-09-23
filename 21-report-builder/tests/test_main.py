import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

KPIS = {
    "period": {"from": "2026-09-14", "to": "2026-09-20"},
    "totals": {"impressions": 150000, "clicks": 6000, "sessions": 8400, "conversions": 360,
               "spend": 5100.5, "ctr": 0.04, "cvr": 0.0429, "cpa": 14.17},
    "by_channel": [
        {"channel": "cpc", "impressions": 60000, "clicks": 2000, "sessions": 1800,
         "conversions": 100, "spend": 2800, "ctr": 0.0333, "cvr": 0.0556, "cpa": 28},
        {"channel": "organic", "impressions": 0, "clicks": 0, "sessions": 3000,
         "conversions": 90, "spend": 0, "ctr": None, "cvr": 0.03, "cpa": 0},
    ],
    "previous": {"period": {"from": "2026-09-07", "to": "2026-09-13"}, "sessions": 8000},
    "delta_pct": {"impressions": 3.0, "clicks": -2.5, "sessions": 5.0, "conversions": 0.0,
                  "spend": 10.0, "ctr": None, "cvr": 1.2, "cpa": -4.0},
}


def render(**kw):
    payload = {"title": "Weekly report", "period": "14–20 Sep 2026", "kpis": KPIS, **kw}
    r = client.post("/render", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def tile(html, label):
    m = re.search(rf'<div class="label">{label}</div>.*?<div class="delta ([a-z]+)">([^<]*)', html)
    return m.group(1), m.group(2).strip()


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_html_is_self_contained():
    h = render()["html"]
    assert h.startswith("<!DOCTYPE html>")
    assert "<script" not in h and "<link" not in h and "src=" not in h
    assert "<svg" in h and "Weekly report" in h and "14–20 Sep 2026" in h


def test_tiles_formatting_and_delta_direction():
    h = render()["html"]
    assert "8,400" in h and "4.00%" in h and "5,100.50" in h
    assert tile(h, "Sessions") == ("good", "▲ +5.0%")
    assert tile(h, "Clicks") == ("bad", "▼ -2.5%")
    assert tile(h, "Cost per conversion") == ("good", "▼ -4.0%")  # lower cpa is better
    assert tile(h, "Spend") == ("neutral", "▲ +10.0%")
    assert tile(h, "Conversions") == ("flat", "■ +0.0%")
    assert re.search(r'<div class="label">CTR</div>.*?no comparison', h)


def test_channel_table_and_bar_chart():
    h = render()["html"]
    assert "<th>Channel</th>" in h and "<td>organic</td>" in h
    bars = re.findall(r'<rect x="150" y="[\d.]+" width="([\d.]+)"', h)
    assert [float(b) for b in bars] == [380.0, 228.0]  # sorted by sessions, scaled to max
    assert "Sessions by channel" in h


def test_chart_falls_back_to_clicks_without_sessions():
    kpis = {**KPIS, "by_channel": [{"channel": "cpc", "clicks": 10, "sessions": 0}]}
    h = render(kpis=kpis)["html"]
    assert "Clicks by channel" in h and "<rect" in h


def test_user_text_is_escaped():
    kpis = {**KPIS, "by_channel": [{"channel": "<img src=x onerror=alert(1)>", "sessions": 5}]}
    out = render(title="<script>x</script>", period="<b>p</b>", kpis=kpis,
                 highlights_markdown="<script>bad()</script>\n\n[x](javascript:alert(1)) **ok**")
    h = out["html"]
    assert "<script>" not in h and "<img src=x" not in h and "<b>p</b>" not in h
    assert "&lt;script&gt;x&lt;/script&gt;" in h
    assert "javascript:" not in h and "<strong>ok</strong>" in h


def test_markdown_report():
    md = render(highlights_markdown="- Organic grew 12%")["markdown"]
    assert md.startswith("# Weekly report\n")
    assert "| Sessions | 8,400 | ▲ +5.0% |" in md
    assert "| CTR | 4.00% | – |" in md
    assert "## Highlights\n\n- Organic grew 12%" in md
    assert "| cpc | 1,800 | 2,000 | 100 | 3.33% | 5.56% | 2,800.00 | 28.00 |" in md


def test_period_object_and_currency():
    out = render(period={"from": "2026-09-14", "to": "2026-09-20"}, currency="$")
    assert "2026-09-14 to 2026-09-20" in out["html"] and "$5,100.50" in out["html"]
    assert "compared with 2026-09-07 to 2026-09-13" in out["html"]


def test_minimal_kpis_without_compare():
    kpis = {"totals": {"sessions": 0, "ctr": None}, "by_channel": [], "previous": None, "delta_pct": None}
    out = render(kpis=kpis)
    assert "No channel data" in out["html"] and "No channel data" in out["markdown"]


def test_pipe_in_channel_escaped_in_markdown():
    kpis = {**KPIS, "by_channel": [{"channel": "a|b", "sessions": 1}]}
    assert "| a\\|b |" in render(kpis=kpis)["markdown"]


def test_missing_fields_422():
    assert client.post("/render", json={"title": "t", "period": "p"}).status_code == 422
