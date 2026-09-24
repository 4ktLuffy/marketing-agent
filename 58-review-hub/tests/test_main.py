import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app, first_name, normalize

KEY = "test-key"
AUTH = {"X-API-Key": KEY}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "reviews.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)


client = TestClient(app)

POSITIVE = ("Maya Chen", 5, "The Desk Blend is the best part of my workday.  Our whole team "
            "looks forward to the Tuesday box.")
NEGATIVE = ("Tom Reyes", 1, "My order arrived late and one bag was broken. I want a refund.")


def review(ext="g-1", source="google", author=POSITIVE[0], rating=POSITIVE[1], text=POSITIVE[2],
           created_at="2026-09-20T10:00:00+02:00"):
    return {"source": source, "external_id": ext, "author": author, "rating": rating,
            "text": text, "created_at": created_at}


def imp(*reviews):
    r = client.post("/reviews/import", json=list(reviews), headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def add_testimonial(**kw):
    body = {"quote": "The Desk Blend is the best part of my workday.",
            "author_display": "Maya, remote team lead", "consent": True,
            "consent_note": "email 2026-09-21"} | kw
    return client.post("/testimonials", json=body, headers=AUTH)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


# ---------- auth


@pytest.mark.parametrize("method,path,body", [
    ("post", "/reviews/import", []),
    ("post", "/reviews/1/status", {"status": "ignored"}),
    ("post", "/testimonials", {"quote": "x", "author_display": "y", "consent": True}),
])
def test_writes_need_the_key(method, path, body):
    assert getattr(client, method)(path, json=body).status_code == 401
    assert getattr(client, method)(path, json=body, headers={"X-API-Key": "wrong"}).status_code == 401


def test_writes_refused_when_service_has_no_key(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.post("/reviews/import", json=[review()], headers=AUTH).status_code == 503


# ---------- import


def test_import_stores_reviews_as_new_with_utc_time():
    out = imp(review(), review("t-9", source="trustpilot", author=NEGATIVE[0], rating=1, text=NEGATIVE[2]))
    assert out["imported"] == 2 and out["duplicates"] == 0 and len(out["ids"]) == 2
    items = client.get("/reviews").json()
    assert {r["status"] for r in items} == {"new"}
    got = client.get(f"/reviews/{out['ids'][0]}").json()
    assert got["created_at"] == "2026-09-20T08:00:00Z"
    assert got["source"] == "google" and got["rating"] == 5


def test_import_is_idempotent_on_source_and_external_id():
    first = imp(review())
    client.post(f"/reviews/{first['ids'][0]}/status", json={"status": "replied"}, headers=AUTH)
    again = imp(review(text="edited text"), review("g-2"))
    assert again["imported"] == 1 and again["duplicates"] == 1
    assert again["skipped"] == [{"source": "google", "external_id": "g-1"}]
    kept = client.get(f"/reviews/{first['ids'][0]}").json()
    assert kept["text"] == POSITIVE[2] and kept["status"] == "replied"  # untouched
    # the same external_id on another platform is a different review
    assert imp(review(source="trustpilot"))["imported"] == 1
    assert len(client.get("/reviews").json()) == 3


def test_import_duplicate_within_one_batch_counts_once():
    out = imp(review(), review())
    assert out["imported"] == 1 and out["duplicates"] == 1


@pytest.mark.parametrize("bad", [
    {"rating": 0}, {"rating": 6}, {"rating": "5"}, {"source": "yelp"}, {"text": "  "},
    {"external_id": ""}, {"created_at": "yesterday"},
])
def test_import_rejects_bad_reviews_and_stores_nothing(bad):
    r = client.post("/reviews/import", json=[review("ok-1"), review() | bad], headers=AUTH)
    assert r.status_code == 422
    assert client.get("/reviews").json() == []


# ---------- list / status


def test_list_filters_by_status_and_rating():
    ids = imp(review("a", rating=5), review("b", rating=3), review("c", rating=1, text=NEGATIVE[2]))["ids"]
    client.post(f"/reviews/{ids[1]}/status", json={"status": "drafted"}, headers=AUTH)
    assert [r["id"] for r in client.get("/reviews?status=new").json()] == [ids[2], ids[0]]
    assert [r["id"] for r in client.get("/reviews?status=drafted").json()] == [ids[1]]
    assert [r["rating"] for r in client.get("/reviews?max_rating=2").json()] == [1]
    assert sorted(r["rating"] for r in client.get("/reviews?min_rating=3").json()) == [3, 5]
    assert [r["rating"] for r in client.get("/reviews?min_rating=2&max_rating=4").json()] == [3]


@pytest.mark.parametrize("query", ["status=done", "min_rating=0", "max_rating=6", "min_rating=4&max_rating=2"])
def test_list_rejects_bad_filters(query):
    assert client.get(f"/reviews?{query}").status_code == 422


def test_status_change_and_failures():
    rid = imp(review())["ids"][0]
    r = client.post(f"/reviews/{rid}/status", json={"status": "ignored"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert client.post(f"/reviews/{rid}/status", json={"status": "posted"}, headers=AUTH).status_code == 422
    assert client.post("/reviews/999/status", json={"status": "replied"}, headers=AUTH).status_code == 404
    assert client.get("/reviews/99999999999999999999").status_code in (404, 422)


# ---------- reply context


def test_reply_context_for_negative_review_with_problems():
    rid = imp(review("n", author=NEGATIVE[0], rating=NEGATIVE[1], text=NEGATIVE[2]))["ids"][0]
    ctx = client.get(f"/reviews/{rid}/reply-context").json()
    assert ctx["author_first_name"] == "Tom"
    assert ctx["negative"] is True and ctx["mentions_problem"] is True
    assert ctx["problem_words"] == ["broken", "late", "refund"]
    assert ctx["vars"] == {"review": NEGATIVE[2], "rating": 1, "author_first_name": "Tom", "negative": True}


def test_reply_context_for_positive_review():
    rid = imp(review(text="Latest roast is lovely, never late."))["ids"][0]
    ctx = client.get(f"/reviews/{rid}/reply-context").json()
    assert ctx["negative"] is False and ctx["rating"] == 5
    # "Latest" is not "late"; "late" itself is found (a flag, not a verdict)
    assert ctx["problem_words"] == ["late"]
    rid2 = imp(review("p2", rating=2, text="Tastes fine, just not for me."))["ids"][0]
    ctx2 = client.get(f"/reviews/{rid2}/reply-context").json()
    assert ctx2["negative"] is True and ctx2["mentions_problem"] is False


def test_reply_context_404():
    assert client.get("/reviews/5/reply-context").status_code == 404


@pytest.mark.parametrize("author,want", [
    ("Maya Chen", "Maya"), ("maya", "maya"), ("A Google user", None), ("J.", None),
    ("Dr. Ann Lee", "Ann"), ("", None),
])
def test_first_name(author, want):
    assert first_name(author) == want


def test_anonymous_author_gets_a_neutral_greeting_var():
    rid = imp(review(author="A Google user"))["ids"][0]
    ctx = client.get(f"/reviews/{rid}/reply-context").json()
    assert ctx["author_first_name"] is None and ctx["vars"]["author_first_name"] == "there"


# ---------- testimonials


def test_testimonial_from_review_must_be_verbatim():
    rid = imp(review())["ids"][0]
    ok = add_testimonial(source_review_id=rid)
    assert ok.status_code == 201, ok.text
    assert ok.json()["source_review_id"] == rid and ok.json()["consent"] is True
    # whitespace differences are fine: the review has two spaces after "workday."
    spaced = add_testimonial(source_review_id=rid, quote="workday. Our whole\nteam looks forward")
    assert spaced.status_code == 201 and spaced.json()["quote"] == "workday. Our whole team looks forward"


@pytest.mark.parametrize("quote", [
    "The Desk Blend is the best coffee of my workday.",          # paraphrased
    "the desk blend is the best part of my workday.",            # case changed
    "The Desk Blend is the best part... the Tuesday box.",       # stitched together
    "Best coffee ever!",                                         # invented
])
def test_non_verbatim_quote_is_422(quote):
    rid = imp(review())["ids"][0]
    r = add_testimonial(source_review_id=rid, quote=quote)
    assert r.status_code == 422
    assert "verbatim" in r.json()["detail"]["message"]
    assert client.get("/testimonials").json() == []


def test_testimonial_from_missing_review_is_404():
    assert add_testimonial(source_review_id=42).status_code == 404


def test_manual_testimonial_and_validation():
    r = add_testimonial(quote="Our office finally has coffee people talk about.", consent=False)
    assert r.status_code == 201 and r.json()["source_review_id"] is None
    assert add_testimonial(consent="yes").status_code == 422
    assert add_testimonial(quote="   ").status_code == 422
    assert add_testimonial(author_display="").status_code == 422


def test_usable_lists_only_consented():
    rid = imp(review())["ids"][0]
    yes = add_testimonial(source_review_id=rid).json()
    no = add_testimonial(quote="Our whole team looks forward to the Tuesday box.",
                     source_review_id=rid, consent=False).json()
    assert [t["id"] for t in client.get("/testimonials?usable=true").json()] == [yes["id"]]
    assert [t["id"] for t in client.get("/testimonials?usable=false").json()] == [no["id"]]
    assert len(client.get("/testimonials").json()) == 2


# ---------- check


@pytest.fixture
def bank():
    rid = imp(review())["ids"][0]
    add_testimonial(source_review_id=rid)
    add_testimonial(quote="Our whole team looks forward to the Tuesday box.", source_review_id=rid,
                consent=False, author_display="Maya")
    return rid


def check(text):
    r = client.post("/testimonials/check", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_check_passes_exact_quote_in_straight_and_curly_quotes(bank):
    for text in ('As Maya says: "The Desk Blend is the best part of my workday."',
                 'As Maya says: “The Desk Blend is the best part of my workday.”',
                 'Maya: „the best part of my workday“ and more.'):
        out = check(text)
        assert out["ok"] is True, (text, out)
        assert len(out["quotes"]) == 1 and out["quotes"][0]["testimonial_id"] == 1


def test_check_flags_paraphrased_quote_in_curly_quotes(bank):
    out = check("Customers love it: “The Desk Blend is the best coffee of my day.”")
    assert out["ok"] is False
    assert out["violations"][0]["text"] == "The Desk Blend is the best coffee of my day."
    assert "invented or paraphrased" in out["violations"][0]["reason"]


def test_check_flags_quote_without_consent(bank):
    out = check('"Our whole team looks forward to the Tuesday box."')
    assert out["ok"] is False
    assert out["violations"][0]["reason"] == "testimonial has no consent to quote"
    assert out["violations"][0]["testimonial_id"] == 2


def test_check_reports_every_quote_with_positions(bank):
    text = '“The Desk Blend is the best part of my workday.” Also "Life-changing coffee!" says Sam.'
    out = check(text)
    assert [q["ok"] for q in out["quotes"]] == [True, False]
    bad = out["violations"][0]
    assert text[bad["start"]:bad["end"]] == '"Life-changing coffee!"'


def test_check_without_quotes_is_ok_and_handles_apostrophes(bank):
    assert check("No quotations here, it’s just copy.") == {"ok": True, "quotes": [], "violations": []}
    assert check("It’s a “fresh” take.")["ok"] is False  # scare quotes are flagged too


def test_normalize_keeps_words_and_case():
    assert normalize("  It’s\n\tgreat ") == "It's great"
    assert normalize("Great") != normalize("great")


# ---------- storage


def test_database_uses_wal(tmp_path):
    client.get("/reviews")
    conn = sqlite3.connect(tmp_path / "reviews.sqlite")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_parallel_first_imports_do_not_duplicate():
    errors, results = [], []

    def go(i):
        try:
            results.append(client.post("/reviews/import", json=[review("same"), review(f"x{i}")],
                                       headers=AUTH).json())
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sum(r["imported"] for r in results) == 9
    assert len(client.get("/reviews").json()) == 9


def test_health_and_legal_reviews_need_a_human_decided_in_code():
    imp(review("h-1", rating=2, text="I felt sick and nauseous after the decaf."),
        review("h-2", rating=1, text="My lawyer will hear about this double charge."),
        review("h-3", rating=2, text="Delivery was late, the latest box too."))
    ids = {r["external_id"]: r["id"] for r in client.get("/reviews").json()}
    ctx = {e: client.get(f"/reviews/{i}/reply-context").json() for e, i in ids.items()}
    assert ctx["h-1"]["needs_human"] and ctx["h-1"]["escalate_words"] == ["nauseous", "sick"]
    assert ctx["h-2"]["needs_human"] and ctx["h-2"]["escalate_words"] == ["lawyer"]
    assert ctx["h-3"]["needs_human"] is False              # a late delivery is not escalated
    assert ctx["g-1"]["needs_human"] is False if "g-1" in ctx else True


def test_withdrawn_consent_makes_a_testimonial_unusable_at_once():
    t = add_testimonial().json()
    assert [x["id"] for x in client.get("/testimonials?usable=true").json()] == [t["id"]]
    assert client.post(f"/testimonials/{t['id']}/consent", json={"consent": False}).status_code == 401
    r = client.post(f"/testimonials/{t['id']}/consent", json={"consent": False, "note": "asked by email"}, headers=AUTH)
    assert r.status_code == 200 and "consent withdrawn: asked by email" in r.json()["consent_note"]
    assert client.get("/testimonials?usable=true").json() == []
    body = {"text": 'As a customer said, "The Desk Blend is the best part of my workday."'}
    assert client.post("/testimonials/check", json=body).json()["ok"] is False   # no longer usable
    assert client.post("/testimonials/999/consent", json={"consent": True}, headers=AUTH).status_code == 404
