# 49 · Revise a rejected draft

Deploy **49 of 60** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Rewrites a rejected draft following the reviewer's reason, keeps its links, and runs it through the quality gate and fact check again. It goes back to `in_review` if it passes. After 3 rejections of the same item (counted by the learning service, 46) it stops and leaves the item as a draft for a human.

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
  n8n publish:workflow --id=mktWf49ReviseDra
```

Its workflow id is fixed (`mktWf49ReviseDra`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `item_id` | calendar item id (status must be `draft`) |
| `reason` | what the reviewer wants changed |

**Returns:** the calendar item is updated

**Called by:** 38 approval form (without waiting), on *Reject – rewrite*

## Depends on

- `19-content-calendar`
- `46-learning-service`
- `03-llm-gateway`
- `35-wf-tool-quality-gate`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "item_id": "1",
  "reason": "Too salesy. Calmer, and say what it tastes like."
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
