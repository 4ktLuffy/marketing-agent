# 75 · Experiment analysis

Deploy **75 of 87** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every Monday at 08:30 (before the weekly report) it asks the campaign service (45) to record the weekly look of every running experiment (`POST /experiments/{id}/decide`). 45 decides in code: per arm a Gamma-Poisson posterior of clicks per post (72 h after publishing, from the link shortener 16) with a prior at the pooled channel rate and a dispersion correction, the 95% HDI of the relative lift, and the HDI + ROPE rule (±15%): `winner`, `no_practical_difference`, or `inconclusive` at the last look (`max_weeks`); otherwise it waits for the next look. A look that is not due yet is refused (409) and skipped here: results are never peeked at early. A winner or a no-difference result goes to the learning service (46, `/rules/from-experiment`): a winner starts a **provisional** rule that writers do not see; a later experiment in the same direction marks it replicated, and only a person makes it active in the rules form (51); a contradicting result demotes or retires it. The summary (with the rules form link when a rule waits) goes to your webhook.

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
  n8n publish:workflow --id=mktWf75ExpAnalys
```

Its workflow id is fixed (`mktWf75ExpAnalys`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

Mondays 08:30. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `NOTIFY_WEBHOOK_URL` | optional; gets the weekly look summary |
| `N8N_PUBLIC_URL` | for the rules form link |

## Depends on

- `45-campaign-service`
- `46-learning-service`
- `16-link-shortener (through 45)`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
