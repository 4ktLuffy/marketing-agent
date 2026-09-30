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

CHANGED_LINE = "#{id}: NOT approved: changed since you looked — reopen the card"


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
    # body_sha256 of the text the card showed: 19 approves only that text (409 otherwise).
    seen_sha256: str | None = None
    # Who decided (the logged-in person's display name); decisions are sent per reviewer.
    reviewer: str | None = None

    def payload(self) -> dict:
        d = {"id": self.item_id, "decision": self.decision}
        for k in ("text", "reason", "publish_at", "seen_sha256"):
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

    def add(self, item_id: int, decision: str, text=None, reason=None, publish_at=None, seen_sha256=None,
            reviewer: str | None = None) -> Decision:
        # A new decision on the same item replaces a pending one (the last one wins, as in the form).
        for t, d in list(self.items.items()):
            if d.item_id == item_id and d.state == "pending":
                del self.items[t]
        d = Decision(secrets.token_urlsafe(16), item_id, decision, text, reason, publish_at, self.clock() + self.delay,
                     seen_sha256=seen_sha256, reviewer=reviewer or self.reviewer)
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
        """Send what is due: one apply_decisions call per reviewer (the payload names one reviewer)."""
        now = self.clock() if now is None else now
        async with self._lock:
            due = [d for d in self.items.values() if d.state == "pending" and (everything or d.due <= now)]
            if not due:
                return []
            groups: dict[str, list[Decision]] = {}
            for d in due:
                d.state = "sending"
                groups.setdefault(d.reviewer or self.reviewer, []).append(d)
            out = []
            try:
                for reviewer, batch in groups.items():
                    res = await self._send(reviewer, batch)
                    self.results.appendleft(res)
                    out.append(res)
            finally:
                for d in due:   # nothing stays "sending" (hidden) if one batch fails oddly
                    self.items.pop(d.token, None)
            return out

    async def _send(self, reviewer: str, due: list[Decision]) -> Result:
        try:
            body = await self.b.apply_decisions(reviewer, [d.payload() for d in due])
            lines = [str(x) for x in body.get("summary") or []]
            stale = body.get("not_in_review") or []
            if stale:
                lines.append("Not in review any more, nothing changed: " + ", ".join(f"#{i}" for i in stale))
            # The text changed after the card was shown: 19 refused, n8n lists the ids in
            # "stale" and says so in the summary. An older workflow only has the raw 409.
            changed = [i for i in body.get("stale") or [] if isinstance(i, int)]
            for i in changed:
                line = CHANGED_LINE.format(id=i)
                if not any(str(x).startswith(f"#{i}: NOT approved") for x in lines):
                    lines.append(line)
            failed = body.get("failed") or []
            for f in failed:
                detail = f.get("detail") if isinstance(f, dict) else f
                if isinstance(detail, dict) and detail.get("message") == "changed since you looked":
                    lines.append(CHANGED_LINE.format(id=f.get("item_id") or "?"))
                    continue
                lines.append(f"Failed: {str(detail)[:300]}")
            res = Result(self.clock(), [d.item_id for d in due],
                         ok=bool(body.get("ok", True)) and not failed and not stale and not changed,
                         lines=lines or ["No decisions made."])
        except BackendError as e:
            res = Result(self.clock(), [d.item_id for d in due], ok=False,
                         lines=[f"Not saved ({e.detail}). The items are back in the queue; decide again."])
        finally:
            for d in due:
                self.items.pop(d.token, None)
        return res

    async def run(self, interval: float = 0.25):
        while True:
            await asyncio.sleep(interval)
            try:
                await self.flush_due()
            except Exception:  # noqa: BLE001 - the loop must survive anything; the result says why
                pass
