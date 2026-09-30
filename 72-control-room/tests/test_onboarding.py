"""Set up from your website or documents (onboarding): sources through 07, proposals from the model
(03 propose_facts) or from any chatbot's pasted answer, every quote verified against the source,
duplicates set aside, owner ticks -> DRAFT facts in 05 with the chosen sensitivity. Nothing is ever
confirmed here. 05, 07 and 03 are mocked with respx."""
import base64
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import onboarding as ob

from .conftest import KEYS, PASSWORD, URLS, csrf_of, login

BRAND, GW = URLS["BRAND_URL"], URLS["GATEWAY_URL"]
EXT = "http://extractor.internal:8000"
SECRET_BASE = "https://api.secret-provider.example/v1"

PAGE1 = ("Lake Ember Lodge - Rates 2026-27\n"
         "Midweek Escape: £180 per room per night, 2 sharing, Sunday to Thursday, 1 October 2026 to 31 March 2027.\n"
         "Weekend rate: £240 per room per night, room only. Breakfast £15 per person.")
PAGE2 = ("Airport transfers available on request.\n"
         "At our Keswick annexe, dogs are welcome (£20 per stay).\n"
         "Lake Ember Lodge is on the east shore of Lake Ember.")
PDF_ANSWER = {"url": None, "title": "Rates", "source": "pdf", "filename": "rates.pdf", "page_count": 2,
              "text": PAGE1 + "\n\n" + PAGE2, "pages": [{"page": 1, "text": PAGE1}, {"page": 2, "text": PAGE2}]}
EXISTING = [
    {"key": "lodge-location", "subject": {"kind": "business", "ref": ""}, "fact_type": "claim", "status": "active",
     "sensitivity": "public", "text": "Lake Ember Lodge is on the east shore of Lake Ember.", "value_text": None},
    {"key": "owner-margin", "subject": {"kind": "business", "ref": ""}, "fact_type": "claim", "status": "active",
     "sensitivity": "internal", "text": "INTERNAL-SENTINEL margin is 38 percent.", "value_text": None},
    {"key": "weekend-rate", "subject": {"kind": "package", "ref": "Weekend rate"}, "fact_type": "price",
     "status": "draft", "sensitivity": "public", "text": "Weekends cost £240.", "value_text": "£240 per room per night"},
]
KITS = [{"id": "hospitality", "label": "Hospitality / accommodation"}, {"id": "retail", "label": "Retail"}]

GOOD = 'FACT | package | Midweek Escape | price | £180 per room per night | everywhere | - | - | quote: "Midweek Escape: £180 per room per night, 2 sharing, Sunday to Thursday"'
INVENTED = 'FACT | service | Spa | price | £50 per person | everywhere | - | - | quote: "Spa treatments £50 per person."'
DUPLICATE = 'FACT | package | Weekend rate | price | £240 per room per night | - | - | - | quote: "Weekend rate: £240 per room per night, room only."'
SCOPED = 'FACT | policy | Dogs | policy | £20 per stay | site: Keswick annexe | - | - | quote: "At our Keswick annexe, dogs are welcome (£20 per stay)."'
QUESTION = 'QUESTION | What do airport transfers cost? | quote: "Airport transfers available on request."'


# ------------------------------------------------------------------ pure logic

def sources():
    return [{"kind": "doc", "ref": "rates.pdf", "title": None,
             "pages": [{"page": 1, "text": PAGE1}, {"page": 2, "text": PAGE2}]}]


def index(facts=EXISTING):
    return ob.existing_index(facts)


def raw(quote, **kw):
    base = {"text": "", "subject": {"kind": "package", "ref": "Midweek Escape"}, "fact_type": "price",
            "value_text": "£180 per room per night", "scope_hints": {}, "source_quote": quote}
    return base | kw


def test_invented_quote_is_rejected_negative_control():
    p, why = ob.check(raw("Midweek Escape: £150 per room per night, 2 sharing"), sources(), index(), [], "t")
    assert p is None and "not in the source text" in why
    p, why = ob.check(raw("Spa treatments £50 per person.", value_text="£50 per person"), sources(), index(), [], "t")
    assert p is None and "not in the source text" in why


