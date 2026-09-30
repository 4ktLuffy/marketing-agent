#!/usr/bin/env bash
# One-command install of the marketing agent on this Docker host. Safe to re-run: it keeps an
# existing .env and its secrets, rebuilds only what changed, and re-imports the workflows.
#
#   ./scripts/install.sh                                   # asks for the profile and public URL
#   ./scripts/install.sh --profile core --yes              # no questions (defaults for the rest)
#   ./scripts/install.sh --profile growth --url https://n8n.example.com/ --owner-email me@example.com
#   DOCKER_CONTEXT=my-context ./scripts/install.sh ...     # or --context my-context
#   ./scripts/install.sh --client acme --profile core --yes   # one client of several on this host
#
# Steps: .env or .env.<client> (generated secrets, mode 0600) -> preflight -> build -> up -> wait
# for health -> n8n owner account -> import workflows (profile-aware) -> smoke test -> URLs.
# Secrets are never printed: the script tells you which file holds them.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=profiles.sh
. scripts/profiles.sh

usage() {
  sed -n '2,13p' scripts/install.sh | sed 's/^# \{0,1\}//'
  cat <<'EOF'
Options:
  --profile core|growth|full   what to install (INSTALL.md, section 1)
  --url URL                    public n8n URL (webhooks, form links); default http://localhost:5678/
                               (a client: http://localhost:<its n8n port>/)
  --owner-email EMAIL          n8n owner login; default admin@example.com (a password is generated)
  --context NAME               Docker context to use (same as DOCKER_CONTEXT=NAME)
  --client SLUG                install one client of several (agency mode): compose project SLUG
                               with its own .env.SLUG, keys, volumes, network and port block
                               (INSTALL.md, "Several clients on one host"); same as MKT_CLIENT=SLUG
  --port-prefix NN             with --client: host ports 127.0.0.1:NNxx (82..99); default the next
                               free one
  --no-build                   use the images already built (marketing-agent-*); fail if one is missing
  --yes                        don't ask; use the defaults for anything not given
  -h, --help                   this help
EOF
}

profile="" url="" owner_email="" assume_yes="" port_prefix="" no_build=""
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) profile="${2:-}"; shift 2 ;;
    --profile=*) profile="${1#*=}"; shift ;;
    --url) url="${2:-}"; shift 2 ;;
    --url=*) url="${1#*=}"; shift ;;
    --owner-email) owner_email="${2:-}"; shift 2 ;;
    --owner-email=*) owner_email="${1#*=}"; shift ;;
    --context) export DOCKER_CONTEXT="${2:-}"; shift 2 ;;
    --context=*) export DOCKER_CONTEXT="${1#*=}"; shift ;;
    --client) export MKT_CLIENT="${2:-}"; shift 2 ;;
    --client=*) export MKT_CLIENT="${1#*=}"; shift ;;
    --port-prefix) port_prefix="${2:-}"; shift 2 ;;
    --port-prefix=*) port_prefix="${1#*=}"; shift ;;
    --no-build) no_build=1; shift ;;
    --yes|-y) assume_yes=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1 (see --help)"; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
die() { printf 'install: %s\n' "$*" >&2; exit 1; }
[ -z "$profile" ] || valid_profile "$profile" || die "profile must be core, growth or full (got '$profile')"
if [ -n "${MKT_CLIENT:-}" ]; then
  valid_client "$MKT_CLIENT" || die "client must be 2-30 characters: a-z first, then a-z, 0-9 or '-', not ending in '-' (got '$MKT_CLIENT')"
  if [ -n "$port_prefix" ] && ! [[ "$port_prefix" =~ ^(8[2-9]|9[0-9])$ ]]; then
    die "--port-prefix must be 82..99 (81 is the single install; got '$port_prefix')"
  fi
elif [ -n "$port_prefix" ]; then
  die "--port-prefix needs --client (the single install keeps ports 81xx and 5678)"
fi
ENV_FILE="$(env_file)"
PROJECT="$(project_name)"
[ "${INSTALL_PARSE_ONLY:-}" = 1 ] && { echo "client=${MKT_CLIENT:-} env=$ENV_FILE project=$PROJECT prefix=$port_prefix profile=$profile"; exit 0; }
ask() {  # ask VAR "question" default
  local answer
  if [ -n "$assume_yes" ] || [ ! -t 0 ]; then printf -v "$1" '%s' "$3"; return; fi
  read -r -p "$2 [$3]: " answer
  printf -v "$1" '%s' "${answer:-$3}"
}
t0=$SECONDS
lap() { printf '    (%ss)\n' "$((SECONDS - ${1:-t0}))"; }

