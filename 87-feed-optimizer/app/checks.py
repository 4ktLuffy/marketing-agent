"""Checks on a proposed title or description, in code, and the rule-based title (no LLM).

A proposal is rejected when it says something the product's own row does not say:
- a number that is in none of the row's fields ("2-pack", "100%", "15W");
- a colour, material, size, gender or age word that the row does not have. When the row fills
  the matching field (color, material, size, gender, age_group), only that field counts, so a
  misleading original title ("Blue" while color says White) cannot be copied;
- a feature or claim word (organic, waterproof, wireless, ...) that the row does not have;
- another product's brand from the same upload, or a well-known brand the row does not name;
- promotional text ("free shipping", "best", "sale", "% off", "buy now"), a price, "!" or
  symbols, ALL CAPS words;
- a title over 150 characters or a description over 5000, HTML or links in a description.

The word lists are short and English. What they miss is listed in the README (Known limits).
"""
import re
import unicodedata

TITLE_MAX = 150
DESCRIPTION_MAX = 5000
FRONT = 70  # Google shows about the first 70 characters of a title in most places

# Fields a proposal may use as evidence: the fields the model is shown.
EVIDENCE_FIELDS = ("title", "description", "brand", "mpn", "color", "size", "material", "pattern", "gender",
                   "age_group", "product_type", "google_product_category", "size_type", "size_system",
                   "product_highlight", "product_detail")
# Numbers may come from these (a numeric google_product_category is an ID, not a fact).
NUMBER_FIELDS = tuple(f for f in EVIDENCE_FIELDS if f != "google_product_category")

COLOURS = set("""red blue green yellow black white gray navy pink purple orange brown beige cream ivory gold
silver bronze teal turquoise maroon burgundy olive khaki tan charcoal coral mint lavender lilac mustard
copper indigo violet magenta cyan aqua taupe camel rust plum emerald sage ochre champagne blush peach
salmon ruby cobalt multicolor sand stone natural clear transparent brass""".split())
MATERIALS = set("""cotton wool merino cashmere alpaca mohair silk linen polyester nylon spandex elastane lycra
leather suede denim canvas bamboo oak walnut pine beech teak acacia maple birch wood steel stainless
aluminum iron copper brass bronze ceramic porcelain stoneware earthenware glass plastic silicone rubber
velvet fleece acrylic viscose rayon modal hemp jute rattan wicker marble granite concrete cork felt
tpu pvc polycarbonate coir sisal wax soy beeswax paraffin enamel titanium gold silver metal""".split())
SIZES = set("xxs xs s m l xl xxl xxxl 2xl 3xl 4xl small medium large petite oversized king queen".split())
GENDER = set("men women unisex male female".split())
AGE = set("kids baby toddler infant newborn adult boys girls".split())
# Features and claims a shopper relies on. Allowed only when the row says them.
CLAIMS = set("""organic vegan vegetarian handmade handcrafted artisan eco sustainable sustainably recycled
recyclable biodegradable compostable waterproof water-resistant windproof breathable wireless bluetooth
rechargeable magnetic magsafe dishwasher microwave washable hypoallergenic antibacterial nontoxic bpa
gluten lactose dairy keto halal kosher fairtrade certified raw""".split())
NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "dozen": "12",
                "pair": "2", "pairs": "2", "twin": "2", "triple": "3"}
# Brands a model likes to add for "compatibility" or fame. Allowed only when the row names them.
KNOWN_BRANDS = set("""apple iphone ipad macbook airpods samsung galaxy google pixel android sony playstation
nintendo xbox microsoft nike adidas puma reebok levi's ikea amazon alexa kindle anker belkin logitech bose
jbl dyson bosch philips lego disney marvel starbucks nespresso keurig tesla gopro fitbit garmin huawei
xiaomi oneplus lenovo dell asus acer canon nikon patagonia""".split())
ACRONYMS = set("hdmi usb led lcd oled qled uhd nfc wifi hepa".split())
STOP = set("""a an and or the of for with in on to by from at as is this that your our its new set pack
size""".split())
# Plural-only nouns: never "singularised" when a product type is added to a title.
PLURAL_ONLY = set("""shorts pants trousers jeans leggings tights glasses sunglasses scissors pliers headphones
earbuds clothes goods oats""".split())

