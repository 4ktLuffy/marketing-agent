# 51 · Review learned rules

Deploy **51 of 60** of the local-LLM marketing agent. This deploy is an n8n form workflow.

A web form listing the writing rules the learning service proposed. Keep a rule and it is added to every writing prompt from then on (via the gateway, 03); reject it and it is never proposed again.

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
  n8n publish:workflow --id=mktWf51RulesForm
```

Its workflow id is fixed (`mktWf51RulesForm`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Trigger

n8n form at `<N8N_PUBLIC_URL>/form/mkt-rules-review`

## Depends on

- `46-learning-service`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
