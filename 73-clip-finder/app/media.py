"""ffmpeg calls: probe, audio extraction, silence detection, the clip render, the poster.

ffmpeg only ever sees file paths inside the job's temp dir and numbers we computed. The
caption file is passed by a relative name with the temp dir as the working directory, so no
path (and no transcript text) is ever written into a filter string.
"""
import re
import shutil
import subprocess
from pathlib import Path

FPS = 30
WIDTH, HEIGHT = 1080, 1920
AUDIO_ONLY_BG = "0x14161C"


class FFmpegMissing(Exception):
    pass


class RenderFailed(Exception):
    pass


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


def _exe() -> str:
    exe, _ = find_ffmpeg()
    if not exe:
        raise FFmpegMissing("ffmpeg not found: install it or the imageio-ffmpeg package")
    return exe


def run(args: list[str], timeout: float, cwd: Path | None = None) -> str:
    """Run ffmpeg with args; returns stderr. RenderFailed on error or timeout."""
    try:
        proc = subprocess.run([_exe(), "-hide_banner", "-nostdin", *args], capture_output=True,
                              text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise RenderFailed(f"ffmpeg took longer than {timeout:g}s")
    if proc.returncode != 0:
        raise RenderFailed(f"ffmpeg failed: {proc.stderr.strip()[-400:]}")
    return proc.stderr


def probe(path: Path, timeout: float = 60) -> dict:
    """Parse `ffmpeg -i` for duration, video size/fps and whether there is audio."""
    try:
        err = subprocess.run([_exe(), "-hide_banner", "-nostdin", "-i", str(path)],
                             capture_output=True, text=True, timeout=timeout).stderr
    except subprocess.TimeoutExpired:
        raise RenderFailed("ffmpeg probe timed out")
    info: dict = {"duration_s": None, "has_video": False, "has_audio": False,
                  "width": None, "height": None, "fps": None, "rotation": 0}
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        info["duration_s"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    # Cover art in an mp3/m4a shows up as a video stream "(attached pic)": that is not video.
    for line in err.splitlines():
        if "Stream #" in line and "Video:" in line and "attached pic" not in line and not info["has_video"]:
            v = re.search(r", (\d{2,5})x(\d{2,5})", line)
            if v:
                info.update(has_video=True, width=int(v[1]), height=int(v[2]))
            f = re.search(r"(\d+(?:\.\d+)?) fps", line)
            if f:
                info["fps"] = float(f[1])
        if "Stream #" in line and "Audio:" in line:
            info["has_audio"] = True
    r = re.search(r"rotate\s*:\s*(-?\d+)", err) or re.search(r"rotation of (-?\d+(?:\.\d+)?)", err)
    if r:
        info["rotation"] = int(float(r[1])) % 360
    if info["has_video"] and info["rotation"] in (90, 270):  # ffmpeg autorotates on decode
        info["width"], info["height"] = info["height"], info["width"]
    return info


def extract_audio(src: Path, out: Path, timeout: float) -> None:
    """Mono 16 kHz WAV, what Whisper wants."""
    run(["-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(out)], timeout)


def parse_silences(stderr: str) -> list[tuple[float, float]]:
    out, start = [], None
    for line in stderr.splitlines():
        m = re.search(r"silence_start: (-?\d+(?:\.\d+)?)", line)
        if m:
            start = max(0.0, float(m[1]))
            continue
        m = re.search(r"silence_end: (\d+(?:\.\d+)?)", line)
        if m and start is not None:
            out.append((round(start, 3), round(float(m[1]), 3)))
            start = None
    return out


def silences(wav: Path, timeout: float, noise_db: float = -35, min_s: float = 0.3) -> list[tuple[float, float]]:
    err = run(["-i", str(wav), "-af", f"silencedetect=noise={noise_db:g}dB:d={min_s:g}", "-f", "null", "-"],
              timeout)
    return parse_silences(err)


def even(n: float) -> int:
    return max(2, int(n) // 2 * 2)


def video_filter(mode: str, src_w: int | None, src_h: int | None, center_x: float | None = None) -> str:
    """Filter chain from the source frame to 1080x1920, before the captions.

    center: a 9:16 slice from the middle (or from center_x, the face centre as a 0..1 fraction
    of the width). blur: the whole frame fitted to the width over a blurred, zoomed copy of
    itself. Sources that are already 9:16 or taller are scaled and padded.
    """
    if not src_w or not src_h:
        return f"scale={WIDTH}:{HEIGHT}"
    if src_w * 16 <= src_h * 9:  # already vertical: fit, pad the rest
        return (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    if mode == "blur":
        return (f"split=2[bg][fg];[bg]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={WIDTH}:{HEIGHT},gblur=sigma=30,eq=brightness=-0.12[bgb];"
                f"[fg]scale={WIDTH}:-2[fgs];[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")
    crop_w = even(src_h * 9 / 16)
    cx = (center_x if center_x is not None else 0.5) * src_w
    x = int(min(max(0, cx - crop_w / 2), src_w - crop_w))
    return f"crop={crop_w}:{even(src_h)}:{x}:0,scale={WIDTH}:{HEIGHT},setsar=1"


def clip_args(src: Path, start: float, duration: float, out: Path, vf: str | None,
              ass_name: str | None, fonts_dir: str | None) -> list[str]:
    """Arguments for one clip. vf=None means an audio-only source: a plain background."""
    args = ["-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src)]
    subs = ""
    if ass_name:
        subs = f",ass={ass_name}" + (f":fontsdir={fonts_dir}" if fonts_dir and _filter_safe(fonts_dir) else "")
    if vf is None:
        args += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", f"color=c={AUDIO_ONLY_BG}:s={WIDTH}x{HEIGHT}:r={FPS}"]
        graph = f"[1:v]format=yuv420p{subs}[vout]"
    else:
        graph = f"[0:v]{vf},fps={FPS}{subs},format=yuv420p[vout]"
    args += [
        "-filter_complex", graph, "-map", "[vout]", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out),
    ]
    return args


def _filter_safe(path: str) -> bool:
    """A fonts dir goes into the filter string: only plain path characters."""
    return bool(re.fullmatch(r"[A-Za-z0-9/_.\-]+", path))


def render_clip(src: Path, start: float, duration: float, out: Path, vf: str | None,
                ass_file: Path | None, fonts_dir: str | None, timeout: float) -> None:
    cwd = ass_file.parent if ass_file else None
    run(clip_args(src, start, duration, out, vf, ass_file.name if ass_file else None, fonts_dir),
        timeout, cwd=cwd)
    if not out.is_file() or out.stat().st_size == 0:
        raise RenderFailed("ffmpeg wrote no clip")


def poster(mp4: Path, out: Path, at_s: float, timeout: float) -> None:
    run(["-y", "-loglevel", "error", "-ss", f"{at_s:.3f}", "-i", str(mp4), "-frames:v", "1",
         "-q:v", "3", str(out)], timeout)


def frames_at(src: Path, times: list[float], out_dir: Path, width: int, timeout: float) -> list[Path]:
    """Small PNG stills of the source at the given times (for face detection)."""
    paths = []
    for i, t in enumerate(times):
        p = out_dir / f"probe{i:03d}.png"
        try:
            run(["-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                 "-vf", f"scale={width}:-2", str(p)], timeout)
        except RenderFailed:
            continue
        if p.is_file():
            paths.append(p)
    return paths
