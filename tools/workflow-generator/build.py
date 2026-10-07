"""Write every n8n workflow deploy (workflow.json + README + CI) from the Python definitions.

    python3 build.py                  # writes into the repo root (two folders up)
    python3 build.py --harness DIR    # also writes test harness workflows into DIR

Test harnesses (webhook wrappers around sub-workflows, fire-now copies of schedules) are only
for local end-to-end testing. They go to DIR, or to <repo>/_dev/harness when that folder exists
(private, gitignored); otherwise they are skipped.
"""
import argparse
import json
import sys
from pathlib import Path

from n8nlib import WF_IDS, Workflow, call_workflow, mapper
from workflows import ALL as ALL_P1, wf24
from workflows_p2 import ALL_P2
from workflows_p3 import ALL_P3
from workflows_p5 import ALL_P5
from workflows_p4 import ALL_P4
from workflows_p6 import ALL_P6
from workflows_p7 import EXTRA_P7
from workflows_ads import EXTRA_ADS
from workflows_flows import EXTRA_FLOWS
from workflows_p8 import ALL_P8
from workflows_p9 import ALL_P9
from workflows_p10 import ALL_P10
from workflows_radar import ALL_RADAR
from workflows_client_report import ALL_CLIENT_REPORT

ALL = (ALL_P1 + ALL_P2 + ALL_P3 + ALL_P4 + ALL_P5 + ALL_P6 + ALL_P8 + ALL_P9 + ALL_RADAR + ALL_P10
       + ALL_CLIENT_REPORT)

# tools/workflow-generator/build.py -> the repo root is two folders up.
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HARNESS = ROOT / "_dev" / "harness"
SLUG = {
    24: "wf-chat-agent", 25: "wf-tool-blog-writer", 26: "wf-tool-social-writer", 27: "wf-tool-ad-copy",
    28: "wf-tool-email-writer", 29: "wf-tool-seo-brief", 30: "wf-tool-repurpose", 31: "wf-tool-research-url",
    32: "wf-tool-keyword-research", 33: "wf-tool-calendar", 34: "wf-tool-kb-answer", 35: "wf-tool-quality-gate",
    36: "wf-sched-trend-digest", 37: "wf-sched-competitor-watch", 38: "wf-approval-form",
    39: "wf-sched-publisher", 40: "wf-sched-content-planner", 41: "wf-sched-weekly-report",
    42: "wf-kb-ingest-form", 43: "wf-error-handler",
    47: "wf-tool-plan-campaign", 48: "wf-campaign-drafter", 49: "wf-revise-draft",
    50: "wf-sched-learning-review", 51: "wf-rules-review-form", 52: "wf-sched-campaign-measure",
    53: "wf-tool-campaigns", 56: "wf-sched-analytics-sync",
    57: "wf-tool-content-formats", 59: "wf-sched-winner-recycler",
    60: "wf-sched-review-replies",
    64: "wf-tool-content-engine", 65: "wf-sched-engine-drafter",
    66: "wf-sched-newsletter",
    68: "wf-sched-content-refresh", 69: "wf-sched-seo-opportunities",
    74: "wf-sched-experiment-manager", 75: "wf-sched-experiment-analysis", 76: "wf-experiments-form",
    77: "wf-tool-clips",
    81: "wf-tool-track-competitor",
    83: "wf-sched-ai-visibility",
    85: "wf-sched-client-report",
}
CI = """name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: workflow.json is valid n8n export
        run: |
          python3 - <<'EOF'
          import json
          wf = json.load(open("workflow.json"))
          assert len(wf["id"]) == 16, "n8n workflow ids are 16 chars"
          names = {n["name"] for n in wf["nodes"]}
          assert len(names) == len(wf["nodes"]), "duplicate node names"
          for src, outs in wf["connections"].items():
              assert src in names, f"connection from unknown node {src}"
              for kind in outs.values():
                  for branch in kind:
                      for c in branch:
                          assert c["node"] in names, f"connection to unknown node {c['node']}"
          print(f"ok: {wf['name']} ({len(names)} nodes)")
          EOF
"""


# Highest deploy number = total deploys (numbered folders 01..N in the repo root).
TOTAL_DEPLOYS = max(int(d.name[:2]) for d in ROOT.iterdir() if d.is_dir() and d.name[:2].isdigit())

def folder(num):
    return ROOT / f"{num}-{SLUG[num]}"


