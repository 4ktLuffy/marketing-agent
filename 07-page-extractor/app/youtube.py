"""YouTube URLs: the transcript (captions) instead of the page HTML, which holds no readable text."""
import json
import os
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlsplit

import requests
from youtube_transcript_api import (
    CouldNotRetrieveTranscript,
    InvalidVideoId,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)

from app import net

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
WATCH_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
PATH_FORMS = ("shorts", "embed", "live")  # youtube.com/<form>/<id>
TIMEOUT = 15.0
# A paragraph closes at a sentence end once it spans PARA_SOFT seconds, and always at PARA_HARD
# (auto-generated captions often have no punctuation at all).
PARA_SOFT, PARA_HARD = 20.0, 30.0
NOISE = re.compile(r"^\[(music|applause|laughter|silence|inaudible)\]$", re.I)

NO_TRANSCRIPT = "no transcript available for this video (captions off)"


class NotYouTube(Exception):
    """The URL is not a YouTube video URL: extract it as a normal page."""


class BadVideoURL(ValueError):
    """A YouTube URL whose video id is missing or malformed."""


class TranscriptUnavailable(Exception):
    """The video exists but has no usable transcript (422)."""


class TranscriptFetchError(Exception):
    """YouTube could not be reached or refused us (502)."""


@dataclass
class Transcript:
    snippets: list[tuple[str, float, float]]  # (text, start_s, duration_s)
    language: str


def video_id(url: str) -> str:
    """The 11-character video id of a YouTube video URL.

    Raises NotYouTube for anything that is not a YouTube host (or a YouTube page that is not a
    video, like a channel), and BadVideoURL for a video URL with a malformed id.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise NotYouTube
    if parts.port not in (None, 80, 443) or parts.username or parts.password:
        raise NotYouTube
    host = parts.hostname.lower()
    segs = [s for s in parts.path.split("/") if s]
    if host in SHORT_HOSTS:
        candidate = segs[0] if len(segs) == 1 else None
    elif host in WATCH_HOSTS:
        if segs == ["watch"]:
            vs = parse_qs(parts.query).get("v", [])
            candidate = vs[0] if len(vs) == 1 else None
        elif len(segs) in (1, 2) and segs[0] in PATH_FORMS:
            candidate = segs[1] if len(segs) == 2 else None
        else:
            raise NotYouTube  # channel, playlist, search, home: a normal page
    else:
        raise NotYouTube
    if candidate is None or not VIDEO_ID.fullmatch(candidate):
        raise BadVideoURL("not a valid YouTube video URL (the video id must be 11 characters: A-Z a-z 0-9 _ -)")
    return candidate


def watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


class _Session(requests.Session):
    """requests has no default timeout, and must not pick up proxies from the environment."""

    def __init__(self):
        super().__init__()
        self.trust_env = False
        self.headers["User-Agent"] = net.USER_AGENT

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        return super().request(*args, **kwargs)


def languages() -> list[str]:
    langs = [x.strip() for x in os.getenv("TRANSCRIPT_LANGS", "en").split(",") if x.strip()]
    return langs or ["en"]


def fetch_transcript(vid: str, langs: list[str]) -> Transcript:
    """Captions in the first preferred language (manual before auto-generated), else any track.

    The library only ever talks to youtube.com, with the validated id, so it needs no SSRF check.
    """
    api = YouTubeTranscriptApi(http_client=_Session())
    try:
        tracks = api.list(vid)
        try:
            track = tracks.find_transcript(langs)
        except NoTranscriptFound:
            track = next(iter(tracks), None)  # manual tracks first, then auto-generated
            if track is None:
                raise TranscriptUnavailable(NO_TRANSCRIPT)
        fetched = track.fetch()
    except (TranscriptsDisabled, NoTranscriptFound):
        raise TranscriptUnavailable(NO_TRANSCRIPT)
    except (VideoUnavailable, InvalidVideoId):
        raise TranscriptUnavailable("this YouTube video is unavailable (private, removed or wrong id)")
    except RequestBlocked:
        raise TranscriptFetchError("YouTube blocked the transcript request from this server's IP address")
    except CouldNotRetrieveTranscript as exc:
        raise TranscriptFetchError(f"could not get the transcript: {type(exc).__name__}")
    except requests.RequestException as exc:
        raise TranscriptFetchError(f"could not reach YouTube: {type(exc).__name__}")
    snippets = [(s.text, float(s.start), float(s.duration)) for s in fetched]
    return Transcript(snippets, fetched.language_code)


def paragraphs(snippets: list[tuple[str, float, float]]) -> list[str]:
    """Join caption snippets into paragraphs of roughly 30 seconds, preferring sentence ends."""
    paras, cur, start = [], [], None
    for text, at, _ in snippets:
        text = " ".join(text.split())
        if not text or NOISE.match(text):
            continue
        if start is None:
            start = at
        cur.append(text)
        span = at - start
        if span >= PARA_HARD or (span >= PARA_SOFT and text.endswith((".", "!", "?"))):
            paras.append(" ".join(cur))
            cur, start = [], None
    if cur:
        paras.append(" ".join(cur))
    return paras


def oembed(vid: str) -> dict:
    """Title and channel from YouTube's oEmbed, through the SSRF-guarded client. {} on any failure."""
    url = f"https://www.youtube.com/oembed?url={quote(watch_url(vid), safe='')}&format=json"
    try:
        data = json.loads(net.fetch(url, html_only=False).content)
    except (net.BlockedURL, net.FetchError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def extract_video(vid: str, max_text: int) -> dict:
    """Same shape as a page extraction, plus source/language/duration_s/video_id."""
    tr = fetch_transcript(vid, languages())
    text = "\n\n".join(paragraphs(tr.snippets))
    if not text:
        raise TranscriptUnavailable(NO_TRANSCRIPT)
    meta = oembed(vid)
    title = str(meta.get("title") or "").strip() or None
    author = str(meta.get("author_name") or "").strip()
    duration = max((at + d for _, at, d in tr.snippets), default=0.0)
    return {
        "url": watch_url(vid),
        "title": title,
        "description": f"YouTube video by {author}" if author else None,
        "lang": tr.language,
        "headings": [],
        "text": text[:max_text],
        "word_count": len(text.split()),
        "links": {"internal": 0, "external": 0},
        "og": {},
        "source": "youtube_transcript",
        "language": tr.language,
        "duration_s": round(duration) if duration else None,
        "video_id": vid,
    }
