# 36 · Morning trend digest

Deploy **36 of 53** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every morning it polls your RSS feeds (08) and Hacker News/Reddit mentions (11), writes a short digest with post ideas, saves it to the knowledge base so the chat agent can answer "what was in today's digest?", and posts it to your webhook.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/36-wf-sched-trend-digest/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf36TrendDige
```

Its workflow id is fixed (`mktWf36TrendDige`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

08:00 daily (n8n `GENERIC_TIMEZONE`). Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `LISTENING_QUERY` | what to search for |
| `NOTIFY_WEBHOOK_URL` | Slack/Discord/Teams incoming webhook (optional) |

## Depends on

- `03-llm-gateway`
- `06-knowledge-base`
- `08-rss-watcher`
- `11-social-listening`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
