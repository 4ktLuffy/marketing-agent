"""Learned disclosure wordings: saved with an accepted finding (88), confirmed by the owner (05)."""
import json

import pytest
from fastapi.testclient import TestClient

from .conftest import KEYS, URLS, csrf_of, login
from .test_tasks import TASKS, TID, blocked_md_task

BRAND = URLS["BRAND_URL"]
DRAFT = {"id": 5, "fact_key": "weekday-rate", "disclosure": "per room per night, 2 sharing",
         "wording": "per room, two sharing", "status": "draft", "proposed_by": "Abebe Kebede", "task_id": TID,
         "created_at": "2026-09-30T08:15:00Z", "decided_by": None, "decided_at": None}
FORM = {"piece_key": "p1", "finding": "1", "sha": "1" * 64, "note": "said as per room, two sharing"}


@pytest.fixture
def bridge(monkeypatch, mock):
    monkeypatch.setenv("TASKS_URL", TASKS)
    from app.main import create_app
    mock.get(f"{TASKS}/tasks/{TID}").respond(json=blocked_md_task())
    with TestClient(create_app(), follow_redirects=False) as c:
        assert login(c).status_code == 303
        yield c, csrf_of(c.get("/more").text), mock


@pytest.fixture
def facts(authed, mock):
    mock.get(f"{BRAND}/facts/v2").respond(json={"facts": []})
    mock.get(f"{BRAND}/questions").respond(json={"questions": []})
    return authed


# ------------------------------------------------------------------ 88: accept with a wording

def test_accept_form_offers_the_wording_field(bridge):
    c, _, _ = bridge
    h = c.get(f"/tasks/{TID}").text
    assert 'name="wording"' in h and "Save this exact wording for next time (copy it from the post)" in h


def test_accept_sends_the_wording_and_says_it_is_saved(bridge):
    c, token, mock = bridge
    route = mock.post(f"{TASKS}/tasks/{TID}/pieces/p1/accept").respond(json={
        "blocked": False, "wording_proposal": {"id": 5, "status": "draft"}})
    r = c.post(f"/tasks/{TID}/accept", data={**FORM, "csrf": token, "wording": "  per room, two sharing "})
    assert r.status_code == 200, r.text[:300]
    assert json.loads(route.calls.last.request.content)["wording"] == "per room, two sharing"
    assert "Finding accepted and recorded" in r.text and "saved for the owner to confirm" in r.text


def test_wording_error_from_88_is_shown_softly(bridge):
    c, token, mock = bridge
    mock.post(f"{TASKS}/tasks/{TID}/pieces/p1/accept").respond(json={
        "blocked": False, "wording_proposal": {"error": "the brand service did not answer"}})
    r = c.post(f"/tasks/{TID}/accept", data={**FORM, "csrf": token, "wording": "per room, two sharing"})
    assert r.status_code == 200
    assert "Finding accepted and recorded" in r.text
    assert "the wording was not saved: the brand service did not answer" in r.text


def test_empty_wording_is_not_sent(bridge):
    c, token, mock = bridge
    route = mock.post(f"{TASKS}/tasks/{TID}/pieces/p1/accept").respond(json={"blocked": False})
    c.post(f"/tasks/{TID}/accept", data={**FORM, "csrf": token, "wording": "   "})
    assert "wording" not in json.loads(route.calls.last.request.content)


def test_bad_wording_length_is_refused_before_88(bridge):
    c, token, mock = bridge
    route = mock.post(f"{TASKS}/tasks/{TID}/pieces/p1/accept").respond(json={})
    for bad in ("ab", "x" * 201):
        r = c.post(f"/tasks/{TID}/accept", data={**FORM, "csrf": token, "wording": bad})
        assert r.status_code == 422 and "3 to 200 characters" in r.text
    assert not route.called


def test_wording_not_in_the_text_422_from_88(bridge):
    c, token, mock = bridge
    mock.post(f"{TASKS}/tasks/{TID}/pieces/p1/accept").respond(422, json={"detail": "wording is not in the piece"})
    r = c.post(f"/tasks/{TID}/accept", data={**FORM, "csrf": token, "wording": "not in the post"})
    assert r.status_code == 422 and "not copied exactly from the post" in r.text


# ------------------------------------------------------------------ 05: the owner confirms

