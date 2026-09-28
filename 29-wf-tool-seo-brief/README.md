# 29 · SEO brief

Deploy **29 of 87** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Builds an SEO content brief from real autocomplete searches (10) and, if you give one, an audit of a competitor page (12). The brief's prose (titles, meta description, outline, FAQ answers) is then fact-checked by the claim checker (44) against the approved facts and the inputs (keyword, audience; not the audited page, which may be a competitor's). Nothing is removed: if any statement is unsupported, the brief starts with a one-line warning and ends with a `## Claims to check before writing` list (each statement and why). If the checker is down, the brief says it was not checked.

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
  n8n publish:workflow --id=mktWf29SeoBrief0
```

Its workflow id is fixed (`mktWf29SeoBrief0`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `keyword` | target keyword |
| `competitor_url` | optional page that ranks today |
| `audience` | optional |

**Returns:** `{result}`: markdown brief (intent, titles, meta, outline, FAQs; plus the claims to check, if any)

**Called by:** the chat agent (24), tool `seo_brief`

## Depends on

- `03-llm-gateway`
- `10-keyword-suggest`
- `12-seo-auditor`
- `44-claim-checker`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "keyword": "coffee subscription for remote workers",
  "competitor_url": "",
  "audience": ""
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
