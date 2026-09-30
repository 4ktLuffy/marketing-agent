#!/usr/bin/env bash
# Load the Ollama credential and the workflow deploys (NN-wf-*) of the installed profile into the
# stack's n8n, publish them, and restart n8n so schedules, forms and the chat go live. Safe to
# re-run: imports overwrite by id.
#
# The profile is COMPOSE_PROFILES in .env (core, growth or full; unset = full). Workflows that need
# a service outside the profile are not imported (scripts/profiles.sh has the list).
#
# Before the first run: `docker compose up -d` and create the n8n owner account (install.sh does
# both).
#
# One client of several (MKT_CLIENT=<slug>, set by install.sh --client): imports into that client's
# n8n, and moves every schedule by SCHEDULE_OFFSET_MIN minutes (scripts/shift-cron.py) so clients'
# schedules don't all hit the one Ollama at the same minute.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
[ "${1:-}" = --client ] && export MKT_CLIENT="${2:-}"
# shellcheck source=profiles.sh
. scripts/profiles.sh   # and client.sh: load_env, compose, n8n_port

load_env
OLLAMA_URL="${OLLAMA_URL:-http://host.docker.internal:11434}"
dc() { compose exec -T n8n "$@"; }
offset="${SCHEDULE_OFFSET_MIN:-0}"
[[ "$offset" =~ ^[0-9]+$ ]] || { echo "SCHEDULE_OFFSET_MIN must be a number of minutes (got '$offset')"; exit 1; }
# The workflow as this install imports it: schedules moved by the client's offset.
workflow_json() { if [ "$offset" -gt 0 ]; then python3 scripts/shift-cron.py "$offset" < "$1"; else cat "$1"; fi; }

if [ -z "${FORMS_USER:-}" ] || [ -z "${FORMS_PASSWORD:-}" ] || [[ "${FORMS_PASSWORD}" == change-me* ]]; then
  echo "set FORMS_USER and FORMS_PASSWORD in $(env_file) first (login for the approval/knowledge/rules forms)"; exit 1
fi
echo "==> forms login credential (user: $FORMS_USER)"
python3 -c 'import json,os; print(json.dumps([{"id":"mktFormsLogin001","name":"Forms login","type":"httpBasicAuth","data":{"user":os.environ["FORMS_USER"],"password":os.environ["FORMS_PASSWORD"]}}]))' \
  | dc sh -c 'cat > /tmp/forms-cred.json && n8n import:credentials --input=/tmp/forms-cred.json && rm /tmp/forms-cred.json'

echo "==> Ollama credential -> $OLLAMA_URL"
sed "s|http://host.docker.internal:11434|$OLLAMA_URL|" n8n/credentials/ollama.json \
  | dc sh -c 'cat > /tmp/ollama-cred.json && n8n import:credentials --input=/tmp/ollama-cred.json && rm /tmp/ollama-cred.json'

# The chat agent (24) calls its model through the gateway's OpenAI-compatible endpoint, so its
# calls show on the control room's Activity page. The key is INTERNAL_API_KEY from .env: it goes
# through stdin, never argv, and never into a file in the repo.
chat_provider="${CHAT_PROVIDER:-local}"
case "$chat_provider" in
  local|hosted|direct) ;;
  *) echo "CHAT_PROVIDER must be local (via the gateway), direct (n8n -> Ollama) or hosted (got '$chat_provider')"; exit 1 ;;
esac
[ -n "${INTERNAL_API_KEY:-}" ] || { echo "set INTERNAL_API_KEY in $(env_file) first"; exit 1; }
export CHAT_GATEWAY_URL="${CHAT_GATEWAY_URL:-http://llm-gateway:8000/v1}"
echo "==> chat model credential -> $CHAT_GATEWAY_URL (key not shown)"
python3 -c 'import json,os; print(json.dumps([{"id":"mktGatewayChat01","name":"LLM gateway (chat)","type":"openAiApi","data":{"apiKey":os.environ["INTERNAL_API_KEY"],"url":os.environ["CHAT_GATEWAY_URL"],"header":True,"headerName":"X-Caller","headerValue":"24 Chat agent"}}]))' \
  | dc sh -c 'cat > /tmp/gw-chat-cred.json && n8n import:credentials --input=/tmp/gw-chat-cred.json && rm /tmp/gw-chat-cred.json'