def test_facts_page_lists_draft_wordings_with_a_badge(facts, mock):
    c, _ = facts
    lst = mock.get(f"{BRAND}/disclosure-wordings").respond(json={"wordings": [DRAFT]})
    h = c.get("/facts").text
    assert lst.calls.last.request.url.params["status"] == "draft"
    assert lst.calls.last.request.headers["x-api-key"] == KEYS["INTERNAL_API_KEY"]
    assert "x-owner-key" not in lst.calls.last.request.headers
    assert 'data-wordings="1"' in h
    h = c.get("/facts?show=wordings").text
    assert "weekday-rate" in h and "per room per night, 2 sharing" in h and "per room, two sharing" in h
    assert "Abebe Kebede" in h and f'href="/tasks/{TID}"' in h
    assert 'action="/facts/wordings/5/confirm"' in h and 'action="/facts/wordings/5/dismiss"' in h


def test_no_wordings_and_an_older_05(facts, mock):
    c, _ = facts
    mock.get(f"{BRAND}/disclosure-wordings").respond(404, json={"detail": "Not Found"})
    h = c.get("/facts?show=wordings")
    assert h.status_code == 200 and "No wordings waiting." in h.text and "data-wordings" not in h.text


@pytest.mark.parametrize("action", ["confirm", "dismiss"])
def test_owner_confirms_or_dismisses_with_owner_key(facts, mock, action):
    c, token = facts
    mock.get(f"{BRAND}/disclosure-wordings").respond(json={"wordings": [DRAFT]})
    route = mock.post(f"{BRAND}/disclosure-wordings/5/{action}").respond(json={**DRAFT, "status": action + "ed"})
    assert c.post(f"/facts/wordings/5/{action}", data={}).status_code == 403   # CSRF
    assert not route.called
    r = c.post(f"/facts/wordings/5/{action}", data={"csrf": token})
    assert r.status_code == 303 and r.headers["location"] == f"/facts?show=wordings&done=wording:{action}ed"
    h = route.calls.last.request.headers
    assert h["x-owner-key"] == KEYS["FACT_OWNER_KEY"] and h["x-api-key"] == KEYS["INTERNAL_API_KEY"]
    assert h["x-actor"] == "henos"
    assert c.post("/facts/wordings/5/approve", data={"csrf": token}).status_code == 404


def test_confirm_refused_by_05_shows_the_list_with_the_reason(facts, mock):
    c, token = facts
    mock.get(f"{BRAND}/disclosure-wordings").respond(json={"wordings": []})
    mock.post(f"{BRAND}/disclosure-wordings/5/confirm").respond(409, json={"detail": "already decided"})
    r = c.post("/facts/wordings/5/confirm", data={"csrf": token})
    assert r.status_code == 409 and "Not done" in r.text and "already decided" in r.text


def test_confirm_off_without_owner_key(monkeypatch, mock):
    from .test_facts_page import fresh
    with fresh(monkeypatch, FACT_OWNER_KEY="") as c:
        assert login(c).status_code == 303
        token = csrf_of(c.get("/more").text)
        mock.get(f"{BRAND}/facts/v2").respond(json={"facts": []})
        mock.get(f"{BRAND}/questions").respond(json={"questions": []})
        mock.get(f"{BRAND}/disclosure-wordings").respond(json={"wordings": [DRAFT]})
        route = mock.post(f"{BRAND}/disclosure-wordings/5/confirm").respond(json={})
        r = c.post("/facts/wordings/5/confirm", data={"csrf": token})
        assert r.status_code == 503 and not route.called


def test_non_owner_sees_wordings_without_buttons(tmp_path, monkeypatch, mock):
    from app.users import hash_password
    f = tmp_path / "users.json"
    f.write_text(json.dumps({"users": [{"name": "abebe", "display": "Abebe", "role": "approver",
                                        "pw_hash": hash_password("approver-pw-1")}]}))
    monkeypatch.setenv("CONTROL_USERS_FILE", str(f))
    from app.main import create_app
    mock.get(f"{BRAND}/facts/v2").respond(json={"facts": []})
    mock.get(f"{BRAND}/questions").respond(json={"questions": []})
    mock.get(f"{BRAND}/disclosure-wordings").respond(json={"wordings": [DRAFT]})
    route = mock.post(f"{BRAND}/disclosure-wordings/5/confirm").respond(json={})
    with TestClient(create_app(), follow_redirects=False) as c:
        assert login(c, password="approver-pw-1", user="abebe").status_code == 303
        token = csrf_of(c.get("/more").text)
        h = c.get("/facts?show=wordings").text
        assert "per room, two sharing" in h and "/facts/wordings/5/confirm" not in h
        assert "The owner confirms or dismisses it." in h
        assert c.post("/facts/wordings/5/confirm", data={"csrf": token}).status_code == 403
        assert not route.called
