"""Facts page (05 v2): grouped list with status, scope in words, sensitivity; the add/edit form;
confirm / retire / starter-kit apply send FACT_OWNER_KEY server-side only; questions; derived facts
are read-only and can be turned into a scoped fact. No key or internal URL is ever rendered."""
import json
from datetime import date, timedelta

import pytest

from app import facts_page

from .conftest import KEYS, PASSWORD, URLS

BRAND = URLS["BRAND_URL"]
OWNER = KEYS["FACT_OWNER_KEY"]
TODAY = date(2026, 9, 29)


def fact(key, **kw):
    base = {"key": key, "subject": {"kind": "package", "ref": "Midweek Escape"}, "fact_type": "price",
            "attribute": "rate", "value": 180, "unit": "night", "currency": "GBP", "value_text": "£180 per room per night",
            "basis": "per_room", "conditions": [{"key": "guests", "op": "=", "value": 2}],
            "scope": {"sites": [], "regions": [], "channels": [], "segments": [], "plan_tiers": [], "variants": []},
            "valid_from": "2026-10-01", "valid_to": "2027-03-31", "review_by": None,
            "source": {"kind": "doc", "ref": "Rate sheet v3"}, "claim_class": "none", "requires_evidence": False,
            "evidence_ref": None, "consent_ref": None, "required_disclosures": ["per room per night, 2 sharing"],
            "allowed_phrasing": [], "forbidden_phrasing": [], "owner": "Front office", "risk": "low",
            "sensitivity": "public", "status": "active", "supersedes_key": None, "version": 1, "latest_version": 1,
            "derived": False, "text": f"Fact {key}."}
    base.update(kw)
    return base


FACTS = [
    fact("weekday-rate"),
    fact("quayside-delivery", subject={"kind": "site", "ref": "Quayside"}, sensitivity="internal",
         scope={"sites": ["Quayside"], "channels": ["delivery"]}, status="draft", version=1, latest_version=1),
    fact("old-offer", valid_to="2026-09-01", status="expired", sensitivity="restricted"),
    fact("ending-soon", valid_to=(TODAY + timedelta(days=10)).isoformat()),
    fact("review-me", review_by="2026-09-01"),
    fact("brand-fact-1", subject={"kind": "business", "ref": "Kestrel"}, derived=True, fact_type="claim",
         sensitivity="public", text="We ship worldwide."),
]


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(facts_page, "today", lambda: TODAY)


@pytest.fixture
def five(mock):
    mock.get(f"{BRAND}/facts/v2").respond(json={"fact_set_version": "fs-3-abc", "facts": FACTS})
    mock.get(f"{BRAND}/questions").respond(json={"questions": [
        {"id": 7, "kind": "missing_fact", "fact_key": None, "task_id": "T-ABC123", "text": "What is the Leeds weekday rate?",
         "status": "open", "created_at": "2026-09-28T10:00:00Z"}]})
    return mock


def test_list_groups_status_scope_sensitivity(authed, five):
    c, _ = authed
    h = c.get("/facts").text
    assert "Midweek Escape" in h and "Quayside" in h
    assert "draft: needs your confirmation" in h
    assert "only: Quayside branch; delivery channel" in h
    assert "everywhere" in h
    assert "expired" in h and "due for review" in h
    assert f"expires {(TODAY + timedelta(days=10)).isoformat()}" in h
    assert "internal: only a placeholder leaves" in h and "restricted: never leaves" in h
    assert "2026-10-01 to 2027-03-31" in h and "doc · Rate sheet v3" in h and "Front office" in h
    assert "What is the Leeds weekday rate?" in h and 'href="/tasks/T-ABC123"' in h
    # derived: read-only, convert button, no edit/confirm
    assert "from your brand profile (read-only)" in h
    assert 'action="/facts/brand-fact-1/convert"' in h and "/facts/brand-fact-1/edit" not in h
    assert 'action="/facts/quayside-delivery/confirm"' in h
    assert 'action="/facts/weekday-rate/confirm"' not in h   # active, nothing to confirm


def test_filters_attention_first(authed, five):
    c, _ = authed
    h = c.get("/facts?show=drafts").text
    assert "Fact quayside-delivery." in h and "Fact weekday-rate." not in h
    h = c.get("/facts?show=expiring").text
    assert "Fact ending-soon." in h and "Fact weekday-rate." not in h
    h = c.get("/facts?show=expired").text
    assert "Fact old-offer." in h and "Fact ending-soon." not in h
    h = c.get("/facts?show=attention").text
    assert "Fact review-me." in h and "Fact weekday-rate." not in h
    assert "Needs you (4)" in h and "Open questions (1)" in h
    h = c.get("/facts").text   # attention first inside a group
    assert h.index("Fact old-offer.") < h.index("Fact weekday-rate.")


