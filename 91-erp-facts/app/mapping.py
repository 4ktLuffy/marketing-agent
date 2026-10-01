"""Mappings: which ERP records become which facts. Loaded from YAML (`MAPPINGS_FILE`).

A mapping reads ONLY the fields it lists (plus `id` and `write_date`, which carry no personal
data). Models that hold personal data are refused unless `allow_models` names them. `outputs`
lets one mapping make several facts per record (hotel: rack and tour rate, single and double).
"""
from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import odoo

KEY_RE = re.compile(r"^[a-z0-9-]{2,60}$")
PLACEHOLDER = re.compile(r"\{(\w+)(\|slug)?\}")
BUILTIN_VARS = {"id", "value", "value_fmt", "currency", "unit"}
DOMAIN_OPS = {"=", "!=", "<", "<=", ">", ">=", "in", "not in", "like", "ilike"}
SUBJECT_KINDS = {"business", "site", "product", "variant", "plan", "service", "package", "menu_item", "person",
                 "policy", "offer"}
FACT_TYPES = {"price", "spec", "availability", "hours", "inclusion", "policy", "certification", "credential",
              "claim", "testimonial", "result", "event", "contact"}
BASES = {"per_unit", "per_person", "per_room", "per_night", "per_seat", "per_month", "per_year", "flat"}
SCOPE_DIMS = {"sites", "regions", "channels", "segments", "plan_tiers", "variants"}


class MappingError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubjectT(_S):
    kind: str
    ref_template: str = ""


class SkipIf(_S):
    field: str
    op: Literal["<", "<=", ">", ">=", "=", "!="]
    value: float


class Derive(_S):
    from_field: str = Field(alias="from")
    regex: str
    names: list[str]
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Variant(_S):
    requires: list[str]
    template: str


class Condition(_S):
    key: str
    op: Literal["=", "!=", "<", "<=", ">", ">=", "in", "not_in"] = "="
    value: Any = None


class Spec(_S):
    """One fact per record."""
    name: str
    model: str
    domain: list = []
    fields: list[str]
    key_template: str
    subject: SubjectT
    fact_type: str = "price"
    attribute: str | None = None
    value_field: str
    currency: str | None = None
    currency_field: str | None = None
    unit: str | None = None
    basis: str | None = None
    conditions: list[Condition] = []
    scope: dict[str, list[str]] = {}
    scope_fields: dict[str, str] = {}
    sensitivity: Literal["public", "internal"] = "internal"
    text_template: str
    text_variants: list[Variant] = []
    value_text_template: str | None = None
    required_disclosures: list[str] = []
    skip_if: list[SkipIf] = []
    derive: list[Derive] = []
    owner: str | None = None


class Mapping(_S):
    name: str
    model: str
    domain: list
    fields: list[str]
    specs: list[Spec]


class MappingSet(_S):
    mappings: dict[str, Mapping]
    allow_models: list[str] = []

    def get(self, name: str) -> Mapping | None:
        return self.mappings.get(name)


