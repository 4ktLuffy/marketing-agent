"""Window ranking by comparison (SCORING_MODE=rank; the default is scoring), prompt `clip_ranking` (04).

Absolute 0-10 scores per window were unreliable with a small local model: a 7B gave two
different clips identical scores and the identical reason, so the pick between them was
arbitrary. Comparing a handful of windows side by side is an easier task, so:

1. Round 1: the windows in time order, in batches of at most `batch_size` (even sizes). The model ranks each
   batch best-first and writes short notes per criterion, a title, a hook line and a reason.
2. The best `ADVANCE` of every batch go on to the next round (ranked again, order only), and
   so on until one batch is left (a tournament; ~1.5x the calls of absolute scoring).
3. Final order: the round a window reached (later is better), then its place in that
   round's batch, then code features (below), then time. Every window gets a distinct rank;
   `score` = 100 x (N - rank + 1) / N, so the best is 100 and there are no ties.

Code features, used to break ties between windows that reached the same place in different
batches, and to order a batch the model could not rank:
- hook: the first sentence has a question mark / a number / a contrast word (0-3);
- standalone: the window does not open on a dangling word ("this", "that", "so", "and",
  "here", ...), which usually leans on what came before;
- length fit: 1 for 25-45 s, less the further outside.

The model's answer is checked: unknown and repeated ids are ignored, windows it left out
go after the ones it ranked (in feature order), and titles / hook lines get the checks of
scoring.set_texts.
"""
import math
import re

from . import scoring
from .windows import Window

PROMPT = "clip_ranking"
ADVANCE = 2
CRITERIA = ("hook", "standalone", "payoff", "quotable")
NOTE_MAX = 120

NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
    "twelve", "fifteen", "twenty", "thirty", "fifty", "hundred", "thousand", "million",
    "half", "double", "doubled", "triple", "tripled", "percent",
}
CONTRAST_WORDS = {
    "but", "instead", "never", "nobody", "nothing", "not", "no", "stop", "wrong", "mistake",
    "surprised", "surprising", "actually", "however", "yet", "without", "dead", "kills", "myth",
    "forget", "worst", "failed", "fail",
}
DANGLING_START = {
    "this", "that", "these", "those", "it", "its", "it's", "they", "them", "he", "she", "so",
    "and", "but", "also", "then", "which", "here", "there", "as", "because", "or", "plus",
    "anyway", "um", "uh",
}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def first_sentence(text: str) -> str:
    m = re.search(r"^(.+?[.?!…])(\s|$)", text.strip())
    return m.group(1) if m else text.strip()


def features(w: Window) -> dict:
    first = first_sentence(w.text)
    toks = _tokens(first)
    hook = (("?" in first)
            + any(t.isdigit() or t in NUMBER_WORDS for t in toks)
            + any(t in CONTRAST_WORDS for t in toks))
    opening = _tokens(w.text)[:1]
    standalone = 0 if (not opening or opening[0] in DANGLING_START) else 1
    d = w.duration
    off = 0.0 if 25 <= d <= 45 else (25 - d if d < 25 else d - 45)
    length_fit = round(max(0.0, 1 - off / 20), 2)
    return {"hook": int(hook), "standalone": standalone, "length_fit": length_fit,
            "total": round(hook + 2 * standalone + length_fit, 2)}


def feature_key(w: Window) -> tuple:
    return (-w.features.get("total", 0), w.start, w.end)


def order_from(batch: list[Window], output: dict) -> tuple[list[Window], int]:
    """Batch order from a model answer: its valid ids first (in its order, each once), the
    rest in feature order. Returns (order, how many the model ranked)."""
    by_id = {w.id: w for w in batch}
    ranked: list[Window] = []
    for raw in output.get("ranking") or []:
        w = by_id.get(str(raw).strip().strip("[]"))
        if w is not None and w not in ranked:
            ranked.append(w)
    rest = sorted((w for w in batch if w not in ranked), key=feature_key)
    return ranked + rest, len(ranked)


def apply_notes(batch: list[Window], output: dict) -> None:
    by_id = {w.id: w for w in batch}
    seen = set()
    for item in output.get("notes") or []:
        if not isinstance(item, dict):
            continue
        w = by_id.get(str(item.get("id", "")).strip().strip("[]"))
        if w is None or w.id in seen:
            continue
        seen.add(w.id)
        w.criteria = {k: " ".join(str(item.get(k) or "").split())[:NOTE_MAX] for k in CRITERIA if item.get(k)}
        scoring.set_texts(w, item)
    for w in batch:
        if w.id not in seen:
            scoring.set_texts(w, {})  # opening words as title and hook, no reason


