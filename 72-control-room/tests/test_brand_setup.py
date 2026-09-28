"""Brand setup pages: each step saves through 05 with the key (server-side), 05's validation
errors appear next to the field, CSRF is enforced, and no key or internal URL is rendered."""
import copy
import json

import httpx
import pytest

from .conftest import KEYS, PASSWORD, URLS

BRAND, GW = URLS["BRAND_URL"], URLS["GATEWAY_URL"]
EDIT = f"{BRAND}/brand/editable"
QUESTIONS = [f"Question {i}?" for i in range(1, 11)]
DOC = {
    "brand": {
        "name": "Northwind Roasters", "one_liner": "Specialty coffee, roasted to order.",
        "website": "https://northwind-roasters.example.com",
        "audience": {"primary": "Remote workers.", "secondary": ""},
        "voice": {"tone": "Warm", "do": [], "dont": []},
        "products": [{"name": "Desk Blend", "one_line": "Chocolate-hazelnut medium roast.", "price": "$18 / 340 g", "aliases": []},
                     {"name": "Swiss Water decaf", "one_line": "Small batches.", "price": None, "aliases": ["decaf"]}],
        "facts": ["Fact one.", "Fact two."], "key_messages": ["Roasted within 48 hours of shipping."],
        "banned_phrases": ["guaranteed", "best * in the world"],
        "emoji_policy": {"max_per_post": 2, "allowed": ["☕", "🌱"]},
        "allowed_domains": [], "required_disclaimers": {"paid_social": ["#ad", "Sponsored"]},
    },
    "overridden": [], "products": {"changed": [], "added": [], "removed": []},
}
COMPLETENESS = {"score": 88, "done": 7, "total": 8, "missing": ["Answer the 10 voice questions (step 5)."], "checks": [
    {"id": "facts", "label": "At least 5 facts", "ok": True, "missing": None},
    {"id": "voice", "label": "Voice interview", "ok": False, "missing": "Answer the 10 voice questions (step 5)."}]}
PROFILE = {"summary": "Write like a friendly barista.", "do": ["Sentences under 15 words"], "dont": ["No hype"],
           "words_we_use": ["fresh"], "words_we_avoid": ["elevate"], "sentence_style": "Short, under 12 words",
           "sample_lines": ["Fresh beans, roasted Monday."]}
HX = {"HX-Request": "true"}


@pytest.fixture
def brand(mock):
    mock.get(EDIT).respond(json=DOC)
    mock.get(f"{BRAND}/brand/completeness").respond(json=COMPLETENESS)
    mock.get(f"{BRAND}/profile/summary").respond(json={"summary": "Brand: Northwind Roasters - Specialty coffee"})
    mock.get(f"{BRAND}/voice/questions").respond(json={"questions": QUESTIONS})
    mock.get(f"{BRAND}/voice").respond(404, json={"detail": "no voice profile stored"})
    return mock


def saved_doc(**brand):
    d = copy.deepcopy(DOC)
    d["brand"].update(brand)
    return d


def test_every_brand_page_renders(authed, brand):
    c, _ = authed
    for p in ["/brand", *(f"/brand/step/{n}" for n in range(1, 7))]:
        r = c.get(p)
        assert r.status_code == 200, (p, r.text[:300])
    assert "88 %" in c.get("/brand").text
    assert 'value="Northwind Roasters"' in c.get("/brand/step/1").text
    assert "Desk Blend" in c.get("/brand/step/2").text
    assert "Fact one.\nFact two." in c.get("/brand/step/3").text
    assert "paid_social: #ad, Sponsored" in c.get("/brand/step/4").text
    assert "Question 10?" in c.get("/brand/step/5").text
    six = c.get("/brand/step/6").text
    assert "Brand: Northwind Roasters" in six and "Voice interview" in six and "within 60 s" in six
    assert c.get("/brand/step/9").status_code == 404
    assert "/brand" in c.get("/more").text


