"""Core profile: growth services' URLs are set EMPTY → pages say "not installed", not an error."""
from app import config


def test_empty_env_means_not_installed_and_unset_means_default(monkeypatch):
    monkeypatch.setenv("ENGINE_URL", "")
    monkeypatch.delenv("VIDEO_URL", raising=False)
    s = config.load()
    assert s.engine_url == "" and s.video_url == "http://video-assembly:8000"
    assert "" not in s.internal_urls
