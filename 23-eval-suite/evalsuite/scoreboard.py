"""Eval scoreboard: first-seen (held-out) vs after-fix numbers for every task-bridge set, from the
result JSON files the runner writes. Honest by construction: first-seen numbers come from the run
made before any fix used that set.

    python -m evalsuite.scoreboard --first 'v2=task-bridge-zero-model-heldout-v2-*' ... \
        --latest-prefix final- --out ../docs/eval-scoreboard.md
"""
import argparse
import glob
import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent.parent / "results"


def totals(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    t = d["summary"]["totals"]
    return {"expected": t["expected"], "hits": t["hits"], "fw": t["false_warnings"],
            "mnf": t["must_not_flag_violations"], "blocked": t["blocked_right"], "drafts": t["drafts"],
            "companies": [c.get("type") or c.get("company") for c in d.get("companies", [])]}


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "n/a"


def row(name, first, latest):
    f = f"{first['hits']}/{first['expected']} ({pct(first['hits'], first['expected'])})" if first else "–"
    l = f"{latest['hits']}/{latest['expected']} ({pct(latest['hits'], latest['expected'])})" if latest else "–"
    ff = f"{first['fw']} / {first['mnf']}" if first else "–"
    lf = f"{latest['fw']} / {latest['mnf']}" if latest else "–"
    kinds = ", ".join(str(k) for k in (latest or first)["companies"][:5])
    return f"| {name} | {kinds} | {f} | {ff} | {l} | {lf} |"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", nargs="*", default=[], help="set=glob of its first-seen result JSON")
    ap.add_argument("--latest-prefix", default="final-")
    ap.add_argument("--sets", nargs="*", default=[])
    ap.add_argument("--out", default="-")
    a = ap.parse_args(argv)
    first = {}
    for spec in a.first:
        name, pat = spec.split("=", 1)
        hits = sorted(glob.glob(str(RESULTS / (pat if pat.endswith(".json") else pat + ".json"))))
        if hits:
            first[name] = totals(Path(hits[0]))
    names = a.sets or sorted(set(first) | {p.stem[len(a.latest_prefix):] for p in RESULTS.glob(a.latest_prefix + "*.json")})
    lines = ["| Set | Businesses | First seen: traps caught | First seen: false alarms / truthful blocked | "
             "After fixes: traps caught | After fixes: false alarms / truthful blocked |",
             "|---|---|---|---|---|---|"]
    ft = {"hits": 0, "expected": 0, "fw": 0, "mnf": 0}
    for n in names:
        lp = RESULTS / f"{a.latest_prefix}{n}.json"
        latest = totals(lp) if lp.exists() else None
        lines.append(row(n, first.get(n), latest))
        if n in first:
            for k in ft:
                ft[k] += first[n][k]
    lines.append(f"| **first-seen total** | | **{ft['hits']}/{ft['expected']} ({pct(ft['hits'], ft['expected'])})** | "
                 f"**{ft['fw']} / {ft['mnf']}** | | |")
    text = "\n".join(lines) + "\n"
    if a.out == "-":
        print(text)
    else:
        Path(a.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
