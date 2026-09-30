"""Learning from the accept button: a person accepts a missing_disclosure finding and gives the exact
words in the text that say it. 88 proposes them to 05 (draft); once the owner confirms them there,
05 serves them on the fact as `disclosure_wordings` and they count as the disclosure next time."""
from datetime import date

import httpx

from app import evidence

from . import data
from .conftest import AUTH, make_task, paste_and_submit
from .test_accept import PASTE, accept, md_index, submitted

WORDING = "riders aged sixteen and up"


def test_wording_must_be_copied_from_the_text(client, stack):
    tid, p = submitted(client, stack)
    r = accept(client, tid, p, wording="only for grown-ups")
    assert r.status_code == 422 and r.json()["detail"] == "the wording must be copied from the text"
    assert stack.wording_posts == [] and stack.items[p["calendar_item_id"]]["status"] == "draft"
    assert accept(client, tid, p, wording="ab").status_code == 422          # too short
    assert accept(client, tid, p, wording="x" * 201).status_code == 422     # too long
    assert accept(client, tid, p, wording="ders aged sixteen").status_code == 422   # not whole words
    # nothing was accepted by the refusals
    assert accept(client, tid, p).status_code == 200


def test_wording_is_proposed_to_the_brand_service(client, stack):
    tid, p = submitted(client, stack)
    f = p["findings"][md_index(p)]
    r = accept(client, tid, p, wording="  Riders   aged SIXTEEN and up ")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["blocked"] is False and out["status"] == "in_review"
    assert out["wording_proposal"] == {"id": 1, "status": "draft"}
    assert stack.wording_posts == [{"fact_key": "ebike-day", "disclosure": f["quote"],
                                    "wording": "Riders aged SIXTEEN and up", "task_id": tid,
                                    "proposed_by": "Maya Okafor"}]
    assert f["quote"] == "riders must be 16 or over"
    ev = [e for e in client.get(f"/tasks/{tid}", headers=AUTH).json()["events"] if e["kind"] == "finding_accepted"]
    assert ev[0]["detail"]["wording"] == "Riders aged SIXTEEN and up"


def test_no_wording_no_proposal(client, stack):
    tid, p = submitted(client, stack)
    r = accept(client, tid, p)
    assert r.status_code == 200 and "wording_proposal" not in r.json()
    assert stack.wording_posts == []
    ev = [e for e in client.get(f"/tasks/{tid}", headers=AUTH).json()["events"] if e["kind"] == "finding_accepted"]
    assert ev[0]["detail"]["wording"] is None


def test_brand_service_down_the_accept_still_stands(client, stack):
    tid, p = submitted(client, stack)
    stack.wording_route.mock(side_effect=httpx.ConnectError("down"))
    r = accept(client, tid, p, wording=WORDING)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["blocked"] is False and out["finding"]["accepted"]["by"] == "Maya Okafor"
    assert "unreachable" in out["wording_proposal"]["error"]
    assert stack.items[p["calendar_item_id"]]["status"] == "in_review"


def test_brand_service_refuses_the_accept_still_stands(client, stack):
    tid, p = submitted(client, stack)
    stack.wording_route.mock(return_value=httpx.Response(422, json={"detail": "not a required disclosure"}))
    r = accept(client, tid, p, wording=WORDING)
    assert r.status_code == 200
    assert r.json()["wording_proposal"]["error"].startswith("brand service returned HTTP 422")


def _with_wording(key="ebike-day", wordings=(WORDING,)):
    facts = data.business("bikes")
    for f in facts:
        f["disclosure_wordings"] = list(wordings) if f["key"] == key else []
    return facts


def test_confirmed_wording_clears_missing_disclosure_on_submit(client, stack):
    t = make_task(client, stack, facts=_with_wording())
    p = paste_and_submit(client, t["id"], PASTE)["pieces"][0]
    assert not any(f["label"] == "missing_disclosure" for f in p["findings"]), p["findings"]
    assert p["blocked"] is False and p["status"] == "in_review"


def _check(client, text):
    r = client.post("/check", json={"text": text, "scope": {"sites": ["porthleven"]}, "publish_on": data.PUBLISH},
                    headers=AUTH)
    assert r.status_code == 200, r.text
    return [f for f in r.json()["findings"] if f["label"] == "missing_disclosure"]


def test_confirmed_wording_clears_missing_disclosure_on_check(client, stack):
    text = "E-bikes are £45 a day, for Riders aged\nsixteen-and-up."
    stack.facts = data.business("bikes")
    assert _check(client, text)
    stack.facts = _with_wording()
    assert _check(client, text) == []


def test_wording_not_in_the_text_does_nothing(client, stack):
    stack.facts = _with_wording(wordings=["for grown-ups only"])
    assert _check(client, "E-bikes are £45 a day, for riders aged sixteen and up.")


def test_wording_on_another_fact_does_not_count(client, stack):
    stack.facts = _with_wording(key="insurance")
    assert _check(client, "E-bikes are £45 a day, for riders aged sixteen and up.")


A = data.fact("site-visit", "The Garden room costs £500 after a site visit.", "£500", value=500, currency="GBP",
              fact_type="price", required_disclosures=["subject to a site visit"],
              subject={"kind": "product", "ref": "Garden room"})
B = data.fact("survey", "The Summer house costs £900.", "£900", value=900, currency="GBP",
              fact_type="price", required_disclosures=["subject to survey"],
              subject={"kind": "product", "ref": "Summer house"})


def _missing(text, facts):
    findings, _ = evidence.check_text(text, facts, date.fromisoformat(data.PUBLISH), {})
    return sorted({f["fact_key"] for f in findings if f["label"] == "missing_disclosure" and f.get("blocking")})


def test_wording_of_fact_a_does_not_satisfy_fact_b():
    a = {**A, "disclosure_wordings": ["once we have been out to look"]}
    text = "The Summer house is £900, once we have been out to look."
    assert _missing(text, [a, B]) == ["survey"]
    b = {**B, "disclosure_wordings": ["once we have been out to look"]}
    assert _missing(text, [A, b]) == []
    assert _missing("The Garden room is £500, once we have been out to look.", [A, b]) == ["site-visit"]
    assert _missing("The Garden room is £500, once we have been out to look.", [a, B]) == []
