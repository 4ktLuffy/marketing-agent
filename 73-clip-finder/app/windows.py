"""Candidate clip windows, built in code from word timestamps (no model involved).

words -> sentences -> windows of min_s..max_s made of whole sentences -> a thinned, capped
candidate list -> (after scoring) the top non-overlapping picks, their boundaries snapped to
scene cuts or silences so a clip never starts or ends mid-word.
"""
from dataclasses import dataclass, field

SENTENCE_END = (".", "?", "!", "…")
# A sentence also ends at a pause this long (transcripts of casual speech lack full stops),
# and a run-on "sentence" is cut at a comma or a pause once it gets long.
PAUSE_BREAK_S = 1.2
SOFT_MAX_SENTENCE_S = 15.0
HARD_MAX_SENTENCE_S = 25.0
SNAP_S = 1.5          # a scene cut or silence this close to a boundary is snapped to
LEAD_IN_S = 0.12      # without a snap point: start this much before the first word
TAIL_S = 0.25         # ... and end this much after the last one
OPENING_S = 3.0       # "the first 3 seconds", shown to the scorer separately


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Sentence:
    start: float
    end: float
    first: int      # index of its first word
    last: int       # index of its last word (inclusive)
    text: str


@dataclass
class Window:
    id: str
    start: float
    end: float
    first_word: int
    last_word: int
    text: str
    opening: str
    scores: dict = field(default_factory=dict)
    score: float | None = None
    title: str | None = None
    hook: str | None = None
    reason: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


def _join(words: list[Word]) -> str:
    return " ".join(" ".join(w.text.split()) for w in words if w.text.strip()).strip()


def sentences(words: list[Word]) -> list[Sentence]:
    out: list[Sentence] = []
    begin = 0
    for i, w in enumerate(words):
        text = w.text.strip()
        nxt = words[i + 1] if i + 1 < len(words) else None
        dur = w.end - words[begin].start
        gap = (nxt.start - w.end) if nxt else 0.0
        end_here = (
            nxt is None
            or text.endswith(SENTENCE_END)
            or gap >= PAUSE_BREAK_S
            or (dur >= SOFT_MAX_SENTENCE_S and (text.endswith((",", ";", ":")) or gap >= 0.4))
            or dur >= HARD_MAX_SENTENCE_S
        )
        if end_here:
            chunk = words[begin:i + 1]
            out.append(Sentence(chunk[0].start, chunk[-1].end, begin, i, _join(chunk)))
            begin = i + 1
    return out


def build_windows(words: list[Word], min_s: float, max_s: float) -> list[Window]:
    """Every window of whole sentences lasting min_s..max_s: per starting sentence, the
    shortest one that reaches min_s and the longest one that stays within max_s."""
    sents = sentences(words)
    out: list[Window] = []
    seen: set[tuple[int, int]] = set()
    for i, s in enumerate(sents):
        shortest = longest = None
        for j in range(i, len(sents)):
            dur = sents[j].end - s.start
            if dur > max_s:
                break
            if dur >= min_s:
                if shortest is None:
                    shortest = j
                longest = j
        for j in (shortest, longest):
            if j is None or (i, j) in seen:
                continue
            seen.add((i, j))
            first, last = s.first, sents[j].last
            out.append(Window(
                id="", start=s.start, end=sents[j].end, first_word=first, last_word=last,
                text=_join(words[first:last + 1]),
                opening=_join([w for w in words[first:last + 1] if w.start < s.start + OPENING_S]),
            ))
    out.sort(key=lambda w: (w.start, w.end))
    return out


def thin(windows: list[Window], stride_s: float, cap: int) -> list[Window]:
    """Keep windows whose start is at least stride_s after the previous kept start (both
    lengths of one start stay together), then at most `cap`, spread evenly over the video.
    Ids w1..wN are assigned in time order."""
    kept: list[Window] = []
    last_start = None
    for w in windows:
        if last_start is None or w.start == last_start or w.start - last_start >= stride_s:
            kept.append(w)
            last_start = w.start
    if cap > 0 and len(kept) > cap:
        step = len(kept) / cap
        kept = [kept[int(k * step)] for k in range(cap)]
    for n, w in enumerate(kept, 1):
        w.id = f"w{n}"
    return kept


