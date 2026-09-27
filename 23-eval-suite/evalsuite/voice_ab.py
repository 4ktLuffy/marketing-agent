"""Does the interview voice profile (05 /voice) make posts sound more like the brand?

  python -m evalsuite.voice_ab                         # cases/voice/voice_ab.yaml, all topics
  python -m evalsuite.voice_ab --only 4                # first 4 topics (smoke run)
  python -m evalsuite.voice_ab --profile saved.json    # reuse a profile instead of generating

Needs the gateway (03) and a brand service (05) you may write to (it PUTs and DELETEs the
voice profile, and restores what was there at the end). With INTERNAL_API_KEY set in the
environment it is sent as X-API-Key.

1. Generate the profile from the interview answers (04 `voice_profile`), store it (PUT /voice).
2. For every held-out topic, write the post twice with `social_posts`:
   A = the brand summary without a voice profile, B = the summary with it. The summary is
   read from 05 in each state and passed as `brand`, so the gateway's 60 s brand cache
   can't mix the two. Everything else (facts, learned rules, temperature) is identical.
3. Blind pairwise judge (04 `voice_judge`, local model): the pair is shown in a random order,
   then again swapped. A side wins only when both orders pick it; otherwise it's a tie.
4. Objective metrics, no LLM: words per sentence vs the profile's target, words the profile
   avoids, and a fixed list of generic "AI" words.
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

CASES = Path(__file__).parent.parent / "cases" / "voice" / "voice_ab.yaml"
RESULTS = Path(__file__).parent.parent / "results"

URL_RE = re.compile(r"https?://\S+|\b\S+\.(?:com|net|org|io)\S*", re.I)
TAG_RE = re.compile(r"[#@]\w+")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")
TARGET_RE = re.compile(
    r"(?:under|below|fewer than|less than|at most|max(?:imum)?|up to|<=|≤)\s*(\d+)\s*words", re.I)
DEFAULT_TARGET = 15


# ---------- objective metrics (pure, unit-tested) ----------

def sentences(text: str) -> list[list[str]]:
    """Words per sentence, after dropping links, hashtags, mentions and emoji."""
    clean = EMOJI_RE.sub(" ", TAG_RE.sub(" ", URL_RE.sub(" ", text)))
    out = []
    for part in SPLIT_RE.split(clean):
        words = WORD_RE.findall(part)
        if words:
            out.append(words)
    return out


def sentence_target(profile: dict) -> int:
    """The profile's own sentence-length limit ("under 12 words"), else 15."""
    for text in [profile.get("sentence_style") or "", *(profile.get("do") or [])]:
        m = TARGET_RE.search(text)
        if m:
            return int(m.group(1))
    return DEFAULT_TARGET


def word_pattern(word: str) -> re.Pattern:
    """Whole-word match that also catches simple inflections: elevate -> elevating, elevated."""
    w = word.strip().lower()
    if " " in w or "-" in w or "'" in w:
        return re.compile(r"(?<!\w)" + re.escape(w) + r"(?!\w)", re.I)
    stem = w[:-1] if w.endswith("e") and len(w) > 4 else w
    return re.compile(r"(?<!\w)" + re.escape(stem) + r"(?:e|es|ed|ing|s|d)?(?!\w)", re.I)


def hits(text: str, words: list[str]) -> list[str]:
    text = text.replace("’", "'")
    return [m.group(0) for w in words if w.strip() for m in word_pattern(w).finditer(text)]


def metrics(text: str, profile: dict, generic: list[str]) -> dict:
    sents = sentences(text)
    target = sentence_target(profile)
    lens = [len(s) for s in sents]
    return {
        "sentences": len(lens),
        "avg_words": round(sum(lens) / len(lens), 1) if lens else 0.0,
        "over_target": sum(n > target for n in lens),
        "avoid_hits": hits(text, profile.get("words_we_avoid") or []),
        "generic_hits": hits(text, generic),
        "exclamations": text.count("!"),
    }


