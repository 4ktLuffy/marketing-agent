"""Clip finder: one long video (a direct media link or an upload) -> short vertical clips.

Transcribe locally (faster-whisper, word timestamps) -> sentence windows of min_s..max_s built
in code -> the LLM gateway scores each window (prompt clip_scoring) -> the best
non-overlapping windows, snapped to scene cuts and silences -> 1080x1920 MP4s with burned-in
word-by-word captions, a poster and an SRT. Fully local; one job at a time, in the background.

It never downloads from video platforms (their terms forbid it): a YouTube/TikTok/Instagram
link gets a 422 asking for the file itself.
"""
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import captions, media, net, scenes, scoring, transcribe, windows

log = logging.getLogger("clip-finder")


@asynccontextmanager
async def lifespan(_app):
    startup()
    yield


app = FastAPI(title="clip-finder", lifespan=lifespan)

# Job and clip ids: 32 lower-case hex characters. Nothing else is ever turned into a path.
ID = re.compile(r"[0-9a-f]{32}")
UPLOAD_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_busy = threading.Lock()          # one job at a time
_jobs_lock = threading.Lock()     # job file writes

# Video platforms whose terms forbid downloading: we ask for the file instead.
PLATFORM_HOSTS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com", "tiktok.com", "instagram.com",
    "facebook.com", "fb.watch", "fb.com", "x.com", "twitter.com", "vimeo.com", "twitch.tv",
    "linkedin.com", "snapchat.com", "reddit.com", "redd.it", "dailymotion.com", "threads.net",
    "pinterest.com", "rumble.com", "kick.com", "bilibili.com", "loom.com",
)
PLATFORM_MESSAGE = ("links to video platforms are not downloaded (their terms forbid it): "
                    "download the video yourself and upload it, or give a direct link to the media file")
REFRAME_MODES = ("center", "face", "blur")


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data"))


def jobs_dir() -> Path:
    return data_dir() / "jobs"


def clips_dir() -> Path:
    return data_dir() / "clips"


def tmp_dir() -> Path:
    return data_dir() / "tmp"


def max_bytes() -> int:
    return int(env_float("MAX_UPLOAD_MB", 1024) * 1024 * 1024)


def max_source_s() -> float:
    return env_float("MAX_SOURCE_MINUTES", 90) * 60


def max_clips_cap() -> int:
    return max(1, int(env_float("MAX_CLIPS", 10)))


def ffmpeg_timeout() -> float:
    return env_float("FFMPEG_TIMEOUT", 600)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- request


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str | None = Field(None, max_length=2000)
    max_clips: int = Field(5, ge=1)
    min_s: float = Field(20, ge=5, le=120)
    max_s: float = Field(60, ge=10, le=180)
    language: str | None = Field(None, pattern=r"^[a-z]{2,3}$")
    reframe: Literal["center", "face", "blur"] = "blur"  # centre crop cut slide text in the real run

    @field_validator("url")
    @classmethod
    def strip(cls, v):
        return v.strip() if v else None

    @model_validator(mode="after")
    def lengths(self):
        if self.max_s < self.min_s + 5:
            raise ValueError("max_s must be at least min_s + 5")
        if self.max_clips > max_clips_cap():
            raise ValueError(f"max_clips is at most {max_clips_cap()} (MAX_CLIPS)")
        return self


def is_platform_link(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in PLATFORM_HOSTS)


def check_source_url(url: str) -> None:
    """422 for anything that is not an http(s) link we may download; SSRF-checked later on
    every hop by net.download."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(422, "url must be an http(s) link to an audio or video file")
    if is_platform_link(url):
        raise HTTPException(422, PLATFORM_MESSAGE)
    try:
        net.check_url(url)
    except net.BlockedURL as e:
        raise HTTPException(422, str(e))
    except net.FetchError as e:
        raise HTTPException(422, str(e))


def display_url(url: str) -> str:
    """The source URL without query or fragment (signed links carry tokens there)."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc.rsplit("@", 1)[-1], p.path, "", ""))


