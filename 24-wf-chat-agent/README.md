# 24 · Marketing chat agent

Deploy **24 of 53** of the local-LLM marketing agent. This deploy is an n8n workflow.

The agent you talk to. The chat runs on `mkt-agent` (your local Ollama model) with 8 turns of memory and 11 tools. Each tool is one of the sub-workflows 25–35, so the model only decides *what* to do and the tools do the work.

## Where to deploy

Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:

```bash
cd ../01-marketing-stack && ./scripts/import-n8n.sh
```

Or by hand:

```bash
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/deploys/24-wf-chat-agent/workflow.json
docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \
  n8n publish:workflow --id=mktWf24ChatAgent
```

Its workflow id is fixed (`mktWf24ChatAgent`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Trigger

n8n hosted chat at `<N8N_PUBLIC_URL>/webhook/mkt-marketing-chat/chat` (n8n login required), or **Open chat** in the editor

## Tools the agent can call

| Tool | Deploy | What it does |
|---|---|---|
| `write_blog_post` | 25-wf-tool-blog-writer | Write a full blog article in the brand voice and save it as a draft. |
| `write_social_posts` | 26-wf-tool-social-writer | Write social media posts, one per channel, and save them as drafts. |
| `write_ad_copy` | 27-wf-tool-ad-copy | Write Google search ad headlines (max 30 chars) and descriptions (max 90 chars). |
| `write_email` | 28-wf-tool-email-writer | Write an email newsletter (subject, preheader, body) and save it as a draft. |
| `seo_brief` | 29-wf-tool-seo-brief | Create an SEO content brief (intent, titles, meta description, outline, FAQs) for a keyword. |
| `repurpose_content` | 30-wf-tool-repurpose | Turn an existing article, web page or pasted text into social posts. |
| `research_url` | 31-wf-tool-research-url | Analyse any web page (e.g. a competitor): messages, pricing, strengths, weaknesses, opportunities. |
| `keyword_research` | 32-wf-tool-keyword-research | Find real search keyword ideas for a seed keyword, grouped by intent. |
| `content_calendar` | 33-wf-tool-calendar | Read or change the content calendar. action = list | get | create | set_status | schedule. |
| `ask_knowledge_base` | 34-wf-tool-kb-answer | Answer questions from the knowledge base: our brand, products and policies, the daily morning trend digest, competitor-change notes and uploaded documents. |
| `plan_campaign` | 47-wf-tool-plan-campaign | Plan a new marketing campaign: creates the campaign with targets and a dated plan of posts, then drafts them for review. |
| `campaigns` | 53-wf-tool-campaigns | List campaigns, show a campaign's scorecard (results vs targets), activate/pause/complete/cancel one, or set a target. |
| `check_copy` | 35-wf-tool-quality-gate | Check copy against brand rules, platform limits and our approved facts; returns problems and a fixed version. |

## Model settings

- Model `mkt-agent:latest`, built by `02-ollama-models`. To use another model, change it in the *Local model* node.
- `numCtx` 16384. n8n's default of 2048 silently cuts off the tool definitions.
- Temperature 0.2: the agent chooses tools, it doesn't write copy.

## Depends on

- `02-ollama-models (mkt-agent)`
- `25–35 sub-workflows`
- `Ollama credential (imported by 01)`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
