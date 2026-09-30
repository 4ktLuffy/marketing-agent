"""Split a chatbot's pasted answer into the task's pieces.

The pack asks for `=== 1 LINKEDIN ===` marker lines. Chatbots also write `**1. LinkedIn**`,
`### Post 2 – Instagram`, `1) Email:` or `LinkedIn:`. Those are accepted. Preamble ("Sure! Here
are...") and sign-off ("Let me know if...") are dropped and reported, never silently. When the
markers do not line up with the pieces, nothing is guessed: `problems` says what is wrong and
the person splits by hand (POST /tasks/{id}/drafts/{d}/split).
"""
import re
from dataclasses import dataclass, field

from . import channels as CH

ZERO_WIDTH = re.compile("[​‌‍⁠﻿­]")
SPACES = re.compile("[     　]")

# words a heading may carry besides the channel and its number ("Instagram caption", "Cold email",
# "Email to members", "Google Business Profile post")
FILLER = frozenset("""post posts caption captions copy version draft update message messages piece thread ad ads
advert article newsletter reel story stories page profile listing blurb body content blast campaign announcement
promo promotional short long cold outreach follow up followup member members customer customers subscriber
subscribers client clients to for the a an and on one two three carousel feed edition template
subject line hook script video teaser snippet description sequence welcome intro social media organic paid
local update option broadcast status pin pins intro excerpt banner reply replies section headline tagline
strapline hero landing bio card leaflet poster tweet""".split())
_PREFIX = re.compile(r"^(?:(?P<w>post|piece|part|draft|option|version|no\.?|number|#)\s*)?(?P<n>\d{1,2})"
                     r"(?:\s*[.):\-–—·|/•]+\s*|\s+|$)", re.I)
_PREFIX2 = re.compile(r"^(?P<w>post|piece|part)\s*(?P<n>\d{1,2})(?:\s*[.):\-–—·|/•]+\s*|\s+|$)", re.I)
_EDGE = re.compile(r"^[^\w(\[]+|[^\w)\]]+$")
_ALIAS_ALT = "|".join(sorted((re.escape(" ".join(a)) for a in CH.ALIASES), key=len, reverse=True))
INLINE = re.compile(r"^\s*(?:\*\*|__)?\s*(?P<n>\d{1,2})\s*[.)]\s*(?P<ch>" + _ALIAS_ALT +
                    r")\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(?P<rest>\S.*)$", re.I)
SENTENCE_END = re.compile(r"[.!?…]\s*$")

PREAMBLE = re.compile(r"^\s*(?:sure|certainly|absolutely|of course|great|okay|ok|here(?:'s| is| are)|below (?:is|are)|"
                      r"i(?:'ve| have) (?:written|drafted|created|put together)|happy to help|no problem|alright)\b", re.I)
SIGNOFF = re.compile(r"^\s*(?:let me know|feel free|i hope|hope (?:this|these|that)|would you like|want me to|"
                     r"if you(?:'d| would)? (?:like|want|need)|happy to (?:adjust|help|tweak|revise|make|change|add|write|do|shorten)|"
                     r"good luck|want (?:a|an|another|me|any|more|it|them|this|these)\b|need (?:a|an|another|any)\b|"
                     r"should i|i can (?:also )?(?:adjust|make|write|add|shorten|tweak|do)|"
                     r"these (?:posts|pieces|drafts) (?:are|should)|shall i|do you want|enjoy)\b", re.I)
RULE_LINE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,}|={3,})\s*$")


def normalise(text: str) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = ZERO_WIDTH.sub("", t)
    t = SPACES.sub(" ", t)
    return t


def strip_markdown(text: str) -> str:
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    t = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"\1", t)
    t = re.sub(r"(?<![\w\[])__(?=\S)(.+?)(?<=\S)__(?![\w\]])", r"\1", t)
    return t


@dataclass
class Marker:
    line_no: int
    n: int | None
    channel: str | None
    inline: str | None = None
    bare: bool = False            # a one-line "Blog: ..." piece, not a heading


