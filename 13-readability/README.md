# readability

Deploy **13 of 53** of the local-LLM marketing agent. It scores how easy a draft is to
read (Flesch reading ease, Flesch-Kincaid grade) and flags long sentences, passive voice
and adverbs, so the blog writer and quality gate can ask the LLM to simplify with
specific feedback. It uses no LLM and no network.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n calls it at `http://readability:8000` (env `READABILITY_URL`).
It is stateless, so it also runs fine on any container host (Render, Fly.io, Railway).

## Run

```bash
docker build -t readability .
docker run --rm -p 8113:8000 readability
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --port 8113
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/score` | `{"text"}` | `{"flesch_reading_ease","fk_grade","words","sentences","avg_sentence_length","long_sentences":[],"passive_voice_count","adverb_count","verdict":"easy\|ok\|hard"}` |

```bash
curl -s localhost:8113/score -H 'content-type: application/json' \
  -d '{"text":"Our Desk Blend is roasted every Monday and shipped within 48 hours. You pick how often it arrives."}'
# {"flesch_reading_ease":80.2,"fk_grade":4.3,"words":18,"sentences":2,...,"verdict":"easy"}
```

Empty text, or text with no words, returns 422.

How it is computed (own heuristics, no `textstat`):

| Field | Rule |
|---|---|
| sentences | split after `.` `!` `?` (and an optional closing quote/bracket) followed by whitespace, and at every line break, so bullets and headings count as sentences |
| syllables | vowel groups per word, dropping silent `-e`, `-es`, `-ed` (but not `-le`, `-ted`, `-ded`); minimum 1 |
| `flesch_reading_ease` | `206.835 - 1.015 * words/sentences - 84.6 * syllables/words` |
| `fk_grade` | `0.39 * words/sentences + 11.8 * syllables/words - 15.59` |
| `long_sentences` | sentences with more than 25 words, returned verbatim |
| `passive_voice_count` | a form of "be", optionally one `-ly` word, then a word ending `-ed`/`-en` or a common irregular participle (`made`, `sent`, `built` ...) |
| `adverb_count` | words ending `-ly`, minus a stoplist (`only`, `family`, `reply`, `early`, `daily`, `likely` ...) |
| `verdict` | `easy` if reading ease >= 60, `ok` if 40 to 60, `hard` below 40 |

These are heuristics: expect scores within a few points of other tools, and some false
positives on passive voice (for example "is tired").

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
