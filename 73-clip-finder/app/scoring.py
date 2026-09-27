"""Window scoring through the LLM gateway (03), prompt `clip_scoring` (04).

The model only scores transcript windows it is given and names them; it never writes new
content. The code keeps it honest: ids outside the batch are ignored, scores are clamped to
0-10, a `hook_line` that is not copied word for word from its window is replaced by the window's
opening, and a title with a number the window does not contain is replaced by the opening
words.
"""
import os
import re

import httpx

from .windows import Window

PROMPT = "clip_scoring"
WEIGHTS = {"hook": 0.35, "standalone": 0.25, "payoff": 0.25, "quotable": 0.15}
TITLE_MAX = 70
HOOK_MAX = 120


class ScoringFailed(Exception):
    pass


def gateway_url() -> str:
    return (os.environ.get("GATEWAY_URL") or "http://llm-gateway:8000").rstrip("/")


def key_headers() -> dict:
    key = os.environ.get("INTERNAL_API_KEY")
    return {"X-API-Key": key} if key else {}


def _mmss(t: float) -> str:
    t = int(round(t))
    return f"{t // 60:02d}:{t % 60:02d}"


def window_block(batch: list[Window]) -> str:
    parts = []
    for w in batch:
        parts.append(f"[{w.id}] {_mmss(w.start)}-{_mmss(w.end)} ({w.duration:.0f} s)\n"
                     f"FIRST 3 SECONDS: {w.opening}\n"
                     f"FULL TEXT: {w.text}")
    return "\n\n".join(parts)


def run_prompt(vars_: dict, timeout: float) -> dict:
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            r = client.post(f"{gateway_url()}/v1/run", json={"prompt": PROMPT, "vars": vars_},
                            headers=key_headers())
    except httpx.HTTPError as exc:
        raise ScoringFailed(f"gateway unreachable: {type(exc).__name__}")
    if r.status_code != 200:
        raise ScoringFailed(f"gateway {r.status_code}: {r.text[:200]}")
    out = r.json().get("output")
    if not isinstance(out, dict):
        raise ScoringFailed("gateway returned no JSON object")
    return out


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())


def _clamp(v) -> int | None:
    try:
        return max(0, min(10, int(round(float(v)))))
    except (TypeError, ValueError):
        return None


def _words_upto(text: str, limit: int) -> str:
    """Whole words of text, at most limit characters (never cut inside a word)."""
    out = ""
    for word in text.split():
        if len(out) + len(word) + 1 > limit:
            break
        out = f"{out} {word}".strip()
    return out.rstrip(",;:") or text[:limit]


def _opening_words(w: Window, limit: int) -> str:
    return _words_upto(w.text, limit)


def apply(batch: list[Window], output: dict) -> int:
    """Copy scores from one model answer onto the batch's windows. Returns how many scored."""
    by_id = {w.id: w for w in batch}
    done = 0
    for item in output.get("scores") or []:
        if not isinstance(item, dict):
            continue
        w = by_id.get(str(item.get("id", "")).strip().strip("[]"))
        if w is None or w.score is not None:
            continue
        scores = {k: _clamp(item.get(k)) for k in WEIGHTS}
        if any(v is None for v in scores.values()):
            continue
        w.scores = scores
        w.score = round(10 * sum(WEIGHTS[k] * v for k, v in scores.items()), 1)
        hook = " ".join(str(item.get("hook_line") or "").split()).strip("\"'“”")
        w.hook = _words_upto(hook, HOOK_MAX) if hook and _norm(hook) and _norm(hook) in _norm(w.text) else None
        if not w.hook:
            w.hook = _opening_words(w, HOOK_MAX)
        title = " ".join(str(item.get("title") or "").split()).strip("\"'“”")[:TITLE_MAX]
        numbers = re.findall(r"\d+", title)
        if not title or any(n not in w.text for n in numbers):
            title = _opening_words(w, 50)
        w.title = title
        w.reason = " ".join(str(item.get("reason") or "").split())[:300] or None
        done += 1
    return done


def score(windows: list[Window], batch_size: int, timeout: float, language: str | None = None,
          progress=None) -> dict:
    """Score every window in batches (each batch retried once). Returns stats; raises
    ScoringFailed only when no window at all could be scored."""
    stats = {"batches": 0, "failed_batches": 0, "scored": 0, "errors": []}
    batches = [windows[i:i + batch_size] for i in range(0, len(windows), batch_size)]
    for n, batch in enumerate(batches):
        stats["batches"] += 1
        vars_ = {"windows": window_block(batch), "count": len(batch)}
        if language:
            vars_["language"] = language
        got, err = 0, None
        for _attempt in range(2):
            try:
                got += apply(batch, run_prompt(vars_, timeout))
            except ScoringFailed as e:
                err = str(e)
                continue
            if all(w.score is not None for w in batch):
                break
        if got == 0:
            stats["failed_batches"] += 1
            if err and len(stats["errors"]) < 3:
                stats["errors"].append(err)
        stats["scored"] += got
        if progress:
            progress((n + 1) / len(batches))
    if windows and stats["scored"] == 0:
        raise ScoringFailed("no window could be scored" + (f": {stats['errors'][0]}" if stats["errors"] else ""))
    return stats
