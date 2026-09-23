# 35 · Quality gate

Deploy **35 of 53** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Checks a piece of copy against the brand rules (05), the platform limits (14), readability (13) and the approved facts (claim checker, 44). If there are errors (including claims the facts don't support), it asks the LLM for a minimal rewrite and checks everything again. What still fails goes back as `problems` for a human.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/35-wf-tool-quality-gate/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf35QualityGa
```

Its workflow id is fixed (`mktWf35QualityGa`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `text` | copy to check |
| `channel` | x, linkedin, instagram, facebook, threads, mastodon, blog, email, google_ads … |
| `context` | optional: the brief or source the copy was written from; facts in it count as evidence |
| `ref` | optional: any id you want back on the result (e.g. a calendar item id) |

**Returns:** `{ref, ok, text, channel, rewritten, fixed[], problems[], warnings[], readability}` for each input item. Items come back in a different order than they went in (fixed ones first), so match results by `ref`

**Called by:** 25, 26, 28, 30 and the chat agent (24, tool `check_copy`)

## Depends on

- `05-brand-service`
- `13-readability`
- `14-platform-rules`
- `44-claim-checker`
- `03-llm-gateway`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "text": "Our Desk Blend has notes of chocolate and caramel and is roasted within 24 hours. https://example.com",
  "channel": "x",
  "context": ""
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
