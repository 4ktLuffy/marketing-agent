"""What the gateway is doing: calls in flight and the last few hundred calls, in memory.

Metadata only. The log never holds prompt text, vars or model output: a call is its id, times,
prompt name, caller, model, provider, outcome (an error *kind*, never a message that could quote
content), retries and token counts. Nothing survives a restart.
"""
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlsplit

_CALLER_BAD = re.compile(r"[^\w .:/()#·+-]", re.U)
SPARK = 30          # durations kept per model for the sparkline


def clean_caller(value: str | None) -> str:
    """X-Caller -> a short safe label ("26 Social writer"); "unknown" when absent or empty."""
    v = _CALLER_BAD.sub("", re.sub(r"\s+", " ", str(value or "")))
    v = re.sub(r" +", " ", v).strip()[:80].strip()
    return v or "unknown"


def provider_label(llm_provider: str, openai_base_url: str) -> str:
    """"ollama", or the hosted API's host name only (never a path, port or key)."""
    if llm_provider != "openai":
        return "ollama"
    host = urlsplit(openai_base_url or "").hostname
    return (host or "openai")[:100]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(0.95 * len(s)) - 1))]


class ActivityLog:
    def __init__(self, size: int = 500):
        self.size = max(1, size)
        self._lock = threading.Lock()
        self._next = 1
        self._running: dict[int, dict] = {}
        self._recent: deque = deque(maxlen=self.size)
        self._day = ""
        self._models: dict[tuple[str, str], dict] = {}   # today's totals per (model, provider)

    def start(self, prompt: str, caller: str, model: str, provider: str) -> int:
        now = time.time()
        with self._lock:
            cid = self._next
            self._next += 1
            self._running[cid] = {"id": cid, "started": now, "prompt": prompt, "caller": clean_caller(caller),
                                  "model": model, "provider": provider}
        return cid

    def finish(self, cid: int, *, ok: bool, error: str | None = None, retries: int = 0,
               tokens_in: int | None = None, tokens_out: int | None = None) -> None:
        now = time.time()
        with self._lock:
            call = self._running.pop(cid, None)
            if call is None:
                return
            ms = int((now - call["started"]) * 1000)
            row = {"id": cid, "started_at": _iso(call["started"]), "finished_at": _iso(now), "duration_ms": ms,
                   "prompt": call["prompt"], "caller": call["caller"], "model": call["model"],
                   "provider": call["provider"], "ok": ok, "error": None if ok else (error or "error"),
                   "retries": max(0, int(retries)), "tokens_in": tokens_in or None, "tokens_out": tokens_out or None}
            self._recent.append(row)
            today = _day(now)
            if today != self._day:
                self._day, self._models = today, {}
            m = self._models.setdefault((call["model"], call["provider"]), {
                "calls": 0, "failures": 0, "ms": [], "tokens_in": 0, "tokens_out": 0, "last_at": None})
            m["calls"] += 1
            m["failures"] += 0 if ok else 1
            m["ms"].append(ms)
            del m["ms"][:-2000]                     # enough for avg / p95 on a busy day
            m["tokens_in"] += tokens_in or 0
            m["tokens_out"] += tokens_out or 0
            m["last_at"] = row["finished_at"]

    def snapshot(self, since: int = 0, limit: int = 100) -> dict:
        now = time.time()
        with self._lock:
            running = [{"id": c["id"], "started_at": _iso(c["started"]), "elapsed_ms": int((now - c["started"]) * 1000),
                        "prompt": c["prompt"], "caller": c["caller"], "model": c["model"], "provider": c["provider"]}
                       for c in sorted(self._running.values(), key=lambda c: c["id"])]
            recent = [dict(r) for r in reversed(self._recent) if r["id"] > since][:max(0, limit)]
            sparks: dict[tuple[str, str], list[int]] = {}
            for r in reversed(self._recent):
                s = sparks.setdefault((r["model"], r["provider"]), [])
                if len(s) < SPARK:
                    s.append(r["duration_ms"])
            today = _day(now)
            stats = self._models if self._day == today else {}
            keys = list(stats) + [k for k in sparks if k not in stats]
            models = []
            for key in keys:
                m = stats.get(key) or {"calls": 0, "failures": 0, "ms": [], "tokens_in": 0, "tokens_out": 0,
                                       "last_at": None}
                models.append({"model": key[0], "provider": key[1], "calls_today": m["calls"],
                               "failures_today": m["failures"],
                               "avg_ms": int(sum(m["ms"]) / len(m["ms"])) if m["ms"] else None,
                               "p95_ms": _p95(m["ms"]), "tokens_in": m["tokens_in"], "tokens_out": m["tokens_out"],
                               "last_at": m["last_at"], "recent_ms": list(reversed(sparks.get(key, [])))})
            models.sort(key=lambda m: (-m["calls_today"], m["model"]))
            return {"day": today, "size": self.size, "last_id": self._next - 1, "running": running,
                    "recent": recent, "models": models}