def test_step1_saves_with_key_and_says_when_the_agent_uses_it(authed, brand):
    c, token = authed
    put = brand.put(EDIT).respond(json=saved_doc(name="Acme Tea"))
    form = {"csrf": token, "name": "Acme Tea", "one_liner": "Tea by post.", "website": "https://acme.example",
            "audience_primary": "Office teams.", "audience_secondary": "", "tone": "Calm"}
    r = c.post("/brand/step/1", data=form)
    assert r.status_code == 303 and r.headers["location"] == "/brand/step/1?saved=1"
    req = put.calls.last.request
    assert req.headers["X-API-Key"] == KEYS["INTERNAL_API_KEY"]
    assert json.loads(req.content) == {"name": "Acme Tea", "one_liner": "Tea by post.", "website": "https://acme.example",
                                       "audience": {"primary": "Office teams.", "secondary": ""}, "voice": {"tone": "Calm"}}
    assert "within 60 s" in c.get("/brand/step/1?saved=1").text
    # htmx: the form fragment comes back with the saved values and the note
    frag = c.post("/brand/step/1", data=form, headers=HX)
    assert frag.status_code == 200 and frag.text.lstrip().startswith("<div id=\"brand-form\">")
    assert "Saved." in frag.text and "within 60 s" in frag.text and 'value="Acme Tea"' in frag.text
    assert "<html" not in frag.text


def test_step2_products_rows_add_remove_and_price_edit(authed, brand):
    c, token = authed
    put = brand.put(EDIT).respond(json=DOC)
    form = {"csrf": token, "action": "save",
            "p_name": ["Desk Blend", "Swiss Water decaf", "Cold Brew", ""],
            "p_one_line": ["Chocolate-hazelnut medium roast.", "Small batches.", "Four bottles.", ""],
            "p_price": ["$19 / 340 g", "", "$24", ""],
            "p_aliases": ["", "decaf, unleaded", "", ""],
            "p_remove": ["1"]}
    r = c.post("/brand/step/2", data=form, headers=HX)
    assert r.status_code == 200 and "Saved." in r.text
    assert json.loads(put.calls.last.request.content) == {"products": [
        {"name": "Desk Blend", "one_line": "Chocolate-hazelnut medium roast.", "price": "$19 / 340 g", "aliases": []},
        {"name": "Cold Brew", "one_line": "Four bottles.", "price": "$24", "aliases": []}]}
    # "Add another product" re-renders with one more row and saves nothing
    n = put.call_count
    r = c.post("/brand/step/2", data={**form, "action": "add"}, headers=HX)
    assert put.call_count == n and r.text.count("<legend>Product") == 5


def test_step2_row_error_from_05_points_at_the_row(authed, brand):
    c, token = authed
    brand.put(EDIT).respond(422, json={"detail": [
        {"type": "missing", "loc": ["body", "products", 1, "one_line"], "msg": "Field required"}]})
    form = {"csrf": token, "action": "save", "p_name": ["", "Desk Blend", "Cold Brew"],
            "p_one_line": ["", "Roast.", ""], "p_price": ["", "", ""], "p_aliases": ["", "", ""]}
    r = c.post("/brand/step/2", data=form)
    assert r.status_code == 422
    # the blank first row is skipped, so 05's index 1 is the third row on the page
    assert "Not saved yet" in r.text and "one-line description: required" in r.text
    assert 'class="product-row has-error"' in r.text
    third = r.text.split("<legend>Product 3</legend>")[1].split("</fieldset>")[0]
    assert "required" in third


def test_step3_fact_error_names_the_line(authed, brand):
    c, token = authed
    brand.put(EDIT).respond(422, json={"detail": [
        {"type": "string_too_long", "loc": ["body", "facts", 1], "msg": "String should have at most 200 characters"}]})
    r = c.post("/brand/step/3", data={"csrf": token, "facts": "Short fact.\n\n" + "x" * 250, "key_messages": ""}, headers=HX)
    assert r.status_code == 200
    assert "Line 3: String should have at most 200 characters" in r.text   # the blank line counts
    assert "x" * 250 in r.text                                             # what was typed is kept


