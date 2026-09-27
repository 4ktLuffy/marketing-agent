"""Answer providers: official APIs only. Each is enabled only when its key env var is set.

| provider         | API                                                     | sources returned              |
|------------------|---------------------------------------------------------|-------------------------------|
| openai_search    | OpenAI Responses API + `web_search` tool                | `url_citation` annotations    |
| perplexity       | Perplexity Agent API (`/v1/agent`, `web_search` tool)   | `search_results` output items |
| gemini_grounded  | Gemini generateContent + `google_search` grounding      | `groundingMetadata.groundingChunks[].web` |
| groq_knowledge   | Groq chat completions (open model, NO web search)       | none: model knowledge only    |

Keys are read from the environment at call time, sent only in request headers (never in a URL),
and never stored, logged or returned. Provider error bodies are never stored either: they can
echo part of a key.
"""
import os
import time
from dataclasses import dataclass, field

import httpx

from .analysis import citation_domain, urls_in


class ProviderError(Exception):
    """A failed call. The message never contains a response body or a key."""


@dataclass
class Answer:
    text: str
    citations: list[dict] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    searches: int = 0
    reported_cost: float | None = None  # when the API reports the cost itself (Perplexity)


# Prices: USD per 1M input / output tokens and per web-search call or grounded prompt.
# List prices checked 2026-09 (see README "Costs"); override with VIS_<P>_PRICE_IN/OUT/SEARCH.
PROVIDERS = {
    "openai_search": {
        "label": "OpenAI Responses API with web search", "web_search": True,
        "key_env": "VIS_OPENAI_API_KEY", "model_env": "VIS_OPENAI_MODEL", "model": "gpt-5-mini",
        "base_env": "VIS_OPENAI_BASE_URL", "base": "https://api.openai.com/v1",
        "price_in": 0.25, "price_out": 2.0, "price_search": 0.01, "daily_calls": 100,
    },
    "perplexity": {
        # Sonar chat completions were retired on 2026-09-27; the Agent API replaces them and
        # reports the charged cost itself (usage.cost.total_cost), which is what 82 records.
        "label": "Perplexity Agent API with web search", "web_search": True,
        "key_env": "VIS_PERPLEXITY_API_KEY", "model_env": "VIS_PERPLEXITY_MODEL", "model": "perplexity/sonar",
        "base_env": "VIS_PERPLEXITY_BASE_URL", "base": "https://api.perplexity.ai",
        "price_in": 0.25, "price_out": 2.50, "price_search": 0.0025, "daily_calls": 100,
    },
    "gemini_grounded": {
        # Google's Gemini API terms forbid analysing Grounded Results, which is what 82 does, so
        # this provider also needs VIS_GEMINI_TERMS_ACCEPTED=true (see README, "Gemini").
        "label": "Gemini API with Google Search grounding", "web_search": True,
        "ack_env": "VIS_GEMINI_TERMS_ACCEPTED",
        "key_env": "VIS_GEMINI_API_KEY", "model_env": "VIS_GEMINI_MODEL", "model": "gemini-2.5-flash",
        "base_env": "VIS_GEMINI_BASE_URL", "base": "https://generativelanguage.googleapis.com/v1beta",
        "price_in": 0.30, "price_out": 2.50, "price_search": 0.035, "daily_calls": 100,
    },
    "groq_knowledge": {
        "label": "Groq open model, no web search (model knowledge only)", "web_search": False,
        "key_env": "VIS_GROQ_API_KEY", "model_env": "VIS_GROQ_MODEL", "model": "openai/gpt-oss-120b",
        "base_env": "VIS_GROQ_BASE_URL", "base": "https://api.groq.com/openai/v1",
        "price_in": 0.15, "price_out": 0.60, "price_search": 0.0, "daily_calls": 200,
    },
}
NAMES = tuple(PROVIDERS)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def _num(name: str, default: float) -> float:
    try:
        v = float(_env(name, str(default)))
        return v if v >= 0 else default
    except ValueError:
        return default


def key_of(name: str) -> str:
    return _env(PROVIDERS[name]["key_env"])


def acknowledged(name: str) -> bool:
    ack = PROVIDERS[name].get("ack_env")
    return not ack or _env(ack).lower() in ("true", "1", "yes")


def enabled() -> list[str]:
    return [n for n in NAMES if key_of(n) and acknowledged(n)]


def model_of(name: str) -> str:
    p = PROVIDERS[name]
    return _env(p["model_env"], p["model"])


def base_of(name: str) -> str:
    p = PROVIDERS[name]
    return _env(p["base_env"], p["base"]).rstrip("/")


def prefix(name: str) -> str:
    return "VIS_" + name.split("_")[0].upper()