def readme(num, wf: Workflow, meta: dict) -> str:
    kind = ("sub-workflow" if "inputs" in meta else
            "scheduled workflow" if "schedule" in meta else
            "form workflow" if "form" in wf.name.lower() else "workflow")
    lines = [f"# {num} · {wf.name.split('· ')[-1]}", "",
             f"Deploy **{num} of {TOTAL_DEPLOYS}** of the marketing agent. This deploy is an n8n {kind}.", "",
             meta["summary"], "",
             "## Where to deploy", "",
             "Import it into the **n8n** of `01-marketing-stack`. The stack's import script does it for you:",
             "", "```bash", "cd ../01-marketing-stack && ./scripts/import-n8n.sh", "```", "",
             "Or by hand, from this folder:", "", "```bash",
             f"docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \\",
             f"  sh -c 'cat > /tmp/wf.json && n8n import:workflow --input=/tmp/wf.json' < workflow.json",
             f"docker compose -f ../01-marketing-stack/docker-compose.yml exec -T n8n \\",
             f"  n8n publish:workflow --id={WF_IDS[num]}",
             "```", "",
             f"Its workflow id is fixed (`{WF_IDS[num]}`), because other workflows call it by that id. "
             "Import it with the CLI rather than *Import from file* in the editor, which assigns a new id.", ""]
    if "trigger" in meta:
        lines += ["## Trigger", "", meta["trigger"], ""]
    if "schedule" in meta:
        lines += ["## Schedule", "", meta["schedule"] + ". Change it in the first node.", ""]
    if "inputs" in meta:
        lines += ["## Inputs", "", "| Input | Meaning |", "|---|---|"]
        lines += [f"| `{k}` | {v} |" for k, v in meta["inputs"].items()]
        lines += ["", f"**Returns:** {meta['output']}", "", f"**Called by:** {meta['called_by']}", ""]
    if "tools" in meta:
        lines += ["## Tools the agent can call", "", "| Tool | Deploy | What it does |", "|---|---|---|"]
        lines += [f"| `{t}` | {n}-{SLUG[n]} | {d} |" for t, n, d in meta["tools"]]
        lines += ["",
                  "## Model settings", "",
                  "- The *Model (via LLM gateway)* node is n8n's OpenAI chat model node pointed at the gateway",
                  "  (`03-llm-gateway`, `POST /v1/chat/completions`). Its credential *LLM gateway (chat)* is",
                  "  created by `01-marketing-stack/scripts/import-n8n.sh`: base URL `http://llm-gateway:8000/v1`",
                  "  (`CHAT_GATEWAY_URL` to change it), API key = `INTERNAL_API_KEY` (from `.env`, never written",
                  "  to the repo) and a custom header `X-Caller: 24 Chat agent`. Every model call of the chat then",
                  "  shows on the control room's **Activity** page as *Chat agent asked mkt-agent (local) for a chat step*.",
                  "- The gateway forwards the request unchanged (messages, tools, tool calls, streaming) to its",
                  "  provider: Ollama's OpenAI-compatible `/v1` by default, or the hosted API when the gateway runs",
                  "  with `LLM_PROVIDER=openai`. It logs metadata only, never your messages or the answers.",
                  "- Model `AGENT_MODEL` (default `mkt-agent`, built by `02-ollama-models`), read from n8n's env.",
                  "  It must be on the gateway's allowlist; the stack's compose file adds `AGENT_MODEL` to it.",
                  "- Context 16384 tokens, from the `mkt-agent` Modelfile (`num_ctx`): Ollama's `/v1` API takes no",
                  "  context size, and 2048 would silently cut off the tool definitions. Another model needs",
                  "  `num_ctx` in its own Modelfile too.",
                  "- Temperature 0.2: the agent chooses tools, it doesn't write copy.", "",
                  "## Variants (optional)", "",
                  "Both have the same workflow id, so importing one replaces `workflow.json`.", "",
                  "- `variants/direct-ollama.json`: n8n talks to Ollama directly (*Local model (Ollama)* node,",
                  "  `numCtx` 16384, credential *Ollama (local)*), as before the gateway route. The chat then does",
                  "  not show on the Activity page. `import-n8n.sh` imports it when `CHAT_PROVIDER=direct`.",
                  "- `variants/hosted.json`: a hosted OpenAI-compatible model directly (Groq",
                  "  `openai/gpt-oss-120b` by default, `reasoning_effort` low), local Ollama as fallback.",
                  "  `import-n8n.sh` imports it when `CHAT_PROVIDER=hosted` and `CHAT_API_KEY` are set in `.env`.",
                  "  Your chat messages and tool results then go to that provider; the writing tools still use",
                  "  the gateway's model. These calls bypass the gateway, so they don't show on the Activity page.", ""]
    if meta.get("env"):
        lines += ["## Configuration (env on the n8n container)", "", "| Env | Meaning |", "|---|---|"]
        lines += [f"| `{k}` | {v} |" for k, v in meta["env"].items()]
        lines += [""]
    if meta.get("setup"):
        lines += ["## Setup", "", meta["setup"], ""]
    lines += ["## Depends on", ""] + [f"- `{d}`" for d in meta["depends"] or ["nothing"]] + [""]
    lines += ["Service URLs come from env vars on the n8n container (`GATEWAY_URL`, `CALENDAR_URL`, …),",
              "which `01-marketing-stack` sets. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set so",
              "workflows can read them.", ""]
    if "test" in meta:
        lines += ["## Try it", "",
                  "In n8n, open the workflow, click **Execute workflow** and paste this as the input:", "",
                  "```json", json.dumps(meta["test"], indent=2, ensure_ascii=False), "```", ""]
    if num != 43:
        lines += ["Failures go to `43-wf-error-handler`.", ""]
    lines += ["## CI", "", "Checks that `workflow.json` parses, the id is valid, and every connection points to a real node.", ""]
    return "\n".join(lines)