def voice_text(profile: dict) -> str:
    """What the judge sees: the profile's rules (no sample lines, so it can't match on them)."""
    lines = [profile["summary"]]
    if profile.get("sentence_style"):
        lines.append("Sentences: " + profile["sentence_style"])
    lines += [f"Do: {r}" for r in profile.get("do") or []]
    lines += [f"Don't: {r}" for r in profile.get("dont") or []]
    if profile.get("words_we_use"):
        lines.append("Words we use: " + ", ".join(profile["words_we_use"]))
    if profile.get("words_we_avoid"):
        lines.append("Words we avoid: " + ", ".join(profile["words_we_avoid"]))
    return "\n".join(lines)


def verdict(first_order_b_is: int, first: int, second: int) -> str:
    """first: judge's pick with B at position `first_order_b_is`; second: with the order swapped."""
    b_first = first == first_order_b_is
    b_second = second == (3 - first_order_b_is)
    if b_first and b_second:
        return "B"
    if not b_first and not b_second:
        return "A"
    return "tie"


def answers_text(case: dict) -> str:
    return "\n\n".join(f"Q: {row['q']}\nA: {row['a']}" for row in case["answers"])


# ---------- live run ----------

class Live:
    def __init__(self, gateway: str, brand: str, learning: str):
        key = os.getenv("INTERNAL_API_KEY")
        self.h = {"X-API-Key": key} if key else {}
        self.gateway, self.brand, self.learning = gateway, brand, learning
        self.http = httpx.Client(timeout=900)

    def run(self, prompt: str, vars_: dict, temperature: float | None = None) -> dict:
        body = {"prompt": prompt, "vars": vars_}
        if temperature is not None:
            body["temperature"] = temperature
        r = self.http.post(f"{self.gateway}/v1/run", json=body, headers=self.h)
        if r.status_code != 200:
            raise RuntimeError(f"{prompt}: {r.status_code} {r.text[:300]}")
        return r.json()

    def summary(self) -> str:
        s = self.http.get(f"{self.brand}/profile/summary").raise_for_status().json()["summary"]
        rules = ""
        if self.learning:
            try:
                rules = (self.http.get(f"{self.learning}/rules/summary", timeout=5).json().get("summary") or "")
            except (httpx.HTTPError, ValueError):
                rules = ""
        return "\n\n".join(p for p in (s, rules.strip()) if p)

    def get_voice(self) -> dict | None:
        r = self.http.get(f"{self.brand}/voice")
        return r.json() if r.status_code == 200 else None

    def put_voice(self, profile: dict) -> None:
        r = self.http.put(f"{self.brand}/voice", json=profile, headers=self.h)
        if r.status_code != 200:
            raise RuntimeError(f"PUT /voice: {r.status_code} {r.text[:300]}")

    def delete_voice(self) -> None:
        self.http.delete(f"{self.brand}/voice", headers=self.h).raise_for_status()

    def check(self, text: str, channel: str) -> list[dict]:
        r = self.http.post(f"{self.brand}/check", json={"text": text, "channel": channel})
        return r.json().get("violations", []) if r.status_code == 200 else []


def post_text(out: dict, channel: str) -> str:
    posts = out["output"]["posts"]
    match = [p for p in posts if p["channel"].strip().lower() == channel] or posts
    return match[0]["text"]


