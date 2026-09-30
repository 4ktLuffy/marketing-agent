"""Tiny valid PDFs written by hand (text in Helvetica, one line per Td), for tests only."""
import io


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]], title: str | None = None) -> bytes:
    """One page per list of lines. An empty list makes a page with no text (like a scan)."""
    objs: list[bytes] = []
    n = len(pages)
    # 1 catalog, 2 pages, 3 font, 4 info, then (page, content) pairs
    kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objs.append(f"<< /Title ({_esc(title or '')}) >>".encode("latin-1"))
    for i, lines in enumerate(pages):
        ops = ["BT /F1 11 Tf 14 TL 50 780 Td"] + [f"({_esc(line)}) Tj T*" for line in lines] + ["ET"]
        stream = "\n".join(ops).encode("cp1252")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {6 + 2 * i} 0 R >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 4 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def encrypt(raw: bytes, user_password: str) -> bytes:
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(raw)))
    w.encrypt(user_password=user_password, owner_password="owner-pw", algorithm="RC4-128")
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()
