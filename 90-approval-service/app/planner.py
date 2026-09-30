"""What a batch of control-room decisions does, without any I/O.

This mirrors n8n's decision code (tools/workflow-generator: CHECK_REQUEST and REVIEW_ITEMS in
workflows_p7.py, DECISIONS_CORE, "Final decisions" and "Summary" in workflows.py), so the
control room (72) gets the same answer whether n8n or this service applies its decisions.
tools/workflow-generator/tests/approval_parity_test.js runs both on the same cases.

Standard library only, Python 3.9+: the parity test runs this module with whatever `python3`
it finds, next to node.

Intentional differences from n8n (all asserted by the parity test):
- reject_rewrite with a reason: back to draft with the note "sent back to rewrite by hand:
  <reason>"; n8n starts the automatic rewrite (workflow 49) instead.
- An edited video script (a rendered video that is not a clip, or a video-channel item): no
  re-render here. The edit is saved, the old video removed and the item goes back to draft
  with the note VIDEO_DRAFT_NOTE. n8n renders it again (71) and approves.
- publish_at is parsed as ISO 8601 only. n8n's JavaScript Date also accepts "10/03/2026" and
  rolls "2026-02-30" over to March 2; here those fall back like any unreadable date.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

KINDS = ("approve", "edit", "reject_rewrite", "reject_drop", "back_to_draft", "skip")
MAX_DECISIONS = 50
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CLIP = re.compile(r"/clips/[0-9a-f]{32}\.mp4$")
PILLAR = re.compile(r"engine pillar #(\d+)")
STALE_MESSAGE = "changed since you looked"
VIDEO_DRAFT_NOTE = "edited video needs a new render (not approved)"
ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?"
                 r"(Z|z|[+-]\d{2}:?\d{2})?$")


class Invalid(ValueError):
    """The request is refused (422) with this message, worded like n8n's."""


# ---------------------------------------------------------------- request


def _js_len(s: str) -> int:
    """String length as JavaScript counts it (UTF-16 code units), so the limits match n8n's."""
    return len(s.encode("utf-16-le")) // 2


def _text(v, limit: int):
    """'' for missing/null, the string if it fits, None if it is not a short string."""
    if v is None:
        return ""
    if isinstance(v, str) and _js_len(v) <= limit:
        return v
    return None


def _positive_int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return v if isinstance(v, int) and v >= 1 else None


def validate(body) -> tuple[str, list[dict]]:
    """{reviewer, decisions} -> (reviewer, decisions). Raises Invalid (n8n's CHECK_REQUEST)."""
    b = body if isinstance(body, dict) else {}
    ds = b.get("decisions")
    if not isinstance(ds, list) or not ds or len(ds) > MAX_DECISIONS:
        raise Invalid(f"decisions: 1 to {MAX_DECISIONS} entries")
    reviewer = _text(b.get("reviewer"), 80)
    if reviewer is None:
        raise Invalid("reviewer: text, at most 80 characters")
    out = []
    for k, x in enumerate(ds):
        if not isinstance(x, (dict, list)):
            raise Invalid(f"decisions[{k}]: an object")
        if isinstance(x, list):  # an array is an object to n8n's check; it then has no id
            x = {}
        item_id = _positive_int(x.get("id"))
        if item_id is None:
            raise Invalid(f"decisions[{k}].id: a positive integer")
        if x.get("decision") not in KINDS:
            raise Invalid(f"decisions[{k}].decision: one of {', '.join(KINDS)}")
        text, reason, when = _text(x.get("text"), 60000), _text(x.get("reason"), 500), _text(x.get("publish_at"), 40)
        if text is None or reason is None or when is None:
            raise Invalid(f"decisions[{k}]: text/reason/publish_at must be short text")
        seen = _text(x.get("seen_sha256"), 64)
        if seen is None or (seen and not HEX64.match(seen)):
            raise Invalid(f"decisions[{k}].seen_sha256: 64 hex characters (0-9a-f) or empty")
        out.append({"id": item_id, "decision": x["decision"], "text": text, "reason": reason,
                    "publish_at": when, "seen_sha256": seen})
    return reviewer.strip() or "control room", out


# ---------------------------------------------------------------- helpers


