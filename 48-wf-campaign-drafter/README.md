# 48 · Campaign drafter

Deploy **48 of 91** of the marketing agent. This deploy is an n8n sub-workflow.

Drafts every `idea` piece of a campaign in the background. Social posts get the campaign's tracked link (utm_content = the calendar item id) and 2 of your approved posts as style examples (46: the ones that earned clearly more clicks on that channel first, else the most recently approved). Every draft goes through the quality gate and fact check (35). Drafts that pass go to `in_review`; the others stay `draft` with the problems noted. Social pieces for instagram, facebook, threads, linkedin and x get a title card from 17 (the piece's title, the brand name from 05, sized by channel) as their `image_url`; if the card fails the piece is saved without one (an Instagram piece gets a note, since Instagram needs an image).

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
- `05-brand-service`
- `17-image-cards`

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