def test_confirm_and_retire_send_owner_key_and_csrf(authed, five):
    c, token = authed
    conf = five.post(f"{BRAND}/facts/v2/quayside-delivery/confirm").respond(json=fact("quayside-delivery"))
    ret = five.post(f"{BRAND}/facts/v2/weekday-rate/retire").respond(json=fact("weekday-rate", status="retired"))
    assert c.post("/facts/quayside-delivery/confirm", data={}).status_code == 403
    assert c.post("/facts/quayside-delivery/confirm", data={"csrf": "wrong"}).status_code == 403
    assert not conf.called
    r = c.post("/facts/quayside-delivery/confirm", data={"csrf": token})
    assert r.status_code == 303 and r.headers["location"] == "/facts?done=confirmed:quayside-delivery"
    hdr = conf.calls[0].request.headers
    assert hdr["x-owner-key"] == OWNER and hdr["x-api-key"] == KEYS["INTERNAL_API_KEY"]
    assert c.post("/facts/weekday-rate/retire", data={"csrf": token}).status_code == 303
    assert ret.calls[0].request.headers["x-owner-key"] == OWNER
    assert c.post("/facts/Bad_Key!/confirm", data={"csrf": token}).status_code == 404


def test_owner_key_only_on_owner_calls(authed, five):
    c, token = authed
    create = five.post(f"{BRAND}/facts/v2").respond(json=fact("new-fact", status="draft"))
    c.get("/facts")
    c.post("/facts/new", data={"csrf": token, "text": "Open 9 to 5.", "subject_kind": "business", "subject_ref": "Shop",
                               "fact_type": "hours", "sensitivity": "public", "claim_class": "none", "risk": "low"})
    for call in [*five.calls]:
        if not call.request.url.path.endswith(("/confirm", "/retire", "/apply", "/import")):
            assert "x-owner-key" not in call.request.headers, call.request.url
    assert create.called


