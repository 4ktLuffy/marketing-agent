# 38 · Content approval

Deploy **38 of 53** of the local-LLM marketing agent. This deploy is an n8n form workflow.

A web form where a person reviews drafts that are `in_review`. For each one: approve, edit the text and approve, reject with a reason (it is rewritten automatically by 49, up to 3 times), reject and drop, or send it back to draft, plus when it should publish. Every decision and edit is logged in the learning service (46), which is how the agent learns your preferences. It's the only way content reaches `approved`, and the publisher (39) only publishes approved items.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/38-wf-approval-form/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf38Approval0
```

Its workflow id is fixed (`mktWf38Approval0`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Trigger

n8n form at `<N8N_PUBLIC_URL>/form/mkt-content-approval`

## Depends on

- `19-content-calendar`
- `46-learning-service`
- `49-wf-revise-draft`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
