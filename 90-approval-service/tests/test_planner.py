"""The pure planner: which calendar calls a decision makes, and the answer 72 gets back."""
from datetime import datetime, timezone

import pytest

from app import planner

NOW = datetime(2026, 9, 30, 14, 25, 7, tzinfo=timezone.utc)
H = lambda c: c * 64  # noqa: E731


def item(i, **kw):
    return {"id": i, "channel": "linkedin", "body": f"Body {i}", "body_sha256": H("a"),
            "scheduled_at": "2026-10-01T09:00:00Z", "campaign_id": None, "notes": None, **kw}


def run(decisions, items, reviewer="Sam"):
    reviewer, ds = planner.validate({"reviewer": reviewer, "decisions": decisions})
    return planner.plan(ds, items, reviewer, NOW)


def calls(p):
    return [(o["stage"], o["method"], o["path"], o["body"]) for o in p["ops"]]


def test_approve_patches_the_time_then_approves_bound_to_the_hash():
    p = run([{"id": 1, "decision": "approve"}], [item(1)])
    assert calls(p) == [
        (1, "PATCH", "/items/1", {"scheduled_at": "2026-10-01T09:00:00Z", "if_match_sha256": H("a")}),
        (2, "POST", "/items/1/status", {"status": "approved", "note": "approved by Sam", "expected_sha256": H("a")}),
    ]
    assert p["summary"] == ["#1: approved for 2026-10-01 09:00 UTC"]
    assert p["events"] == [{"item_id": 1, "channel": "linkedin", "campaign_id": None, "decision": "approved",
                            "draft": "Body 1", "final": "Body 1", "reason": None, "reviewer": "Sam"}]


def test_the_seen_hash_wins_over_the_fetched_one():
    p = run([{"id": 1, "decision": "approve", "seen_sha256": H("c")}], [item(1)])
    assert p["ops"][0]["body"]["if_match_sha256"] == H("c")
    assert p["ops"][1]["body"]["expected_sha256"] == H("c")


def test_publish_time_publish_at_then_scheduled_at_then_next_full_hour():
    it = [item(1), item(2, scheduled_at=None, body_sha256=None)]
    p = run([{"id": 1, "decision": "approve", "publish_at": "2026-10-03 08:30"},
             {"id": 2, "decision": "approve"}], it)
    assert p["ops"][0]["body"]["scheduled_at"] == "2026-10-03T08:30:00.000Z"
    assert p["ops"][2]["body"] == {"scheduled_at": "2026-09-30T15:00:00.000Z"}  # no hash anywhere: unbound
    assert p["ops"][3]["body"] == {"status": "approved", "note": "approved by Sam"}
    assert run([{"id": 1, "decision": "approve", "publish_at": "garbage"}], [item(1)])["ops"][0]["body"][
        "scheduled_at"] == "2026-10-01T09:00:00Z"


@pytest.mark.parametrize("given,iso", [
    ("2026-10-03T08:30:00+02:00", "2026-10-03T06:30:00.000Z"),
    ("2026-10-03T08:30-0130", "2026-10-03T10:00:00.000Z"),
    ("2026-10-03", "2026-10-03T00:00:00.000Z"),
    ("2026-10-03T08:30:00.1239Z", "2026-10-03T08:30:00.123Z"),
    ("2026-02-30T10:00", None), ("10/03/2026", None), ("2026-10-03T08", None), ("", None),
])
def test_to_iso(given, iso):
    assert planner.to_iso(given) == iso


def test_edit_saves_the_text_and_approves_exactly_that_text():
    p = run([{"id": 1, "decision": "edit", "text": "New text\r\n", "reason": "shorter"}], [item(1)])
    assert calls(p) == [
        (1, "PATCH", "/items/1", {"scheduled_at": "2026-10-01T09:00:00Z", "if_match_sha256": H("a"), "body": "New text"}),
        (2, "POST", "/items/1/status", {"status": "approved", "note": "edited and approved by Sam: shorter",
                                        "expected_body": "New text"}),
    ]
    assert p["events"][0]["decision"] == "edited" and p["events"][0]["final"] == "New text"
    assert p["summary"] == ["#1: edited and approved for 2026-10-01 09:00 UTC"]


