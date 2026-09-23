#!/usr/bin/env bash
# Pull base models and build the marketing-agent variants. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
export OLLAMA_HOST

command -v ollama >/dev/null || { echo "ollama not found: https://ollama.com/download"; exit 1; }
curl -fsS "$OLLAMA_HOST/api/tags" >/dev/null || { echo "ollama is not running at $OLLAMA_HOST"; exit 1; }

BASE_MODEL="${BASE_MODEL:-qwen2.5:7b}"
EMBED_MODEL="${EMBED_MODEL:-qwen3-embedding:0.6b}"

echo "==> pulling $BASE_MODEL and $EMBED_MODEL"
ollama pull "$BASE_MODEL"
ollama pull "$EMBED_MODEL"

for f in modelfiles/*.Modelfile; do
  name="$(basename "$f" .Modelfile)"
  echo "==> building $name"
  # Allow swapping the base model without editing the files.
  sed "s|^FROM .*|FROM $BASE_MODEL|" "$f" > "/tmp/$name.Modelfile"
  ollama create "$name" -f "/tmp/$name.Modelfile"
done

echo "==> done"; ollama list | grep -E "mkt-|$EMBED_MODEL"
