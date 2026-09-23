# 33 · Content calendar

Deploy **33 of 53** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Gives the agent access to the content calendar (19): list, get, create, change status and schedule items. Status changes follow the calendar's rules, so the agent can't skip human approval.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/33-wf-tool-calendar/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf33Calendar0
```

Its workflow id is fixed (`mktWf33Calendar0`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `action` | `list` · `get` · `create` · `set_status` · `schedule` |
| `id` | item id |
| `status` | filter (list) or new status (set_status) |
| `channel` | filter or new item channel |
| `title` | create |
| `body` | create |
| `scheduled_at` | ISO 8601 UTC for schedule/create |

**Returns:** `{result}`: readable lines

**Called by:** the chat agent (24), tool `content_calendar`

## Depends on

- `19-content-calendar`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "action": "list",
  "id": "",
  "status": "",
  "channel": "",
  "title": "",
  "body": "",
  "scheduled_at": ""
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
