"""Enrichment from the lead's OWN company website only.

Order: pick the domain (never a free-mail provider) -> robots.txt -> homepage + about page
through the page extractor (07, SSRF-guarded) -> `lead_enrich` prompt via the gateway (03)
-> every quote must be an exact part of the fetched text -> claim checker (44) with the page
text as context -> unsupported statements are dropped. No LinkedIn, no data brokers, no
social networks, no search engines: nothing but the company's own pages.
"""
import ipaddress
import os
import re
import time
import urllib.robotparser
from urllib.parse import urlsplit

import httpx

from app import safe_http

ROBOTS_AGENT = "marketing-agent"
USER_AGENT = "marketing-agent/1.0 (+lead-hub)"
MAX_ROBOTS_BYTES = 512 * 1024
PAGE_CHARS = 8000
ABOUT_PATHS = ("/about", "/about-us", "/company")

# Free and consumer mail providers: their domain says nothing about the lead's company.
FREE_MAIL = frozenset("""
gmail.com googlemail.com yahoo.com yahoo.co.uk yahoo.fr yahoo.de yahoo.es yahoo.it ymail.com
rocketmail.com hotmail.com hotmail.co.uk hotmail.fr hotmail.de hotmail.it hotmail.es outlook.com
outlook.de outlook.fr live.com live.co.uk live.fr live.de msn.com aol.com aim.com icloud.com me.com
mac.com proton.me protonmail.com protonmail.ch pm.me tutanota.com tutanota.de tuta.io
gmx.com gmx.de gmx.net gmx.at gmx.ch web.de t-online.de freenet.de posteo.de mailbox.org
yandex.com yandex.ru ya.ru mail.ru inbox.ru bk.ru list.ru rambler.ru zoho.com zohomail.com
fastmail.com fastmail.fm hey.com mail.com email.com usa.com qq.com 163.com 126.com sina.com
naver.com daum.net hanmail.net orange.fr wanadoo.fr free.fr laposte.net sfr.fr libero.it
virgilio.it alice.it tiscali.it seznam.cz wp.pl o2.pl interia.pl onet.pl btinternet.com
sky.com virginmedia.com talktalk.net ntlworld.com comcast.net verizon.net att.net sbcglobal.net
bellsouth.net cox.net charter.net earthlink.net optonline.net rediffmail.com duck.com
""".split())


# Never fetched, even when a lead types one in as "company website": social networks,
# professional networks and link-in-bio hosts are not the company's own site (and LinkedIn's
# terms forbid automated access).
NEVER_FETCH = frozenset("""
linkedin.com lnkd.in facebook.com fb.com instagram.com x.com twitter.com t.co tiktok.com
youtube.com youtu.be threads.net pinterest.com reddit.com tumblr.com snapchat.com
xing.com crunchbase.com zoominfo.com apollo.io clearbit.com rocketreach.co lusha.com
linktr.ee bio.link beacons.ai whatsapp.com wa.me t.me telegram.me discord.gg discord.com
""".split())


def never_fetch(domain: str) -> bool:
    d = domain.lower()
    return any(d == n or d.endswith("." + n) for n in NEVER_FETCH)


class EnrichError(Exception):
    """A dependency failed; the message is safe to store and show (no secrets, no bodies)."""


def extra_free_mail() -> set[str]:
    return {d.strip().lower() for d in os.environ.get("FREE_MAIL_EXTRA", "").split(",") if d.strip()}


def is_free_mail(domain: str) -> bool:
    return domain.lower() in FREE_MAIL or domain.lower() in extra_free_mail()


DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")


def normalize_domain(value: str | None) -> str | None:
    """"https://www.Acme.io/about" -> "acme.io". None when it is not a plain host name."""
    if not value:
        return None
    v = value.strip().lower()
    if "://" not in v:
        v = "http://" + v
    try:
        host = urlsplit(v).hostname or ""
    except ValueError:
        return None
    host = host.rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    try:
        ipaddress.ip_address(host)
        return None  # an IP address is not a company website
    except ValueError:
        pass
    return host if DOMAIN_RE.match(host) else None


def pick_domain(email: str, given: str | None) -> tuple[str | None, str]:
    """(domain, why). The email's domain wins unless it is free mail; then the domain the
    lead typed into the form, if any (also never free mail)."""
    email_domain = normalize_domain(email.rsplit("@", 1)[-1])
    if email_domain and not is_free_mail(email_domain) and not never_fetch(email_domain):
        return email_domain, "email"
    g = normalize_domain(given)
    if g and never_fetch(g):
        return None, "social network or data broker domain: never fetched"
    if g and not is_free_mail(g):
        return g, "given"
    if email_domain and is_free_mail(email_domain):
        return None, "free mail provider and no company domain given"
    return None, "no company domain"


