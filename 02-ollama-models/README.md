# ollama-models

Deploy **2 of 53** of the local-LLM marketing agent. This repo sets up the local models
everything else uses:

| Model | Built from | Used by | Why this configuration |
|---|---|---|---|
| `mkt-agent` | `qwen2.5:7b` | n8n chat agent (24) | temperature 0.2, `num_ctx` 16384, so the tool definitions and chat history aren't cut off |
| `mkt-writer` | `qwen2.5:7b` | LLM gateway (03) | `num_ctx` 8192, repeat penalty for long copy |
| `qwen3-embedding:0.6b` | pulled as-is | knowledge base (06) | small and fast; 1024-dim vectors |

Ollama's default context window is small. Without these builds, the chat agent's tool
list and history get cut off without any error, and the agent starts ignoring tools.

## Where to deploy

On the **machine that runs Ollama**. On a Mac, run Ollama natively (not in Docker)
so it uses the GPU through Metal. The Docker stack reaches it at
`http://host.docker.internal:11434`. On a Linux GPU server, run it there and set
`OLLAMA_URL` in `01-marketing-stack/.env`.

Memory: a 7B model at Q4 needs about 5 GB of RAM or VRAM, so 16 GB is enough. On
8 GB machines use `BASE_MODEL=qwen2.5:3b`; tool calling gets noticeably worse.

## Run

```bash
./scripts/setup.sh                 # pulls models, builds mkt-agent + mkt-writer
python3 scripts/smoke_test.py      # checks tool calling, JSON output, embeddings
```

To swap the base model:

```bash
BASE_MODEL=qwen3:8b ./scripts/setup.sh && python3 scripts/smoke_test.py
```

Only keep a base model if the smoke test passes with it. The tool-calling check is what
matters for the agent.

Expected output:

```
PASS tool calling (mkt-agent): write_social_posts({"channels": "x,linkedin", "topic": "New Decaf Coffee Release"})
PASS structured JSON (mkt-writer): ...
PASS embeddings (qwen3-embedding:0.6b): 2 vectors x 1024 dims
```

## Which base model? (measured)

Tool choice by the chat agent, from `23-eval-suite`, `python -m evalsuite.tools` (24 requests, 13 tools):

| Model | Right tool | Notes |
|---|---|---|
| `qwen2.5:7b` (default) | 72/72 (3 runs each) | |
| `qwen3.5:4b-mlx` | 45/48 (2 runs each) | smaller; slightly worse |
| `granite4:7b-a1b-h` | 43/48 (2 runs each) | calls tools for small talk ("What can you do?") |

Measure a new candidate the same way before switching: `python -m evalsuite.tools --model <name>`.

## Configuration

| Env | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server to set up or test |
| `BASE_MODEL` | `qwen2.5:7b` | model both variants are built from |
| `EMBED_MODEL` | `qwen3-embedding:0.6b` | embedding model |

## CI

Runs shellcheck on the scripts, compiles the smoke test, and checks every Modelfile
sets `num_ctx`. Model tests need a real Ollama, so run the smoke test on your machine.
