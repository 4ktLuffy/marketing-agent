# change-monitor

Deploy **09 of 91** of the local-LLM marketing agent. It watches competitor pages (pricing,
features, landing pages) and, on each check, reports which ones changed with a line diff of
their visible text. The competitor-watch schedule (37) runs `/check` every 6 hours and has the
LLM summarise the diffs. It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network, with a volume on `/data`. n8n calls it at `http://change-monitor:8000`
(env `MONITOR_URL`). It keeps state in SQLite, so on other hosts give it a persistent disk.

## Run

```bash
docker build -t change-monitor .
docker run --rm -p 8109:8000 -e INTERNAL_API_KEY=change-me -v monitor-data:/data change-monitor
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me DB_PATH=./data/monitor.sqlite uvicorn app.main:app --port 8109
```

## Endpoints

🔑 = needs header `X-API-Key: $INTERNAL_API_KEY`.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/watches` 🔑 | `{"url","label"?,"css"?,"xpath"?,"include_filters"?,"ignore_patterns"?,"trigger_text"?,"tag"?}` | `201` watch (below) |
| GET | `/watches` | query `tag`? | `[watch]`, only those with that exact `tag` if given |
| GET | `/snapshots` 🔑 | query `tag`? | `[{"id","url","label","tag","last_checked_at","text"}]`: each checked watch's stored text (its last check), only that `tag` if given. 78's positioning map reads it. |
| DELETE | `/watches/{id}` 🔑 | — | `204`, or `404` |
| POST | `/check` 🔑 | — | `{"changed":[{"id","url","label","diff","added_words","removed_words","trigger"?}],"unchanged","errors":[{"id","url","error"}]}` |

A watch is `{"id","url","label","created_at","last_checked_at","css","xpath","include_filters","ignore_patterns","trigger_text","tag"}`;
unset fields are `null`, unset lists `[]`.

```bash
curl -s localhost:8109/watches -H 'content-type: application/json' -H 'X-API-Key: change-me' \
  -d '{"url":"https://example.com","label":"Example home"}'
# {"id":1,"url":"https://example.com","label":"Example home","created_at":"2026-09-23T08:00:00+00:00","last_checked_at":null,
#  "css":null,"xpath":null,"include_filters":[],"ignore_patterns":[],"trigger_text":null,"tag":null}
curl -s -X POST localhost:8109/check -H 'X-API-Key: change-me'
# {"changed":[],"unchanged":1,"errors":[]}
```

- The first check of a watch stores a baseline and counts as unchanged.
- Without `css`/`xpath`, page text is the visible text without `<head>`, scripts, styles,
  `<nav>` and `<footer>`, one line per block with whitespace collapsed, so a changed copyright
  year or menu does not count as a change.
- `diff` is a unified diff of those lines (`-` before, `+` after), capped at about 4,000
  characters. `added_words` / `removed_words` are word counts (multiset difference).
- `unchanged` is a count. A page that cannot be fetched goes to `errors` and keeps its old
  snapshot; the other watches are still checked.
- `/check` needs the key: it overwrites the stored snapshots.

### Selecting and filtering what counts as a change

All optional. Each check runs, in this order: **select** (`css`, `xpath`) → **include**
(`include_filters`) → **ignore** (`ignore_patterns`) → **trigger** (`trigger_text`). The stored
snapshot, `diff` and word counts use only the lines left after ignore.

| Field | Type | Meaning |
|---|---|---|
| `css` | string | CSS selector (lxml + cssselect). Only the matched elements are used. |
| `xpath` | string | XPath 1.0 (lxml). May return elements, or strings/text nodes (`//p/text()`, `string(//h1)`). |
| `include_filters` | list of regex | Keep only lines matching at least one. |
| `ignore_patterns` | list of regex | Drop lines matching any (dates, counters, "Only 3 left!"). |
| `trigger_text` | regex | Report the watch only when a line matching it appears or disappears; the change item gets `"trigger":"appeared"` or `"disappeared"` (`appeared` if both). Other line changes update the snapshot silently. |
| `tag` | string, ≤100 chars | Free label for an owner service; `GET /watches?tag=x` lists only those watches. |

- With both `css` and `xpath`, css matches come first, then xpath matches; an element matched
  twice is used once. Scripts, styles, `<noscript>`, `<template>` and `<svg>` inside the matches
  are removed; `<nav>`/`<footer>` are kept, since you chose the region. Each block becomes one
  line, as above.
- If the selectors match nothing, the watch goes to `errors` with
  `"css/xpath matched nothing on the page"` and keeps its old snapshot.
- Regexes are Python `re`, case-insensitive, at most 300 characters each and 20 per list;
  they match anywhere in a line (use `^`/`$` to anchor).
- A bad CSS selector, XPath or regex, or an over-long value, is refused at create time with
  `422` and a `detail` naming the field.
- To change a watch's filters, delete it and add it again. That starts a new baseline.

Pricing page, watching only the plan cards and ignoring the urgency counter and the date:

```bash
curl -s localhost:8109/watches -H 'content-type: application/json' -H 'X-API-Key: change-me' -d '{
  "url": "https://example.com/pricing", "label": "Acme pricing", "tag": "competitors",
  "css": "#plans",
  "ignore_patterns": ["only \\d+ left", "^last updated"]
}'
```

Other fields on the same page:

```bash
add() { curl -s localhost:8109/watches -H 'content-type: application/json' -H "X-API-Key: $INTERNAL_API_KEY" -d "$1"; }
# xpath: only the price text nodes
add '{"url":"https://example.com/pricing","xpath":"//p[@class=\"price\"]/text()"}'
# include_filters: whole page, but only lines that show a price or "contact us"
add '{"url":"https://example.com/pricing","include_filters":["\\$\\d+","contact us"]}'
# trigger_text: report only when "sold out" or "discount" shows up or goes away
add '{"url":"https://example.com/pricing","css":"#plans","trigger_text":"sold out|discount"}'
# tag: list one owner's watches
curl -s 'localhost:8109/watches?tag=competitors'
```

Errors: `401` wrong or missing `X-API-Key`. `503` on key-protected endpoints when
`INTERNAL_API_KEY` is not set on the server. `422` if a watch URL is blocked (see below) or a
selector/regex is invalid.

**SSRF guard.** Only `http`/`https`. The host is resolved and refused if any address is
loopback, private, link-local, reserved or multicast, both when the watch is added and on
every fetch, including each redirect hop. Fetching: 15 s timeout, up to 5 redirects, 5 MB max.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for `POST`/`DELETE /watches`, `POST /check` and `GET /snapshots`. Unset = those return `503`. |
| `DB_PATH` | `/data/monitor.sqlite` | SQLite file with watches and snapshots. |
| `ALLOW_PRIVATE_URLS` | `false` | `true` allows private and loopback addresses. Only for trusted local testing. |

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
