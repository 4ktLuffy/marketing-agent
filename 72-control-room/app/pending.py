"""Decisions wait UNDO_SECONDS before they are sent, so "Undo" never has to reverse anything.

A decision is `pending` (can be undone), then `sending` (posted to n8n), then it becomes a
result. Items that are pending or sending are hidden from the queue. On shutdown, pending
decisions are sent (the approver made them and did not undo them).
"""
import asyncio
import secrets
import time
from collections import deque
from dataclasses import dataclass, field

from .backends import BackendError


@dataclass
class Decision:
    token: str
    item_id: int
    decision: str
    text: str | None
    reason: str | None
    publish_at: str | None
    due: float
    state: str = "pending"          # pending | sending

    def payload(self) -> dict:
        d = {"id": self.item_id, "decision": self.decision}
        for k in ("text", "reason", "publish_at"):
            v = getattr(self, k)
            if v:
                d[k] = v
        return d


@dataclass
class Result:
    at: float
    item_ids: list[int]
    ok: bool
    lines: list[str] = field(default_factory=list)


class PendingDecisions:
    def __init__(self, backends, reviewer: str, delay: float, clock=time.time):
        self.b, self.reviewer, self.delay, self.clock = backends, reviewer, delay, clock
        self.items: dict[str, Decision] = {}
        self.results: deque[Result] = deque(maxlen=20)
        self._lock = asyncio.Lock()

    def add(self, item_id: int, decision: str, text=None, reason=None, publish_at=None) -> Decision:
        # A new decision on the same item replaces a pending one (the last one wins, as in the form).
        for t, d in list(self.items.items()):
            if d.item_id == item_id and d.state == "pending":
                del self.items[t]
        d = Decision(secrets.token_urlsafe(16), item_id, decision, text, reason, publish_at, self.clock() + self.delay)
        self.items[d.token] = d
        return d

    def undo(self, token: str) -> Decision | None | bool:
        """The undone decision; None if unknown; False if it was already sent."""
        d = self.items.get(token)
        if d is None:
            return None
        if d.state != "pending":
            return False
        del self.items[token]
        return d

    def hidden_ids(self) -> set[int]:
        return {d.item_id for d in self.items.values()}

    async def flush_due(self, now: float | None = None, everything: bool = False) -> list[Result]:
        now = self.clock() if now is None else now
        async with self._lock:
            due = [d for d in self.items.values() if d.state == "pending" and (everything or d.due <= now)]
            if not due:
                return []
            for d in due:
                d.state = "sending"
            try:
                body = await self.b.apply_decisions(self.reviewer, [d.payload() for d in due])
                lines = [str(x) for x in body.get("summary") or []]
                stale = body.get("not_in_review") or []
                if stale:
                    lines.append("Not in review any more, nothing changed: " + ", ".join(f"#{i}" for i in stale))
                failed = body.get("failed") or []
                for f in failed:
                    lines.append(f"Failed: {str(f.get('detail') if isinstance(f, dict) else f)[:300]}")
                res = Result(self.clock(), [d.item_id for d in due], ok=bool(body.get("ok", True)) and not failed and not stale,
                             lines=lines or ["No decisions made."])
            except BackendError as e:
                res = Result(self.clock(), [d.item_id for d in due], ok=False,
                             lines=[f"Not saved ({e.detail}). The items are back in the queue; decide again."])
            finally:
                for d in due:
                    self.items.pop(d.token, None)
            self.results.appendleft(res)
            return [res]

    async def run(self, interval: float = 0.25):
        while True:
            await asyncio.sleep(interval)
            try:
                await self.flush_due()
            except Exception:  # noqa: BLE001 - the loop must survive anything; the result says why
                pass