def harness(num, meta) -> dict:
    """Webhook -> Execute Workflow(num) so tests can drive a sub-workflow over HTTP."""
    wf = Workflow(num, f"TEST harness {num}", error_workflow=False)
    wf.id = f"mktTest{num:02d}Harness"[:16].ljust(16, "0")
    wh = wf.add("Webhook", "n8n-nodes-base.webhook", 2.1, {
        "httpMethod": "POST", "path": f"test-{num}", "responseMode": "lastNode", "options": {},
    }, webhookId=f"test-{num}")
    fields = {k: "={{ $json.body.%s ?? '' }}" % k for k in meta["inputs"]}
    ex = call_workflow(wf, "Run", num, fields)
    wf.link(wh, ex)
    return json.loads(wf.to_json())


def webhook_copy(wf: Workflow) -> dict:
    """Same workflow, schedule trigger swapped for a webhook, so a test can fire it now."""
    data = json.loads(wf.to_json())
    data["id"] = f"mktTest{wf.num:02d}Schedul"[:16].ljust(16, "0")
    data["name"] = f"{wf.num} · TEST fire-now copy"
    data["settings"].pop("errorWorkflow", None)
    trig = next(n for n in data["nodes"] if n["type"] == "n8n-nodes-base.scheduleTrigger")
    trig.update(type="n8n-nodes-base.webhook", typeVersion=2.1, webhookId=f"fire-{wf.num}",
                parameters={"httpMethod": "POST", "path": f"fire-{wf.num}", "responseMode": "lastNode", "options": {}})
    trig["id"] = trig["id"][:-4] + "beef"
    for n in data["nodes"]:
        n["id"] = n["id"][:-4] + "beef"  # distinct node ids from the real workflow
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--harness", type=Path, default=None,
                    help="write local test harnesses here (default: <repo>/_dev/harness if it exists)")
    args = ap.parse_args(argv)
    harness_dir = args.harness or (DEFAULT_HARNESS if DEFAULT_HARNESS.is_dir() else None)
    if harness_dir:
        harness_dir.mkdir(parents=True, exist_ok=True)
    for build in ALL:
        wf, meta = build()
        d = folder(wf.num)
        (d / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        (d / "workflow.json").write_text(wf.to_json())
        (d / "README.md").write_text(readme(wf.num, wf, meta))
        (d / ".github" / "workflows" / "ci.yml").write_text(CI)
        (d / ".gitignore").write_text(".DS_Store\n*.bak.json\n")
        if harness_dir and "inputs" in meta:
            (harness_dir / f"harness-{wf.num}.json").write_text(json.dumps(harness(wf.num, meta), indent=2))
        if harness_dir and "schedule" in meta:
            (harness_dir / f"sched-{wf.num}.json").write_text(json.dumps(webhook_copy(wf), indent=2))
        print(f"wrote {d.name}: {len(wf.nodes)} nodes")
    # Chat agent variants: same id, so importing one replaces workflow.json (the gateway route).
    (folder(24) / "variants").mkdir(exist_ok=True)
    for provider, name in (("hosted", "hosted.json"), ("local", "direct-ollama.json")):
        wf, _ = wf24(provider)
        (folder(24) / "variants" / name).write_text(wf.to_json())
        print(f"wrote 24 variants/{name}")
    # Workflows that ship inside a service's repo (e.g. 72-control-room/n8n/workflow.json):
    # only the JSON is written, never that repo's README or CI.
    for build, rel in EXTRA_P7 + EXTRA_ADS + EXTRA_FLOWS:
        wf, _ = build()
        (ROOT / rel).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / rel).write_text(wf.to_json())
        print(f"wrote {rel}: {len(wf.nodes)} nodes")


if __name__ == "__main__":
    sys.exit(main())
