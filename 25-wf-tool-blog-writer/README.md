# 25 · Blog writer

Deploy **25 of 60** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Writes a markdown blog post. It pulls brand facts from the knowledge base (06), writes through the gateway (03), runs the quality gate (35) and saves the post to the calendar (19).

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
  n8n publish:workflow --id=mktWf25BlogWrite
```

Its workflow id is fixed (`mktWf25BlogWrite`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `topic` | what the post is about |
| `audience` | optional |
| `keywords` | optional, comma-separated |

**Returns:** `{result}`: a calendar reference, the quality notes and the full post

**Called by:** the chat agent (24), tool `write_blog_post`

## Depends on

- `03-llm-gateway`
- `06-knowledge-base`
- `19-content-calendar`
- `35-wf-tool-quality-gate`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "topic": "How to stay focused working from home",
  "audience": "remote engineers",
  "keywords": ""
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