def test_real_quote_survives_whitespace_case_and_smart_quotes():
    q = "“midweek   escape: £180 per room per night,\n2 sharing, SUNDAY to Thursday”"
    p, why = ob.check(raw(q), sources(), index(), [], "t")
    assert why is None and p["where"] == "rates.pdf p.1" and p["source"] == {"kind": "doc", "ref": "rates.pdf p.1"}
    assert p["context"][1].startswith("Midweek Escape: £180") and "Lake Ember Lodge" in p["context"][0]
    assert p["basis"] == "per_room"


@pytest.mark.parametrize("quote, why", [
    ("Midweek Escape: £180 ... Sunday to Thursday", "shortened"),
    ("£180", "too short"),
    ("", "too short"),
])
def test_shortened_or_trivial_quotes_are_rejected(quote, why):
    p, reason = ob.check(raw(quote), sources(), index(), [], "t")
    assert p is None and why in reason


def test_value_number_must_be_in_the_quote_and_sentence_numbers_in_the_source():
    p, why = ob.check(raw("Midweek Escape: £180 per room per night", value_text="£90 per person"),
                      sources(), index(), [], "t")
    assert p is None and "90" in why
    p, why = ob.check(raw("Midweek Escape: £180 per room per night", text="Midweek Escape is £180, about £25 a head."),
                      sources(), index(), [], "t")
    assert p is None and "25" in why
    # a number elsewhere on the same page is fine in the sentence (2 sharing)
    p, why = ob.check(raw("Midweek Escape: £180 per room per night", text="Midweek Escape is £180 per room per night, 2 sharing."),
                      sources(), index(), [], "t")
    assert why is None


def test_dates_are_kept_only_when_the_quote_states_them():
    q = "Midweek Escape: £180 per room per night, 2 sharing, Sunday to Thursday, 1 October 2026 to 31 March 2027."
    p, _ = ob.check(raw(q, valid_from="2026-10-01", valid_to="2027-03-31"), sources(), index(), [], "t")
    assert (p["valid_from"], p["valid_to"]) == ("2026-10-01", "2027-03-31")
    p, _ = ob.check(raw("Midweek Escape: £180 per room per night", valid_to="2027-03-31"), sources(), index(), [], "t")
    assert p["valid_to"] is None and any("does not state it" in n for n in p["notes"])
    p, _ = ob.check(raw("Midweek Escape: £180 per room per night", valid_to="31/03/2027"), sources(), index(), [], "t")
    assert p["valid_to"] is None and any("not a date" in n for n in p["notes"])


def test_dedupe_against_existing_facts_and_within_the_batch():
    p, why = ob.check(raw("Lake Ember Lodge is on the east shore of Lake Ember.", text="lake ember lodge is on the  east shore of Lake Ember.",
                          subject={"kind": "business", "ref": ""}, value_text=""), sources(), index(), [], "t")
    assert p is None and why == "already in your facts (lodge-location)"
    p, why = ob.check(raw("Weekend rate: £240 per room per night, room only.", subject={"kind": "package", "ref": "weekend rate"},
                          value_text="£240 per room per night"), sources(), index(), [], "t")
    assert p is None and "weekend-rate" in why
    first, _ = ob.check(raw("Midweek Escape: £180 per room per night"), sources(), index(), [], "t")
    again, why = ob.check(raw("Midweek Escape: £180 per room per night, 2 sharing"), sources(), index(), [first], "t")
    assert again is None and why == "same as proposal 1"


