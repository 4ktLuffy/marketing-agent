"""Named users and roles: the users file, login per role, the role matrix, who is named on
decisions and on calls to other services, and the `python -m app.users` CLI."""
import io
import json
import logging
import os
import stat

import httpx
import pytest
from fastapi.testclient import TestClient

from app import users
from app.users import hash_password, verify

from .conftest import CAL, PASSWORD, URLS, WEBHOOK, csrf_of, flush, item, login

BRAND = URLS["BRAND_URL"]
TASKS = "http://tasks.internal:8000"
TID = "T-7K2M9Q"
PW = {"olive": "owner-pw-" + "Zr4Xk9Lm2Q", "sam": "appr-pw-" + "Hq7Tn3Vc8W", "wanda": "writer-pw-" + "Bp5Ys1Jd6E"}
PEOPLE = [("olive", "Olive Owner", "owner"), ("sam", "Sam Parker", "approver"), ("wanda", "Álex Writer", "writer")]


def write_file(path, people=PEOPLE):
    path.write_text(json.dumps({"users": [{"name": n, "display": d, "role": r, "pw_hash": hash_password(PW[n])}
                                          for n, d, r in people]}))
    return path


@pytest.fixture
def ufile(tmp_path, monkeypatch):
    p = write_file(tmp_path / "users.json")
    monkeypatch.setenv("CONTROL_USERS_FILE", str(p))
    monkeypatch.setenv("TASKS_URL", TASKS)
    return p


@pytest.fixture
def app_u(ufile):
    from app.main import create_app
    return create_app()


@pytest.fixture
def cl(app_u, mock):
    with TestClient(app_u, follow_redirects=False) as c:
        yield c


def as_(c, name):
    """Log `c` in as `name`; returns the CSRF token."""
    c.cookies.clear()
    r = login(c, password=PW[name], user=name)
    assert r.status_code == 303, r.text[:300]
    return csrf_of(c.get("/more").text)


# ------------------------------------------------------------------ hashing and the file

def test_scrypt_hash_format_and_verify():
    h = hash_password("correct horse battery")
    algo, salt, dig = h.split("$")
    assert algo == "scrypt" and len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(dig)) == 32
    assert verify("correct horse battery", h) and not verify("wrong", h)
    assert hash_password("x" * 8) != hash_password("x" * 8)          # salted
    assert not verify("anything", None) and not verify("anything", "scrypt$zz$zz") and not verify("", "junk")


def test_default_path(monkeypatch):
    monkeypatch.setenv("CONTROL_USERS_FILE", "/tmp/elsewhere.json")
    assert users.default_path() == "/tmp/elsewhere.json"
    monkeypatch.setenv("CONTROL_USERS_FILE", "")
    assert users.default_path() == ""
    monkeypatch.delenv("CONTROL_USERS_FILE")
    assert users.default_path() == ("/data/users.json" if os.path.isdir("/data") else "")


# ------------------------------------------------------------------ login

@pytest.mark.parametrize("name,display,role", PEOPLE)
def test_login_per_role(cl, name, display, role):
    as_(cl, name)
    h = cl.get("/more").text
    assert f"Signed in as <b>{display}</b> ({role})" in h
    assert f"Signed in as {display} ({role})" in h   # the top bar


def test_wrong_password_and_unknown_user(cl):
    assert login(cl, password="not-the-password", user="olive").status_code == 401
    r = login(cl, password=PW["olive"], user="nobody")
    assert r.status_code == 401 and "Wrong user or password." in r.text
    # the environment's single login no longer works once the file exists
    assert login(cl).status_code == 401
    assert login(cl, password=PW["sam"], user="olive").status_code == 401   # someone else's password


def test_single_user_fallback_is_owner(authed):
    c, _ = authed
    assert "Signed in as <b>alex</b> (owner)" in c.get("/more").text


def test_fallback_display_is_the_reviewer(monkeypatch, mock):
    monkeypatch.setenv("CONTROL_REVIEWER", "Alex T.")
    from app.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        assert login(c).status_code == 303
        assert "Signed in as <b>Alex T.</b> (owner)" in c.get("/more").text


