# 39 · Publisher

Deploy **39 of 53** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every 15 minutes it takes the approved calendar items that are due, swaps each link for a tracked short link (16), sends the post to your publish endpoint, and marks it published only if the endpoint accepted it.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/39-wf-sched-publisher/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf39Publisher
```

Its workflow id is fixed (`mktWf39Publisher`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

every 15 minutes. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `PUBLISH_WEBHOOK_URL` | endpoint that receives `{id, channel, title, text, link, campaign}`: a Zapier/Make/Buffer hook or your own. Empty = the workflow does nothing. |

## Setup

To post straight from n8n instead, replace the **Publish** node with n8n's LinkedIn, X or Facebook Graph node (with its credential) and keep the **Succeeded** check after it.

## Depends on

- `16-link-shortener`
- `19-content-calendar`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