@dataclass
class Result:
    split: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _decorations(line: str) -> tuple[str, bool]:
    """(bare label, was it decorated). A decorated line (=== x ===, #, **x**) is a stronger marker.
    Emoji and symbols around the label ("### 📸 Instagram", "📱 SMS:") are dropped."""
    s = line.strip()
    deco = False
    s = re.sub(r"^(\d{1,2})\uFE0F?\u20E3\s*", r"\1. ", s)      # keycap numbers: "1️⃣ Google ad"
    # emoji / symbols before a bold label ("📸 **Instagram**"): the bold still decorates it
    s = re.sub(r"^[^\w(\[*_#=]+(?=\*\*|__)", "", s)
    m = re.fullmatch(r"={2,}\s*(.*?)\s*={2,}", s)
    if m:
        s, deco = m.group(1), True
    if s.startswith("#"):
        s, deco = s.lstrip("#").strip(), True
    for _ in range(2):
        s = _EDGE.sub("", s).strip() if not re.match(r"^[*_#]", s) else s
        for w in ("**", "__"):
            if s.startswith(w) and s.rstrip(":").rstrip().endswith(w) and len(s) > 4:
                s, deco = s.rstrip(":").rstrip()[2:-2].strip(), True
        s = s.rstrip(":").strip()
        s = re.sub(r"^\*\*|\*\*$|^__|__$", "", s).strip()
        s = _EDGE.sub("", s).strip()
    return s, deco


@dataclass
class Label:
    n: int | None
    numbered_piece: bool      # "Post 2", "Piece 3", "=== 2 ==="
    channels: set             # canonical channels named (strong ones if any)
    weak_only: bool           # only weak aliases ("Text", "Web")
    unknown: int              # words that are neither channel, number nor heading filler
    words: int


def read_label(label: str) -> Label | None:
    """Number, channels and leftover words of a heading label."""
    n, numbered = None, False
    m = _PREFIX2.match(label) or _PREFIX.match(label)
    if m:
        n, numbered = int(m.group("n")), bool(m.group("w") and m.group("w").lower() in ("post", "piece", "part"))
        label = label[m.end():]
    else:
        t = re.search(r"\s(?:(?:post|piece|part)\s*)?#?(?P<n>\d{1,2})\)?$", label, re.I)   # "Instagram post 1"
        if t:
            n, numbered = int(t.group("n")), True
            label = label[:t.start()]
    inside = " ".join(re.findall(r"\(([^)]{0,60})\)", label))
    outside = re.sub(r"\([^)]{0,60}\)", " ", label)

    def toks(x):
        return [w for w in re.split(r"[\s\-_/&+,:·•|–—.!?()\[\]*]+", x.lower().replace("’", "'")) if w]
    out_t, in_t = toks(outside), toks(inside)
    found_out, rest_out = CH.find_aliases(out_t)
    found_in, _ = CH.find_aliases(in_t)
    found = found_out or found_in
    strong = {c for c, st, _, _ in found if st}
    chans = strong or {c for c, _, _, _ in found}
    # weak aliases next to a strong one are just words ("Email text", "Facebook ad")
    unknown = sum(1 for i in rest_out if not out_t[i].isdigit() and out_t[i] not in FILLER)
    return Label(n, numbered, chans, bool(chans) and not strong, unknown, len(out_t))


def parse_marker(line: str, channels: set[str], n_pieces: int, standalone: bool = False) -> Marker | None:
    """A heading line that starts a piece:
    - decorated (=== x ===, # x, **x**) with a number or a channel the task asked for;
    - plain "LinkedIn:", "Email newsletter:", "📱 SMS:" (a task channel, a colon, heading words only);
    - plain "1. LinkedIn", "3 GOOGLE" (a number and a task channel);
    - a bare "INSTAGRAM" on a line of its own after a blank line (`standalone`), strong alias only.
    Anything else is text. Channels are compared by `channels.canonical`, so "Google post" fills a
    google_business piece and "Cold email" an email piece."""
    if not line.strip():
        return None
    m = _label_marker(line, channels, n_pieces, standalone) if len(line.strip()) <= 80 else None
    if m:
        return m
    im = INLINE.match(line)
    return _inline(im, channels) if im else None


# "Here's your LinkedIn post:", "And here's a short banner for the website:" — a chatbot presenting
# one piece: the whole line, ending in a colon, naming exactly one task channel
PRESENTER = re.compile(r"^\s*(?:\*\*|__)?\s*(?:(?:and\s+)?(?:here(?:'s|’s|\s+is|\s+are)|below\s+is|this\s+is)\s+"
                       r"(?:(?:your|the|a|an|my|our)\s+)?|(?:and|also|plus|finally),?\s+(?:your|the|a|an|my|our)\s+)(?P<rest>[^:]{1,70}?)\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*$", re.I)


def _presenter_marker(line: str, channels, n_pieces: int) -> Marker | None:
    m = PRESENTER.match(line) if n_pieces > 1 else None      # one piece: it stays a preamble
    if not m:
        return None
    lab = read_label(m.group("rest"))
    if not lab or not lab.channels or lab.words > 8:
        return None
    want = {CH.canonical(c) for c, _ in _pairs_of(channels)}
    asked = {x for x in (_asked(c, channels) for c in lab.channels) if x}
    if len(asked) != 1 or len(lab.channels) != 1:
        return None                  # "Here are the Instagram and Facebook posts:"
    ch = next(iter(asked))
    n = lab.n if lab.n is not None and 1 <= lab.n <= n_pieces else None
    return Marker(0, n, ch) if ch in want else None


