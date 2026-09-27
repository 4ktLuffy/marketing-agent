"""Pure analysis of one AI answer: who is mentioned, who is cited, where in a ranked list.

No LLM here. Everything is deterministic so a weekly number means the same thing every week.
"""
import math
import re
import unicodedata
from urllib.parse import urlsplit

# ---------- text


def clean(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    return t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


def strip_md(text: str) -> str:
    """Markdown emphasis, links and citation markers out; the words stay."""
    t = re.sub(r"\[([^\]\n]+)\]\((?:https?://)?[^)\s]+\)", r"\1", text)   # [label](url) -> label
    t = re.sub(r"\[\d{1,3}\](?:\[\d{1,3}\])*", "", t)                      # [1][2] citation markers
    t = re.sub(r"【[^】]*】", "", t)                                         # gpt-oss style source markers
    t = re.sub(r"(\*\*|__|\*|`)", "", t)
    return t


# ---------- terms and entities


def host_of(value: str) -> str:
    v = (value or "").strip().lower()
    if not v:
        return ""
    if "://" not in v:
        v = "http://" + v
    host = (urlsplit(v).hostname or "").strip(".")
    return host[4:] if host.startswith("www.") else host


def is_domain(value: str) -> bool:
    return bool(re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,24}", (value or "").strip().lower()))


def term_regex(term: str) -> re.Pattern | None:
    """Whole-word pattern for a brand name, alias or domain.

    - A domain ("rival.example.com") matches as a domain, not inside a longer host.
    - A single word ("Trade") matches case-sensitively as written (also in ALL CAPS), so the
      brand "Trade" is not found in "trade-off" or "fair trade".
    - Several words ("Atlas Coffee Club") match in any case, with any spaces or a hyphen
      between the words, and with a possessive "'s".
    """
    t = clean(term).strip()
    if len(t) < 2:
        return None
    if is_domain(t):
        return re.compile(r"(?<![\w.-])(?:www\.)?" + re.escape(t.lower()) + r"(?![\w-]|\.[a-z0-9])", re.I)
    words = t.split()
    body = r"[\s\-]+".join(re.escape(w) for w in words)
    edge_l, edge_r = r"(?<![\w&])", r"(?!\w)"
    if len(words) == 1 and not any(c.isdigit() for c in t):
        forms = {t, t.upper()}
        return re.compile(edge_l + "(?:" + "|".join(re.escape(f) for f in sorted(forms)) + ")" + edge_r)
    return re.compile(edge_l + body + edge_r, re.I)


def entity(name: str, aliases=(), domains=(), kind="competitor") -> dict:
    """{name, kind, terms, domains}: `terms` are what counts as a mention, `domains` what counts
    as a citation (subdomains included)."""
    doms = sorted({host_of(d) for d in domains if host_of(d)})
    terms, seen = [], set()
    for t in [name, *aliases, *doms]:
        t = clean(str(t or "")).strip()
        if t and t.lower() not in seen and term_regex(t):
            seen.add(t.lower())
            terms.append(t)
    return {"name": name, "kind": kind, "terms": terms, "domains": doms}


def _patterns(ent: dict) -> list[re.Pattern]:
    return [p for p in (term_regex(t) for t in ent["terms"]) if p]


def first_mention(text: str, ent: dict) -> int | None:
    """Character offset of the first mention, or None."""
    t = strip_md(clean(text))
    hits = [m.start() for p in _patterns(ent) for m in [p.search(t)] if m]
    return min(hits) if hits else None


def mentions(text: str, ent: dict) -> bool:
    return first_mention(text, ent) is not None


# ---------- sentences


def sentences(text: str) -> list[str]:
    """Plain sentences: list items, table cells and prose split at . ! ? followed by a space."""
    out = []
    for line in clean(text).splitlines():
        line = line.strip()
        if not line or re.fullmatch(r"\|?\s*:?-{2,}.*", line):
            continue
        # A table row is one statement ("Northwind | $18 / 340 g" -> "Northwind; $18 / 340 g").
        cells = (["; ".join(c.strip() for c in line.strip("|").split("|") if c.strip())]
                 if line.startswith("|") else [line])
        for cell in cells:
            cell = re.sub(r"^(?:#{1,6}\s*|[-*•+]\s+|\d{1,2}[.)]\s+)", "", cell)
            cell = strip_md(cell).strip()
            for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", cell):
                s = s.strip()
                if len(s) >= 8 and re.search(r"[A-Za-z]{3}", s):
                    out.append(s)
    return out


def sentences_about(text: str, brand: dict, products: list[str]) -> list[str]:
    """Sentences that name the brand, plus sentences naming one of its products when the answer
    names the brand at all (so "Their Team Box costs $99" after a brand mention is checked)."""
    if not mentions(text, brand):
        return []
    prod = entity("products", aliases=products, kind="product") if products else None
    out = []
    for s in sentences(text):
        if mentions(s, brand) or (prod and mentions(s, prod)):
            if s not in out:
                out.append(s)
    return out


# ---------- ranked lists

_NUM_ITEM = re.compile(r"^(\s{0,3})(?:#{1,6}\s*)?(?:\*\*)?(\d{1,2})[.)](?:\*\*)?\s+(.+)$")
_BULLET = re.compile(r"^([-*•+])\s+(.+)$")
_TABLE = re.compile(r"^\s*\|(.+)\|\s*$")


def _head(item: str) -> str:
    """The part of a list item that names the option: the bold lead if there is one, else the
    text up to the first dash, colon or sentence end."""
    s = clean(item).strip()
    m = re.match(r"^\s*(?:\*\*|__)(.+?)(?:\*\*|__)", s)
    if m:
        return m.group(1)
    s = strip_md(s)
    return re.split(r"\s[–—-]\s|:\s|\.\s", s, maxsplit=1)[0]


def ranked_lists(text: str) -> list[list[str]]:
    """Every list in the answer as a list of item heads, in order.

    Numbered items ("1.", "2)", "### 3.") start a new list when the number goes back to 1.
    Top-level bullets form a list when the answer has no numbered list. Markdown table rows
    (header and separator skipped) form a list; the first non-numeric cell is the head.
    Indented sub-bullets never count as items."""
    lines = clean(text).splitlines()
    numbered: list[list[str]] = []
    bullets: list[list[str]] = []
    tables: list[list[str]] = []
    cur_b: list[str] = []
    cur_t: list[str] = []
    t_rows = 0
    last_num = 0
    for raw in lines:
        m = _NUM_ITEM.match(raw)
        if m:
            n = int(m.group(2))
            if not numbered or n <= last_num or n == 1:
                numbered.append([])
            numbered[-1].append(_head(m.group(3)))
            last_num = n
            continue
        b = _BULLET.match(raw)  # no leading spaces: top level only
        if b:
            cur_b.append(_head(b.group(2)))
            continue
        if cur_b and raw.strip():
            if not raw.startswith((" ", "\t")):
                bullets.append(cur_b)
                cur_b = []
        tm = _TABLE.match(raw)
        if tm:
            cells = [c.strip() for c in tm.group(1).split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) or not c for c in cells):
                continue  # separator row
            t_rows += 1
            if t_rows == 1:
                continue  # header row
            head = next((c for c in cells if c and not re.fullmatch(r"[#\d.)\s]+", strip_md(c))), "")
            cur_t.append(_head(head))
            continue
        if t_rows and not tm:
            if cur_t:
                tables.append(cur_t)
            cur_t, t_rows = [], 0
    if cur_b:
        bullets.append(cur_b)
    if cur_t:
        tables.append(cur_t)
    lists = [lst for lst in numbered if len(lst) >= 2] + [lst for lst in tables if len(lst) >= 2]
    if not any(len(lst) >= 2 for lst in numbered):
        lists += [lst for lst in bullets if len(lst) >= 2]
    return lists


