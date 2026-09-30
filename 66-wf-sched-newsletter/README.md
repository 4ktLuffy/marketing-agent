# 66 · Weekly newsletter

Deploy **66 of 90** of the local-LLM marketing agent. This deploy is an n8n scheduled workflow.

Every Friday it builds this week's newsletter from the content you already published (JTBD #7): calendar items published in the last 7 days plus approved blog posts (up to 8; review replies, emails and newsletters are left out). The LLM (prompt `newsletter_issue`) writes a subject, preheader, intro and one short section per item; every link must be copied from the items, and the workflow removes any other URL (listed in the notes). The text goes through the quality gate (35) in report-only mode (a rewrite would flatten the headings and links; its problems go into the notes and the item stays `draft`), is rendered as email HTML (18) and sent to the Listmonk bridge (63, `NEWSLETTER_URL/campaigns`) as a **draft** campaign named `Newsletter <ISO week>`: a person reviews and sends it in Listmonk. It also saves a calendar item (channel `newsletter`, `in_review` when the gate passed, else `draft`) whose notes say where the Listmonk draft is; the publisher (39) never sends it. One issue per ISO week: if the calendar already has a `newsletter` item with the week in its title, it stops. With fewer than 2 items it stops with a message. With `NEWSLETTER_URL` empty it still saves the calendar item, to copy by hand.

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
  n8n publish:workflow --id=mktWf66Newslettr
```

Its workflow id is fixed (`mktWf66Newslettr`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Schedule

Fridays 10:00. Change it in the first node.

## Configuration (env on the n8n container)

| Env | Meaning |
|---|---|
| `NEWSLETTER_URL` | listmonk-bridge (63), e.g. `http://listmonk-bridge:8000`; called with `X-API-Key: INTERNAL_API_KEY`. Empty = only the calendar item |
| `NEWSLETTER_AUDIENCE` | optional, e.g. `existing customers` |
| `NOTIFY_WEBHOOK_URL` | optional; receives a one-line summary |

## Depends on

- `19-content-calendar`
- `03-llm-gateway`
- `35-wf-tool-quality-gate`
- `18-email-renderer`
- `63-listmonk-bridge (optional)`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
