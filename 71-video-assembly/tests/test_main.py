import os
import sys
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import frames, main, media, tts
from app.main import app

KEY = "test-key"
AUTH = {"X-API-Key": KEY}
client = TestClient(app)

SCRIPT = {
    "hook": {"spoken": "Ever wondered how decaf loses its caffeine?", "on_screen": "Decaf, explained"},
    "beats": [
        {"spoken": "It starts as green beans, soaked in warm water.", "on_screen": "Green beans first",
         "shot": "close-up of green beans in a glass bowl"},
    ],
    "cta": "Follow for more coffee tips",
    "caption": "How decaf is made, in under a minute.",
    "hashtags": ["decaf", "coffee", "howitsmade"],
    "estimated_seconds": 30,
}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.delenv("TTS_BACKEND", raising=False)
    monkeypatch.delenv("PIPER_VOICE", raising=False)
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    monkeypatch.delenv("MAX_SECONDS", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)


@pytest.fixture
def fake_encode(monkeypatch):
    """Skip ffmpeg for API tests: record the call and write a stub file."""
    calls = []

    def encode(frame_paths, durations, audio, out, timeout):
        calls.append({"frames": list(frame_paths), "durations": list(durations), "audio": audio})
        assert all(Path(p).is_file() for p in frame_paths) and Path(audio).is_file()
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42stub")

    monkeypatch.setattr(media, "encode", encode)
    monkeypatch.setattr(media, "find_ffmpeg", lambda: ("/bin/true", "system"))
    return calls


def render(body=None, headers=AUTH):
    return client.post("/render", json=body or {"script": SCRIPT, "voice": {"backend": "none"}}, headers=headers)


# ---------- health, auth


def test_health_reports_ffmpeg_and_backends_without_secrets(monkeypatch):
    monkeypatch.setattr(media, "find_ffmpeg", lambda: ("/x/ffmpeg", "imageio-ffmpeg"))
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["ffmpeg"] == {"found": True, "source": "imageio-ffmpeg"}
    assert body["voice_backends"]["none"] is True and body["voice_backends"]["piper"] is False
    assert KEY not in r.text


def test_health_degraded_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(media, "find_ffmpeg", lambda: (None, None))
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "degraded"


def test_render_needs_the_key(fake_encode, monkeypatch):
    assert render(headers={}).status_code == 401
    assert render(headers={"X-API-Key": "wrong"}).status_code == 401
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert render().status_code == 503


# ---------- layout


def test_wrap_keeps_every_line_inside_the_box():
    fnt = frames.font(True, 80)
    text = "The quick brown fox jumps over the lazy dog while the barista grinds fresh beans"
    lines = frames.wrap(text, fnt, 600)
    assert len(lines) > 1 and all(fnt.getlength(line) <= 600 for line in lines)
    assert " ".join(lines) == text


def test_long_word_is_hard_broken():
    fnt = frames.font(True, 80)
    word = "Supercalifragilisticexpialidocious" * 3
    lines = frames.wrap(f"a {word} b", fnt, 500)
    assert all(fnt.getlength(line) <= 500 for line in lines)
    assert "".join(lines).replace(" ", "") == f"a{word}b"


def test_fit_shrinks_long_text_and_clamps():
    short_font, _ = frames.fit("Decaf", 900, 800, 132, 48)
    long_font, long_lines = frames.fit("word " * 60, 900, 800, 132, 48)
    assert long_font.size < short_font.size
    assert len(long_lines) * frames.line_height(long_font) <= 800
    huge_font, huge_lines = frames.fit("word " * 2000, 900, 300, 132, 48)
    assert huge_font.size == 48 and huge_lines[-1].endswith("…")
    assert len(huge_lines) * frames.line_height(huge_font) <= 300


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("kind", ["hook", "beat", "cta"])
def test_frame_is_1080x1920(theme, kind):
    seg = frames.Segment(kind, "Green beans first " * 3, "It starts as green beans. " * 8, "a shot")
    img = frames.render_frame(seg, 1, 3, 1 if kind == "beat" else None, 1, brand_name="Acme Coffee",
                              logo_text="AC", accent="#FFCC00", theme=theme, show_shot=True)
    assert img.size == (1080, 1920) and img.mode == "RGB"


def test_on_color_contrast():
    assert frames.on_color("#FFFFFF") == "#111111"
    assert frames.on_color("#14161C") == "#FFFFFF"


# ---------- timing


def test_estimate_is_2_6_words_per_second_with_a_minimum():
    assert media.estimate_seconds("one two") == 1.5
    assert media.estimate_seconds(" ".join(["w"] * 26)) == pytest.approx(10.0)


def test_voiced_segment_is_audio_plus_pad():
    assert media.voiced_seconds(2.0) == pytest.approx(2.3)
    assert media.voiced_seconds(0.2) == 1.5


