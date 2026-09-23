#!/usr/bin/env bash
# Clone every deploy repo next to this one, so docker compose can build them.
#   ./scripts/clone-all.sh <github-user-or-org> [repo-prefix]
# Repos are expected to be named like the folders (e.g. 03-llm-gateway), optionally
# with a prefix: ./scripts/clone-all.sh me mkt-   ->  me/mkt-03-llm-gateway
set -euo pipefail
owner="${1:?usage: clone-all.sh <github-user> [prefix]}"
prefix="${2:-}"
cd "$(dirname "$0")/../.." || exit 1

repos=(
  02-ollama-models 03-llm-gateway 04-prompt-library 05-brand-service 06-knowledge-base
  07-page-extractor 08-rss-watcher 09-change-monitor 10-keyword-suggest 11-social-listening
  12-seo-auditor 13-readability 14-platform-rules 15-utm-builder 16-link-shortener
  17-image-cards 18-email-renderer 19-content-calendar 20-analytics-ingest 21-report-builder
  22-status-page 23-eval-suite 24-wf-chat-agent 25-wf-tool-blog-writer 26-wf-tool-social-writer
  27-wf-tool-ad-copy 28-wf-tool-email-writer 29-wf-tool-seo-brief 30-wf-tool-repurpose
  31-wf-tool-research-url 32-wf-tool-keyword-research 33-wf-tool-calendar 34-wf-tool-kb-answer
  35-wf-tool-quality-gate 36-wf-sched-trend-digest 37-wf-sched-competitor-watch
  38-wf-approval-form 39-wf-sched-publisher 40-wf-sched-content-planner
  41-wf-sched-weekly-report 42-wf-kb-ingest-form 43-wf-error-handler 44-claim-checker
  45-campaign-service 46-learning-service 47-wf-tool-plan-campaign 48-wf-campaign-drafter
  49-wf-revise-draft 50-wf-sched-learning-review 51-wf-rules-review-form
  52-wf-sched-campaign-measure 53-wf-tool-campaigns
)
for r in "${repos[@]}"; do
  if [ -d "$r" ]; then echo "skip $r (exists)"; continue; fi
  git clone --depth 1 "https://github.com/$owner/$prefix$r.git" "$r"
done