profile="$(current_profile)"
# NN-wf-* deploys, then workflows that ship inside a service repo (72-control-room/n8n).
workflows=()
while IFS= read -r f; do workflows+=("$f"); done < <(profile_workflows "$profile")
[ ${#workflows[@]} -gt 0 ] || { echo "no workflow repos found next to this one (run scripts/clone-all.sh)"; exit 1; }
echo "==> profile $profile: ${#workflows[@]} workflows${MKT_CLIENT:+ for client $MKT_CLIENT}$([ "$offset" -gt 0 ] && echo ", schedules +$offset min")"

# Import sub-workflows before the workflows that call them.
for f in "${workflows[@]}"; do
  name="${f#../}"; name="${name%/workflow.json}"
  echo "==> import $name"
  workflow_json "$f" | dc sh -c 'cat > /tmp/wf.json && n8n import:workflow --input=/tmp/wf.json >/dev/null && rm /tmp/wf.json'
done

# Fallback: the chat agent talking to Ollama directly, as before the gateway route (not shown on
# the Activity page). Same workflow id, so it replaces the one imported above.
if [ "$chat_provider" = direct ]; then
  echo "==> chat agent: direct to Ollama (variants/direct-ollama.json)"
  dc sh -c 'cat > /tmp/wf.json && n8n import:workflow --input=/tmp/wf.json >/dev/null && rm /tmp/wf.json' \
    < ../24-wf-chat-agent/variants/direct-ollama.json
fi

# Optional: chat agent on a hosted OpenAI-compatible model (Groq by default).
if [ "$chat_provider" = "hosted" ]; then
  [ -n "${CHAT_API_KEY:-}" ] || { echo "CHAT_PROVIDER=hosted needs CHAT_API_KEY in $(env_file)"; exit 1; }
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

# Moved to a smaller profile: workflows of the larger one stay in n8n but are unpublished, so their
# schedules don't run against services that are gone.
if [ "$profile" != full ]; then
  listed="$(dc n8n list:workflow 2>/dev/null || true)"
  while IFS= read -r f; do
    printf '%s\n' "${workflows[@]}" | grep -qxF "$f" && continue
    id="$(grep -m1 -o '"id": "mkt[A-Za-z0-9]*"' "$f" | cut -d'"' -f4)"
    if grep -q "^$id|" <<<"$listed"; then
      echo "==> unpublish ${f#../} (not in profile $profile)"
      dc n8n unpublish:workflow --id="$id" >/dev/null </dev/null   # exec would eat the loop's stdin
    fi
  done < <(profile_workflows full)
fi

echo "==> restart n8n"
compose restart n8n >/dev/null
# Wait until n8n serves again, so a smoke test right after this doesn't hit a starting n8n.
for _ in $(seq 90); do
  [ "$(compose ps --format '{{.Health}}' n8n 2>/dev/null)" = healthy ] \
    && curl -fsS -m 5 "http://127.0.0.1:$(n8n_port)/rest/settings" >/dev/null 2>&1 && break
  sleep 2
done
echo "done. Chat: ${N8N_PUBLIC_URL:-http://localhost:$(n8n_port)/}webhook/mkt-marketing-chat/chat"
echo "      Approval form: ${N8N_PUBLIC_URL:-http://localhost:$(n8n_port)/}form/mkt-content-approval"
echo "      Control room (72): http://localhost:$(host_port 72) (or your HTTPS address for it)"
echo "      Knowledge form: ${N8N_PUBLIC_URL:-http://localhost:$(n8n_port)/}form/mkt-knowledge-add"
