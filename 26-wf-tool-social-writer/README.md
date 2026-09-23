# 26 · Social post writer

Deploy **26 of 53** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Writes one native post per channel, in the style of up to 3 posts you approved or edited before (46). It removes channels nobody asked for, makes sure the link is present, tags the link with UTM parameters per channel (15), runs every post through the quality gate (35) and saves each post to the calendar (19).

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/26-wf-tool-social-writer/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf26SocialWri
```

Its workflow id is fixed (`mktWf26SocialWri`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `topic` | what to post about |
| `channels` | e.g. `x, linkedin, instagram` |
| `link` | optional URL |
| `campaign` | optional; UTM campaign, default `always-on` |

**Returns:** `{result}`: each post with its calendar id and quality notes

**Called by:** the chat agent (24), tool `write_social_posts`

## Depends on

- `03-llm-gateway`
- `15-utm-builder`
- `19-content-calendar`
- `35-wf-tool-quality-gate`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "topic": "Our new decaf, roasted in small batches",
  "channels": "x, linkedin",
  "link": "https://example.com/decaf",
  "campaign": "decaf launch"
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
