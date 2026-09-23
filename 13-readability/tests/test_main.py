import pytest
from fastapi.testclient import TestClient

from app.main import app, count_syllables, split_sentences, verdict

client = TestClient(app)


def score(text):
    r = client.post("/score", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("word,expected", [
    ("cat", 1), ("coffee", 2), ("table", 2), ("roasted", 2), ("shipped", 1),
    ("beautiful", 3), ("subscription", 3), ("the", 1), ("readability", 5), ("2026", 1),
])
def test_syllable_heuristic(word, expected):
    assert count_syllables(word) == expected


def test_sentence_split_handles_punctuation_and_lines():
    text = 'We roast on Monday. It ships Tuesday! Why wait?\n- Free shipping\n\nDone "now." Yes.'
    assert split_sentences(text) == [
        "We roast on Monday.", "It ships Tuesday!", "Why wait?", "- Free shipping",
        'Done "now."', "Yes.",
    ]


def test_simple_text_is_easy():
    body = score("The cat sat on the mat. We like our coffee hot. It is good.")
    assert body["verdict"] == "easy"
    assert body["flesch_reading_ease"] >= 60
    assert body["words"] == 14
    assert body["sentences"] == 3
    assert body["avg_sentence_length"] == 4.7
    assert body["long_sentences"] == []


def test_dense_text_is_hard_and_long_sentence_is_returned():
    long = ("Organizational stakeholders increasingly prioritize comprehensive, "
            "institutionally standardized methodologies for evaluating operational "
            "sustainability initiatives across geographically distributed international "
            "subsidiaries, particularly regarding environmental, regulatory, and "
            "administrative considerations affecting quarterly performance evaluations "
            "and consolidated reporting.")
    body = score(long)
    assert body["verdict"] == "hard"
    assert body["fk_grade"] > 16
    assert body["long_sentences"] == [long]
    assert body["adverb_count"] == 4  # increasingly, institutionally, geographically, particularly


def test_passive_voice_and_adverbs():
    body = score("The beans were roasted yesterday. The box was quickly shipped. "
                 "Mistakes were made. We only reply early and daily. She is often happy.")
    assert body["passive_voice_count"] == 3
    # quickly counts; only, reply, early, daily are on the stoplist
    assert body["adverb_count"] == 1


@pytest.mark.parametrize("text", ["", "   \n ", "!!! ... ???"])
def test_empty_text_is_422(text):
    r = client.post("/score", json={"text": text})
    assert r.status_code == 422


def test_missing_text_field_is_422():
    assert client.post("/score", json={}).status_code == 422


@pytest.mark.parametrize("fre,expected", [(60, "easy"), (59.9, "ok"), (40, "ok"), (39.9, "hard")])
def test_verdict_thresholds(fre, expected):
    assert verdict(fre) == expected