for tool in docker openssl curl python3; do
  command -v "$tool" >/dev/null || die "$tool is not installed (INSTALL.md, Prerequisites)"
done
docker compose version >/dev/null 2>&1 || die "docker compose is missing (Docker Desktop, OrbStack, colima + docker-compose)"
docker info >/dev/null 2>&1 || die "the Docker daemon is not running${DOCKER_CONTEXT:+ (context $DOCKER_CONTEXT)}"

# ---------- .env (or .env.<client>): read, never execute, never print (load_env is in client.sh)

env_get() { (unset "$1"; load_env; printf '%s' "${!1:-}"); }
secret() { openssl rand -hex 24; }
# Set KEY=VALUE in the env file: replaces every KEY= line (the last one wins for compose), or
# appends it. The value goes through the environment, never argv, so `ps` can't show it.
env_set() {
  KEY="$1" VAL="$2" ENVF="$ENV_FILE" python3 - <<'PY'
import os
k, v, f = os.environ["KEY"], os.environ["VAL"], os.environ["ENVF"]
lines = open(f).read().splitlines()
hits = [i for i, l in enumerate(lines) if l.startswith(k + "=")]
for i in hits:
    lines[i] = f"{k}={v}"
if not hits:
    lines.append(f"{k}={v}")
open(f, "w").write("\n".join(lines) + "\n")
PY
}

step "Configuration ($ENV_FILE${MKT_CLIENT:+, client $MKT_CLIENT})"
umask 077
if [ ! -f "$ENV_FILE" ]; then
  if [ -n "${MKT_CLIENT:-}" ] && [ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")" ]; then
    die "compose project $PROJECT already has containers but $ENV_FILE is missing (scripts/uninstall.sh --client $PROJECT removes them)"
  fi
  cp .env.example "$ENV_FILE"
  echo "    created $ENV_FILE from .env.example"
  fresh=1
else
  echo "    keeping the existing $ENV_FILE"
  fresh=""
fi
chmod 600 "$ENV_FILE"

# A client's port block, n8n port and schedule offset: chosen once, kept on re-runs. Public URLs
# still at the single install's localhost defaults move to the client's ports.
if [ -n "${MKT_CLIENT:-}" ]; then
  have="$(env_get PORT_PREFIX)"
  if [ -n "$port_prefix" ] && [ -n "$have" ] && [ "$port_prefix" != "$have" ]; then
    die "$ENV_FILE already uses port prefix $have; to move it, edit PORT_PREFIX, N8N_PORT and the *_URL lines there"
  fi
  if [ -z "$have" ]; then
    if [ -z "$port_prefix" ]; then
      port_prefix="$(next_port_prefix .)" || die "all 18 client port blocks (82..99) are taken"
    else
      for f in .env.*; do
        { [ "$f" = "$ENV_FILE" ] || [ "$f" = .env.example ] || [ ! -f "$f" ]; } && continue
        grep -qx "PORT_PREFIX=$port_prefix" "$f" && die "port prefix $port_prefix is already used by $f"
      done
    fi
    env_set COMPOSE_PROJECT_NAME "$PROJECT"
    env_set MKT_CLIENT_ID "$PROJECT"   # container label com.marketing-agent.client (uninstall.sh checks it)
    env_set PORT_PREFIX "$port_prefix"
    env_set N8N_PORT "$(client_n8n_port "$port_prefix")"
    env_set LISTMONK_PORT "$(client_listmonk_port "$port_prefix")"
    env_set SCHEDULE_OFFSET_MIN "$(schedule_offset_for_prefix "$port_prefix")"
  fi
  # Services only other containers call publish no host port (docker-compose.private.yml says why).
  [ -n "$(env_get COMPOSE_FILE)" ] || env_set COMPOSE_FILE docker-compose.yml:docker-compose.private.yml
  # Files other clients must not read or change: its own brand.yaml (the base under its brand
  # edits), feed list and secrets (gsc.json), started from the examples.
  cdir="clients/$MKT_CLIENT"
  mkdir -p "$cdir/secrets"
  [ -d "$cdir/brand" ] || cp -R ../05-brand-service/config "$cdir/brand"
  [ -d "$cdir/rss" ] || cp -R ../08-rss-watcher/config "$cdir/rss"
  chmod 700 "$cdir" "$cdir/secrets"
  [ -n "$(env_get BRAND_CONFIG_DIR)" ] || env_set BRAND_CONFIG_DIR "./$cdir/brand"
  [ -n "$(env_get RSS_CONFIG_DIR)" ] || env_set RSS_CONFIG_DIR "./$cdir/rss"
  [ -n "$(env_get SECRETS_DIR)" ] || env_set SECRETS_DIR "./$cdir/secrets"
  [ "$(env_get MKT_CLIENT_ID)" = "$PROJECT" ] || env_set MKT_CLIENT_ID "$PROJECT"
  PORT_PREFIX="$(env_get PORT_PREFIX)"; N8N_PORT="$(env_get N8N_PORT)"
  for pair in N8N_PUBLIC_URL:n8n CARDS_PUBLIC_URL:17 SHORT_LINK_BASE_URL:16 VIDEO_PUBLIC_URL:71 \
              CLIPS_PUBLIC_URL:73 LISTMONK_PUBLIC_URL:listmonk; do
    key="${pair%%:*}"; nn="${pair#*:}"
    case "$nn" in
      n8n) single="http://localhost:5678/"; mine="http://localhost:$N8N_PORT/" ;;
      listmonk) single="http://localhost:9000"; mine="http://localhost:$(env_get LISTMONK_PORT)" ;;
      *) single="http://localhost:81$nn"; mine="http://localhost:$PORT_PREFIX$nn" ;;
    esac
    val="$(env_get "$key")"
    if [ -z "$val" ] || [ "$val" = "$single" ]; then env_set "$key" "$mine"; fi
  done
  echo "    client $MKT_CLIENT: ports 127.0.0.1:${PORT_PREFIX}xx, n8n on $N8N_PORT, schedules +$(env_get SCHEDULE_OFFSET_MIN) min"