def overlaps(a: Window, b: Window) -> bool:
    return a.start < b.end and b.start < a.end


def pick(windows: list[Window], max_clips: int, min_score: float = 0.0) -> list[Window]:
    """Highest score first, skipping any window that overlaps one already picked. Returned in
    time order. Unscored windows are never picked."""
    ranked = sorted((w for w in windows if w.score is not None and w.score >= min_score),
                    key=lambda w: (-w.score, w.start))
    chosen: list[Window] = []
    for w in ranked:
        if len(chosen) >= max_clips:
            break
        if not any(overlaps(w, c) for c in chosen):
            chosen.append(w)
    return sorted(chosen, key=lambda w: w.start)


# ---------- boundary snapping


def _silence_end_near(t: float, silences: list[tuple[float, float]], lo: float, hi: float):
    """Silence interval ending in [lo, hi] (speech starts right after it), nearest to t."""
    hits = [s for s in silences if lo <= s[1] <= hi]
    return min(hits, key=lambda s: abs(s[1] - t)) if hits else None


def _silence_start_near(t: float, silences: list[tuple[float, float]], lo: float, hi: float):
    hits = [s for s in silences if lo <= s[0] <= hi]
    return min(hits, key=lambda s: abs(s[0] - t)) if hits else None


def snap(w: Window, words: list[Word], cuts: list[float], silences: list[tuple[float, float]],
         media_s: float, max_s: float, snap_s: float = SNAP_S) -> tuple[float, float, dict]:
    """New (start, end) for a window, and how each side was snapped.

    Start: a scene cut in [start - snap_s, start] (never before the previous word ends), else
    a silence that ends near the first word (start in that silence, just before speech), else
    a small lead-in. End: the mirror image after the last word. A snap that would make the clip
    longer than max_s is dropped for the end first, then the start.
    """
    first, last = words[w.first_word], words[w.last_word]
    prev_end = words[w.first_word - 1].end if w.first_word > 0 else 0.0
    next_start = words[w.last_word + 1].start if w.last_word + 1 < len(words) else media_s
    how = {}

    lo = max(prev_end, first.start - snap_s, 0.0)
    near_cuts = [c for c in cuts if lo <= c <= first.start]
    plain_start = max(lo, first.start - LEAD_IN_S)
    if near_cuts:
        start, how["start"] = max(near_cuts), "scene"
    else:
        sil = _silence_end_near(first.start, silences, lo, first.start + 0.3)
        if sil:
            start, how["start"] = max(sil[0], lo, min(sil[1], first.start) - LEAD_IN_S), "silence"
        else:
            start, how["start"] = plain_start, "pad"

    hi = min(next_start, last.end + snap_s, media_s)
    near_cuts = [c for c in cuts if last.end <= c <= hi]
    plain_end = min(hi, last.end + TAIL_S)
    if near_cuts:
        end, how["end"] = min(near_cuts), "scene"
    else:
        sil = _silence_start_near(last.end, silences, last.end - 0.3, hi)
        if sil:
            end, how["end"] = min(sil[1], hi, max(sil[0], last.end) + TAIL_S), "silence"
        else:
            end, how["end"] = plain_end, "pad"

    if end - start > max_s:
        end, how["end"] = plain_end, "pad"
    if end - start > max_s:
        start, how["start"] = plain_start, "pad"
    if end - start > max_s:  # the words alone already fill max_s: trim the tail pad
        end = start + max_s
    return round(max(0.0, start), 3), round(min(media_s, end), 3), how


def snap_all(chosen: list[Window], words: list[Word], cuts: list[float], silences: list[tuple[float, float]],
             media_s: float, max_s: float) -> list[tuple[float, float, dict]]:
    """snap() for clips in time order, then settle any overlap the padding created between
    neighbours: both meet halfway between the one's last word and the other's first word."""
    out = [list(snap(w, words, cuts, silences, media_s, max_s)) for w in chosen]
    for i in range(len(out) - 1):
        if out[i][1] > out[i + 1][0]:
            mid = round((words[chosen[i].last_word].end + words[chosen[i + 1].first_word].start) / 2, 3)
            out[i][1] = min(out[i][1], mid)
            out[i + 1][0] = max(out[i + 1][0], mid)
    return [tuple(x) for x in out]
