"""Measure the claim checker (44) on labelled claims.

  python -m evalsuite.claims                                   # all files in cases/claims/
  python -m evalsuite.claims cases/claims/northwind_final.yaml # one file
  python -m evalsuite.claims --checker http://localhost:8144

Each claim is sent alone to POST /verify. "Caught" = an unsupported claim was flagged;
"false alarm" = a supported claim was flagged. Both matter: a checker that flags
everything catches 100% and is useless.

The labelled files describe the example brand (05's config/brand.yaml). When you change
the brand facts, write labelled claims for your own brand.
"""
import argparse
import os
import sys
from pathlib import Path

import httpx
import yaml

CLAIMS_DIR = Path(__file__).parent.parent / "cases" / "claims"


def measure(path: Path, checker: str) -> tuple[int, int, int, int]:
    caught = missed = alarms = passed = 0
    for case in yaml.safe_load(path.read_text()):
        headers = {"X-API-Key": os.environ["INTERNAL_API_KEY"]} if os.getenv("INTERNAL_API_KEY") else {}
        r = httpx.post(f"{checker}/verify", json={"text": case["claim"]}, headers=headers, timeout=600)
        r.raise_for_status()
        flagged = not r.json()["ok"]
        if case["label"] == "unsupported":
            caught += flagged
            missed += not flagged
            if not flagged:
                print(f"    missed       {case['claim']}")
        else:
            alarms += flagged
            passed += not flagged
            if flagged:
                reasons = [x for c in r.json()["claims"] for x in c["reasons"]]
                print(f"    false alarm  {case['claim']}  {reasons}")
    return caught, missed, alarms, passed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--checker", default=os.getenv("CLAIMS_URL", "http://localhost:8144"))
    a = ap.parse_args(argv)
    files = [Path(f) for f in a.files] or sorted(CLAIMS_DIR.glob("*.yaml"))
    totals = [0, 0, 0, 0]
    for f in files:
        print(f"== {f.name}")
        res = measure(f, a.checker.rstrip("/"))
        totals = [x + y for x, y in zip(totals, res)]
        c, m, fa, p = res
        print(f"   caught {c}/{c + m} invented · false alarms {fa}/{fa + p} true")
    c, m, fa, p = totals
    print(f"\nTOTAL caught {c}/{c + m} invented · false alarms {fa}/{fa + p} true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
