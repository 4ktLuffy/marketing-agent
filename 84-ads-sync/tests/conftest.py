import pytest
import respx
from fastapi.testclient import TestClient

from app import connectors, main
from app.main import app

from . import fixtures as F

KEY = "internal-secret-key-123"
AUTH = {"X-API-Key": KEY}
SECRETS = {
    "META_ACCESS_TOKEN": "EAAmeta-secret-token-XYZ987",
    "GOOGLE_ADS_DEVELOPER_TOKEN": "devtok-secret-ABC123",
    "GOOGLE_ADS_CLIENT_SECRET": "GOCSPX-client-secret-456",
    "GOOGLE_ADS_REFRESH_TOKEN": "1//refresh-secret-token-789",
}
META_URL = f"https://graph.facebook.com/v26.0/{F.META_ACCOUNT}/insights"
GOOGLE_URL = f"https://googleads.googleapis.com/v25/customers/{F.GOOGLE_CUSTOMER}/googleAds:searchStream"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CAMP = "http://campaigns.test"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for k in list(__import__("os").environ):
        if k.startswith(("META_", "GOOGLE_ADS_", "ALERT_")) or k in ("CAMPAIGNS_URL", "ANALYTICS_URL"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("META_AD_ACCOUNT_IDS", F.META_ACCOUNT)
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_ID", "123-client.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_ADS_CUSTOMER_IDS", "123-456-7890")
    monkeypatch.setenv("CAMPAIGNS_URL", CAMP)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ads.sqlite"))
    monkeypatch.setattr(main, "today", lambda: F.TODAY)
    monkeypatch.setattr(connectors.time, "sleep", lambda s: None)
    connectors.Google._cache.clear()
    yield
    connectors.Google._cache.clear()


@pytest.fixture
def client():
    return TestClient(app)


def meta_route(mock, pages):
    """Serve pages in order; the 2nd request must follow paging.next (after=...)."""
    calls = {"n": 0}

    def handler(request):
        i = calls["n"]
        calls["n"] += 1
        if i:
            assert "after=" in str(request.url)
        return __import__("httpx").Response(200, json=pages[min(i, len(pages) - 1)],
                                            headers={"x-business-use-case-usage": '{"111": [{"type": "ads_insights", '
                                                     '"call_count": 12, "total_cputime": 5, "total_time": 8, '
                                                     '"estimated_time_to_regain_access": 0}]}'})
    return mock.get(META_URL).mock(side_effect=handler)


@pytest.fixture
def apis():
    """Every outside API mocked with the fixture story."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as mock:
        meta_route(mock, F.meta_pages())
        mock.post(TOKEN_URL).respond(json=F.GOOGLE_TOKEN)
        mock.post(GOOGLE_URL).respond(json=F.google_stream())
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        yield mock
