import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from youtube_transcript_api import NoTranscriptFound, RequestBlocked, TranscriptsDisabled

from app import net, youtube
from app.main import app

client = TestClient(app)
VID = "dQw4w9WgXcQ"
OEMBED = "https://www.youtube.com/oembed"


class Snip:
    def __init__(self, text, start, duration=2.0):
        self.text, self.start, self.duration = text, start, duration


class Fetched(list):
    def __init__(self, snippets, language_code):
        super().__init__(snippets)
        self.language_code = language_code


class Track:
    def __init__(self, code, snippets):
        self.language_code, self.snippets = code, snippets

    def fetch(self):
        return Fetched(self.snippets, self.language_code)


class Tracks:
    def __init__(self, tracks):
        self.tracks = tracks

    def find_transcript(self, langs):
        for code in langs:
            for t in self.tracks:
                if t.language_code == code:
                    return t
        raise NoTranscriptFound(VID, langs, self)

    def __iter__(self):
        return iter(self.tracks)


def fake_api(result, calls=None):
    """A stand-in for YouTubeTranscriptApi: `list()` returns `result`, or raises it."""
    class Api:
        def __init__(self, http_client=None):
            assert http_client is not None and http_client.trust_env is False

        def list(self, vid):
            if calls is not None:
                calls.append(vid)
            if isinstance(result, Exception):
                raise result
            return result
    return Api


SNIPPETS = [
    Snip("Welcome to the show.", 0.0), Snip("[Music]", 3.0), Snip("Today we talk pricing", 10.0),
    Snip("and how to set it.", 21.0),  # sentence end after 20 s: paragraph closes
    Snip("second   paragraph\nstarts here", 25.0), Snip("no punctuation at all", 40.0),
    Snip("still going", 56.0),  # 31 s after the paragraph began: hard break
    Snip("last words.", 60.0, 5.0),
]


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.delenv("TRANSCRIPT_LANGS", raising=False)
    monkeypatch.setattr(net, "resolve", lambda host: ["142.250.0.1"])


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(youtube, "YouTubeTranscriptApi", fake_api(Tracks([Track("en", SNIPPETS)]), seen))
    return seen


def mock_oembed(**json):
    return respx.get(OEMBED).mock(return_value=httpx.Response(200, json=json or {"title": "Pricing 101", "author_name": "Acme"}))


@respx.mock
@pytest.mark.parametrize("url", [
    f"https://www.youtube.com/watch?v={VID}",
    f"https://youtube.com/watch?v={VID}&t=42s&list=PL123",
    f"https://m.youtube.com/watch?v={VID}",
    f"https://youtu.be/{VID}",
    f"https://youtu.be/{VID}?si=abc",
    f"https://www.youtube.com/shorts/{VID}",
    f"http://www.youtube.com/embed/{VID}",
])
def test_youtube_forms_return_transcript(url, calls):
    route = mock_oembed()
    r = client.post("/extract", json={"url": url})
    assert r.status_code == 200, r.text
    body = r.json()
    assert calls == [VID]
    assert body["url"] == f"https://www.youtube.com/watch?v={VID}"
    assert body["title"] == "Pricing 101"
    assert body["description"] == "YouTube video by Acme"
    assert body["source"] == "youtube_transcript"
    assert body["language"] == body["lang"] == "en"
    assert body["duration_s"] == 65
    assert body["video_id"] == VID
    assert body["text"].split("\n\n") == [
        "Welcome to the show. Today we talk pricing and how to set it.",
        "second paragraph starts here no punctuation at all still going",
        "last words.",
    ]
    assert body["word_count"] == len(body["text"].split())
    # same keys as a page extraction, so wf30/wf31 need no change
    for key in ("headings", "links", "og"):
        assert key in body
    assert f"url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3D{VID}" in str(route.calls.last.request.url)


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=short",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQX",      # 12 chars
    "https://www.youtube.com/watch?v=dQw4w9WgX%3Fc",     # decodes to a '?'
    "https://www.youtube.com/watch?v=../../etc/pas",
    "https://www.youtube.com/watch",
    f"https://www.youtube.com/watch?v={VID}&v=aaaaaaaaaaa",  # ambiguous
    "https://youtu.be/",
    f"https://youtu.be/{VID}/extra",
    "https://www.youtube.com/shorts/abc",
])
def test_invalid_video_ids_are_rejected(url, calls):
    r = client.post("/extract", json={"url": url})
    assert r.status_code == 422
    assert "valid YouTube video URL" in r.json()["detail"]
    assert calls == []