def _label_marker(line: str, channels: set[str], n_pieces: int, standalone: bool) -> Marker | None:
    pm = _presenter_marker(line, channels, n_pieces)
    if pm:
        return pm
    label, deco = _decorations(line)
    had_colon = line.strip().rstrip("*_ ").endswith(":")
    lab = read_label(label) if label else None
    if not (lab and (lab.n is not None or lab.channels) and lab.words <= 8):
        return None
    pairs = _pairs_of(channels)
    want = {CH.canonical(c) for c, _ in pairs}
    # "Google ad" on a piece for channel "google": the task's own name for it; a heading naming the
    # channel itself wins over one that fills a piece by its kind ("Blog" on a website piece of kind blog)
    asked = {CH.canonical(t) for c in lab.channels for t, _ in pairs if CH.accepts(t, c)}
    if not asked:
        asked = {CH.canonical(t) for c in lab.channels for t, k in pairs if CH.accepts(t, c, k)}
    if len(asked) > 1:
        return None                  # "Instagram and Facebook": which one?
    ch = next(iter(asked)) if asked else None
    other = next(iter(sorted(lab.channels))) if lab.channels and not asked else None
    n = lab.n
    if n is not None and not 1 <= n <= n_pieces + 5:
        return None
    if ch is None and n is None:
        return None                  # "**Website**" inside a blog post
    if ch is None and other is not None:
        ch = other                   # "=== 2 FACEBOOK ===" on an Instagram piece: a problem later
    numbered_piece = n is not None and (line.strip().startswith("=") or lab.numbered_piece)
    if deco and lab.unknown <= 2 and (ch is not None or numbered_piece):
        return Marker(0, n, ch)
    if lab.unknown:
        return None
    if not deco and had_colon and ch is not None and ch in want:
        return Marker(0, n, ch)
    if not deco and ch is not None and n is not None and ch in want:
        return Marker(0, n, ch)      # "1. LinkedIn", "1) LinkedIn", "1 INSTAGRAM", "Post 1 (Instagram)"
    if (not deco and standalone and ch is not None and ch in want and n is None and not lab.weak_only
            and not SENTENCE_END.search(line)):
        return Marker(0, None, ch)   # "INSTAGRAM" on its own line
    return None


def _pairs_of(channels) -> list[tuple[str, str | None]]:
    """Task channels as (channel, kind); plain channel names carry no kind."""
    return [c if isinstance(c, tuple) else (c, None) for c in channels]


def _asked(label_channel: str, channels) -> str | None:
    """The task channel (canonical) a label channel fills: by channel first, then by piece kind."""
    pairs = _pairs_of(channels)
    for use_kind in (False, True):
        hit = {CH.canonical(t) for t, k in pairs if CH.accepts(t, label_channel, k if use_kind else None)}
        if len(hit) == 1:
            return next(iter(hit))
        if hit:
            return None
    return None


def _inline(m, channels) -> Marker | None:
    hit = CH.ALIASES.get(tuple(re.sub(r"[-_/]+", " ", m.group("ch").lower()).split()))
    ch = _asked(hit[0], channels) if hit else None
    if ch is None:
        return None
    return Marker(0, int(m.group("n")), ch, m.group("rest").strip())


# "Blog: Your first purchase, one fixed fee..." — a task channel, a colon and a sentence, on a line
# of its own after a blank line: the piece written on one line. Weak aliases never; short values
# ("Website: www.example.com", "Email: hello@...") are contact details, not a piece.
INLINE_BARE = re.compile(r"^\s*(?:\*\*|__)?\s*(?P<ch>" + _ALIAS_ALT + r")(?:\s+(?:post|copy|caption|message|update|"
                         r"intro|teaser|version|blurb|snippet))?\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(?P<rest>\S.*)$", re.I)


# "LinkedIn — Pivotdesk Team costs £29 a month.", "Instagram – Flowers that don't wait 💐": a task
# channel, a dash and text, at the start of a paragraph. The text after the dash is the piece's
# first line (a title or its first sentence). Strong aliases only, never "Text —" or "Web —".
DASH_INLINE = re.compile(r"^\s*(?:\*\*|__)?\s*(?P<ch>" + _ALIAS_ALT + r")(?:\s+(?:" + "|".join(sorted(FILLER)) +
                         r"))*\s*(?:\*\*|__)?\s+[—–-]\s+(?P<rest>\S.*)$", re.I)


