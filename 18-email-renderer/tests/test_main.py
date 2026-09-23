from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BODY = """# Spring is here

Hi there, our **spring sale** starts _today_.

- 20% off everything
- Free shipping

Read [the details](https://example.com/sale).
"""


def render(**kw):
    payload = {"subject": "Spring sale", "body_markdown": BODY, **kw}
    return client.post("/render", json=payload)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_render_full_email():
    r = render(
        preheader="20% off, this week only",
        cta_text="Shop now",
        cta_url="https://example.com/sale?utm_source=newsletter",
        footer="Acme Inc, 1 Main St",
    )
    assert r.status_code == 200
    h = r.json()["html"]
    assert 'width="600"' in h and "<table" in h
    assert "<script" not in h and "<link" not in h and "<style" not in h
    assert '<h1 style="' in h and '<strong>spring sale</strong>' in h
    assert '<li style="' in h
    assert "display:none" in h and "20% off, this week only" in h
    assert 'href="https://example.com/sale?utm_source=newsletter"' in h
    assert "v:roundrect" in h and "Shop now" in h
    assert "{{unsubscribe_url}}" in h and "Acme Inc, 1 Main St" in h


def test_plain_text_version():
    text = render(cta_text="Shop now", cta_url="https://example.com/s", footer="Acme Inc").json()["text"]
    assert text.startswith("Spring is here\n\nHi there")
    assert "our spring sale starts today." in text
    assert "- 20% off everything" in text
    assert "the details (https://example.com/sale)" in text
    assert "Shop now: https://example.com/s" in text
    assert "Unsubscribe: {{unsubscribe_url}}" in text
    assert "**" not in text and "#" not in text


def test_no_cta_without_both_fields():
    r = render(cta_text="Shop now")
    assert r.status_code == 200
    assert "roundrect" not in r.json()["html"]


def test_subject_and_preheader_escaped():
    h = render(subject="<b>Hi</b> & bye", preheader='<img src=x onerror="a()">').json()["html"]
    assert "<title>&lt;b&gt;Hi&lt;/b&gt; &amp; bye</title>" in h
    assert "<img src=x" not in h
    assert "&lt;img src=x onerror=&quot;a()&quot;&gt;" in h


def test_raw_html_and_js_links_in_markdown_are_neutralised():
    h = client.post("/render", json={
        "subject": "s",
        "body_markdown": "<script>alert(1)</script>\n\n[click](javascript:alert(1))",
    }).json()["html"]
    assert "<script>" not in h and "&lt;script&gt;" in h
    assert "javascript:" not in h


def test_cta_url_must_be_http():
    for bad in ("javascript:alert(1)", "/relative", "ftp://example.com/x"):
        r = render(cta_text="Go", cta_url=bad)
        assert r.status_code == 422, bad


def test_empty_body_rejected():
    r = client.post("/render", json={"subject": "s", "body_markdown": "   "})
    assert r.status_code == 422


def test_missing_required_fields():
    assert client.post("/render", json={"subject": "s"}).status_code == 422