def test_chunks_mark_pages_stay_small_and_never_cross_sources():
    long = [{"page": i, "text": ("Sentence number %d is here. " % i) * 60} for i in range(1, 6)]
    srcs = [{"kind": "doc", "ref": "a.pdf", "pages": long},
            {"kind": "url", "ref": "https://x.example/", "pages": [{"page": None, "text": "Open 9 to 5."}]}]
    chunks = ob.make_chunks(srcs, size=2000)
    assert all(len(c["text"]) <= 2000 for c in chunks)
    assert chunks[0]["text"].startswith("[page 1]") and [c["n"] for c in chunks] == list(range(1, len(chunks) + 1))
    assert chunks[-1] == {"source": 1, "pages": [], "text": "Open 9 to 5.", "n": len(chunks)}
    assert all(c["source"] == 0 for c in chunks[:-1])
    # every page's text survives chunking (quotes are then found in the source, not the chunk)
    joined = " ".join(c["text"] for c in chunks[:-1])
    assert joined.count("Sentence number 3 is here.") == 60


def test_known_facts_in_a_pack_are_public_only():
    known = ob.known_lines(EXISTING)
    assert "east shore" in known and "INTERNAL-SENTINEL" not in known


# ------------------------------------------------------------------ chatbot answers, tolerant parsing

VARIANTS = {
    "plain": GOOD,
    "preamble and sign-off": "Sure! Here are the facts I found:\n\n" + GOOD + "\n\nLet me know if you need anything else!",
    "code fence": "```\n" + GOOD + "\n```",
    "bullets": "- " + GOOD,
    "numbered": "1. " + GOOD,
    "bold label": GOOD.replace("FACT |", "**FACT** |"),
    "markdown table": ("| FACT | subject kind | subject | type | value as written | scope | valid from | valid to | quote |\n"
                       "|---|---|---|---|---|---|---|---|---|\n| " + GOOD + " |"),
    "smart quotes": GOOD.replace('quote: "', "quote: “").rstrip('"') + "”",
    "no quote label": GOOD.replace('quote: "', '"'),
    "lower case and n/a": GOOD.lower().replace("| - | - |", "| n/a | none |").replace("midweek escape: £180", "Midweek Escape: £180"),
    "FACT: label": GOOD.replace("FACT | package", "FACT: package"),
    "escaped pipes": GOOD.replace(" | ", " \\| "),
    "full-width bars": GOOD.replace(" | ", " ｜ "),
    "tabs": GOOD.replace(" | ", "\t"),
}


@pytest.mark.parametrize("name", list(VARIANTS))
def test_pack_answer_variants_parse_to_one_verified_fact(name):
    facts, questions, problems = ob.parse_answer(VARIANTS[name])
    assert len(facts) == 1 and not questions and not problems, (name, facts, problems)
    f = facts[0]
    assert f["subject"]["kind"].lower() == "package" and f["fact_type"].lower() == "price"
    p, why = ob.check(f, sources(), index(), [], "chatbot")
    assert why is None, (name, why, f)
    assert p["value_text"].lower() == "£180 per room per night" and p["valid_from"] is None


def test_pack_answer_scope_questions_and_problems():
    text = "\n".join([SCOPED, QUESTION, "QUESTION | Is parking free?", "Note | this | line | has | no | head",
                      "FACT | package | only three"])
    facts, questions, problems = ob.parse_answer(text)
    assert facts[0]["scope_hints"]["sites"] == ["Keswick annexe"]
    assert questions == [{"text": "What do airport transfers cost?", "source_quote": 'quote: "Airport transfers available on request."'},
                         {"text": "Is parking free?", "source_quote": ""}]
    assert len(problems) == 2 and "FACT or QUESTION" in problems[0] and "too few columns" in problems[1]
    scope, cond = ob.parse_scope("site: Leeds or York; customers: students, plan: Pro; weekdays only")
    assert scope["sites"] == ["Leeds", "York"] and scope["segments"] == ["students"] and scope["plan_tiers"] == ["Pro"]
    assert cond == "weekdays only"
    assert ob.parse_scope("everywhere") == ({d: [] for d, _ in ob.DIMS}, "")