def test_missing_file_falls_back_to_env_user(tmp_path, monkeypatch, mock):
    monkeypatch.setenv("CONTROL_USERS_FILE", str(tmp_path / "not-there.json"))
    from app.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        assert login(c).status_code == 303


def test_unreadable_file_refuses_everyone(tmp_path, monkeypatch, mock):
    bad = tmp_path / "users.json"
    bad.write_text("{not json")
    monkeypatch.setenv("CONTROL_USERS_FILE", str(bad))
    from app.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        r = login(c)   # the env password must NOT work as a fallback
        assert r.status_code == 503 and "users file can&#39;t be read" in r.text


def test_user_added_without_restart_and_removed_user_is_logged_out(cl, ufile):
    as_(cl, "wanda")
    assert cl.get("/more").status_code == 200
    # the owner removes wanda with the CLI: her next click goes to the login page
    write_file(ufile, PEOPLE[:2])
    r = cl.get("/more")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    # a new person can log in at once
    PW["nia"] = "nia-pw-" + "Gm8Rz2Kx5T"
    write_file(ufile, [*PEOPLE[:2], ("nia", "Nia", "approver")])
    assert login(cl, password=PW["nia"], user="nia").status_code == 303


def test_role_change_applies_at_the_next_request(cl, ufile, mock):
    token = as_(cl, "wanda")
    assert cl.post("/decide", data={"id": 7, "decision": "approve", "csrf": token}).status_code == 403
    write_file(ufile, [PEOPLE[0], PEOPLE[1], ("wanda", "Álex Writer", "approver")])
    assert cl.post("/decide", data={"id": 7, "decision": "approve", "csrf": token}).status_code == 303


# ------------------------------------------------------------------ role matrix

def test_writer_is_refused_approver_and_owner_actions(cl, mock):
    token = as_(cl, "wanda")
    refused = [("/decide", {"id": 7, "decision": "approve"}), ("/undo/abc", {}), (f"/tasks/{TID}/accept", {}),
               ("/client-links/new", {}), ("/client-links/3/revoke", {}), ("/engine/2/resume", {}),
               ("/facts/some-fact/confirm", {}), ("/facts/rules/4/confirm", {}), ("/facts/kits/hotel/apply", {}),
               ("/facts/wordings/5/confirm", {}), ("/brand/reset", {}), ("/brand/step/1", {})]
    for path, data in refused:
        r = cl.post(path, data={**data, "csrf": token})
        assert r.status_code == 403, path
        assert "Your role (writer) can&#39;t do this; ask the owner." in r.text, path
    r = cl.post("/calendar/reschedule", json={"id": 1, "start": "2026-10-01T09:00:00Z"}, headers={"X-CSRF-Token": token})
    assert r.status_code == 403
    assert not mock.calls   # nothing reached any service
    # a writer still reads every page
    mock.get(f"{CAL}/items").respond(json=[item(7)])
    mock.get(f"{TASKS}/blockers").respond(json={"blockers": []})
    q = cl.get("/")
    assert q.status_code == 200 and 'hx-post="/decide"' not in q.text and "Read only: approving needs" in q.text


def test_writer_may_write_drafts(cl, mock):
    token = as_(cl, "wanda")
    made = mock.post(f"{BRAND}/facts/v2").respond(json={"key": "open-hours", "status": "draft"})
    r = cl.post("/facts/new", data={"csrf": token, "text": "Open 9 to 5.", "subject_kind": "business",
                                    "subject_ref": "Shop", "fact_type": "hours", "sensitivity": "public",
                                    "claim_class": "none", "risk": "low", "key": "open-hours"})
    assert r.status_code == 303 and made.called
    # X-Actor names the writer; non-ASCII folded for the header
    assert made.calls.last.request.headers["x-actor"] == "Alex Writer"


def test_csrf_is_checked_before_the_role(cl, mock):
    as_(cl, "wanda")
    r = cl.post("/decide", data={"id": 7, "decision": "approve"})
    assert r.status_code == 403 and "CSRF" in r.text


