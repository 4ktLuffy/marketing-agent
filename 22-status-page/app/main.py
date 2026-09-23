"""Status page: polls every stack service's /health and shows one green/red table."""
import asyncio
import html
import os
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="status-page")

# Every service in 01-marketing-stack, by its container name (all listen on 8000).
DEFAULT_SERVICES = [
    "llm-gateway", "brand-service", "knowledge-base", "page-extractor", "rss-watcher",
    "change-monitor", "keyword-suggest", "social-listening", "seo-auditor", "readability",
    "platform-rules", "utm-builder", "link-shortener", "image-cards", "email-renderer",
    "content-calendar", "analytics-ingest", "report-builder", "claim-checker",
    "campaign-service", "learning-service",
]


def services() -> list[tuple[str, str, str]]:
    """(name, base url, url to probe). SERVICES="name=url,..."; a bare name means http://name:8000."""
    raw = os.environ.get("SERVICES", "").strip()
    entries = [e.strip() for e in raw.split(",") if e.strip()] if raw else DEFAULT_SERVICES
    out = []
    for entry in entries:
        name, _, url = entry.partition("=")
        name = name.strip()
        base = (url.strip() or f"http://{name}:8000").rstrip("/")
        out.append((name, base, f"{base}/health"))
    ollama = os.environ.get("OLLAMA_URL", "").strip().rstrip("/")
    if ollama:
        out.append(("ollama", ollama, f"{ollama}/api/tags"))
    return out


def timeout() -> float:
    return float(os.environ.get("CHECK_TIMEOUT", "3"))


async def check(client: httpx.AsyncClient, name: str, base: str, probe: str) -> dict:
    start = time.perf_counter()
    error = None
    try:
        r = await client.get(probe)
        ok = r.status_code == 200
        if not ok:
            error = f"HTTP {r.status_code}"
    except httpx.TimeoutException:
        ok, error = False, f"timeout after {timeout():g}s"
    except httpx.HTTPError as e:
        ok, error = False, f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
    latency = round((time.perf_counter() - start) * 1000)
    return {"name": name, "url": base, "ok": ok, "latency_ms": latency, "error": error}


async def run_checks() -> dict:
    async with httpx.AsyncClient(timeout=timeout(), follow_redirects=False) as client:
        results = await asyncio.gather(*(check(client, *s) for s in services()))
    return {
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "all_ok": all(r["ok"] for r in results),
        "services": list(results),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
async def status():
    return await run_checks()


@app.get("/", response_class=HTMLResponse)
async def page():
    data = await run_checks()
    up = sum(r["ok"] for r in data["services"])
    rows = "".join(
        f'<tr><td><span class="dot {"up" if r["ok"] else "down"}"></span>'
        f'{"up" if r["ok"] else "down"}</td>'
        f'<td>{html.escape(r["name"])}</td><td class="url">{html.escape(r["url"])}</td>'
        f'<td class="num">{r["latency_ms"]} ms</td><td>{html.escape(r["error"] or "")}</td></tr>'
        for r in data["services"]
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Marketing stack status</title>
<style>
:root{{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e3e2de;--up:#0ca30c;--down:#d03b3b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--line:#383835}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
main{{max-width:900px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}}p{{color:var(--ink2);margin:0 0 16px}}
.wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{color:var(--ink2);font-size:13px}}.num{{text-align:right;font-variant-numeric:tabular-nums}}
.url{{color:var(--ink2)}}.dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}}
.up{{background:var(--up)}}.down{{background:var(--down)}}
</style></head>
<body><main>
<h1>Marketing stack status</h1>
<p>{up} of {len(data["services"])} up · checked {data["checked_at"]} · refreshes every 30 s</p>
<div class="wrap"><table>
<thead><tr><th>State</th><th>Service</th><th>URL</th><th class="num">Latency</th><th>Error</th></tr></thead>
<tbody>{rows}</tbody></table></div>
</main></body></html>"""
