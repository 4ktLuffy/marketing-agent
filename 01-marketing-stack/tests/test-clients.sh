#!/usr/bin/env bash
# Unit tests for agency mode (several clients on one host): client names, port blocks, flag
# parsing of install.sh/uninstall.sh, the rendered compose ports and networks, and the cron shift.
# No containers are started; the compose part needs `docker compose` (not a running daemon).
#
#   bash tests/test-clients.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=../scripts/client.sh
. scripts/client.sh

passed=0 failed=0
ok() { passed=$((passed + 1)); }
bad() { failed=$((failed + 1)); printf 'FAIL %s\n' "$*"; }
check() { local name="$1"; shift; if "$@"; then ok; else bad "$name"; fi; }
eq() { if [ "$2" = "$3" ]; then ok; else bad "$1: expected '$3', got '$2'"; fi; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# ---------- client names
for good in acme bravo client-7 ab a-b-c; do check "valid $good" valid_client "$good"; done
for no in "" a Acme 7up -acme acme- "ac me" "acme;rm" marketing-agent abcdefghijklmnopqrstuvwxyz12345 ../x; do
  if valid_client "$no"; then bad "invalid '$no' accepted"; else ok; fi
done

# ---------- env file and project
eq "env file default" "$(MKT_CLIENT='' env_file)" .env
eq "env file client" "$(MKT_CLIENT=acme env_file)" .env.acme
eq "project default" "$(MKT_CLIENT='' project_name)" marketing-agent
eq "project client" "$(MKT_CLIENT=acme project_name)" acme
eq "host port default" "$(PORT_PREFIX='' host_port 03)" 8103
eq "host port client" "$(PORT_PREFIX=84 host_port 72)" 8472
eq "n8n port default" "$(N8N_PORT='' n8n_port)" 5678

# ---------- port blocks
eq "first prefix" "$(next_port_prefix "$tmp")" 82
printf 'X=1\nPORT_PREFIX=82\n' > "$tmp/.env.acme"
printf 'PORT_PREFIX=83\n' > "$tmp/.env.bravo"
printf 'PORT_PREFIX=82\n' > "$tmp/.env.example"   # never counts
eq "next free prefix" "$(next_port_prefix "$tmp")" 84
rm "$tmp/.env.acme"
eq "freed prefix reused" "$(next_port_prefix "$tmp")" 82
for p in $(seq 82 99); do printf 'PORT_PREFIX=%s\n' "$p" > "$tmp/.env.c$p"; done
if next_port_prefix "$tmp" >/dev/null; then bad "19th client got a prefix"; else ok; fi
rm -f "$tmp"/.env.*
eq "n8n port of 82" "$(client_n8n_port 82)" 8299
eq "listmonk port of 82" "$(client_listmonk_port 82)" 8290
eq "offset 82" "$(schedule_offset_for_prefix 82)" 10
eq "offset 85" "$(schedule_offset_for_prefix 85)" 40
eq "offset 87 wraps" "$(schedule_offset_for_prefix 87)" 0
# No client port may land on a port of the single install or on a service's port of another client.
all=""
for p in $(seq 82 99); do
  for nn in $SERVICE_PORT_NUMBERS; do all="$all $p$nn"; done
  all="$all $(client_n8n_port "$p") $(client_listmonk_port "$p")"
done
dups="$(tr ' ' '\n' <<<"$all" | grep . | sort | uniq -d | tr '\n' ' ')"
eq "18 client blocks never collide" "$dups" ""
single=" 5678 9000 11434 $(for nn in $SERVICE_PORT_NUMBERS; do printf '81%s ' "$nn"; done)"
hit=""; for x in $all; do [[ "$single" == *" $x "* ]] && hit="$hit $x"; done
eq "client blocks avoid the single install's ports" "$hit" ""

# ---------- install.sh / uninstall.sh flags (parse only: nothing runs)
parse() { INSTALL_PARSE_ONLY=1 MKT_CLIENT='' ./scripts/install.sh "$@" 2>&1; }
eq "install default" "$(parse --profile core)" "client= env=.env project=marketing-agent prefix= profile=core"
eq "install client" "$(parse --client acme --profile core --yes)" "client=acme env=.env.acme project=acme prefix= profile=core"
eq "install client=" "$(parse --client=bravo --port-prefix=85)" "client=bravo env=.env.bravo project=bravo prefix=85 profile="
eq "install env MKT_CLIENT" "$(INSTALL_PARSE_ONLY=1 MKT_CLIENT=acme ./scripts/install.sh 2>&1)" "client=acme env=.env.acme project=acme prefix= profile="
for args in "--client Acme" "--client a" "--client ../etc" "--client marketing-agent" "--port-prefix 83" \
            "--client acme --port-prefix 81" "--client acme --port-prefix 8" "--client acme --port-prefix 100" \
            "--profile tiny"; do
  # shellcheck disable=SC2086  # word splitting of $args is the point
  if parse $args >/dev/null; then bad "install accepted: $args"; else ok; fi
done
INSTALL_PARSE_ONLY=1 ./scripts/install.sh --bogus >/dev/null 2>&1; eq "unknown option exits 2" "$?" 2
un() { UNINSTALL_PARSE_ONLY=1 ./scripts/uninstall.sh "$@" 2>&1; }
eq "uninstall parse" "$(un --client acme --yes)" "client=acme keep_data= yes=1"
if un >/dev/null; then bad "uninstall without --client accepted"; else ok; fi
if un --client marketing-agent >/dev/null; then bad "uninstall of the single install accepted"; else ok; fi

# ---------- rendered compose: ports, networks, volumes
if docker compose version >/dev/null 2>&1; then
  base="$tmp/env.base"
  sed 's/=change-me.*$/=0123456789abcdef0123/' .env.example > "$base"
  render() {  # render ENVFILE PROJECT -> "published ports" | networks | volumes (json)
    docker compose -p "$2" --env-file "$1" --profile full --profile newsletter --profile ollama --profile claude --profile erp \
      config --format json 2>/dev/null
  }
  ports_of() { python3 -c 'import json,sys
d=json.load(sys.stdin)
print(" ".join(sorted(str(p["published"]) for s in d["services"].values() for p in s.get("ports", []))))'; }
  expect_default="5678 $(for nn in $SERVICE_PORT_NUMBERS; do printf '81%s ' "$nn"; done)9000 11434"
  expect_default="$(tr ' ' '\n' <<<"$expect_default" | grep . | sort | tr '\n' ' ' | sed 's/ $//')"
  eq "single install keeps today's ports" "$(render "$base" marketing-agent | ports_of)" "$expect_default"
  cp "$base" "$tmp/env.acme"; printf 'PORT_PREFIX=83\nN8N_PORT=8399\nLISTMONK_PORT=8390\nOLLAMA_PORT=8334\n' >> "$tmp/env.acme"
  acme_ports="$(render "$tmp/env.acme" acme | ports_of)"
  expect_acme="$({ printf '%s\n' 8399 8390 8334; for nn in $SERVICE_PORT_NUMBERS; do printf '83%s\n' "$nn"; done; } | sort | tr '\n' ' ' | sed 's/ $//')"
  eq "client ports follow PORT_PREFIX" "$acme_ports" "$expect_acme"
  overlap="$(comm -12 <(tr ' ' '\n' <<<"$acme_ports" | sort) <(tr ' ' '\n' <<<"$expect_default" | sort) | tr '\n' ' ')"
  eq "client and single install share no port" "$overlap" ""
  # As install.sh writes it: services only other containers call publish nothing (private.yml).
  cp "$tmp/env.acme" "$tmp/env.acme.private"
  printf 'COMPOSE_FILE=docker-compose.yml:docker-compose.private.yml\n' >> "$tmp/env.acme.private"
  public="n8n control-room status-page site-assistant link-shortener image-cards video-assembly clip-finder lead-hub listmonk ollama"
  eq "a client publishes only what a browser opens" \
    "$(render "$tmp/env.acme.private" acme | python3 -c 'import json,sys
d=json.load(sys.stdin)
print(" ".join(sorted(n for n,s in d["services"].items() if s.get("ports"))))')" \
    "$(tr ' ' '\n' <<<"$public" | sort | tr '\n' ' ' | sed 's/ $//')"
  eq "... on its own ports (= client.sh PUBLIC_PORT_NUMBERS)" "$(render "$tmp/env.acme.private" acme | ports_of)" \
    "$({ printf '%s\n' 8399 8390 8334; for nn in $PUBLIC_PORT_NUMBERS; do printf '83%s\n' "$nn"; done; } | sort | tr '\n' ' ' | sed 's/ $//')"
  undecided="$(render "$base" marketing-agent | PUBLIC="$public" python3 -c 'import json,os,sys
d=json.load(sys.stdin); closed=set()
for line in open("docker-compose.private.yml"):
    if "!reset" in line: closed.add(line.split(":")[0].strip())
pub=set(os.environ["PUBLIC"].split())
print(" ".join(sorted(n for n,s in d["services"].items() if s.get("ports") and n not in closed | pub)
               + sorted(closed - set(d["services"]))))')"
  eq "every published service is either public or closed in private.yml" "$undecided" ""
  eq "install.sh gives a client the private ports file" \
    "$(grep -c 'env_set COMPOSE_FILE docker-compose.yml:docker-compose.private.yml' scripts/install.sh)" 1
  nets="$(render "$tmp/env.acme" acme | python3 -c 'import json,sys; print(" ".join(n["name"] for n in json.load(sys.stdin)["networks"].values()))')"
  eq "client network is its own" "$nets" "acme_marketing"
  vols="$(render "$tmp/env.acme" acme | python3 -c 'import json,sys; v=json.load(sys.stdin)["volumes"].values(); print(all(x["name"].startswith("acme_") for x in v), len(v))')"
  eq "client volumes are prefixed" "${vols%% *}" "True"
  imgs="$(render "$tmp/env.acme" acme | python3 -c 'import json,sys
d=json.load(sys.stdin)
print(sorted({s["image"].split("-",2)[0]+"-"+s["image"].split("-",2)[1] for s in d["services"].values() if "build" in s}))')"
  eq "built images are shared (marketing-agent-*)" "$imgs" "['marketing-agent']"
  printf 'MKT_CLIENT_ID=acme\n' >> "$tmp/env.acme"
  labels="$(render "$tmp/env.acme" acme | python3 -c 'import json,sys
d=json.load(sys.stdin)
print(sorted({(s.get("labels") or {}).get("com.marketing-agent.client") for s in d["services"].values()}))')"
  eq "every client container labelled for uninstall.sh" "$labels" "['acme']"
  labels="$(render "$base" marketing-agent | python3 -c 'import json,sys
d=json.load(sys.stdin)
print(sorted({(s.get("labels") or {}).get("com.marketing-agent.client") for s in d["services"].values()}))')"
  eq "single install labelled single" "$labels" "['single']"
  printf 'BRAND_CONFIG_DIR=./clients/acme/brand\nSECRETS_DIR=./clients/acme/secrets\n' >> "$tmp/env.acme"
  mounts="$(render "$tmp/env.acme" acme | python3 -c 'import json,os,sys
d=json.load(sys.stdin)
out=[]
for n in ("brand-service","gsc-sync"):
    for v in d["services"][n]["volumes"]:
        if v["target"] in ("/config","/secrets"): out.append(os.path.relpath(v["source"]))
print(" ".join(out))')"
  eq "client brand and secrets dirs" "$mounts" "clients/acme/brand clients/acme/secrets"
else
  echo "skip: docker compose not installed (compose rendering tests)"
fi

# ---------- cron shift (scripts/shift-cron.py) on rules and on every shipped workflow
out="$(python3 - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("sc", "scripts/shift-cron.py")
sc = importlib.util.module_from_spec(spec); spec.loader.exec_module(sc)
cases = [("0 7 * * 1", 20, "20 7 * * 1"), ("30 8 * * 1", 40, "10 9 * * 1"),
         ("50 23 * * *", 20, "10 23 * * *"), ("0 */6 * * *", 20, "20 */6 * * *"),
         ("*/15 * * * *", 20, "5,20,35,50 * * * *"), ("0 7 * * 1", 0, "0 7 * * 1"),
         ("1,31 * * * *", 10, "1,31 * * * *"), ("30 5 * * *", 30, "0 6 * * *"),
         ("0 7 1 * *", 60, "0 7 1 * *")]
bad = [(e, o, sc.shift_cron(e, o), want) for e, o, want in cases if sc.shift_cron(e, o) != want]
print("ok" if not bad else bad)
PY
)"
eq "shift_cron cases" "$out" ok
n=0 moved=0
for f in ../[0-9][0-9]-wf-*/workflow.json ../[0-9][0-9]-*/n8n/workflow.json; do
  [ -f "$f" ] || continue
  n=$((n + 1))
  cmp -s <(python3 -m json.tool < "$f") <(python3 scripts/shift-cron.py 0 < "$f" | python3 -m json.tool) || bad "offset 0 changed $f"
  r="$(python3 - "$f" <<'PY'
import json, subprocess, sys
f = sys.argv[1]
a = json.load(open(f))
b = json.loads(subprocess.run(["python3", "scripts/shift-cron.py", "20"], stdin=open(f), capture_output=True, check=True).stdout)
crons = lambda w: [r["expression"] for n in w["nodes"] if n["type"].endswith("scheduleTrigger") for r in n["parameters"]["rule"]["interval"]]
same_rest = [n for n in a["nodes"] if not n["type"].endswith("scheduleTrigger")] == [n for n in b["nodes"] if not n["type"].endswith("scheduleTrigger")]
changed = sum(x != y for x, y in zip(crons(a), crons(b)))
print("ok" if same_rest and len(crons(a)) == len(crons(b)) and changed == len(crons(a)) else "bad", changed)
PY
)"
  [ "${r%% *}" = ok ] || bad "shift 20 on $f: $r"
  moved=$((moved + ${r##* }))
done
if [ "$n" -eq 0 ]; then
  echo "skip: no workflow deploys next to this repo (cron shift on shipped workflows)"
else
  check "workflows found ($n)" [ "$n" -gt 40 ]
  check "every schedule moved (found $moved)" [ "$moved" -ge 18 ]
fi

echo "test-clients: $passed passed, $failed failed"
[ "$failed" -eq 0 ]
