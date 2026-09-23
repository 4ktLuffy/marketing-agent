"""Readability: Flesch scores and plain-language checks. Pure Python, no LLM."""
import re

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="readability")

LONG_SENTENCE_WORDS = 25
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['\u2019-][A-Za-z0-9]+)*")
# Sentence ends at . ! ? (optionally one closing quote/bracket) + whitespace, or at a line break.
SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?])\s+|(?<=[.!?][\"')\]\u201d\u2019])\s+|\s*\n+\s*"
)

BE_FORMS = {"am", "is", "are", "was", "were", "be", "been", "being", "isn't", "aren't",
            "wasn't", "weren't"}
# Common words ending -ed/-en that are not past participles.
NOT_PARTICIPLES = {"been", "often", "even", "open", "seven", "eleven", "ten", "then", "when",
                   "garden", "children", "women", "men", "token", "kitchen", "chicken",
                   "listen", "happen", "heaven", "linen", "oven", "dozen", "citizen", "screen",
                   "green", "seen", "keen", "between", "red", "bed", "need", "speed", "seed",
                   "feed", "shed", "hundred", "wed", "sacred", "naked"}
IRREGULAR_PARTICIPLES = {"made", "done", "built", "sent", "paid", "sold", "told", "held",
                         "kept", "left", "bought", "brought", "caught", "taught", "found",
                         "known", "shown", "grown", "thrown", "drawn", "put", "set", "led",
                         "run", "cut", "hit", "shut", "spent", "lost", "won", "met", "felt"}
# Words ending -ly that are not adverbs (or not worth flagging).
NOT_ADVERBS = {"only", "family", "reply", "early", "daily", "weekly", "monthly", "quarterly", "yearly",
               "hourly", "likely", "unlikely", "apply", "supply", "fly", "july", "italy",
               "ally", "rally", "belly", "jelly", "bully", "holy", "ugly", "silly", "lovely",
               "friendly", "lonely", "costly", "elderly", "orderly", "assembly", "anomaly",
               "butterfly", "comply", "rely", "imply", "multiply", "emily", "lily", "sly",
               "wholly", "curly", "chilly", "hilly", "jolly", "oily", "bubbly", "courtly"}


def count_syllables(word: str) -> int:
    """Vowel-group heuristic with the usual English corrections. Min 1 per word."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 1  # a number like 2026 still reads as a unit
    if len(w) <= 3:
        return 1
    # Silent endings: -es / -ed (unless -ted/-ded), and a final -e (but keep consonant+le).
    w = re.sub(r"(?:[^laeiouy]es|[^tdaeiouy]ed)$", lambda m: m.group(0)[0], w)
    if w.endswith("e") and not re.search(r"[^aeiouy]le$", w):
        w = w[:-1]
    groups = re.findall(r"[aeiouy]+", w)
    return max(1, len(groups))


def split_sentences(text: str) -> list[str]:
    parts = SENTENCE_SPLIT_RE.split(text.strip())
    return [p.strip() for p in parts if p and WORD_RE.search(p)]


def is_participle(word: str) -> bool:
    w = word.lower()
    if w in NOT_PARTICIPLES:
        return False
    return w in IRREGULAR_PARTICIPLES or (len(w) > 3 and w.endswith(("ed", "en")))


def count_passive(sentence_words: list[str]) -> int:
    """'be' form, optionally one -ly adverb, then a past participle ('was quickly shipped')."""
    n = 0
    words = [w.lower() for w in sentence_words]
    for i, w in enumerate(words):
        if w not in BE_FORMS:
            continue
        j = i + 1
        if j < len(words) and words[j].endswith("ly"):
            j += 1
        if j < len(words) and is_participle(words[j]):
            n += 1
    return n


def verdict(fre: float) -> str:
    if fre >= 60:
        return "easy"
    if fre >= 40:
        return "ok"
    return "hard"


class ScoreRequest(BaseModel):
    text: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/score")
def score(req: ScoreRequest):
    sentences = split_sentences(req.text)
    per_sentence = [WORD_RE.findall(s) for s in sentences]
    words = [w for ws in per_sentence for w in ws]
    if not words:
        raise HTTPException(422, "text has no words")

    n_words, n_sent = len(words), len(sentences)
    syllables = sum(count_syllables(w) for w in words)
    wps = n_words / n_sent
    spw = syllables / n_words
    fre = 206.835 - 1.015 * wps - 84.6 * spw
    grade = 0.39 * wps + 11.8 * spw - 15.59

    return {
        "flesch_reading_ease": round(fre, 1),
        "fk_grade": round(grade, 1),
        "words": n_words,
        "sentences": n_sent,
        "avg_sentence_length": round(wps, 1),
        "long_sentences": [s for s, ws in zip(sentences, per_sentence)
                           if len(ws) > LONG_SENTENCE_WORDS],
        "passive_voice_count": sum(count_passive(ws) for ws in per_sentence),
        "adverb_count": sum(1 for w in words
                            if w.lower().endswith("ly") and len(w) > 3
                            and w.lower() not in NOT_ADVERBS),
        "verdict": verdict(fre),
    }
