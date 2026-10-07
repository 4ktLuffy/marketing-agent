# 24 · Marketing chat agent

Deploy **24 of 91** of the marketing agent. This deploy is an n8n workflow.

The agent you talk to. The chat runs on `mkt-agent` (your local Ollama model) through the LLM gateway (03), so every model call shows on the control room's Activity page, with 8 turns of memory and 17 tools. Each tool is a sub-workflow (see the table), so the model only decides *what* to do and the tools do the work.

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
  n8n publish:workflow --id=mktWf24ChatAgent
```

Its workflow id is fixed (`mktWf24ChatAgent`), because other workflows call it by that id. Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.

## Trigger

n8n hosted chat at `<N8N_PUBLIC_URL>/webhook/mkt-marketing-chat/chat` (n8n login required), or **Open chat** in the editor

## Tools the agent can call

| Tool | Deploy | What it does |
|---|---|---|
| `write_blog_post` | 25-wf-tool-blog-writer | Write a full blog article in the brand voice and save it as a draft. |
| `write_social_posts` | 26-wf-tool-social-writer | Write NEW social media posts about a topic, one per channel, and save them as drafts. If the user gives text or a link to turn into posts, use repurpose_content instead. |
| `write_ad_copy` | 27-wf-tool-ad-copy | Write Google search ad headlines (max 30 chars) and descriptions (max 90 chars). |
| `write_email` | 28-wf-tool-email-writer | Write an email newsletter (subject, preheader, body) and save it as a draft. |
| `write_content_format` | 57-wf-tool-content-formats | Write a short-form VIDEO SCRIPT (Reels/TikTok/Shorts), a LANDING PAGE, or a multi-email NURTURE SEQUENCE, and save it as a draft. Not for social posts, a single newsletter or a blog article. |
| `seo_brief` | 29-wf-tool-seo-brief | Create an SEO content brief (intent, titles, meta description, outline, FAQs) for a keyword. |
| `repurpose_content` | 30-wf-tool-repurpose | Turn text the user pasted, an article or a web page into social posts (e.g. 'turn this into tweets: ...'). |
| `research_url` | 31-wf-tool-research-url | Analyse any web page (e.g. a competitor): messages, pricing, strengths, weaknesses, opportunities. |
| `track_competitor` | 81-wf-tool-track-competitor | Start TRACKING a competitor: add it to the competitor list so its key pages (pricing, product) are watched for changes and its ads are followed. Not for a one-off analysis of a page (use research_url). |
| `keyword_research` | 32-wf-tool-keyword-research | Find real search keyword ideas for a seed keyword, grouped by intent. |
| `content_calendar` | 33-wf-tool-calendar | Read or change the content calendar. action = list | get | create | set_status | schedule. |
| `ask_knowledge_base` | 34-wf-tool-kb-answer | Answer questions from the knowledge base: our brand, products and policies, the daily morning trend digest, competitor-change notes and uploaded documents. |
| `plan_campaign` | 47-wf-tool-plan-campaign | Plan a new marketing campaign: creates the campaign with targets and a dated plan of posts, then drafts them for review. |
| `plan_content_month` | 64-wf-tool-content-engine | Plan a MONTH of content: many dated posts over 4 weeks across several channels from ONE topic or source (article, guide, transcript), fact-checked and drafted daily for review. Not for writing a single piece (use the writing tools). No goal or KPIs: for a campaign use plan_campaign. |
| `campaigns` | 53-wf-tool-campaigns | List campaigns, show a campaign's scorecard (results vs targets), activate/pause/complete/cancel one, or set a target. |
| `check_copy` | 35-wf-tool-quality-gate | Check copy against brand rules, platform limits and our approved facts; returns problems and a fixed version. |
| `clip_video` | 77-wf-tool-clips | Cut a long video the user already has (webinar, talk, podcast recording) into short vertical clips with captions and save them for approval. Needs a link to the video file. Not for writing a new video script (write_content_format) or turning text or an article into posts (repurpose_content). |

## Model settings

- The *Model (via LLM gateway)* node is n8n's OpenAI chat model node pointed at the gateway
  (`03-llm-gateway`, `POST /v1/chat/completions`). Its credential *LLM gateway (chat)* is
  created by `01-marketing-stack/scripts/import-n8n.sh`: base URL `http://llm-gateway:8000/v1`
  (`CHAT_GATEWAY_URL` to change it), API key = `INTERNAL_API_KEY` (from `.env`, never written
  to the repo) and a custom header `X-Caller: 24 Chat agent`. Every model call of the chat then
  shows on the control room's **Activity** page as *Chat agent asked mkt-agent (local) for a chat step*.
- The gateway forwards the request unchanged (messages, tools, tool calls, streaming) to its
  provider: Ollama's OpenAI-compatible `/v1` by default, or the hosted API when the gateway runs
  with `LLM_PROVIDER=openai`. It logs metadata only, never your messages or the answers.
- Model `AGENT_MODEL` (default `mkt-agent`, built by `02-ollama-models`), read from n8n's env.
  It must be on the gateway's allowlist; the stack's compose file adds `AGENT_MODEL` to it.
- Context 16384 tokens, from the `mkt-agent` Modelfile (`num_ctx`): Ollama's `/v1` API takes no
  context size, and 2048 would silently cut off the tool definitions. Another model needs
  `num_ctx` in its own Modelfile too.
- Temperature 0.2: the agent chooses tools, it doesn't write copy.

## Variants (optional)

Both have the same workflow id, so importing one replaces `workflow.json`.

- `variants/direct-ollama.json`: n8n talks to Ollama directly (*Local model (Ollama)* node,
  `numCtx` 16384, credential *Ollama (local)*), as before the gateway route. The chat then does
  not show on the Activity page. `import-n8n.sh` imports it when `CHAT_PROVIDER=direct`.
- `variants/hosted.json`: a hosted OpenAI-compatible model directly (Groq
  `openai/gpt-oss-120b` by default, `reasoning_effort` low), local Ollama as fallback.
  `import-n8n.sh` imports it when `CHAT_PROVIDER=hosted` and `CHAT_API_KEY` are set in `.env`.
  Your chat messages and tool results then go to that provider; the writing tools still use
  the gateway's model. These calls bypass the gateway, so they don't show on the Activity page.

## Depends on

- `03-llm-gateway (`/v1/chat/completions`)`
- `02-ollama-models (mkt-agent)`
- `25–35 sub-workflows`
- `LLM gateway (chat) credential (imported by 01)`

Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),
which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so
workflows can read them.

Failures go to `43-wf-error-handler`.

## CI

Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.