def totals(rows: list[dict], side: str) -> dict:
    ms = [r[side]["metrics"] for r in rows]
    sent = sum(m["sentences"] for m in ms)
    return {
        "avg_words_per_sentence": round(sum(m["avg_words"] * m["sentences"] for m in ms) / sent, 1) if sent else 0,
        "sentences_over_target": f"{sum(m['over_target'] for m in ms)}/{sent}",
        "avoid_word_hits": sum(len(m["avoid_hits"]) for m in ms),
        "generic_ai_word_hits": sum(len(m["generic_hits"]) for m in ms),
        "exclamations": sum(m["exclamations"] for m in ms),
        "brand_errors": sum(sum(v["severity"] == "error" for v in r[side]["check"]) for r in rows),
        "avg_chars": round(sum(len(r[side]["text"]) for r in rows) / len(rows)),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(CASES))
    ap.add_argument("--gateway", default=os.getenv("GATEWAY_URL", "http://localhost:8103"))
    ap.add_argument("--judge-gateway", default="", help="gateway for the voice_judge prompt only (e.g. a hosted model); default: --gateway")
    ap.add_argument("--brand", default=os.getenv("BRAND_URL", "http://localhost:8105"))
    ap.add_argument("--learning", default=os.getenv("LEARNING_URL", ""),
                    help="46 learning service; its rules are added to BOTH sides, as the gateway would")
    ap.add_argument("--profile", help="use this voice profile JSON instead of generating one")
    ap.add_argument("--only", type=int, default=0, help="first N topics")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)

    case = yaml.safe_load(Path(a.cases).read_text())
    topics = case["topics"][: a.only or None]
    generic = case["generic_ai_words"]
    live = Live(a.gateway.rstrip("/"), a.brand.rstrip("/"), a.learning.rstrip("/"))
    judge = Live((a.judge_gateway or a.gateway).rstrip("/"), a.brand.rstrip("/"), a.learning.rstrip("/"))
    rng = random.Random(a.seed)
    saved = live.get_voice()
    report = {"cases": a.cases, "seed": a.seed, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        live.delete_voice()
        brand_a = live.summary()
        if a.profile:
            profile = json.loads(Path(a.profile).read_text())
        else:
            t = time.monotonic()
            profile = live.run("voice_profile", {"answers": answers_text(case), "brand": brand_a})["output"]
            print(f"profile generated in {time.monotonic() - t:.0f}s")
        live.put_voice(profile)  # 05 validates it (422 on a bad profile)
        brand_b = live.summary()
        report |= {"profile": profile, "summary_a_chars": len(brand_a), "summary_b_chars": len(brand_b),
                   "summary_b": brand_b}
        print(json.dumps(profile, indent=2, ensure_ascii=False))
        print(f"summary chars: A {len(brand_a)}, B {len(brand_b)}; sentence target {sentence_target(profile)} words\n")

        rows = []
        for tp in topics:
            ch = tp["channel"].strip().lower()
            row = {"id": tp["id"], "channel": ch}
            for side, brand in (("A", brand_a), ("B", brand_b)):
                out = live.run("social_posts", {"topic": tp["topic"], "channels": ch, "brand": brand})
                text = post_text(out, ch)
                row[side] = {"text": text, "metrics": metrics(text, profile, generic),
                             "check": live.check(text, ch)}
            b_pos = rng.choice([1, 2])
            pair = {b_pos: row["B"]["text"], 3 - b_pos: row["A"]["text"]}
            j1 = judge.run("voice_judge", {"voice": voice_text(profile), "post_1": pair[1], "post_2": pair[2]})
            j2 = judge.run("voice_judge", {"voice": voice_text(profile), "post_1": pair[2], "post_2": pair[1]})
            row["judge"] = {"b_first_position": b_pos,
                            "first": j1["output"], "swapped": j2["output"],
                            "verdict": verdict(b_pos, j1["output"]["closer"], j2["output"]["closer"])}
            rows.append(row)
            ma, mb = row["A"]["metrics"], row["B"]["metrics"]
            print(f"{row['judge']['verdict']:>3}  {tp['id']:<22} {ch:<9} words/sent A {ma['avg_words']:>4} B {mb['avg_words']:>4}"
                  f"  generic A {len(ma['generic_hits'])} B {len(mb['generic_hits'])}"
                  f"  avoid A {len(ma['avoid_hits'])} B {len(mb['avoid_hits'])}"
                  f"  (picks {j1['output']['closer']},{j2['output']['closer']})")
    finally:
        try:
            live.put_voice(saved) if saved else live.delete_voice()
        except (httpx.HTTPError, RuntimeError) as exc:
            print(f"WARNING: could not restore the previous voice profile: {exc}", file=sys.stderr)

    wins = {k: sum(r["judge"]["verdict"] == k for r in rows) for k in ("A", "B", "tie")}
    positional = sum(r["judge"]["first"]["closer"] == r["judge"]["swapped"]["closer"] for r in rows)
    report |= {"rows": rows, "judge": wins | {"same_position_both_orders": positional},
               "totals": {"A": totals(rows, "A"), "B": totals(rows, "B")}}
    print(f"\njudge (win = both orders agree): B {wins['B']}  A {wins['A']}  tie {wins['tie']}"
          f"  of {len(rows)}; judge picked the same position both times in {positional}")
    for side in ("A", "B"):
        print(f"{side} ({'with' if side == 'B' else 'no'} voice): {report['totals'][side]}")
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"voice_ab-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
