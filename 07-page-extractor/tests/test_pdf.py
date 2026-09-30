"""PDFs: base64 upload and URL fetch of application/pdf -> text per page with page numbers; clear
4xx for encrypted, scanned (no text layer), too big, too many pages and not-a-PDF. The PDFs are
written by hand in tests/pdfmaker.py; nothing leaves the test."""
import base64

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import pdf, safe_http
from app.main import app

from .pdfmaker import encrypt, make_pdf

client = TestClient(app)
BROCHURE = make_pdf([
    ["Lake Ember Lodge - Rates 2026-27", "Midweek Escape: GBP 180 per room per night, 2 sharing, Sun-Thu."],
    ["Airport transfers GBP 25 per person each way.", "Breakfast included on weekdays only."],
], title="Lake Ember rate sheet")


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.215.14"])


def test_pdf_upload_text_per_page():
    r = client.post("/extract", json={"pdf_base64": b64(BROCHURE), "filename": "rates.pdf"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "pdf" and body["filename"] == "rates.pdf" and body["url"] is None
    assert body["title"] == "Lake Ember rate sheet"
    assert body["page_count"] == 2 and [p["page"] for p in body["pages"]] == [1, 2]
    assert "GBP 180 per room per night, 2 sharing, Sun-Thu." in body["pages"][0]["text"]
    assert "Airport transfers GBP 25 per person each way." in body["pages"][1]["text"]
    assert "Airport transfers" not in body["pages"][0]["text"]
    # the page shape old callers read is still there
    assert body["headings"] == [] and body["links"] == {"internal": 0, "external": 0} and body["og"] == {}
    assert "Midweek Escape" in body["text"] and body["word_count"] > 20


def test_pdf_data_url_and_whitespace_in_base64_are_accepted():
    data = "data:application/pdf;base64," + "\n".join(b64(BROCHURE)[i:i + 76] for i in range(0, len(b64(BROCHURE)), 76))
    assert client.post("/extract", json={"pdf_base64": data}).status_code == 200


def test_scanned_pdf_without_text_layer_is_422_with_advice():
    scan = make_pdf([[], ["3"]])   # image-only pages; one stray page number
    r = client.post("/extract", json={"pdf_base64": b64(scan)})
    assert r.status_code == 422
    assert "no text layer" in r.json()["detail"] and "paste the text instead" in r.json()["detail"]


def test_password_protected_pdf_is_422_with_advice():
    r = client.post("/extract", json={"pdf_base64": b64(encrypt(BROCHURE, "s3cret"))})
    assert r.status_code == 422
    assert "password" in r.json()["detail"] and "paste the text instead" in r.json()["detail"]


def test_pdf_encrypted_with_empty_password_is_read():
    r = client.post("/extract", json={"pdf_base64": b64(encrypt(BROCHURE, ""))})
    assert r.status_code == 200, r.text
    assert "Midweek Escape" in r.json()["pages"][0]["text"]


def test_too_many_pages_is_refused():
    many = make_pdf([["Page text long enough to count as text."]] * 51)
    r = client.post("/extract", json={"pdf_base64": b64(many)})
    assert r.status_code == 413 and "at most 50" in r.json()["detail"]
    ok = make_pdf([["Page text long enough to count as text."]] * 50)
    assert client.post("/extract", json={"pdf_base64": b64(ok)}).json()["page_count"] == 50


def test_too_big_is_413_before_decoding(monkeypatch):
    monkeypatch.setattr(pdf, "MAX_PDF_BYTES", 1000)
    r = client.post("/extract", json={"pdf_base64": b64(BROCHURE + b" " * 2000)})
    assert r.status_code == 413 and "10 MB" in r.json()["detail"]


@pytest.mark.parametrize("data, why", [
    ("not base64!!", "not valid base64"),
    (b64(b"<html>hello</html>"), "not a PDF"),
    (b64(b"%PDF-1.4\n garbage without objects"), "not a readable PDF"),
])
def test_bad_uploads_are_422(data, why):
    r = client.post("/extract", json={"pdf_base64": data})
    assert r.status_code == 422 and why in r.json()["detail"]


def test_pdf_and_url_together_is_422():
    assert client.post("/extract", json={"pdf_base64": b64(BROCHURE), "url": "https://example.com/"}).status_code == 422


@respx.mock
def test_url_serving_a_pdf_is_read_page_by_page():
    route = respx.get("https://example.com/files/rates.pdf").mock(
        return_value=httpx.Response(200, content=BROCHURE, headers={"content-type": "application/pdf"}))
    r = client.post("/extract", json={"url": "https://example.com/files/rates.pdf"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"] == "https://example.com/files/rates.pdf" and body["filename"] == "rates.pdf"
    assert body["pages"][1]["page"] == 2 and "per person each way" in body["pages"][1]["text"]
    assert route.called


@respx.mock
def test_pdf_url_may_be_bigger_than_a_page_but_not_than_10_mb():
    big = BROCHURE + b"%" + b" " * (6 * 1024 * 1024)   # 6 MB: over the 5 MB page limit, fine for a PDF
    respx.get("https://example.com/big.pdf").mock(
        return_value=httpx.Response(200, content=big, headers={"content-type": "application/pdf"}))
    assert client.post("/extract", json={"url": "https://example.com/big.pdf"}).status_code == 200
    respx.get("https://example.com/huge.pdf").mock(return_value=httpx.Response(
        200, content=b"%PDF-", headers={"content-type": "application/pdf", "content-length": str(11 * 1024 * 1024)}))
    r = client.post("/extract", json={"url": "https://example.com/huge.pdf"})
    assert r.status_code == 502 and "10 MB" in r.json()["detail"]


@respx.mock
def test_html_page_limit_is_unchanged():
    respx.get("https://example.com/big.html").mock(return_value=httpx.Response(
        200, content=b"<p>x</p>", headers={"content-type": "text/html", "content-length": str(6 * 1024 * 1024)}))
    r = client.post("/extract", json={"url": "https://example.com/big.html"})
    assert r.status_code == 502 and "5 MB" in r.json()["detail"]


@respx.mock
def test_scanned_pdf_by_url_is_422():
    respx.get("https://example.com/scan.pdf").mock(
        return_value=httpx.Response(200, content=make_pdf([[]]), headers={"content-type": "application/pdf"}))
    r = client.post("/extract", json={"url": "https://example.com/scan.pdf"})
    assert r.status_code == 422 and "paste the text instead" in r.json()["detail"]


@respx.mock
def test_pdf_url_redirect_to_private_address_is_still_blocked():
    respx.get("https://example.com/r.pdf").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/rates.pdf"}))
    inner = respx.get("http://127.0.0.1/rates.pdf")
    assert client.post("/extract", json={"url": "https://example.com/r.pdf"}).status_code == 422
    assert not inner.called


def test_safe_http_is_the_canonical_copy_unchanged():
    """The SSRF guard is shared by six deploys; PDF support lives in net.py and pdf.py only."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "app" / "safe_http.py").read_text()
    assert "pdf" not in src.lower()
