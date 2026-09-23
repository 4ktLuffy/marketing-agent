#!/usr/bin/env bash
# Load the Ollama credential and all workflow deploys (NN-wf-*) into the stack's n8n, publish them,
# and restart n8n so schedules, forms and the chat go live. Safe to re-run: imports
# overwrite by id.
#
# Before the first run: `docker compose up -d` and create the n8n owner account in the UI.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

# Read .env like docker compose does (KEY=VALUE, values may contain spaces); never execute it.
load_env() {
  [ -f .env ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*# || "$line" != *=* ]] && continue
    key="${line%%=*}"; val="${line#*=}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] && export "$key=$val"
  done < .env
}

load_env
OLLAMA_URL="${OLLAMA_URL:-http://host.docker.internal:11434}"
dc() { docker compose exec -T n8n "$@"; }

echo "==> Ollama credential -> $OLLAMA_URL"
sed "s|http://host.docker.internal:11434|$OLLAMA_URL|" n8n/credentials/ollama.json \
  | dc sh -c 'cat > /tmp/ollama-cred.json && n8n import:credentials --input=/tmp/ollama-cred.json && rm /tmp/ollama-cred.json'

shopt -s nullglob
workflows=(../[0-9][0-9]-wf-*/workflow.json)
[ ${#workflows[@]} -gt 0 ] || { echo "no workflow repos found next to this one (run scripts/clone-all.sh)"; exit 1; }

# Import sub-workflows before the workflows that call them.
for f in "${workflows[@]}"; do
  name="$(basename "$(dirname "$f")")"
  echo "==> import $name"
  dc n8n import:workflow --input="/deploys/$name/workflow.json" >/dev/null
done

echo "==> publish"
for f in "${workflows[@]}"; do
  id="$(grep -m1 -o '"id": "mkt[A-Za-z0-9]*"' "$f" | cut -d'"' -f4)"
  dc n8n publish:workflow --id="$id" >/dev/null
done

echo "==> restart n8n"
docker compose restart n8n >/dev/null
echo "done. Chat: ${N8N_PUBLIC_URL:-http://localhost:5678/}webhook/mkt-marketing-chat/chat"
echo "      Approval form: ${N8N_PUBLIC_URL:-http://localhost:5678/}form/mkt-content-approval"
echo "      Knowledge form: ${N8N_PUBLIC_URL:-http://localhost:5678/}form/mkt-knowledge-add"
