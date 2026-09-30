# 64 · Content engine (plan a month)

Deploy **64 of 90** of the local-LLM marketing agent. This deploy is an n8n sub-workflow.

Plans a month of content from one pillar (a topic plus, ideally, the long piece it comes from: pasted text or a URL read by 07). It creates the pillar in the content engine (61), extracts 15–30 small self-contained ideas ("atoms": claims, stories, FAQs with their answer, tips, stats, objections, quotes) with the `pillar_atoms` prompt through the gateway (03, with the approved facts), drops in code the FAQ/objection atoms without an answer, and checks every atom with the claim checker (44), one at a time, with the pillar as evidence. Only verified atoms are stored as usable. 61 then plans 4 weeks in code (atoms × hook styles × formats × channels, platform cadences, no repeats) and this workflow creates one `idea` item per slot in the calendar (19) (on a re-plan, the idea items of the planned slots 61 replaced are moved to `rejected` with the note `re-planned`), with its date, channel, hook style and the note `engine pillar #P slot #S atom #A (<kind>)`. The drafter (65) writes those items each morning. It takes a while on a local model: one LLM call for the atoms plus one claim check per atom (tens of minutes for 25 atoms).

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
  n8n publish:workflow --id=mktWf64ContEngin
```

Its workflow id is fixed (`mktWf64ContEngin`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Inputs

| Input | Meaning |
|---|---|
| `topic` | what the pillar is about (its title) |
| `brief` | optional: one or two sentences on the angle (default: the topic) |
| `audience` | optional: who it is for |
| `channels` | comma list: linkedin, x, instagram, facebook, threads, mastodon, blog, email, video |
| `month` | optional: `YYYY-MM` or a month name (default: the start date's month, else next month) |
| `start_date` | optional: `YYYY-MM-DD` the 4 weeks start on (default: the 1st of the month) |
| `source_url` | optional: page with the pillar (read with 07) |
| `source_text` | optional: the pillar text itself (wins over the URL) |

**Returns:** `{result}`: atoms kept and dropped (with reasons), slots per channel against the cadence, unfilled slots, dates, calendar ids. A pillar with fewer than 12 verified atoms is stored but not planned, and the reply says why

**Called by:** the chat agent (24), tool `plan_content_month`

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `ENGINE_URL` | the content engine (61), default `http://content-engine:8000`. Empty (core install) = the tool answers that the content engine is not installed |

## Depends on

- `61-content-engine`
- `03-llm-gateway`
- `04-prompt-library (prompt `pillar_atoms`)`
- `07-page-extractor`
- `44-claim-checker`
- `19-content-calendar`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

## Try it

In n8n, open the workflow, click **Execute workflow** and paste this as the input:

```json
{
  "topic": "Why remote teams need a coffee ritual",
  "brief": "",
  "audience": "people-ops leads at remote companies",
  "channels": "linkedin, x",
  "month": "",
  "start_date": "",
  "source_url": "",
  "source_text": "Remote teams lose the small talk that happens by the office kitchen. A fixed weekly coffee call brings some of it back: same time, cameras on, no agenda."
}
```

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