def _dash_inline(line: str, channels) -> Marker | None:
    m = DASH_INLINE.match(line)
    if not m:
        return None
    hit = CH.ALIASES.get(tuple(re.sub(r"[-_/]+", " ", m.group("ch").lower()).split()))
    # weak aliases never, except the channel X written as its own name: a capital "X" and a dash
    if not hit or not (hit[1] or m.group("ch") == "X"):
        return None
    ch = _asked(hit[0], channels)
    return Marker(0, None, ch, m.group("rest").strip()) if ch else None


def _inline_bare(line: str, channels) -> Marker | None:
    m = INLINE_BARE.match(line)
    if not m:
        return None
    hit = CH.ALIASES.get(tuple(re.sub(r"[-_/]+", " ", m.group("ch").lower()).split()))
    if not hit or not hit[1]:
        return None
    rest = m.group("rest").strip()
    if len(rest.split()) < 5 or re.search(r"https?://|www\.|@\w", rest):
        return None
    ch = _asked(hit[0], channels)
    if ch is None:
        return None
    return Marker(0, None, ch, rest, bare=True)


def _trim(block: str) -> tuple[str, str, str]:
    """(pre, body, post): a leading preamble paragraph and trailing sign-off paragraphs removed."""
    paras = re.split(r"\n\s*\n", block.strip("\n"))
    pre, post = [], []
    while paras and (not paras[0].strip() or RULE_LINE.match(paras[0])):
        paras.pop(0)
    if len(paras) > 1 and PREAMBLE.match(paras[0]) and len(paras[0]) <= 300:
        pre.append(paras.pop(0))
    while paras and (not paras[-1].strip() or RULE_LINE.match(paras[-1])):
        paras.pop()
    while len(paras) > 1 and SIGNOFF.match(paras[-1]) and len(paras[-1]) <= 400:
        post.insert(0, paras.pop())
        while paras and RULE_LINE.match(paras[-1]):
            paras.pop()
    body = "\n\n".join(paras).strip()
    # a trailing separator line inside the last paragraph ("text\n---")
    body = re.sub(r"\n\s*(?:-{3,}|\*{3,}|_{3,})\s*$", "", body).strip()
    return "\n\n".join(pre).strip(), body, "\n\n".join(post).strip()


def _heading_look(line: str) -> bool:
    """A symbol / emoji or an italic star before the label, or two or more words ("Instagram caption")."""
    s = line.strip()
    if re.match(r"^[^\w\s(\[]", s):
        return True
    label, _ = _decorations(line)
    return len(re.sub(r"\([^)]*\)", " ", label).split()) >= 2


def _find_markers(lines: list[str], channels, n_pieces: int) -> list[Marker]:
    out = []
    for i, line in enumerate(lines):
        before_blank = i == 0 or not lines[i - 1].strip()
        standalone = before_blank and i + 1 < len(lines) and bool(lines[i + 1].strip())
        # a heading paragraph of its own ("📸 Instagram caption", "*📱 WhatsApp broadcast*", blank
        # lines on both sides, text after): it looks like a heading (an emoji / symbol or italic star
        # before it, or heading words after the channel), so a lone word in a paragraph is not one
        if (not standalone and before_blank and i + 1 < len(lines) and not lines[i + 1].strip()
                and any(x.strip() for x in lines[i + 2:]) and _heading_look(line)):
            standalone = True
        m = parse_marker(line, channels, n_pieces, standalone)
        if m is None and (i == 0 or not lines[i - 1].strip()):
            m = _dash_inline(line, channels) or _inline_bare(line, channels)
        if m:
            m.line_no = i
            out.append(m)
    # a one-line "Blog: ..." piece counts only when no heading names that channel
    named = {m.channel for m in out if not m.bare}
    return [m for m in out if not (m.bare and m.channel in named)]


def _is_break(line: str) -> bool:
    """A line that separates two pieces without naming one: a rule (---) or a decorated heading."""
    s = line.strip()
    if not s:
        return False
    if RULE_LINE.match(s):
        return True
    if len(s) > 80:
        return False
    return bool(re.fullmatch(r"={2,}.*={2,}|#{1,6}\s+\S.*|(?:\*\*|__)[^*_]{1,70}(?:\*\*|__):?", s))


def _breaks(lines: list[str]) -> list[int]:
    """Break lines with text before and after them."""
    idx = [i for i, ln in enumerate(lines) if _is_break(ln)]
    return [i for i in idx if any(x.strip() and not _is_break(x) for x in lines[:i])
            and any(x.strip() and not _is_break(x) for x in lines[i + 1:])]


