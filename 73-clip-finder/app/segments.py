"""Topic segmentation of a transcript, so that a clip holds ONE point of a talk.

Windows used to be any run of whole sentences lasting min_s..max_s. On a talk with several
points, the longest of those ran over two points, and a small local model preferred exactly
those (they have more content). Windows are now built INSIDE topic segments.

A boundary is scored at every gap between two sentences, TextTiling-style (Hearst 1997):

- embedding drop (when an embedder is available): cosine similarity between the embedding of
  the text of the BLOCK sentences before the gap and of the BLOCK after it; its depth (how far
  it dips below the nearest peak on each side) is compared with the talk's own depths (robust
  z-score), so it does not depend on the embedding model's scale. Measured on two test talks
  this is a WEAK cue with a 0.6B embedder (one talk is about one subject throughout), so it
  only supports a boundary: alone it never makes one;
- pause: the silence at the gap, compared with the talk's median sentence gap;
- discourse marker: the sentence after the gap opens a new point ("Second, ...", "Number four",
  "The next lesson", "Another thing", "Finally", "To sum up", ...).

Boundaries are the best-scoring gaps from THRESHOLD up, at least MIN_SEGMENT_S apart: a marker
alone is enough, a clear pause with an embedding drop is enough, either alone is not. Without an
embedder (Ollama down, model missing) the pause and the markers decide alone (FALLBACK weights).
Everything is deterministic for the same words and embeddings.
"""
import math
import re
from dataclasses import dataclass, field
from statistics import median

import httpx

from .windows import Sentence

BLOCK = 2                 # sentences per side for the embedding comparison
MIN_SEGMENT_S = 8.0       # two boundaries are never closer than this
THRESHOLD = 0.5
WEIGHTS = {"embed": 0.35, "pause": 0.3, "marker": 0.5}
FALLBACK = {"embed": 0.0, "pause": 0.6, "marker": 0.5}
Z_FULL = 3.5              # a depth this many robust SDs above the talk's median counts fully
PAUSE_FULL_S = 1.5        # a pause this much longer than the median gap counts fully
EMBED_CHUNK = 64
SHORT_POINT = 0.75        # a segment of >= 0.75 x min_s is a whole point: a window may cover it

_ORD = (r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|next|last|final|"
        r"another|one more")
_NUM = r"one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2}"
_NOUN = (r"one|thing|things|point|lesson|step|tip|idea|rule|mistake|reason|trick|part|topic|"
         r"question|habit|ritual|principle|takeaway|example|story")
MARKER_RE = re.compile(
    r"^(?:(?:and|so|okay|ok|now|alright|all right|well)[,]?\s+)*(?:"
    rf"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)(?:ly)?\b(?!\s+(?:time|day|week|year|month)s?\b)"
    r"|finally\b|lastly\b|last but not least\b|moving on\b|let'?s move on\b|next up\b"
    r"|(?:to sum up|in summary|to summari[sz]e|to wrap up|in conclusion|to recap)\b"
    rf"|number (?:{_NUM})\b"
    rf"|(?:here is|here's|here are)\s+(?:the|my|our)\s+(?:{_ORD})\b"
    rf"|(?:the|my|our)\s+(?:{_ORD})\s+(?:(?:big|small|key|important|main)\s+)?(?:{_NOUN})\b"
    rf"|another\s+(?:(?:big|small|key|important)\s+)?(?:{_NOUN})\b"
    rf"|(?:{_NOUN})\s+(?:number\s+)?(?:{_NUM})\b"
    r"|next\b(?!\s+(?:week|month|year|day|time|morning|quarter)s?\b)"
    r")",
    re.IGNORECASE,
)


def is_marker(text: str) -> bool:
    return bool(MARKER_RE.match(" ".join(text.strip().lstrip("\"'“(").split())))


@dataclass
class Segment:
    first: int          # index of its first sentence
    last: int           # index of its last sentence (inclusive)
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Segmentation:
    segments: list[Segment]
    method: str                                  # "embeddings+pauses+markers" | "pauses+markers"
    gaps: list[dict] = field(default_factory=list)   # per gap before sentence i: scores
    boundaries: list[int] = field(default_factory=list)  # sentence indexes that open a segment
    error: str | None = None


# ---------- embeddings


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _mean(vecs: list[list[float]]) -> list[float]:
    n = len(vecs)
    return [sum(col) / n for col in zip(*vecs)]


def block_similarity(emb: list[list[float]], block: int = BLOCK) -> list[float | None]:
    """sim[i] from per-sentence embeddings (mean of each side's block); sim[0] is None."""
    out: list[float | None] = [None]
    for i in range(1, len(emb)):
        left = emb[max(0, i - block):i]
        right = emb[i:i + block]
        out.append(round(_cos(_mean(left), _mean(right)), 6))
    return out


def gap_similarity(texts: list[str], embed, block: int = BLOCK) -> list[float | None]:
    """sim[i] for the gap before sentence i (i = 1..n-1): the text of the `block` sentences
    before it vs the `block` after it, each embedded as one passage; sim[0] is None."""
    n = len(texts)
    left = [" ".join(texts[max(0, i - block):i]) for i in range(1, n)]
    right = [" ".join(texts[i:i + block]) for i in range(1, n)]
    vecs = embed(left + right)
    if len(vecs) != 2 * (n - 1) or not all(vecs):
        raise ValueError("embedder returned the wrong number of vectors")
    return [None] + [round(_cos(a, b), 6) for a, b in zip(vecs[:n - 1], vecs[n - 1:])]