# ---------- helpers


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


def public_base(request: Request) -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)).strip().rstrip("/")


def save_job(job: dict) -> None:
    jobs_dir().mkdir(parents=True, exist_ok=True)
    with _jobs_lock:
        path = jobs_dir() / f"{job['id']}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(job))
        os.replace(tmp, path)  # never a half-written job file


def load_job(job_id: str) -> dict | None:
    if not ID.fullmatch(job_id):
        return None
    path = jobs_dir() / f"{job_id}.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def purge_old(now: float | None = None) -> int:
    """Delete clips and job records older than RETENTION_DAYS (0 or unset = keep forever)."""
    days = env_float("RETENTION_DAYS", 0)
    if days <= 0:
        return 0
    cutoff = (now or time.time()) - days * 86400
    removed = 0
    for folder, exts in ((clips_dir(), (".mp4", ".jpg", ".srt")), (jobs_dir(), (".json",))):
        if not folder.is_dir():
            continue
        for p in folder.iterdir():
            if p.suffix in exts and ID.fullmatch(p.stem) and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                removed += 1
    return removed


def startup():
    try:
        if tmp_dir().is_dir():
            shutil.rmtree(tmp_dir(), ignore_errors=True)  # left over from a killed job
        if jobs_dir().is_dir():  # a job that was running when the service stopped never finishes
            for p in jobs_dir().glob("*.json"):
                job = load_job(p.stem)
                if job and job.get("status") in ("queued", "running"):
                    job.update(status="failed", error="the service restarted during this job",
                               finished_at=now_iso())
                    save_job(job)
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
        "whisper": {"installed": transcribe.available(), "model": transcribe.model_name()},
        "scene_detection": scenes.scenes_available(),
        "busy": _busy.locked(),
        "limits": {"max_upload_mb": env_float("MAX_UPLOAD_MB", 1024),
                   "max_source_minutes": env_float("MAX_SOURCE_MINUTES", 90), "max_clips": max_clips_cap()},
    }
    return JSONResponse(body, status_code=200 if exe else 503)


def _form_params(form) -> dict:
    out = {}
    for k in ("url", "max_clips", "min_s", "max_s", "language", "reframe"):
        v = form.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    extra = set(form.keys()) - {"url", "max_clips", "min_s", "max_s", "language", "reframe", "file"}
    if extra:
        raise HTTPException(422, f"unknown form fields: {', '.join(sorted(extra))}")
    return out


def _validation_error(e: ValidationError) -> HTTPException:
    return HTTPException(422, [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors(include_url=False)])


