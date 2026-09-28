"""Performance page: the paid-ads panel from 84 ads-sync (read-only summary)."""
import httpx
import pytest

from .conftest import KEYS, PASSWORD
from .test_pages import mock_all

ADS = "http://ads.internal:8000"
SUMMARY = {"has_data": True,
           "window": {"current": {"start": "2026-09-21", "end": "2026-09-27"}, "previous": {"start": "2026-09-14", "end": "2026-09-20"}},
           "platforms": [{"platform": "meta", "currency": "EUR",
                          "current": {"spend": 770.0, "conversions": 25, "cpl": 30.8, "roas": 1.82, "ctr": 0.014, "cpc": 1.57},
                          "delta_pct": {"spend": 10.0}}],
           "total": [{"currency": "EUR", "current": {"spend": 1050.0, "conversions": 39, "cpl": 26.92, "roas": 2.33, "ctr": 0.0226, "cpc": 1.4},
                      "delta_pct": {"spend": 7.1}}],
           "campaigns": [{"key": "autumn-launch", "platforms": ["google", "meta"], "currency": "EUR",
                          "current": {"spend": 910.0, "conversions": 35, "cpl": 26.0, "roas": 2.69}}],
           "alerts": [{"rule": "spend_no_conversions", "severity": "high", "message": "meta:23850002: 60.00 spent over the last 3 days with 0 conversions."}],
           "facts": [], "unmapped_spend": {}}


@pytest.fixture
def with_ads(monkeypatch):
    monkeypatch.setenv("ADS_URL", ADS)


def test_ads_panel_renders(with_ads, authed, mock):
    c, _ = authed
    mock_all(mock)
    route = mock.get(f"{ADS}/summary").respond(json=SUMMARY)
    html = c.get("/performance?days=7").text
    assert route.calls[0].request.url.params["days"] == "7"
    assert "Paid ads" in html and "770.00 EUR" in html and "+10.0%" in html and "30.8" in html
    assert "Total EUR" in html and "autumn-launch" in html and "google, meta" in html
    assert "spend no conversions" in html and "1.40%" in html
    assert ADS not in html
    for v in list(KEYS.values()) + [PASSWORD]:
        assert v not in html


def test_ads_panel_no_data_and_down(with_ads, authed, mock):
    c, _ = authed
    mock_all(mock)
    mock.get(f"{ADS}/summary").respond(json={"has_data": False})
    assert "No ad data in this period yet" in c.get("/performance").text
    mock.get(f"{ADS}/summary").mock(side_effect=httpx.ConnectError("down"))
    r = c.get("/performance")
    assert r.status_code == 200 and "ads: unreachable" in r.text and "ads-sync did not answer" in r.text


def test_ads_panel_not_installed(authed, mock):
    c, _ = authed
    mock_all(mock)
    html = c.get("/performance").text
    assert "Not installed" in html and "ADS_URL" in html
    assert not any("/summary" in str(call.request.url) for call in mock.calls)
