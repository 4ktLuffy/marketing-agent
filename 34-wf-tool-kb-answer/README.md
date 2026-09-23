# 34 · Knowledge base answer

Deploy **34 of 53** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Answers questions about your brand only from the knowledge base (06), with numbered citations. If nothing relevant is found, it says so instead of guessing.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/34-wf-tool-kb-answer/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf34KbAnswer0
```

Its workflow id is fixed (`mktWf34KbAnswer0`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `question` | the question |

**Returns:** `{result}`: answer and sources

**Called by:** the chat agent (24), tool `ask_knowledge_base`

## Depends on

- `03-llm-gateway`
- `06-knowledge-base`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "question": "Can I pause my subscription?"
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
