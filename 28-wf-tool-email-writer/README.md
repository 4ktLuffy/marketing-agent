# 28 · Email newsletter

Deploy **28 of 60** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Writes a newsletter: subject, preheader, body and CTA. It uses brand facts from the knowledge base, runs the quality gate on the body, checks the subject length, renders email-safe HTML (18) and saves a draft to the calendar.

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
  n8n publish:workflow --id=mktWf28EmailWrit
```

Its workflow id is fixed (`mktWf28EmailWrit`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `topic` | what the email is about |
| `audience` | optional |
| `cta_url` | optional button link |

**Returns:** `{result}`: subject, preheader, body, quality notes

**Called by:** the chat agent (24), tool `write_email`

## Depends on

- `03-llm-gateway`
- `06-knowledge-base`
- `14-platform-rules`
- `18-email-renderer`
- `19-content-calendar`
- `35-wf-tool-quality-gate`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "topic": "Our autumn roast lineup",
  "audience": "subscribers",
  "cta_url": "https://example.com/autumn"
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
