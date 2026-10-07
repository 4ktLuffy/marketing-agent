from evalsuite.task_bridge import kit_findings


def test_kit_violations_become_findings():
    piece = {"piece_key": "p1", "filled_text": "Crate prices this week. Brand A lager, official beer of the festival!",
             "checks": {"brand": [
                 {"rule": "kit_forbidden", "severity": "error", "match": "official beer of", "detail": "x"},
                 {"rule": "kit_disclosure", "severity": "error", "detail": "add under-21"},
                 {"rule": "ai_sheen", "severity": "warn", "match": "delve", "detail": "y"}]}}
    got = kit_findings(piece)
    assert [(f["label"], f["sentence"]) for f in got] == [
        ("forbidden_phrase", "Brand A lager, official beer of the festival!"),
        ("missing_disclosure", "Crate prices this week.")]
    assert all(f["blocking"] and f["fact_key"] is None for f in got)


def test_no_checks_no_findings():
    assert kit_findings({"piece_key": "p1", "filled_text": "x"}) == []
