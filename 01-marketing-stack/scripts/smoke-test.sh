#!/usr/bin/env bash
# Run AFTER `docker compose up -d --build` and `scripts/import-n8n.sh`.
# Checks the running system end to end. Only reads, plus one LLM call and one fact check.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# One client of several: MKT_CLIENT=<slug> or --client <slug> (its project, env file and ports).
[ "${1:-}" = --client ] && export MKT_CLIENT="${2:-}"
# shellcheck source=profiles.sh
. scripts/profiles.sh   # and client.sh: load_env, compose, host_port, n8n_port
load_env

fails=0
pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }
json() { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

echo "== Containers"
not_running=$(compose ps --format '{{.Service}} {{.State}}' | awk '$2 != "running" {print $1}')
[ -z "$not_running" ] && pass "all containers running" || fail "not running: $not_running (docker compose logs <name>)"
# "running" includes containers whose healthcheck fails; empty Health = no healthcheck.
unhealthy=$(compose ps --format '{{.Service}} {{.Health}}' | awk '$2 != "" && $2 != "healthy" {print $1 "(" $2 ")"}')
[ -z "$unhealthy" ] && pass "every healthcheck healthy" || fail "not healthy: $unhealthy (compose ps)"

echo "== Services (status page)"
status=$(curl -fsS -m 30 "http://127.0.0.1:$(host_port 22)/status" 2>/dev/null)
if [ -z "$status" ]; then
  fail "status page not reachable on :$(host_port 22)"
else
  down=$(echo "$status" | json "' '.join(s['name'] for s in d['services'] if not s['ok'])")
  [ -z "$down" ] && pass "every service healthy, including Ollama" || fail "down: $down"
fi

echo "== n8n"
curl -fsS -m 10 "http://127.0.0.1:$(n8n_port)/healthz" >/dev/null && pass "n8n up" || fail "n8n not answering on :$(n8n_port)"
# Same set import-n8n.sh imports for this profile: the NN-wf-* deploys plus workflows shipped in a
# service (72/n8n), minus those whose service is not installed (scripts/profiles.sh).
profile="$(current_profile)"
listed=$(compose exec -T n8n n8n list:workflow 2>/dev/null)
expected=0; missing=""
while IFS= read -r f; do
  expected=$((expected + 1))
  id="$(grep -m1 -o '"id": "mkt[A-Za-z0-9]*"' "$f" | cut -d'"' -f4)"
  grep -q "^$id|" <<<"$listed" || missing="$missing ${f#../}"
done < <(profile_workflows "$profile")
if [ "$expected" -gt 0 ] && [ -z "$missing" ]; then pass "$expected/$expected workflows of profile $profile imported"
else fail "profile $profile: missing${missing:- all} (run scripts/import-n8n.sh)"; fi

# The gateway and the claim checker are called from inside the stack (client.sh stack_http): a
# client publishes no host port for them. The key goes on stdin, never on a command line.
gw="$(compose ps -q llm-gateway 2>/dev/null | head -n1)"
body() { tail -n +2; }

echo "== Access control"
code=$(IK="" stack_http "$gw" POST http://llm-gateway:8000/v1/run '{"prompt":"kb_answer"}' 10 | head -n1)
[ "$code" = "401" ] && pass "gateway refuses calls without the key" || fail "gateway answered $code without the key (expected 401)"

echo "== LLM path (gateway -> Ollama)"
out=$(IK="${INTERNAL_API_KEY:-}" stack_http "$gw" POST http://llm-gateway:8000/v1/run \
  '{"prompt":"kb_answer","vars":{"question":"How long do refunds take?","context":"[1] Refunds are processed within 5 days."}}' 300 | body)
answer=$(echo "$out" | json "d['output']")
[ -n "$answer" ] && pass "model answered: ${answer:0:80}" || fail "gateway/Ollama call failed: ${out:0:200}"

echo "== Fact check (claim checker)"
out=$(IK="${INTERNAL_API_KEY:-}" stack_http "$gw" POST http://claim-checker:8000/verify \
  '{"text":"Every order ships within 3 minutes and includes a free espresso machine."}' 300 | body)
ok=$(echo "$out" | json "d['ok']")
[ "$ok" = "False" ] && pass "invented claim was flagged" || fail "claim checker did not flag an invented claim: ${out:0:200}"

echo
base="${N8N_PUBLIC_URL:-http://localhost:$(n8n_port)/}"
if [ "$fails" -gt 0 ]; then echo "$fails check(s) failed."; exit 1; fi
cat <<EOF
All checks passed. Next:
  Chat:            n8n → "24 · Marketing chat agent" → Open chat
  Approval form:   ${base}form/mkt-content-approval
  Knowledge form:  ${base}form/mkt-knowledge-add
  Rules form:      ${base}form/mkt-rules-review
EOF
