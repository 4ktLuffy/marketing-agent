#!/usr/bin/env bash
# Run AFTER `docker compose up -d --build` and `scripts/import-n8n.sh`.
# Checks the running system end to end. Only reads, plus one LLM call and one fact check.
set -uo pipefail
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

fails=0
pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }
json() { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

echo "== Containers"
not_running=$(docker compose ps --format '{{.Service}} {{.State}}' | awk '$2 != "running" {print $1}')
[ -z "$not_running" ] && pass "all containers running" || fail "not running: $not_running (docker compose logs <name>)"

echo "== Services (status page)"
status=$(curl -fsS -m 30 http://127.0.0.1:8122/status 2>/dev/null)
if [ -z "$status" ]; then
  fail "status page not reachable on :8122"
else
  down=$(echo "$status" | json "' '.join(s['name'] for s in d['services'] if not s['ok'])")
  [ -z "$down" ] && pass "every service healthy, including Ollama" || fail "down: $down"
fi

echo "== n8n"
curl -fsS -m 10 http://127.0.0.1:5678/healthz >/dev/null && pass "n8n up" || fail "n8n not answering on :5678"
count=$(docker compose exec -T n8n n8n list:workflow 2>/dev/null | grep -c '^mktWf')
expected=$(ls -d ../[0-9][0-9]-wf-* 2>/dev/null | wc -l | tr -d ' ')
[ "$count" = "$expected" ] && pass "$count/$expected workflows imported" \
  || fail "$count/$expected workflows imported (run scripts/import-n8n.sh)"

# The gateway and the claim checker need the internal key. It goes to curl on stdin (-K -),
# never on the command line where other users could read it.
keyed() { printf 'header = "X-API-Key: %s"\n' "${INTERNAL_API_KEY:-}" | curl -K - "$@"; }

echo "== Access control"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8103/v1/run -H 'content-type: application/json' -d '{"prompt":"kb_answer"}')
[ "$code" = "401" ] && pass "gateway refuses calls without the key" || fail "gateway answered $code without the key (expected 401)"

echo "== LLM path (gateway -> Ollama)"
out=$(keyed -fsS -m 300 http://127.0.0.1:8103/v1/run -H 'content-type: application/json' \
  -d '{"prompt":"kb_answer","vars":{"question":"How long do refunds take?","context":"[1] Refunds are processed within 5 days."}}' 2>/dev/null)
answer=$(echo "$out" | json "d['output']")
[ -n "$answer" ] && pass "model answered: ${answer:0:80}" || fail "gateway/Ollama call failed: ${out:0:200}"

echo "== Fact check (claim checker)"
out=$(keyed -fsS -m 300 http://127.0.0.1:8144/verify -H 'content-type: application/json' \
  -d '{"text":"Every order ships within 3 minutes and includes a free espresso machine."}' 2>/dev/null)
ok=$(echo "$out" | json "d['ok']")
[ "$ok" = "False" ] && pass "invented claim was flagged" || fail "claim checker did not flag an invented claim: ${out:0:200}"

echo
base="${N8N_PUBLIC_URL:-http://localhost:5678/}"
if [ "$fails" -gt 0 ]; then echo "$fails check(s) failed."; exit 1; fi
cat <<EOF
All checks passed. Next:
  Chat:            n8n → "24 · Marketing chat agent" → Open chat
  Approval form:   ${base}form/mkt-content-approval
  Knowledge form:  ${base}form/mkt-knowledge-add
  Rules form:      ${base}form/mkt-rules-review
EOF