def test_step4_rules_parse_and_local_errors(authed, brand):
    c, token = authed
    put = brand.put(EDIT).respond(json=DOC)
    form = {"csrf": token, "banned_phrases": "guaranteed\n miracle \n", "emoji_allowed": "☕ 🌱, 📦",
            "emoji_max": "2", "allowed_domains": "shop.example.org", "disclaimers": "Paid_Social: #ad, Sponsored\nsms: Reply STOP"}
    assert c.post("/brand/step/4", data=form).status_code == 303
    assert json.loads(put.calls.last.request.content) == {
        "banned_phrases": ["guaranteed", "miracle"], "allowed_domains": ["shop.example.org"],
        "required_disclaimers": {"paid_social": ["#ad", "Sponsored"], "sms": ["Reply STOP"]},
        "emoji_policy": {"allowed": ["☕", "🌱", "📦"], "max_per_post": 2}}
    n = put.call_count
    r = c.post("/brand/step/4", data={**form, "emoji_max": "two", "disclaimers": "paid_social #ad"})
    assert r.status_code == 422 and put.call_count == n
    assert "a whole number" in r.text and "Line 1: write it as channel: text" in r.text
    # blank max = no limit
    c.post("/brand/step/4", data={**form, "emoji_max": ""})
    assert json.loads(put.calls.last.request.content)["emoji_policy"]["max_per_post"] is None


def test_step4_emoji_error_from_05_is_shown_on_the_emoji_field(authed, brand):
    c, token = authed
    brand.put(EDIT).respond(422, json={"detail": [{"type": "value_error", "loc": ["body", "emoji_policy", "allowed"],
                                                   "msg": "Value error, item 2 ('ok') is not a single emoji"}]})
    r = c.post("/brand/step/4", data={"csrf": token, "banned_phrases": "", "emoji_allowed": "☕ ok", "emoji_max": "",
                                      "allowed_domains": "", "disclaimers": ""}, headers=HX)
    assert "item 2 (&#39;ok&#39;) is not a single emoji" in r.text and "Value error" not in r.text


def test_voice_interview_generates_reviews_and_saves(authed, brand):
    c, token = authed
    run = brand.post(f"{GW}/v1/run").respond(json={"prompt": "voice_profile", "output": PROFILE})
    answers = {f"a{i}": "" for i in range(10)}
    few = c.post("/brand/voice/generate", data={"csrf": token, **answers, "a0": "Remote workers."}, headers=HX)
    assert "Answer at least 3" in few.text and not run.called
    answers.update(a0="Remote workers.", a2="Warm, dry, plain.", a9="Never health claims.")
    r = c.post("/brand/voice/generate", data={"csrf": token, **answers}, headers=HX)
    req = run.calls.last.request
    assert req.headers["X-API-Key"] == KEYS["INTERNAL_API_KEY"]
    assert req.headers["X-Caller"] == "72 voice interview"      # the gateway's activity log shows it
    body = json.loads(req.content)
    assert body["prompt"] == "voice_profile"
    assert body["vars"]["answers"] == ("Q: Question 1?\nA: Remote workers.\n\nQ: Question 3?\nA: Warm, dry, plain.\n\n"
                                       "Q: Question 10?\nA: Never health claims.")
    assert "Write like a friendly barista." in r.text and "Save voice profile" in r.text
    put = brand.put(f"{BRAND}/voice").respond(json=PROFILE)
    form = {"csrf": token, "summary": PROFILE["summary"], "do": "Sentences under 15 words\n", "dont": "No hype",
            "words_we_use": "fresh", "words_we_avoid": "elevate", "sentence_style": "Short, under 12 words",
            "sample_lines": "Fresh beans, roasted Monday."}
    s = c.post("/brand/voice/save", data=form, headers=HX)
    assert json.loads(put.calls.last.request.content) == PROFILE
    assert put.calls.last.request.headers["X-API-Key"] == KEYS["INTERNAL_API_KEY"]
    assert "Saved." in s.text and "within 60 s" in s.text


