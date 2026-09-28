#!/usr/bin/env bash
# Cross-client leak test for agency mode (docs/agency-mode.md, Phase 1). Installs TWO clients side
# by side on this Docker host, gives each a canary fact only it has, and checks that nothing of one
# reaches the other:
#
#   a  gateway: the brand text and facts it injects into a prompt (read inside each gateway) and a
#      real writing call (/v1/run social_posts) never contain the other client's canary
#   b  claim checker: A's checker flags B's canary claim as unsupported (and passes A's own)
#   c  keys: B's calendar refuses A's APPROVER_KEY (403) and A's INTERNAL_API_KEY (401); each n8n
#      holds its own client's approver key
#   d  DNS/network: inside A, brand-service resolves only to A's container; B's container name does
#      not resolve and B's container address is not reachable
#   e  ports: no published port is shared, all are on 127.0.0.1, each client stays in its block,
#      and publishes no internal service port (docker-compose.private.yml); from inside one client
#      none of the other's internal ports answers via host.docker.internal (checked under d)
#   f  uninstall.sh --client A removes A's containers, volumes, network, env file and files; B
#      keeps running with its data
#   g  (runs first) uninstall.sh refuses a compose project install.sh did not create: a dummy
#      container under project "<A>-notours", without and with a forged .env file
#
#   ./scripts/leak-test.sh --context colima-arm                 # clients acme and bravo, core profile
#   ./scripts/leak-test.sh --context my-ctx --clients x1,x2 --keep --no-build
#   ./scripts/leak-test.sh --context my-ctx --inject-leak      # negative control: must exit 1
#
# --inject-leak recreates the bug this test exists for: B's brand-service joins A's network under
# the name brand-service (what a fixed, shared network name did). The checks must then report LEAK.
#
# Exit 0: no leak and every control passed. 1: a leak (or an install failed). 3: no leak seen, but
# a positive control failed, so a check was inconclusive. It refuses to touch a client that already
# exists (env file or containers). Secrets are read from the env files and never printed.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=client.sh
. scripts/client.sh

clients="acme,bravo" keep="" build_flag="" profile=core inject=""
while [ $# -gt 0 ]; do
  case "$1" in
    --context) export DOCKER_CONTEXT="${2:-}"; shift 2 ;;
    --context=*) export DOCKER_CONTEXT="${1#*=}"; shift ;;
    --clients) clients="${2:-}"; shift 2 ;;
    --clients=*) clients="${1#*=}"; shift ;;
    --keep) keep=1; shift ;;
    --no-build) build_flag=--no-build; shift ;;
    --inject-leak) inject=1; shift ;;
    -h|--help) awk 'NR>1 && !/^#/ {exit} NR>1' scripts/leak-test.sh | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (see --help)"; exit 2 ;;
  esac
done
die() { printf 'leak-test: %s\n' "$*" >&2; exit 1; }
[ -n "${DOCKER_CONTEXT:-}" ] || die "say which Docker host to install two stacks on: --context NAME (or DOCKER_CONTEXT)"
A="${clients%%,*}" B="${clients#*,}"
valid_client "$A" && valid_client "$B" && [ "$A" != "$B" ] || die "--clients needs two different client names (got '$clients')"
docker info >/dev/null 2>&1 || die "Docker is not running (context $DOCKER_CONTEXT)"
for c in "$A" "$B"; do
  [ ! -e ".env.$c" ] || die ".env.$c exists: $c may be a real client. Pick other names (--clients) or remove it with uninstall.sh"
  [ -z "$(docker ps -aq --filter "label=com.docker.compose.project=$c")" ] || die "compose project $c already has containers"
done

