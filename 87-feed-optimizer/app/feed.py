"""Reading and writing a Google Merchant Center product feed (CSV or TSV with a header row).

The parser keeps the exact text of every field as it was in the file. The export writes the file
back from those pieces and replaces only the approved title and description cells, so every
other cell, the quoting, the line endings, the column order and the row order stay byte for
byte what was uploaded.
"""
import csv
import io
import re
from dataclasses import dataclass, field


class FeedError(ValueError):
    pass


BOM = "﻿"
# Never changed by this service, whatever a model or a person proposes (enforced in export()).
NEVER_CHANGED = ("id", "gtin", "mpn", "price", "sale_price", "link", "image_link", "availability", "condition")
EDITABLE = ("title", "description")


def canonical(name: str) -> str:
    """Header name as Merchant Center reads it: "Image Link", "g:image_link" -> "image_link"."""
    n = name.strip().lower()
    if n.startswith("g:"):
        n = n[2:]
    return re.sub(r"[\s-]+", "_", n)


@dataclass
class Record:
    raw: list[str]          # each field exactly as written in the file (quotes included)
    values: list[str]       # each field's value
    eol: str                # the record's line ending as written ("\r\n", "\n", "\r" or "")

    def blank(self) -> bool:
        return len(self.values) == 1 and self.values[0] == ""


@dataclass
class Feed:
    delimiter: str
    bom: bool
    header: Record
    records: list[Record] = field(default_factory=list)   # data records and blank lines, in file order

    @property
    def columns(self) -> list[str]:
        return [canonical(c) for c in self.header.values]

    def rows(self) -> list[tuple[int, dict]]:
        """(record index, {canonical column: value}) for every non-blank data record."""
        cols = self.columns
        return [(i, dict(zip(cols, r.values))) for i, r in enumerate(self.records) if not r.blank()]


def _records(text: str, delim: str):
    """RFC 4180 records. A quote opens a quoted field only as the field's first character (like
    Python's csv module); a doubled quote inside is one quote. Yields Record objects."""
    i, n = 0, len(text)
    while i < n:
        raw, values = [], []
        while True:
            start = i
            if i < n and text[i] == '"':
                i += 1
                buf = []
                while True:
                    if i >= n:
                        raise FeedError("a quoted field is never closed (a \" is missing)")
                    c = text[i]
                    if c == '"':
                        if i + 1 < n and text[i + 1] == '"':
                            buf.append('"')
                            i += 2
                            continue
                        i += 1
                        break
                    buf.append(c)
                    i += 1
                while i < n and text[i] not in (delim, "\r", "\n"):   # text after the closing quote
                    buf.append(text[i])
                    i += 1
                value = "".join(buf)
            else:
                while i < n and text[i] not in (delim, "\r", "\n"):
                    i += 1
                value = text[start:i]
            raw.append(text[start:i])
            values.append(value)
            if i < n and text[i] == delim:
                i += 1
                continue
            if text.startswith("\r\n", i):
                eol, i = "\r\n", i + 2
            elif i < n:
                eol, i = text[i], i + 1
            else:
                eol = ""
            break
        yield Record(raw, values, eol)


def parse(data: bytes, max_rows: int, max_columns: int = 200, max_field_chars: int = 20000) -> Feed:
    """Bytes of an uploaded feed -> Feed. Raises FeedError with a reason a person can act on."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FeedError(f"the file is not UTF-8 (byte {exc.start}); save it as UTF-8 and upload it again") from None
    bom = text.startswith(BOM)
    if bom:
        text = text[1:]
    if not text.strip():
        raise FeedError("the file is empty")
    first = text.split("\n", 1)[0]
    delim = "\t" if "\t" in first else ","
    it = _records(text, delim)
    header = next(it)
    cols = [canonical(c) for c in header.values]
    if len(cols) > max_columns:
        raise FeedError(f"{len(cols)} columns; at most {max_columns}")
    missing = [c for c in ("id", "title") if c not in cols]
    if missing:
        raise FeedError(f"the header has no {' or '.join(missing)} column (first line: {first[:120]!r})")
    dup = sorted({c for c in cols if c and cols.count(c) > 1})
    if dup:
        raise FeedError(f"columns appear twice: {', '.join(dup)}")
    feed = Feed(delim, bom, header)
    seen: set[str] = set()
    rows = 0
    for rec in it:
        feed.records.append(rec)
        if rec.blank():
            continue
        rows += 1
        line = rows + 1
        if rows > max_rows:
            raise FeedError(f"more than {max_rows} products; split the file")
        if len(rec.values) != len(cols):
            raise FeedError(f"product {rows} (record {line}) has {len(rec.values)} fields, the header has {len(cols)}")
        if any(len(v) > max_field_chars for v in rec.values):
            raise FeedError(f"product {rows} has a field longer than {max_field_chars} characters")
        pid = rec.values[cols.index("id")].strip()
        if not pid:
            raise FeedError(f"product {rows} has an empty id")
        if pid in seen:
            raise FeedError(f"id {pid[:60]!r} appears twice; Merchant Center needs unique ids")
        seen.add(pid)
    if rows == 0:
        raise FeedError("the file has a header but no products")
    return feed


def _quote(value: str, delim: str) -> str:
    """A new cell value written the way csv's QUOTE_MINIMAL writes it."""
    if any(c in value for c in (delim, '"', "\r", "\n")):
        return '"' + value.replace('"', '""') + '"'
    return value


def export(feed: Feed, changes: dict[str, dict[str, str]]) -> str:
    """The uploaded file with changes[id][column] applied to title/description cells only.

    Everything else is written back from the raw text. A change to any other column is ignored:
    the never-changed fields cannot be altered here, whatever reached the database.
    """
    cols = feed.columns
    id_at = cols.index("id")
    out = [BOM] if feed.bom else []
    out.append(feed.delimiter.join(feed.header.raw) + feed.header.eol)
    for rec in feed.records:
        raw = list(rec.raw)
        if not rec.blank():
            ch = changes.get(rec.values[id_at].strip()) or {}
            for col in EDITABLE:
                if col in ch and col in cols and ch[col] != rec.values[cols.index(col)]:
                    raw[cols.index(col)] = _quote(ch[col], feed.delimiter)
        out.append(feed.delimiter.join(raw) + rec.eol)
    return "".join(out)


def write_table(header: list[str], rows: list[list[str]], delimiter: str) -> str:
    """A new table (supplemental feed, or the full feed in the other format) via the csv module."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()
