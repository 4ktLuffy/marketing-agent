"""Do customers' own words (70 customer-language) make posts sound less generic?

  python -m evalsuite.voc_ab --setup                  # import the sample, mine, then run
  python -m evalsuite.voc_ab                          # reuse what 70 already mined
  python -m evalsuite.voc_ab --only 3                 # first 3 topics (smoke run)
  python -m evalsuite.voc_ab --judge-gateway https://other-gateway   # judge on another model
  python -m evalsuite.voc_ab --cases cases/voc/voc_ab_v2.yaml           # the second, held-out topic set
  python -m evalsuite.voc_ab --rejudge results/voc_ab-X.json --judge-gateway URL   # same posts, other judge
  python -m evalsuite.voc_ab --no-open-with           # B = customer_phrases only (the first runs)

Needs the gateway (03), customer-language (70) and the brand service (05). With
INTERNAL_API_KEY set in the environment it is sent as X-API-Key (--setup writes to 70).

1. (--setup) Import cases' `samples` into 70 and POST /mine (product names ignored).
2. For every held-out topic, write the post twice with `social_posts`:
   A = no customer language. B = 70 GET /relevant?topic=..&k=8 `customer_phrases` (what the
   social writer passes) AND `open_with`: one phrase picked in code (pick_open_with: the first
   /relevant item, 70's hybrid ranking, that is short, has no sentence punctuation and isn't
   already in the topic/brand/facts). Code then checks that the post's first sentence contains
   it (opens_with: case-insensitive, whitespace-normalized, whole words); on a miss it calls
   once more with `open_with_feedback`. The enforcement rate is reported (first try / after
   the retry). Brand, facts, rules and temperature are identical on both sides.
3. Blind pairwise judge (04 `voc_judge`): it sees short customer quotes relevant to the topic
   (70 /relevant, quotes only) and the pair in a random order, then swapped. A side wins only
   when both orders pick it; otherwise it's a tie.
4. Objective metrics, no LLM: does the post contain a customer phrase word for word (the
   phrases given to B, and any mined phrase), generic "AI" words (the voice A/B list),
   length, quotation marks (customer words must not be presented as a quote), brand errors.
   A phrase that already appears in the topic, the brand summary or the approved facts is not
   counted: the A side sees those too, so a hit would say nothing about customer language.
"""
import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import httpx
import yaml

from evalsuite.voice_ab import hits, post_text, verdict

ROOT = Path(__file__).parent.parent
CASES = ROOT / "cases" / "voc" / "voc_ab.yaml"
VOICE_CASES = ROOT / "cases" / "voice" / "voice_ab.yaml"
RESULTS = ROOT / "results"
QUOTE_CHARS = "\"“”„«»"


# ---------- objective metrics (pure, unit-tested) ----------

def norm(text: str) -> str:
    return " ".join(text.replace("’", "'").replace("‘", "'").split()).casefold()


def has_phrase(text: str, phrase: str) -> bool:
    """Word for word, case-insensitive, on word boundaries (same rule as 70's headline check)."""
    p = norm(phrase)
    return bool(p) and re.search(r"(?<![a-z0-9'])" + re.escape(p) + r"(?![a-z0-9'])", norm(text)) is not None


def countable(phrases: list[str], exclude_text: str) -> list[str]:
    """Phrases that say something about customer language: not already in the topic/brand/facts."""
    return [p for p in dict.fromkeys(phrases) if not has_phrase(exclude_text, p)]


def metrics(text: str, given: list[str], bank: list[str], generic: list[str], open_with: str | None = None) -> dict:
    return {
        "opens_with_phrase": bool(open_with) and opens_with(text, open_with),
        "bolted_on": bool(open_with) and bolted_on(text, open_with),
        "given_hits": [p for p in given if has_phrase(text, p)],
        "bank_hits": [p for p in bank if has_phrase(text, p)],
        "generic_hits": hits(text, generic),
        "chars": len(text),
        "quotation_marks": sum(text.count(c) for c in QUOTE_CHARS),
    }