@pytest.mark.parametrize("exc", [TranscriptsDisabled(VID), NoTranscriptFound(VID, ["en"], None)])
@respx.mock
def test_no_transcript_is_422(monkeypatch, exc):
    monkeypatch.setattr(youtube, "YouTubeTranscriptApi", fake_api(exc))
    r = client.post("/extract", json={"url": f"https://youtu.be/{VID}"})
    assert r.status_code == 422
    assert r.json() == {"detail": "no transcript available for this video (captions off)"}


def test_no_tracks_at_all_is_422(monkeypatch):
    monkeypatch.setattr(youtube, "YouTubeTranscriptApi", fake_api(Tracks([])))
    r = client.post("/extract", json={"url": f"https://youtu.be/{VID}"})
    assert r.status_code == 422
    assert "captions off" in r.json()["detail"]


def test_blocked_by_youtube_is_502(monkeypatch):
    monkeypatch.setattr(youtube, "YouTubeTranscriptApi", fake_api(RequestBlocked(VID)))
    r = client.post("/extract", json={"url": f"https://youtu.be/{VID}"})
    assert r.status_code == 502
    assert "blocked" in r.json()["detail"]


@respx.mock
def test_language_preference_and_fallback(monkeypatch):
    mock_oembed()
    tracks = Tracks([Track("de", [Snip("Hallo.", 0)]), Track("fr", [Snip("Bonjour.", 0)])])
    monkeypatch.setattr(youtube, "YouTubeTranscriptApi", fake_api(tracks))
    monkeypatch.setenv("TRANSCRIPT_LANGS", "es, fr")
    body = client.post("/extract", json={"url": f"https://youtu.be/{VID}"}).json()
    assert (body["language"], body["text"]) == ("fr", "Bonjour.")
    monkeypatch.setenv("TRANSCRIPT_LANGS", "en")  # not available: first track, whatever it is
    body = client.post("/extract", json={"url": f"https://youtu.be/{VID}"}).json()
    assert (body["language"], body["text"]) == ("de", "Hallo.")


@respx.mock
def test_long_transcript_is_truncated(monkeypatch):
    mock_oembed()
    long = [Snip("word " * 50, i * 5.0) for i in range(200)]  # 10,000 words
    monkeypatch.setattr(youtube, "YouTubeTranscriptApi", fake_api(Tracks([Track("en", long)])))
    body = client.post("/extract", json={"url": f"https://youtu.be/{VID}"}).json()
    assert len(body["text"]) == 20_000
    assert body["word_count"] == 10_000


@respx.mock
def test_oembed_failure_leaves_title_empty(calls):
    respx.get(OEMBED).mock(return_value=httpx.Response(404))
    body = client.post("/extract", json={"url": f"https://youtu.be/{VID}"}).json()
    assert body["title"] is None and body["description"] is None
    assert body["text"].startswith("Welcome")


@respx.mock
def test_ssrf_guard_applies_to_oembed(monkeypatch, calls):
    monkeypatch.setattr(net, "resolve", lambda host: ["127.0.0.1"])  # poisoned DNS for youtube.com
    route = mock_oembed()
    r = client.post("/extract", json={"url": f"https://youtu.be/{VID}"})
    assert r.status_code == 200
    assert r.json()["title"] is None
    assert not route.called


@respx.mock
@pytest.mark.parametrize("url", [
    "https://www.youtube.com/@acme",                  # a channel page, not a video
    "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com:8443/watch?v=dQw4w9WgXcQ",
])
def test_non_video_urls_are_extracted_as_pages(url, calls):
    respx.get(url).mock(return_value=httpx.Response(200, html="<title>Plain page</title><p>hi</p>"))
    body = client.post("/extract", json={"url": url}).json()
    assert body["title"] == "Plain page"
    assert "source" not in body
    assert calls == []
