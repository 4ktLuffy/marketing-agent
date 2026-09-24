# brand-service

Deploy **05 of 60** of the local-LLM marketing agent. It holds one brand profile (voice,
products, key messages, banned phrases, disclaimers) and serves it two ways: as JSON, and
as a compact plain-text summary the LLM gateway (03) embeds in every prompt. `POST /check`
lints any draft against the brand rules before it goes near the calendar.
It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n and the gateway call it at `http://brand-service:8000` (env `BRAND_URL`).
It is stateless (the profile is a mounted file), so it also runs on any container host.

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
| `all_caps` | warn | more than 3 all-caps words of 2+ letters, not counting `allowed_acronyms` |
| `exclamation_marks` | warn | more than 2 `!` |

Channel names are free-form and lowercased; the example defines `paid_social`,
`influencer`, `giveaway` and `sms`. A channel with no entry has no disclaimer requirement.
The summary lists hard rules (banned phrases, emoji limit) first, so if a large profile is
cut at 1600 characters only the descriptive parts are lost.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `BRAND_FILE` | `/config/brand.yaml` | brand profile yaml; missing file gives 503 on every endpoint except `/health` |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
