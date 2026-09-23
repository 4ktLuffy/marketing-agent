"""Keyword suggest: autocomplete ideas from Google and DuckDuckGo, no API keys."""
import asyncio
import json
import os
import string
import time

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="keyword-suggest")

GOOGLE_URL = "https://suggestqueries.google.com/complete/search"
DDG_URL = "https://duckduckgo.com/ac/"
USER_AGENT = "Mozilla/5.0 (compatible; marketing-agent-keyword-suggest/1.0)"

# Question words read naturally before the seed, the rest after it.
PREFIX_MODIFIERS = ["how", "what", "why", "best"]
SUFFIX_MODIFIERS = ["vs", "for", "near me", "cheap", "without"] + list(string.ascii_lowercase)

CONCURRENCY = int(os.getenv("CONCURRENCY", "5"))
TIMEOUT_S = float(os.getenv("TOTAL_TIMEOUT_S", "20"))
CACHE_TTL_S = int(os.getenv("CACHE_TTL_S", "3600"))
_cache: dict[tuple, tuple[float, dict]] = {}


class SuggestRequest(BaseModel):
    seed: str = Field(min_length=1, max_length=100)
    expand: bool = True
    lang: str = Field("en", pattern=r"^[A-Za-z-]{2,10}$")
    country: str = Field("us", pattern=r"^[A-Za-z]{2}$")


def parse_suggestions(data) -> list[str]:
    """Both sources answer [query, [suggestions], ...]; old DDG answers [{"phrase": ...}]."""
    if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
        return [s for s in data[1] if isinstance(s, str)]
    if isinstance(data, list):
        return [d["phrase"] for d in data if isinstance(d, dict) and isinstance(d.get("phrase"), str)]
    raise ValueError("unexpected response format")


async def fetch(client: httpx.AsyncClient, source: str, q: str, lang: str, country: str) -> list[str]:
    if source == "google":
        params = {"client": "firefox", "hl": lang, "gl": country, "q": q}
        r = await client.get(GOOGLE_URL, params=params)
    else:
        r = await client.get(DDG_URL, params={"q": q, "type": "list"})
    r.raise_for_status()
    return parse_suggestions(json.loads(r.text))  # r.text honours a non-utf8 charset


def build_queries(seed: str, expand: bool) -> list[tuple[str, str]]:
    """(query, modifier) pairs, the plain seed first."""
    queries = [(seed, "")]
    if expand:
        queries += [(f"{m} {seed}", m) for m in PREFIX_MODIFIERS]
        queries += [(f"{seed} {m}", m) for m in SUFFIX_MODIFIERS]
    return queries


async def collect(req: SuggestRequest) -> dict:
    seed = " ".join(req.seed.split())
    sem = asyncio.Semaphore(CONCURRENCY)
    jobs = [(q, mod, src) for q, mod in build_queries(seed, req.expand) for src in ("google", "duckduckgo")]

    async with httpx.AsyncClient(timeout=8, headers={"User-Agent": USER_AGENT}) as client:
        async def run(q, src):
            async with sem:
                return await fetch(client, src, q, req.lang, req.country)

        tasks = [asyncio.create_task(run(q, src)) for q, _, src in jobs]
        await asyncio.wait(tasks, timeout=TIMEOUT_S)

    keywords, seen = [], set()
    failures: dict[str, list[str]] = {}
    for (q, mod, src), task in zip(jobs, tasks):
        if not task.done():
            task.cancel()
            failures.setdefault(src, []).append("timed out")
            continue
        if task.exception():
            failures.setdefault(src, []).append(repr(task.exception())[:200])
            continue
        for kw in task.result():
            kw = " ".join(kw.split())
            if kw and kw.lower() not in seen:
                seen.add(kw.lower())
                keywords.append({"keyword": kw, "source": src, "modifier": mod})

    # One entry per failing source, not one per query.
    errors = [
        {"source": src, "error": msgs[0], "failed_queries": len(msgs)}
        for src, msgs in failures.items()
    ]
    return {"seed": seed, "keywords": keywords, "count": len(keywords), "errors": errors}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/suggest")
async def suggest(req: SuggestRequest):
    key = (" ".join(req.seed.split()).lower(), req.expand, req.lang.lower(), req.country.lower())
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    result = await collect(req)
    if not result["errors"]:  # don't pin a partial answer for an hour
        _cache[key] = (time.monotonic(), result)
    return result
