"""MODEL_CHECK=review: only unsure sentences go to 44, and 44 can only add non-blocking review notes."""
import httpx

from app import main

from . import data
from .conftest import AUTH, make_task, paste_and_submit

TEXT = ("Hybrids are [[day-hire-porthleven]]. Our most popular bike is the hybrid. We have 40 bikes ready. "
        "Come and say hello. Hybrids are £32 a day. Voted best bike shop in Cornwall. "
        "Our riders love the coast path.")
REVIEWED = "Our most popular bike is the hybrid."     # rules: non-blocking review
CUED = "We have 40 bikes ready."                    # rules: nothing, but a number


def check(client, text=TEXT):
    r = client.post("/check", json={"text": text, "scope": {"sites": ["porthleven"]}, "publish_on": data.PUBLISH,
                                     "channel": "linkedin"}, headers=AUTH)
    assert r.status_code == 200
    return r.json()


def rule_findings(client, stack, monkeypatch):
    monkeypatch.setenv("MODEL_CHECK", "off")
    out = check(client)
    monkeypatch.setenv("MODEL_CHECK", "review")
    return out


def test_only_review_and_cued_sentences_are_sent(client, stack, monkeypatch):
    stack.facts = data.business("bikes")
    before = rule_findings(client, stack, monkeypatch)
    assert stack.verify_route.call_count == 0
    out = check(client)
    assert stack.verify_route.call_count == 1
    sent = stack.verify_calls[0]
    assert set(sent) == {"text", "facts"}                        # no context: 44 would read it as evidence
    assert sent["text"].split("\n") == [REVIEWED, CUED]          # review first, then cued; never blocked/matched
    # public lines only, with validity / scope added
    assert all(line.startswith("- [[") for line in sent["facts"])
    assert data.INTERNAL_SENTINEL not in "\n".join(sent["facts"])
    assert any("applies only to sites: porthleven" in line for line in sent["facts"])
    assert out["findings"] == before["findings"] and out["blocked"] == before["blocked"]   # 44 said all fine


def test_model_only_adds_non_blocking_review(client, stack, monkeypatch):
    stack.facts = data.business("bikes")
    before = rule_findings(client, stack, monkeypatch)
    stack.verify_answer = [
        {"claim": CUED, "supported": False, "reasons": ["numbers not in the facts: 40"]},
        {"claim": "Hybrids are £32 a day.", "supported": False, "reasons": ["x"]},    # not sent: ignored
        {"claim": REVIEWED, "supported": True, "reasons": []},
    ]
    out = check(client)
    assert out["findings"][: len(before["findings"])] == before["findings"]           # rules untouched
    added = out["findings"][len(before["findings"]):]
    assert added == [{"sentence": CUED, "label": "review", "fact_key": None, "quote": None, "blocking": False,
                      "detail": "model check: numbers not in the facts: 40"}]
    assert out["blocked"] == before["blocked"]


def test_clean_text_without_cues_sends_nothing(client, stack, monkeypatch):
    stack.facts = data.business("bikes")
    monkeypatch.setenv("MODEL_CHECK", "review")
    check(client, "Come and say hello. Our riders love the coast path.")
    assert stack.verify_route.call_count == 0


def test_cap_per_piece(client, stack, monkeypatch):
    stack.facts = data.business("bikes")
    monkeypatch.setenv("MODEL_CHECK", "review")
    check(client, " ".join(f"We have {n} bikes ready." for n in range(40, 60)))
    assert len(stack.verify_calls[0]["text"].split("\n")) == main.REVIEW_MAX


def test_errors_skip_with_a_note(client, stack, monkeypatch, mock):
    stack.facts = data.business("bikes")
    before = rule_findings(client, stack, monkeypatch)
    for resp in (httpx.Response(502, json={"detail": "gateway down"}), httpx.Response(200, text="not json"),
                 httpx.Response(200, json=["odd"]), httpx.TimeoutException("slow")):
        stack.verify_route.mock(side_effect=resp) if isinstance(resp, Exception) else \
            stack.verify_route.mock(side_effect=None, return_value=resp)
        out = check(client)
        assert out["findings"] == before["findings"]
        assert len(out["notes"]) == 1 and out["notes"][0].startswith("model check skipped")


def test_submit_notes_skip_in_checks(client, stack, monkeypatch):
    monkeypatch.setenv("MODEL_CHECK", "review")
    stack.verify_route.mock(side_effect=httpx.ConnectError("down"))
    t = make_task(client, stack)
    out = paste_and_submit(client, t["id"], "=== 1 LINKEDIN ===\nWe have 40 bikes ready.\n\n"
                                            "=== 2 INSTAGRAM ===\nCome and say hello.")
    p1, p2 = out["pieces"]
    assert any(n.startswith("model check skipped") for n in p1["notes"]) and p2["notes"] == []
    assert not p1["blocked"]


def test_auto_and_off_unchanged(client, stack, monkeypatch):
    stack.facts = data.business("bikes")
    monkeypatch.setenv("MODEL_CHECK", "off")
    check(client)
    assert stack.verify_route.call_count == 0
    assert client.get("/health").json()["model_check_mode"] == "off"
    monkeypatch.setenv("MODEL_CHECK", "auto")
    check(client)
    assert stack.verify_calls[-1]["text"].startswith("Hybrids are £28 per day.")      # auto: the whole piece
    h = client.get("/health").json()
    assert h["model_check"] is True and h["model_check_mode"] == "auto"
    monkeypatch.setenv("MODEL_CHECK", "review")
    assert client.get("/health").json()["model_check"] is False           # `model_check` means auto, as before
    monkeypatch.delenv("CLAIMS_URL")
    check(client)
    assert client.get("/health").json()["model_check_mode"] == "off"      # review without 44 is off
    assert stack.verify_route.call_count == 1


def test_review_sentences_pure():
    fs = [{"sentence": "Aox is £5.", "label": "review", "blocking": False},
          {"sentence": "Box is £6.", "label": "review", "blocking": False},
          {"sentence": "Box is £6.", "label": "no_source", "blocking": True},
          {"sentence": "Cox is £7.", "label": "match", "blocking": False}]
    text = "Is Dox free? Aox is £5. Box is £6. Cox is £7. Eox is £8. Fox is nice."
    assert main.review_sentences(text, fs) == ["Aox is £5.", "Eox is £8."]