def split(text: str, pieces: list[dict]) -> Result:
    """pieces: [{"key", "channel"}] in task order."""
    res = Result()
    t = normalise(text)
    channels = {(p["channel"], p.get("kind")) for p in pieces}
    lines = t.split("\n")
    markers = _find_markers(lines, channels, len(pieces))
    first_note = None
    if not markers and len(pieces) > 1:
        # a heading that names no channel ("## Course page intro") as the very first line after the
        # preamble: it starts piece 1; the rules for a piece without a heading place the rest
        _, body0, _ = _trim(t)
        head = body0.split("\n")[0].strip() if body0 else ""
        if head and _is_break(head) and not RULE_LINE.match(head) and len(body0.split("\n")) > 1:
            at = next(i for i, ln in enumerate(lines) if ln.strip() == head)
            markers = [Marker(at, 1, None)]
            first_note = f"had a heading that names no channel (“{head[:60]}”); it was used as piece 1"

    if not markers and len(pieces) > 1:
        markers, first_note = _options_by_legend(lines, pieces, channels) or (markers, first_note)

    if not markers:
        pre, body, post = _trim(t)
        if len(pieces) == 1:
            if body:
                res.split.append(_piece(pieces[0], body, pre, post))
            else:
                res.problems.append("the pasted text is empty")
            return res
        blines = body.split("\n")
        br = _breaks(blines)
        if body and len(br) == len(pieces) - 1:
            # no headings, but exactly one separator between each piece: in task order, flagged
            bounds = [-1] + br + [len(blines)]
            parts = ["\n".join(blines[bounds[k] + 1:bounds[k + 1]]).strip() for k in range(len(pieces))]
            if all(parts):
                for k, p in enumerate(pieces):
                    res.split.append(_piece(p, parts[k], pre if k == 0 else "", post if k == len(pieces) - 1 else ""))
                res.problems.append(f"no piece headings found; the {len(pieces)} parts between the separators were "
                                    f"given to the pieces in task order ({', '.join(p['channel'] for p in pieces)}). "
                                    "Check them, or split by hand.")
                return res
        res.problems.append(f"no piece markers found; expected {len(pieces)} pieces "
                            f"({', '.join(p['channel'] for p in pieces)}). Split it by hand.")
        return res

    # which piece each marker starts; a marker that fits none is kept as text of the piece before it
    targets = []
    kept = []
    taken: set[int] = set()
    for m in markers:
        target = _target(m, pieces, taken, res.problems)
        if target is None:
            continue
        if target in taken:
            res.problems.append(f"piece {target + 1} ({pieces[target]['channel']}) appears twice")
            continue
        taken.add(target)
        kept.append(m)
        targets.append(target)
    assigned: dict[int, list] = {}
    for idx, (m, target) in enumerate(zip(kept, targets)):
        end = kept[idx + 1].line_no if idx + 1 < len(kept) else len(lines)
        blines = ([m.inline] if m.inline else []) + lines[m.line_no + 1:end]
        assigned[target] = [m, blines]

    leading_lines = lines[:kept[0].line_no] if kept else lines
    leading = "\n".join(leading_lines).strip()
    notes: dict[int, str] = {}
    in_order = targets == sorted(targets)
    # a piece without a heading, where its place is unambiguous
    if kept and in_order:
        if 0 not in assigned and targets[0] == 1 and leading:
            pre, body, _ = _trim(leading)
            if body:
                assigned[0] = [None, body.split("\n")]
                leading = pre
                notes[0] = "had no heading; the text before the first heading was used for it"
        _email_from_subject(pieces, assigned, notes)
        for i in range(1, len(pieces)):
            if i in assigned or i - 1 not in assigned:
                continue
            nxt = next((j for j in range(i + 1, len(pieces)) if j in assigned), None)
            if nxt is not None and nxt != i + 1:
                continue                     # two pieces missing in a row: no guess
            prev = assigned[i - 1][1]
            br = _breaks(prev)
            if len(br) != 1:
                continue
            b = br[0]
            head = prev[b].strip()
            assigned[i - 1][1] = prev[:b]
            # a heading that names nothing ("# First home, first solicitor") is the next piece's title
            keep = not RULE_LINE.match(head) and not re.fullmatch(r"={2,}.*={2,}", head) and _title_heading(head)
            assigned[i] = [None, prev[b:] if keep else prev[b + 1:]]
            notes[i] = (f"had no heading; the text after “{head[:60]}” was used for it" if not RULE_LINE.match(head)
                        else "had no heading; the text after the separator line was used for it")

    if kept and in_order:
        _last_piece_without_heading(pieces, assigned, notes)
    if first_note and 0 in assigned:
        notes[0] = first_note

    for i, p in enumerate(pieces):
        if i not in assigned:
            res.problems.append(f"piece {i + 1} ({p['channel']}) not found in the pasted text")
            continue
        _, blines = assigned[i]
        pre, body, post = _trim("\n".join(blines))
        is_last = i == max(assigned)
        if not is_last and post:           # a sign-off can only follow the last piece
            body, post = (body + "\n\n" + post).strip(), ""
        if i == min(assigned) and leading:
            pre = (leading + ("\n\n" + pre if pre else "")).strip()
        if not body:
            res.problems.append(f"piece {i + 1} ({p['channel']}) is empty")
            continue
        if i in notes:
            res.problems.append(f"piece {i + 1} ({p['channel']}) {notes[i]}. Check it, or split by hand.")
        res.split.append(_piece(p, body, pre, post))
    if leading and not PREAMBLE.match(leading) and len(leading) > 300:
        res.problems.append("text before the first marker was not used (shown as removed); check it is not a piece")
    return res


