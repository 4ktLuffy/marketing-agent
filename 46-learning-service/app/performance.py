"""Writer examples ranked by what performed, not only by what a reviewer approved.

Pure functions (no I/O) so the ranking can be tested and simulated on its own.

A post is one calendar item with tracked links (45 `/insights/posts`): its clicks, channel,
hook style and the day it went out. Only posts a reviewer approved (or edited) in 46 can
become examples: their text is the reviewer-approved text.

Why not simply "the most clicked posts": with one click count per post, the top of a
list of equally good posts is whichever got lucky, and a writer that copies it learns
noise. So a post must show **evidence** before it is used:

- it has settled (at least `settle_days` old, so its clicks have mostly arrived) and is
  not older than `max_age_days`;
- its channel has at least `min_posts` settled posts to compare with;
- it has at least `min_clicks` clicks;
- its clicks are unlikely under the channel's typical post: P(X >= clicks) is at most
  `alpha / n` (n = settled posts in the channel, a Bonferroni-style correction because
  we look at every post and keep the best), where X is negative binomial with the
  median and the robust (MAD-based) variance of the channel's other settled posts
  (Poisson when they are not more spread out than Poisson). Real clicks are
  overdispersed; a plain Poisson test there calls a lucky post a winner most of the time.

Posts that pass are ranked by clicks x 0.5 ** (age / half_life_days) (recency decay:
a recent winner reflects the current audience and offer better). Then **diversity**:
at most one example per hook style and none whose words overlap an already chosen
example by `dup_jaccard` or more (the calendar item carries no atom id, so near-identical
text stands in for "the same idea"). Remaining slots are filled with approval-based
examples by the caller.

Known limit: with one click count per post, a lucky post and a good one look the same
unless the gap is large. Stricter tails (plain variance) miss real winners when several
exist; looser ones (Poisson) pass lucky posts when clicks are overdispersed. This is the
middle setting; the numbers are in the simulation. The simulation in tests/test_performance.py measures both Poisson and
overdispersed clicks.
"""
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median


@dataclass(frozen=True)
class Params:
    min_clicks: int = 5
    min_posts: int = 5
    alpha: float = 0.1
    settle_days: float = 2.0
    max_age_days: int = 90
    half_life_days: float = 45.0
    dup_jaccard: float = 0.5


def _sf(c: int, mean: float, log_pmf) -> float:
    """P(X >= c) from a log pmf: 1 - P(X <= c-1) when the tail is large, else the tail's
    own terms (they shrink geometrically past the mean), so tiny tails keep their precision."""
    if c <= 0:
        return 1.0
    if c <= mean:
        return max(0.0, 1.0 - sum(math.exp(log_pmf(i)) for i in range(c)))
    total, i = 0.0, c
    while True:
        t = math.exp(log_pmf(i))
        total += t
        if t <= total * 1e-17 or i > c + 50 * (mean + 10):
            return min(1.0, total)
        i += 1


def poisson_sf(c: int, lam: float) -> float:
    """P(X >= c) for X ~ Poisson(lam)."""
    if c > 0 and lam <= 0:
        return 0.0
    log_lam = math.log(lam) if lam > 0 else 0.0
    return _sf(c, lam, lambda i: -lam + i * log_lam - math.lgamma(i + 1))


def tail_p(c: int, mean: float, var: float) -> float:
    """P(X >= c) for a count with this mean and variance: negative binomial when the
    counts are more spread out than Poisson (as clicks usually are), else Poisson."""
    if var <= mean * 1.0001 or mean <= 0:
        return poisson_sf(c, mean)
    r = mean * mean / (var - mean)
    p = r / (r + mean)
    lp, lq, lgr = math.log(p), math.log1p(-p), math.lgamma(r)
    return _sf(c, mean, lambda k: math.lgamma(k + r) - lgr - math.lgamma(k + 1) + r * lp + k * lq)


def robust_variance(counts: list[int]) -> float:
    """(1.4826 x median absolute deviation)^2: the spread of the ordinary posts, barely
    moved by the few real winners among them (a plain variance lets two winners hide a
    third)."""
    m = median(counts)
    return (1.4826 * median(abs(x - m) for x in counts)) ** 2


def age_days(posted_at: str | None, now: datetime) -> float | None:
    if not posted_at:
        return None
    try:
        dt = datetime.fromisoformat(str(posted_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 86400


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", (text or "").lower()))


def jaccard(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def rank(posts: list[dict], texts: dict[int, str], k: int, now: datetime,
         params: Params = Params()) -> tuple[list[dict], dict]:
    """Pick up to k performance examples.

    posts: [{"item_id", "channel", "clicks", "hook_style", "posted_at"}] (all tracked posts,
    approved or not: every settled post counts for the channel's typical rate).
    texts: item_id -> reviewer-approved text; only these can be picked.
    Returns (picked, stats); each picked entry is the post plus "text", "score", "p_value".
    """
    settled: dict[str, list[dict]] = {}
    for p in posts:
        a = age_days(p.get("posted_at"), now)
        if a is None or a < params.settle_days or a > params.max_age_days:
            continue
        ch = str(p.get("channel") or "unknown").lower()
        settled.setdefault(ch, []).append({**p, "age_days": a, "clicks": int(p.get("clicks") or 0)})

    candidates = []
    stats = {"settled_posts": sum(len(v) for v in settled.values()), "channels_with_evidence": 0,
             "passed_evidence": 0}
    for ch, group in settled.items():
        n = len(group)
        if n < params.min_posts:
            continue
        stats["channels_with_evidence"] += 1
        for p in group:
            if p["item_id"] not in texts or p["clicks"] < params.min_clicks:
                continue
            others = [q["clicks"] for q in group if q is not p]
            base = median(others)
            pv = tail_p(p["clicks"], max(base, 0.5), robust_variance(others))
            if pv > params.alpha / n:
                continue
            decay = 0.5 ** (p["age_days"] / params.half_life_days)
            candidates.append({**p, "text": texts[p["item_id"]], "score": round(p["clicks"] * decay, 3),
                               "p_value": pv, "baseline": base})
    stats["passed_evidence"] = len(candidates)

    candidates.sort(key=lambda c: (-c["score"], c["p_value"], c["item_id"]))
    picked: list[dict] = []
    styles: set[str] = set()
    for c in candidates:
        if len(picked) >= k:
            break
        style = str(c.get("hook_style") or "").strip().lower()
        if style and style in styles:
            continue
        if any(jaccard(c["text"], q["text"]) >= params.dup_jaccard for q in picked):
            continue
        picked.append(c)
        if style:
            styles.add(style)
    return picked, stats


def naive_top(posts: list[dict], texts: dict[int, str], k: int) -> list[dict]:
    """Negative control: the k most clicked approved posts, no evidence rule, no decay,
    no diversity."""
    ok = [p for p in posts if p["item_id"] in texts]
    return sorted(ok, key=lambda p: (-int(p.get("clicks") or 0), p["item_id"]))[:k]