leaks=0 inconclusive=0
pass() { printf '  PASS  %s\n' "$*"; }
leak() { printf '  LEAK  %s\n' "$*"; leaks=$((leaks + 1)); }
ctl() { printf '  CTRL  %s\n' "$*"; inconclusive=$((inconclusive + 1)); }  # failed positive control
info() { printf '  INFO  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

installed=()
cleanup() {
  [ -n "$keep" ] && { echo "--keep: left installed: ${installed[*]+${installed[*]}}"; return; }
  for c in ${installed[@]+"${installed[@]}"}; do
    [ -e ".env.$c" ] || [ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$c")" ] || continue
    ./scripts/uninstall.sh --client "$c" --yes >/dev/null 2>&1 || echo "cleanup of $c failed: scripts/uninstall.sh --client $c"
  done
}
trap cleanup EXIT

val() { sed -n "s/^$2=//p" ".env.$1" | tail -n1; }       # client KEY -> value (never echoed)
port() { printf '%s%s' "$(val "$1" PORT_PREFIX)" "$2"; }   # client NN -> host port
cid() { docker ps -q --filter "label=com.docker.compose.project=$1" --filter "label=com.docker.compose.service=$2" | head -n1; }
# A client's internal services publish no host port (docker-compose.private.yml), so calls go from
# inside that client's gateway container (client.sh stack_http); keys go on stdin, never in argv.
call() {  # call CLIENT INTERNAL_KEY APPROVER_KEY|"" METHOD URL [BODY] [TIMEOUT] -> status, then body
  IK="$2" AK="$3" stack_http "$(cid "$1" llm-gateway)" "$4" "$5" "${6:-}" "${7:-20}"
}
code() { call "$@" | head -n1; }
body() { call "$@" | tail -n +2; }
sha() { printf '%s' "$1" | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest()[:16])'; }

# Canary facts: each only in its own client's brand. The tokens are invented words, so a model
# can't produce them by chance.
# (Functions, not associative arrays: macOS ships bash 3.2.)
name_of() { if [ "$1" = "$A" ]; then echo "Leaktest $A Coffee"; else echo "Leaktest $B Bikes"; fi; }
token_of() { if [ "$1" = "$A" ]; then echo Quillmore; else echo Zentrova; fi; }
canary_of() {
  if [ "$1" = "$A" ]; then echo "Our decaf is roasted in Hobart by the Quillmore roastery."
  else echo "Every Zentrova frame carries a 9-year warranty."; fi
}
other() { if [ "$1" = "$A" ]; then echo "$B"; else echo "$A"; fi; }

# ---------------------------------------------------------------- g: uninstall guard
step "g. uninstall.sh refuses a compose project that is not a client of this stack"
dummy="$A-notours"
if [ -e ".env.$dummy" ] || [ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$dummy")" ]; then
  ctl "project $dummy or .env.$dummy already exists; guard check skipped"
elif ! docker run -d --name "$dummy-probe-1" --label "com.docker.compose.project=$dummy" alpine sleep 600 >/dev/null; then
  ctl "could not start the dummy container (alpine image?)"
else
  alive() { [ -n "$(docker ps -aq --filter "name=^$dummy-probe-1\$")" ]; }
  if ./scripts/uninstall.sh --client "$dummy" --yes >/dev/null 2>&1; then leak "uninstall.sh --yes accepted foreign project $dummy"
  else pass "refused foreign project $dummy (no .env.$dummy)"; fi
  alive && pass "the foreign container is untouched" || leak "uninstall removed the foreign container"
  printf 'MKT_CLIENT_ID=%s\n' "$dummy" > ".env.$dummy"   # a forged or leftover env file
  if ./scripts/uninstall.sh --client "$dummy" --yes >/dev/null 2>&1; then leak "uninstall.sh accepted $dummy with a forged env file"
  else pass "refused $dummy with a forged .env.$dummy (container lacks com.marketing-agent.client=$dummy)"; fi
  alive && pass "the foreign container is still untouched" || leak "uninstall removed the foreign container (forged env)"
  rm -f ".env.$dummy"
  docker rm -f "$dummy-probe-1" >/dev/null
fi

# ---------------------------------------------------------------- install both
logs="${TMPDIR:-/tmp}"
t0=$SECONDS
for c in "$A" "$B"; do
  step "Install client $c (profile $profile)"
  installed+=("$c")
  t=$SECONDS
  if ! ./scripts/install.sh --client "$c" --profile "$profile" --yes $build_flag > "$logs/leak-test-install-$c.log" 2>&1; then
    tail -n 25 "$logs/leak-test-install-$c.log"
    die "install of $c failed (full log $logs/leak-test-install-$c.log)"
  fi
  grep -E '^\s+(PASS|FAIL)|client '"$c"':' "$logs/leak-test-install-$c.log" | sed 's/^/    /' | head -n 20
  info "$c installed in $((SECONDS - t)) s: ports 127.0.0.1:$(val "$c" PORT_PREFIX)xx, n8n $(val "$c" N8N_PORT), schedules +$(val "$c" SCHEDULE_OFFSET_MIN) min"
done
info "both installed in $((SECONDS - t0)) s"

step "Resources with both clients running"
for c in "$A" "$B"; do
  n="$(docker ps -q --filter "label=com.docker.compose.project=$c" | wc -l | tr -d ' ')"
  ids=()
  while IFS= read -r i; do ids+=("$i"); done < <(docker ps -q --filter "label=com.docker.compose.project=$c")
  mem="$(docker stats --no-stream --format '{{.MemUsage}}' "${ids[@]}" \
    | awk '{v=$1; u=v; gsub(/[0-9.]/,"",u); gsub(/[A-Za-z]/,"",v); if (u=="GiB") v*=1024; else if (u=="KiB") v/=1024; s+=v} END {printf "%.0f", s}')"
  info "$c: $n containers, $mem MiB (docker stats, idle after install)"
done
info "VM memory: $(docker run --rm alpine free -m 2>/dev/null | awk '/^Mem:/ {print "total " $2 " MiB, used " $3 " MiB, available " $7 " MiB"}')"

# ---------------------------------------------------------------- plant canaries
step "Plant one canary fact in each client's brand (05 PUT /brand/editable)"
planted=$SECONDS
for c in "$A" "$B"; do
  ik="$(val "$c" INTERNAL_API_KEY)"
  body="$(body "$c" "$ik" "" GET http://brand-service:8000/brand/editable \
    | NAME="$(name_of "$c")" CANARY="$(canary_of "$c")" python3 -c 'import json,os,sys
b=json.load(sys.stdin)["brand"]
print(json.dumps({"name": os.environ["NAME"], "facts": b["facts"] + [os.environ["CANARY"]]}))')"
  # (the body goes as an argument to the host-side python only; it holds no secret)
  got="$(body "$c" "$ik" "" PUT http://brand-service:8000/brand/editable "$body" | python3 -c 'import json,sys; b=json.load(sys.stdin)["brand"]; print(b["name"], "|", b["facts"][-1])')"
  [[ "$got" == "$(name_of "$c") | $(canary_of "$c")" ]] && pass "$c brand now has its canary ($got)" || die "planting in $c failed: $got"
done

if [ -n "$inject" ]; then
  step "NEGATIVE CONTROL: $B's brand-service joins ${A}_marketing as 'brand-service' (the leak must be found)"
  docker network connect --alias brand-service "${A}_marketing" "$(cid "$B" brand-service)" && info "connected"
fi

# ---------------------------------------------------------------- a: gateway injection + output
step "a. Gateway: what it injects into prompts, and a real writing call"
for c in "$A" "$B"; do
  o="$(other "$c")"
  # Read with the gateway's own code, in its container, over its own network (fresh process, no
  # cache), five times: Docker DNS rotates between containers that share a name.
  text="$(for _ in 1 2 3 4 5; do docker exec "$(cid "$c" llm-gateway)" python -c \
    'from app.main import brand_text, facts_text; print(brand_text()); print(facts_text())' 2>/dev/null; done)"
  [[ "$text" == *"$(token_of "$c")"* && "$text" == *"$(name_of "$c")"* ]] \
    && pass "control: $c gateway injects $c's name and canary ($(wc -c <<<"$text" | tr -d ' ') chars)" \
    || ctl "$c gateway text lacks its own name/canary (brand-service not read?)"
  [[ "$text" == *"$(token_of "$o")"* || "$text" == *"$(name_of "$o")"* ]] \
    && leak "$c gateway injects $o's brand or canary" || pass "$c gateway injection has nothing of $o"
done
wait_s=$((planted + 62 - SECONDS)); [ "$wait_s" -gt 0 ] && { info "waiting ${wait_s}s for the gateways' 60 s brand cache"; sleep "$wait_s"; }
for c in "$A" "$B"; do
  o="$(other "$c")"
  t=$SECONDS
  out="$(body "$c" "$(val "$c" INTERNAL_API_KEY)" "" POST http://llm-gateway:8000/v1/run \
    '{"prompt":"social_posts","vars":{"topic":"what makes us different: where our coffee is roasted, and our frame warranty","channels":"linkedin"}}' 600)"
  post="$(python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["output"]))' <<<"$out" 2>/dev/null)"
  if [ -z "$post" ]; then ctl "$c /v1/run failed: ${out:0:200}"; continue; fi
  [[ "$post" == *"$(token_of "$o")"* || "$post" == *"Hobart"* && "$o" == "$A" || "$post" == *"9-year"* && "$o" == "$B" ]] \
    && leak "$c's post mentions $o's canary: ${post:0:300}" \
    || pass "$c's post ($((SECONDS - t)) s, ${#post} chars) has no $o canary"
  [[ "$post" == *"$(token_of "$c")"* ]] && info "$c's post used its own canary fact" || info "$c's post did not use its own canary (model's choice; injection checked above)"
done

# ---------------------------------------------------------------- b: claim checker
step "b. Claim checker: the other client's canary is unsupported"
verify() {  # client text -> ok field (True/False/error)
  body "$1" "$(val "$1" INTERNAL_API_KEY)" "" POST http://claim-checker:8000/verify \
    "$(TEXT="$2" python3 -c 'import json,os; print(json.dumps({"text": os.environ["TEXT"]}))')" 600 | python3 -c 'import json,sys; print(json.load(sys.stdin)["ok"])' 2>/dev/null || echo error
}
for c in "$A" "$B"; do
  o="$(other "$c")"
  r="$(verify "$c" "$(canary_of "$c")")"
  [ "$r" = True ] && pass "control: $c's checker supports $c's own canary" || ctl "$c's checker did not support its own canary ($r)"
  r="$(verify "$c" "$(canary_of "$o")")"
  case "$r" in
    False) pass "$c's checker flags $o's canary as unsupported" ;;
    True) leak "$c's checker accepts $o's canary: $o's facts reached $c" ;;
    *) ctl "$c's checker errored on $o's canary" ;;
  esac