OPTION_LINE = re.compile(r"^\s*(?:\*\*|__|#{1,6}\s*)?\s*option\s+(?P<n>\d)\s*(?:\*\*|__)?\s*[:.)\-–—]?\s*$", re.I)


def _options_by_legend(lines: list[str], pieces: list[dict], channels):
    """"Option 1 / Option 2 / Option 3" headings, and a first paragraph that says which is which
    ("Option 1 is for LinkedIn, Option 2 the email, Option 3 X"). Options usually mean alternatives
    of ONE piece, so they count only when the legend names every task channel once, the options
    are numbered 1..N in order and N is the number of pieces. Option k gets the k-th channel named.
    Returns (markers, note) or None."""
    opts = [(i, int(m.group("n"))) for i, ln in enumerate(lines) if (m := OPTION_LINE.match(ln))]
    if [n for _, n in opts] != list(range(1, len(pieces) + 1)):
        return None
    legend = " ".join(lines[:opts[0][0]]).strip()
    if not legend or len(legend) > 400:
        return None
    toks = [w for w in re.split(r"[\s\-_/&+,:;·•|–—.!?()\[\]*]+", legend.lower().replace("’", "'")) if w]
    order = []
    for j in range(len(toks)):
        for size in (3, 2, 1):
            hit = CH.ALIASES.get(tuple(toks[j:j + size]))
            if hit:                        # weak aliases too ("Option 3 X"): the legend must name all
                ch = _asked(hit[0], channels)
                if ch and ch not in order:
                    order.append(ch)
                break
    if sorted(order) != sorted({CH.canonical(p["channel"]) for p in pieces}) or len(order) != len(pieces):
        return None
    markers = [Marker(i, None, order[n - 1]) for i, n in opts]
    return markers, "had an “Option” heading; the first paragraph said which channel each option is for"


def _title_heading(line: str) -> bool:
    """A markdown heading that reads like a title ("First home, first solicitor: what to expect"), not
    one that describes the piece ("Local listing blurb for Maps", "Option 2"): three or more words
    that are not heading words."""
    label, _ = _decorations(line)
    lab = read_label(label) if label else None
    return bool(lab) and lab.n is None and not lab.channels and lab.unknown >= 3


def _target(m: Marker, pieces, taken, problems) -> int | None:
    if m.n is not None:
        i = m.n - 1
        if i >= len(pieces):
            problems.append(f"marker for piece {m.n} but the task has {len(pieces)} pieces")
            return None
        if m.channel and m.channel != CH.canonical(pieces[i]["channel"]):
            problems.append(f"marker {m.n} says {m.channel} but piece {m.n} is {pieces[i]['channel']}")
            return None
        return i
    for i, p in enumerate(pieces):
        if CH.canonical(p["channel"]) == m.channel and i not in taken:
            return i
    problems.append(f"a {m.channel} marker has no {m.channel} piece left to fill")
    return None


def _piece(p: dict, body: str, pre: str, post: str) -> dict:
    if CH.no_markdown(p["channel"]):
        body = strip_markdown(body)
    return {"piece_key": p["key"], "text": body.strip(), "removed_pre": pre, "removed_post": post}


# ---------- the last piece without a heading

SUBJECT_LINE = re.compile(r"^\s*(?:\*\*|__)?\s*subject(?:\s+line)?\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*\S", re.I)
_LETTER_WORDS = (r"thanks(?:\s+again)?|thank\s+you|many\s+thanks|with\s+thanks|best|best\s+wishes|all\s+the\s+best|"
                 r"(?:kind|warm|warmest|best)\s+regards|regards|(?:with\s+)?(?:warm|warmest|kind|best)\s+wishes|cheers|sincerely|yours(?:\s+(?:sincerely|faithfully|truly))?|"
                 r"speak\s+soon|talk\s+soon|see\s+you\s+soon|warmly|take\s+care")
