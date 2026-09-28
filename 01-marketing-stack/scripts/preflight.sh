#!/usr/bin/env bash
# Run BEFORE `docker compose up`. Checks everything that commonly breaks a first deploy.
# Prints PASS / WARN / FAIL per check; exits 1 if anything FAILs. Changes nothing.
# One client of several: MKT_CLIENT=<slug> (or --client <slug>) checks .env.<slug> and its ports.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
[ "${1:-}" = --client ] && export MKT_CLIENT="${2:-}"
# shellcheck source=client.sh
. scripts/client.sh   # load_env, compose, env_file, host_port
ENV_FILE="$(env_file)"

fails=0
pass() { printf '  PASS  %s\n' "$1"; }
warn() { printf '  WARN  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }

echo "== Docker"
if ! command -v docker >/dev/null; then
  fail "docker not installed (Docker Desktop, OrbStack or docker-ce)"
elif ! docker info >/dev/null 2>&1; then
  fail "docker is installed but the daemon is not running"
else
  pass "docker daemon running"
  docker compose version >/dev/null 2>&1 && pass "docker compose available" || fail "docker compose plugin missing"
fi

echo "== $ENV_FILE"
if [ -n "${MKT_CLIENT:-}" ] && ! valid_client "$MKT_CLIENT"; then
  fail "client '$MKT_CLIENT' is not a valid name (a-z first, then a-z, 0-9, '-')"
elif [ ! -f "$ENV_FILE" ]; then
  fail "$ENV_FILE missing: run scripts/install.sh${MKT_CLIENT:+ --client $MKT_CLIENT} (or cp .env.example .env and fill it in)"
else
  load_env
  for v in POSTGRES_PASSWORD N8N_ENCRYPTION_KEY INTERNAL_API_KEY APPROVER_KEY FORMS_PASSWORD CONTROL_PASSWORD CONTROL_ROOM_KEY; do
    val="${!v:-}"
    if [ -z "$val" ] || [[ "$val" == change-me* ]]; then
      fail "$v is empty or still a placeholder (openssl rand -hex 24)"
    elif [ "${#val}" -lt 16 ]; then
      warn "$v is shorter than 16 characters"
    else
      pass "$v set"
    fi
  done
  if [ -n "${APPROVER_KEY:-}" ] && [ "${APPROVER_KEY:-}" = "${INTERNAL_API_KEY:-}" ]; then
    fail "APPROVER_KEY must differ from INTERNAL_API_KEY (it is what stops services approving copy)"
  fi
  # The control room's key (72) only opens n8n's decision webhook; it must not double as another key.
  for other in INTERNAL_API_KEY APPROVER_KEY FORMS_PASSWORD CONTROL_PASSWORD; do
    if [ -n "${CONTROL_ROOM_KEY:-}" ] && [ "${CONTROL_ROOM_KEY:-}" = "${!other:-}" ]; then
      fail "CONTROL_ROOM_KEY must differ from $other"
    fi
  done
fi

echo "== Repositories next to this one"
missing=0
for r in $(sed -n '/^repos=(/,/^)/p' scripts/clone-all.sh | grep -oE '[0-9]{2}-[a-z0-9-]+'); do
  [ -d "../$r" ] || { fail "../$r missing (scripts/clone-all.sh <github-user>)"; missing=1; }
done
[ "$missing" = 0 ] && pass "all deploy folders present"
brand_yaml="${BRAND_CONFIG_DIR:-../05-brand-service/config}/brand.yaml"
[ -f "$brand_yaml" ] && grep -q "Northwind Roasters" "$brand_yaml" \
  && warn "${brand_yaml#../} is still the example brand (Northwind Roasters; edits in the control room's brand setup override it)"

echo "== Ollama"
url="${OLLAMA_URL:-http://host.docker.internal:11434}"
host_url="${url/host.docker.internal/localhost}"
if tags=$(curl -fsS -m 5 "$host_url/api/tags" 2>/dev/null); then
  pass "Ollama reachable at $host_url"
  models=("${AGENT_MODEL:-mkt-agent}" "${EMBED_MODEL:-qwen3-embedding:0.6b}")
  [ "${LLM_PROVIDER:-ollama}" = "ollama" ] && models+=("${WRITER_MODEL:-mkt-writer}")
  for m in "${models[@]}"; do
    echo "$tags" | grep -q "\"name\":\"$m" && pass "model $m installed" \
      || fail "model $m missing (run ../02-ollama-models/scripts/setup.sh)"
  done
  if [ "$(uname)" = "Linux" ] && [[ "$url" == *host.docker.internal* ]]; then
    warn "Linux: containers reach Ollama via the docker bridge; start Ollama with OLLAMA_HOST=0.0.0.0 (it listens on 127.0.0.1 by default)"
  fi
else
  fail "Ollama not reachable at $host_url (is it running?)"
fi

if [ "${LLM_PROVIDER:-ollama}" = "openai" ] || [ "${VERIFIER_PROVIDER:-ollama}" = "openai" ]; then
  echo "== Hosted model (LLM_PROVIDER or VERIFIER_PROVIDER = openai)"
  if [ -z "${OPENAI_BASE_URL:-}" ] || [ -z "${OPENAI_API_KEY:-}" ]; then
    fail "LLM_PROVIDER=openai needs OPENAI_BASE_URL and OPENAI_API_KEY"
  # key via stdin config, not argv, so other processes can't read it from `ps`
  elif printf 'header = "Authorization: Bearer %s"\n' "$OPENAI_API_KEY" | curl -fsS -m 10 -K - "$OPENAI_BASE_URL/models" >/dev/null 2>&1; then
    pass "hosted API reachable and the key is accepted (key not shown)"
  else
    fail "hosted API rejected the key or is unreachable ($OPENAI_BASE_URL)"
  fi
fi

echo "== Compose file"
if compose config -q >/dev/null 2>&1; then pass "docker-compose.yml valid"; else fail "docker compose config reports an error"; fi

echo "== Ports (bound to 127.0.0.1)"
busy=""
ports="$(n8n_port)"
for nn in $SERVICE_PORT_NUMBERS; do ports="$ports $(host_port "$nn")"; done
for p in $ports; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then busy="$busy $p"; fi
done
[ -z "$busy" ] && pass "all ports free" || warn "already in use:$busy (fine if it's this stack already running)"

echo
if [ "$fails" -gt 0 ]; then echo "$fails check(s) failed. Fix them before docker compose up."; exit 1; fi
echo "Ready: docker compose up -d --build"
