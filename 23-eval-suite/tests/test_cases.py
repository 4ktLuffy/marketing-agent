import yaml
from pathlib import Path

CASES = Path(__file__).parent.parent / "cases"
KNOWN = {"max_chars", "min_chars", "contains", "not_contains", "count", "numbers_from_input",
         "brand_ok", "platform_ok", "channels_match", "equals"}


def test_case_files_are_valid_and_ids_unique():
    ids = []
    for f in CASES.glob("*.yaml"):  # top level only; cases/claims/ has its own format
        for case in yaml.safe_load(f.read_text()):
            assert {"id", "prompt", "vars", "checks"} <= set(case), case.get("id")
            assert all(c["type"] in KNOWN for c in case["checks"]), case["id"]
            ids.append(case["id"])
    assert len(ids) == len(set(ids)) and len(ids) >= 8


def test_claim_files_are_labelled():
    files = list((CASES / "claims").glob("*.yaml"))
    assert files
    for f in files:
        rows = yaml.safe_load(f.read_text())
        assert rows and all(set(r) == {"claim", "label"} for r in rows), f.name
        assert {r["label"] for r in rows} == {"supported", "unsupported"}, f.name


def test_tool_selection_cases_are_valid():
    rows = yaml.safe_load((CASES / "tools" / "requests.yaml").read_text())
    assert rows and all(set(r) == {"say", "expect"} for r in rows)
