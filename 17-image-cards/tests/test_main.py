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
