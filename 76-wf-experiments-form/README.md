# 76 · Review experiments

Deploy **76 of 81** of the local-LLM marketing agent. This deploy is an n8n form workflow.

A web form listing the experiments the agent (74) or a person proposed, each with its hypothesis, the two versions, and the stopping rule stated up front. **Approve** moves it to `approved` in the campaign service (45; the call carries the approver key, which only n8n holds); the next plan of the content engine (61) then assigns the two versions to planned slots, balanced by weekday and hour, and the experiment runs. **Reject** stops it. Every post of an experiment still goes through the approval form (38).

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
  n8n publish:workflow --id=mktWf76ExpForm00
```

Its workflow id is fixed (`mktWf76ExpForm00`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Trigger

n8n form at `<N8N_PUBLIC_URL>/form/mkt-experiments`

## Depends on

- `45-campaign-service`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
