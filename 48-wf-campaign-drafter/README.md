# 48 · Campaign drafter

Deploy **48 of 53** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Drafts every `idea` piece of a campaign in the background. Social posts get the campaign's tracked link (utm_content = the calendar item id) and 2 posts you approved before as style examples (46). Every draft goes through the quality gate and fact check (35). Drafts that pass go to `in_review`; the others stay `draft` with the problems noted.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/48-wf-campaign-drafter/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf48CampDraft
```

Its workflow id is fixed (`mktWf48CampDraft`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `campaign_id` | campaign id from 45 |

**Returns:** calendar items updated; a summary is posted to `NOTIFY_WEBHOOK_URL`

**Called by:** 47 plan campaign (without waiting)

## Depends on

- `45-campaign-service`
- `19-content-calendar`
- `03-llm-gateway`
- `46-learning-service`
- `35-wf-tool-quality-gate`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "campaign_id": "1"
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
