# 53 · Campaigns

Deploy **53 of 60** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Lets the chat agent list campaigns, show a fresh scorecard, set targets, and activate, pause, complete or cancel a campaign (45). A campaign can't be activated without a target, and that rule is enforced by the service.

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
  n8n publish:workflow --id=mktWf53Campaigns
```

Its workflow id is fixed (`mktWf53Campaigns`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `action` | `list` · `scorecard` · `activate` · `pause` · `complete` · `cancel` · `set_target` |
| `campaign` | campaign id, slug or name |
| `metric` | for set_target |
| `target_value` | for set_target |

**Returns:** `{result}`

**Called by:** the chat agent (24), tool `campaigns`

## Depends on

- `45-campaign-service`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "action": "list",
  "campaign": "",
  "metric": "",
  "target_value": ""
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
