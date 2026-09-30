# 81 · Track a competitor

Deploy **81 of 89** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Adds a competitor from its homepage. It reads the homepage with the page extractor (07, `list_links: true`), and the LLM (prompt `competitor_key_pages`) picks at most 5 key pages (pricing, product, features, about) **from that page's own links**. Checked in code: a URL is kept only if it is exactly one of the homepage's links on the same domain (login, cart, legal, careers, contact pages are never candidates) and 07 can read it. The competitor is saved in the registry (78 `POST /competitors?upsert=true`, status `active`); 78 turns the homepage and the key pages into 09 watches with default filters (dates, cookie lines, "Only 3 left" counters ignored). Running it again for the same site updates it. The answer lists what is watched and how ads are covered (Meta API for EU markets; Google, LinkedIn and TikTok as links to open by hand). With `AD_LIBRARY_URL` empty it adds nothing and says why.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand, from this folder:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  sh -c 'cat > /tmp/wf.json && n8n import:workflow --input=/tmp/wf.json' < workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf81TrackComp
```

Its workflow id is fixed (`mktWf81TrackComp`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `url` | the competitor's homepage |
| `name` | optional display name |
| `markets` | optional 2-letter countries, comma-separated (default: 78's AD_COUNTRIES) |

**Returns:** `{result}`: markdown confirmation

**Called by:** the chat agent (24), tool `track_competitor`

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `AD_LIBRARY_URL` | 78-ad-library-sync, e.g. `http://ad-library-sync:8000` |

## Depends on

- `03-llm-gateway`
- `07-page-extractor`
- `78-ad-library-sync`
- `09-change-monitor (via 78)`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "url": "https://example.com",
  "name": "",
  "markets": ""
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
