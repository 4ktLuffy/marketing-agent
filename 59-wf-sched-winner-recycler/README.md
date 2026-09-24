# 59 · Winner recycler

Deploy **59 of 60** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every Monday it takes the posts whose tracked short links earned the most clicks in the last 90 days (45 insights, from 16 clicks) and turns the best ones into fresh drafts: same channel, same facts, a new opening, through the repurpose tool (30), which runs the quality gate (35) and saves each draft to the calendar (19) with the note `recycled from #<id> (<n> clicks)`. A post qualifies when it has at least `RECYCLE_MIN_CLICKS` clicks, was published at least `RECYCLE_MIN_AGE_DAYS` days ago and was not recycled in the last 90 days. At most `RECYCLE_PER_WEEK` posts are recycled per 7 days, most clicks first (fewer, better). The old tracked link is not copied: the draft gets the clean target URL, so the publisher (39) tracks the new post under its own id. Nothing is published until a person approves the draft. When nothing qualifies it posts the reasons and stops.

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
  n8n publish:workflow --id=mktWf59Recycler0
```

Its workflow id is fixed (`mktWf59Recycler0`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

Mondays 08:00. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `RECYCLE_MIN_CLICKS` | clicks a post needs to be recycled; default 5 |
| `RECYCLE_MIN_AGE_DAYS` | days since publishing before a post can be recycled; default 30 |
| `RECYCLE_PER_WEEK` | most posts recycled per 7 days; default 2 (0 turns recycling off) |
| `NOTIFY_WEBHOOK_URL` | optional; receives the weekly summary |

## Depends on

- `45-campaign-service`
- `16-link-shortener`
- `19-content-calendar`
- `30-wf-tool-repurpose`
- `35-wf-tool-quality-gate`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
