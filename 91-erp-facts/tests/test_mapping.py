import copy

import pytest
import yaml

from app import build, mapping
from .conftest import EXAMPLE

BASE = {"name": "m", "model": "product.template", "domain": [["sale_ok", "=", True]],
        "fields": ["name", "list_price"], "subject": {"kind": "product", "ref_template": "{name}"},
        "key_template": "p-{name|slug}", "value_field": "list_price", "currency": "GBP",
        "text_template": "{name} is {value_fmt}"}


def parse(**over):
    m = copy.deepcopy(BASE)
    m.update(over)
    return mapping.parse({"mappings": [m]})


def errs(**over):
    with pytest.raises(mapping.MappingError) as e:
        parse(**over)
    return " | ".join(e.value.problems)


def test_example_file_loads():
    ms = mapping.load(EXAMPLE)
    assert len(ms.mappings["service-plans"].specs) == 2 and len(ms.mappings["drink-crates"].specs) == 1


def test_validation_errors():
    assert "value_field" in errs(value_field="nope")
    assert "not a listed field" in errs(key_template="p-{ghost}")
    assert "not a listed field" in errs(text_template="{ghost}")
    assert "domain operator" in errs(domain=[["sale_ok", "DROP", True]])
    assert "domain term" in errs(domain=["bad"])
    assert "3-letter" in errs(currency="pounds")
    assert "fact_type" in errs(fact_type="vibes")
    assert "subject.kind" in errs(subject={"kind": "thing", "ref_template": "x"})
    assert "basis" in errs(basis="per_moon")
    assert "scope dimension" in errs(scope={"planets": ["x"]})
    assert "not both" in errs(currency_field="name")
    assert "derive regex" in errs(derive=[{"from": "name", "regex": "(a)(b)", "names": ["x"]}])
    assert "outputs must share" in errs(outputs=[{}, {"model": "product.product"}])


def test_unknown_key_and_shape_errors():
    with pytest.raises(mapping.MappingError):
        parse(bogus=1)
    with pytest.raises(mapping.MappingError):
        mapping.parse({})
    with pytest.raises(mapping.MappingError, match="twice"):
        mapping.parse({"mappings": [BASE, BASE]})


def test_missing_file_and_bad_yaml(tmp_path):
    with pytest.raises(mapping.MappingError):
        mapping.load(str(tmp_path / "nope.yaml"))
    p = tmp_path / "b.yaml"
    p.write_text("a: [")
    with pytest.raises(mapping.MappingError):
        mapping.load(str(p))


def test_app_refuses_to_start_on_bad_mappings(tmp_path):
    from app.main import create_app
    from .conftest import make_cfg
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump({"mappings": [{**BASE, "model": "res.partner"}]}))
    with pytest.raises(mapping.MappingError):
        create_app(make_cfg(mappings_file=str(p)), env={})


def test_build_skip_and_key_rules():
    m = parse().mappings["m"]
    s = m.specs[0]
    assert isinstance(build.build(m, s, {"id": 1, "name": "!!", "list_price": 5}), build.Skip)  # key too short
    assert isinstance(build.build(m, s, {"id": 1, "name": "Beer", "list_price": False}), build.Skip)
    ok = build.build(m, s, {"id": 1, "name": "Beer One", "list_price": 12.5, "write_date": "d"})
    assert ok.key == "p-beer-one" and ok.body["value"] == 12.5 and "12.50" in ok.body["text"]


def test_duplicate_keys_reported(client, odoo):
    odoo.records["product.template"].append({"id": 99, "name": "Cola 33cl", "company_id": 3, "sale_ok": True,
                                             "list_price": 13.0, "write_date": "x"})
    d = client.get("/drift").json()
    assert any("duplicate key" in s["reason"] for s in d["skipped"])
