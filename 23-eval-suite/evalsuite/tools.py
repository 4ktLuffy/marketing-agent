"""Does the chat agent pick the right tool? Measures the agent's first decision.

  python -m evalsuite.tools                                  # model mkt-agent, 3 runs
  python -m evalsuite.tools --model qwen3:8b --runs 5
  python -m evalsuite.tools --workflow ../24-wf-chat-agent/workflow.json

It rebuilds the agent's tools (names, descriptions, $fromAI parameters) and system prompt
from 24's workflow.json and asks Ollama directly, the way n8n's agent does on its first
step. Small local models get worse at choosing as tools are added, so rerun this after
adding a tool or changing the model. Cases: cases/tools/requests.yaml.
"""
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).parent.parent
FROM_AI = re.compile(r"\$fromAI\('([^']+)',\s*`([^`]*)`,\s*'([a-z]+)'(,\s*'')?\)")


def load_agent(workflow: Path) -> tuple[str, list[dict]]:
    wf = json.loads(workflow.read_text())
    system = ""
    tools = []
    for node in wf["nodes"]:
        if node["type"].endswith(".agent"):
            system = node["parameters"]["options"]["systemMessage"].lstrip("=")
            system = re.sub(r"\{\{[^}]*\}\}", date.today().strftime("%A %d %B %Y"), system)
        if node["type"].endswith(".toolWorkflow"):
            props, required = {}, []
            for key, value in node["parameters"]["workflowInputs"]["value"].items():
                m = FROM_AI.search(value)
                if not m:
                    continue
                props[key] = {"type": m.group(3), "description": m.group(2)}
                if not m.group(4):          # no default → required (same as n8n)
                    required.append(key)
            tools.append({"type": "function", "function": {
                "name": node["name"], "description": node["parameters"]["description"],
                "parameters": {"type": "object", "properties": props, "required": required}}})
    return system, tools


def first_tool(ollama: str, model: str, system: str, tools: list, message: str) -> str:
    r = httpx.post(f"{ollama}/api/chat", json={
        "model": model, "stream": False, "tools": tools,
        "options": {"temperature": 0.2, "num_ctx": 16384},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": message}],
    }, timeout=300)
    r.raise_for_status()
    calls = r.json()["message"].get("tool_calls") or []
    return calls[0]["function"]["name"] if calls else "(no tool)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mkt-agent")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--ollama", default=os.getenv("OLLAMA_URL", "http://localhost:11434"))
    ap.add_argument("--workflow", default=str(ROOT.parent / "24-wf-chat-agent" / "workflow.json"))
    a = ap.parse_args(argv)
    system, tools = load_agent(Path(a.workflow))
    names = {t["function"]["name"] for t in tools}
    cases = [c for c in yaml.safe_load((ROOT / "cases" / "tools" / "requests.yaml").read_text()) if c["expect"] in names | {"(no tool)"}]
    print(f"model={a.model} tools={len(tools)} cases={len(cases)} runs={a.runs}")
    right = total = 0
    for c in cases:
        got = [first_tool(a.ollama.rstrip("/"), a.model, system, tools, c["say"]) for _ in range(a.runs)]
        ok = sum(g == c["expect"] for g in got)
        right += ok
        total += len(got)
        flag = "" if ok == len(got) else f"   got {got}"
        print(f"{ok}/{len(got)}  {c['expect']:<22} {c['say'][:60]}{flag}")
    print(f"\nright tool {right}/{total} = {right / total:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
