# 27 · Ad copy

Deploy **27 of 53** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Writes responsive search ad assets. Google's length limits are enforced by the gateway's schema, and the copy gets a brand check before it's saved to the calendar.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/27-wf-tool-ad-copy/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf27AdCopy000
```

Its workflow id is fixed (`mktWf27AdCopy000`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `product` | what is advertised |
| `offer` | optional |
| `audience` | optional |
| `keywords` | optional |

**Returns:** `{result}`: headlines and descriptions with character counts

**Called by:** the chat agent (24), tool `write_ad_copy`

## Depends on

- `03-llm-gateway`
- `05-brand-service`
- `19-content-calendar`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "product": "Coffee subscription for remote workers",
  "offer": "First bag free",
  "audience": "",
  "keywords": ""
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
