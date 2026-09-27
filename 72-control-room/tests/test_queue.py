import httpx

from app import views

from .conftest import CAL, ENGINE, HEX, URLS, item

CARD_URL = f"http://localhost:8117/cards/{HEX}.png"
VIDEO_URL = f"http://localhost:8171/videos/{HEX}.mp4"


def queue_items():
    return [
        item(1, channel="instagram", image_url=CARD_URL, hook_style="question", title="With image",
             notes="drafted by the engine drafter\nengine pillar #4 slot #9 atom #2 (tip)"),
        item(2, channel="video", video_url=VIDEO_URL, image_url=VIDEO_URL[:-4] + ".jpg", title="With video",
             body="Hook: Cold brew\nOn screen: COLD",
             notes=f"Quality gate: unsupported claim: They taste every sip.; unsupported claim: Best beans | video preview: {VIDEO_URL} (14.63 s)"),
        item(3, channel="x", title="No media", scheduled_at=None, link="https://example.com/p?utm_source=x",
             notes="needs a human: brand check failed: banned phrase\nwarnings: novelty checked without embeddings (weaker check)"),
        item(4, channel="linkedin", title="Long", body="word " * 80, scheduled_at="2026-09-30T08:00:00Z"),
    ]


def test_queue_renders_image_video_and_plain_items(authed, mock):
    c, _ = authed
    mock.get(f"{CAL}/items", params={"status": "in_review"}).respond(json=queue_items())
    mock.get(f"{ENGINE}/pillars").respond(json=[{"id": 4, "title": "Cold brew month"}])
    html = c.get("/").text
    # order: scheduled first (by time), unscheduled last
    assert html.index('id="card-4"') < html.index('id="card-1"') < html.index('id="card-3"')
    # image card through the control room, never the service's port
    assert f'src="/media/cards/{HEX}.png"' in html and "localhost:8117" not in html
    # video: player with poster through the proxy, the public link, the length from the notes
    assert f'src="/media/videos/{HEX}.mp4"' in html and f'poster="/media/videos/{HEX}.jpg"' in html
    assert "Watch the video (15 s)" in html
    # flags from the quality gate / claim checker / notes
    assert "They taste every sip" in html and "Best beans" in html and 'class="flag danger"' in html
    assert "brand check failed: banned phrase" in html and 'class="flag warn"' in html
    # hook style, pillar name, network styling, next-full-hour note, links
    assert "hook: question" in html and "pillar: Cold brew month" in html and "net-instagram" in html
    assert "no time: next full hour" in html and "https://example.com/p?utm_source=x" in html
    # LinkedIn "see more" fold on long text
    assert "…see more" in html
    # buttons, shortcuts, count
    assert html.count('data-act="approve"') == 4 and 'aria-keyshortcuts="a"' in html and 'class="count">4<' in html


def test_flagged_tab(authed, mock):
    c, _ = authed
    mock.get(f"{CAL}/items").respond(json=queue_items())
    mock.get(f"{ENGINE}/pillars").respond(json=[])
    html = c.get("/?tab=flagged").text
    assert 'id="card-2"' in html and 'id="card-3"' in html and 'id="card-1"' not in html and "Flagged (2)" in html


def test_empty_queue_and_calendar_down(authed, mock):
    c, _ = authed
    route = mock.get(f"{CAL}/items").respond(json=[])
    assert "Nothing waits for review" in c.get("/").text
    route.side_effect = httpx.ConnectError("down")
    html = c.get("/").text
    assert "The calendar did not answer" in html and "calendar.internal" not in html


def test_item_text_is_escaped(authed, mock):
    c, _ = authed
    mock.get(f"{CAL}/items").respond(json=[item(1, body='<script>alert(1)</script>', title='<img src=x onerror=1>',
                                                  image_url="javascript:alert(1)")])
    html = c.get("/").text
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html and "<img src=x" not in html
    assert "javascript:alert" not in html


def test_media_proxy_streams_with_range(authed, mock):
    c, _ = authed
    route = mock.get(f"{URLS['VIDEO_URL']}/videos/{HEX}.mp4").respond(
        206, content=b"abc", headers={"content-type": "video/mp4", "content-range": "bytes 0-2/10", "accept-ranges": "bytes"})
    r = c.get(f"/media/videos/{HEX}.mp4", headers={"Range": "bytes=0-2"})
    assert r.status_code == 206 and r.content == b"abc" and r.headers["content-range"] == "bytes 0-2/10"
    assert route.calls.last.request.headers["range"] == "bytes=0-2"
    assert "x-api-key" not in route.calls.last.request.headers
    mock.get(f"{URLS['CARDS_URL']}/cards/{HEX}.png").respond(200, content=b"\x89PNG", headers={"content-type": "image/png"})
    assert c.get(f"/media/cards/{HEX}.png").content == b"\x89PNG"


def test_media_proxy_refuses_other_paths(authed):
    c, _ = authed
    for p in ["/media/cards/../../etc.png", "/media/cards/xyz.png", "/media/other/abc.png", f"/media/videos/{HEX}.exe"]:
        assert c.get(p).status_code == 404, p


def test_media_needs_login(client):
    assert client.get(f"/media/cards/{HEX}.png").status_code == 401


def test_views_media_mapping_and_flags():
    assert views.media_src(CARD_URL) == f"/media/cards/{HEX}.png"
    assert views.media_src("https://cdn.example.com/a.png") == "https://cdn.example.com/a.png"
    assert views.media_src("ftp://x") is None and views.media_src(None) is None
    f = views.flags({"notes": "[2026-09-27T15:51:29Z] in_review -> draft: script edited: video could not be re-rendered (HTTP 422)"})
    assert f and f[0]["level"] == "danger"
    t = views.timeline({"notes": "drafted\n[2026-09-27T15:50:29Z] draft -> in_review: e2e"})
    assert t[1] == {"at": "2026-09-27T15:50:29Z", "change": "draft → in_review", "text": "e2e"}