done

# ---------------------------------------------------------------- c: keys
step "c. Keys: approver and internal keys work only for their own client"
for c in "$A" "$B"; do
  o="$(other "$c")"
  ik="$(val "$c" INTERNAL_API_KEY)" ak="$(val "$c" APPROVER_KEY)"
  cal="http://content-calendar:8000"
  id="$(body "$c" "$ik" "" POST "$cal/items" '{"title":"leak test","channel":"linkedin","body":"leak test item","status":"draft"}' \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' 2>/dev/null)"
  [ -n "$id" ] || { ctl "could not create an item in $c's calendar"; continue; }
  [ "$(code "$c" "$ik" "" POST "$cal/items/$id/status" '{"status":"in_review"}')" = 200 ] \
    || ctl "$c item $id did not move to in_review"
  r="$(code "$c" "$ik" "$(val "$o" APPROVER_KEY)" POST "$cal/items/$id/status" '{"status":"approved"}')"
  [ "$r" = 403 ] && pass "$c's calendar refuses $o's APPROVER_KEY (403)" || leak "$c's calendar answered $r to $o's APPROVER_KEY"
  r="$(code "$c" "$(val "$o" INTERNAL_API_KEY)" "$ak" POST "$cal/items/$id/status" '{"status":"approved"}')"
  [ "$r" = 401 ] && pass "$c's calendar refuses $o's INTERNAL_API_KEY (401)" || leak "$c's calendar answered $r to $o's INTERNAL_API_KEY"
  r="$(code "$c" "$(val "$o" INTERNAL_API_KEY)" "" POST http://llm-gateway:8000/v1/run '{"prompt":"kb_answer"}')"
  [ "$r" = 401 ] && pass "$c's gateway refuses $o's INTERNAL_API_KEY (401)" || leak "$c's gateway answered $r to $o's INTERNAL_API_KEY"
  r="$(code "$c" "$ik" "$ak" POST "$cal/items/$id/status" '{"status":"approved"}')"
  [ "$r" = 200 ] && pass "control: $c's own approver key approves (200)" || ctl "$c's own approver key got $r"
  held="$(docker exec "$(cid "$c" n8n)" printenv APPROVER_KEY 2>/dev/null)"
  [ "$(sha "$held")" = "$(sha "$ak")" ] && pass "$c's n8n holds $c's approver key (sha256 compared)" \
    || { [ "$(sha "$held")" = "$(sha "$(val "$o" APPROVER_KEY)")" ] && leak "$c's n8n holds $o's approver key" || ctl "$c's n8n approver key matches neither"; }
