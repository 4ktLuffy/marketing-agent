"""Speech to words with timestamps: faster-whisper (MIT), CPU int8, VAD filter on.

WHISPER_MODEL is a size name ("small", default) or a local model folder. The Docker image
ships Systran/faster-whisper-small at a pinned revision and runs with HF_HUB_OFFLINE=1, so a
running service never downloads a model.
"""
import os
import threading

from .windows import Word

_model = None
_model_name = None
_lock = threading.Lock()


class TranscribeUnavailable(Exception):
    pass


def model_name() -> str:
    return (os.environ.get("WHISPER_MODEL") or "small").strip()


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _load():
    global _model, _model_name
    name = model_name()
    with _lock:
        if _model is None or _model_name != name:
            try:
                from faster_whisper import WhisperModel
            except Exception as e:
                raise TranscribeUnavailable(f"faster-whisper is not installed: {type(e).__name__}")
            threads = int(os.environ.get("WHISPER_THREADS", "0") or 0)
            _model = WhisperModel(name, device="cpu", compute_type=os.environ.get("WHISPER_COMPUTE", "int8"),
                                  cpu_threads=threads)
            _model_name = name
        return _model


def transcribe(wav_path: str, language: str | None = None, progress=None) -> tuple[list[Word], dict]:
    """(words, info). progress(fraction) is called as segments come in."""
    model = _load()
    segments, info = model.transcribe(
        wav_path, language=language, word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500}, beam_size=int(os.environ.get("WHISPER_BEAM", "5")),
        condition_on_previous_text=False,
    )
    total = float(getattr(info, "duration", 0) or 0)
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            text = (w.word or "").strip()
            if text and w.end > w.start:
                words.append(Word(text, round(float(w.start), 3), round(float(w.end), 3)))
        if progress and total:
            progress(min(1.0, float(seg.end) / total))
    return words, {"language": getattr(info, "language", None),
                   "language_probability": round(float(getattr(info, "language_probability", 0) or 0), 3),
                   "model": model_name()}