def test_scope_words_the_chatbot_cannot_place_stay_in_the_sentence():
    line = 'FACT | package | Midweek Escape | price | £180 per room per night | weekdays only | - | - | quote: "Midweek Escape: £180 per room per night"'
    facts, _, _ = ob.parse_answer(line)
    p, why = ob.check(facts[0], sources(), index(), [], "chatbot")
    assert why is None and "weekdays only" in p["text"]


# ------------------------------------------------------------------ the pages

@pytest.fixture
def setup(monkeypatch, mock):
    monkeypatch.setenv("EXTRACTOR_URL", EXT)
    from app.main import create_app
    mock.get(f"{BRAND}/facts/v2").respond(json={"facts": EXISTING})
    mock.get(f"{BRAND}/starter-kits").respond(json=KITS)
    mock.get(f"{BRAND}/questions").respond(json={"questions": []})
    mock.get(f"{GW}/health").respond(json={"status": "ok", "provider": "openai", "base_url": SECRET_BASE, "model": "m"})
    mock.get(f"{GW}/v1/prompts").respond(json=[{"name": "propose_facts"}, {"name": "voice_profile"}])
    app = create_app()
    with TestClient(app, follow_redirects=False) as c:
        assert login(c).status_code == 303
        yield c, csrf_of(c.get("/more").text), mock


def start_pdf(c, token, mock, answer=PDF_ANSWER):
    ext = mock.post(f"{EXT}/extract").respond(json=answer)
    r = c.post("/facts/setup", data={"csrf": token, "business_type": "hospitality", "urls": "", "text": ""},
               files=[("pdfs", ("rates.pdf", b"%PDF-1.4 fake brochure bytes", "application/pdf"))])
    assert r.status_code == 303, r.text
    return r.headers["location"], ext


def test_start_page_links_and_kits(setup):
    c, _, _ = setup
    h = c.get("/facts/setup").text
    assert 'enctype="multipart/form-data"' in h and 'name="pdfs"' in h and "Hospitality / accommodation" in h
    assert 'href="/facts/setup"' in c.get("/facts").text and 'href="/facts/setup"' in c.get("/more").text