done
[ "$(sha "$(val "$A" APPROVER_KEY)")" != "$(sha "$(val "$B" APPROVER_KEY)")" ] && pass "the two approver keys differ" || leak "both clients got the same APPROVER_KEY"

# ---------------------------------------------------------------- d: DNS and network
step "d. DNS and network from inside each client"
ips_of() {  # client service -> its container's addresses, space separated, sorted
  docker inspect "$(cid "$1" "$2")" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' | tr ' ' '\n' | grep . | sort | tr '\n' ' '
}
for c in "$A" "$B"; do
  o="$(other "$c")"
  gw="$(cid "$c" llm-gateway)"
  mine="$(ips_of "$c" brand-service)" theirs="$(ips_of "$o" brand-service)"
  seen="$(docker exec "$gw" python -c 'import socket; print(" ".join(sorted(set(a[4][0] for a in socket.getaddrinfo("brand-service", 8000, socket.AF_INET)))))' 2>/dev/null)"
  [ -n "$seen" ] && [ "$seen " = "$mine" ] && pass "in $c, brand-service -> $seen ($c's container only)" \
    || leak "in $c, brand-service -> '$seen' (theirs: $theirs; $c's: $mine)"
  name="$(docker inspect "$(cid "$o" brand-service)" --format '{{.Name}}')"; name="${name#/}"
  r="$(docker exec "$gw" python -c "import socket,sys
