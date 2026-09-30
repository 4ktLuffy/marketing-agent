# 74 · Experiment manager

Deploy **74 of 90** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every Monday the agent proposes at most 2 marketing experiments and saves them as `proposed` in the campaign service (45) for a person to approve in the experiments form (76); your webhook gets the approval link. The LLM (prompt `experiment_proposals`) sees the hook styles' clicks per post (45 `/insights/hooks`), clicks per channel (45 `/insights`), and every earlier experiment, including the ones that found **no practical difference** so it does not re-test them (45 also refuses those with 409). Code keeps a proposal only if its variable, channel and two values are valid, the arms differ, it is not already running or answered, and its `evidence` cites only numbers that appear in the data. An experiment is ONE variable (hook_style, format, cta, length or time), two arms, one channel, measured as clicks per post within 72 h of publishing. It stops early when 2 proposals already wait for a person (`EXPERIMENT_MAX_PROPOSED`). The model never decides a result: 45 does, in code (workflow 75).

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
  n8n publish:workflow --id=mktWf74ExpManagr
```

Its workflow id is fixed (`mktWf74ExpManagr`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

Mondays 07:00. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `EXPERIMENT_CHANNELS` | channels experiments may use (default `PLAN_CHANNELS`, else `linkedin, x`) |
| `EXPERIMENT_MAX_PROPOSED` | stop proposing while this many wait for approval (default 2) |
| `NOTIFY_WEBHOOK_URL` | optional; gets the proposals and the form link |
| `N8N_PUBLIC_URL` | for the form link |

## Depends on

- `45-campaign-service`
- `03-llm-gateway`
- `04-prompt-library (prompt `experiment_proposals`)`
- `76-wf-experiments-form`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