def test_pdf_path_end_to_end_chatbot_way(setup):
    c, token, mock = setup
    work, ext = start_pdf(c, token, mock)
    body = json.loads(ext.calls[0].request.content)
    assert base64.b64decode(body["pdf_base64"]) == b"%PDF-1.4 fake brochure bytes" and body["filename"] == "rates.pdf"
    h = c.get(work).text
    assert "Way 2" in h and "[page 2]" in h and "Airport transfers available on request." in h
    assert "FACT | subject kind | subject | type | value as written | scope | valid from | valid to | quote: &#34;...&#34;" in h
    assert "Type of business: Hospitality / accommodation" in h
    assert "east shore" in h and "INTERNAL-SENTINEL" not in h        # only public facts leave in the pack
    assert "uses your hosted model: the source text goes to that provider" in h
    assert SECRET_BASE not in h
    sid = work.rsplit("/", 1)[-1]
    answer = "Here you go:\n" + "\n".join([GOOD, INVENTED, DUPLICATE, SCOPED, QUESTION]) + "\nHope this helps!"
    r = c.post(f"{work}/paste", data={"csrf": token, "chunk": "1", "answer": answer})
    assert r.status_code == 303 and r.headers["location"] == f"/facts/setup/{sid}/review"
    h = c.get(r.headers["location"]).text
    assert "<mark>Midweek Escape: £180 per room per night, 2 sharing, Sunday to Thursday</mark>" in h
    assert "rates.pdf p.2" in h and "only: Keswick annexe branch" in h
    assert "<b>Spa: £50 per person</b> · “Spa treatments £50 per person.”" in h          # invented: set aside, shown why
    assert "the quote is not in the source text" in h
    assert "already in your facts (weekend-rate)" in h
    assert "What do airport transfers cost?" in h
    assert h.count('name="keep_') == 2
    assert '<option value="internal" selected>' in h                                     # default until the owner says public

    created = mock.post(f"{BRAND}/facts/v2").respond(json={"key": "x", "status": "draft"})
    asked = mock.post(f"{BRAND}/questions").respond(json={"id": 9, "status": "open"})
    ids = re.findall(r'name="keep_(\d+)"', h)
    qid = re.search(r'name="ask_(\d+)"', h).group(1)
    form = {"csrf": token, f"keep_{ids[0]}": "yes", f"sens_{ids[0]}": "public", f"text_{ids[0]}": "Midweek Escape: £180 per room per night, 2 sharing, Sun-Thu.",
            f"from_{ids[0]}": "2026-10-01", f"to_{ids[0]}": "2027-03-31",
            f"sens_{ids[1]}": "restricted", f"ask_{qid}": "yes"}          # the second fact is NOT ticked
    r = c.post(f"/facts/setup/{sid}/save", data=form)
    assert r.status_code == 303 and r.headers["location"] == "/facts?show=drafts&done=onboarded:1-1"
    assert len(created.calls) == 1
    fact = json.loads(created.calls[0].request.content)
    assert fact["sensitivity"] == "public" and "status" not in fact
    assert fact["key"] == "midweek-escape-price" and fact["subject"] == {"kind": "package", "ref": "Midweek Escape"}
    assert fact["source"] == {"kind": "doc", "ref": 'rates.pdf p.1: "Midweek Escape: £180 per room per night, 2 sharing, Sunday to Thursday"'}
    assert (fact["valid_from"], fact["valid_to"]) == ("2026-10-01", "2027-03-31") and fact["basis"] == "per_room"
    assert fact["text"] == "Midweek Escape: £180 per room per night, 2 sharing, Sun-Thu."
    q = json.loads(asked.calls[0].request.content)
    assert q["kind"] == "missing_fact" and q["text"].startswith("What do airport transfers cost?")
    # nothing confirmed, no owner key anywhere
    for call in mock.calls:
        assert "x-owner-key" not in call.request.headers, call.request.url
        assert not call.request.url.path.endswith(("/confirm", "/import", "/apply")), call.request.url
    h = c.get(r.headers["location"]).text
    assert "1 draft added from your website or documents, and 1 open question" in h


def test_model_way_calls_propose_facts_per_chunk_and_verifies(setup):
    c, token, mock = setup
    work, _ = start_pdf(c, token, mock)
    out = {"facts": [raw("Weekend rate: £240 per room per night, room only. Breakfast £15 per person.",
                         text="Breakfast costs £15 per person at weekends.", subject={"kind": "package", "ref": "Weekend breakfast"},
                         fact_type="inclusion", value_text="£15 per person"),
                     raw("Breakfast is £12 per person.", value_text="£12 per person")],
           "questions": [{"text": "What does a transfer cost?", "source_quote": "Airport transfers available on request."},
                         {"text": "Is there a pool?", "source_quote": "Heated pool open all year."}]}
    run = mock.post(f"{GW}/v1/run").respond(json={"output": out})
    r = c.post(f"{work}/model", data={"csrf": token})
    assert r.status_code == 303
    req = run.calls[0].request
    assert req.headers["x-api-key"] == KEYS["INTERNAL_API_KEY"] and req.headers["x-caller"] == "72 onboarding"
    sent = json.loads(req.content)
    assert sent["prompt"] == "propose_facts" and "[page 1]" in sent["vars"]["source_text"]
    assert sent["vars"]["business_type"] == "Hospitality / accommodation" and "INTERNAL-SENTINEL" not in json.dumps(sent)
    h = c.get(r.headers["location"]).text
    assert "Breakfast costs £15 per person at weekends." in h and "(model, part 1)" in h
    assert "Breakfast is £12 per person." in h and "the quote is not in the source text" in h
    assert "Is there a pool?" in h and h.count('name="ask_') == 1
    created = mock.post(f"{BRAND}/facts/v2").respond(json={"key": "x"})
    pid = re.search(r'name="keep_(\d+)"', h).group(1)
    assert c.post(f"{r.headers['location'][:-7]}/save", data={"csrf": token, f"keep_{pid}": "yes"}).status_code == 303
    fact = json.loads(created.calls[0].request.content)
    assert fact["sensitivity"] == "internal" and fact["fact_type"] == "inclusion"   # default when not chosen