def rank_batch(batch: list[Window], notes: bool, timeout: float, language: str | None,
               stats: dict) -> list[Window]:
    """One batch best-first. A batch the model cannot rank (after one retry) is ordered by
    the code features and counted in failed_batches."""
    if len(batch) == 1:
        if notes:
            apply_notes(batch, {})
        return batch
    vars_ = {"windows": scoring.window_block(batch), "count": len(batch)}
    if notes:
        vars_["notes"] = True
    if language:
        vars_["language"] = language
    for _attempt in range(2):
        stats["calls"] += 1
        try:
            out = scoring.run_prompt(vars_, timeout, PROMPT)
        except scoring.ScoringFailed as e:
            if len(stats["errors"]) < 3:
                stats["errors"].append(str(e))
            continue
        order, n = order_from(batch, out)
        if n >= 2 or n == len(batch):
            if notes:
                apply_notes(batch, out)
            stats["model_ranked"] += n
            return order
        if len(stats["errors"]) < 3:
            stats["errors"].append(f"ranking named {n} of {len(batch)} windows")
    stats["failed_batches"] += 1
    if notes:
        apply_notes(batch, {})
    return sorted(batch, key=feature_key)


def split(group: list[Window], batch_size: int) -> list[list[Window]]:
    """Consecutive batches of about batch_size, sizes as even as possible: 31 windows in
    batches of 6 are 5+5+5+5+5+6, never 6+6+6+6+6+1 (a batch of one would go on unjudged).
    No batch has fewer than 3 windows (unless the whole group does), so that every round
    shrinks the field; a batch may then hold up to 2 more than batch_size."""
    n = min(math.ceil(len(group) / batch_size), max(1, len(group) // 3))
    base, extra = divmod(len(group), n)
    out, i = [], 0
    for b in range(n):
        size = base + (1 if b >= n - extra else 0)
        out.append(group[i:i + size])
        i += size
    return out


def rank(windows: list[Window], batch_size: int, timeout: float, language: str | None = None,
         progress=None) -> dict:
    """Rank every window (sets .rank, .score, .features, .criteria, title/hook/reason).
    Raises ScoringFailed only when the model ranked no batch of the first round."""
    batch_size = max(3, batch_size)
    stats = {"mode": "rank", "batches": 0, "failed_batches": 0, "scored": 0, "rounds": 0, "calls": 0,
             "model_ranked": 0, "errors": []}
    if not windows:
        return stats
    for w in windows:
        w.features = features(w)
    place: dict[str, tuple[int, int]] = {}  # id -> (round reached, place in that round's batch)
    group = sorted(windows, key=lambda w: (w.start, w.end))
    first_batches = math.ceil(len(group) / batch_size)
    expected = first_batches + max(1, math.ceil(first_batches * ADVANCE / batch_size))
    rnd = 0
    while True:
        rnd += 1
        batches = split(group, batch_size)
        advancing: list[Window] = []
        failed_before = stats["failed_batches"]
        for batch in batches:
            stats["batches"] += 1
            order = rank_batch(batch, rnd == 1, timeout, language, stats)
            for pos, w in enumerate(order):
                place[w.id] = (rnd, pos)
            advancing.extend(order[:ADVANCE])
            if progress:
                progress(min(1.0, stats["batches"] / expected))
        multi = sum(1 for b in batches if len(b) > 1)
        if rnd == 1 and multi and stats["failed_batches"] - failed_before == multi:
            raise scoring.ScoringFailed("no batch could be ranked" + (f": {stats['errors'][0]}" if stats["errors"] else ""))
        if len(batches) == 1:
            break
        group = sorted(advancing, key=lambda w: (w.start, w.end))
    stats["rounds"] = rnd
    ordered = sorted(windows, key=lambda w: (-place[w.id][0], place[w.id][1], *feature_key(w)))
    n = len(ordered)
    for i, w in enumerate(ordered, 1):
        w.rank = i
        w.score = round(100 * (n - i + 1) / n, 1)
    stats["scored"] = n
    if progress:
        progress(1.0)
    return stats
