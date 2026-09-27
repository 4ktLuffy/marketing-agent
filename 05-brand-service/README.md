# brand-service

Deploy **05 of 83** of the local-LLM marketing agent. It holds one brand profile (voice,
products, key messages, banned phrases, disclaimers) and serves it two ways: as JSON, and
as a compact plain-text summary the LLM gateway (03) embeds in every prompt. `POST /check`
lints any draft against the brand rules before it goes near the calendar.
It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n and the gateway call it at `http://brand-service:8000` (env `BRAND_URL`).
Its only state is the mounted brand file and the optional voice profile (`VOICE_FILE`), so it also runs on any container host.

## Run

```bash
docker build -t brand-service .
docker run --rm -p 8105:8000 brand-service                      # built-in example brand
docker run --rm -p 8105:8000 -v "$PWD/my-brand:/config:ro" brand-service   # your brand
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
BRAND_FILE=config/brand.yaml uvicorn app.main:app --port 8105
```

The yaml is re-read whenever its modification time changes, so edit it in place; no
restart needed. `config/brand.yaml` is a fictional example ("Northwind Roasters") that
shows every supported key.

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| GET | `/profile` | — | the brand yaml as JSON |
| GET | `/profile/summary` | — | `{"summary": str}` (plain text, at most 1600 chars) |
| GET | `/facts` | — | `{"facts":[{"id","text"}]}`: the approved facts (explicit `facts:` list + products + key messages); the claim checker (44) only accepts claims these support |
| GET | `/voice/questions` | — | `{"questions":[10 str]}`: the brand-voice interview |
| GET | `/voice` | — | the stored voice profile, or 404 |
| PUT | `/voice` | voice profile JSON (output of the 04 `voice_profile` prompt) | the stored profile; 422 on a bad body; needs `X-API-Key` |
| DELETE | `/voice` | — | `{"deleted": bool}`; needs `X-API-Key` |
| POST | `/check` | `{"text","channel"?}` | `{"ok","violations":[{"rule","detail","severity":"error\|warn","match"?}]}`; `match` is the exact offending text (banned phrases) |

```bash
curl -s localhost:8105/check -H 'content-type: application/json' \
  -d '{"text":"The best in the world coffee!!!","channel":"paid_social"}'
# {"ok":false,"violations":[{"rule":"banned_phrase",...,"severity":"error"},
#   {"rule":"missing_disclaimer",...,"severity":"error"},{"rule":"exclamation_marks",...,"severity":"warn"}]}
```

Checks run by `/check`. `ok` is `false` only when there is at least one `error`.

| Rule | Severity | Fires when |
|---|---|---|
| `banned_phrase` | error | a `banned_phrases` entry appears (case-insensitive, whole words: `cure` does not match `secure`; `*` = up to three words, so `best * in the world` catches `best coffee in the world`) |
| `missing_disclaimer` | error | `channel` is a key of `required_disclaimers` and none of its strings appear |
| `too_many_emojis` | warn | more emojis than `emoji_policy.max_per_post` (flags and ZWJ sequences count once) |
| `emoji_not_allowed` | error (configurable: `emoji_policy.not_allowed_severity`) | an emoji outside `emoji_policy.allowed` (only when that list is set); `match` holds the emoji |
| `avoid_word` | warn | a `words_we_avoid` entry of the stored voice profile appears (whole words); `match` holds it |
| `all_caps` | warn | more than 3 all-caps words of 2+ letters, not counting `allowed_acronyms` |
| `exclamation_marks` | warn | more than 2 `!` |

Channel names are free-form and lowercased; the example defines `paid_social`,
`influencer`, `giveaway` and `sms`. A channel with no entry has no disclaimer requirement.
The summary lists hard rules (banned phrases, emoji limit) first, so if a large profile is
cut at 1600 characters only the descriptive parts are lost.

## Brand voice from an interview

Voice training that doesn't stick is the most common complaint about AI writing tools.
Here the voice comes from ten questions a person answers in plain words:

1. `GET /voice/questions`, answer them (who you talk to, words you'd never use, a post you
   loved, humour, formality, emoji habits, how competitors sound, what you never promise...).
2. Run the 04 prompt `voice_profile` through the gateway with the answers as `answers`
   (and approved posts from 46 `/examples` as `examples`, if you have them).
3. Review the JSON, then `PUT /voice` with it.

From then on the summary carries a compact block right after the hard rules:
`Voice:` (the profile summary), `Voice do:`, `Voice don't:` and `Avoid words:`. It sits
before the descriptive lines, so the 1600-character cut never reaches it; the sentence
style and words-we-use are appended at the very end only while they fit. Sample lines are stored but never sent to writers (in the eval they carried invented details that writers copied).
With a long profile the cut can shorten the last line (products); writing prompts still
get every product from `/facts`. `/check` warns on the avoid words.

The profile is stored in `VOICE_FILE` (JSON, written atomically), separate from
`brand.yaml`, which the service never writes. Whether it helps is measured, not assumed:
`23-eval-suite` `python -m evalsuite.voice_ab`.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `BRAND_FILE` | `/config/brand.yaml` | brand profile yaml; missing file gives 503 on every endpoint except `/health` |
| `VOICE_FILE` | `/config/voice.json` | stored voice profile; its folder must be writable (mount `/config` read-write, or point this at a volume) |
| `INTERNAL_API_KEY` | unset | when set, `PUT`/`DELETE /voice` need it in `X-API-Key` |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