def totals(rows: list[dict], side: str) -> dict:
    ms = [r[side]["metrics"] for r in rows]
    n = len(ms)
    return {
        "posts": n,
        "phrase_in_first_sentence": f"{sum(bool(m.get('opens_with_phrase')) for m in ms)}/{n}",
        "phrase_bolted_on": f"{sum(bool(m.get('bolted_on')) for m in ms)}/{n}",
        "with_given_phrase": f"{sum(bool(m['given_hits']) for m in ms)}/{n}",
        "with_any_customer_phrase": f"{sum(bool(m['bank_hits']) for m in ms)}/{n}",
        "customer_phrase_hits": sum(len(m["bank_hits"]) for m in ms),
        "generic_ai_word_hits": sum(len(m["generic_hits"]) for m in ms),
        "posts_with_quotation_marks": sum(m["quotation_marks"] > 0 for m in ms),
        "brand_errors": sum(sum(v["severity"] == "error" for v in r[side]["check"]) for r in rows),
        "avg_chars": round(sum(m["chars"] for m in ms) / n) if n else 0,
    }


# ---------- open_with: pick the phrase in code, check it in code (pure, unit-tested) ----------

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
INNER_PUNCT_RE = re.compile(r"[.!?;:…]")
EDGE_PUNCT = " \t\n.,!?;:…\"'“”‘’()[]-–—"


def first_sentence(text: str) -> str:
    """The text up to the first sentence end (. ! ? … followed by a space) or line break."""
    for part in SENTENCE_SPLIT_RE.split(text.strip()):
        if part.strip():
            return part.strip()
    return ""


def opens_with(text: str, phrase: str) -> bool:
    return has_phrase(first_sentence(text), phrase)


def pick_open_with(items: list[dict], exclude_text: str, max_words: int = 8) -> str | None:
    """First /relevant item a sentence can contain: short, no sentence punctuation inside, and
    not already in the topic/brand/facts (the A side sees those, so it would prove nothing)."""
    for it in items:
        phrase = it["text"].strip(EDGE_PUNCT)
        if (phrase and len(phrase.split()) <= max_words and not INNER_PUNCT_RE.search(phrase)
                and not any(c in phrase for c in QUOTE_CHARS) and countable([phrase], exclude_text)):
            return phrase
    return None


def bolted_on(text: str, phrase: str) -> bool:
    """The check is met by pasting the phrase as a label, not a sentence: the post starts with it
    and it is lowercase at the very start, or followed by | : – — or by a new capitalized
    sentence/emoji with no punctuation ("box arrived late We're sorry..."). A phrase that
    ends its own sentence with a period ("Coarse grind for cold brew.") is NOT counted, so
    this undercounts fragments."""
    t, pn = re.sub(r"^[\s\u2600-\u27bf\U0001F000-\U0001FAFF\ufe0f]+", "", text), norm(phrase)
    if not pn or not norm(t).startswith(pn):
        return False
    if t[:1].islower():
        return True
    rest = " ".join(t.replace("’", "'").split())[len(pn):]
    return bool(re.match(r"\s*[|:–—]|\s+(?:[A-Z]|[^\w\s,.!?'])", rest))


def open_with_feedback(phrase: str, first: str) -> str:
    return (f'Your previous answer broke that rule: its first sentence was "{first}". '
            f'Write the post again so that its first sentence contains "{phrase}" word for word.')


# ---------- live run ----------

class Live:
    def __init__(self, gateway: str, voc: str, brand: str):
        key = os.getenv("INTERNAL_API_KEY")
        self.h = {"X-API-Key": key} if key else {}
        self.gateway, self.voc, self.brand = gateway, voc, brand
        self.http = httpx.Client(timeout=1200)

    def run(self, prompt: str, vars_: dict) -> dict:
        r = self.http.post(f"{self.gateway}/v1/run", json={"prompt": prompt, "vars": vars_}, headers=self.h)
        if r.status_code != 200:
            raise RuntimeError(f"{prompt}: {r.status_code} {r.text[:300]}")
        return r.json()

    def get(self, base: str, path: str, **params):
        return self.http.get(f"{base}{path}", params=params).raise_for_status().json()

    def post(self, base: str, path: str, body):
        r = self.http.post(f"{base}{path}", json=body, headers=self.h)
        if r.status_code != 200:
            raise RuntimeError(f"POST {path}: {r.status_code} {r.text[:300]}")
        return r.json()

    def check(self, text: str, channel: str) -> list[dict]:
        r = self.http.post(f"{self.brand}/check", json={"text": text, "channel": channel})
        return r.json().get("violations", []) if r.status_code == 200 else []

    def brand_text(self) -> str:
        parts = []
        for path in ("/profile/summary", "/facts"):
            try:
                parts.append(json.dumps(self.get(self.brand, path), ensure_ascii=False))
            except (httpx.HTTPError, ValueError):
                pass
        return "\n".join(parts)