def test_model_way_does_three_parts_per_click(setup, monkeypatch):
    c, token, mock = setup
    monkeypatch.setattr(ob, "CHUNK_CHARS", 100)
    page = "\n".join(f"Room {i} costs £{100 + i} per night on weekdays only." for i in range(40))
    work, _ = start_pdf(c, token, mock, PDF_ANSWER | {"pages": [{"page": 1, "text": page}]})
    run = mock.post(f"{GW}/v1/run").respond(json={"output": {"facts": [], "questions": []}})
    h = c.get(work).text
    n = h.count('name="chunk"')
    assert n > 6 and f"Propose facts from the next 3 parts ({n} left)" in h
    c.post(f"{work}/model", data={"csrf": token})
    assert len(run.calls) == 3
    c.post(f"{work}/model", data={"csrf": token})
    assert len(run.calls) == 6 and f"({n - 6} left)" in c.get(work).text


def test_model_not_offered_without_the_prompt_or_gateway(setup):
    c, token, mock = setup
    mock.get(f"{GW}/v1/prompts").respond(json=[{"name": "voice_profile"}])
    c.app.state.backends._cache.clear()
    work, _ = start_pdf(c, token, mock)
    h = c.get(work).text
    assert "does not have the propose_facts prompt" in h and f'action="{work}/model"' not in h
    assert c.post(f"{work}/model", data={"csrf": token}).status_code == 503
    mock.get(f"{GW}/health").respond(500)
    c.app.state.backends._cache.clear()
    assert "does not answer right now" in c.get(work).text


def test_pasted_text_only_and_unreadable_answer(setup):
    c, token, mock = setup
    r = c.post("/facts/setup", data={"csrf": token, "text": "Open 9am to 5pm, Monday to Friday, at our Leeds branch."})
    work = r.headers["location"]
    r = c.post(f"{work}/paste", data={"csrf": token, "chunk": "1", "answer": "I could not find any facts, sorry."})
    assert r.status_code == 422 and "No FACT or QUESTION line" in r.text
    assert c.post(f"{work}/paste", data={"csrf": token, "chunk": "7", "answer": GOOD}).status_code == 404
    line = 'FACT | site | Leeds branch | hours | 9am to 5pm | site: Leeds | - | - | quote: "Open 9am to 5pm, Monday to Friday, at our Leeds branch."'
    r = c.post(f"{work}/paste", data={"csrf": token, "chunk": "1", "answer": line})
    h = c.get(r.headers["location"]).text
    assert "pasted text (" in h and "only: Leeds branch" in h


def test_extractor_errors_are_shown_with_advice(setup):
    c, token, mock = setup
    mock.post(f"{EXT}/extract").respond(422, json={"detail": "no text layer (a scanned or image-only PDF): paste the text instead"})
    r = c.post("/facts/setup", data={"csrf": token}, files=[("pdfs", ("scan.pdf", b"%PDF-1.4 x", "application/pdf"))])
    assert r.status_code == 422 and "scan.pdf: no text layer" in r.text and "paste the text instead" in r.text
    r = c.post("/facts/setup", data={"csrf": token}, files=[("pdfs", ("notes.pdf", b"hello", "application/pdf"))])
    assert r.status_code == 422 and "notes.pdf: not a PDF" in r.text
    assert c.post("/facts/setup", data={"csrf": token}).status_code == 422   # nothing given


