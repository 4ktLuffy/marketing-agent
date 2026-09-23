# 30 · Repurpose content

Deploy **30 of 53** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Turns an article, a web page (fetched with 07) or pasted text into one grounded post per channel. Each post goes through the quality gate and is saved to the calendar.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/30-wf-tool-repurpose/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf30Repurpose
```

Its workflow id is fixed (`mktWf30Repurpose`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `url` | page to repurpose (or leave empty and give text) |
| `text` | optional pasted content |
| `channels` | e.g. `x, linkedin` |
| `link` | optional link to include (defaults to url) |

**Returns:** `{result}`: posts with calendar ids

**Called by:** the chat agent (24), tool `repurpose_content`

## Depends on

- `03-llm-gateway`
- `07-page-extractor`
- `19-content-calendar`
- `35-wf-tool-quality-gate`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "url": "",
  "text": "Remote workers lose focus after lunch. A lighter roast feels less heavy after a meal. We tested three brew ratios and 1:16 was the favourite.",
  "channels": "x, linkedin",
  "link": "https://example.com/afternoon"
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