def fresh(monkeypatch, **env):
    """A client on an app created after these env changes."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return TestClient(create_app(), follow_redirects=False)


def test_owner_actions_refused_without_owner_key(monkeypatch, mock):
    from .conftest import csrf_of, login
    with fresh(monkeypatch, FACT_OWNER_KEY="") as client:
        assert login(client).status_code == 303
        token = csrf_of(client.get("/more").text)
        mock.get(f"{BRAND}/facts/v2").respond(json={"facts": FACTS})
        mock.get(f"{BRAND}/questions").respond(json={"questions": []})
        conf = mock.post(f"{BRAND}/facts/v2/quayside-delivery/confirm").respond(json={})
        h = client.get("/facts").text
        assert "Confirming is switched off" in h
        r = client.post("/facts/quayside-delivery/confirm", data={"csrf": token})
        assert r.status_code == 503 and "FACT_OWNER_KEY is not set" in r.text and not conf.called


def test_add_fact_form_builds_the_fact(authed, five):
    c, token = authed
    create = five.post(f"{BRAND}/facts/v2").respond(json=fact("midweek-escape-rate", status="draft"))
    form = {"csrf": token, "text": "Midweek Escape costs £180 per room per night.", "subject_kind": "package",
            "subject_ref": "Midweek Escape", "fact_type": "price", "value_text": "£180 per room per night",
            "valid_from": "2026-10-01", "valid_to": "2027-03-31", "sensitivity": "internal",
            "scope_sites": "Quayside, Leeds", "scope_channels": "", "attribute": "rate", "basis": "per_room",
            "value": "180", "unit": "night", "currency": "gbp", "c_key": ["guests", "days", ""], "c_op": ["=", "in", "="],
            "c_value": ["2", "Sun, Mon", ""], "review_by": "", "source_kind": "doc", "source_ref": "Rate sheet",
            "claim_class": "none", "required_disclosures": "per room per night\n\n2 sharing", "allowed_phrasing": "",
            "forbidden_phrasing": "from £180", "owner": "Front office", "risk": "low", "requires_evidence": "yes"}
    r = c.post("/facts/new", data=form)
    assert r.status_code == 303 and r.headers["location"] == "/facts?done=saved:midweek-escape-rate"
    body = json.loads(create.calls[0].request.content)
    assert body["key"] == "midweek-escape-rate"
    assert body["subject"] == {"kind": "package", "ref": "Midweek Escape"}
    assert body["value"] == 180 and body["currency"] == "GBP" and body["basis"] == "per_room"
    assert body["conditions"] == [{"key": "guests", "op": "=", "value": 2}, {"key": "days", "op": "in", "value": ["Sun", "Mon"]}]
    assert body["scope"]["sites"] == ["Quayside", "Leeds"] and body["scope"]["channels"] == []
    assert body["required_disclosures"] == ["per room per night", "2 sharing"]
    assert body["forbidden_phrasing"] == ["from £180"] and body["requires_evidence"] is True
    assert body["source"] == {"kind": "doc", "ref": "Rate sheet"} and body["sensitivity"] == "internal"
    assert create.calls[0].request.headers.get("x-owner-key") is None


def test_form_validates_before_calling_05(authed, five):
    c, token = authed
    create = five.post(f"{BRAND}/facts/v2").respond(json={})
    r = c.post("/facts/new", data={"csrf": token, "text": "", "subject_kind": "spaceship", "fact_type": "price",
                                   "valid_from": "2026-13-01", "valid_to": "2026-01-01", "currency": "pounds",
                                   "sensitivity": "public", "claim_class": "none", "risk": "low", "key": "Bad Key",
                                   "c_key": [""], "c_op": ["="], "c_value": ["2"]})
    assert r.status_code == 422 and not create.called
    h = r.text
    for msg in ("required: the fact as one plain sentence", "pick one from the list", "a date like 2026-10-01",
                "three letters, like GBP", "small letters, digits and dashes", "Condition 1: name what it depends on"):
        assert msg in h, msg
    assert 'aria-invalid="true"' in h


def test_05_validation_errors_shown_next_to_the_field(authed, five):
    c, token = authed
    five.post(f"{BRAND}/facts/v2").respond(422, json={"detail": [
        {"loc": ["body", "scope", "sites", 0], "msg": "String should have at most 80 characters", "type": "x"}]})
    r = c.post("/facts/new", data={"csrf": token, "text": "A fact.", "subject_kind": "business", "fact_type": "claim",
                                   "sensitivity": "public", "claim_class": "none", "risk": "low", "scope_sites": "Q"})
    assert r.status_code == 422 and 'id="err-scope_sites"' in r.text and "at most 80 characters" in r.text


def test_add_condition_row_without_js(authed, five):
    c, token = authed
    create = five.post(f"{BRAND}/facts/v2").respond(json={})
    r = c.post("/facts/new", data={"csrf": token, "action": "add_condition", "text": "x", "c_key": ["a"], "c_op": ["="],
                                   "c_value": ["1"], "subject_kind": "business", "fact_type": "claim",
                                   "sensitivity": "public", "claim_class": "none", "risk": "low"})
    assert r.status_code == 200 and r.text.count('name="c_key"') == 2 and not create.called


def test_edit_uses_latest_version_and_puts(authed, five):
    c, token = authed
    draft = fact("weekday-rate", value_text="£190 per room per night")
    five.get(f"{BRAND}/facts/v2/weekday-rate").respond(json=fact("weekday-rate") | {"versions": [
        {"version": 1, "status": "active", "data": fact("weekday-rate")},
        {"version": 2, "status": "draft", "data": draft}]})
    put = five.put(f"{BRAND}/facts/v2/weekday-rate").respond(json=fact("weekday-rate", latest_version=2))
    h = c.get("/facts/weekday-rate/edit").text
    assert "£190 per room per night" in h and 'readonly' in h
    r = c.post("/facts/weekday-rate/edit", data={"csrf": token, "key": "other-key", "text": "Changed.",
                                                 "subject_kind": "package", "fact_type": "price", "sensitivity": "public",
                                                 "claim_class": "none", "risk": "low"})
    assert r.status_code == 303
    assert json.loads(put.calls[0].request.content)["key"] == "weekday-rate"   # URL key wins


def test_convert_derived_fact_posts_supersedes_key(authed, five):
    c, token = authed
    five.get(f"{BRAND}/facts/v2/brand-fact-1").respond(json=FACTS[-1])
    create = five.post(f"{BRAND}/facts/v2").respond(json={})
    assert c.post("/facts/brand-fact-1/convert", data={}).status_code == 403
    r = c.post("/facts/brand-fact-1/convert", data={"csrf": token})
    assert r.status_code == 303 and r.headers["location"] == "/facts/brand-fact-1-scoped/edit"
    body = json.loads(create.calls[0].request.content)
    assert body["supersedes_key"] == "brand-fact-1" and body["key"] == "brand-fact-1-scoped"
    assert body["text"] == "We ship worldwide." and body["sensitivity"] == "public"
    assert "x-owner-key" not in create.calls[0].request.headers


def test_starter_kit_preview_and_apply(authed, five):
    c, token = authed
    kit = {"id": "clinic", "label": "Clinic", "description": "Health services.", "claim_classes": ["regulated_health"],
           "fact_types": [{"fact_type": "credential", "subject_kind": "person", "attributes": ["registration"],
                           "example": "Dr Lee is HCPC registered."}],
           "forbidden_phrasing": [{"phrase": "specialist", "why": "Only for registered specialists.",
                                   "claim_class": "regulated_health", "needs": "a specialist registration fact"}],
           "required_disclosures": [{"when": "prices", "text": "Prices from our 2026 list", "claim_class": "none"}]}
    five.get(f"{BRAND}/starter-kits").respond(json=[kit])
    five.get(f"{BRAND}/rules").respond(json={"rules": [{"id": 3, "kit": "clinic", "kind": "forbidden_phrase",
        "phrase": "specialist", "why": "Only for registered specialists.", "claim_class": "regulated_health",
        "needs": None, "status": "draft", "created_at": "x"}]})
    apply = five.post(f"{BRAND}/starter-kits/clinic/apply").respond(json={"kit": "clinic", "created": 2})
    rule = five.post(f"{BRAND}/rules/3/confirm").respond(json={})
    h = c.get("/facts/kits?kit=clinic").text
    assert "“specialist”" in h and "Only for registered specialists." in h and "Prices from our 2026 list" in h
    assert "Dr Lee is HCPC registered." in h
    assert c.post("/facts/kits/clinic/apply", data={}).status_code == 403 and not apply.called
    r = c.post("/facts/kits/clinic/apply", data={"csrf": token})
    assert r.status_code == 303 and apply.calls[0].request.headers["x-owner-key"] == OWNER
    assert c.post("/facts/rules/3/confirm", data={"csrf": token}).status_code == 303
    assert rule.calls[0].request.headers["x-owner-key"] == OWNER
    assert c.post("/facts/rules/3/delete", data={"csrf": token}).status_code == 404


def test_questions_answer_and_dismiss(authed, five):
    c, token = authed
    ans = five.post(f"{BRAND}/questions/7/answer").respond(json={})
    dis = five.post(f"{BRAND}/questions/7/dismiss").respond(json={})
    assert c.post("/facts/questions/7/answer", data={"answer": "£150"}).status_code == 403
    r = c.post("/facts/questions/7/answer", data={"csrf": token, "answer": ""})
    assert r.status_code == 422 and not ans.called
    assert c.post("/facts/questions/7/answer", data={"csrf": token, "answer": "£150"}).status_code == 303
    assert json.loads(ans.calls[0].request.content) == {"answer": "£150"}
    assert "x-owner-key" not in ans.calls[0].request.headers
    assert c.post("/facts/questions/7/dismiss", data={"csrf": token}).status_code == 303 and dis.called


def test_no_key_in_any_facts_response(authed, five):
    c, token = authed
    five.get(f"{BRAND}/starter-kits").respond(json=[])
    five.get(f"{BRAND}/rules").respond(json={"rules": []})
    five.post(f"{BRAND}/facts/v2/weekday-rate/confirm").respond(403, json={"detail": "missing or wrong X-Owner-Key"})
    bodies = [c.get(p).text for p in ("/facts", "/facts?show=questions", "/facts/new", "/facts/kits")]
    bodies.append(c.post("/facts/weekday-rate/confirm", data={"csrf": token}).text)
    everything = "\n".join(bodies)
    for secret in [*KEYS.values(), PASSWORD, BRAND, "brand.internal"]:
        assert secret not in everything


def test_not_installed_and_login_required(monkeypatch, client, mock):
    for p in ("/facts", "/facts/new", "/facts/kits"):
        r = client.get(p)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.post("/facts/x-y/confirm").status_code == 401
    from .conftest import login
    with fresh(monkeypatch, BRAND_URL="") as c2:
        login(c2)
        for p in ("/facts", "/facts/new", "/facts/kits"):
            r = c2.get(p)
            assert r.status_code == 200 and "isn't installed" in r.text, p


def test_scope_words():
    assert facts_page.scope_words({"sites": ["Quayside"], "channels": ["delivery"]}) == "only: Quayside branch; delivery channel"
    assert facts_page.scope_words({"sites": ["A", "B"]}) == "only: A or B branch"
    assert facts_page.scope_words({}) == "everywhere"