def test_approver_decides_but_cannot_confirm_facts(cl, mock):
    token = as_(cl, "sam")
    conf = mock.post(f"{BRAND}/facts/v2/some-fact/confirm").respond(json={})
    r = cl.post("/facts/some-fact/confirm", data={"csrf": token})
    assert r.status_code == 403 and "Your role (approver)" in r.text and not conf.called
    assert cl.post("/decide", data={"id": 7, "decision": "approve", "csrf": token}).status_code == 303


def test_owner_may_confirm(cl, mock):
    token = as_(cl, "olive")
    conf = mock.post(f"{BRAND}/facts/v2/some-fact/confirm").respond(json={})
    r = cl.post("/facts/some-fact/confirm", data={"csrf": token})
    assert r.status_code == 303 and conf.called
    h = conf.calls.last.request.headers
    assert h["x-actor"] == "Olive Owner" and h["x-owner-key"]


def test_every_post_route_is_role_checked(app_u):
    """No POST route slips through without need(role) (login, logout and the client pages aside)."""
    open_posts = {"/login", "/logout", "/c/{token}", "/c/{token}/respond"}

    def roles(dep):
        out = []
        for d in dep.dependencies:
            if getattr(d.call, "role", None):
                out.append(d.call.role)
            out += roles(d)
        return out

    checked = {}
    for r in app_u.routes:
        if "POST" in getattr(r, "methods", set()) and r.path not in open_posts:
            got = roles(r.dependant)
            assert got, f"{r.path} has no role check"
            checked[r.path] = got[0]
    assert checked["/decide"] == "approver" and checked["/tasks/{task_id}/accept"] == "approver"
    assert checked["/facts/{key}/confirm"] == "owner" and checked["/brand/reset"] == "owner"
    assert checked["/facts/wordings/{wid}/{action}"] == "owner"
    assert checked["/tasks/new"] == "writer" and checked["/facts/setup"] == "writer"
    assert checked["/client-links/new"] == "approver" and checked["/calendar/reschedule"] == "approver"


def test_buttons_hidden_by_role(cl, mock):
    from .test_facts_page import FACTS
    mock.get(f"{BRAND}/facts/v2").respond(json={"facts": FACTS})
    mock.get(f"{BRAND}/questions").respond(json={"questions": []})
    mock.get(f"{BRAND}/disclosure-wordings").respond(json={"wordings": []})
    as_(cl, "sam")
    h = cl.get("/facts").text
    assert 'action="/facts/quayside-delivery/confirm"' not in h and "/facts/quayside-delivery/edit" in h
    as_(cl, "olive")
    assert 'action="/facts/quayside-delivery/confirm"' in cl.get("/facts").text


# ------------------------------------------------------------------ who is named

def test_decisions_are_sent_once_per_reviewer(cl, app_u, mock):
    route = mock.post(WEBHOOK).respond(json={"ok": True, "summary": ["done"]})
    t = as_(cl, "sam")
    cl.post("/decide", data={"id": 7, "decision": "approve", "csrf": t})
    cl.post("/decide", data={"id": 8, "decision": "reject_drop", "csrf": t})
    t = as_(cl, "olive")
    cl.post("/decide", data={"id": 9, "decision": "approve", "csrf": t})
    res = flush(cl, app_u)
    assert len(res) == 2 and route.call_count == 2
    sent = {json.loads(c.request.content)["reviewer"]: json.loads(c.request.content)["decisions"] for c in route.calls}
    assert [d["id"] for d in sent["Sam Parker"]] == [7, 8] and [d["id"] for d in sent["Olive Owner"]] == [9]
    assert all(set(json.loads(c.request.content)) == {"reviewer", "decisions"} for c in route.calls)


def test_client_link_names_the_person(cl, mock):
    token = as_(cl, "sam")
    made = mock.post(f"{CAL}/client-links").respond(json={"id": 1, "token": "t" * 43, "item_ids": [7]})
    r = cl.post("/client-links/new", data={"csrf": token, "item_id": "7", "label": "Oct", "days": "7"})
    assert r.status_code == 201, r.text[:300]
    assert made.calls.last.request.headers["x-actor"] == "Sam Parker"


