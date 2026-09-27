import pytest
import yaml
from pathlib import Path

CASES = Path(__file__).parent.parent / "cases"
KNOWN = {"max_chars", "min_chars", "contains", "not_contains", "count", "numbers_from_input",
         "brand_ok", "platform_ok", "channels_match", "equals", "no_numbering", "cta_like"}


def test_case_files_are_valid_and_ids_unique():
    ids = []
    for f in CASES.glob("*.yaml"):  # top level only; cases/claims/ has its own format
        if f.stem.startswith("site_assistant"):  # visitor messages for evalsuite.site_assistant
            continue
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


@pytest.mark.parametrize("name,min_cases", [("site_assistant", 20), ("site_assistant_heldout", 12)])
def test_site_assistant_cases_are_valid(name, min_cases):
    spec = yaml.safe_load((CASES / f"{name}.yaml").read_text())
    kinds = {"answer", "handoff", "refusal", "disclosure", "qualify"}
    cases = spec["cases"]
    assert len(cases) >= min_cases and len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert {"id", "group", "say", "expect"} <= set(c), c.get("id")
        assert c["group"] in {"covered", "uncovered", "adversarial", "injection", "buying"}, c["id"]
        assert set(c["expect"] if isinstance(c["expect"], list) else [c["expect"]]) <= kinds, c["id"]
    groups = {c["group"] for c in cases}
    assert groups == {"covered", "uncovered", "adversarial", "injection", "buying"}