@pytest.mark.parametrize("text", ["Body 1", "  Body 1\r\n", "", None])
def test_edit_with_the_text_unchanged_is_an_approve(text):
    p = run([{"id": 1, "decision": "edit", "text": text}], [item(1)])
    assert p["ops"][1]["body"] == {"status": "approved", "note": "approved by Sam", "expected_sha256": H("a")}
    assert "body" not in p["ops"][0]["body"]


def test_edited_video_goes_back_to_draft_without_the_old_video():
    video = "http://video.test/videos/" + "a" * 32 + ".mp4"
    p = run([{"id": 1, "decision": "edit", "text": "Hook: New"}],
            [item(1, channel="video", video_url=video, image_url=video[:-4] + ".jpg")])
    assert calls(p) == [
        (1, "PATCH", "/items/1", {"body": "Hook: New", "video_url": None, "image_url": None, "if_match_sha256": H("a")}),
        (2, "POST", "/items/1/status", {"status": "draft", "note": "edited video needs a new render (not approved)"}),
    ]
    assert p["events"] == [] and p["summary"][0].startswith("#1: NOT approved, back to draft")


def test_a_video_channel_item_without_a_video_is_not_approved_after_an_edit():
    p = run([{"id": 1, "decision": "edit", "text": "New script"}], [item(1, channel="Video")])
    assert [o["body"].get("status") for o in p["ops"]] == [None, "draft"]


def test_a_clip_caption_edit_is_approved_and_keeps_the_clip():
    clip = "http://clips.test/clips/" + "b" * 32 + ".mp4"
    p = run([{"id": 1, "decision": "edit", "text": "Caption 2"}], [item(1, channel="video", video_url=clip)])
    assert p["ops"][1]["body"]["status"] == "approved" and "video_url" not in p["ops"][0]["body"]


def test_an_unchanged_video_is_approved():
    video = "http://video.test/videos/" + "a" * 32 + ".mp4"
    p = run([{"id": 1, "decision": "approve"}], [item(1, video_url=video)])
    assert p["ops"][1]["body"]["status"] == "approved"


def test_rejects_and_back_to_draft():
    items = [item(1), item(2), item(3), item(4)]
    p = run([{"id": 1, "decision": "reject_drop", "reason": "off brand"},
             {"id": 2, "decision": "reject_rewrite"},
             {"id": 3, "decision": "reject_rewrite", "reason": "too salesy"},
             {"id": 4, "decision": "back_to_draft"}], items)
    assert calls(p) == [
        (1, "POST", "/items/1/status", {"status": "rejected", "note": "rejected by Sam: off brand"}),
        (1, "POST", "/items/2/status", {"status": "rejected", "note": "rejected by Sam"}),
        (1, "POST", "/items/3/status", {"status": "rejected", "note": "rejected by Sam: too salesy"}),
        (2, "POST", "/items/3/status", {"status": "draft", "note": "sent back to rewrite by hand: too salesy"}),
        (1, "POST", "/items/4/status", {"status": "draft", "note": "sent back to draft by Sam"}),
    ]
    assert p["summary"] == [
        "#1: rejected", "#2: rejected (no reason given, so it was not rewritten)",
        '#3: rejected, back to draft to rewrite by hand ("too salesy"); automatic rewrites run only through n8n',
        "#4: back to draft"]
    assert [(e["item_id"], e["decision"], e["final"]) for e in p["events"]] == [
        (1, "rejected", None), (2, "rejected", None), (3, "rejected", None)]


def test_skip_not_in_review_last_wins_and_order():
    p = run([{"id": 3, "decision": "approve"}, {"id": 9, "decision": "approve"}, {"id": 2, "decision": "skip"},
             {"id": 1, "decision": "approve"}, {"id": 1, "decision": "reject_drop"}], [item(1), item(2), item(3)])
    assert p["not_in_review"] == [9] and p["count"] == 2
    assert [(o["path"], o["body"].get("status")) for o in p["ops"]] == [
        ("/items/1/status", "rejected"), ("/items/3", None), ("/items/3/status", "approved")]