def test_accept_by_is_the_display_name(cl, mock):
    from .test_tasks import blocked_md_task
    token = as_(cl, "sam")
    mock.get(f"{TASKS}/tasks/{TID}").respond(json=blocked_md_task())
    route = mock.post(f"{TASKS}/tasks/{TID}/pieces/p1/accept").respond(json={"blocked": False})
    r = cl.post(f"/tasks/{TID}/accept", data={"csrf": token, "piece_key": "p1", "finding": "1", "sha": "1" * 64,
                                             "note": "said as per room"})
    assert r.status_code == 200
    assert json.loads(route.calls.last.request.content)["by"] == "Sam Parker"


def test_password_never_in_responses_or_logs(cl, ufile, caplog):
    caplog.set_level(logging.DEBUG)
    bodies = [login(cl, password=PW["olive"] + "x", user="olive").text, login(cl, password=PW["olive"], user="olive").text,
              cl.get("/more").text]
    for pw in PW.values():
        assert all(pw not in b for b in bodies) and pw not in caplog.text
        assert pw not in ufile.read_text()


# ------------------------------------------------------------------ CLI

def cli(monkeypatch, capsys, *args, stdin=""):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    try:
        code = users.main(list(args))
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_add_passwd_remove_list(tmp_path, monkeypatch, capsys):
    f = tmp_path / "data" / "users.json"
    monkeypatch.setenv("CONTROL_USERS_FILE", str(f))
    secret = "first-" + "Pw9Lk2Mz7X"
    code, out, err = cli(monkeypatch, capsys, "add", "olive", "owner", "--display", "Olive  Owner", "--password-stdin",
                         stdin=secret + "\n")
    assert code == 0 and "Added olive (owner)" in out and "no longer work" in err
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    rows = json.loads(f.read_text())["users"]
    assert rows[0]["display"] == "Olive Owner" and rows[0]["pw_hash"].startswith("scrypt$") and secret not in f.read_text()
    assert cli(monkeypatch, capsys, "add", "sam", "approver", "--password-stdin", stdin="short\n")[0] != 0
    assert cli(monkeypatch, capsys, "add", "sam", "approver", "--password-stdin", stdin="approver-pw-1\n")[0] == 0
    assert cli(monkeypatch, capsys, "add", "sam", "writer", "--password-stdin", stdin="approver-pw-1\n")[0] != 0
    assert cli(monkeypatch, capsys, "add", "bad name!", "writer", "--password-stdin", stdin="whatever-pw\n")[0] != 0
    code, out, _ = cli(monkeypatch, capsys, "list")
    assert code == 0 and "olive\towner\tOlive Owner" in out and "sam\tapprover\tsam" in out and "scrypt" not in out

    class S:
        users_file, user, password, reviewer = str(f), "x", "", ""
    assert users.authenticate(S, "olive", secret).role == "owner"
    new = "second-" + "Pw4Hj8Nq1R"
    code, out, err = cli(monkeypatch, capsys, "passwd", "olive", "--password-stdin", stdin=new + "\n")
    assert code == 0 and new not in out + err
    assert users.authenticate(S, "olive", secret) is None and users.authenticate(S, "olive", new).name == "olive"
    assert cli(monkeypatch, capsys, "passwd", "nobody", "--password-stdin", stdin=new + "\n")[0] != 0
    code, _, _ = cli(monkeypatch, capsys, "remove", "olive")
    assert code != 0   # the last owner stays
    assert cli(monkeypatch, capsys, "remove", "sam")[0] == 0
    assert [u["name"] for u in json.loads(f.read_text())["users"]] == ["olive"]
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


def test_cli_prompts_with_getpass(tmp_path, monkeypatch, capsys):
    f = tmp_path / "users.json"
    answers = iter(["typed-" + "Pw3Gb6Vs9C", "typed-" + "Pw3Gb6Vs9C"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    code, out, err = cli(monkeypatch, capsys, "--file", str(f), "add", "nia", "writer")
    assert code == 0 and "typed-" not in out + err
    mismatch = iter(["one-" + "Pw3Gb6Vs9C", "two-" + "Pw3Gb6Vs9C"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(mismatch))
    code, _, _ = cli(monkeypatch, capsys, "--file", str(f), "passwd", "nia")
    assert code != 0
