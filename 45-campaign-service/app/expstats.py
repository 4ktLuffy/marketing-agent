"""The experiment verdict, computed in code (the LLM never decides it).

One post is the unit; its outcome is the clicks it earned in the first 72 hours. Per arm:
clicks of a post ~ Poisson(rate), rate ~ Gamma. Design: _dev/research/experiment-loop.md s. 5.

- Prior: weakly informative, centred on the pooled clicks per post of the experiment's
  channel posts (both arms together), worth `prior_posts` posts. Both arms shrink toward
  the same mean (partial pooling), so a small arm can't look extreme by chance alone.
- Overdispersion: clicks per post vary far more than Poisson (a few posts take off). The
  pooled Pearson dispersion phi (>= 1) divides the counts before they enter the posterior
  (quasi-Poisson), which keeps the mean and widens the interval by sqrt(phi).
- Relative lift of B over A = rate_B / rate_A - 1, from Monte Carlo draws with a fixed
  seed (same data, same answer). 95% HDI = the shortest interval holding 95% of the draws.
- Kruschke's HDI + ROPE rule, with ROPE = +-rope (default 15%):
  HDI entirely above +rope or below -rope -> "winner";
  HDI entirely inside the ROPE -> "no_practical_difference";
  otherwise "continue", or "inconclusive" at the final look.
  No verdict before both arms have `min_posts` posts (except "inconclusive" at the end).
"""
from __future__ import annotations

import math

import numpy as np

HDI_MASS = 0.95
DRAWS = 20000


def dispersion(arms: list[list[float]]) -> float:
    """Pooled Pearson dispersion of clicks per post around each arm's mean; at least 1.

    phi = sum over arms and posts of (y - mean_arm)^2 / mean_arm, divided by (N - arms).
    With fewer than 4 residual degrees of freedom it is 1 (not enough data to say).
    """
    num, dof = 0.0, 0
    for ys in arms:
        if not ys:
            continue
        m = sum(ys) / len(ys)
        dof += len(ys) - 1
        if m > 0:
            num += sum((y - m) ** 2 for y in ys) / m
    if dof < 4:
        return 1.0
    return max(1.0, num / dof)


def hdi(samples: np.ndarray, mass: float = HDI_MASS) -> tuple[float, float]:
    s = np.sort(samples)
    n = len(s)
    m = int(math.ceil(mass * n))
    widths = s[m - 1:] - s[: n - m + 1]
    i = int(np.argmin(widths))
    return float(s[i]), float(s[i + m - 1])


def analyze(a: list[float], b: list[float], *, rope: float = 0.15, min_posts: int = 12,
            final: bool = False, prior_posts: float = 2.0, prior_rate: float | None = None,
            draws: int = DRAWS, seed: int = 0) -> dict:
    """Verdict for arm A (clicks per post, list) vs arm B. Deterministic for a given seed."""
    n_a, n_b = len(a), len(b)
    y_a, y_b = float(sum(a)), float(sum(b))
    pooled_n = n_a + n_b
    if prior_rate is None:
        prior_rate = (y_a + y_b) / pooled_n if pooled_n else 1.0
    prior_rate = max(prior_rate, 0.1)   # a zero-click channel still gets a proper prior
    phi = dispersion([a, b])
    alpha0, beta0 = prior_rate * prior_posts, prior_posts
    post = {
        "A": (alpha0 + y_a / phi, beta0 + n_a / phi),
        "B": (alpha0 + y_b / phi, beta0 + n_b / phi),
    }
    rng = np.random.default_rng(seed)
    ra = rng.gamma(post["A"][0], 1.0 / post["A"][1], draws)
    rb = rng.gamma(post["B"][0], 1.0 / post["B"][1], draws)
    lift = rb / ra - 1.0
    lo, hi = hdi(lift)
    best = np.maximum(ra, rb)

    enough = n_a >= min_posts and n_b >= min_posts
    winner = None
    if not enough:
        decision = "inconclusive" if final else "continue"
    elif lo > rope:
        decision, winner = "winner", "B"
    elif hi < -rope:
        decision, winner = "winner", "A"
    elif lo >= -rope and hi <= rope:
        decision = "no_practical_difference"
    else:
        decision = "inconclusive" if final else "continue"

    def arm(label, ys, n, y):
        al, be = post[label]
        return {"posts": n, "clicks": int(y) if float(y).is_integer() else y,
                "clicks_per_post": round(y / n, 2) if n else None,
                "posterior_mean": round(al / be, 3), "alpha": round(al, 3), "beta": round(be, 3)}

    return {
        "arms": {"A": arm("A", a, n_a, y_a), "B": arm("B", b, n_b, y_b)},
        "lift_mean": round(float(np.mean(lift)), 4),
        "lift_hdi": [round(lo, 4), round(hi, 4)],
        "rope": [-rope, rope],
        "p_b_better": round(float(np.mean(rb > ra)), 4),
        "expected_loss": {"A": round(float(np.mean(best - ra)), 4), "B": round(float(np.mean(best - rb)), 4)},
        "dispersion": round(phi, 3),
        "prior": {"rate": round(prior_rate, 3), "posts": prior_posts},
        "min_posts": min_posts,
        "enough_posts": enough,
        "final": final,
        "decision": decision,
        "winner": winner,
        "method": "Gamma-Poisson per arm, prior at the pooled channel rate, quasi-Poisson dispersion; "
                  "95% HDI of relative lift vs ROPE (Kruschke)",
        "draws": draws,
        "seed": seed,
    }
