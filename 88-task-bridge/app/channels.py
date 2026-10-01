"""Channel names and the words people (and chatbots) use for them.

A task names a channel once ("google_business", "email", "website"); a chatbot heading may call
it "Google Business Profile", "GBP", "Google post", "Email newsletter", "Cold email", "Text
message", "Landing page"... `canonical()` maps both sides to one name so they can be compared.
Weak aliases ("text", "web", "reel", "article", "x") only count when nothing stronger is on the
line and never on a bare heading line of their own.
"""
import re

# canonical -> [(alias, strong)]
_TABLE = {
    "linkedin": ["linkedin", "linked in", "linkedin article", "linkedin newsletter"],
    "instagram": ["instagram", "insta", "ig", "instagram reel", "instagram story", ("reel", False), ("reels", False)],
    "facebook": ["facebook", "fb", "facebook page", "facebook post", "fb post", "meta post"],
    "x": ["twitter", "tweet", "twitter x", "x twitter", "x post", "x thread", ("x", False)],
    "threads": ["threads", "threads post"],
    "telegram": ["telegram", "telegram post", "telegram message", "telegram channel", "telegram broadcast",
                 "telegram channel post", ("tg", False)],
    "mastodon": ["mastodon", "toot"],
    "tiktok": ["tiktok", "tik tok", "tiktok caption", "tiktok script"],
    "youtube": ["youtube", ("yt", False), "youtube description", "youtube short", "youtube shorts",
                ("shorts", False)],
    "pinterest": ["pinterest", "pinterest pin", "idea pin", "pin description", ("pin", False), ("pins", False)],
    "email": ["email", "e-mail", "newsletter", "e-newsletter", "email newsletter", "mailer", "mailshot", "e-shot",
              "eshot"],
    "sms": ["sms", "text message", "text messages", "sms message", "sms text", "mobile text", ("text", False),
            ("texts", False)],
    "whatsapp": ["whatsapp", "whatsapp message", "whatsapp broadcast", "whatsapp status", "whatsapp business", ("wa", False)],
    "google_business": ["google business profile", "google business", "google my business", "gbp", "gmb",
                        "google post", "google update", "google business post", "gbp post", "google profile",
                        "google"],
    "website": ["website", "web page", "webpage", "landing page", "product page", "homepage", "home page",
                "web copy", "website copy", "site copy", "service page", ("web", False)],
    "blog": ["blog", "blog post", "blog article", "blog intro", ("article", False)],
    "google_ads": ["google ads", "google ad", "search ad", "search ads"],
    "meta_ad": ["meta ad", "meta ads"],
    "ad": [("ad", False), ("advert", False)],
    "flyer": ["flyer", "flier", "poster", "leaflet", "a5 flyer", "a4 poster"],
}
ALIASES: dict[tuple[str, ...], tuple[str, bool]] = {}
for _canon, _items in _TABLE.items():
    for _it in _items:
        _alias, _strong = (_it, True) if isinstance(_it, str) else _it
        ALIASES[tuple(re.sub(r"[-_/]+", " ", _alias).split())] = (_canon, _strong)
MAX_ALIAS = max(len(k) for k in ALIASES)

NO_MARKDOWN = {"linkedin", "instagram", "facebook", "x", "threads", "mastodon", "tiktok", "sms",
               "google_business", "whatsapp"}


def canonical(channel: str) -> str:
    """"google-business" / "Google Business Profile" / "gbp" -> "google_business"; unknown names
    keep their own (lower-case, underscored) form."""
    words = tuple(re.sub(r"[-_/]+", " ", (channel or "").lower()).split())
    hit = ALIASES.get(words)
    if hit:
        return hit[0]
    return "_".join(words)


def no_markdown(channel: str) -> bool:
    return canonical(channel) in NO_MARKDOWN


def find_aliases(tokens: list[str]) -> tuple[list[tuple[str, bool, int, int]], list[int]]:
    """Channel aliases in a token list, longest first: ([(canonical, strong, start, end)], indexes
    of tokens that are not part of an alias)."""
    found, rest, i = [], [], 0
    while i < len(tokens):
        for n in range(min(MAX_ALIAS, len(tokens) - i), 0, -1):
            hit = ALIASES.get(tuple(tokens[i:i + n]))
            if hit:
                found.append((hit[0], hit[1], i, i + n))
                i += n
                break
        else:
            rest.append(i)
            i += 1
    return found, rest


# A task channel that names a family: a piece on "google" may be a Business Profile post or a
# search ad, so a "Google ad" heading fills it.
FAMILY = {"google": {"google_business", "google_ads"}}


def accepts(task_channel: str, label_channel: str, kind: str | None = None) -> bool:
    """Does a heading naming `label_channel` (canonical) fill a piece on `task_channel`? A piece whose
    kind names a channel of its own ("website", kind "blog" or "article") also takes a heading that
    names that kind ("Blog"): the blog post lives on the website."""
    raw = " ".join(re.sub(r"[-_/]+", " ", (task_channel or "").lower()).split())
    if canonical(task_channel) == label_channel or label_channel in FAMILY.get(raw, ()):
        return True
    return bool(kind) and label_channel in KIND_HOSTS and canonical(kind) == label_channel \
        and canonical(task_channel) in KIND_HOSTS[label_channel]


# a piece kind that is a channel hosted on another one: a blog post / article on the website
KIND_HOSTS = {"blog": {"website"}}
