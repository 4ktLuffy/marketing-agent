# 56 · Analytics sync (Umami)

Deploy **56 of 60** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every morning it pulls yesterday's visits and conversions per campaign and channel from Umami (through 55) into analytics (20), so campaign scorecards (52) and the weekly report (41) use real numbers without CSV uploads. It stays quiet on success and posts to your webhook if something failed.

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
  n8n publish:workflow --id=mktWf56AnalySync
```

Its workflow id is fixed (`mktWf56AnalySync`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

05:30 daily, before campaign measurement (06:00). Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `UMAMI_SYNC_URL` | `http://umami-sync:8000`; empty = skip (CSV uploads still work) |
| `NOTIFY_WEBHOOK_URL` | optional |

## Depends on

- `55-umami-sync`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