LETTER_SIGNOFF = re.compile(r"^\s*(?:" + _LETTER_WORDS + r")\s*[,!.]?\s*$", re.I)
LETTER_START = re.compile(r"^\s*(?:" + _LETTER_WORDS + r")\b", re.I)
POSTSCRIPT = re.compile(r"^\s*p\.?\s?s\b", re.I)
HASHTAGS = re.compile(r"^(?:#\w+[\s,]*)+$")
TITLE_LINE = re.compile(r"^\s*(?:\*\*|__)?\s*(?:title|headline|h1)\s*(?:\*\*|__)?\s*:\s*\S", re.I)
TITLED = {"blog", "website", "flyer"}
GREETING = re.compile(r"^\s*(?:hi|hello|hey|dear|good\s+(?:morning|afternoon|evening))\b", re.I)


def _title_like(ln: str) -> bool:
    return (8 <= len(ln) <= 80 and ln[:1].isupper() and not re.search(r"[.,:;]\s*$", ln)
            and not (SIGNOFF.match(ln) or LETTER_START.match(ln) or GREETING.match(ln) or SUBJECT_LINE.match(ln)
                     or HASHTAGS.match(ln) or RULE_LINE.match(ln) or POSTSCRIPT.match(ln) or PREAMBLE.match(ln))
            and len(ln.split()) >= 2)


# channels whose piece is one short line: the character budget that shape allows
SHORT_SHAPE = {"sms": 160, "x": 280, "threads": 500, "mastodon": 500, "pinterest": 500, "whatsapp": 1000,
               "google_business": 1500}


def _email_from_subject(pieces: list[dict], assigned: dict, notes: dict) -> None:
    """(a) An email piece without a heading starts at a "Subject:" line inside the piece before it
    (after a blank line, with text before it)."""
    for i in range(len(pieces)):
        if i in assigned or CH.canonical(pieces[i]["channel"]) != "email" or (i - 1) not in assigned:
            continue
        blines = assigned[i - 1][1]
        for j, ln in enumerate(blines):
            if (SUBJECT_LINE.match(ln) and j > 0 and not blines[j - 1].strip()
                    and any(x.strip() for x in blines[:j])):
                assigned[i - 1][1] = blines[:j]
                assigned[i] = [None, blines[j:]]
                notes[i] = "had no heading; the text from its “Subject:” line on was used for it"
                break


def _paras(blines: list[str]) -> list[tuple[int, int]]:
    """[start, end) line ranges of the paragraphs of a block."""
    out, start = [], None
    for i, ln in enumerate(blines):
        if ln.strip() and start is None:
            start = i
        elif not ln.strip() and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(blines)))
    return out


def _last_piece_without_heading(pieces: list[dict], assigned: dict, notes: dict) -> None:
    """Headings are in task order but a piece has none, and the text of the piece before it runs on:
    (a) an email piece starts at a "Subject:" line (after a blank line, with text before it);
    (b) when exactly one piece is missing, the text after a letter sign-off ("Thanks,\\nName" then a
        blank line) is that piece;
    (c) when exactly one piece is missing and it is a one-line channel (sms, x), a last one-line
        paragraph that fits it is that piece.
    Each is reported (a note in `problems`). With two or more pieces missing, (b) and (c) guess nothing.
    (a) runs first (`_email_from_subject`), before the separator rule."""
    miss = [i for i in range(len(pieces)) if i not in assigned]
    if len(miss) != 1:
        return
    i = miss[0]
    k = max((j for j in assigned if j < i), default=None)
    if k is None:
        return
    blines = assigned[k][1]
    ch = CH.canonical(pieces[i]["channel"])
    k_name = pieces[k]["channel"]

    # (b) the text after a letter sign-off
    for j, ln in enumerate(blines):
        if not LETTER_SIGNOFF.match(ln) or not any(x.strip() for x in blines[:j]):
            continue
        e, names = j + 1, 0
        while e < len(blines) and blines[e].strip() and names < 3 and len(blines[e].strip()) <= 60:
            e, names = e + 1, names + 1
        if names == 0 or e >= len(blines) or blines[e].strip():
            continue
        rest = blines[e + 1:]
        body = "\n".join(rest).strip()
        first = re.split(r"\n\s*\n", body)[0] if body else ""
        if not first or SIGNOFF.match(first) or POSTSCRIPT.match(first) or RULE_LINE.match(first):
            continue
        assigned[k][1] = blines[:e]
        assigned[i] = [None, rest]
        notes[i] = f"had no heading; the text after the {k_name} sign-off was used for it"
        return

    # (d) a titled piece (blog, website, flyer) starts at its title: a "Title:" line, or for those
    #     channels one stand-alone title-like line (blank lines around it, no full stop, not a
    #     greeting / sign-off / subject), when there is exactly one such line after the first paragraph
    pr = _paras(blines)
    titles = []
    for a, b in pr[1:]:
        ln = blines[a].strip()
        if TITLE_LINE.match(ln):
            titles.append((a, True))
        elif (ch in TITLED and b - a == 1 and a + 1 < len(blines) and any(x.strip() for x in blines[a + 1:])
              and _title_like(ln)):
            titles.append((a, False))
    explicit = [a for a, ex in titles if ex]
    pick = explicit if len(explicit) == 1 else ([a for a, _ in titles] if len(titles) == 1 else [])
    if len(pick) == 1 and (explicit or ch in TITLED):
        a = pick[0]
        rest = "\n".join(blines[a:]).strip()
        if rest and not SIGNOFF.match(rest):
            assigned[k][1] = blines[:a]
            assigned[i] = [None, blines[a:]]
            notes[i] = f"had no heading; the text from the title “{blines[a].strip()[:60]}” on was used for it"
            return

    # (c) a last one-line paragraph that fits a one-line channel
    if _last_line_fits(pieces, assigned, notes, i, k, blines):
        return
    # (e) the final piece of the task: a greeting paragraph (email / newsletter) or the last paragraph
    _final_paragraph(pieces, assigned, notes, i, k, blines)