def site_base(domain: str) -> str:
    """Base URL of the company site. SITE_URL_TEMPLATE exists for local tests only
    (e.g. http://{domain}:8190); its host must still be the domain."""
    template = os.environ.get("SITE_URL_TEMPLATE") or "https://{domain}"
    base = template.format(domain=domain).rstrip("/")
    if (urlsplit(base).hostname or "") != domain:
        raise EnrichError("SITE_URL_TEMPLATE must keep {domain} as the host")
    return base


def _origin(url: str) -> tuple[str, int | None]:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return parts.scheme, -1
    return parts.scheme, port or {"http": 80, "https": 443}.get(parts.scheme)


def same_site(url: str, domain: str, base: str | None = None) -> bool:
    """The company's own site: its host (or www.) and, given the base, the same scheme and port.
    A redirect to another port of the same name is not the site (security review 2026-09-28,
    LH-3: with DNS rebinding that name can point at an internal service)."""
    host = (urlsplit(url).hostname or "").lower()
    if host != domain and host != "www." + domain:
        return False
    return base is None or _origin(url) == _origin(base)


# ---------- robots.txt (07 has no robots check, so the hub reads it itself, guarded)
# The guard is app/safe_http.py, the same file as 07's: GuardedClient checks every address of
# the host and connects only to a checked one (security review X-1: no DNS-rebinding gap).

_is_public = safe_http._is_public  # 07's rule, incl. IPv6 forms that embed an IPv4 (LH-3)


def _public_host(host: str) -> bool:
    """Every address of host is public (ALLOW_PRIVATE_URLS=true switches the check off)."""
    try:
        safe_http.vet(f"http://[{host}]/" if ":" in host else f"http://{host}/")
    except (safe_http.BlockedURL, safe_http.FetchError):
        return False
    return True


def robots(base: str, domain: str) -> urllib.robotparser.RobotFileParser | None:
    """Parsed robots.txt, or None when there is none (404 etc. = everything allowed).
    Raises EnrichError when the site forbids reading robots.txt or is unreachable/blocked."""
    url = base + "/robots.txt"
    try:
        with safe_http.GuardedClient(timeout=10, headers={"User-Agent": USER_AGENT}) as client:
            try:
                resp = client.get(url)
            except (safe_http.BlockedURL, safe_http.FetchError) as exc:
                raise EnrichError(f"{domain} does not resolve to a public address") from exc
            for _ in range(2):
                if not resp.is_redirect:
                    break
                nxt = str(resp.url.join(resp.headers.get("location", "")))
                if not same_site(nxt, domain, base):
                    return None  # robots.txt moved off-site: treat as none
                try:
                    resp = client.get(nxt)  # checked and pinned again
                except (safe_http.BlockedURL, safe_http.FetchError):
                    return None
    except httpx.HTTPError as exc:
        raise EnrichError(f"site unreachable ({type(exc).__name__})") from exc
    if resp.status_code in (401, 403):
        raise EnrichError(f"robots.txt returned HTTP {resp.status_code}: not reading the site")
    if resp.status_code >= 500:
        raise EnrichError(f"robots.txt returned HTTP {resp.status_code}: try again later")
    if resp.status_code >= 400 or resp.is_redirect:
        return None
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(resp.text[:MAX_ROBOTS_BYTES].splitlines())
    return rp


# ---------- services


def _key_headers() -> dict:
    key = os.environ.get("INTERNAL_API_KEY")
    return {"X-API-Key": key} if key else {}


def extract(url: str) -> dict | None:
    """07 /extract. None for a page that doesn't exist or can't be read (502/422)."""
    base = os.environ.get("EXTRACTOR_URL", "http://page-extractor:8000").rstrip("/")
    try:
        r = httpx.post(f"{base}/extract", json={"url": url}, timeout=40)
    except httpx.HTTPError as exc:
        raise EnrichError(f"page extractor unreachable ({type(exc).__name__})") from exc
    if r.status_code in (422, 502):
        return None
    if r.status_code != 200:
        raise EnrichError(f"page extractor returned HTTP {r.status_code}")
    return r.json()


def gateway(prompt: str, variables: dict) -> dict:
    base = os.environ.get("GATEWAY_URL", "http://llm-gateway:8000").rstrip("/")
    try:
        r = httpx.post(f"{base}/v1/run", json={"prompt": prompt, "vars": variables},
                       headers={**_key_headers(), "X-Caller": "80 lead hub"},
                       timeout=float(os.environ.get("GATEWAY_TIMEOUT", "300")))
    except httpx.HTTPError as exc:
        raise EnrichError(f"gateway unreachable ({type(exc).__name__})") from exc
    if r.status_code != 200:
        raise EnrichError(f"gateway returned HTTP {r.status_code} for prompt {prompt}")
    out = r.json().get("output")
    if not isinstance(out, dict):
        raise EnrichError(f"gateway returned no JSON object for prompt {prompt}")
    return out


