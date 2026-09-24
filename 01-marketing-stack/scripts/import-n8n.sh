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

if [ -z "${FORMS_USER:-}" ] || [ -z "${FORMS_PASSWORD:-}" ] || [[ "${FORMS_PASSWORD}" == change-me* ]]; then
  echo "set FORMS_USER and FORMS_PASSWORD in .env first (login for the approval/knowledge/rules forms)"; exit 1
fi
echo "==> forms login credential (user: $FORMS_USER)"
python3 -c 'import json,os; print(json.dumps([{"id":"mktFormsLogin001","name":"Forms login","type":"httpBasicAuth","data":{"user":os.environ["FORMS_USER"],"password":os.environ["FORMS_PASSWORD"]}}]))' \
  | dc sh -c 'cat > /tmp/forms-cred.json && n8n import:credentials --input=/tmp/forms-cred.json && rm /tmp/forms-cred.json'

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
  dc sh -c 'cat > /tmp/wf.json && n8n import:workflow --input=/tmp/wf.json >/dev/null && rm /tmp/wf.json' < "$f"
done

# Optional: chat agent on a hosted OpenAI-compatible model (Groq by default).
if [ "${CHAT_PROVIDER:-local}" = "hosted" ]; then
  [ -n "${CHAT_API_KEY:-}" ] || { echo "CHAT_PROVIDER=hosted needs CHAT_API_KEY in .env"; exit 1; }
  export CHAT_BASE_URL="${CHAT_BASE_URL:-https://api.groq.com/openai/v1}" CHAT_MODEL="${CHAT_MODEL:-openai/gpt-oss-120b}"
  echo "==> hosted chat model: $CHAT_MODEL at $CHAT_BASE_URL (key not shown)"
  # The key goes through stdin, never argv.
  python3 -c 'import json,os; print(json.dumps([{"id":"mktHostedChat001","name":"Hosted chat model","type":"openAiApi","data":{"apiKey":os.environ["CHAT_API_KEY"],"url":os.environ["CHAT_BASE_URL"]}}]))' \
    | dc sh -c 'cat > /tmp/chat-cred.json && n8n import:credentials --input=/tmp/chat-cred.json && rm /tmp/chat-cred.json'
  python3 -c 'import json,os,sys; d=json.load(sys.stdin)
for n in d["nodes"]:
    if n["type"].endswith("lmChatOpenAi"): n["parameters"]["model"]["value"]=os.environ["CHAT_MODEL"]
print(json.dumps(d))' < ../24-wf-chat-agent/variants/hosted.json \
    | dc sh -c 'cat > /tmp/wf.json && n8n import:workflow --input=/tmp/wf.json >/dev/null && rm /tmp/wf.json'
fi

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
