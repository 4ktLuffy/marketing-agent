# brand-service

Deploy **05 of 91** of the marketing agent. It holds one brand profile (voice,
products, key messages, banned phrases, disclaimers) and serves it two ways: as JSON, and
as a compact plain-text summary the LLM gateway (03) embeds in every prompt. `POST /check`
lints any draft against the brand rules before it goes near the calendar.
It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n and the gateway call it at `http://brand-service:8000` (env `BRAND_URL`).
Its state is the mounted brand file, the fact store (`FACTS_DB`, SQLite), the optional edits saved over it (`BRAND_OVERRIDES_FILE`) and the optional voice profile (`VOICE_FILE`), so it also runs on any container host.

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
| GET | `/facts?max_sensitivity=` | — | `{"facts":[{"id","text"}]}`: the approved facts (explicit `facts:` list + products + key messages, then active v2 facts; see *Scoped facts (v2)*); the claim checker (44) only accepts claims these support |
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

## Scoped facts (v2)

The flat `facts:` list can't say *where* or *until when* a fact is true, and that is the most common
way true facts end up in wrong copy: another branch's price, a plan-only feature, a certification
that covers one variant, last season's offer. The fact store keeps each fact with a stable key and
the context it holds in (contract: `_dev/phase1-contracts.md` §1-§2):

- `key` (`[a-z0-9-]{2,60}`), `subject` `{kind, ref}` (kind: business, site, product, variant, plan,
  service, package, menu_item, person, policy, offer), `fact_type` (price, spec, availability, hours,
  inclusion, policy, certification, credential, claim, testimonial, result, event, contact),
  `attribute`, `value`/`unit`/`currency`/`value_text`/`basis`, `conditions`.
- `scope` `{sites, regions, channels, segments, plan_tiers, variants}`: an empty list means all.
- `valid_from`/`valid_to`/`review_by` (`YYYY-MM-DD`, empty = open), `source`, `claim_class`,
  `required_disclosures`, `allowed_phrasing`/`forbidden_phrasing`, `owner`, `risk`, `text`
  (≤ 500 chars, the sentence copy may use).