def js_iso(dt: datetime) -> str:
    """JavaScript's Date.toISOString(): UTC, milliseconds, Z."""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def to_iso(s) -> str | None:
    """publish_at -> ISO UTC string, or None. Naive means UTC; a date alone is midnight UTC."""
    s = str(s or "").strip()
    m = ISO.match(s)
    if not m:
        return None
    y, mo, d, h, mi, sec, frac, tz = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(sec or 0),
                      int((frac or "0")[:3].ljust(3, "0")) * 1000, tzinfo=timezone.utc)
    except ValueError:
        return None
    if tz and tz not in ("Z", "z"):
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        dt -= sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return js_iso(dt)


def next_hour(now: datetime) -> str:
    now = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return js_iso(now + timedelta(hours=1))


def norm(s) -> str:
    return str(s or "").replace("\r\n", "\n").strip()


def hex64(s) -> str | None:
    return s if isinstance(s, str) and HEX64.match(s) else None


def when_text(when: str) -> str:
    return when[:16].replace("T", " ", 1)


def is_stale(status, detail) -> bool:
    """19 refused because the text changed after the reviewer looked (409), or a bound item came
    without a hash (428)."""
    return status == 428 or (status == 409 and isinstance(detail, dict) and detail.get("message") == STALE_MESSAGE)


def stale_line(item_id: int) -> str:
    return f"#{item_id}: NOT approved: changed since you looked — reopen the card"


# ---------------------------------------------------------------- the plan


def plan(decisions: list[dict], in_review: list[dict], reviewer: str, now: datetime) -> dict:
    """The calendar operations for validated decisions on the items in review now.

    Returns {ops, events, summary, not_in_review, pillars, count}:
    - ops: [{stage, method, path, body, item_id}], stage 1 before stage 2 (as n8n's staged_ops);
    - events: learning events (46) for approved / edited / rejected items;
    - pillars: {item_id: pillar id} for content-engine items (notes "engine pillar #P");
    - count: how many of the decided items are in review (0: nothing to decide).
    """
    wanted: list[int] = []
    for d in decisions:
        if d["decision"] != "skip" and d["id"] not in wanted:
            wanted.append(d["id"])
    by_id = {}
    for it in in_review:
        if isinstance(it, dict) and it.get("id"):
            by_id.setdefault(str(it["id"]), it)
    items = {i: by_id[str(i)] for i in wanted if str(i) in by_id}
    missing = [i for i in wanted if str(i) not in by_id]
    last = {d["id"]: d for d in decisions}  # an id sent twice: the last one wins, as in the form

    ops, events, summary, video_ops, video_summary = [], [], [], [], []
    pillars = {}
    for item_id in sorted(items):  # n8n walks a JS object with integer keys: ascending ids
        it, d = items[item_id], last[item_id]
        kind = d["decision"]
        if kind == "skip":
            continue
        edited, reason = norm(d["text"]), norm(d["reason"])
        when = to_iso(d["publish_at"]) or it.get("scheduled_at") or next_hour(now)
        seen = hex64(d["seen_sha256"]) or hex64(it.get("body_sha256"))
        if_match = {"if_match_sha256": seen} if seen else {}

        def note(action):
            return f"{action} by {reviewer}{': ' + reason if reason else ''}"

        def event(decision, final):
            return {"item_id": item_id, "channel": it.get("channel"), "campaign_id": it.get("campaign_id"),
                    "decision": decision, "draft": it.get("body"), "final": final, "reason": reason or None,
                    "reviewer": reviewer}

        m = PILLAR.search(str(it.get("notes") or ""))
        if m:
            pillars[item_id] = int(m.group(1))
        path = f"/items/{item_id}"

        if kind in ("approve", "edit"):
            changed = kind == "edit" and bool(edited) and edited != norm(it.get("body"))
            video_url = it.get("video_url") or ""
            has_video = bool(re.match(r"^https?://", video_url))
            is_clip = bool(CLIP.search(video_url))
            is_video = has_video or str(it.get("channel") or "").lower() == "video"
            if changed and is_video and not is_clip:
                # No re-render here: save the edit, drop the old video (it shows the old script)
                # and its poster when the poster is the image, back to draft. Like n8n's
                # "could not be re-rendered" path, which never approves with the old video.
                old_video, old_image = it.get("video_url"), it.get("image_url")
                poster = re.sub(r"\.mp4$", ".jpg", old_video) if old_video else None
                patch = {"body": edited, "video_url": None}
                if old_image and old_image == poster:
                    patch["image_url"] = None
                patch.update(if_match)
                video_ops.append({"stage": 1, "method": "PATCH", "path": path, "body": patch, "item_id": item_id})
                video_ops.append({"stage": 2, "method": "POST", "path": f"{path}/status", "item_id": item_id,
                                  "body": {"status": "draft", "note": VIDEO_DRAFT_NOTE}})
                video_summary.append(f"#{item_id}: NOT approved, back to draft: your edit is saved, but the video "
                                     "needs a new render, which this service does not do; the old video was "
                                     "removed. Render it again before approving.")
                continue
            patch = {"scheduled_at": when, **if_match}
            if changed:
                patch["body"] = edited
            bound = {"expected_body": edited} if changed else {"expected_sha256": seen} if seen else {}
            ops.append({"stage": 1, "method": "PATCH", "path": path, "body": patch, "item_id": item_id})
            ops.append({"stage": 2, "method": "POST", "path": f"{path}/status", "item_id": item_id,
                        "body": {"status": "approved", "note": note("edited and approved" if changed else "approved"),
                                 **bound}})
            events.append(event("edited" if changed else "approved", edited if changed else it.get("body")))
            summary.append(f"#{item_id}: {'edited and approved' if changed else 'approved'} for {when_text(when)} UTC")
        elif kind in ("reject_rewrite", "reject_drop"):
            rewrite = kind == "reject_rewrite"
            ops.append({"stage": 1, "method": "POST", "path": f"{path}/status", "item_id": item_id,
                        "body": {"status": "rejected", "note": note("rejected")}})
            events.append(event("rejected", None))
            if rewrite and reason:
                # n8n would start the automatic rewrite (workflow 49); here a person rewrites it.
                ops.append({"stage": 2, "method": "POST", "path": f"{path}/status", "item_id": item_id,
                            "body": {"status": "draft", "note": f"sent back to rewrite by hand: {reason}"}})
                summary.append(f"#{item_id}: rejected, back to draft to rewrite by hand (\"{reason}\"); "
                               "automatic rewrites run only through n8n")
            else:
                summary.append(f"#{item_id}: rejected{' (no reason given, so it was not rewritten)' if rewrite else ''}")
        elif kind == "back_to_draft":
            ops.append({"stage": 1, "method": "POST", "path": f"{path}/status", "item_id": item_id,
                        "body": {"status": "draft", "note": note("sent back to draft")}})
            summary.append(f"#{item_id}: back to draft")

    # Edited videos come after the others, as n8n's "Final decisions" appends them.
    return {"ops": ops + video_ops, "events": events, "summary": summary + video_summary,
            "not_in_review": missing, "pillars": pillars, "count": len(items)}


