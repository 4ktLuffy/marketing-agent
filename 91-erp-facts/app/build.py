"""Turn one ERP record into one draft fact body (or a reason to skip it)."""
from __future__ import annotations

import operator
import re
from dataclasses import dataclass

from .mapping import KEY_RE, Mapping, Spec, render, slug

OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "=": operator.eq,
       "!=": operator.ne}


@dataclass
class Built:
    key: str
    body: dict
    value: float | int
    currency: str | None
    write_date: str | None
    record: str


@dataclass
class Skip:
    record: str
    reason: str


def plain(v):
    """Odoo returns False for empty and [id, name] for many2one."""
    if v is False or v is None:
        return None
    if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], int):
        return v[1]
    return v


def fmt_num(v) -> str:
    return f"{v:,.0f}" if float(v) == int(v) else f"{v:,.2f}"


def build(mapping: Mapping, spec: Spec, rec: dict) -> Built | Skip:
    rid = rec.get("id")
    label = f"{mapping.model} #{rid} {plain(rec.get('name')) or ''}".strip()
    raw = rec.get(spec.value_field)
    if raw is False or raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return Skip(label, f"no value in {spec.value_field}")
    value = int(raw) if float(raw) == int(raw) else round(float(raw), 4)
    for sk in spec.skip_if:
        cmp = value if sk.field == "value" else plain(rec.get(sk.field))
        if isinstance(cmp, (int, float)) and not isinstance(cmp, bool) and OPS[sk.op](cmp, sk.value):
            return Skip(label, f"{sk.field} {sk.op} {sk.value:g} (placeholder)")

    currency = spec.currency
    if spec.currency_field:
        currency = plain(rec.get(spec.currency_field))
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            return Skip(label, f"no usable currency in {spec.currency_field}")

    vars: dict = {f: plain(rec.get(f)) for f in mapping.fields}
    vars.update(id=rid, value=value, value_fmt=fmt_num(value), currency=currency or "", unit=spec.unit or "")
    for d in spec.derive:
        src = vars.get(d.from_field)
        m = re.search(d.regex, str(src or ""), re.I)
        if m:
            for n, g in zip(d.names, m.groups()):
                vars[n] = g

    key = slug(render(spec.key_template, vars))
    if not KEY_RE.match(key):
        return Skip(label, f"key '{key}' is not 2-60 characters of a-z 0-9 -")
    text = None
    for var in spec.text_variants:
        if all(vars.get(r) for r in var.requires):
            text = render(var.template, vars)
            break
    text = text or render(spec.text_template, vars)
    if not text.strip() or len(text) > 500:
        return Skip(label, "text is empty or longer than 500 characters")
    if spec.value_text_template:
        value_text = render(spec.value_text_template, vars)
    else:
        value_text = f"{currency + ' ' if currency else ''}{vars['value_fmt']}" + (f" per {spec.unit}" if spec.unit else "")

    scope = {k: list(v) for k, v in spec.scope.items()}
    for dim, f in spec.scope_fields.items():
        v = plain(rec.get(f))
        if not v:
            return Skip(label, f"scope field {f} is empty")
        scope.setdefault(dim, [])
        if str(v) not in scope[dim]:
            scope[dim].append(str(v))

    wd = rec.get("write_date") or None
    body = {
        "key": key,
        "subject": {"kind": spec.subject.kind, "ref": render(spec.subject.ref_template, vars)[:120]},
        "fact_type": spec.fact_type,
        "attribute": spec.attribute,
        "value": value,
        "unit": spec.unit,
        "currency": currency,
        "value_text": value_text[:200],
        "basis": spec.basis,
        "conditions": [c.model_dump() for c in spec.conditions],
        "scope": scope,
        "source": {"kind": "doc", "ref": f"Odoo {mapping.model} #{rid} write_date {wd}"[:300]},
        "required_disclosures": list(spec.required_disclosures),
        "owner": spec.owner or "ERP (Odoo)",
        "sensitivity": spec.sensitivity,
        "text": text.strip(),
    }
    return Built(key, body, value, currency, wd, label)