def test_crossfade_offsets_and_total():
    d = [2.0, 3.0, 4.0]
    assert media.starts(d, 0.25) == [0.0, 1.75, 4.5]
    assert media.total_seconds(d, 0.25) == pytest.approx(8.5)
    assert media.total_seconds([]) == 0.0


def test_ffmpeg_args_carry_no_user_text(tmp_path):
    fr = [tmp_path / "frame00.png", tmp_path / "frame01.png"]
    args = media.ffmpeg_args(fr, [2.0, 3.0], tmp_path / "track.wav", tmp_path / "out.mp4")
    joined = " ".join(args)
    assert "xfade=transition=fade:duration=0.250:offset=1.750" in joined
    assert "subtitles" not in joined and "drawtext" not in joined
    for flag in ("libx264", "yuv420p", "aac", "+faststart"):
        assert flag in args


def test_audio_track_places_clips(tmp_path):
    clip = tmp_path / "c.wav"
    with wave.open(str(clip), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(22050)
        w.writeframes(b"\x10\x00" * 22050)  # 1 s of a constant
    out = tmp_path / "t.wav"
    media.build_track(out, 3.0, [(1.5, clip)])
    assert media.wav_seconds(out) == pytest.approx(3.0)
    with wave.open(str(out), "rb") as w:
        data = w.readframes(w.getnframes())
    assert data[:2] == b"\x00\x00" and data[2 * int(1.6 * 22050):][:2] == b"\x10\x00"


# ---------- API: render, ids, caps


def test_render_stores_video_and_poster(fake_encode, tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://videos.example.com/")
    r = render({"script": SCRIPT, "voice": {"backend": "none"}, "brand": {"name": "Acme", "logo_text": "AC"},
                "style": {"theme": "dark"}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert main.VIDEO_ID.fullmatch(body["id"])
    assert body["url"] == f"https://videos.example.com/videos/{body['id']}.mp4"
    assert (body["width"], body["height"], body["voice_backend"]) == (1080, 1920, "none")
    assert [s["kind"] for s in body["segments"]] == ["hook", "beat", "cta"]
    assert len(fake_encode[0]["frames"]) == 3
    assert (tmp_path / "videos" / f"{body['id']}.mp4").is_file()
    assert client.get(f"/videos/{body['id']}.mp4").headers["content-type"] == "video/mp4"
    poster = client.get(f"/videos/{body['id']}.jpg")
    assert poster.status_code == 200 and poster.content[:2] == b"\xff\xd8"
    assert list((tmp_path / "tmp").iterdir()) == []  # temp dir cleaned up


def test_default_backend_comes_from_env(fake_encode, monkeypatch):
    monkeypatch.setenv("TTS_BACKEND", "none")
    assert render({"script": SCRIPT}).json()["voice_backend"] == "none"


@pytest.mark.parametrize("bad", [
    "0" * 31, "0" * 33, "G" * 32, "A" * 32, "..%2F..%2Fetc%2Fpasswd", "%2e%2e%2f" * 4, "../../etc/passwd",
])
def test_bad_ids_are_404(bad):
    assert client.get(f"/videos/{bad}.mp4").status_code == 404
    assert client.get(f"/videos/{bad}.jpg").status_code == 404


def test_unknown_but_valid_id_is_404():
    assert client.get(f"/videos/{'a' * 32}.mp4").status_code == 404


def test_traversal_never_reaches_a_file_outside(tmp_path):
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"x")
    for path in ("/videos/..%2Fsecret.mp4", "/videos/../secret.mp4", "/videos/%2e%2e/secret.mp4"):
        assert client.get(path).status_code == 404


def test_too_long_script_is_refused(fake_encode, monkeypatch):
    monkeypatch.setenv("MAX_SECONDS", "10")
    long_beat = {"spoken": " ".join(["word"] * 40), "on_screen": "Long"}
    r = render({"script": {**SCRIPT, "beats": [long_beat]}, "voice": {"backend": "none"}})
    assert r.status_code == 422 and "MAX_SECONDS" in r.json()["detail"]
    assert fake_encode == []


@pytest.mark.parametrize("script", [
    {**SCRIPT, "beats": []},
    {**SCRIPT, "beats": [SCRIPT["beats"][0]] * 9},
    {**SCRIPT, "hook": {"spoken": "   ", "on_screen": "x"}},
    {**SCRIPT, "cta": "x" * 201},
])
def test_invalid_scripts_are_422(fake_encode, script):
    assert render({"script": script, "voice": {"backend": "none"}}).status_code == 422


def test_bad_brand_accent_is_422(fake_encode):
    assert render({"script": SCRIPT, "brand": {"accent": "red"}}).status_code == 422


def test_busy_renderer_is_429(fake_encode, monkeypatch):
    import threading
    sem = threading.BoundedSemaphore(1)
    sem.acquire()
    monkeypatch.setattr(main, "render_slots", lambda: sem)
    assert render().status_code == 429


def test_retention_deletes_old_files_only(tmp_path, monkeypatch):
    folder = tmp_path / "videos"
    folder.mkdir()
    old, new, other = folder / f"{'a' * 32}.mp4", folder / f"{'b' * 32}.mp4", folder / "keep.txt"
    for p in (old, new, other):
        p.write_bytes(b"x")
    past = time.time() - 10 * 86400
    os.utime(old, (past, past))
    os.utime(other, (past, past))
    assert main.purge_old() == 0  # RETENTION_DAYS unset = keep forever
    monkeypatch.setenv("RETENTION_DAYS", "7")
    assert main.purge_old() == 1
    assert not old.exists() and new.exists() and other.exists()


# ---------- voice backends


def test_say_is_refused_off_macos(monkeypatch, fake_encode):
    monkeypatch.setattr(sys, "platform", "linux")
    r = render({"script": SCRIPT, "voice": {"backend": "say"}})
    assert r.status_code == 422 and "macOS only" in r.json()["detail"]


def test_piper_without_a_model_is_422(fake_encode, tmp_path, monkeypatch):
    r = render({"script": SCRIPT, "voice": {"backend": "piper"}})
    assert r.status_code == 422
    monkeypatch.setenv("PIPER_VOICE", str(tmp_path / "missing.onnx"))
    monkeypatch.setattr(tts, "piper_installed", lambda: True)
    r = render({"script": SCRIPT, "voice": {"backend": "piper"}})
    assert r.status_code == 422 and "not found" in r.json()["detail"]


def test_unknown_backend_from_env_is_422(fake_encode, monkeypatch):
    monkeypatch.setenv("TTS_BACKEND", "elevenlabs")
    r = render({"script": SCRIPT})
    assert r.status_code == 422 and "unknown voice backend" in r.json()["detail"]


@pytest.mark.parametrize("voice_id", ["../../etc/passwd", "a/b", "x;rm -rf /", ".hidden"])
def test_voice_id_cannot_escape(voice_id, tmp_path, monkeypatch):
    monkeypatch.setenv("PIPER_VOICE", str(tmp_path / "en_US-lessac-medium.onnx"))
    monkeypatch.setattr(tts, "piper_installed", lambda: True)
    with pytest.raises(tts.BackendUnavailable):
        tts.check("piper", voice_id)


def test_piper_voice_id_picks_a_sibling_model(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPER_VOICE", str(tmp_path / "en_US-lessac-medium.onnx"))
    assert tts.piper_voice_path("en_GB-alba-medium") == tmp_path / "en_GB-alba-medium.onnx"


def test_failing_voice_is_422_not_500(fake_encode, monkeypatch):
    monkeypatch.setattr(tts, "check", lambda backend, voice_id=None: None)

    def boom(*a, **k):
        raise RuntimeError("engine crashed")
    monkeypatch.setattr(tts, "synthesize", boom)
    r = render({"script": SCRIPT, "voice": {"backend": "piper"}})
    assert r.status_code == 422 and "failed on segment 1" in r.json()["detail"]


@pytest.mark.skipif(not tts.say_available(), reason="macOS `say` only")
def test_say_writes_a_wav(tmp_path):
    out = tmp_path / "v.wav"
    tts.synthesize("say", "Hello from the test.", out)
    assert media.wav_seconds(out) > 0.3


# ---------- integration: a real 3-segment render through ffmpeg


def _ffmpeg_available() -> bool:
    return media.find_ffmpeg()[0] is not None


@pytest.mark.skipif(not _ffmpeg_available(), reason="no ffmpeg (system or imageio-ffmpeg)")
def test_real_render_produces_a_valid_vertical_mp4(tmp_path):
    r = render({"script": SCRIPT, "voice": {"backend": "none"}, "brand": {"name": "Acme Coffee"}})
    assert r.status_code == 201, r.text
    body = r.json()
    mp4 = tmp_path / "videos" / f"{body['id']}.mp4"
    info = media.probe(mp4)
    assert info["video_codec"] == "h264" and info["pix_fmt"] == "yuv420p"
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["fps"] == 30
    assert info["audio_codec"] == "aac"
    assert info["duration_s"] == pytest.approx(body["duration_s"], abs=0.15)
    expected = media.total_seconds([media.estimate_seconds(s) for s in (
        SCRIPT["hook"]["spoken"], SCRIPT["beats"][0]["spoken"], SCRIPT["cta"])])
    assert body["duration_s"] == pytest.approx(expected)
    # faststart: the moov atom comes before mdat.
    head = mp4.read_bytes()[:4096]
    assert b"moov" in head


def test_emoji_are_dropped_from_frames_and_voice():
    assert main.plain("Bean to Brew 🍂") == "Bean to Brew"
    assert main.plain("Perfectly brewed ☕️ now") == "Perfectly brewed now"
    assert main.plain("Café — naïve 100%") == "Café — naïve 100%"
    assert main.plain("🙌") == "🙌"  # nothing left: keep it rather than an empty frame
