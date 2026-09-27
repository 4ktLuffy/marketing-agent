"""Checks applied to one gateway output. Each returns (passed, detail)."""
import json
import re

import httpx

# Thousands separators only before exactly 3 digits ("1,2 or 4 weeks" is three numbers).
NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


INDEX_RE = re.compile(r"^(.*)\[(-?\d+)\]$")

# A thread part or slide the model numbered itself ("1/", "2.", "(3)", "Slide 4:", "... 2/5").
# The workflow numbers them, so model numbering would appear twice.
NUMBERING_RE = re.compile(r"^\s*(\(?\d+\s*[/.):](?!\d)|\d+\s*/\s*\d+|(slide|tweet|part|post)\s*\d+)|\b\d+\s*/\s*\d+\s*$", re.I)

# A call to action asks for one action. Weak proxy: an action verb (or a link / "bio").
CTA_RE = re.compile(r"\b(follow|save|share|comment|subscribe|order|shop|try|start|get|grab|visit|check|join|"
                    r"sign up|book|tell|dm|message|reply|click|tap|learn|read|explore|discover|pick|choose|buy|"
                    r"head|link in bio|bio)\b|https?://", re.I)


def select(output, path: str | None) -> list:
    """Tiny path language: None/'' = whole output; 'a.b', 'posts[*].text', 'headlines[*]',
    'posts[0]', 'slides[-1].title' (one item by index; out of range matches nothing)."""
    if not path:
        return [output]
    values = [output]
    for part in path.split("."):
        star = part.endswith("[*]")
        m = None if star else INDEX_RE.match(part)
        key = part[:-3] if star else (m.group(1) if m else part)
        nxt = []
        for v in values:
            if key:
                if not isinstance(v, dict) or key not in v:
                    continue
                v = v[key]
            if star:
                nxt.extend(v if isinstance(v, list) else [])
            elif m:
                i = int(m.group(2))
                if isinstance(v, list) and -len(v) <= i < len(v):
                    nxt.append(v[i])
            else:
                nxt.append(v)
        values = nxt
    return values


def as_text(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def run_check(check: dict, output, variables: dict, services: dict) -> tuple[bool, str]:
    kind = check["type"]
    targets = select(output, check.get("path"))
    if not targets:
        return False, f"path {check.get('path')!r} matched nothing"

    if kind == "max_chars":
        bad = [len(as_text(t)) for t in targets if len(as_text(t)) > check["value"]]
        return not bad, f"lengths over {check['value']}: {bad}" if bad else "ok"

    if kind == "min_chars":
        bad = [len(as_text(t)) for t in targets if len(as_text(t)) < check["value"]]
        return not bad, f"lengths under {check['value']}: {bad}" if bad else "ok"

    if kind == "contains":
        bad = [t for t in targets if check["value"].lower() not in as_text(t).lower()]
        return not bad, f"{len(bad)} target(s) missing {check['value']!r}" if bad else "ok"

    if kind == "not_contains":
        hits = [w for w in check["values"] for t in targets if w.lower() in as_text(t).lower()]
        return not hits, f"found {sorted(set(hits))}" if hits else "ok"

    if kind == "count":
        n = len(targets)
        lo, hi = check.get("min", 0), check.get("max", 10**9)
        return lo <= n <= hi, f"{n} items (want {lo}..{hi})"

    if kind == "numbers_from_input":
        # Marketing copy must not invent statistics: every number in the output has to
        # appear somewhere in the input variables.
        # The brand profile is part of the model's input too (the gateway injects it).
        source = " ".join(as_text(v) for v in variables.values()) + " " + services.get("brand_summary", "")
        norm = lambda s: {n.replace(",", "") for n in NUMBER_RE.findall(s)}
        allowed = norm(source) | set(map(str, check.get("allow", [])))
        invented = sorted({n for t in targets for n in norm(as_text(t))} - allowed)
        return not invented, f"numbers not in input: {invented}" if invented else "ok"

    if kind == "brand_ok":
        errors = []
        for t in targets:
            r = httpx.post(f"{services['brand']}/check", json={"text": as_text(t), "channel": check.get("channel")}, timeout=10)
            r.raise_for_status()
            errors += [v["detail"] for v in r.json()["violations"] if v["severity"] == "error"]
        return not errors, f"brand errors: {errors}" if errors else "ok"

    if kind == "platform_ok":
        # Items carry their own channel (e.g. posts[*] with .channel and .text).
        problems = []
        for t in targets:
            channel = t.get("channel") if isinstance(t, dict) else check["channel"]
            text = t.get("text") if isinstance(t, dict) else t
            r = httpx.post(f"{services['rules']}/validate", json={"channel": channel.strip().lower(), "text": text}, timeout=10)
            if r.status_code == 422:
                problems.append(f"unknown channel {channel!r}")
                continue
            body = r.json()
            if not body["ok"]:
                problems.append(f"{channel}: {[v['detail'] for v in body['violations']]}")
        return not problems, "; ".join(problems) if problems else "ok"

    if kind == "channels_match":
        want = {c.strip().lower() for c in variables[check["var"]].split(",")}
        got = {as_text(t).strip().lower() for t in targets}
        return got == want, f"channels {sorted(got)} vs requested {sorted(want)}"

    if kind == "no_numbering":
        bad = [as_text(t)[:40] for t in targets if NUMBERING_RE.search(as_text(t))]
        return not bad, f"numbered by the model: {bad}" if bad else "ok"

    if kind == "cta_like":
        bad = [as_text(t)[:60] for t in targets if not CTA_RE.search(as_text(t))]
        return not bad, f"no call to action in: {bad}" if bad else "ok"

    if kind == "equals":
        bad = [t for t in targets if t != check["value"]]
        return not bad, f"expected {check['value']!r}, got {bad}" if bad else "ok"

    raise ValueError(f"unknown check type {kind!r}")