def slug(text: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", str(text).lower())).strip("-")


def placeholders(template: str) -> set[str]:
    return {m.group(1) for m in PLACEHOLDER.finditer(template)}


def render(template: str, vars: dict[str, Any]) -> str:
    def sub(m):
        v = vars.get(m.group(1), "")
        v = "" if v is None or v is False else str(v)
        return slug(v) if m.group(2) else v
    return PLACEHOLDER.sub(sub, template)


def _check_domain(domain: list, problems: list[str], where: str) -> None:
    for i, term in enumerate(domain):
        if term in ("&", "|", "!"):
            continue
        if not (isinstance(term, (list, tuple)) and len(term) == 3):
            problems.append(f"{where}: domain term {i} must be [field, op, value] or & | !")
            continue
        f, op, val = term
        try:
            odoo.check_field(str(f))
        except odoo.OdooBlocked as e:
            problems.append(f"{where}: domain {e}")
        if op not in DOMAIN_OPS:
            problems.append(f"{where}: domain operator '{op}' is not allowed")
        ok = lambda x: x is None or isinstance(x, (str, int, float, bool))  # noqa: E731
        if not (ok(val) or (isinstance(val, (list, tuple)) and all(ok(x) for x in val))):
            problems.append(f"{where}: domain term {i} has a non-scalar value")


def _validate_spec(s: Spec, allow_models: list[str], problems: list[str]) -> None:
    w = f"mapping '{s.name}'"
    try:
        odoo.check_model(s.model, allow_models)
    except odoo.OdooBlocked as e:
        problems.append(f"{w}: {e}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", s.name):
        problems.append(f"{w}: name must be lowercase letters, digits, dashes")
    for f in s.fields:
        try:
            odoo.check_field(f)
        except odoo.OdooBlocked as e:
            problems.append(f"{w}: {e}")
    _check_domain(s.domain, problems, w)
    allowed = set(s.fields)
    if s.value_field not in allowed:
        problems.append(f"{w}: value_field '{s.value_field}' is not in fields")
    if s.currency_field and s.currency_field not in allowed:
        problems.append(f"{w}: currency_field '{s.currency_field}' is not in fields")
    if s.currency and s.currency_field:
        problems.append(f"{w}: give currency or currency_field, not both")
    if s.currency and not re.fullmatch(r"[A-Z]{3}", s.currency):
        problems.append(f"{w}: currency must be a 3-letter code")
    for dim, f in s.scope_fields.items():
        if dim not in SCOPE_DIMS:
            problems.append(f"{w}: scope_fields dimension '{dim}' is unknown")
        if f not in allowed:
            problems.append(f"{w}: scope_fields '{f}' is not in fields")
    for dim in s.scope:
        if dim not in SCOPE_DIMS:
            problems.append(f"{w}: scope dimension '{dim}' is unknown")
    for sk in s.skip_if:
        if sk.field != "value" and sk.field not in allowed:
            problems.append(f"{w}: skip_if field '{sk.field}' is not in fields")
    if s.subject.kind not in SUBJECT_KINDS:
        problems.append(f"{w}: subject.kind '{s.subject.kind}' is not a fact subject kind")
    if s.fact_type not in FACT_TYPES:
        problems.append(f"{w}: fact_type '{s.fact_type}' is unknown")
    if s.basis is not None and s.basis not in BASES:
        problems.append(f"{w}: basis '{s.basis}' is unknown")
    derived = set()
    for d in s.derive:
        if d.from_field not in allowed:
            problems.append(f"{w}: derive from '{d.from_field}' is not in fields")
        try:
            rx = re.compile(d.regex)
            if rx.groups != len(d.names):
                problems.append(f"{w}: derive regex has {rx.groups} groups but {len(d.names)} names")
        except re.error:
            problems.append(f"{w}: derive regex is not valid")
        derived |= set(d.names)
    base_ok = allowed | BUILTIN_VARS
    for label, tpl, ok in (("key_template", s.key_template, base_ok | derived),
                           ("subject.ref_template", s.subject.ref_template, base_ok | derived),
                           ("text_template", s.text_template, base_ok),
                           ("value_text_template", s.value_text_template or "", base_ok | derived)):
        for p in placeholders(tpl) - ok:
            problems.append(f"{w}: {label} uses '{p}', which is not a listed field")
    for v in s.text_variants:
        for p in placeholders(v.template) - (base_ok | derived):
            problems.append(f"{w}: a text_variants template uses '{p}', which is not a listed field")
        for p in v.requires:
            if p not in derived:
                problems.append(f"{w}: text_variants requires '{p}', which no derive produces")
    if not s.key_template.strip():
        problems.append(f"{w}: key_template is empty")


def parse(data: Any) -> MappingSet:
    problems: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("mappings"), list) or not data["mappings"]:
        raise MappingError(["the file needs a top-level 'mappings:' list"])
    allow = [str(m) for m in (data.get("allow_models") or [])]
    out: dict[str, Mapping] = {}
    for i, raw in enumerate(data["mappings"]):
        if not isinstance(raw, dict):
            problems.append(f"mapping {i} is not a mapping")
            continue
        raw = dict(raw)
        outputs = raw.pop("outputs", None) or [{}]
        label = raw.get("name", f"#{i}")
        if label in out:
            problems.append(f"mapping name '{label}' is used twice")
            continue
        specs = []
        for j, over in enumerate(outputs):
            try:
                specs.append(Spec(**{**raw, **over}))
            except ValidationError as e:
                for err in e.errors(include_url=False):
                    problems.append(f"mapping '{label}' output {j}: {'.'.join(map(str, err['loc']))}: {err['msg']}")
            except TypeError as e:
                problems.append(f"mapping '{label}': {e}")
        for s in specs:
            _validate_spec(s, allow, problems)
            if s.model != specs[0].model or s.domain != specs[0].domain:
                problems.append(f"mapping '{label}': outputs must share the model and domain")
        if specs:
            # one ERP read per mapping: union of the fields of all outputs
            fields = list(dict.fromkeys(f for s in specs for f in s.fields))
            dom = specs[0].domain
            out[label] = Mapping(name=label, model=specs[0].model, domain=dom, fields=fields, specs=specs)
    if problems:
        raise MappingError(sorted(set(problems)))
    return MappingSet(mappings=out, allow_models=allow)


def load(path: str) -> MappingSet:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError:
        raise MappingError([f"mappings file '{path}' cannot be read"]) from None
    except yaml.YAMLError:
        raise MappingError([f"mappings file '{path}' is not valid YAML"]) from None
    return parse(data)