def _last_line_fits(pieces, assigned, notes, i, k, blines) -> bool:
    ch = CH.canonical(pieces[i]["channel"])
    k_name = pieces[k]["channel"]
    limit = SHORT_SHAPE.get(ch)
    if limit and pieces[i].get("max_chars"):
        limit = min(limit, int(pieces[i]["max_chars"]))
    if not limit:
        return False
    pr = _paras(blines)
    while pr and SIGNOFF.match(blines[pr[-1][0]]):
        pr.pop()                              # the chatbot's own sign-off stays where it is
    if len(pr) < 2:
        return False
    a, b = pr[-1]
    last = blines[a].strip()
    if (b - a != 1 or len(last) > limit or LETTER_START.match(last) or HASHTAGS.match(last)
            or SUBJECT_LINE.match(last) or RULE_LINE.match(last) or POSTSCRIPT.match(last)):
        return False
    assigned[k][1] = blines[:a]
    assigned[i] = [None, blines[a:]]
    notes[i] = (f"had no heading; the last line of piece {k + 1} ({k_name}) fits a {pieces[i]['channel']} "
                f"message (one line, {limit} characters or fewer) and was used for it")
    return True


LETTER_CHANNELS = {"email", "newsletter"}
LONG_FORM = {"blog", "website"}                      # a page or article is never one leftover paragraph
LONG_KINDS = {"blog", "article", "page", "landing_section", "landing_page"}


def _final_paragraph(pieces, assigned, notes, i, k, blines) -> None:
    """(e) Only the LAST piece of the task has no heading, right after the piece before it (chatbots
    often drop the heading of the last piece and leave a blank line instead):
    - an email / newsletter piece starts at the first later paragraph that opens with a greeting;
    - any other piece (not a blog / web page) is the last paragraph (sign-offs set aside), 40
      characters or more, when it fits the piece's length
      limit and is not hashtags, a sign-off, a letter closing, a subject line or a P.S.
    The piece before it keeps at least one paragraph. Always reported as a note."""
    if i != len(pieces) - 1 or k != i - 1:
        return
    ch = CH.canonical(pieces[i]["channel"])
    k_name = pieces[k]["channel"]
    pr = _paras(blines)
    while pr and SIGNOFF.match("\n".join(blines[pr[-1][0]:pr[-1][1]])):
        pr.pop()
    if len(pr) < 2:
        return
    if ch in LETTER_CHANNELS or pieces[i].get("kind") == "newsletter":
        for a, _ in pr[1:]:
            if GREETING.match(blines[a]):
                assigned[k][1] = blines[:a]
                assigned[i] = [None, blines[a:]]
                notes[i] = (f"had no heading; the text from the greeting “{blines[a].strip()[:40]}” on "
                            "was used for it")
                return
        return                                # a letter with no greeting: no guess
    if ch in LONG_FORM or pieces[i].get("kind") in LONG_KINDS:
        return
    a, b = pr[-1]
    para = "\n".join(blines[a:b]).strip()
    first = blines[a].strip()
    limit = pieces[i].get("max_chars") or SHORT_SHAPE.get(ch)
    if (len(para) < 40 or (limit and len(para) > int(limit)) or HASHTAGS.match(first) or LETTER_START.match(first)
            or SUBJECT_LINE.match(first) or RULE_LINE.match(first) or POSTSCRIPT.match(first)
            or GREETING.match(first)):
        return
    assigned[k][1] = blines[:a]
    assigned[i] = [None, blines[a:]]
    notes[i] = (f"had no heading; the last paragraph of piece {k + 1} ({k_name}) was used for it "
                "(the last piece often loses its heading)")
