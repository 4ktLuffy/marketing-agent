"""Owner-confirmed starter-kit rules travel in every pack, so the chatbot writes them in from the start."""
from app import pack

from .conftest import make_task

RULES = [
    {"kind": "required_disclosure", "phrase": "Selling to persons under 21 is prohibited.", "why": "any alcohol advertisement or public post", "status": "active", "kit": "ethiopia-alcohol"},
    {"kind": "required_disclosure", "phrase": "Draft only. Hold for human review; do not auto-publish.", "why": "any alcohol copy is drafted (recommendation, not a statutory text)", "status": "active", "kit": "ethiopia-alcohol"},
    {"kind": "forbidden_phrase", "phrase": "official beer of", "why": "Art. 60(3)", "status": "active", "kit": "ethiopia-alcohol"},
    {"kind": "forbidden_phrase", "phrase": "prize draw", "why": "Art. 60(5)", "status": "draft", "kit": "ethiopia-alcohol"},
]


def test_lines_keep_rules_and_drop_notes_and_drafts():
    lines = pack.kit_rule_lines(RULES)
    text = "\n".join(lines)
    assert "Selling to persons under 21 is prohibited." in text and '"official beer of"' in text
    assert "Hold for human review" not in text          # a procedural note, not copy
    assert "prize draw" not in text                     # still a draft rule


def test_no_rules_no_lines():
    assert pack.kit_rule_lines([]) == [] and pack.kit_rule_lines(None) == []


def test_pack_carries_confirmed_rules(client, stack):
    stack.kit_rules = RULES
    t = make_task(client, stack)
    assert "Business rules, always: include" in t["pack"] and "under 21" in t["pack"]
    assert "never write" in t["pack"] and "official beer of" in t["pack"]


def test_pack_without_rules_is_unchanged(client, stack):
    t = make_task(client, stack)
    assert "Business rules" not in t["pack"]