@app.post("/jobs", status_code=202, dependencies=[Depends(require_key)])
async def create_job(request: Request):
    """JSON {url, max_clips, min_s, max_s, language?, reframe?} or multipart with `file`."""
    if not media.find_ffmpeg()[0]:
        raise HTTPException(503, "ffmpeg not found on this service")
    ctype = request.headers.get("content-type", "").lower()
    length = int(request.headers.get("content-length") or 0)
    if length > max_bytes() + 1024 * 1024:
        raise HTTPException(413, f"upload larger than {env_float('MAX_UPLOAD_MB', 1024):g} MB (MAX_UPLOAD_MB)")
    # Busy check first, so a second upload is refused before its body is read.
    if not _busy.acquire(blocking=False):
        raise HTTPException(429, "a job is already running; retry when it has finished")
    started = False
    work = None
    try:
        job_id = secrets.token_hex(16)
        work = tmp_dir() / job_id
        work.mkdir(parents=True, exist_ok=True)
        upload_name = None
        if ctype.startswith("multipart/form-data"):
            form = await request.form(max_files=1, max_fields=10)
            try:
                params = _form_params(form)
                upload = form.get("file")
                if upload is not None and not isinstance(upload, str):
                    upload_name = UPLOAD_NAME.sub("_", (upload.filename or "upload"))[-80:] or "upload"
                    got = 0
                    with open(work / "source", "wb") as f:
                        while chunk := await upload.read(1024 * 1024):
                            got += len(chunk)
                            if got > max_bytes():
                                raise HTTPException(413, "upload larger than MAX_UPLOAD_MB")
                            f.write(chunk)
                    if got == 0:
                        raise HTTPException(422, "the uploaded file is empty")
            finally:
                await form.close()
        else:
            try:
                params = await request.json()
            except ValueError:
                raise HTTPException(422, "send JSON {url, ...} or multipart/form-data with a file")
            if not isinstance(params, dict):
                raise HTTPException(422, "body must be a JSON object")
        try:
            req = JobRequest(**params)
        except ValidationError as e:
            raise _validation_error(e)
        except TypeError:
            raise HTTPException(422, "invalid body")
        if upload_name and req.url:
            raise HTTPException(422, "send either a url or a file, not both")
        if not upload_name and not req.url:
            raise HTTPException(422, "send a url to a video/audio file, or upload one as `file`")
        if req.url:
            check_source_url(req.url)

        purge_old()
        job = {
            "id": job_id, "status": "queued", "stage": "queued", "progress": 0.0,
            "created_at": now_iso(), "started_at": None, "finished_at": None,
            "source": {"kind": "url" if req.url else "upload",
                       "name": display_url(req.url) if req.url else upload_name},
            "params": {"max_clips": req.max_clips, "min_s": req.min_s, "max_s": req.max_s,
                       "language": req.language, "reframe": req.reframe},
            "timings_ms": {}, "notes": [], "error": None, "clips": [],
        }
        save_job(job)
        start_background(run_job, job, req.url, work)
        started = True
        return {"id": job_id, "status": "queued",
                "status_url": f"{public_base(request)}/jobs/{job_id}"}
    finally:
        if not started:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
            _busy.release()


def start_background(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True, name="clip-job").start()


def with_urls(job: dict, base: str) -> dict:
    out = dict(job)
    out["clips"] = [dict(c, url=f"{base}/clips/{c['id']}.mp4", poster_url=f"{base}/clips/{c['id']}.jpg",
                         srt_url=f"{base}/clips/{c['id']}.srt") for c in job.get("clips", [])]
    return out


@app.get("/jobs/{job_id}", dependencies=[Depends(require_key)])
def get_job(job_id: str, request: Request):
    job = load_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return with_urls(job, public_base(request))


def _stored(clip_id: str, ext: str) -> Path:
    if not ID.fullmatch(clip_id):
        raise HTTPException(404, "clip not found")
    path = clips_dir() / f"{clip_id}.{ext}"
    if not path.is_file():
        raise HTTPException(404, "clip not found")
    return path


CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}


@app.get("/clips/{clip_id}.mp4", response_class=FileResponse, responses={200: {"content": {"video/mp4": {}}}})
def get_clip(clip_id: str):
    return FileResponse(_stored(clip_id, "mp4"), media_type="video/mp4", headers=CACHE)


@app.get("/clips/{clip_id}.jpg", response_class=FileResponse, responses={200: {"content": {"image/jpeg": {}}}})
def get_poster(clip_id: str):
    return FileResponse(_stored(clip_id, "jpg"), media_type="image/jpeg", headers=CACHE)


@app.get("/clips/{clip_id}.srt", response_class=FileResponse,
         responses={200: {"content": {"application/x-subrip": {}}}})
def get_srt(clip_id: str):
    return FileResponse(_stored(clip_id, "srt"), media_type="application/x-subrip; charset=utf-8", headers=CACHE)


# ---------- the pipeline (runs in a background thread; one at a time)


class JobFailed(Exception):
    pass


STAGES = {  # stage -> (progress at start, progress at end)
    "download": (0.0, 0.08), "audio": (0.08, 0.1), "transcribe": (0.1, 0.6), "scenes": (0.6, 0.65),
    "score": (0.65, 0.8), "render": (0.8, 1.0),
}


