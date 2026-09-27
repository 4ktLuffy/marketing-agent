"""The real pipeline on a tiny generated video: ffmpeg testsrc + speech from macOS `say`,
real faster-whisper, real scene detection and ffmpeg, a mocked scorer.

Skipped where `say` does not exist (CI on Linux) or the Whisper model is not on this machine
already (the test never downloads one). WHISPER_MODEL picks the model (default: small).
"""
import os
import shutil
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app import main, media, scoring, transcribe

pytestmark = pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("say"),
                                reason="needs macOS `say` for the speech track")

SPEECH = (
    "Here is the first idea. Coffee breaks on remote teams need a fixed time, or nobody comes. "
    "We tried a random time every week and it failed badly. "
    "Here is the second idea. Cameras on, but no agenda at all. The moment you add an agenda, "
    "it turns into another status meeting and people stop coming. "
    "Here is the third idea. Rotate who picks the topic. It gives the quiet people a turn to talk, "
    "and it is the best thing we changed all year."
)


def whisper_ready() -> bool:
    if not transcribe.available():
        return False
    from faster_whisper import WhisperModel
    try:
        WhisperModel(transcribe.model_name(), device="cpu", compute_type="int8", local_files_only=True)
        return True
    except Exception:
        return False


@pytest.fixture
def talk(tmp_path):
    exe, _ = media.find_ffmpeg()
    if not exe:
        pytest.skip("no ffmpeg")
    if not whisper_ready():
        pytest.skip("faster-whisper model not available locally")
    aiff = tmp_path / "speech.aiff"
    subprocess.run(["say", "-r", "170", "-o", str(aiff), SPEECH], check=True, timeout=120)
    out = tmp_path / "talk.mp4"
    # Two visual scenes (a cut at 12 s) under the speech.
    subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-t", "12", "-i", "testsrc=size=1280x720:rate=25",
                    "-f", "lavfi", "-t", "60", "-i", "smptebars=size=1280x720:rate=25",
                    "-i", str(aiff),
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out)],
                   check=True, timeout=180)
    return out


def test_real_pipeline_with_mocked_scorer(talk, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INTERNAL_API_KEY", "k")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://clips.test")
    seen = []

    def fake_prompt(vars_, timeout):
        seen.append(vars_)
        ids = [line.split("]")[0][1:] for line in vars_["windows"].splitlines() if line.startswith("[w")]
        return {"scores": [{"id": i, "hook": 8 - n % 3, "standalone": 7, "payoff": 6, "quotable": 5,
                            "title": f"Remote coffee idea {i}", "hook_line": "Cameras on, but no agenda at all.",
                            "reason": "clear point"} for n, i in enumerate(ids)]}

    monkeypatch.setattr(scoring, "run_prompt", fake_prompt)
    monkeypatch.setattr(main, "start_background", lambda target, *args: target(*args))
    client = TestClient(main.app)
    with open(talk, "rb") as f:
        r = client.post("/jobs", headers={"X-API-Key": "k"}, files={"file": ("talk.mp4", f, "video/mp4")},
                        data={"max_clips": "2", "min_s": "8", "max_s": "20"})
    assert r.status_code == 202, r.text
    job = client.get(f"/jobs/{r.json()['id']}", headers={"X-API-Key": "k"}).json()
    assert job["status"] == "done", job
    assert job["transcript"]["words"] > 50 and job["candidates"] >= 2 and seen
    assert "coffee" in seen[0]["windows"].lower()
    assert 1 <= len(job["clips"]) <= 2
    clips = job["clips"]
    for a, b in zip(clips, clips[1:]):
        assert a["end_s"] <= b["start_s"]
    for c in clips:
        assert 7 <= c["duration_s"] <= 21
        mp4 = client.get(f"/clips/{c['id']}.mp4")
        assert mp4.status_code == 200 and c["url"] == f"http://clips.test/clips/{c['id']}.mp4"
        path = tmp_path / "check.mp4"
        path.write_bytes(mp4.content)
        info = media.probe(path)
        assert (info["width"], info["height"]) == (1080, 1920)
        assert info["has_audio"] and abs(info["duration_s"] - c["duration_s"]) < 0.5
        assert client.get(f"/clips/{c['id']}.jpg").content[:2] == b"\xff\xd8"
        srt = client.get(f"/clips/{c['id']}.srt").text
        assert "-->" in srt
    assert not os.listdir(tmp_path / "data" / "tmp")