def prices(name: str) -> dict:
    p, pre = PROVIDERS[name], prefix(name)
    return {"in_per_mtok": _num(f"{pre}_PRICE_IN", p["price_in"]),
            "out_per_mtok": _num(f"{pre}_PRICE_OUT", p["price_out"]),
            "per_search": _num(f"{pre}_PRICE_SEARCH", p["price_search"])}


def caps(name: str) -> dict:
    """Daily limits per provider. 0 tokens / 0 USD = no limit on that measure."""
    pre = prefix(name)
    return {"calls": int(_num(f"{pre}_DAILY_CALLS", PROVIDERS[name]["daily_calls"])),
            "tokens": int(_num(f"{pre}_DAILY_TOKENS", 0)),
            "usd": _num(f"{pre}_DAILY_USD", 0)}


def cost(name: str, a: Answer) -> float:
    if a.reported_cost is not None:
        return round(a.reported_cost, 6)
    pr = prices(name)
    return round(a.input_tokens / 1e6 * pr["in_per_mtok"] + a.output_tokens / 1e6 * pr["out_per_mtok"]
                 + a.searches * pr["per_search"], 6)


def describe() -> list[dict]:
    """Public view of every provider: never a key, only whether one is set."""
    return [{"name": n, "label": p["label"], "enabled": n in enabled(), "key_set": bool(key_of(n)),
             "needs": p.get("ack_env") if not acknowledged(n) else None, "web_search": p["web_search"],
             "model": model_of(n), "key_env": p["key_env"], "caps": caps(n), "prices": prices(n)}
            for n, p in PROVIDERS.items()]


# ---------- response parsing (pure; formats from each provider's API reference)


def _cit(url: str, title: str | None = None) -> dict | None:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    c = {"url": url[:2000], "title": (title or "").strip()[:300] or None}
    c["domain"] = citation_domain(c)
    return c


def _dedupe(cits: list) -> list[dict]:
    out, seen = [], set()
    for c in cits:
        if c and c["url"] not in seen:
            seen.add(c["url"])
            out.append(c)
    return out[:50]


def parse_openai(body: dict) -> Answer:
    """Responses API: output[] has `web_search_call` items and a `message` item whose
    content[].type == "output_text" carries `text` and `annotations[]` of
    {type: "url_citation", url, title, start_index, end_index}."""
    texts, cits, searches = [], [], 0
    for item in body.get("output") or []:
        if item.get("type") == "web_search_call":
            searches += 1  # its action.sources (consulted, not cited) are not citations
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") != "output_text":
                continue
            texts.append(part.get("text") or "")
            for a in part.get("annotations") or []:
                if a.get("type") == "url_citation":
                    cits.append(_cit(a.get("url"), a.get("title")))
    u = body.get("usage") or {}
    return Answer("\n".join(texts).strip(), _dedupe(cits), body.get("model") or "",
                  int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0), searches)


def parse_perplexity(body: dict) -> Answer:
    """Agent API (`POST /v1/agent`): output[] has `search_results` items
    ({queries, results: [{id, title, url, snippet, date, source}]}) and a `message` whose
    output_text may carry `url_citation` annotations. usage.cost.total_cost is the charged
    amount in USD. The retired Sonar chat format (choices, citations[], search_results[]) is
    still read, for old fixtures and proxies."""
    texts, cits, searches = [], [], 0
    for item in body.get("output") or []:
        if item.get("type") == "search_results":
            searches += 1
            cits += [_cit(r.get("url"), r.get("title")) for r in item.get("results") or [] if isinstance(r, dict)]
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    texts.append(part.get("text") or "")
                    cits += [_cit(a.get("url"), a.get("title")) for a in part.get("annotations") or []
                             if a.get("type") == "url_citation"]
    if body.get("choices"):  # legacy Sonar chat completion
        texts.append(((body["choices"][0] or {}).get("message") or {}).get("content") or "")
        cits += [_cit(r.get("url"), r.get("title")) for r in body.get("search_results") or [] if isinstance(r, dict)]
        cits += [_cit(u) for u in body.get("citations") or [] if isinstance(u, str)]
        searches = max(searches, 1)
    u = body.get("usage") or {}
    total = (u.get("cost") or {}).get("total_cost") if isinstance(u.get("cost"), dict) else None
    return Answer("\n".join(texts).strip(), _dedupe(cits), body.get("model") or "",
                  int(u.get("input_tokens") or u.get("prompt_tokens") or 0),
                  int(u.get("output_tokens") or u.get("completion_tokens") or 0), searches,
                  float(total) if isinstance(total, (int, float)) else None)