try: print(socket.gethostbyname('$name'))
except OSError: print('unresolved')" 2>/dev/null)"
  [ "$r" = unresolved ] && pass "in $c, $o's container name $name does not resolve" || leak "in $c, $name resolves to $r"
  ip="${theirs%% *}"
  r="$(docker exec "$gw" python -c "import socket
s=socket.socket(); s.settimeout(3)
try: s.connect(('$ip', 8000)); print('connected')
except OSError as e: print('blocked', type(e).__name__)" 2>/dev/null)"
  [[ "$r" == blocked* ]] && pass "in $c, $o's brand-service address $ip:8000 is not reachable ($r)" || leak "in $c, $o's brand-service at $ip:8000: $r"
  # Docker Desktop and colima forward 127.0.0.1 ports to host.docker.internal: only the public ones
  # may answer. Control: $o's control room (public) must connect, or the probe itself is broken.
  reach() { docker exec "$gw" python -c "import socket
s=socket.socket(); s.settimeout(3)
try: s.connect(('host.docker.internal', $1)); print('connected')
except OSError as e: print('closed', type(e).__name__)" 2>/dev/null; }
  r="$(reach "$(port "$o" 72)")"
  [ "$r" = connected ] && pass "control: in $c, $o's public control-room port answers via host.docker.internal" \
    || ctl "in $c, $o's control-room port via host.docker.internal: $r (probe cannot see host ports here)"
  opened=""
  for nn in $SERVICE_PORT_NUMBERS; do
    [[ " $PUBLIC_PORT_NUMBERS " == *" $nn "* ]] && continue
    [ "$(reach "$(port "$o" "$nn")")" = connected ] && opened="$opened $nn"
  done
  [ -z "$opened" ] && pass "in $c, none of $o's internal service ports is reachable via host.docker.internal" \
    || leak "in $c, $o's internal ports reachable via host.docker.internal:$opened"
done

# ---------------------------------------------------------------- e: ports
step "e. Published ports"
published() {  # client -> "ip:port" per published port
  docker ps --filter "label=com.docker.compose.project=$1" --format '{{.Ports}}' | tr ',' '\n' \
    | sed -n 's/^ *\([0-9.]*\):\([0-9]*\)->.*/\1:\2/p' | sort -u
}
pa="$(published "$A")" pb="$(published "$B")"
shared="$(comm -12 <(cut -d: -f2 <<<"$pa" | sort) <(cut -d: -f2 <<<"$pb" | sort) | tr '\n' ' ')"
[ -z "$shared" ] && pass "$(grep -c . <<<"$pa") + $(grep -c . <<<"$pb") published ports, none shared" || leak "ports shared: $shared"
wild="$(printf '%s\n%s\n' "$pa" "$pb" | grep -v '^127\.0\.0\.1:' | tr '\n' ' ')"
[ -z "$wild" ] && pass "every port bound to 127.0.0.1" || leak "not on 127.0.0.1: $wild"
for c in "$A" "$B"; do
  p="$(val "$c" PORT_PREFIX)" n="$(val "$c" N8N_PORT)"
  out="$(published "$c" | cut -d: -f2 | grep -v -E "^(${p}[0-9]{2}|${n})\$" | tr '\n' ' ')"
  [ -z "$out" ] && pass "$c's ports are all ${p}xx or its n8n $n" || leak "$c publishes outside its block: $out"
  internal=""
  for port_ in $(published "$c" | cut -d: -f2); do
    nn="${port_#"$p"}"
    [ "$nn" != "$port_" ] && [[ " $SERVICE_PORT_NUMBERS " == *" $nn "* && " $PUBLIC_PORT_NUMBERS " != *" $nn "* ]] \
      && internal="$internal $port_"
  done
  [ -z "$internal" ] && pass "$c publishes no internal service port (only n8n, control room, status, media, public endpoints)" \
    || leak "$c publishes internal service ports: $internal"
  name="$(body "$c" "$(val "$c" INTERNAL_API_KEY)" "" GET http://brand-service:8000/profile | python3 -c 'import json,sys; print(json.load(sys.stdin).get("name"))' 2>/dev/null)"
  [ "$name" = "$(name_of "$c")" ] && pass "control: $c's own brand-service serves $c's brand" || ctl "$c's brand-service serves '$name', not $c's brand"
done

# ---------------------------------------------------------------- f: uninstall A
[ -z "$inject" ] || docker network disconnect "${A}_marketing" "$(cid "$B" brand-service)" 2>/dev/null
step "f. uninstall.sh --client $A; $B must stay intact"
t=$SECONDS
./scripts/uninstall.sh --client "$A" --yes | sed 's/^/    /'
info "uninstall took $((SECONDS - t)) s"
lbl="label=com.docker.compose.project=$A"
left="$(docker ps -aq --filter "$lbl" | wc -l | tr -d ' ')/$(docker volume ls -q --filter "$lbl" | wc -l | tr -d ' ')/$(docker network ls -q --filter "$lbl" | wc -l | tr -d ' ')"
[ "$left" = 0/0/0 ] && pass "$A: 0 containers, 0 volumes, 0 networks left" || leak "$A left behind (containers/volumes/networks): $left"
[ ! -e ".env.$A" ] && [ ! -e "clients/$A" ] && pass ".env.$A and clients/$A removed" || leak ".env.$A or clients/$A still there"
states="$(MKT_CLIENT="$B" compose ps --format '{{.Service}} {{.State}} {{.Health}}')"
bad="$(awk '$2 != "running" || ($3 != "" && $3 != "healthy") {print $1}' <<<"$states" | tr '\n' ' ')"
[ -z "$bad" ] && pass "$B: all $(grep -c . <<<"$states") containers still running and healthy" || leak "$B damaged by $A's uninstall: $bad"
facts="$(body "$B" "$(val "$B" INTERNAL_API_KEY)" "" GET http://brand-service:8000/facts)"
[[ "$facts" == *"$(token_of "$B")"* ]] && pass "$B's brand data (volume) intact: canary still there" || leak "$B lost its brand data"
[ "$(docker volume ls -q --filter "label=com.docker.compose.project=$B" | wc -l | tr -d ' ')" -gt 0 ] && pass "$B's volumes still there"

step "Result"
echo "  leaks: $leaks, failed controls: $inconclusive, total $((SECONDS - t0)) s"
[ "$leaks" -gt 0 ] && exit 1
[ "$inconclusive" -gt 0 ] && exit 3
exit 0
