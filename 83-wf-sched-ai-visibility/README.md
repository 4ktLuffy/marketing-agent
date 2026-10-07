# 83 · AI visibility

Deploy **83 of 91** of the marketing agent. This deploy is an n8n scheduled workflow.

Every Tuesday it measures whether AI assistants name and cite the brand when buyers ask about the category, through `82-ai-visibility` (official APIs only; nothing is scraped). It checks 82's `/health` (a provider key and an approved question set are needed; otherwise it says what is missing and stops), then starts a run and waits for it (`POST /runs?wait=true`: every approved question × every provider with a key × `VIS_SAMPLES` answers). It reads `GET /summary` (per provider: mention rate on unaided questions with a 95% interval, citation rate, average list position, share of voice, change vs the previous run on the same question set), `GET /claims/wrong` (sentences about the brand that the claim checker (44) could not support with the approved facts) and `GET /gaps` (questions where competitors are named or cited and the brand is not, with the pages the answers cite and a suggested answer-first FAQ).

Wrong claims (wrong numbers such as prices first) and gaps, taking turns, become calendar (19) items with channel `visibility_gap` and status `idea`: at most `VISIBILITY_IDEAS_PER_WEEK` per 7 days, and never the same claim or question twice within 28 days (the notes carry `visibility gap key [...]`). The publisher (39) never sends `visibility_gap` items anywhere, even approved. A gap item says which SEO brief (29) to ask for and which page to refresh (68). The summary goes to the notification channel. Every number is labelled as an API answer: it approximates, but is not, what a person sees in the ChatGPT or Perplexity app.

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
  n8n publish:workflow --id=mktWf83AiVisibil
```

Its workflow id is fixed (`mktWf83AiVisibil`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

Tuesdays 06:00. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `VISIBILITY_URL` | 82-ai-visibility, e.g. `http://ai-visibility:8000`. Empty = the workflow does nothing |
| `VISIBILITY_IDEAS_PER_WEEK` | most calendar ideas per 7 days; default 5 (0 = none) |
| `NOTIFY_WEBHOOK_URL` | optional; receives the summary |

## Depends on

- `82-ai-visibility`
- `19-content-calendar`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
