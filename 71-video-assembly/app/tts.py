"""Voiceover backends: piper (local neural TTS), say (macOS, dev only), none.

Text always goes to the engine through a file or an in-process call, never on a command line.
"""
import os
import re
import shutil
import subprocess
import sys
import wave
from functools import lru_cache
from pathlib import Path

BACKENDS = ("piper", "say", "none")
VOICE_ID = re.compile(r"[A-Za-z0-9_.\- ]{1,64}")
TTS_TIMEOUT = 60


class BackendUnavailable(Exception):
    """The chosen backend can't run here. The API turns this into a 422."""


def piper_voice_path(voice_id: str | None = None) -> Path | None:
    """PIPER_VOICE, or <dir of PIPER_VOICE>/<voice_id>.onnx when a voice_id is given."""
    default = os.environ.get("PIPER_VOICE", "").strip()
    if not default:
        return None
    base = Path(default)
    if voice_id:
        name = voice_id if voice_id.endswith(".onnx") else f"{voice_id}.onnx"
        if "/" in name or name.startswith("."):
            return None
        return base.parent / name
    return base


def piper_installed() -> bool:
    try:
        import piper  # noqa: F401
    except Exception:
        return False
    return True


def say_available() -> bool:
    return sys.platform == "darwin" and shutil.which("say") is not None


def available() -> dict[str, bool]:
    voice = piper_voice_path()
    return {
        "piper": bool(voice and voice.is_file() and piper_installed()),
        "say": say_available(),
        "none": True,
    }


def check(backend: str, voice_id: str | None = None) -> None:
    """Raise BackendUnavailable with a message a person can act on."""
    if backend not in BACKENDS:
        raise BackendUnavailable(f"unknown voice backend {backend!r}; use one of {', '.join(BACKENDS)}")
    if voice_id is not None and not VOICE_ID.fullmatch(voice_id):
        raise BackendUnavailable("voice_id may only contain letters, digits, space, '.', '_' and '-'")
    if backend == "say":
        if sys.platform != "darwin":
            raise BackendUnavailable("voice backend 'say' is macOS only (a dev backend); use 'piper' or 'none'")
        if shutil.which("say") is None:
            raise BackendUnavailable("voice backend 'say' needs the macOS 'say' command, which is not on PATH")
        if voice_id and voice_id not in say_voices():
            raise BackendUnavailable(f"macOS voice {voice_id!r} is not installed (see `say -v '?'`)")
    elif backend == "piper":
        if not piper_installed():
            raise BackendUnavailable("voice backend 'piper' needs the piper-tts package, which is not installed")
        path = piper_voice_path(voice_id)
        if path is None:
            raise BackendUnavailable("voice backend 'piper' needs PIPER_VOICE (path to a .onnx voice model)")
        if not path.is_file() or not Path(f"{path}.json").is_file():
            raise BackendUnavailable(f"piper voice model {path.name} (and its .onnx.json) not found")


@lru_cache(maxsize=1)
def say_voices() -> frozenset[str]:
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=15).stdout
    # Lines look like: "Samantha            en_US    # Hello! ..." (names may contain spaces).
    return frozenset(re.split(r"\s{2,}", line.strip())[0] for line in out.splitlines() if line.strip())


@lru_cache(maxsize=4)
def _piper_voice(path: str):
    from piper import PiperVoice
    return PiperVoice.load(path)


def synthesize(backend: str, text: str, out_wav: Path, voice_id: str | None = None) -> None:
    """Write a mono 16-bit WAV of text to out_wav."""
    if backend == "say":
        txt = out_wav.with_suffix(".txt")
        txt.write_text(text, encoding="utf-8")
        cmd = ["say", "-o", str(out_wav), "--data-format=LEI16@22050", "-f", str(txt)]
        if voice_id:
            cmd[1:1] = ["-v", voice_id]  # validated against the installed voice list
        subprocess.run(cmd, check=True, capture_output=True, timeout=TTS_TIMEOUT)
    elif backend == "piper":
        voice = _piper_voice(str(piper_voice_path(voice_id)))
        with wave.open(str(out_wav), "wb") as wf:
            if hasattr(voice, "synthesize_wav"):   # piper-tts >= 1.3
                voice.synthesize_wav(text, wf)
            else:                                   # piper-tts 1.2
                voice.synthesize(text, wf)
    else:
        raise ValueError("backend 'none' has no audio")
