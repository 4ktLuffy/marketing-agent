"""Text of a PDF, page by page (brochures, price lists, menus, rate sheets).

Only the PDF's own text layer is read (pypdf); there is no OCR. A scanned brochure (pictures of
pages) or a PDF that needs a password is refused with a message that says what to do instead.
"""
import base64
import binascii
import io
import logging
import re

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError, PyPdfError

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PAGES = 50
MAX_PAGE_TEXT = 20_000
# A page "has text" when it has at least this many letters or digits. Scans often carry a
# stray header or a page number in their text layer; that is not a text layer to work from.
MIN_PAGE_CHARS = 20
NO_TEXT = "no text layer (a scanned or image-only PDF): paste the text instead"

# pypdf logs every malformed object it recovers from; a brochure is allowed to be messy.
logging.getLogger("pypdf").setLevel(logging.ERROR)


class PDFError(ValueError):
    """The PDF cannot be used. `status` is the HTTP status to answer with; the message is safe to show."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def decode_base64(data: str) -> bytes:
    """The upload as bytes; refuses anything over MAX_PDF_BYTES before decoding all of it."""
    data = re.sub(r"\s+", "", data or "")
    if data.startswith("data:"):              # a data URL from a browser: data:application/pdf;base64,....
        data = data.split(",", 1)[-1]
    if len(data) * 3 // 4 > MAX_PDF_BYTES + 3:
        raise PDFError("PDF larger than 10 MB", 413)
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise PDFError("pdf_base64 is not valid base64") from None
    if len(raw) > MAX_PDF_BYTES:
        raise PDFError("PDF larger than 10 MB", 413)
    return raw


def _clean(text: str) -> str:
    """Whitespace per line collapsed, blank-line runs squeezed, lines kept (a price list is lines)."""
    lines = [" ".join(line.split()) for line in (text or "").replace("\r", "\n").split("\n")]
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def extract_pdf(raw: bytes, filename: str | None = None) -> dict:
    """{title, text, pages: [{page, text}], page_count, word_count, source: "pdf", filename}."""
    if len(raw) > MAX_PDF_BYTES:
        raise PDFError("PDF larger than 10 MB", 413)
    if not raw.lstrip()[:5].startswith(b"%PDF-"):
        raise PDFError("not a PDF file (it does not start with %PDF-)")
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            # Many PDFs are "encrypted" only to stop printing; they open with an empty password.
            try:
                ok = reader.decrypt("")
            except (NotImplementedError, PyPdfError) as exc:
                raise PDFError(f"the PDF is encrypted and cannot be opened ({exc}): paste the text instead") from None
            if not ok:
                raise PDFError("the PDF needs a password: paste the text instead, or upload a copy without a password")
        n = len(reader.pages)
        if n == 0:
            raise PDFError("the PDF has no pages")
        if n > MAX_PAGES:
            raise PDFError(f"the PDF has {n} pages; at most {MAX_PAGES} (split it, or upload the pages that matter)", 413)
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except (PyPdfError, KeyError, ValueError, TypeError, AttributeError):
                text = ""   # one broken page does not sink the brochure
            pages.append({"page": i, "text": _clean(text)[:MAX_PAGE_TEXT]})
        title = None
        try:
            meta = reader.metadata
            title = (str(meta.title).strip() or None) if meta and meta.title else None
        except (PyPdfError, KeyError, ValueError, TypeError, AttributeError):
            title = None
    except FileNotDecryptedError:
        raise PDFError("the PDF needs a password: paste the text instead, or upload a copy without a password") from None
    except PDFError:
        raise
    except (PdfReadError, PyPdfError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        raise PDFError(f"not a readable PDF ({type(exc).__name__})") from None

    if not any(sum(ch.isalnum() for ch in p["text"]) >= MIN_PAGE_CHARS for p in pages):
        raise PDFError(NO_TEXT)
    full = "\n\n".join(p["text"] for p in pages if p["text"])
    return {"title": title[:300] if title else None, "text": full, "pages": pages, "page_count": len(pages),
            "word_count": len(full.split()), "source": "pdf", "filename": (filename or "")[:200] or None}