# ---------------------------------------------------------------- the answer


def result(p: dict, failures: list[dict], notes: list[str] | None = None) -> dict:
    """The answer 72 expects: {ok, summary, not_in_review, stale, failed, message} (n8n's
    "Summary" + "Result"). `failures`: [{item_id, status, detail}] of refused calendar calls."""
    stale = []
    for f in failures:
        if is_stale(f["status"], f["detail"]) and f["item_id"] and f["item_id"] not in stale:
            stale.append(f["item_id"])
    summary = [line for line in p["summary"] if not any(line.startswith(f"#{i}:") for i in stale)]
    summary += [stale_line(i) for i in stale] + list(notes or [])
    failed = [{"item_id": f["item_id"], "status": f["status"], "detail": f["detail"]}
              for f in failures if not is_stale(f["status"], f["detail"])]
    lines = list(summary) if summary else ["No decisions made."]
    if failed:
        lines += ["", "Some updates failed:"] + [json.dumps(f["detail"], ensure_ascii=False, separators=(",", ":"))
                                                 for f in failed]
    return {"ok": not failed and not stale, "summary": summary, "not_in_review": p["not_in_review"],
            "stale": stale, "failed": failed, "message": "\n".join(lines)}


def nothing_to_decide(missing: list[int]) -> dict:
    return {"ok": True, "summary": [], "not_in_review": missing, "stale": [], "failed": [],
            "message": "No decisions made."}
