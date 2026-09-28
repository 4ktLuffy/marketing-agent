# brand-service

Deploy **05 of 87** of the local-LLM marketing agent. It holds one brand profile (voice,
products, key messages, banned phrases, disclaimers) and serves it two ways: as JSON, and
as a compact plain-text summary the LLM gateway (03) embeds in every prompt. `POST /check`
lints any draft against the brand rules before it goes near the calendar.
It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n and the gateway call it at `http://brand-service:8000` (env `BRAND_URL`).
Its only state is the mounted brand file, the optional edits saved over it (`BRAND_OVERRIDES_FILE`) and the optional voice profile (`VOICE_FILE`), so it also runs on any container host.

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
restart needed. People who don't edit YAML use the control room's **Brand setup** pages (72),
which save through `PUT /brand/editable` (see *Editing the brand without YAML* below). `config/brand.yaml` is a fictional example ("Northwind Roasters") that
shows every supported key.

## Endpoints

With `INTERNAL_API_KEY` set (the stack sets it), every endpoint but `/health` needs it in
`X-API-Key`, reads included.

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
| GET | `/brand/editable` | — | `{"brand": {every editable field, merged}, "overridden": [field names], "products": {"changed","added","removed"}}`; needs `X-API-Key` |
| PUT | `/brand/editable` | any subset of the editable fields | the same as GET; fields left out keep their value; 422 names the field (`loc`, e.g. `["body","facts",3]`); needs `X-API-Key` |
| DELETE | `/brand/editable` | — | `{"deleted": bool}`: back to `brand.yaml` as shipped; needs `X-API-Key` |
| GET | `/brand/completeness` | — | `{"score": 0-100, "done", "total", "checks": [{"id","label","ok","missing"}], "missing": [str]}` |
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

## Editing the brand without YAML

`brand.yaml` stays the read-only base (the shipped example, or your own mounted file). Edits
are saved in a separate JSON file, `BRAND_OVERRIDES_FILE`, written atomically, and merged on
top on every read (both files are re-read when they change), so `/profile`, `/profile/summary`,
`/facts` and `/check` all see the edited brand. The gateway (03) caches the summary and facts for
60 s, so the agent uses an edit within a minute.

Merge rules:

- Top-level scalars (`name`, `website`) and lists (`facts`, `banned_phrases`, `allowed_domains`,
  `key_messages`) **replace** the base value.
- Mappings (`audience`, `voice`, `emoji_policy`, `required_disclaimers`) merge **key by key**, so
  base keys the editor doesn't show (`audience.pains`, `emoji_policy.note`) survive.
- `products` merge **by name** (case-insensitive): an edited product replaces the base product
  with that name in place, new names are appended, and base products left out of a saved list are
  recorded in `_removed_products` and dropped.
- Only what differs from the base is stored, so `overridden` in `GET /brand/editable` is exactly
  what an edit changed. `DELETE /brand/editable` removes the file: back to the base.

Validation (`PUT`): name ≤ 80 characters; one-liner ≤ 200; website a full `http(s)://` address;
products need `name` and `one_line` (price optional, `aliases` a list; names unique); at most 100
facts of at most 200 characters each; `emoji_policy.allowed` only single emoji,
`max_per_post` 0–10; `allowed_domains` are domains (a URL is reduced to its host); unknown fields
and `null` are refused. A broken overrides file is logged and ignored (the base still works).

`GET /brand/completeness` is a checklist: name and one-liner, website, audience, products with a
one-line description, at least 5 facts, banned phrases, an emoji rule, and the voice interview.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `BRAND_FILE` | `/config/brand.yaml` | brand profile yaml; missing file gives 503 on every endpoint except `/health` |
| `VOICE_FILE` | `/config/voice.json` | stored voice profile; its folder must be writable (mount `/config` read-write, or point this at a volume) |
| `BRAND_OVERRIDES_FILE` | `/config/brand.overrides.json` | edits saved over `brand.yaml` (JSON, written atomically); its folder must be writable: in the stack point it at a volume, e.g. `/data/brand.overrides.json` |
| `INTERNAL_API_KEY` | unset | when set, every endpoint except `/health` needs it in `X-API-Key`, reads included: with several clients on one machine each has its own key, so a URL pointing at the wrong client's brand fails instead of answering with that brand's facts |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