def test_voice_save_422_and_gateway_down(authed, brand):
    c, token = authed
    brand.put(f"{BRAND}/voice").respond(422, json={"detail": [
        {"type": "too_long", "loc": ["body", "do"], "msg": "List should have at most 6 items"}]})
    r = c.post("/brand/voice/save", data={"csrf": token, "summary": "x", "do": "\n".join("abcdefg")})
    assert r.status_code == 422 and "List should have at most 6 items" in r.text
    brand.post(f"{GW}/v1/run").mock(side_effect=httpx.ConnectError("down"))
    answers = {f"a{i}": "answer" for i in range(10)}
    r = c.post("/brand/voice/generate", data={"csrf": token, **answers})
    assert r.status_code == 502 and "could not write the profile" in r.text and "gateway.internal" not in r.text
    assert r.text.count(">answer</textarea>") == 10      # answers are kept


def test_reset_needs_confirmation_and_calls_delete_with_key(authed, brand):
    c, token = authed
    delete = brand.delete(EDIT).respond(json={"deleted": True})
    r = c.post("/brand/reset", data={"csrf": token})
    assert r.status_code == 422 and "Tick the box" in r.text and not delete.called
    r = c.post("/brand/reset", data={"csrf": token, "confirm": "yes"})
    assert r.status_code == 303 and delete.calls.last.request.headers["X-API-Key"] == KEYS["INTERNAL_API_KEY"]


@pytest.mark.parametrize("path", ["/brand/step/1", "/brand/step/2", "/brand/step/3", "/brand/step/4",
                                  "/brand/voice/generate", "/brand/voice/save", "/brand/reset"])
def test_csrf_enforced_on_every_post(authed, brand, path):
    c, _ = authed
    put = brand.put(EDIT).respond(json=DOC)
    assert c.post(path, data={"name": "X", "confirm": "yes"}).status_code == 403
    assert c.post(path, data={"csrf": "wrong", "name": "X"}).status_code == 403
    assert c.post(path, data={"name": "X"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert not put.called


def test_brand_pages_need_login(client, mock):
    assert client.get("/brand/step/1").status_code == 303
    assert client.post("/brand/step/1", data={"name": "X"}).status_code == 401


def test_brand_service_down_shows_a_safe_error(authed, mock):
    c, token = authed
    mock.get(EDIT).mock(side_effect=httpx.ConnectError("down"))
    mock.get(f"{BRAND}/brand/completeness").mock(side_effect=httpx.ConnectError("down"))
    r = c.get("/brand/step/1")
    assert r.status_code == 200 and "did not answer" in r.text and "brand.internal" not in r.text
    assert "did not answer" in c.get("/brand").text


def test_no_secret_or_internal_url_in_brand_pages(authed, brand):
    c, token = authed
    brand.put(EDIT).respond(422, json={"detail": [{"loc": ["body", "name"], "msg": "bad", "type": "x"}]})
    brand.post(f"{GW}/v1/run").respond(json={"output": PROFILE})
    brand.put(f"{BRAND}/voice").respond(json=PROFILE)
    bodies = [c.get(p).text for p in ["/brand", *(f"/brand/step/{n}" for n in range(1, 7))]]
    bodies.append(c.post("/brand/step/1", data={"csrf": token, "name": "X"}, headers=HX).text)
    bodies.append(c.post("/brand/voice/generate", data={"csrf": token, **{f"a{i}": "yes" for i in range(10)}}, headers=HX).text)
    bodies.append(c.post("/brand/voice/save", data={"csrf": token, "summary": "Write like us."}, headers=HX).text)
    everything = "\n".join(bodies)
    for secret in [*KEYS.values(), PASSWORD]:
        assert secret not in everything
    for url in URLS.values():
        assert url.split("//")[1].split(":")[0] not in everything, url
    assert "X-API-Key" not in everything
