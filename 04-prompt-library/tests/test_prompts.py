"""Every prompt must load, render with its example_vars, and carry a valid schema.

These checks mirror what 03-llm-gateway does at runtime, so a broken prompt fails
here in CI instead of as a 500 in production.
"""
from pathlib import Path

import pytest
import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from jsonschema import Draft202012Validator

PROMPTS = sorted((Path(__file__).parent.parent / "prompts").glob("*.yaml"))
env = SandboxedEnvironment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)


def load(path):
    return yaml.safe_load(path.read_text())


def test_there_are_prompts():
    assert len(PROMPTS) >= 10


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_prompt_is_well_formed(path):
    p = load(path)
    assert p["name"] == path.stem, "name must match the filename"
    assert p.get("description"), "description is shown to the agent; keep it"
    assert p.get("output", "text") in ("text", "json")
    assert set((p.get("vars") or {}).values()) <= {"required", "optional"}
    if p.get("output") == "json":
        Draft202012Validator.check_schema(p["schema"])
        assert p["schema"].get("type") == "object", "Ollama structured output wants an object root"


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_prompt_renders_with_example_vars(path):
    p = load(path)
    vars_ = p.get("vars") or {}
    example = p.get("example_vars") or {}
    required = [k for k, v in vars_.items() if v == "required"]
    assert set(required) <= set(example), f"example_vars must cover required vars {required}"
    full = {k: None for k in vars_} | {"brand": "Test Brand"} | example
    user = env.from_string(p["template"]).render(full)
    system = env.from_string(p.get("system", "")).render(full)
    assert user.strip()
    if "{{ brand }}" in p.get("system", ""):
        assert "Test Brand" in system


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_template_uses_only_declared_vars(path):
    p = load(path)
    declared = set(p.get("vars") or {}) | {"brand"}
    from jinja2 import meta
    used = set()
    for part in (p["template"], p.get("system", "")):
        used |= meta.find_undeclared_variables(env.parse(part))
    assert used <= declared, f"undeclared vars used: {used - declared}"
