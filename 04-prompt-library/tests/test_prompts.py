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


def test_social_posts_open_with_is_optional_and_exact():
    p = load(Path(__file__).parent.parent / "prompts" / "social_posts.yaml")
    base = {k: None for k in p["vars"]} | {"topic": "Pausing before a trip", "channels": "x"}
    without = env.from_string(p["template"]).render(base)
    assert "FIRST sentence" not in without
    with_ = env.from_string(p["template"]).render(base | {"open_with": "pause my subscription"})
    assert 'The FIRST sentence of every post must contain this exact phrase, word for word: "pause my subscription".' in with_
    assert "do not attribute it to anyone" in with_ and "Never paste it on as a label" in with_
    assert "Do not put it in quotation marks" in with_  # 58 flags quotes that are not consented testimonials
    retry = env.from_string(p["template"]).render(base | {"open_with": "pause my subscription",
                                                          "open_with_feedback": "FEEDBACK-LINE"})
    assert "FEEDBACK-LINE" in retry and "FEEDBACK-LINE" not in with_


def test_client_report_summary_keeps_causes_hedged():
    p = load(Path(__file__).parent.parent / "prompts" / "client_report_summary.yaml")
    assert set(p["vars"]) == {"data", "month", "previous_month"}
    system = env.from_string(p["system"]).render(brand="B")
    assert "copied exactly as written" in system and "we can't tell yet" in system
    for word in ("because", "led to", "drove", "thanks to"):
        assert f'"{word}"' in system   # named as forbidden, since 85 drops such sentences
    assert p["schema"]["properties"]["summary"]["maxItems"] == 5


def test_feed_title_uses_only_the_row_and_description_is_optional():
    p = load(Path(__file__).parent.parent / "prompts" / "feed_title.yaml")
    assert set(p["vars"]) == {"product", "with_description"} and p["vars"]["with_description"] == "optional"
    assert "{{ brand }}" not in p["system"] + p["template"]   # the shop's voice has no place in a feed title
    base = {"product": '{"title": "Mug"}', "with_description": None}
    without = env.from_string(p["template"]).render(base)
    assert "Leave description as an empty string" in without and "Also write a description" not in without
    with_ = env.from_string(p["template"]).render(base | {"with_description": "yes"})
    assert "Also write a description" in with_ and "no HTML" in with_
    for rule in ("At most 150 characters", "Use only words and numbers that appear in the product data",
                 '"free shipping"', "never ALL CAPS", "first 70 characters"):
        assert rule in without
    assert p["schema"]["properties"]["title"]["maxLength"] == 150   # 87 checks it again in code
    assert p["schema"]["required"] == ["title", "description"]


def test_propose_facts_demands_verbatim_quotes_and_scope_words():
    p = load(Path(__file__).parent.parent / "prompts" / "propose_facts.yaml")
    assert p["vars"] == {"source_text": "required", "business_type": "optional", "known_facts": "optional"}
    assert "{{ brand }}" not in p["system"] + p["template"]   # the brand profile is what this builds
    base = {"source_text": "Open 9am to 5pm weekdays only.", "business_type": None, "known_facts": None}
    plain = env.from_string(p["template"]).render(base)
    assert "Open 9am to 5pm weekdays only." in plain
    assert "Type of business" not in plain and "already has" not in plain
    full = env.from_string(p["template"]).render(base | {"business_type": "Clinic", "known_facts": "- KNOWN-LINE"})
    assert "Type of business: Clinic" in full and "KNOWN-LINE" in full
    for rule in ("copied exactly", "Only facts stated in the source text", "Never infer a price",
                 '"per person"', '"weekdays only"', '"at our Leeds branch"', "Never guess a year"):
        assert rule in plain, rule
    item = p["schema"]["properties"]["facts"]["items"]
    assert {"source_quote", "subject", "fact_type", "value_text", "scope_hints", "text"} <= set(item["required"])
    assert set(item["properties"]["scope_hints"]["properties"]) == {"sites", "regions", "channels", "segments",
                                                                    "plan_tiers", "variants"}
    # the enums are 05's fact model (contract §1), so 72 can post a proposal without mapping
    assert item["properties"]["subject"]["properties"]["kind"]["enum"] == [
        "business", "site", "product", "variant", "plan", "service", "package", "menu_item", "person", "policy", "offer"]
    assert "certification" in item["properties"]["fact_type"]["enum"]
    assert p["schema"]["properties"]["questions"]["items"]["required"] == ["text", "source_quote"]


def test_propose_facts_example_output_validates_against_its_schema():
    """A well-formed answer passes the schema; a fact without its quote does not (negative control)."""
    p = load(Path(__file__).parent.parent / "prompts" / "propose_facts.yaml")
    v = Draft202012Validator(p["schema"])
    good = {"facts": [{"text": "Airport transfers cost £25 per person each way.",
                       "subject": {"kind": "service", "ref": "Airport transfer"}, "fact_type": "price",
                       "attribute": "transfer_price", "value_text": "£25 per person each way", "value": 25,
                       "currency": "GBP", "basis": "per_person",
                       "scope_hints": {"sites": [], "segments": []}, "valid_from": None, "valid_to": None,
                       "source_quote": "Airport transfers £25 per person each way."}],
            "questions": [{"text": "What does a dog stay cost?", "source_quote": "dogs are welcome"}]}
    assert not list(v.iter_errors(good))
    bad = {"facts": [{k: x for k, x in good["facts"][0].items() if k != "source_quote"}], "questions": []}
    assert list(v.iter_errors(bad))
    wrong_kind = {"facts": [good["facts"][0] | {"subject": {"kind": "branch", "ref": "Leeds"}}], "questions": []}
    assert list(v.iter_errors(wrong_kind))
