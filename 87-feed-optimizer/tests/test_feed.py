"""Parsing CSV/TSV and writing the export back byte for byte."""
import csv
import io

import pytest

from app import feed


def parse(text, **kw):
    return feed.parse(text.encode("utf-8") if isinstance(text, str) else text, max_rows=kw.pop("max_rows", 100), **kw)


def test_csv_with_quotes_commas_newlines_and_bom():
    text = ('﻿id,Title,Description,Price,g:image_link,Extra Col\r\n'
            '1,"Mug, large","Says ""hi""\nsecond line",12.00 USD,https://x/1.jpg,keep me\r\n'
            '2,Plain,,5.00 USD,https://x/2.jpg,\r\n')
    f = parse(text)
    assert f.bom and f.delimiter == ","
    assert f.columns == ["id", "title", "description", "price", "image_link", "extra_col"]
    rows = [r for _, r in f.rows()]
    assert rows[0]["title"] == "Mug, large" and rows[0]["description"] == 'Says "hi"\nsecond line'
    assert rows[1]["description"] == "" and rows[1]["extra_col"] == ""
    # Same values as Python's csv module reads.
    ref = list(csv.reader(io.StringIO(text[1:], newline="")))
    assert [r.values for r in f.records] == ref[1:]


def test_tsv_is_detected_and_unknown_columns_are_kept():
    f = parse("id\ttitle\tcustom_label_3\nA\tSock\tautumn\n")
    assert f.delimiter == "\t" and f.rows()[0][1] == {"id": "A", "title": "Sock", "custom_label_3": "autumn"}


def test_export_without_changes_is_byte_identical():
    for text in ('﻿id,title,description\r\n1,"Mug, large","a ""b"""\r\n\r\n2,Plain,x\r\n',
                 "id\ttitle\tprice\nA\tSock\t1.00 USD\nB\t\"quoted\"\t2.00 USD",
                 "id,title\n1,Mug\n"):
        f = parse(text)
        assert feed.export(f, {}) == text


def test_export_changes_only_title_and_description_cells():
    text = 'id,title,description,price,link\r\n1,"Mug, large",old desc,12.00 USD,https://x/1\r\n2,Plate,d2,3.00 USD,https://x/2\r\n'
    f = parse(text)
    out = feed.export(f, {"1": {"title": 'Kiln Mug, "Large"', "description": "new", "price": "0.01 USD",
                                "link": "https://evil"}})
    lines = out.split("\r\n")
    assert lines[1] == '1,"Kiln Mug, ""Large""",new,12.00 USD,https://x/1'   # price and link ignored
    assert lines[2] == "2,Plate,d2,3.00 USD,https://x/2"                    # untouched row: same bytes
    again = parse(out)
    assert [r["title"] for _, r in again.rows()] == ['Kiln Mug, "Large"', "Plate"]


@pytest.mark.parametrize("text,msg", [
    ("", "empty"),
    ("id,title\n", "no products"),
    ("sku,name\n1,x\n", "no id or title"),
    ("id,title,title\n1,a,b\n", "twice"),
    ("id,title\n1,a,extra\n", "has 3 fields"),
    ("id,title\n1,a\n1,b\n", "appears twice"),
    ("id,title\n,a\n", "empty id"),
    ('id,title\n1,"never closed\n', "never closed"),
])
def test_bad_files_are_refused_with_a_reason(text, msg):
    with pytest.raises(feed.FeedError, match=msg):
        parse(text)


def test_not_utf8_and_caps():
    with pytest.raises(feed.FeedError, match="not UTF-8"):
        parse("id,title\n1,caf\xe9\n".encode("latin-1"))
    with pytest.raises(feed.FeedError, match="more than 2 products"):
        parse("id,title\n1,a\n2,b\n3,c\n", max_rows=2)
    with pytest.raises(feed.FeedError, match="longer than 5"):
        parse("id,title\n1,abcdefgh\n", max_field_chars=5)
    with pytest.raises(feed.FeedError, match="3 columns"):
        parse("id,title,x\n1,a,b\n", max_columns=2)
