#!/usr/bin/env bash
# Remove ONE client of a multi-client host (agency mode): its containers, its volumes (ALL of that
# client's data: n8n, calendar, brand edits, knowledge base, ...), its network, .env.<client> and
# clients/<client>/. Other clients and the single install are not touched; the shared images stay.
#
#   ./scripts/uninstall.sh --client acme            # asks you to type the name to confirm
#   ./scripts/uninstall.sh --client acme --yes      # no question (scripts, tests)
#   ./scripts/uninstall.sh --client acme --keep-data   # containers and network only; volumes,
#                                                      # env file and clients/acme stay
#
# Refuses anything install.sh --client did not create: .env.<client> must name the client
# (MKT_CLIENT_ID) and every container of that compose project must carry the stack's label
# com.marketing-agent.client=<client>. --yes skips the question, never these checks.
# Export first if the client wants its data (INSTALL.md, "Backups": volumes <client>_*).
# The single install (no --client) is removed by hand: INSTALL.md, section 11.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=client.sh
. scripts/client.sh

client="" assume_yes="" keep_data=""
while [ $# -gt 0 ]; do
  case "$1" in
    --client) client="${2:-}"; shift 2 ;;
    --client=*) client="${1#*=}"; shift ;;
    --context) export DOCKER_CONTEXT="${2:-}"; shift 2 ;;
    --context=*) export DOCKER_CONTEXT="${1#*=}"; shift ;;
    --yes|-y) assume_yes=1; shift ;;
    --keep-data) keep_data=1; shift ;;
    -h|--help) sed -n '2,16p' scripts/uninstall.sh | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (see --help)"; exit 2 ;;
  esac
done
die() { printf 'uninstall: %s\n' "$*" >&2; exit 1; }
[ -n "$client" ] || die "--client <slug> is required (the single install: INSTALL.md, section 11)"
valid_client "$client" || die "not a client name: '$client'"
export MKT_CLIENT="$client"
[ "${UNINSTALL_PARSE_ONLY:-}" = 1 ] && { echo "client=$client keep_data=$keep_data yes=$assume_yes"; exit 0; }
docker info >/dev/null 2>&1 || die "the Docker daemon is not running${DOCKER_CONTEXT:+ (context $DOCKER_CONTEXT)}"

label="label=com.docker.compose.project=$client"
containers="$(docker ps -aq --filter "$label")"
volumes="$(docker volume ls -q --filter "$label")"
networks="$(docker network ls -q --filter "$label")"
env_f="$(env_file)"
if [ -z "$containers$volumes$networks" ] && [ ! -e "$env_f" ] && [ ! -e "clients/$client" ]; then
  echo "nothing to remove for client $client"; exit 0
fi
# Only a client install.sh made: its env file names it (MKT_CLIENT_ID), and every container of the
# compose project carries the stack's label for it. Anything else on this host with the same
# project name is someone else's: refuse, whatever --yes says.
grep -qx "MKT_CLIENT_ID=$client" "$env_f" 2>/dev/null \
  || die "$env_f with MKT_CLIENT_ID=$client not found: $client is not a client installed by install.sh here; nothing removed"
ours="$(docker ps -aq --filter "$label" --filter "label=com.marketing-agent.client=$client")"
if [ "$(wc -w <<<"$containers")" != "$(wc -w <<<"$ours")" ]; then
  die "compose project $client has containers without the label com.marketing-agent.client=$client (not this stack's); nothing removed"
fi
echo "Client $client: $(wc -w <<<"$containers" | tr -d ' ') containers, $(wc -w <<<"$volumes" | tr -d ' ') volumes, $(wc -w <<<"$networks" | tr -d ' ') networks${keep_data:+ (volumes and files kept)}"
if [ -z "$assume_yes" ]; then
  [ -t 0 ] || die "not a terminal: pass --yes to confirm"
  read -r -p "Type the client name to delete ${keep_data:+the containers of }$client${keep_data:-, with ALL its data}: " answer
  [ "$answer" = "$client" ] || die "not confirmed; nothing removed"
fi

# Compose first (it knows every profile's services); the label sweep catches anything compose can't
# see, e.g. when the env file is already gone.
down_args=(--remove-orphans)
[ -n "$keep_data" ] || down_args+=(-v)
if [ -f "$env_f" ]; then
  compose --profile full --profile newsletter --profile ollama down "${down_args[@]}" >/dev/null 2>&1 \
    || echo "    compose down reported an error; removing by label"
fi
left="$(docker ps -aq --filter "$label")"
[ -z "$left" ] || docker rm -f $left >/dev/null
left="$(docker network ls -q --filter "$label")"
[ -z "$left" ] || docker network rm $left >/dev/null
if [ -z "$keep_data" ]; then
  left="$(docker volume ls -q --filter "$label")"
  [ -z "$left" ] || docker volume rm $left >/dev/null
  rm -f "$env_f"
  rm -rf "clients/$client"
  rmdir clients 2>/dev/null || true
fi

# What is left of this client (must be nothing, or only its data with --keep-data).
c="$(docker ps -aq --filter "$label" | wc -l | tr -d ' ')"
v="$(docker volume ls -q --filter "$label" | wc -l | tr -d ' ')"
n="$(docker network ls -q --filter "$label" | wc -l | tr -d ' ')"
echo "Removed client $client. Left: $c containers, $v volumes, $n networks$([ -e "$env_f" ] && echo ", $env_f kept")."
if [ "$c$n" != 00 ] || { [ -z "$keep_data" ] && [ "$v" != 0 ]; }; then exit 1; fi