# One spelling per word, so "grey" in a proposal matches "gray" in the row.
SYNONYMS = {"grey": "gray", "multicolour": "multicolor", "multicoloured": "multicolor", "multi": "multicolor",
            "aluminium": "aluminum", "wooden": "wood", "woollen": "wool", "woolen": "wool", "golden": "gold",
            "womens": "women", "women's": "women", "womens'": "women", "ladies": "women", "lady": "women",
            "woman": "women", "mens": "men", "men's": "men", "man": "men", "kids'": "kids", "kid's": "kids",
            "kid": "kids", "children": "kids", "children's": "kids", "child": "kids", "babies": "baby",
            "baby's": "baby", "boy": "boys", "boy's": "boys", "boys'": "boys", "girl": "girls", "girl's": "girls",
            "girls'": "girls", "colour": "color", "organically": "organic", "handcraft": "handcrafted",
            "bamboos": "bamboo"}
# What a field value allows beyond its own words.
EXPANDS = {"s": {"small"}, "m": {"medium"}, "l": {"large"}, "xl": {"large"}, "xxl": {"large"},
           "small": {"s"}, "medium": {"m"}, "large": {"l"},
           "female": {"women", "girls"}, "male": {"men", "boys"},
           "kids": {"boys", "girls"}, "toddler": {"kids", "baby"}, "infant": {"baby"}, "newborn": {"baby"}}
CATEGORY_FIELDS = {"colour": ("color",), "material": ("material",), "size": ("size",),
                   "gender": ("gender",), "age": ("age_group",)}
CATEGORY_WORDS = {"colour": COLOURS, "material": MATERIALS, "size": SIZES, "gender": GENDER, "age": AGE}

PROMO = [re.compile(p) for p in (
    r"\bfree\s+(?:shipping|delivery|gift|returns?|postage|samples?)\b", r"\bbest\b", r"\bbest[- ]?sell(?:ers?|ing)\b",
    r"\btop[- ]rated\b", r"#\s?1\b", r"\bno\.?\s?1\b", r"\bnumber one\b", r"\bsale\b", r"\bdiscount(?:s|ed)?\b",
    r"%\s?off\b", r"\bcheap(?:est|er)?\b", r"\blowest price\b", r"\bbargains?\b", r"\bdeals?\b",
    r"\b(?:buy|shop|order) now\b", r"\blimited (?:time|offer|stock)\b", r"\bhurry\b", r"\bclearance\b",
    r"\bpromo(?:tion)?s?\b", r"\bcoupons?\b", r"\bexclusive\b", r"\bguaranteed?\b", r"\bmust[- ]have\b",
    r"\bamazing\b", r"\bunbeatable\b", r"\bever\b")]