def depths(sim: list[float | None]) -> list[float]:
    """TextTiling depth: climb to the highest similarity on each side while it keeps rising,
    and add both rises. 0 for sim[0]."""
    n = len(sim)
    out = [0.0] * n
    for i in range(1, n):
        s = sim[i]
        lp, j = s, i
        while j - 1 >= 1 and sim[j - 1] >= lp:
            lp, j = sim[j - 1], j - 1
        rp, j = s, i
        while j + 1 < n and sim[j + 1] >= rp:
            rp, j = sim[j + 1], j + 1
        out[i] = round((lp - s) + (rp - s), 6)
    return out


def _robust_z(values: list[float]) -> list[float]:
    if not values:
        return []
    med = median(values)
    mad = median(abs(v - med) for v in values) or 1e-6
    return [(v - med) / (1.4826 * mad) for v in values]


def ollama_embedder(url: str, model: str, timeout: float = 60.0):
    """A function texts -> vectors using Ollama's /api/embed; raises on any failure."""
    base = url.rstrip("/")

    def embed(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            for i in range(0, len(texts), EMBED_CHUNK):
                chunk = texts[i:i + EMBED_CHUNK]
                r = client.post(f"{base}/api/embed", json={"model": model, "input": chunk})
                r.raise_for_status()
                vecs = r.json().get("embeddings")
                if not isinstance(vecs, list) or len(vecs) != len(chunk):
                    raise ValueError("embedding response has the wrong shape")
                out.extend(vecs)
        return out

    return embed


# ---------- segmentation


def segment(sents: list[Sentence], embed=None) -> Segmentation:
    """Split sentences into topic segments. `embed` maps texts to vectors (or None)."""
    n = len(sents)
    if n == 0:
        return Segmentation([], "pauses+markers")
    method, error, emb_score = "pauses+markers", None, [0.0] * n
    weights = FALLBACK
    if embed is not None and n >= 4:
        try:
            dep = depths(gap_similarity([s.text for s in sents], embed))
            z = _robust_z(dep[1:])
            raw = [0.0] + [min(1.0, max(0.0, v / Z_FULL)) for v in z]
            # a drop is often found one sentence off (the turn sentence is ambiguous); pauses and
            # markers place the boundary precisely, the drop only has to be next to it
            emb_score = [0.0] + [round(max(raw[max(1, i - 1):i + 2]), 4) for i in range(1, n)]
            method, weights = "embeddings+pauses+markers", WEIGHTS
        except Exception as e:  # segmentation still works on pauses and markers
            error = f"embeddings unavailable ({type(e).__name__}); used pauses and discourse markers"

    pauses = [max(0.0, sents[i].start - sents[i - 1].end) for i in range(1, n)]
    med_pause = median(pauses) if pauses else 0.0
    gaps: list[dict] = []
    for i in range(1, n):
        pause = pauses[i - 1]
        p = round(min(1.0, max(0.0, (pause - med_pause) / PAUSE_FULL_S)), 4)
        m = 1.0 if is_marker(sents[i].text) else 0.0
        score = weights["embed"] * emb_score[i] + weights["pause"] * p + weights["marker"] * m
        gaps.append({"before": i, "t": round(sents[i].start, 3), "embed": emb_score[i], "pause": p,
                     "marker": m, "score": round(score, 4)})

    chosen: list[int] = []
    for g in sorted(gaps, key=lambda g: (-g["score"], g["before"])):
        if g["score"] < THRESHOLD:
            break
        i = g["before"]
        t = sents[i].start
        edges = [sents[0].start, sents[-1].end] + [sents[j].start for j in chosen]
        if all(abs(t - e) >= MIN_SEGMENT_S for e in edges):
            chosen.append(i)
    chosen.sort()
    return Segmentation(_segments(sents, chosen), method, gaps, chosen, error)


def _segments(sents: list[Sentence], boundaries: list[int]) -> list[Segment]:
    starts = [0] + boundaries
    ends = [b - 1 for b in boundaries] + [len(sents) - 1]
    return [Segment(a, b, sents[a].start, sents[b].end) for a, b in zip(starts, ends)]


def merge_short(seg: Segmentation, sents: list[Sentence], min_s: float, strong: float = 0.8,
                floor: float = SHORT_POINT) -> list[Segment]:
    """A segment of at least floor x min_s is a point of its own: kept, and a window may cover
    exactly it even when it is a little shorter than min_s (build_windows(whole_min_s=...)).
    A shorter one is merged with a neighbour: across a boundary WITHOUT a discourse marker if it
    has one (a marker opens a new point), else across the weaker boundary; never across a
    boundary scoring >= strong (then it stays alone and yields no window)."""
    gap = {g["before"]: g for g in seg.gaps}
    bounds = list(seg.boundaries)
    while True:
        segs = _segments(sents, bounds)
        short = [k for k, s in enumerate(segs) if s.duration < floor * min_s]
        merged = False
        for k in sorted(short, key=lambda k: (segs[k].duration, k)):
            left = bounds[k - 1] if k > 0 else None                   # boundary opening segs[k]
            right = bounds[k] if k < len(bounds) else None            # boundary closing segs[k]
            options = [b for b in (left, right)
                       if b is not None and gap.get(b, {}).get("score", 0) < strong]
            if not options:
                continue
            bounds.remove(min(options, key=lambda b: (gap.get(b, {}).get("marker", 0),
                                                      gap.get(b, {}).get("score", 0), b)))
            merged = True
            break
        if not merged:
            return segs