def write_b(live: "Live", vars_: dict, ch: str, phrase: str | None) -> dict:
    """Side B. With a phrase: check the first sentence in code, one retry with feedback."""
    text = post_text(live.run("social_posts", vars_), ch)
    if not phrase:
        return {"text": text, "open_with": None}
    enf = {"phrase": phrase, "first_try": opens_with(text, phrase), "attempts": 1}
    if not enf["first_try"]:
        enf["missed_first_sentence"] = first_sentence(text)
        retry = vars_ | {"open_with_feedback": open_with_feedback(phrase, enf["missed_first_sentence"])}
        text = post_text(live.run("social_posts", retry), ch)
        enf["attempts"] = 2
    enf["final"] = opens_with(text, phrase)
    return {"text": text, "open_with": enf}


def judge_row(judge: "Live", row: dict, rng: random.Random) -> None:
    quote_block = "\n".join(f"- {q}" for q in row["judge_quotes"]) or row["customer_phrases"]
    b_pos = rng.choice([1, 2])
    pair = {b_pos: row["B"]["text"], 3 - b_pos: row["A"]["text"]}
    j1 = judge.run("voc_judge", {"quotes": quote_block, "post_1": pair[1], "post_2": pair[2]})
    j2 = judge.run("voc_judge", {"quotes": quote_block, "post_1": pair[2], "post_2": pair[1]})
    row["judge"] = {"b_first_position": b_pos, "first": j1["output"], "swapped": j2["output"],
                    "model": j1.get("model"), "usage": [j1.get("usage"), j2.get("usage")],
                    "verdict": verdict(b_pos, j1["output"]["closer"], j2["output"]["closer"])}