def list_position(text: str, ent: dict) -> int | None:
    """1-based position of the first list item whose head names the entity, in the first list
    that names it. None when it is in no list."""
    pats = _patterns(ent)
    for lst in ranked_lists(text):
        for i, head in enumerate(lst, 1):
            h = strip_md(head)
            if any(p.search(h) for p in pats):
                return i
    return None


# ---------- citations


def citation_domain(c: dict) -> str:
    """Domain of a cited source. Gemini grounding URIs are redirects
    (vertexaisearch.cloud.google.com/grounding-api-redirect/...) whose `title` is the domain."""
    host = host_of(c.get("url", ""))
    title = (c.get("title") or "").strip().lower()
    if (host.endswith("vertexaisearch.cloud.google.com") or not host) and is_domain(title):
        return host_of(title)
    return host


def domain_matches(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains if d)


def cites(citations: list[dict], ent: dict) -> list[str]:
    """URLs among the citations that are the entity's own pages."""
    return [c.get("url", "") for c in citations if domain_matches(c.get("domain") or citation_domain(c), ent["domains"])]


URL_RE = re.compile(r"https?://[^\s)\]>\"'`|]+")


def urls_in(text: str) -> list[str]:
    out = []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(".,;:!?*")
        if u not in out:
            out.append(u)
    return out[:50]


# ---------- one answer


def analyse(text: str, citations: list[dict], brand: dict, competitors: list[dict]) -> dict:
    cits = [dict(c, domain=c.get("domain") or citation_domain(c)) for c in citations]
    comp = {}
    for c in competitors:
        comp[c["name"]] = {"mentioned": mentions(text, c), "position": list_position(text, c),
                           "cited": bool(cites(cits, c))}
    order = sorted(((first_mention(text, e), e["name"]) for e in [brand, *competitors]
                    if first_mention(text, e) is not None))
    return {
        "brand_mentioned": mentions(text, brand),
        "brand_position": list_position(text, brand),
        "brand_cited": bool(cites(cits, brand)),
        "brand_cited_urls": cites(cits, brand)[:10],
        "mention_order": [name for _, name in order],
        "competitors": comp,
        "cited_domains": sorted({c["domain"] for c in cits if c["domain"]}),
        "lists": len(ranked_lists(text)),
    }


# ---------- statistics


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """95% Wilson score interval for k successes out of n. Unlike p ± 1.96·SE it stays inside
    [0, 1] and is honest for small n and for 0 or n successes."""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def rate(k: int, n: int) -> dict:
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "ci95": wilson(k, n)}
