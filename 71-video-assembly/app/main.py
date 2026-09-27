"""Video assembly: a short-form video script (prompt video_script, 04) -> a vertical MP4.

One Pillow frame per segment (hook, each beat, CTA) with the on-screen text, a brand bar and
the spoken line burned in as a caption; optional local voiceover (piper, or `say` on a Mac
for development); ffmpeg joins the stills with crossfades. Fully local, no paid APIs.
"""
import hmac
import logging
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import frames, media, tts

log = logging.getLogger("video-assembly")


@asynccontextmanager
async def lifespan(_app):
    startup()
    yield


app = FastAPI(title="video-assembly", lifespan=lifespan)

# Stored video ids: 32 lower-case hex characters. Nothing else is ever turned into a path.
VIDEO_ID = re.compile(r"[0-9a-f]{32}")
_renders: threading.BoundedSemaphore | None = None
_renders_lock = threading.Lock()


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def max_seconds() -> float:
    return env_float("MAX_SECONDS", 90)


def render_slots() -> threading.BoundedSemaphore:
    global _renders
    with _renders_lock:
        if _renders is None:
            _renders = threading.BoundedSemaphore(max(1, int(env_float("MAX_CONCURRENT_RENDERS", 2))))
        return _renders


# ---------- request models (the video_script prompt's output, a little more lenient)


def _clean(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("must not be blank")
    return v


class Hook(BaseModel):
    model_config = ConfigDict(extra="ignore")
    spoken: str = Field(min_length=1, max_length=200)
    on_screen: str = Field(min_length=1, max_length=80)

    @field_validator("spoken", "on_screen")
    @classmethod
    def clean(cls, v: str) -> str:
        return _clean(v)


class Beat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    spoken: str = Field(min_length=1, max_length=300)
    on_screen: str = Field(min_length=1, max_length=80)
    shot: str | None = Field(None, max_length=200)

    @field_validator("spoken", "on_screen")
    @classmethod
    def clean(cls, v: str) -> str:
        return _clean(v)


class Script(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hook: Hook
    beats: list[Beat] = Field(min_length=1, max_length=8)
    cta: str = Field(min_length=1, max_length=200)
    caption: str | None = Field(None, max_length=2200)
    hashtags: list[str] | None = Field(None, max_length=30)
    estimated_seconds: int | None = None

    @field_validator("cta")
    @classmethod
    def clean(cls, v: str) -> str:
        return _clean(v)


class Brand(BaseModel):
    name: str | None = Field(None, max_length=40)
    accent: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    logo_text: str | None = Field(None, max_length=3)


class Voice(BaseModel):
    backend: Literal["piper", "say", "none"] | None = None
    voice_id: str | None = Field(None, max_length=64)


class Style(BaseModel):
    theme: Literal["light", "dark"] = "light"
    show_shots: bool = False


class RenderRequest(BaseModel):
    script: Script
    brand: Brand | None = None
    voice: Voice | None = None
    style: Style | None = None


# ---------- helpers


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data"))


def videos_dir() -> Path:
    return data_dir() / "videos"


def public_base(request: Request) -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)).strip().rstrip("/")


def default_backend() -> str:
    return (os.environ.get("TTS_BACKEND") or "none").strip().lower()


def purge_old(now: float | None = None) -> int:
    """Delete stored videos/posters older than RETENTION_DAYS (0 or unset = keep forever)."""
    days = env_float("RETENTION_DAYS", 0)
    folder = videos_dir()
    if days <= 0 or not folder.is_dir():
        return 0
    cutoff = (now or time.time()) - days * 86400
    removed = 0
    for p in folder.iterdir():
        if p.suffix in (".mp4", ".jpg") and VIDEO_ID.fullmatch(p.stem) and p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
            removed += 1
    return removed


def plain(text: str | None) -> str | None:
    """Drop emoji and other pictographs: the frame fonts can't draw them (they'd show as boxes)
    and a voice would read their names aloud. Falls back to the original if nothing is left."""
    if text is None:
        return None
    kept = "".join(ch for ch in text if unicodedata.category(ch) not in ("So", "Cs", "Co", "Cn")
                   and ch not in "\u200d\ufe0f\ufe0e")
    kept = " ".join(kept.split())
    return kept or text


def segments_of(script: Script) -> list[frames.Segment]:
    segs = [frames.Segment("hook", plain(script.hook.on_screen), plain(script.hook.spoken))]
    segs += [frames.Segment("beat", plain(b.on_screen), plain(b.spoken), plain(b.shot)) for b in script.beats]
    segs.append(frames.Segment("cta", plain(script.cta), plain(script.cta)))
    return segs


def startup():
    try:
        tmp = data_dir() / "tmp"
        if tmp.is_dir():
            shutil.rmtree(tmp, ignore_errors=True)  # left over from a killed render
        purge_old()
    except OSError as e:
        log.warning("startup cleanup failed: %s", e)


# ---------- endpoints


@app.get("/health")
def health():
    exe, source = media.find_ffmpeg()
    body = {
        "status": "ok" if exe else "degraded",
        "ffmpeg": {"found": bool(exe), "source": source},
        "voice_backends": tts.available(),
        "default_backend": default_backend(),
        "max_seconds": max_seconds(),
    }
    return JSONResponse(body, status_code=200 if exe else 503)


