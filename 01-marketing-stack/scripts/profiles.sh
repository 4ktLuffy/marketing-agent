# shellcheck shell=bash
# Install profiles, shared by install.sh, import-n8n.sh and smoke-test.sh. Source it; it runs nothing.
#
#   core    services without a `profiles:` key in docker-compose.yml (always on)
#   growth  core + services marked [growth, full]
#   full    growth + services marked [full]
#
# The profile in use is COMPOSE_PROFILES in .env (install.sh writes it; docker compose reads it too).
# With MKT_CLIENT set, the file is .env.<client> and compose runs as that project (client.sh).

# shellcheck source=client.sh
. "$(dirname "${BASH_SOURCE[0]}")/client.sh"

# Workflow deploys that only do something when a growth/full service runs. They are not imported
# on a smaller profile. Chat tools (64, 77, 81) are always imported: they answer "not installed"
# when their service's URL is empty, so the chat agent never calls a missing workflow.
GROWTH_WORKFLOWS="56-wf-sched-analytics-sync 60-wf-sched-review-replies 65-wf-sched-engine-drafter
68-wf-sched-content-refresh 69-wf-sched-seo-opportunities 74-wf-sched-experiment-manager
75-wf-sched-experiment-analysis 76-wf-experiments-form 84-ads-sync 86-flow-runner"
FULL_WORKFLOWS="83-wf-sched-ai-visibility"

# n8n (and control room) env vars that point at growth/full services. Empty on a smaller profile,
# so every workflow that reads them skips that step instead of calling a service that is not there.
GROWTH_URL_VARS="REVIEWS_URL ENGINE_URL NEWSLETTER_URL GSC_URL VOC_URL VIDEO_URL CLIPS_URL CMS_PUBLISH_URL ADS_URL FLOW_URL"
FULL_URL_VARS="AD_LIBRARY_URL LEADS_URL VISIBILITY_URL FEED_URL"

# Containers that are not polled by the status page (no /health on :8000, or the page itself).
STATUS_EXCLUDE="postgres n8n status-page listmonk listmonk-db ollama"

# core | growth | full, from COMPOSE_PROFILES (comma-separated; the largest one wins).
# Unset means the whole stack runs (every service), so it counts as full.
current_profile() {
  local p=",${COMPOSE_PROFILES-full},"
  p="${p// /}"
  if [[ "$p" == *,full,* ]]; then echo full
  elif [[ "$p" == *,growth,* ]]; then echo growth
  else echo core
  fi
}

valid_profile() { [[ "${1:-}" =~ ^(core|growth|full)$ ]]; }

# Workflow deploy folders (NN-wf-*, plus workflows shipped in a service's n8n/ folder) that belong
# to profile $1, as paths relative to 01-marketing-stack. Sub-workflows sort before their callers.
profile_workflows() {
  local profile="$1" f name skip
  shopt -s nullglob
  for f in ../[0-9][0-9]-wf-*/workflow.json ../[0-9][0-9]-*/n8n/workflow.json; do
    name="${f#../}"; name="${name%%/*}"
    skip=""
    case "$profile" in
      core) [[ " $GROWTH_WORKFLOWS $FULL_WORKFLOWS " == *[[:space:]]"$name"[[:space:]]* ]] && skip=1 ;;
      growth) [[ " $FULL_WORKFLOWS " == *[[:space:]]"$name"[[:space:]]* ]] && skip=1 ;;
    esac
    [ -z "$skip" ] && echo "$f"
  done
  shopt -u nullglob
}

# Env vars that must be empty for profile $1 (their service is not installed).
profile_empty_urls() {
  case "$1" in
    core) echo "$GROWTH_URL_VARS $FULL_URL_VARS" ;;
    growth) echo "$FULL_URL_VARS" ;;
    *) echo "" ;;
  esac
}

# Comma-separated services the status page (22) polls for profile $1: every service of that
# profile that answers /health on :8000. Needs docker compose (reads docker-compose.yml).
status_services() {
  local s out=""
  while IFS= read -r s; do
    [[ " $STATUS_EXCLUDE " == *" $s "* ]] || out="${out:+$out,}$s"
  done < <(compose --profile "$1" config --services)
  echo "$out"
}
