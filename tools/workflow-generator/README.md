# workflow-generator

Every n8n workflow in this repo is generated. The Python here is the source; the
`NN-wf-*/workflow.json` files (and each workflow deploy's `README.md`, `.gitignore` and
`.github/workflows/ci.yml`, plus `24-wf-chat-agent/variants/hosted.json` and
`72-control-room/n8n/workflow.json`, `84-ads-sync/n8n/` and `86-flow-runner/n8n/`) are its output. **Change a workflow here, never in the n8n
editor or in the JSON**, then rebuild.

```bash
python3 tools/workflow-generator/build.py          # Python 3.10+, standard library only
for t in tools/workflow-generator/tests/*.js; do node "$t"; done   # Node 18+
git diff --stat                                    # review what changed
```

The output is deterministic: node ids are UUIDv5 from the workflow and node name, so an
unchanged definition rebuilds byte for byte. CI (`generator` job in the root
`.github/workflows/ci.yml`) runs `build.py` and fails if any committed file changes, then runs the
tests.

## Files

| File | What |
|---|---|
| `n8nlib.py` | the builder: `Workflow`, node helpers (`http`, `code`, `gateway`, `schedule`, …), fixed workflow ids (`WF_IDS`), the shared notification code. Every gateway call (`llm=True`) also sends `X-Caller: <NN> <name>` (e.g. `26 Social post writer`), so the gateway's activity log and the control room's Activity page show which workflow asked |
| `workflows.py` | 24 chat agent (+ its tool list), 25–43 |
| `workflows_p2.py` … `workflows_p10.py`, `workflows_radar.py` | 47–83, grouped by the phase they were built in |
| `workflows_ads.py` | paid ads: the daily sync + alerts workflow shipped in `84-ads-sync/n8n/`, and 41's Ads section |
| `workflows_flows.py` | 86 email flows: the 15-minute review sync + tick workflow shipped in `86-flow-runner/n8n/` |
| `workflows_client_report.py` | 85 monthly client report: sources, tables in code, the checked LLM summary |
| `build.py` | writes every deploy folder; `SLUG` maps a number to its folder name |
| `tests/*.js` | run the Code-node JavaScript as it ships (read from the built `workflow.json`) against fixed inputs, including negative cases |

## Adding a workflow

1. Write `wfNN()` returning `(Workflow, meta)`; `meta` feeds the deploy's README (`summary`,
   `depends`, `env`, `inputs`/`output` for sub-workflows, `schedule`, `test`).
2. Give it a fixed id in `n8nlib.WF_IDS` (16 characters; other workflows call it by this id).
3. Add it to an `ALL_*` list and its folder name to `SLUG` in `build.py`, then build.
4. If it needs a service outside the core install, add its folder to
   `01-marketing-stack/scripts/profiles.sh` (`GROWTH_WORKFLOWS` / `FULL_WORKFLOWS`). A chat tool
   stays in every profile and must answer "not installed" when its service's URL is empty.

## Local test harnesses (optional)

`build.py --harness DIR` also writes a webhook wrapper for every sub-workflow and a fire-now copy
of every schedule into `DIR`, for driving them over HTTP in a test n8n. Without the flag they go to
`<repo>/_dev/harness` if that folder exists (it is gitignored), otherwise they are skipped.