def test_url_source_goes_through_07(setup):
    c, token, mock = setup
    ext = mock.post(f"{EXT}/extract").respond(json={"url": "https://lodge.example/prices", "title": "Prices",
                                                    "text": "Weekend rate: £240 per room per night, room only."})
    r = c.post("/facts/setup", data={"csrf": token, "urls": "https://lodge.example/prices"})
    assert r.status_code == 303 and json.loads(ext.calls[0].request.content) == {"url": "https://lodge.example/prices"}
    assert "https://lodge.example/prices" in c.get(r.headers["location"]).text
    r = c.post("/facts/setup", data={"csrf": token, "urls": "file:///etc/passwd"})
    assert r.status_code == 422 and "starts with https://" in r.text


def test_csrf_required_on_every_post(setup):
    c, token, mock = setup
    work, _ = start_pdf(c, token, mock)
    for path, data in (("/facts/setup", {"text": "x"}), (f"{work}/paste", {"chunk": "1", "answer": GOOD}),
                       (f"{work}/model", {}), (f"{work}/save", {})):
        assert c.post(path, data=data).status_code == 403, path
        assert c.post(path, data={**data, "csrf": "wrong"}).status_code == 403, path


def test_unknown_or_expired_run_is_404(setup):
    c, token, _ = setup
    assert c.get("/facts/setup/" + "A" * 24).status_code == 404
    assert c.post("/facts/setup/" + "A" * 24 + "/save", data={"csrf": token}).status_code == 404


def test_login_required(client, mock):
    assert client.get("/facts/setup").status_code == 303


def test_not_installed_states(monkeypatch, mock):
    monkeypatch.setenv("BRAND_URL", "")
    from app.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        login(c)
        assert "brand service (05) isn't installed" in c.get("/facts/setup").text
    monkeypatch.setenv("BRAND_URL", BRAND)
    monkeypatch.setenv("EXTRACTOR_URL", "")
    mock.get(f"{BRAND}/starter-kits").respond(json=KITS)
    mock.get(f"{BRAND}/facts/v2").respond(json={"facts": []})
    with TestClient(create_app(), follow_redirects=False) as c:
        login(c)
        token = csrf_of(c.get("/more").text)
        h = c.get("/facts/setup").text
        assert "page extractor (07), which isn't installed" in h and 'name="pdfs"' not in h
        r = c.post("/facts/setup", data={"csrf": token, "urls": "https://x.example/"})
        assert r.status_code == 422 and "paste the text instead" in r.text
        assert c.post("/facts/setup", data={"csrf": token, "text": "Open daily."}).status_code == 303


def test_no_secret_in_any_onboarding_response(setup):
    c, token, mock = setup
    work, _ = start_pdf(c, token, mock)
    mock.post(f"{GW}/v1/run").respond(json={"output": {"facts": [], "questions": []}})
    bodies = [c.get(p).text for p in ("/facts/setup", work, f"{work}/review")]   # (/facts itself shows internal facts to the owner)
    bodies.append(c.post(f"{work}/paste", data={"csrf": token, "chunk": "1", "answer": GOOD + "\n" + INVENTED}).text)
    bodies.append(c.get(f"{work}/review").text)
    bodies.append(c.post(f"{work}/model", data={"csrf": token}).text)
    everything = "\n".join(bodies)
    for secret in [*KEYS.values(), PASSWORD, EXT, "extractor.internal", "brand.internal", "gateway.internal",
                   SECRET_BASE, "X-API-Key", "X-Owner-Key", "INTERNAL-SENTINEL"]:
        assert secret not in everything, secret
    assert "Midweek Escape" in everything


def test_worst_case_pack_fits_a_free_chat():
    """A full-size chunk plus the most known facts still fits the 8,000 characters 88 uses for free chats."""
    from app import onboarding as ob
    src = {"kind": "doc", "label": "rates.pdf", "ref": "rates.pdf", "pages": [{"page": 3, "text": "Our lodge offers rooms with a view. " * 800}]}
    chunk = ob.make_chunks([src])[0]
    known = "\n".join(f"- Known public fact number {i} about the business." for i in range(400))[:ob.KNOWN_MAX_CHARS]
    pack = ob.build_pack(chunk, 9, src, "hospitality", known)
    assert len(pack) <= 8000, len(pack)