@app.post("/render", status_code=201, dependencies=[Depends(require_key)])
def render(req: RenderRequest, request: Request):
    backend = (req.voice.backend if req.voice and req.voice.backend else default_backend())
    voice_id = req.voice.voice_id if req.voice else None
    try:
        tts.check(backend, voice_id)
    except tts.BackendUnavailable as e:
        raise HTTPException(422, str(e))
    if not media.find_ffmpeg()[0]:
        raise HTTPException(503, "ffmpeg not found on this service")

    segs = segments_of(req.script)
    limit = max_seconds()
    if backend == "none":
        est = media.total_seconds([media.estimate_seconds(s.spoken) for s in segs])
        if est > limit:
            raise HTTPException(422, f"script would run {est:.1f}s; the limit is {limit:g}s (MAX_SECONDS)")

    slots = render_slots()
    if not slots.acquire(blocking=False):
        raise HTTPException(429, "too many renders in progress; retry shortly")
    t0 = time.monotonic()
    try:
        purge_old()
        return _render(req, request, segs, backend, voice_id, limit, t0)
    finally:
        slots.release()


def _render(req, request, segs, backend, voice_id, limit, t0):
    brand = req.brand or Brand()
    style = req.style or Style()
    out_dir = videos_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    (data_dir() / "tmp").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=data_dir() / "tmp") as tmpname:
        tmp = Path(tmpname)
        # 1. Voice (or estimates) -> durations.
        clips: list[Path | None] = []
        durations: list[float] = []
        for i, seg in enumerate(segs):
            if backend == "none":
                clips.append(None)
                durations.append(media.estimate_seconds(seg.spoken))
                continue
            wav = tmp / f"voice{i:02d}.wav"
            try:
                tts.synthesize(backend, seg.spoken, wav, voice_id)
                durations.append(media.voiced_seconds(media.wav_seconds(wav)))
            except Exception as e:
                log.warning("tts failed on segment %d: %s", i, e)
                raise HTTPException(422, f"voice backend {backend!r} failed on segment {i + 1}: "
                                         f"{type(e).__name__}")
            clips.append(wav)
        total = media.total_seconds(durations)
        if total > limit:
            raise HTTPException(422, f"video would run {total:.1f}s; the limit is {limit:g}s (MAX_SECONDS)")
        at = media.starts(durations)

        # 2. Frames.
        beat_count = sum(1 for s in segs if s.kind == "beat")
        paths, beat_no = [], 0
        poster = None
        for i, seg in enumerate(segs):
            if seg.kind == "beat":
                beat_no += 1
            img = frames.render_frame(
                seg, i, len(segs), beat_no if seg.kind == "beat" else None, beat_count,
                brand_name=brand.name, logo_text=brand.logo_text, accent=brand.accent,
                theme=style.theme, show_shot=style.show_shots)
            p = tmp / f"frame{i:02d}.png"
            img.save(p, "PNG", compress_level=1)
            paths.append(p)
            if i == 0:
                poster = img

        # 3. Audio track (silent when no voice) and encode.
        track = tmp / "track.wav"
        media.build_track(track, total, [(at[i], c) for i, c in enumerate(clips) if c is not None])
        mp4 = tmp / "out.mp4"
        try:
            media.encode(paths, durations, track, mp4, timeout=env_float("FFMPEG_TIMEOUT", 180))
        except media.FFmpegMissing as e:
            raise HTTPException(503, str(e))
        except media.RenderFailed as e:
            log.error("render failed: %s", e)
            raise HTTPException(500, "video encoding failed")

        video_id = secrets.token_hex(16)
        jpg_tmp = tmp / "poster.jpg"
        poster.save(jpg_tmp, "JPEG", quality=85)
        size = mp4.stat().st_size
        # Same filesystem (DATA_DIR), so these renames are atomic: never serve a half-written file.
        os.replace(jpg_tmp, out_dir / f"{video_id}.jpg")
        os.replace(mp4, out_dir / f"{video_id}.mp4")

    base = public_base(request)
    return {
        "id": video_id,
        "url": f"{base}/videos/{video_id}.mp4",
        "poster_url": f"{base}/videos/{video_id}.jpg",
        "duration_s": total,
        "width": frames.WIDTH,
        "height": frames.HEIGHT,
        "fps": media.FPS,
        "size_bytes": size,
        "voice_backend": backend,
        "render_ms": int((time.monotonic() - t0) * 1000),
        "segments": [
            {"index": i, "kind": s.kind, "on_screen": s.on_screen, "spoken": s.spoken,
             "start_s": at[i], "duration_s": round(durations[i], 3)}
            for i, s in enumerate(segs)
        ],
    }


def _stored(video_id: str, ext: str) -> Path:
    if not VIDEO_ID.fullmatch(video_id):
        raise HTTPException(404, "video not found")
    path = videos_dir() / f"{video_id}.{ext}"
    if not path.is_file():
        raise HTTPException(404, "video not found")
    return path


CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}


@app.get("/videos/{video_id}.mp4", response_class=FileResponse,
         responses={200: {"content": {"video/mp4": {}}}})
def get_video(video_id: str):
    return FileResponse(_stored(video_id, "mp4"), media_type="video/mp4", headers=CACHE)


@app.get("/videos/{video_id}.jpg", response_class=FileResponse,
         responses={200: {"content": {"image/jpeg": {}}}})
def get_poster(video_id: str):
    return FileResponse(_stored(video_id, "jpg"), media_type="image/jpeg", headers=CACHE)