def run_job(job: dict, url: str | None, work: Path) -> None:
    t_job = time.monotonic()
    last_save = [0.0]

    def stage(name: str):
        job.update(stage=name, progress=round(STAGES[name][0], 3))
        save_job(job)

    def progress(name: str):
        lo, hi = STAGES[name]

        def cb(frac: float):
            job["progress"] = round(lo + (hi - lo) * max(0.0, min(1.0, frac)), 3)
            if time.monotonic() - last_save[0] > 1.0:
                last_save[0] = time.monotonic()
                save_job(job)
        return cb

    def timed(name: str, fn, *a, **kw):
        t0 = time.monotonic()
        try:
            return fn(*a, **kw)
        finally:
            job["timings_ms"][name] = int((time.monotonic() - t0) * 1000)

    try:
        job.update(status="running", started_at=now_iso())
        src = work / "source"
        if url:
            stage("download")
            try:
                got = timed("download", net.download, url, src, max_bytes(),
                            timeout=env_float("DOWNLOAD_TIMEOUT", 60), progress=progress("download"))
            except net.BlockedURL as e:
                raise JobFailed(f"blocked url: {e}")
            except net.FetchError as e:
                raise JobFailed(f"download failed: {e}")
            job["source"]["bytes"] = got["bytes"]
        else:
            job["source"]["bytes"] = src.stat().st_size
        info = media.probe(src)
        dur = info.get("duration_s")
        if not dur or not info.get("has_audio"):
            raise JobFailed("not a playable audio/video file with a sound track")
        if dur > max_source_s():
            raise JobFailed(f"source runs {dur / 60:.1f} min; the limit is {max_source_s() / 60:g} min "
                            "(MAX_SOURCE_MINUTES)")
        p = job["params"]
        if dur < p["min_s"]:
            raise JobFailed(f"source runs {dur:.0f} s, shorter than min_s ({p['min_s']:g} s)")
        job["source"].update(duration_s=round(dur, 3), has_video=info["has_video"],
                             width=info["width"], height=info["height"])

        stage("audio")
        wav = work / "audio.wav"
        timed("audio", media.extract_audio, src, wav, ffmpeg_timeout())
        sil = timed("silences", media.silences, wav, ffmpeg_timeout())

        stage("transcribe")
        try:
            words, tinfo = timed("transcribe", transcribe.transcribe, str(wav), p["language"],
                                 progress=progress("transcribe"))
        except transcribe.TranscribeUnavailable as e:
            raise JobFailed(str(e))
        job["transcript"] = {"words": len(words), **tinfo}
        if not words:
            raise JobFailed("no speech found in the source")

        cands = windows.thin(windows.build_windows(words, p["min_s"], p["max_s"]),
                             stride_s=env_float("WINDOW_STRIDE_S", 8), cap=int(env_float("MAX_CANDIDATES", 48)))
        job["candidates"] = len(cands)
        if not cands:
            raise JobFailed(f"no stretch of whole sentences lasts {p['min_s']:g}-{p['max_s']:g} s")

        stage("scenes")
        cuts: list[float] = []
        if info["has_video"] and scenes.scenes_available():
            try:
                cuts = timed("scenes", scenes.detect_cuts, src)
            except Exception as e:  # scene cuts only refine boundaries
                job["notes"].append(f"scene detection failed ({type(e).__name__}); snapped to silences only")
        elif info["has_video"]:
            job["notes"].append("PySceneDetect not installed; snapped to silences only")
        job["scene_cuts"] = len(cuts)

        stage("score")
        try:
            stats = timed("score", scoring.score, cands, int(env_float("SCORE_BATCH", 6)),
                          env_float("GATEWAY_TIMEOUT", 300), p["language"], progress=progress("score"))
        except scoring.ScoringFailed as e:
            raise JobFailed(f"scoring failed: {e}")
        job["scoring"] = {k: stats[k] for k in ("batches", "failed_batches", "scored")}
        if stats["failed_batches"]:
            job["notes"].append(f"{stats['failed_batches']} scoring batch(es) failed; those windows were skipped")
        chosen = windows.pick(cands, p["max_clips"], env_float("MIN_SCORE", 0))

        stage("render")
        mode = p["reframe"]
        if mode == "face" and info["has_video"] and not scenes.faces_available():
            job["notes"].append("face reframing unavailable here (MediaPipe face detector not installed); "
                                "used the blurred-background fit")
            mode = "blur"   # keeps slides and side-by-side speakers whole; the centre crop cut them
        fonts_dir, font_name = captions.caption_font()
        t0 = time.monotonic()
        clips_dir().mkdir(parents=True, exist_ok=True)
        # Snapping may add up to SNAP_S on each side, but a clip never runs past max_s + 1 s.
        bounds = windows.snap_all(chosen, words, cuts, sil, info["duration_s"], p["max_s"] + 1.0)
        for n, (w, bound) in enumerate(zip(chosen, bounds)):
            job["clips"].append(render_one(n, w, bound, words, info, mode, src, work, fonts_dir, font_name))
            progress("render")((n + 1) / len(chosen))
        job["timings_ms"]["render"] = int((time.monotonic() - t0) * 1000)
        job.update(status="done", stage="done", progress=1.0)
    except JobFailed as e:
        job.update(status="failed", error=str(e))
    except (media.FFmpegMissing, media.RenderFailed) as e:
        log.error("job %s: %s", job["id"], e)
        job.update(status="failed", error=f"video processing failed: {str(e)[:300]}")
    except Exception as e:  # never leave a job 'running'
        log.exception("job %s crashed", job["id"])
        job.update(status="failed", error=f"internal error: {type(e).__name__}")
    finally:
        job["timings_ms"]["total"] = int((time.monotonic() - t_job) * 1000)
        job["finished_at"] = now_iso()
        save_job(job)
        shutil.rmtree(work, ignore_errors=True)
        _busy.release()


