"""Run one prompt (or all) against a live gateway with its example_vars.

  python scripts/try_prompt.py social_posts
  python scripts/try_prompt.py --all --gateway http://localhost:8103
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).parent.parent / "prompts"


def run(gateway: str, path: Path) -> bool:
    p = yaml.safe_load(path.read_text())
    started = time.monotonic()
    r = httpx.post(f"{gateway}/v1/run", json={"prompt": p["name"], "vars": p.get("example_vars", {})}, timeout=600)
    took = time.monotonic() - started
    ok = r.status_code == 200
    print(f"{'OK ' if ok else 'ERR'} {p['name']:<26} {took:6.1f}s  attempts={r.json().get('attempts', '-') if ok else '-'}")
    body = r.json()
    out = body.get("output") if ok else body.get("detail")
    print(json.dumps(out, indent=2, ensure_ascii=False) if not isinstance(out, str) else out)
    print("-" * 80)
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--gateway", default="http://localhost:8103")
    a = ap.parse_args()
    paths = sorted(ROOT.glob("*.yaml")) if a.all else [ROOT / f"{a.name}.yaml"]
    results = [run(a.gateway.rstrip("/"), p) for p in paths]
    sys.exit(0 if all(results) else 1)
