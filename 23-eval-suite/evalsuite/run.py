"""Run eval cases against a live gateway and report pass rates.

  python -m evalsuite.run                         # all cases, 3 repeats
  python -m evalsuite.run --only ad_copy --repeats 5
  python -m evalsuite.run --min-pass-rate 0.8     # exit 1 below this (for CI / cron)

A case passes a run only if the gateway answered AND every check passed. Repeats matter:
at temperature > 0 one green run says little.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

from evalsuite.checks import run_check

CASES_DIR = Path(__file__).parent.parent / "cases"


def load_cases(only: str | None) -> list[dict]:
    cases = []
    for f in sorted(CASES_DIR.glob("*.yaml")):
        for case in yaml.safe_load(f.read_text()):
            if only and only not in (case["id"], case["prompt"]):
                continue
            cases.append(case)
    return cases


def run_case(case: dict, gateway: str, services: dict) -> dict:
    started = time.monotonic()
    try:
        r = httpx.post(f"{gateway}/v1/run", json={"prompt": case["prompt"], "vars": case["vars"]}, timeout=600)
    except httpx.HTTPError as exc:
        return {"ok": False, "seconds": 0, "error": f"gateway unreachable: {exc}", "checks": []}
    took = round(time.monotonic() - started, 1)
    if r.status_code != 200:
        return {"ok": False, "seconds": took, "error": f"gateway {r.status_code}: {r.text[:200]}", "checks": []}
    body = r.json()
    results = []
    for check in case.get("checks", []):
        try:
            passed, detail = run_check(check, body["output"], case["vars"], services)
        except httpx.HTTPError as exc:
            passed, detail = False, f"service error: {exc}"
        results.append({"check": check["type"], "path": check.get("path"), "passed": passed, "detail": detail})
    return {
        "ok": all(c["passed"] for c in results),
        "seconds": took,
        "attempts": body["attempts"],
        "checks": results,
        "output": body["output"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default=os.getenv("GATEWAY_URL", "http://localhost:8103"))
    ap.add_argument("--brand", default=os.getenv("BRAND_URL", "http://localhost:8105"))
    ap.add_argument("--rules", default=os.getenv("RULES_URL", "http://localhost:8114"))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--only")
    ap.add_argument("--min-pass-rate", type=float, default=0.0)
    ap.add_argument("--out", default="results")
    a = ap.parse_args(argv)

    services = {"brand": a.brand.rstrip("/"), "rules": a.rules.rstrip("/"), "brand_summary": ""}
    try:
        services["brand_summary"] = httpx.get(f"{services['brand']}/profile/summary", timeout=10).json()["summary"]
    except (httpx.HTTPError, KeyError, ValueError):
        print("warning: brand summary unavailable; numbers_from_input will only see case vars")
    cases = load_cases(a.only)
    if not cases:
        print("no cases matched")
        return 2

    report, total_ok, total_runs = [], 0, 0
    for case in cases:
        runs = [run_case(case, a.gateway.rstrip("/"), services) for _ in range(a.repeats)]
        ok = sum(r["ok"] for r in runs)
        total_ok += ok
        total_runs += len(runs)
        print(f"{ok}/{len(runs)}  {case['id']:<34} avg {sum(r['seconds'] for r in runs)/len(runs):5.1f}s")
        for r in runs:
            if r.get("error"):
                print(f"      error: {r['error']}")
            for c in r["checks"]:
                if not c["passed"]:
                    print(f"      fail {c['check']}{' @' + c['path'] if c['path'] else ''}: {c['detail']}")
        report.append({"id": case["id"], "prompt": case["prompt"], "passed": ok, "runs": runs})

    rate = total_ok / total_runs
    print(f"\npass rate {total_ok}/{total_runs} = {rate:.0%}")
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out / f"eval-{stamp}.json").write_text(json.dumps(
        {"gateway": a.gateway, "repeats": a.repeats, "pass_rate": rate, "cases": report},
        indent=2, ensure_ascii=False))
    return 0 if rate >= a.min_pass_rate else 1


if __name__ == "__main__":
    sys.exit(main())