def render_one(n: int, w: windows.Window, bound: tuple, words, info, mode, src, work, fonts_dir,
               font_name) -> dict:
    start, end, how = bound
    dur = end - start
    lines = captions.lines(words, start, end)
    ass_file = work / f"clip{n}.ass"
    ass_file.write_text(captions.ass(lines, font_name), encoding="utf-8")
    center_x = None
    vf = None
    if info["has_video"]:
        if mode == "face":
            times = [start + dur * (k + 0.5) / 8 for k in range(8)]
            stills = media.frames_at(src, times, work, 480, ffmpeg_timeout())
            center_x = scenes.face_center_x(stills)
            for s in stills:
                s.unlink(missing_ok=True)
        vf = media.video_filter("blur" if mode == "blur" else "center", info["width"], info["height"], center_x)
    clip_id = secrets.token_hex(16)
    mp4 = work / f"{clip_id}.mp4"
    jpg = work / f"{clip_id}.jpg"
    media.render_clip(src, start, dur, mp4, vf, ass_file, fonts_dir, ffmpeg_timeout())
    media.poster(mp4, jpg, min(1.0, dur / 2), ffmpeg_timeout())
    srt_tmp = work / f"{clip_id}.srt"
    srt_tmp.write_text(captions.srt(lines), encoding="utf-8")
    size = mp4.stat().st_size
    # Same filesystem (DATA_DIR), so these renames are atomic: never serve a half-written file.
    for f in (srt_tmp, jpg, mp4):
        os.replace(f, clips_dir() / f.name)
    return {
        "id": clip_id, "index": n + 1, "start_s": start, "end_s": end, "duration_s": round(dur, 3),
        "title": w.title, "hook": w.hook, "reason": w.reason, "score": w.score, "scores": w.scores,
        "transcript": w.text, "snapped": how,
        "reframe": ("face" if center_x is not None else ("center" if mode == "face" else mode))
        if info["has_video"] else "audio_only",
        "width": media.WIDTH, "height": media.HEIGHT, "size_bytes": size,
    }