def enforcement(rows: list[dict]) -> dict:
    enf = [r["B"]["open_with"] for r in rows if r["B"].get("open_with")]
    return {"posts_with_open_with": len(enf),
            "first_try": f"{sum(e['first_try'] for e in enf)}/{len(enf)}",
            "after_retry": f"{sum(e['final'] for e in enf)}/{len(enf)}",
            "retries": sum(e["attempts"] > 1 for e in enf)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(CASES))
    ap.add_argument("--gateway", default=os.getenv("GATEWAY_URL", "http://localhost:8103"))
    ap.add_argument("--judge-gateway", default="",
                    help="gateway for the voc_judge prompt only (e.g. a hosted model); default: --gateway")
    ap.add_argument("--voc", default=os.getenv("VOC_URL", "http://localhost:8170"))
    ap.add_argument("--brand", default=os.getenv("BRAND_URL", "http://localhost:8105"))
    ap.add_argument("--setup", action="store_true", help="import the cases' samples into 70 and mine first")
    ap.add_argument("--judge-quotes", type=int, default=6, help="customer quotes the judge sees per topic")
    ap.add_argument("--only", type=int, default=0, help="first N topics")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-open-with", action="store_true",
                    help="B gets customer_phrases only, no enforced opening phrase (how the first runs were done)")
    ap.add_argument("--rejudge", default="", help="a previous report: keep its posts, only run the judge again")
    a = ap.parse_args(argv)

    voc = a.voc.rstrip("/")
    live = Live(a.gateway.rstrip("/"), voc, a.brand.rstrip("/"))
    judge = Live((a.judge_gateway or a.gateway).rstrip("/"), voc, a.brand.rstrip("/"))
    rng = random.Random(a.seed)

    if a.rejudge:
        report = json.loads(Path(a.rejudge).read_text())
        rows = report["rows"][: a.only or None]
        report |= {"rejudged_from": a.rejudge, "judge_gateway": a.judge_gateway or a.gateway,
                   "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": a.seed}
        for row in rows:
            for side in ("A", "B"):  # metrics added after the report was written
                row[side]["metrics"]["bolted_on"] = bool(row.get("open_with")) and bolted_on(row[side]["text"], row["open_with"])
        report["totals"] = {"A": totals(rows, "A"), "B": totals(rows, "B")}
        for row in rows:
            judge_row(judge, row, rng)
            print(f"{row['judge']['verdict']:>3}  {row['id']:<20} (picks {row['judge']['first']['closer']},"
                  f"{row['judge']['swapped']['closer']})")
    else:
        case = yaml.safe_load(Path(a.cases).read_text())
        topics = case["topics"][: a.only or None]
        generic = yaml.safe_load(VOICE_CASES.read_text())["generic_ai_words"]
        report = {"cases": a.cases, "seed": a.seed, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "gateway": a.gateway, "judge_gateway": a.judge_gateway or a.gateway,
                  "open_with": not a.no_open_with}
        if a.setup:
            samples = json.loads((ROOT / case["samples"]).read_text())  # relative to 23-eval-suite/
            report["import"] = live.post(voc, "/sources/import", samples["sources"])
            t = time.monotonic()
            report["mine"] = live.post(voc, "/mine", {"ignore": case.get("ignore_phrases", [])})
            print(f"import {report['import']}; mined in {time.monotonic() - t:.0f}s")
        report["stats"] = live.get(voc, "/stats")
        bank_all = [p["phrase"] for p in live.get(voc, "/phrases", min_sources=2, limit=5000)]
        brand_text = live.brand_text()
        print(f"phrase bank: {len(bank_all)} phrases; themes {report['stats']['themes']}\n")

        rows = []
        for tp in topics:
            ch = tp["channel"].strip().lower()
            rel = live.get(voc, "/relevant", topic=tp["topic"], k=case.get("k", 8))
            quotes = live.get(voc, "/relevant", topic=tp["topic"], k=a.judge_quotes, kinds="quote")
            exclude = f"{tp['topic']}\n{brand_text}"
            given = countable([i["text"] for i in rel["items"]], exclude)
            bank = countable(bank_all, exclude)
            phrase = None if a.no_open_with else pick_open_with(rel["items"], exclude)
            row = {"id": tp["id"], "channel": ch, "topic": tp["topic"], "relevant_method": rel["method"],
                   "relevant_items": [{k: i.get(k) for k in ("text", "type", "score", "embedding_score",
                                                              "keyword_jaccard")} for i in rel["items"]],
                   "customer_phrases": rel["customer_phrases"], "given_countable": given, "open_with": phrase,
                   "judge_quotes": [i["text"] for i in quotes["items"]]}
            base = {"topic": tp["topic"], "channels": ch}
            row["A"] = {"text": post_text(live.run("social_posts", base), ch)}
            b_vars = base | {"customer_phrases": rel["customer_phrases"]} | ({"open_with": phrase} if phrase else {})
            row["B"] = write_b(live, b_vars, ch, phrase)
            for side in ("A", "B"):
                row[side]["metrics"] = metrics(row[side]["text"], given, bank, generic, phrase)
                row[side]["check"] = live.check(row[side]["text"], ch)
            judge_row(judge, row, rng)
            rows.append(row)
            ma, mb = row["A"]["metrics"], row["B"]["metrics"]
            enf = row["B"]["open_with"] or {}
            print(f"{row['judge']['verdict']:>3}  {tp['id']:<20} {ch:<9} open_with {phrase!r}"
                  f" first A {int(ma['opens_with_phrase'])} B {int(mb['opens_with_phrase'])}"
                  f" (tries {enf.get('attempts', '-')})  generic A {len(ma['generic_hits'])} B {len(mb['generic_hits'])}"
                  f"  chars A {ma['chars']} B {mb['chars']}"
                  f"  (picks {row['judge']['first']['closer']},{row['judge']['swapped']['closer']})")
        report["totals"] = {"A": totals(rows, "A"), "B": totals(rows, "B")}
        report["enforcement"] = enforcement(rows)

    wins = {k: sum(r["judge"]["verdict"] == k for r in rows) for k in ("A", "B", "tie")}
    positional = sum(r["judge"]["first"]["closer"] == r["judge"]["swapped"]["closer"] for r in rows)
    report |= {"rows": rows, "judge": wins | {"same_position_both_orders": positional,
                                              "model": rows[0]["judge"].get("model") if rows else None}}
    print(f"\njudge {report['judge']['model']} (win = both orders agree): B {wins['B']}  A {wins['A']}"
          f"  tie {wins['tie']}  of {len(rows)}; judge picked the same position both times in {positional}")
    if "enforcement" in report:
        print(f"open_with enforcement: {report['enforcement']}")
    for side in ("A", "B"):
        print(f"{side} ({'with' if side == 'B' else 'no'} customer language): {report['totals'][side]}")
    RESULTS.mkdir(exist_ok=True)
    tag = "-rejudge" if a.rejudge else ""
    out = RESULTS / f"voc_ab{tag}-{Path(a.cases).stem if not a.rejudge else Path(a.rejudge).stem}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