def claim_check(text: str, context: str, extra_facts: list[str] | None = None) -> dict:
    base = os.environ.get("CLAIMS_URL", "http://claim-checker:8000").rstrip("/")
    body = {"text": text, "context": context[:20000]}
    if extra_facts:
        body["extra_facts"] = extra_facts[:50]
    try:
        r = httpx.post(f"{base}/verify", json=body, headers=_key_headers(),
                       timeout=float(os.environ.get("CLAIMS_TIMEOUT", "600")))
    except httpx.HTTPError as exc:
        raise EnrichError(f"claim checker unreachable ({type(exc).__name__})") from exc
    if r.status_code != 200:
        raise EnrichError(f"claim checker returned HTTP {r.status_code}")
    return r.json()


# ---------- text helpers


def norm(text: str) -> str:
    t = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(t.split()).casefold()


def supported_sentences(sentences: list[str], result: dict) -> tuple[set[int], list[str]]:
    """Indexes of `sentences` the checker supported. A sentence it split differently or did
    not return counts as unsupported: an empty answer never means "fine"."""
    ok = {norm(c["claim"]) for c in result.get("claims", []) if c.get("supported")}
    bad = {norm(c["claim"]) for c in result.get("claims", []) if not c.get("supported")}
    bad_numbers = [u for u in result.get("unsupported", []) if u.startswith("the number ")]
    keep, dropped = set(), []
    for i, s in enumerate(sentences):
        n = norm(s)
        if n in ok and n not in bad and not any(u[len("the number "):] in s for u in bad_numbers):
            keep.add(i)
        else:
            dropped.append(s)
    return keep, dropped


def sentence(text: str) -> str:
    t = " ".join(text.split()).strip()
    return t if t.endswith((".", "!", "?")) else t + "."


# ---------- the pipeline step


def enrich_domain(domain: str) -> dict:
    """Fetch and summarise one company site. Returns the stored enrichment record."""
    delay = float(os.environ.get("ENRICH_DELAY_S", "1"))
    base = site_base(domain)
    rp = robots(base, domain)
    pages, skipped = [], []
    for path in ("/",) + ABOUT_PATHS:
        if path != "/" and any(p["path"] != "/" for p in pages):
            break  # one about page is enough
        url = base + path
        if rp is not None and not rp.can_fetch(ROBOTS_AGENT, url):
            skipped.append({"url": url, "why": "disallowed by robots.txt"})
            continue
        if pages or skipped:
            time.sleep(delay)
        page = extract(url)
        if page is None:
            if path == "/":
                skipped.append({"url": url, "why": "could not be read"})
            continue
        final = page.get("url") or url
        if not same_site(final, domain, base):
            skipped.append({"url": url, "why": "redirected off the company domain"})
            continue
        text = (page.get("text") or "")[:PAGE_CHARS]
        if text.strip():
            pages.append({"path": path, "url": final, "title": page.get("title"), "text": text})
    record = {"domain": domain, "sources": [p["url"] for p in pages], "skipped": skipped,
              "industry": "", "sells": "", "facts": [], "size_hints": [], "dropped": []}
    if not pages:
        record["note"] = "no readable pages on the company site"
        return record

    corpus = "\n\n".join(f"SOURCE {p['url']}\n{p['text']}" for p in pages)
    page_text = "\n".join(p["text"] for p in pages)
    ntext = norm(page_text)
    out = gateway("lead_enrich", {"domain": domain, "pages": corpus})
    sources = set(record["sources"])

    def src(u):
        return u if u in sources else record["sources"][0]

    facts, hints, dropped = [], [], []
    for f in out.get("facts") or []:
        q = (f.get("quote") or "").strip()
        if not q or norm(q) not in ntext:
            dropped.append({"text": f.get("text", ""), "why": "quote is not on the page"})
            continue
        facts.append({"text": sentence(f.get("text", "")), "quote": q, "source_url": src(f.get("source_url"))})
    for h in out.get("size_hints") or []:
        q = (h.get("quote") or "").strip()
        if not q or norm(q) not in ntext:
            dropped.append({"text": h.get("statement", ""), "why": "size quote is not on the page"})
            continue
        hints.append({"statement": h.get("statement", "").strip(), "quote": q, "source_url": src(h.get("source_url"))})

    # Claim check: every statement, in sentences, against the page text.
    checks = [f["text"] for f in facts]
    industry, sells = (out.get("industry") or "").strip(), (out.get("sells") or "").strip()
    if industry:
        checks.append(sentence(f"The company works in {industry}"))
    if sells:
        checks.append(sentence(f"The company sells {sells}"))
    keep: set[int] = set()
    if checks:
        # One statement per line: the checker splits on sentence ends and new lines.
        result = claim_check("\n".join(checks), page_text)
        keep, flagged = supported_sentences(checks, result)
        dropped += [{"text": s, "why": "claim checker: not supported by the pages"} for s in flagged]
    n = len(facts)
    record["facts"] = [f for i, f in enumerate(facts) if i in keep]
    idx = n
    if industry:
        record["industry"] = industry if idx in keep else ""
        idx += 1
    if sells:
        record["sells"] = sells if idx in keep else ""
    record["size_hints"] = hints
    record["dropped"] = dropped
    return record
