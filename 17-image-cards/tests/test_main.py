import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app

client = TestClient(app)


def png(r) -> Image.Image:
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(r.content))
    assert img.format == "PNG"
    return img


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("size,dims", [("og", (1200, 630)), ("square", (1080, 1080)), ("story", (1080, 1920))])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_card_dimensions(size, dims, theme):
    r = client.post("/card", json={
        "title": "Spring sale: 20% off every subscription", "subtitle": "This week only",
        "brand": "Acme Coffee", "size": size, "theme": theme,
    })
    assert png(r).size == dims


def test_long_title_and_unbreakable_word_still_render():
    title = ("Supercalifragilisticexpialidocious" * 4 + " ") + "word " * 20
    r = client.post("/card", json={"title": title[:140], "subtitle": "s " * 100, "size": "og"})
    assert png(r).size == (1200, 630)


def test_title_only_defaults():
    assert png(client.post("/card", json={"title": "Hi"})).size == (1200, 630)


@pytest.mark.parametrize("body", [
    {"title": "x", "size": "banner"},
    {"title": "x", "theme": "blue"},
    {"title": ""},
    {"title": "   "},
    {"title": "x" * 141},
    {"title": "x", "brand": "b" * 41},
])
def test_invalid_input_422(body):
    assert client.post("/card", json=body).status_code == 422


def test_accent_color_is_optional_and_validated():
    assert png(client.post("/card", json={"title": "Hi", "accent": "#FF6600"})).size == (1200, 630)
    assert client.post("/card", json={"title": "Hi", "accent": "red"}).status_code == 422


# ---------- stored cards

KEY = "test-key"
AUTH = {"X-API-Key": KEY}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "cards"))
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    return tmp_path / "cards"


def test_create_card_stores_png_and_serves_it(store):
    r = client.post("/cards", json={"title": "Autumn roast is here", "brand": "Acme"}, headers=AUTH)
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["id"]) == 32
    assert body["url"] == f"http://testserver/cards/{body['id']}.png"
    assert (body["width"], body["height"], body["size"]) == (1200, 630, "og")
    assert (store / f"{body['id']}.png").is_file()
    got = client.get(f"/cards/{body['id']}.png")  # no key needed to view
    assert got.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert png(got).size == (1200, 630)


def test_public_base_url_is_used_when_set(store, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8117/")
    body = client.post("/cards", json={"title": "Hi"}, headers=AUTH).json()
    assert body["url"] == f"http://localhost:8117/cards/{body['id']}.png"


@pytest.mark.parametrize("channel,size,dims", [
    ("instagram", "square", (1080, 1080)), ("Threads", "square", (1080, 1080)),
    ("facebook", "square", (1080, 1080)), ("linkedin", "og", (1200, 630)),
    ("x", "og", (1200, 630)), ("tiktok", "story", (1080, 1920)), ("newsletter", "og", (1200, 630)),
])
def test_channel_picks_the_size(store, channel, size, dims):
    body = client.post("/cards", json={"title": "Hi", "channel": channel}, headers=AUTH).json()
    assert body["size"] == size
    assert png(client.get(f"/cards/{body['id']}.png")).size == dims


def test_explicit_size_beats_channel(store):
    body = client.post("/cards", json={"title": "Hi", "channel": "instagram", "size": "story"}, headers=AUTH).json()
    assert body["size"] == "story"


def test_ids_are_random(store):
    ids = {client.post("/cards", json={"title": "Same"}, headers=AUTH).json()["id"] for _ in range(5)}
    assert len(ids) == 5


def test_create_card_needs_the_key(store, monkeypatch):
    assert client.post("/cards", json={"title": "Hi"}).status_code == 401
    assert client.post("/cards", json={"title": "Hi"}, headers={"X-API-Key": "nope"}).status_code == 401
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.post("/cards", json={"title": "Hi"}, headers=AUTH).status_code == 503
    assert not store.exists() or not any(store.iterdir())


def test_create_card_invalid_input_is_422(store):
    assert client.post("/cards", json={"title": ""}, headers=AUTH).status_code == 422
    assert client.post("/cards", json={"title": "x", "channel": "c" * 41}, headers=AUTH).status_code == 422


def test_card_endpoint_is_unchanged_and_keyless(store):
    assert png(client.post("/card", json={"title": "Hi"})).size == (1200, 630)


def test_unknown_card_is_404(store):
    assert client.get(f"/cards/{'0' * 32}.png").status_code == 404


@pytest.mark.parametrize("path", [
    "/cards/..%2F..%2Fetc%2Fpasswd.png",
    "/cards/%2E%2E%2Fsecret.png",
    "/cards/../secret.png",
    "/cards/....png",
    "/cards/" + "A" * 32 + ".png",          # upper case is not an id
    "/cards/" + "0" * 31 + ".png",          # too short
    "/cards/" + "0" * 33 + ".png",          # too long
    "/cards/" + "0" * 30 + "..png",
    "/cards/.secret.png",
    "/cards/%00" + "0" * 31 + ".png",
    "/cards/" + "0" * 32,                   # no extension
    "/cards/" + "0" * 32 + ".png.tmp",
])
def test_path_traversal_and_bad_ids_are_refused(store, tmp_path, path):
    # A PNG outside the card folder, and one hidden inside it, must never be served.
    (tmp_path / "secret.png").write_bytes(b"\x89PNG\r\n\x1a\nSECRET")
    store.mkdir(parents=True, exist_ok=True)
    (store / ".secret.png").write_bytes(b"\x89PNG\r\n\x1a\nSECRET")
    (store / ("0" * 30 + "..png")).write_bytes(b"\x89PNG\r\n\x1a\nSECRET")
    r = client.get(path)
    assert r.status_code in (404, 405, 422), (path, r.status_code)
    assert b"SECRET" not in r.content
