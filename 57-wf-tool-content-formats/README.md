# 57 · Content formats

Deploy **57 of 60** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Writes three formats the other writers don't: a short-form vertical **video script** (Reels/TikTok/Shorts, 30–60 s: hook, beats with spoken line, on-screen text and shot, CTA, caption, hashtags), **landing page** copy (hero, benefit blocks, social proof, answer-first FAQ, CTA) and a 3–5 email **nurture sequence** (day, subject, preview, body, CTA per email). It pulls context from the knowledge base (06), writes through the gateway (03) with the approved facts, runs the copy through the quality gate (35) and saves one draft to the calendar (19) for approval. For a landing page it first asks the review hub (58, `REVIEWS_URL`) for up to 3 testimonials with consent; the social proof must be one of them copied exactly, with its author, or `[add a customer quote]`, and the gate checks every quotation against the proof bank. Without the review hub it uses an approved fact or the placeholder.

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
  n8n publish:workflow --id=mktWf57ContentFm
```

Its workflow id is fixed (`mktWf57ContentFm`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `format` | `video_script`, `landing_page` or `email_sequence` (also accepts e.g. `reel`, `landing page`, `nurture`) |
| `topic` | what the video is about / the product the page sells / the goal of the sequence |
| `audience` | optional |
| `offer` | optional |
| `details` | optional facts the user gave for this piece |

**Returns:** `{result}`: calendar id and status, quality notes and the full piece. An unknown format returns a message listing the three

**Called by:** the chat agent (24), tool `write_content_format`

## Depends on

- `03-llm-gateway`
- `06-knowledge-base`
- `19-content-calendar`
- `35-wf-tool-quality-gate`
- `58-review-hub (optional, landing page testimonials)`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "format": "video_script",
  "topic": "How our decaf is made",
  "audience": "afternoon coffee drinkers",
  "offer": "",
  "details": ""
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
