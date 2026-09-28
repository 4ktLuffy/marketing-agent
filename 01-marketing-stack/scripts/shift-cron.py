#!/usr/bin/env python3
"""Move every schedule trigger of an n8n workflow by OFFSET minutes (stdin JSON -> stdout JSON).

Used by import-n8n.sh for one client of several on one host (SCHEDULE_OFFSET_MIN), so N clients'
Monday planners don't all queue on the one Ollama at 07:00. The workflow files stay unchanged;
only the copy imported into that client's n8n moves. OFFSET is 0..59.

  "0 7 * * 1"     + 20 -> "20 7 * * 1"
  "50 23 * * *"   + 20 -> "10 23 * * *"     (no carry past midnight: the weekday stays right)
  "30 8 * * 1"    + 40 -> "10 9 * * 1"      (carry into a plain hour)
  "0 */6 * * *"   + 20 -> "20 */6 * * *"
  "*/15 * * * *"  + 20 -> "5,20,35,50 * * * *"
"""
import json
import sys


def shift_cron(expr: str, offset: int) -> str:
    offset %= 60
    parts = expr.split()
    if offset == 0 or len(parts) != 5:
        return expr
    minute, hour = parts[0], parts[1]
    if minute.isdigit():
        m = int(minute) + offset
        if m >= 60 and hour.isdigit() and int(hour) < 23:
            hour = str(int(hour) + 1)
        minute = str(m % 60)
    elif minute.startswith("*/") and minute[2:].isdigit() and 60 % int(minute[2:]) == 0:
        step = int(minute[2:])
        minute = ",".join(str(m) for m in range(offset % step, 60, step))
    else:
        return expr  # lists and ranges: left as written
    return " ".join([minute, hour] + parts[2:])


def shift_workflow(wf: dict, offset: int) -> int:
    """Shift every scheduleTrigger's cron rules in place; returns how many changed."""
    changed = 0
    for node in wf.get("nodes", []):
        if not str(node.get("type", "")).endswith("scheduleTrigger"):
            continue
        for rule in (node.get("parameters", {}).get("rule", {}) or {}).get("interval", []) or []:
            if rule.get("field") == "cronExpression" and isinstance(rule.get("expression"), str):
                new = shift_cron(rule["expression"], offset)
                if new != rule["expression"]:
                    rule["expression"] = new
                    changed += 1
    return changed


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].isdigit():
        print("usage: shift-cron.py OFFSET_MINUTES < workflow.json > shifted.json", file=sys.stderr)
        return 2
    wf = json.load(sys.stdin)
    shift_workflow(wf, int(argv[1]))
    json.dump(wf, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
