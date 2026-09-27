"""Timing, the audio track, and the ffmpeg call.

ffmpeg only ever sees file paths inside the render's temp dir and numbers we computed: no
user text reaches its command line.
"""
import array
import re
import shutil
import subprocess
import wave
from pathlib import Path

FPS = 30
WORDS_PER_SECOND = 2.6
MIN_SEGMENT_S = 1.5
VOICE_PAD_S = 0.3
XFADE_S = 0.25
SAMPLE_RATE = 22050   # silent track and `say`; piper voices bring their own rate


class FFmpegMissing(Exception):
    pass


class RenderFailed(Exception):
    pass


# ---------- timing


def estimate_seconds(spoken: str) -> float:
    """No voice: 2.6 words per second, at least 1.5 s."""
    return max(MIN_SEGMENT_S, len(spoken.split()) / WORDS_PER_SECOND)


def voiced_seconds(audio_s: float) -> float:
    return max(MIN_SEGMENT_S, audio_s + VOICE_PAD_S)


def starts(durations: list[float], xfade: float = XFADE_S) -> list[float]:
    """Output start time of each segment: each crossfade overlaps two segments by `xfade`."""
    out, t = [], 0.0
    for d in durations:
        out.append(round(t, 3))
        t += d - xfade
    return out


def total_seconds(durations: list[float], xfade: float = XFADE_S) -> float:
    if not durations:
        return 0.0
    return round(sum(durations) - xfade * (len(durations) - 1), 3)


# ---------- audio


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def build_track(out: Path, total_s: float, clips: list[tuple[float, Path]] | None = None) -> None:
    """One mono 16-bit WAV of total_s seconds, silent except for each clip placed at its start.

    With no clips this is the silent track: platforms reject uploads without an audio stream.
    """
    clips = clips or []
    rate = SAMPLE_RATE
    if clips:
        with wave.open(str(clips[0][1]), "rb") as w:
            rate = w.getframerate()
    n = int(round(total_s * rate))
    buf = array.array("h", bytes(2 * n))
    for start, path in clips:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != rate:
                raise RenderFailed("voice clips must be mono 16-bit at one sample rate")
            samples = array.array("h", w.readframes(w.getnframes()))
        at = int(round(start * rate))
        end = min(n, at + len(samples))
        buf[at:end] = samples[: end - at]
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(buf.tobytes())


# ---------- ffmpeg


def find_ffmpeg() -> tuple[str | None, str | None]:
    """(path, source): system ffmpeg on PATH first, then the imageio-ffmpeg bundled binary."""
    system = shutil.which("ffmpeg")
    if system:
        return system, "system"
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe(), "imageio-ffmpeg"
    except Exception:
        return None, None


def ffmpeg_args(frames: list[Path], durations: list[float], audio: Path, out: Path,
                xfade: float = XFADE_S) -> list[str]:
    """Arguments after the ffmpeg binary: stills with crossfades, H.264/yuv420p, AAC, faststart."""
    args = ["-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    for frame, d in zip(frames, durations):
        args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{d:.3f}", "-i", str(frame)]
    args += ["-i", str(audio)]
    n = len(frames)
    if n == 1:
        graph = "[0:v]fps=30,format=yuv420p[vout]"
    else:
        parts, prev = [], "[0:v]"
        offs = starts(durations, xfade)
        for i in range(1, n):
            label = f"[v{i}]"
            parts.append(f"{prev}[{i}:v]xfade=transition=fade:duration={xfade:.3f}:offset={offs[i]:.3f}{label}")
            prev = label
        parts.append(f"{prev}fps=30,format=yuv420p[vout]")
        graph = ";".join(parts)
    total = total_seconds(durations, xfade)
    args += [
        "-filter_complex", graph, "-map", "[vout]", "-map", f"{n}:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-t", f"{total:.3f}", "-movflags", "+faststart", str(out),
    ]
    return args


def encode(frames: list[Path], durations: list[float], audio: Path, out: Path, timeout: float) -> None:
    exe, _ = find_ffmpeg()
    if not exe:
        raise FFmpegMissing("ffmpeg not found: install it or the imageio-ffmpeg package")
    try:
        proc = subprocess.run([exe, *ffmpeg_args(frames, durations, audio, out)],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RenderFailed(f"ffmpeg took longer than {timeout:g}s")
    if proc.returncode != 0 or not out.is_file():
        raise RenderFailed(f"ffmpeg failed: {proc.stderr.strip()[-400:]}")


def probe(path: Path, timeout: float = 30) -> dict:
    """Minimal ffprobe: parse `ffmpeg -i` for duration, size, codecs, fps."""
    exe, _ = find_ffmpeg()
    if not exe:
        raise FFmpegMissing("ffmpeg not found")
    err = subprocess.run([exe, "-hide_banner", "-nostdin", "-i", str(path)],
                         capture_output=True, text=True, timeout=timeout).stderr
    info: dict = {"raw": err}
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        info["duration_s"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    v = re.search(r"Video: (\w+).*?, (\w+)\(?.*?, (\d{2,5})x(\d{2,5})", err)
    if v:
        info.update(video_codec=v[1], pix_fmt=v[2], width=int(v[3]), height=int(v[4]))
    f = re.search(r"(\d+(?:\.\d+)?) fps", err)
    if f:
        info["fps"] = float(f[1])
    a = re.search(r"Audio: (\w+)", err)
    info["audio_codec"] = a[1] if a else None
    return info
