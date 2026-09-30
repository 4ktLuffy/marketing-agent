# shellcheck shell=bash
# One install or several clients on one host (docs/agency-mode.md, Phase 1). Source it; it runs
# nothing. Shared by install.sh, uninstall.sh, preflight.sh, import-n8n.sh, smoke-test.sh and
# leak-test.sh.
#
#   MKT_CLIENT unset  the single install: compose project "marketing-agent", file .env, ports
#                     127.0.0.1:81NN and 5678 (exactly as before agency mode).
#   MKT_CLIENT=acme   compose project "acme", file .env.acme, ports ${PORT_PREFIX}NN (e.g. 82NN),
#                     n8n on ${N8N_PORT} (e.g. 8299), schedules shifted by SCHEDULE_OFFSET_MIN.
#
# Isolation comes from the compose project: its own network (<project>_marketing, so a service
# name only resolves inside that client), its own volumes, containers and keys. Ollama on the host
# is the only thing the clients share.

# Lowercase letter first, then letters, digits or '-'; 2-30 characters. Used as the compose project
# name, so volumes are <slug>_pg_data etc.
valid_client() {
  [[ "${1:-}" =~ ^[a-z][a-z0-9-]{1,29}$ ]] && [ "$1" != marketing-agent ] && [[ "$1" != *- ]]
}

env_file() { if [ -n "${MKT_CLIENT:-}" ]; then echo ".env.$MKT_CLIENT"; else echo .env; fi; }
project_name() { echo "${MKT_CLIENT:-marketing-agent}"; }

# docker compose for this install: the client's project and env file, or the defaults.
compose() {
  if [ -n "${MKT_CLIENT:-}" ]; then
    docker compose -p "$MKT_CLIENT" --env-file ".env.$MKT_CLIENT" "$@"
  else
    docker compose "$@"
  fi
}

# Read the env file like docker compose does (KEY=VALUE, values may contain spaces; later lines
# win) and export it. Never executes it, never prints it.
load_env() {
  local f line key val
  f="$(env_file)"
  [ -f "$f" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*# || "$line" != *=* ]] && continue
    key="${line%%=*}"; val="${line#*=}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] && export "$key=$val"
  done < "$f"
}

# Host port of service NN (the deploy number: 03 gateway, 05 brand, 72 control room, ...).
host_port() { printf '%s%s\n' "${PORT_PREFIX:-81}" "$1"; }
n8n_port() { echo "${N8N_PORT:-5678}"; }

# stack_http CONTAINER METHOD URL [BODY] [TIMEOUT_S]: one HTTP call made from inside CONTAINER, on
# the stack's own network, so it works for a client whose internal services publish no host port
# (docker-compose.private.yml). URL uses service names (http://llm-gateway:8000/...). IK and AK in
# the environment become X-API-Key / X-Approver-Key; they travel on stdin, never in argv. Prints
# the status code on the first line (0 = no answer), then the body.
# shellcheck disable=SC2016  # the Python below is single-quoted on purpose
STACK_HTTP_PY='import json, sys, urllib.request, urllib.error
s = json.loads(sys.stdin.readline())
h = {"content-type": "application/json"}
h.update({k: v for k, v in (("X-API-Key", s["ik"]), ("X-Approver-Key", s["ak"])) if v})
req = urllib.request.Request(s["url"], method=s["method"], headers=h, data=s["body"].encode() if s["body"] else None)
try:
    with urllib.request.urlopen(req, timeout=s["timeout"]) as r:
        code, body = r.status, r.read()
except urllib.error.HTTPError as e:
    code, body = e.code, e.read()
except Exception as e:
    code, body = 0, str(e).encode()
sys.stdout.write(f"{code}\n"); sys.stdout.flush(); sys.stdout.buffer.write(body)'
stack_http() {
  local ctr="$1"
  [ -n "$ctr" ] || { printf '0\nno container\n'; return 0; }
  IK="${IK:-}" AK="${AK:-}" M="$2" U="$3" B="${4:-}" T="${5:-20}" python3 -c 'import json, os
e = os.environ
print(json.dumps({"ik": e["IK"], "ak": e["AK"], "method": e["M"], "url": e["U"], "body": e["B"], "timeout": float(e["T"])}))' \
    | docker exec -i "$ctr" python -c "$STACK_HTTP_PY"
}

# Every NN published as ${PORT_PREFIX}NN in docker-compose.yml (core, growth and full).
# shellcheck disable=SC2034  # read by preflight.sh and leak-test.sh
SERVICE_PORT_NUMBERS="03 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 44 45 46 47 48 54 55 58 61 62 63 67 70 71 72 73 78 79 80 82 84 86 87 88 89"
# Of those, the ones a client still publishes (docker-compose.private.yml closes the rest): short
# links, cards, status page, video, control room, clips, site assistant, lead webhooks.
# shellcheck disable=SC2034  # read by leak-test.sh
PUBLIC_PORT_NUMBERS="16 17 22 71 72 73 79 80"

# Port prefix of a new client: the lowest of 82..99 no other .env.* file in $1 (default .) uses.
# 81 is the single install. Prints nothing and fails when all 18 are taken.
next_port_prefix() {
  local dir="${1:-.}" used="" f p
  shopt -s nullglob
  for f in "$dir"/.env.*; do
    [ "${f##*/}" = .env.example ] && continue
    p="$(sed -n 's/^PORT_PREFIX=\([0-9][0-9]\)[[:space:]]*$/\1/p' "$f" | tail -n1)"
    [ -n "$p" ] && used="$used $p"
  done
  shopt -u nullglob
  for p in $(seq 82 99); do
    [[ " $used " == *" $p "* ]] || { echo "$p"; return 0; }
  done
  return 1
}

# Client ports derived from the prefix: n8n on <prefix>99, Listmonk (opt-in) on <prefix>90. No
# service number uses 90 or 99, and 82..99 never produce 5678, 9000 or 11434.
client_n8n_port() { echo "${1}99"; }
client_listmonk_port() { echo "${1}90"; }

# Minutes every schedule of a client moves by, so N planners don't queue on one Ollama at once:
# 10 minutes per client (prefix 82 -> 10, 83 -> 20, ..., 87 -> 0, 88 -> 10 ...).
schedule_offset_for_prefix() { echo $(( (($1 - 81) * 10) % 60 )); }