fi

# Every required secret: generated when empty or still the example placeholder.
generated=0
for key in POSTGRES_PASSWORD N8N_ENCRYPTION_KEY INTERNAL_API_KEY APPROVER_KEY FORMS_PASSWORD CONTROL_PASSWORD CONTROL_ROOM_KEY FACT_OWNER_KEY MCP_TOKEN; do
  val="$(env_get "$key")"
  if [ -z "$val" ] || [[ "$val" == change-me* ]]; then
    if [ "$key" = N8N_ENCRYPTION_KEY ] && docker volume inspect "${PROJECT}_n8n_data" >/dev/null 2>&1; then
      die "N8N_ENCRYPTION_KEY is a placeholder but n8n already has data (volume ${PROJECT}_n8n_data). Put back the key it was started with; a new one would make its credentials unreadable."
    fi
    env_set "$key" "$(secret)"
    generated=$((generated + 1))
  fi
done
[ "$(env_get FORMS_USER)" ] || env_set FORMS_USER reviewer
[ "$(env_get CONTROL_USER)" ] || env_set CONTROL_USER approver
[ "$generated" -gt 0 ] && echo "    generated $generated secret(s) into $ENV_FILE (not shown)"

# Profile: flag > question (default: the installed one, else core).
installed="$(env_get COMPOSE_PROFILES)"
default_profile=core
[ -n "$installed" ] && [ -z "$fresh" ] && default_profile="$(COMPOSE_PROFILES="$installed" current_profile)"
if [ -z "$profile" ]; then
  echo "    Profiles: core (the agent: chat, writers, fact check, approval, campaigns, learning)"
  echo "              growth (+ content engine, publishing bridges, GSC/analytics sync, reviews, video, clips,"
  echo "                paid-ads reporting)"
  echo "              full (+ competitor ads, site assistant, lead hub, AI visibility)"
  ask profile "    Profile" "$default_profile"
fi
valid_profile "$profile" || die "profile must be core, growth or full (got '$profile')"