def parse_gemini(body: dict) -> Answer:
    """generateContent: candidates[0].content.parts[].text and
    candidates[0].groundingMetadata.groundingChunks[].web {uri, title}. The uri is a
    vertexaisearch redirect; `title` is the source's domain. `webSearchQueries` lists the
    searches; a response without groundingMetadata was answered without searching."""
    cand = (body.get("candidates") or [{}])[0]
    parts = ((cand.get("content") or {}).get("parts") or [])
    text = "".join(p.get("text") or "" for p in parts if not p.get("thought")).strip()
    gm = cand.get("groundingMetadata") or {}
    cits = [_cit((ch.get("web") or {}).get("uri"), (ch.get("web") or {}).get("title"))
            for ch in gm.get("groundingChunks") or []]
    u = body.get("usageMetadata") or {}
    out = int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0)
    return Answer(text, _dedupe(cits), body.get("modelVersion") or "",
                  int(u.get("promptTokenCount") or 0), out, 1 if gm else 0)


def parse_groq(body: dict) -> Answer:
    """OpenAI-compatible chat completion. No sources: URLs the model writes are recalled from
    training data, not retrieved, so they are kept apart (urls_in_text), never as citations."""
    msg = (((body.get("choices") or [{}])[0]).get("message") or {})
    u = body.get("usage") or {}
    return Answer((msg.get("content") or "").strip(), [], body.get("model") or "",
                  int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), 0)


PARSERS = {"openai_search": parse_openai, "perplexity": parse_perplexity,
           "gemini_grounded": parse_gemini, "groq_knowledge": parse_groq}


# ---------- requests


def max_tokens() -> int:
    return int(_num("VIS_MAX_OUTPUT_TOKENS", 2048)) or 2048


def request_for(name: str, question: str) -> tuple[str, dict, dict]:
    """(url, headers, json) for one question. The key goes in a header, never the URL."""
    key, model, base = key_of(name), model_of(name), base_of(name)
    country = _env("VIS_COUNTRY").upper()[:2]
    if name == "openai_search":
        tool = {"type": "web_search"}
        if country:
            tool["user_location"] = {"type": "approximate", "country": country}
        return (f"{base}/responses", {"Authorization": f"Bearer {key}"},
                {"model": model, "input": question, "tools": [tool], "max_output_tokens": max_tokens()})
    if name == "perplexity":
        body = {"model": model, "input": question, "tools": [{"type": "web_search"}],
                "tool_choice": _env("VIS_PERPLEXITY_TOOL_CHOICE", "auto"), "max_output_tokens": max_tokens()}
        return f"{base}/v1/agent", {"Authorization": f"Bearer {key}"}, body
    if name == "gemini_grounded":
        return (f"{base}/models/{model}:generateContent", {"x-goog-api-key": key},
                {"contents": [{"role": "user", "parts": [{"text": question}]}],
                 "tools": [{"google_search": {}}], "generationConfig": {"maxOutputTokens": max_tokens()}})
    if name == "groq_knowledge":
        body = {"model": model, "messages": [{"role": "user", "content": question}],
                "max_completion_tokens": max_tokens()}
        effort = _env("VIS_GROQ_REASONING_EFFORT", "low")
        if effort and "gpt-oss" in model:
            body["reasoning_effort"] = effort
        return f"{base}/chat/completions", {"Authorization": f"Bearer {key}"}, body
    raise ValueError(f"unknown provider {name}")


def ask(name: str, question: str, client: httpx.Client | None = None) -> Answer:
    """One question to one provider. Retries a 429 or 5xx twice (waiting what Retry-After
    says, at most 30 s). Raises ProviderError without any response body."""
    if name not in enabled():
        raise ProviderError("not enabled (no API key, or its terms flag is not set)")
    url, headers, body = request_for(name, question)
    timeout = _num("VIS_TIMEOUT", 120)
    own = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        for attempt in range(3):
            try:
                r = client.post(url, headers=headers, json=body, timeout=timeout)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise ProviderError(f"unreachable ({type(exc).__name__})") from None
                time.sleep(2)
                continue
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    raise ProviderError("HTTP 200 without JSON") from None
                a = PARSERS[name](data)
                a.model = a.model or model_of(name)
                if not a.text:
                    raise ProviderError("empty answer")
                return a
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                try:
                    wait = float(r.headers.get("retry-after") or 5)
                except ValueError:
                    wait = 5
                time.sleep(min(max(wait, 0.5), _num("VIS_MAX_RETRY_WAIT_S", 30)))
                continue
            raise ProviderError(f"HTTP {r.status_code}")
        raise ProviderError("gave up after retries")
    finally:
        if own:
            client.close()


def text_urls(a: Answer) -> list[str]:
    return urls_in(a.text)
