"""Local add-ons (LOCAL_DIR): a market's currency words, towns, wording and calendar, outside the code.
The suite runs with tests/fixtures/local (an invented market: "kora" / XKR, Greek-script words)."""
import textwrap
from datetime import date

from app import arith, evidence, local

from .conftest import AUTH


def test_local_currency_word_after_or_before_the_number():
    assert [v.key for v in evidence.extract("Crates are 1,700 kora, or XKR 1,700.")] == [
        ("money", "1700", "XKR"), ("money", "1700", "XKR")]


def test_words_in_another_script_are_read_only_when_that_script_is_there():
    assert [v.key for v in evidence.extract("1,700 κόρα και 5 δολάρια")] == [("money", "1700", "XKR"), ("money", "5", "USD")]
    assert [v.key for v in evidence.extract("1,700 crates and 5 boxes")] == []


def test_amounts_are_written_back_with_the_local_name():
    assert arith.fmt(evidence.Decimal("2080"), "XKR") == "2,080 kora"
    assert arith._bare(evidence.Decimal("2080"), "XKR") == "2,080"
    assert arith.fmt(evidence.Decimal("75"), "USD") == "$75"


def test_local_wording_counts_as_the_disclosure():
    assert evidence._disc_canon("1,700 κόρα ανά κιβώτιο") != "1,700 κόρα ανά κιβώτιο"


def test_local_towns_are_known_places():
    assert "Riverton" in evidence.KNOWN_PLACES and "kora" in evidence._PLACE_STOP


def test_without_a_local_dir_nothing_is_added(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_DIR", str(tmp_path / "missing"))
    local.reload()
    try:
        assert local.currency() == {} and local.places() == () and local.occasions_plugin() is None
    finally:
        monkeypatch.undo()
        local.reload()


def test_calendar_plugin_feeds_the_pack_and_the_endpoint(client, monkeypatch, tmp_path):
    (tmp_path / "occasions.py").write_text(textwrap.dedent('''
        from datetime import date
        NAME = "demo"
        NOTE = "Dates are approximate."
        PACK_NOTES = ["Write times in 24-hour format."]
        def format_date(day):
            return f"day {day.timetuple().tm_yday} of the demo year"
        def occasions_near(day, days=21, past_days=0):
            return [{"name": "Spring Festival", "date": date(2026, 10, 10), "end": date(2026, 10, 11),
                     "local_date": "Fourthmonth 1", "verified": True, "notes": "Families travel."}]
    '''))
    monkeypatch.setenv("LOCAL_DIR", str(tmp_path))
    local.reload()
    try:
        from app import main
        lines = main.calendar_lines("2026-10-06")
        assert lines[0].startswith("Publish date 2026-10-06 = day 279") and "Spring Festival" in lines[1]
        assert lines[-1] == "Write times in 24-hour format."
        body = client.get("/occasions", params={"on": "2026-10-06"}, headers=AUTH).json()
        assert body["calendar"] == "demo" and body["occasions"][0]["local_date"] == "Fourthmonth 1"
    finally:
        monkeypatch.undo()
        local.reload()


def test_no_calendar_plugin_means_no_calendar_lines(client):
    from app import main
    assert main.calendar_lines("2026-10-06") is None
    assert client.get("/occasions", headers=AUTH).json()["calendar"] is None