def test_engine_pillars_from_notes():
    p = run([{"id": 1, "decision": "approve"}], [item(1, notes="x\nengine pillar #12 slot #3")])
    assert p["pillars"] == {1: 12}


def test_blank_reviewer_is_the_control_room():
    assert run([{"id": 1, "decision": "approve"}], [item(1)], reviewer="  ")["ops"][1]["body"]["note"] == \
        "approved by control room"


# ---------- the answer


def test_result_stale_failed_and_ok():
    p = run([{"id": 1, "decision": "approve"}, {"id": 2, "decision": "approve"}, {"id": 3, "decision": "reject_drop"}],
            [item(1), item(2), item(3)])
    stale = {"message": "changed since you looked", "current_sha256": H("f")}
    r = planner.result(p, [{"item_id": 1, "status": 409, "detail": stale},
                           {"item_id": 1, "status": 409, "detail": stale},
                           {"item_id": 3, "status": 409, "detail": {"message": "cannot move from draft to rejected"}}])
    assert r["ok"] is False and r["stale"] == [1]
    assert r["summary"] == ["#2: approved for 2026-10-01 09:00 UTC", "#3: rejected",
                            "#1: NOT approved: changed since you looked — reopen the card"]
    assert r["failed"] == [{"item_id": 3, "status": 409, "detail": {"message": "cannot move from draft to rejected"}}]
    assert r["message"].endswith('Some updates failed:\n{"message":"cannot move from draft to rejected"}')
    assert planner.result(p, [{"item_id": 2, "status": 428, "detail": "needs a bound approval"}])["stale"] == [2]
    ok = planner.result(p, [], ["Note: x"])
    assert ok["ok"] is True and ok["summary"][-1] == "Note: x" and ok["failed"] == []


def test_nothing_to_decide():
    assert planner.nothing_to_decide([7]) == {"ok": True, "summary": [], "not_in_review": [7], "stale": [],
                                              "failed": [], "message": "No decisions made."}


# ---------- the request


@pytest.mark.parametrize("body,error", [
    ({"decisions": []}, "decisions: 1 to 50 entries"),
    ({"decisions": [{"id": i, "decision": "skip"} for i in range(1, 52)]}, "decisions: 1 to 50 entries"),
    (None, "decisions: 1 to 50 entries"),
    ({"reviewer": "x" * 81, "decisions": [{"id": 1, "decision": "skip"}]}, "reviewer: text, at most 80 characters"),
    ({"decisions": [None]}, "decisions[0]: an object"),
    ({"decisions": [{"id": "1", "decision": "approve"}]}, "decisions[0].id: a positive integer"),
    ({"decisions": [{"id": True, "decision": "approve"}]}, "decisions[0].id: a positive integer"),
    ({"decisions": [{"id": 1, "decision": "publish"}]},
     "decisions[0].decision: one of approve, edit, reject_rewrite, reject_drop, back_to_draft, skip"),
    ({"decisions": [{"id": 1, "decision": "edit", "text": "x" * 60001}]},
     "decisions[0]: text/reason/publish_at must be short text"),
    ({"decisions": [{"id": 1, "decision": "approve", "seen_sha256": "A" * 64}]},
     "decisions[0].seen_sha256: 64 hex characters (0-9a-f) or empty"),
])
def test_invalid_requests(body, error):
    with pytest.raises(planner.Invalid) as e:
        planner.validate(body)
    assert str(e.value) == error


def test_limits_count_like_javascript():
    # "😀" is two UTF-16 units in JavaScript: 40 of them are 80 units, 41 are too many.
    planner.validate({"reviewer": "😀" * 40, "decisions": [{"id": 1, "decision": "skip"}]})
    with pytest.raises(planner.Invalid):
        planner.validate({"reviewer": "😀" * 41, "decisions": [{"id": 1, "decision": "skip"}]})
    assert planner.validate({"decisions": [{"id": 2.0, "decision": "approve"}]})[1][0]["id"] == 2
