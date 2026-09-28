import json
import os
import time
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main, media, net, safe_http
from app.main import app

KEY = "test-key"
AUTH = {"X-API-Key": KEY}
client = TestClient(app)
HEX = "0123456789abcdef" * 2


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    for k in ("PUBLIC_BASE_URL", "RETENTION_DAYS", "MAX_UPLOAD_MB", "MAX_CLIPS", "ALLOW_PRIVATE_URLS", "WHISPER_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(media, "find_ffmpeg", lambda: ("/x/ffmpeg", "system"))
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.216.34"])
    yield
    if main._busy.locked():
        main._busy.release()


@pytest.fixture
def no_run(monkeypatch):
    """Record the job instead of running the pipeline."""
    calls = []

    def fake(job, url, work):
        calls.append({"job": job, "url": url, "work": work,
                      "source": (work / "source").read_bytes() if (work / "source").exists() else None})
        main._busy.release()

    monkeypatch.setattr(main, "run_job", fake)
    # Run the "background" job inline so each test sees its effect at once.
    monkeypatch.setattr(main, "start_background", lambda target, *args: target(*args))
    return calls


def post(body, headers=AUTH):
    return client.post("/jobs", json=body, headers=headers)


# ---------- health, auth


def test_health_reports_tools_without_secrets(monkeypatch):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["ffmpeg"]["found"] is True and body["busy"] is False
    assert body["whisper"]["model"] == "small" and KEY not in r.text


def test_health_degraded_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(media, "find_ffmpeg", lambda: (None, None))
    assert client.get("/health").status_code == 503


def test_jobs_need_the_key(no_run, monkeypatch):
    assert post({"url": "https://cdn.example.com/talk.mp4"}, headers={}).status_code == 401
    assert post({"url": "https://cdn.example.com/talk.mp4"}, headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get(f"/jobs/{HEX}").status_code == 401
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert post({"url": "https://cdn.example.com/talk.mp4"}).status_code == 503
    assert not no_run


# ---------- sources


def test_url_job_is_queued_with_defaults(no_run, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://clips.example.com/")
    r = post({"url": "https://cdn.example.com/talks/ep1.mp4?token=secret"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert main.ID.fullmatch(body["id"]) and body["status"] == "queued"
    assert body["status_url"] == f"https://clips.example.com/jobs/{body['id']}"
    job = no_run[0]["job"]
    assert job["params"] == {"max_clips": 5, "min_s": 20.0, "max_s": 60.0, "language": None, "reframe": "blur"}   # default: slide text must never be cut
    assert job["source"] == {"kind": "url", "name": "https://cdn.example.com/talks/ep1.mp4"}  # no token stored
    stored = client.get(f"/jobs/{body['id']}", headers=AUTH).json()
    assert stored["status"] == "queued" and "secret" not in json.dumps(stored)
    assert not main._busy.locked()


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abc", "https://youtu.be/abc", "https://m.youtube.com/shorts/x",
    "https://www.tiktok.com/@a/video/1", "https://instagram.com/reel/x", "https://vimeo.com/123",
    "https://x.com/a/status/1", "https://www.facebook.com/watch?v=1", "https://fb.watch/abc",
])
def test_platform_links_are_refused(no_run, url):
    r = post({"url": url})
    assert r.status_code == 422
    assert "download the video yourself and upload it" in r.json()["detail"]
    assert not no_run


def test_lookalike_hosts_are_not_platforms():
    assert not main.is_platform_link("https://notyoutube.com/a.mp4")
    assert not main.is_platform_link("https://youtube.com.evil.example/a.mp4")
    assert main.is_platform_link("https://WWW.YouTube.com./watch")


@pytest.mark.parametrize("url,ip", [
    ("http://127.0.0.1/a.mp4", None), ("http://localhost/a.mp4", "127.0.0.1"),
    ("http://10.0.0.5/a.mp4", None), ("http://[::1]/a.mp4", None),
    ("http://metadata.internal/a.mp4", "169.254.169.254"), ("http://x.example/a.mp4", "::ffff:192.168.1.2"),
])
def test_private_addresses_are_blocked(no_run, monkeypatch, url, ip):
    if ip:
        monkeypatch.setattr(safe_http, "resolve", lambda host: [ip])
    r = post({"url": url})
    assert r.status_code == 422 and "non-public" in r.json()["detail"]
    assert not no_run


@pytest.mark.parametrize("url", ["ftp://x.example/a.mp4", "file:///etc/passwd", "gopher://x", "not a url"])
def test_only_http_links(no_run, url):
    assert post({"url": url}).status_code == 422


def test_upload_job_streams_the_file(no_run):
    r = client.post("/jobs", headers=AUTH, files={"file": ("my talk (final).mp4", b"\x00\x00fakevideo", "video/mp4")},
                    data={"max_clips": "3", "min_s": "15", "max_s": "45", "language": "en", "reframe": "blur"})
    assert r.status_code == 202, r.text
    call = no_run[0]
    assert call["source"] == b"\x00\x00fakevideo" and call["url"] is None
    assert call["job"]["source"] == {"kind": "upload", "name": "my_talk_final_.mp4"}
    assert call["job"]["params"] == {"max_clips": 3, "min_s": 15.0, "max_s": 45.0, "language": "en",
                                     "reframe": "blur"}


def test_upload_and_url_together_or_neither(no_run):
    r = client.post("/jobs", headers=AUTH, files={"file": ("a.mp4", b"x", "video/mp4")},
                    data={"url": "https://cdn.example.com/a.mp4"})
    assert r.status_code == 422
    assert post({}).status_code == 422
    r = client.post("/jobs", headers=AUTH, files={"file": ("a.mp4", b"x", "video/mp4")}, data={"evil": "1"})
    assert r.status_code == 422
    assert not no_run and not main._busy.locked()


def test_upload_cap(no_run, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.001")  # ~1 KB
    r = client.post("/jobs", headers=AUTH, files={"file": ("a.mp4", b"x" * 5000, "video/mp4")})
    assert r.status_code == 413
    assert not no_run and not main._busy.locked()
    assert not any((Path(os.environ["DATA_DIR"]) / "tmp").glob("*/source"))


@pytest.mark.parametrize("body", [
    {"url": "https://cdn.example.com/a.mp4", "max_clips": 0},
    {"url": "https://cdn.example.com/a.mp4", "max_clips": 11},
    {"url": "https://cdn.example.com/a.mp4", "min_s": 2},
    {"url": "https://cdn.example.com/a.mp4", "max_s": 500},
    {"url": "https://cdn.example.com/a.mp4", "min_s": 40, "max_s": 42},
    {"url": "https://cdn.example.com/a.mp4", "language": "English"},
    {"url": "https://cdn.example.com/a.mp4", "reframe": "zoom"},
    {"url": "https://cdn.example.com/a.mp4", "unknown": 1},
])
def test_caps_and_validation(no_run, body):
    assert post(body).status_code == 422
    assert not no_run


def test_one_job_at_a_time(no_run):
    main._busy.acquire()
    try:
        r = post({"url": "https://cdn.example.com/a.mp4"})
        assert r.status_code == 429
        r = client.post("/jobs", headers=AUTH, files={"file": ("a.mp4", b"x", "video/mp4")})
        assert r.status_code == 429
    finally:
        main._busy.release()
    assert post({"url": "https://cdn.example.com/a.mp4"}).status_code == 202


# ---------- ids and files


@pytest.mark.parametrize("bad", ["..", "..%2F..%2Fetc%2Fpasswd", "ABCDEF0123456789ABCDEF0123456789",
                                 HEX[:-1], HEX + "0", "%2e%2e"])
def test_strict_ids(bad):
    assert client.get(f"/clips/{bad}.mp4").status_code == 404
    assert client.get(f"/clips/{bad}.srt").status_code == 404
    assert client.get(f"/jobs/{bad}", headers=AUTH).status_code == 404


def test_clip_files_are_served(tmp_path):
    d = tmp_path / "clips"
    d.mkdir()
    (d / f"{HEX}.mp4").write_bytes(b"mp4")
    (d / f"{HEX}.jpg").write_bytes(b"jpg")
    (d / f"{HEX}.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    r = client.get(f"/clips/{HEX}.mp4")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and r.content == b"mp4"
    assert client.get(f"/clips/{HEX}.jpg").headers["content-type"] == "image/jpeg"
    assert client.get(f"/clips/{HEX}.srt").headers["content-type"].startswith("application/x-subrip")
    assert client.get(f"/clips/{'f' * 32}.mp4").status_code == 404


def test_job_urls_use_public_base(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://clips.example.com")
    main.save_job({"id": HEX, "status": "done", "clips": [{"id": "a" * 32, "title": "t"}]})
    clip = client.get(f"/jobs/{HEX}", headers=AUTH).json()["clips"][0]
    assert clip["url"] == f"https://clips.example.com/clips/{'a' * 32}.mp4"
    assert clip["poster_url"].endswith(".jpg") and clip["srt_url"].endswith(".srt")


def test_retention_and_restart_cleanup(tmp_path, monkeypatch):
    main.save_job({"id": HEX, "status": "running", "clips": []})
    (tmp_path / "tmp" / "x").mkdir(parents=True)
    d = tmp_path / "clips"
    d.mkdir()
    old, new = d / f"{'a' * 32}.mp4", d / f"{'b' * 32}.mp4"
    keep = d / "notes.txt"
    for p in (old, new, keep):
        p.write_bytes(b"x")
    os.utime(old, (time.time() - 10 * 86400,) * 2)
    os.utime(keep, (time.time() - 10 * 86400,) * 2)
    monkeypatch.setenv("RETENTION_DAYS", "7")
    main.startup()
    assert not (tmp_path / "tmp").exists()
    assert main.load_job(HEX)["status"] == "failed"
    assert not old.exists() and new.exists() and keep.exists()


# ---------- downloads (SSRF guard on every hop, size cap, content type)


@respx.mock
def test_download_streams_media(tmp_path):
    respx.get("https://cdn.example.com/a.mp4").mock(
        return_value=httpx.Response(200, content=b"v" * 3000, headers={"content-type": "video/mp4"}))
    got = net.download("https://cdn.example.com/a.mp4", tmp_path / "s", max_bytes=10_000)
    assert got["bytes"] == 3000 and (tmp_path / "s").read_bytes() == b"v" * 3000


@respx.mock
def test_download_refuses_html_and_big_files(tmp_path):
    respx.get("https://cdn.example.com/page").mock(
        return_value=httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}))
    with pytest.raises(net.NotMedia):
        net.download("https://cdn.example.com/page", tmp_path / "s", max_bytes=10_000)
    respx.get("https://cdn.example.com/big.mp4").mock(
        return_value=httpx.Response(200, content=b"v" * 20_000, headers={"content-type": "video/mp4"}))
    with pytest.raises(net.TooLarge):
        net.download("https://cdn.example.com/big.mp4", tmp_path / "s", max_bytes=10_000)
    assert not (tmp_path / "s").exists()


@respx.mock
def test_download_octet_stream_needs_a_media_extension(tmp_path):
    for path in ("/file.bin", "/file.mov"):
        respx.get(f"https://cdn.example.com{path}").mock(
            return_value=httpx.Response(200, content=b"v", headers={"content-type": "application/octet-stream"}))
    with pytest.raises(net.NotMedia):
        net.download("https://cdn.example.com/file.bin", tmp_path / "s", max_bytes=100)
    assert net.download("https://cdn.example.com/file.mov", tmp_path / "s", max_bytes=100)["bytes"] == 1


@respx.mock
def test_download_rechecks_redirects(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["10.0.0.1"] if host == "internal.example" else ["93.184.216.34"])
    respx.get("https://cdn.example.com/a.mp4").mock(
        return_value=httpx.Response(302, headers={"location": "http://internal.example/secret.mp4"}))
    with pytest.raises(net.BlockedURL):
        net.download("https://cdn.example.com/a.mp4", tmp_path / "s", max_bytes=100)


# ---------- the pipeline with every heavy step faked


def test_run_job_records_failure_and_frees_the_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "probe", lambda p: {"duration_s": 10.0, "has_audio": False, "has_video": True,
                                                   "width": 1920, "height": 1080})
    work = tmp_path / "tmp" / HEX
    work.mkdir(parents=True)
    (work / "source").write_bytes(b"x")
    job = {"id": HEX, "status": "queued", "params": {"max_clips": 1, "min_s": 20, "max_s": 60,
                                                     "language": None, "reframe": "center"},
           "source": {"kind": "upload"}, "timings_ms": {}, "notes": [], "clips": []}
    main._busy.acquire()
    main.run_job(job, None, work)
    assert not main._busy.locked() and not work.exists()
    stored = main.load_job(HEX)
    assert stored["status"] == "failed" and "sound track" in stored["error"]
    assert "total" in stored["timings_ms"]