PRICE_RE = re.compile(r"[$€£¥]|\b(?:usd|eur|gbp|chf|aud|cad)\b")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?['’]?")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
HTML_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
URL_RE = re.compile(r"https?://|www\.", re.I)


def fold(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", t).strip().lower()


def norm(word: str) -> str:
    w = word.lower().replace("’", "'")
    w = SYNONYMS.get(w, w)
    return SYNONYMS.get(w.rstrip("'"), w.rstrip("'")) if w.endswith("'") else w


def words(text: str) -> list[str]:
    return [norm(w) for w in WORD_RE.findall(unicodedata.normalize("NFKC", text or ""))]


def tidy(text: str) -> str:
    """One line, single spaces."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()


# A pack count is a claim about how many come in the box, not just a number: "3-Pack", "pack of 3",
# "3 pcs", "6 pairs". It needs a count in the row, not any 3 ("3 m" cable).
QTY_RE = re.compile(r"\b(\d+)\s*[- ]?\s*(?:pack|pk|pcs|pieces|piece|pairs|pair|count|ct)\b"
                    r"|\b(?:pack|set|box|case|bundle) of (\d+)\b", re.I)


def counts(text: str) -> set[str]:
    return {(a or b).lstrip("0") or "0" for a, b in QTY_RE.findall(text or "")}


def _num_forms(tok: str) -> set[str]:
    t = tok.replace(",", ".")
    out = {tok, tok.replace(",", ""), t}
    return out | {f.lstrip("0") or "0" for f in out}


def _evidence(row: dict, fields=EVIDENCE_FIELDS) -> str:
    return " \n ".join(str(row.get(f) or "") for f in fields if row.get(f))


def _allowed(words_: list[str]) -> set[str]:
    out = set(words_)
    for w in words_:
        out |= EXPANDS.get(w, set())
    return out


class Context:
    """What one product's row allows, computed once per product."""

    def __init__(self, row: dict, batch_brands: set[str] | None = None):
        self.row = row
        self.text = _evidence(row)
        self.folded = fold(self.text)
        self.all_words = _allowed(words(self.text))
        self.numbers: set[str] = set()
        for tok in NUM_RE.findall(_evidence(row, NUMBER_FIELDS)):
            self.numbers |= _num_forms(tok)
        for w in words(_evidence(row, NUMBER_FIELDS)):
            if w in NUMBER_WORDS:
                self.numbers.add(NUMBER_WORDS[w])
        self.counts = counts(_evidence(row, NUMBER_FIELDS))
        self.number_words = {w for w in self.all_words if w in NUMBER_WORDS} | {
            w for w, d in NUMBER_WORDS.items() if d in self.numbers}
        # Taxonomy words are product nouns ("Irons", "Glass Jars"), allowed in every category.
        taxonomy = words(" ".join(str(row.get(f) or "") for f in ("product_type", "google_product_category")))
        taxonomy += [_singular(w) for w in taxonomy]
        self.category_allowed = {}
        for cat, fields in CATEGORY_FIELDS.items():
            field_text = " ".join(str(row.get(f) or "") for f in fields).strip()
            self.category_allowed[cat] = (_allowed(words(field_text)) | set(taxonomy)) if field_text else self.all_words
        self.brand = tidy(row.get("brand") or "")
        self.brand_folded = fold(self.brand)
        self.symbols = {c for c in self.text if unicodedata.category(c) in ("So", "Sk")}
        self.anchor = _content(" ".join(str(row.get(f) or "") for f in ("title", "product_type")))
        self.other_brands = {b for b in (fold(x) for x in (batch_brands or set())) if b and not _has(self.folded, b)}
        # ALL CAPS words the row itself writes in capitals outside the title (brand, mpn, description).
        caps_src = _evidence({k: v for k, v in row.items() if k != "title"})
        self.caps_ok = {w.lower() for w in WORD_RE.findall(caps_src) if w.isupper()} | ACRONYMS


def _has(folded_text: str, phrase: str) -> bool:
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", folded_text))


def _reason(code: str, detail: str) -> dict:
    return {"code": code, "detail": detail}


def check(text: str, ctx: Context, kind: str = "title") -> list[dict]:
    """Reasons to reject `text` as this product's title or description. Empty = keep."""
    reasons: list[dict] = []
    limit = TITLE_MAX if kind == "title" else DESCRIPTION_MAX
    if not text.strip():
        return [_reason("empty", f"empty {kind}")]
    if len(text) > limit:
        reasons.append(_reason("too_long", f"{len(text)} characters, the limit is {limit}"))
    folded = fold(text)

    for tok in NUM_RE.findall(text):
        if not _num_forms(tok) & ctx.numbers:
            reasons.append(_reason("number", f"{tok!r} is not in this product's data"))
    for n in sorted(counts(text) - ctx.counts):
        reasons.append(_reason("number", f"a pack of {n} is not in this product's data"))
    raw_words = WORD_RE.findall(unicodedata.normalize("NFKC", text))
    # The brand's own words ("Lumen & Oak", "Black Diamond") are not attribute claims.
    unbranded = text
    if ctx.brand:
        unbranded = re.sub(r"(?<![A-Za-z0-9])" + re.escape(ctx.brand) + r"(?![A-Za-z0-9])", " ", text, flags=re.I)
    seen: set[str] = set()
    for raw in WORD_RE.findall(unicodedata.normalize("NFKC", unbranded)):
        w = norm(raw)
        if w in seen:
            continue
        seen.add(w)
        if w in NUMBER_WORDS and w not in ctx.number_words and w not in ctx.all_words:
            reasons.append(_reason("number", f"{raw!r} is not in this product's data"))
        # A one-letter size (S, M, L) counts only when written as a capital on its own.
        if len(w) == 1 and not raw.isupper():
            continue
        cats = [c for c, ws in CATEGORY_WORDS.items() if w in ws]
        if cats and not any(w in ctx.category_allowed[c] for c in cats):
            where = " or ".join(f"{c} ({', '.join(CATEGORY_FIELDS[c])})" for c in cats)
            reasons.append(_reason(cats[0], f"{raw!r}: this product's {where} does not say it"))
        elif w in CLAIMS and w not in ctx.all_words:
            reasons.append(_reason("claim", f"{raw!r} is not in this product's data"))
        elif w in KNOWN_BRANDS and w not in ctx.all_words:
            reasons.append(_reason("brand_other", f"{raw!r} is a brand this product's data does not name"))
    for b in sorted(ctx.other_brands):
        if _has(folded, b):
            reasons.append(_reason("brand_other", f"{b!r} is another product's brand"))

    brand_ok = ctx.brand_folded
    for p in PROMO:
        m = p.search(folded)
        if m and not (brand_ok and m.group(0) in brand_ok):
            reasons.append(_reason("promo", f"promotional text {m.group(0)!r}"))
    if PRICE_RE.search(folded):
        reasons.append(_reason("price", "a price or currency in the text"))
    sym = [c for c in text if c == "!" or (unicodedata.category(c) in ("So", "Sk", "Cs", "Co") and c not in ctx.symbols)]
    if sym:
        reasons.append(_reason("symbols", f"symbols {''.join(sorted(set(sym)))!r}"))
    letters = [c for c in text if c.isalpha()]
    caps = [w for w in raw_words if len(w) >= 4 and w.isupper() and w.lower() not in ctx.caps_ok]
    if (len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.8) or caps:
        reasons.append(_reason("all_caps", f"ALL CAPS: {' '.join(caps[:4]) or 'the whole text'}"))

    if kind == "title":
        if ctx.brand_folded and not _has(folded, ctx.brand_folded):
            reasons.append(_reason("brand_missing", f"the brand {ctx.brand!r} is not in the title"))
        if "\n" in text or "\t" in text:
            reasons.append(_reason("format", "a title is one line"))
        if ctx.anchor and not (_content(text) & ctx.anchor):
            reasons.append(_reason("unrelated", "no word of the original title or product type"))
    else:
        if HTML_RE.search(text):
            reasons.append(_reason("html", "HTML in a plain-text description"))
        if URL_RE.search(text):
            reasons.append(_reason("url", "a link in the description"))
    return reasons


# ---------- rule-based title (no LLM)


def _singular(word: str) -> str:
    w = word.lower()
    if w in PLURAL_ONLY or len(w) <= 3:
        return word
    if w.endswith("ies"):
        return word[:-3] + "y"
    if w.endswith(("sses", "shes", "ches", "xes", "zes")):
        return word[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _content(text: str) -> set[str]:
    return {_singular(w) for w in words(text) if w not in STOP and len(w) > 1}


def _strip_promo(title: str, ctx: Context) -> str:
    t = title
    for p in PROMO:
        t = re.sub(p.pattern, " ", t, flags=re.I) if not (ctx.brand_folded and p.search(ctx.brand_folded)) else t
    t = re.sub(r"[$€£¥]\s?\d+(?:[.,]\d+)?", " ", t)
    t = "".join(" " if (c == "!" or unicodedata.category(c) in ("So", "Sk", "Cs", "Co")) else c for c in t)
    return t


def _fix_caps(title: str, ctx: Context) -> str:
    def one(m):
        w = m.group(0)
        if len(w) >= 2 and w.isupper() and w.lower() not in ctx.caps_ok and not any(ch.isdigit() for ch in w):
            return w.capitalize()
        return w
    letters = [c for c in title if c.isalpha()]
    whole = len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.8
    return re.sub(r"[A-Za-z]+", one, title) if whole else re.sub(r"\b[A-Z]{4,}\b", one, title)


def _clean_separators(t: str) -> str:
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s*([-|/,:;])(?:\s*[-|/,:;])+\s*", r" \1 ", t)       # "- ," -> "-"
    t = re.sub(r"^[\s\-|/,:;.*]+|[\s\-|/,:;*]+$", "", t)
    return re.sub(r"\s+", " ", t).replace(" ,", ",").strip()


def _drop_conflicts(title: str, ctx: Context, warnings: list[str]) -> str:
    """Remove category words (colour, material, size, gender, age) the row's own fields contradict."""
    out = title
    for raw in WORD_RE.findall(title):
        w = norm(raw)
        if len(w) == 1 and not raw.isupper():
            continue
        cats = [c for c, ws in CATEGORY_WORDS.items() if w in ws]
        if cats and not any(w in ctx.category_allowed[c] for c in cats):
            field = CATEGORY_FIELDS[cats[0]][0]
            warnings.append(f"the original title says {raw!r}, the {field} field says "
                            f"{ctx.row.get(field) or '(empty)'!r}: left out of the rule-based title")
            out = re.sub(r"(?<![A-Za-z0-9'])" + re.escape(raw) + r"(?![A-Za-z0-9])", " ", out)
    return out


def _gender_prefix(row: dict) -> str:
    age = fold(row.get("age_group") or "")
    gender = fold(row.get("gender") or "")
    if age in ("kids", "toddler"):
        return {"male": "Boys'", "female": "Girls'"}.get(gender, "Kids'")
    if age in ("infant", "newborn"):
        return "Baby"
    return {"female": "Women's", "male": "Men's", "unisex": "Unisex"}.get(gender, "")


def rule_title(row: dict, ctx: Context | None = None) -> tuple[str, list[str]]:
    """Brand + gender + the original title, cleaned + product type (if the title is short and
    lacks it) + the colour, size, material and pattern the title does not mention yet. Only words
    from the row. Returns (title, warnings)."""
    ctx = ctx or Context(row)
    warnings: list[str] = []
    core = tidy(row.get("title") or "")
    if ctx.brand:
        core = re.sub(r"(?<![A-Za-z0-9])" + re.escape(ctx.brand) + r"(?![A-Za-z0-9])", " ", core, flags=re.I)
    core = _fix_caps(_strip_promo(core, ctx), ctx)
    core = _clean_separators(_drop_conflicts(core, ctx, warnings))
    have = _content(core)
    head = []
    if ctx.brand:
        head.append(ctx.brand)
    prefix = _gender_prefix(row)
    if prefix and not (set(words(core)) & (GENDER | AGE)):
        head.append(prefix)
    ptype = tidy(str(row.get("product_type") or "").split(">")[-1])
    if ptype and len(core.split()) <= 3 and not (_content(ptype) & have):
        parts = ptype.split()
        parts[-1] = _singular(parts[-1])
        core = f"{core} {' '.join(parts)}".strip()
        have |= _content(ptype)
    attrs = []
    for f in ("color", "size", "material", "pattern"):
        v = tidy(row.get(f) or "")
        if not v or {_singular(w) for w in words(v) if w not in STOP} <= have:
            continue
        if f == "size" and re.fullmatch(r"[A-Za-z]{1,4}|\d+(?:[.,]\d+)?", v):
            v = f"Size {v}"
        attrs.append(v[0].upper() + v[1:])
        have |= {_singular(w) for w in words(v)}
    base = " ".join(head + [core]).strip()
    title = base + (" - " + ", ".join(attrs) if attrs else "")
    while len(title) > TITLE_MAX and attrs:
        attrs.pop()
        title = base + (" - " + ", ".join(attrs) if attrs else "")
    if len(title) > TITLE_MAX:
        title = title[:TITLE_MAX].rsplit(" ", 1)[0].rstrip(" -,")
    return title, warnings


def front_coverage(title: str, row: dict) -> tuple[int, int]:
    """(key attributes within the first 70 characters, key attributes the row has). Key attributes:
    brand, colour, size, material. One counts when all its words are in the first 70 characters."""
    front = set(words(title[:FRONT]))
    have = total = 0
    for f in ("brand", "color", "size", "material"):
        v = tidy(row.get(f) or "")
        if not v:
            continue
        total += 1
        need = {w for w in words(v) if w not in STOP}
        if need and need <= front:
            have += 1
    return have, total