- `sensitivity`: `public` (may be sent to any model), `internal` (the default; tools may use it,
  88 sends it to outside chatbots only as a slot), `restricted` (never leaves the store's API).
- `status`: `draft` → `active` (confirmed by the owner) → `superseded`/`retired`; `expired` is
  reported for an active fact past its `valid_to` (stored history is not changed; the first time
  it is seen a `changes` row of kind `expired` is written). An import may also set `expired`
  explicitly.

**Versions are append-only.** Every write adds a row to `fact_versions`; SQLite triggers refuse
UPDATE and DELETE on it (and on the `changes` log), including by hand. `PUT` adds a draft version
while the confirmed one keeps being served; `confirm` switches to the latest version.

**A human confirms.** `confirm`, `retire`, `import` and applying a starter kit need
`X-Owner-Key: $FACT_OWNER_KEY` (compared in constant time) on top of `X-API-Key`. With
`FACT_OWNER_KEY` unset they answer 503, so a stack without the key can draft facts but nothing (and
no model output) becomes a fact on its own. Only 05 and the control room (72) hold the key.
Optional `X-Actor` names who made a change in the version history.

**Valid on day D**: status active and `valid_from ≤ D ≤ valid_to`. **Scope match** for a task:
for each dimension where the fact lists values, the task must name at least one value and all of
its values must be in the fact's list; naming another value is `out_of_scope`, leaving the
dimension empty is `scope_unspecified` (case-insensitive).

**Derived facts.** The brand profile's facts (brand.yaml + saved edits) appear in `/facts/v2` and
`/facts/query` read-only with `derived: true`, scope all, sensitivity public, key
`price-<product-slug>` for a product's price line and `legacy-<sha8(text)>` for the rest. To turn
one into a scoped fact, `POST /facts/v2` a new key with `supersedes_key` set to the derived key and
confirm it: from then on the derived line is left out of `/facts` and reported `superseded`.

**fact_set_version** `fs-<seq>-<sha8>`: `seq` is the last `changes` sequence number; the hash covers
the sorted active `key:version` pairs and the derived facts' texts, so it changes on every confirm,
retire or brand edit. 88 stamps packs with it.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/facts/v2?status=&include_derived=` | — | `{"fact_set_version","facts":[Fact + version, latest_version, derived]}` |
| GET | `/facts/v2/{key}` | — | Fact + `versions` `[{version, status, data, created_by, created_at, confirmed_by}]` |
| POST | `/facts/v2` | Fact (status ignored) | new key → draft v1; a key still in draft → new draft version; an active key → 409 (use PUT) |
| PUT | `/facts/v2/{key}` | Fact (key optional) | new draft version; the confirmed version stays served until confirm |
| POST | `/facts/v2/{key}/confirm` · `/retire` | — | owner key; the latest version becomes active (superseding the previous one, and `supersedes_key`'s fact) / retired |
| POST | `/facts/v2/import` | `{"facts":[Fact], "confirm": bool}` | owner key; all or nothing (≤ 500); `{"created","updated","unchanged","confirmed","keys"}`; re-importing the same data is a no-op |
| GET | `/facts/query?site=&region=&channel=&segment=&plan_tier=&variant=&at=&max_sensitivity=` | — | `{"fact_set_version","at","facts":[...],"excluded":[{"key","reason"}]}`; scope params repeat; `at` defaults to today, `max_sensitivity` to internal; reasons: draft, superseded, retired, expired, not_yet_valid, restricted, internal, out_of_scope, scope_unspecified (checked in that order: status, sensitivity, dates, scope) |
| GET | `/facts/changes?since=` | — | `{"seq","changes":[{"seq","key","version","kind","at"}]}` (kind: created, updated, confirmed, retired, expired, superseded; ≤ 1000 per call) |
| GET/POST | `/questions` | `{"kind":"missing_fact\|confirm\|scope","fact_key"?,"task_id"?,"text"}` | `{"questions":[...]}` / the question `{"id","kind","fact_key","task_id","text","status","answer","created_at","closed_at"}` |
| POST | `/questions/{id}/answer` · `/dismiss` | `{"answer"?}` | the closed question; 409 if already closed |
| GET | `/starter-kits` | — | `[{"id","label","description","fact_types","claim_classes","forbidden_phrasing":[{"phrase","why","claim_class","needs"}],"required_disclosures":[{"when","text","claim_class"}]}]` |
| POST | `/starter-kits/{id}/apply` | — | owner key; stores the kit's phrasings and disclosures as **draft rules** (idempotent): `{"kit","created","existing","rules","suggested_fact_types"}` |
| GET | `/rules?status=&kit=` | — | `{"rules":[{"id","kit","kind":"forbidden_phrase\|required_disclosure","phrase","why","claim_class","needs","status"}]}` |
| POST | `/rules/{id}/confirm` · `/dismiss` | — | owner key; rule status active / dismissed |
| POST | `/disclosure-wordings` | `{"fact_key","disclosure","wording","task_id"?,"proposed_by"?}` | 201 a **draft** wording row; 404 unknown fact; 422 when `disclosure` is not one of the fact's current `required_disclosures` (case and spaces ignored) or `wording` is empty / over 200 chars; the same wording again → 200 with the existing row (a dismissed one stays dismissed) |
| GET | `/disclosure-wordings?status=&fact_key=` | — | `{"wordings":[{"id","fact_key","disclosure","wording","status","proposed_by","task_id","created_at","decided_by","decided_at"}]}`, newest first, ≤ 500 |
| POST | `/disclosure-wordings/{id}/confirm` · `/dismiss` | — | owner key (`X-Actor` → `decided_by`); the row, status active / dismissed; 404 unknown id |

### The business's own words for a disclosure

A fact can require a disclosure ("riders must be 16 or over"). Real copy often says it in other
words ("for riders aged sixteen and up"), and the task bridge (88) then reports a missing
disclosure. When a person accepts that finding there and copies the exact words from the text,
88 proposes them here (`POST /disclosure-wordings`, a **draft**). Nothing changes until the owner
confirms it (`/disclosure-wordings/{id}/confirm`, owner key). From then on every served fact
(`/facts/query`, `/facts/v2`, `/facts/v2/{key}`) carries `disclosure_wordings`: the confirmed
wordings for that key (an empty list when none), and 88 counts any of them, word for word, as the
disclosure. A dismissed wording is never served and proposing it again keeps it dismissed.

Wordings are kept apart from the fact itself: they are not a new version, never merged into
`allowed_phrasing`, never in legacy `/facts`, and confirming or dismissing one writes nothing to
`/facts/changes` (so approved posts do not go back to draft). `disclosure_wordings` is read-only:
sending a listed fact back (import, PUT) ignores it.

### Legacy `/facts` compatibility guarantee

`GET /facts` keeps its shape and its positional ids `f1..fN`, because 44 strips `[fN]` markers and
03 renders them. The brand profile's facts come first, in exactly the order they always had; with an
empty or absent store the response is byte-identical to the one before the store existed (a golden
test pins it, and a read never creates the store file). Stored facts are appended after them only
when **active, valid today and not restricted**, filtered by `?max_sensitivity=` (default
`internal`; `public` is for a gateway that uses a hosted model). Each appended entry is
`{"id","text","key","version","scope_note"}`; a scoped fact carries its scope in the text, e.g.
`Parking is free (only: sites Quayside).`, so a writer never reads it as true everywhere. A derived
line replaced by a confirmed fact (`supersedes_key`) is left out and the ids stay contiguous. If the
store cannot be read, `/facts` logs it and serves the brand profile's facts alone.

### Industry starter kits

`config/starter-kits/{hospitality,manufacturing,saas,retail,clinic,restaurant,consultancy}.yaml` are
data, not code: a label, suggested fact types with example attributes, the claim classes that
matter, wording to avoid with a one-line reason and the fact that would make it acceptable (e.g.
"UL Listed" needs a certification fact naming the variant; "SOC 2 Type II" needs that report;
"gluten-free" needs a current allergen fact), and disclosure patterns. They are cautious
marketing-hygiene prompts, **not legal or regulatory advice**. Applying a kit writes them to a
separate `rules` table as drafts (rules are not facts, so they never appear in `/facts` or
`/facts/query`); the owner confirms or dismisses each. 05 does not enforce rules yet; 88 and the
control room read them. Kits are validated on load (a broken kit file is a 500, never a partial
rule set); `STARTER_KITS_DIR` points elsewhere (add your own kit files there, e.g. for a regulated
sector). A disclosure whose `when` text says "non-Latin" is only enforced on copy containing non-Latin
script.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `BRAND_FILE` | `/config/brand.yaml` | brand profile yaml; missing file gives 503 on every endpoint except `/health` |
| `VOICE_FILE` | `/config/voice.json` | stored voice profile; its folder must be writable (mount `/config` read-write, or point this at a volume) |
| `BRAND_OVERRIDES_FILE` | `/config/brand.overrides.json` | edits saved over `brand.yaml` (JSON, written atomically); its folder must be writable: in the stack point it at a volume, e.g. `/data/brand.overrides.json` |
| `FACTS_DB` | `/data/facts.sqlite` | the scoped fact store (SQLite, WAL); created on the first write, never by a read; its folder must be writable |
| `FACT_OWNER_KEY` | unset | a human's key for confirm / retire / import / applying a starter kit (`X-Owner-Key`); unset = those answer 503 |
| `FACTS_TODAY` | unset | pins "today" (`YYYY-MM-DD`) for validity checks; for eval replays only |
| `STARTER_KITS_DIR` | `config/starter-kits` next to `app/` | where the starter kit yaml files are read from |
| `INTERNAL_API_KEY` | unset | when set, every endpoint except `/health` needs it in `X-API-Key`, reads included: with several clients on one machine each has its own key, so a URL pointing at the wrong client's brand fails instead of answering with that brand's facts |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