# Public n8n URL: flag > question (default: what the env file has).
current_url="$(env_get N8N_PUBLIC_URL)"
if [ -z "$url" ]; then ask url "    Public n8n URL (form links, webhooks)" "${current_url:-http://localhost:$(n8n_port)/}"; fi
[[ "$url" =~ ^https?://[^[:space:]]+$ ]] || die "the public URL must start with http:// or https:// (got '$url')"
[[ "$url" == */ ]] || url="$url/"
[ "$url" = "$current_url" ] || env_set N8N_PUBLIC_URL "$url"

# n8n owner login (created below when n8n has none yet). Password generated, kept in the env file only.
saved_email="$(env_get N8N_OWNER_EMAIL)"
if [ -n "$saved_email" ] && [ -n "$owner_email" ] && [ "$owner_email" != "$saved_email" ]; then
  die "n8n's owner is $saved_email (N8N_OWNER_EMAIL in $ENV_FILE); change it in n8n's settings, not here"
fi
[ -n "$owner_email" ] || owner_email="${saved_email:-admin@example.com}"
[[ "$owner_email" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] || die "not an email address: $owner_email"
[ "$(env_get N8N_OWNER_EMAIL)" = "$owner_email" ] || env_set N8N_OWNER_EMAIL "$owner_email"
# n8n wants 8+ characters with a number and an uppercase letter.
[ -n "$(env_get N8N_OWNER_PASSWORD)" ] || env_set N8N_OWNER_PASSWORD "$(openssl rand -hex 16)Aa1"

# Managed block: the profile, the URLs of services it does not install (empty = workflows skip
# them) and the status page's service list. Rewritten on every run; keys in it are owned by it.
load_env
empty_urls="$(profile_empty_urls "$profile")"
status="$(status_services "$profile")"
BLOCK_KEYS="COMPOSE_PROFILES STATUS_SERVICES $GROWTH_URL_VARS $FULL_URL_VARS" ENVF="$ENV_FILE" \
PROFILE="$profile" EMPTY="$empty_urls" STATUS="$status" python3 - <<'PY'
import os
f = os.environ["ENVF"]
keys = set(os.environ["BLOCK_KEYS"].split())
begin, end = "# >>> install.sh (managed", "# <<< install.sh"
out, inside = [], False
for l in open(f).read().splitlines():
    if l.startswith(begin): inside = True; continue
    if l.startswith(end): inside = False; continue
    if inside or l.split("=", 1)[0].strip() in keys: continue
    out.append(l)
while out and not out[-1].strip(): out.pop()
out += ["", f"{begin}: re-run scripts/install.sh --profile <core|growth|full> to change) >>>",
        f"COMPOSE_PROFILES={os.environ['PROFILE']}",
        "# Not installed in this profile: empty, so the workflows that use them skip.",
        *[f"{k}=" for k in os.environ["EMPTY"].split()],
        f"STATUS_SERVICES={os.environ['STATUS']}",
        f"{end} <<<"]
open(f, "w").write("\n".join(out) + "\n")
PY
chmod 600 "$ENV_FILE"
echo "    profile $profile, public URL $url, n8n owner $owner_email"
load_env
export COMPOSE_PROFILES="$profile"

# ---------- checks, build, start

step "Preflight"
./scripts/preflight.sh || die "preflight failed: fix every FAIL above, then run install.sh again"

if [ -n "$no_build" ]; then
  step "Build ($profile): skipped (--no-build)"
  missing_images=""
  while read -r image; do
    docker image inspect "$image" >/dev/null 2>&1 || missing_images="$missing_images $image"
  done < <(compose --profile "$profile" config --images)
  [ -z "$missing_images" ] || die "--no-build, but these images are missing:$missing_images"
else
  step "Build ($profile)"
  t=$SECONDS
  compose --profile "$profile" build
  lap "$t"
fi

# Services of a larger profile installed earlier: stop and remove them (their data volumes stay).
wanted="$(compose --profile "$profile" config --services)"
expected="$(wc -l <<<"$wanted" | tr -d ' ')"
extra=()
while IFS= read -r s; do
  grep -qx "$s" <<<"$wanted" || extra+=("$s")
done < <(compose --profile full config --services)
if [ ${#extra[@]} -gt 0 ] && [ -n "$(compose --profile full ps -a -q "${extra[@]}" 2>/dev/null)" ]; then
  step "Remove services outside $profile (volumes kept)"
  compose --profile full rm -s -f "${extra[@]}"
fi

step "Start"
t=$SECONDS
compose --profile "$profile" up -d
echo "    waiting for every healthcheck (n8n's first start runs migrations)..."
deadline=$((SECONDS + 420))
while :; do
  states="$(compose --profile "$profile" ps --format '{{.Service}} {{.State}} {{.Health}}')"
  waiting="$(awk '$2 != "running" || ($3 != "" && $3 != "healthy") {print $1 "(" $2 ($3 ? "/" $3 : "") ")"}' <<<"$states" | tr '\n' ' ')"
  total="$(grep -c . <<<"$states" || true)"
  [ -z "$waiting" ] && [ "$total" -ge "$expected" ] && break
  [ "$SECONDS" -lt "$deadline" ] || die "not healthy after 7 min: $waiting(docker compose logs <service>)"
  sleep 5
done
echo "    $total containers running and healthy"

# Every published port must answer on the host. colima (0.10.3, vz) sometimes keeps a stale port
# forward for a container recreated in place: healthy inside, "connection refused" on 127.0.0.1.
# Stopping it for a few seconds and starting it again restores the forward.
published() {  # "service port" per published port
  compose --profile "$profile" ps --format json | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    rows = json.loads(line)
    for r in rows if isinstance(rows, list) else [rows]:
        for p in r.get("Publishers") or []:
            if p.get("PublishedPort"):
                print(r["Service"], p["PublishedPort"])'
}
answers() { [ "$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$1/" 2>/dev/null)" != "000" ]; }
refused=()
while read -r svc port; do answers "$port" || refused+=("$svc"); done < <(published)
if [ ${#refused[@]} -gt 0 ]; then
  echo "    not reachable on the host: ${refused[*]}; restarting them once"
  compose --profile "$profile" stop "${refused[@]}" >/dev/null
  sleep 5
  compose --profile "$profile" start "${refused[@]}" >/dev/null
  sleep 10
  still=()
  while read -r svc port; do answers "$port" || still+=("$svc:$port"); done < <(published)
  [ ${#still[@]} -eq 0 ] || echo "    WARN still not reachable on 127.0.0.1: ${still[*]} (check your Docker's port forwarding)"
fi
lap "$t"

# ---------- n8n owner

step "n8n owner account"
n8n_local="http://127.0.0.1:$(n8n_port)"
# /healthz answers before n8n has registered its REST routes: wait for the settings themselves.
setup_needed=""
for _ in $(seq 90); do
  setup_needed="$(curl -fsS -m 5 "$n8n_local/rest/settings" 2>/dev/null \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); d=d.get("data",d); print(str(d["userManagement"]["showSetupOnFirstLoad"]).lower())' 2>/dev/null)" \
    && [ -n "$setup_needed" ] && break
  sleep 2
done
[ -n "$setup_needed" ] || die "n8n settings not readable on $n8n_local after 3 min (docker compose logs n8n)"
if [ "$setup_needed" = "true" ]; then
  # The body (with the password) goes to curl on stdin, never argv.
  python3 -c 'import json,os; print(json.dumps({"email":os.environ["N8N_OWNER_EMAIL"],"firstName":"Marketing","lastName":"Owner","password":os.environ["N8N_OWNER_PASSWORD"]}))' \
    | curl -fsS -m 30 -o /dev/null -H 'content-type: application/json' --data-binary @- "$n8n_local/rest/owner/setup" \
    || die "n8n refused the owner setup (see docker compose logs n8n)"
  echo "    created the owner $N8N_OWNER_EMAIL (password: N8N_OWNER_PASSWORD in $ENV_FILE)"
else
  echo "    n8n already has an owner; left as it is"
fi

# ---------- workflows, smoke test

step "Import workflows ($profile)"
t=$SECONDS
./scripts/import-n8n.sh
lap "$t"

step "Smoke test"
t=$SECONDS
smoke=0
./scripts/smoke-test.sh || smoke=$?
lap "$t"

base="${N8N_PUBLIC_URL:-http://localhost:$(n8n_port)/}"
cat <<EOF

==> Installed profile $profile${MKT_CLIENT:+ for client $MKT_CLIENT (compose project $PROJECT)} in $((SECONDS - t0)) s$([ "$smoke" = 0 ] || echo " (smoke test FAILED: see above)")

  Control room (review on your phone): http://localhost:$(host_port 72)   login: CONTROL_USER / CONTROL_PASSWORD
  n8n (editor, chat agent "24"):       ${base}                    login: N8N_OWNER_EMAIL / N8N_OWNER_PASSWORD
  Chat:                                ${base}webhook/mkt-marketing-chat/chat
  Approval form:                       ${base}form/mkt-content-approval   login: FORMS_USER / FORMS_PASSWORD
  Knowledge form:                      ${base}form/mkt-knowledge-add
  Status page:                         http://localhost:$(host_port 22)

  Every login is in $(pwd)/$ENV_FILE (mode 0600). Read it there; it is not printed here.

Next: open the control room and fill in Brand setup (Brand in the top bar; More -> Brand setup
on a phone), add your FAQs in the knowledge form, then follow PILOT.md. Publishing stays a dry
run until you turn it on for one channel.
EOF
exit "$smoke"
