# llm-gateway

Deploy **3 of 60** of the local-LLM marketing agent. Every LLM call the agent makes
goes through this service. You call it with a **prompt name and variables**, and it
returns **validated text or JSON**.

Small local models drift out of format, so this service keeps all the handling in one place:

- **Templates.** It renders named prompts from `04-prompt-library` with Jinja in a
  sandbox, and reloads them when the files change.
- **Brand.** It injects the brand profile from `05-brand-service` into every prompt as `{{ brand }}`,
  followed by the active rules from `46-learning-service` when `LEARNING_URL` is set.
- **Facts.** Prompts that declare a `facts` var get the brand's approved facts from
  `05-brand-service` `/facts` as numbered lines (`[f1] Desk Blend is a medium roast ...`).
  Writing prompts that only saw the brand summary invented tasting notes, dates and numbers.
- **Structured output.** For JSON prompts it passes the prompt's JSON Schema to Ollama as
  `format`, which constrains decoding, then validates the result with `jsonschema`.
- **Retries with feedback.** When output is invalid, too long or empty, it shows the model
  its own answer and the exact problem, then retries (3 attempts by default).
- **Cleanup.** It strips `<think>` blocks and unwraps answers wrapped in a single code fence.
- **Local or hosted.** It uses Ollama by default, or any OpenAI-compatible API (for example
  Groq) with `LLM_PROVIDER=openai`. JSON prompts use `json_schema` there, falling back to
  JSON mode for models without it. Every response reports token `usage`.

## Where to deploy

**Docker host** running `01-marketing-stack`, on the `marketing` network. n8n calls it at
`http://llm-gateway:8000` (env `GATEWAY_URL`). The prompt library is mounted read-only at
`/prompts`. It needs Ollama reachable at `OLLAMA_URL`.

## Run

```bash
docker build -t llm-gateway .
docker run --rm -p 8103:8000 \
  -e OLLAMA_URL=http://host.docker.internal:11434 -e MODEL=mkt-writer \
  -v "$PWD/../04-prompt-library/prompts:/prompts:ro" llm-gateway
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
PROMPTS_DIR=../04-prompt-library/prompts OLLAMA_URL=http://localhost:11434 MODEL=mkt-writer \
  uvicorn app.main:app --port 8103
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok","model","ollama": true/false}` |
| GET | `/v1/prompts` | — | `[{"name","description","output","required_vars","optional_vars"}]` |
| POST | `/v1/run` | `{"prompt","vars":{},"model"?,"temperature"?}` | `{"prompt","model","output","attempts","duration_ms"}` |

Errors: `404` unknown prompt · `422` missing required vars · `502` Ollama unreachable or
no valid output after the last attempt (the detail says what was wrong).

```bash
curl -s localhost:8103/v1/run -H 'content-type: application/json' -d '{
  "prompt": "ad_copy",
  "vars": {"product": "Coffee subscription for remote workers", "offer": "First bag free"}
}'
# {"prompt":"ad_copy","model":"mkt-writer","output":{"headlines":[...],"descriptions":[...]},"attempts":1,"duration_ms":6200}
```

## Configuration

| Env | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama`, or `openai` for any OpenAI-compatible API (Groq, OpenRouter, Together, OpenAI) |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | empty | for `LLM_PROVIDER=openai`, e.g. `https://api.groq.com/openai/v1`. The key is never logged or returned |
| `REASONING_EFFORT` | empty | for reasoning models on `openai` provider: `low`/`medium`/`high`. Use `low` for `gpt-oss` on Groq (default effort produced empty JSON for social posts; `low` passed 4/4) |
| `RATE_LIMIT_RETRIES` | `4` | 429 retries, waiting as long as the API's reset headers say (max 60 s each). A daily-limit 429 stops immediately with a clear error |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama server |
| `INTERNAL_API_KEY` | empty | when set (the stack sets it), `POST /v1/run` requires header `X-API-Key` with this value. `/health` and `/v1/prompts` stay open |
| `ALLOWED_MODELS` | empty | extra models a caller may request in `model` (comma-separated). Always allowed: `MODEL` and models named in prompt files. Anything else gets 403, so nothing on the network can run arbitrary (paid) models through the gateway |
| `MAX_VARS_CHARS` | `60000` | largest `vars` (as JSON) accepted; 413 above it. `temperature` must be 0–2 |
| `MODEL` | `qwen2.5:7b` | default model; the stack sets `mkt-writer` (deploy 02) |
| `PROMPTS_DIR` | `/prompts` | prompt templates (deploy 04) |
| `BRAND_URL` | empty | brand service; its summary is injected as `{{ brand }}` (cached 60 s; last good value kept if it goes down). Prompts that declare a `facts` var also get its `/facts` as `[id] text` lines, one per fact: cached 60 s, last good value kept if it goes down, empty string before the first success (never an error). Not fetched for prompts without the var, and not touched when the caller passes `facts` in `vars` (pass `""` to send none) |
| `LEARNING_URL` | empty | learning service (deploy 46); its `/rules/summary` (active rules learned from reviewer edits) is appended to `{{ brand }}` after the brand summary, separated by a blank line. Cached 60 s; last good value kept if it goes down; ignored when empty or unavailable. Neither summary is added when the caller passes `brand` in `vars` |
| `MAX_ATTEMPTS` | `3` | tries per call |
| `NUM_CTX` | `8192` | context window sent to Ollama |
| `REQUEST_TIMEOUT` | `240` | seconds per Ollama call |

## Measured on qwen2.5:7b (M-series Mac, 16 GB)

All 14 prompts produced valid output on the first attempt. Calls took 2–35 s (short
answers are fast; blog posts and multi-channel social posts take ~30 s). Quality is
measured separately by `23-eval-suite`.

## CI

Runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on every push to `main`.
