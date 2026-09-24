# 31 · Research a URL

Deploy **31 of 60** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Reads a web page (07) and returns a competitive analysis: messages, pricing, strengths, weaknesses and opportunities for your brand.

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
  n8n publish:workflow --id=mktWf31ResearchU
```

Its workflow id is fixed (`mktWf31ResearchU`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `url` | page to analyse |
| `focus` | optional, e.g. `pricing` |

**Returns:** `{result}`: markdown analysis

**Called by:** the chat agent (24), tool `research_url`

## Depends on

- `03-llm-gateway`
- `07-page-extractor`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "url": "https://example.com",
  "focus": ""
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
