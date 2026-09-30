"""Each tool: happy path and errors, through the SDK's in-memory client."""
from app.server import TOOL_NAMES, build_server

from .conftest import INT_SENTINEL, RES_SENTINEL, TID, call, cfg, dump, tools


def test_lists_exactly_these_tools(server):
    listed = tools(server)
    assert sorted(t.name for t in listed) == sorted(TOOL_NAMES)
    assert sorted(TOOL_NAMES) == sorted(["get_business_facts", "make_task_pack", "submit_answer", "submit_split",
                                         "check_text", "get_task", "list_blockers"])
    for t in listed:
        assert t.description and len(t.description) > 60, t.name
        assert not any(w in t.name for w in ("approve", "publish", "confirm", "retire", "export"))
    by = {t.name: t for t in listed}
    assert by["get_business_facts"].annotations.read_only_hint is True
    assert by["submit_answer"].annotations.read_only_hint is False
    assert by["submit_answer"].input_schema["properties"]["text"]["maxLength"] == 40_000


def test_facts_public_only(fake, server):
    r = call(server, "get_business_facts", {"site": "lakeside", "on_date": "2026-10-06"})
    assert not r.is_error, dump(r)
    out = r.structured_content
    q = fake.queries[-1]
    assert q.get("max_sensitivity") == "public" and q.get("at") == "2026-10-06" and q.get_list("site") == ["lakeside"]
    keys = [f["key"] for f in out["facts"]]
    assert keys == ["weekday-rate", "lake-view"]
    wr = out["facts"][0]
    assert wr["value_text"] == "£180 per room per night" and wr["slot"] == "[[weekday-rate]]"
    assert wr["required_disclosures"] == ["per room per night, 2 sharing"]
    assert set(wr) >= {"key", "text", "value_text", "required_disclosures", "scope", "valid_from", "valid_to"}
    ex = {e["key"]: e["reason"] for e in out["excluded"]}
    assert ex == {"summer-rate": "expired", "partner-rate": "internal", "owner-margin": "restricted"}
    assert all(set(e) == {"key", "reason"} for e in out["excluded"])  # no values, even if 05 sends one
    text = dump(r)
    assert INT_SENTINEL not in text and RES_SENTINEL not in text and "£240" not in text


def test_facts_scope_unspecified_and_default_date(fake, server):
    out = call(server, "get_business_facts", {}).structured_content
    assert "at" not in fake.queries[-1]
    assert {e["key"]: e["reason"] for e in out["excluded"]}["lake-view"] == "scope_unspecified"


def test_facts_defends_against_a_leaky_05(fake, server):
    fake.leak = True  # 05 ignores max_sensitivity and sends internal/restricted facts
    r = call(server, "get_business_facts", {})
    out = r.structured_content
    assert [f["key"] for f in out["facts"]] == ["weekday-rate"]
    assert {"key": "partner-rate", "reason": "internal"} in out["excluded"]
    assert {"key": "owner-margin", "reason": "restricted"} in out["excluded"]
    assert INT_SENTINEL not in dump(r) and RES_SENTINEL not in dump(r)


def test_facts_bad_input(fake, server):
    assert call(server, "get_business_facts", {"on_date": "06/10/2026"}).is_error
    r = call(server, "get_business_facts", {"on_date": "2026-02-30"})
    assert r.is_error and "not a real date" in dump(r)
    assert call(server, "get_business_facts", {"site": "x" * 81}).is_error
    assert not fake.queries


def test_facts_upstream_down(server):
    import httpx
    import respx
    with respx.mock(assert_all_mocked=True) as m:
        m.get("http://brand.test/facts/query").mock(side_effect=httpx.ConnectError("refused"))
        r = call(server, "get_business_facts", {})
    assert r.is_error and "brand service (05) is not reachable" in dump(r)


def test_wrong_internal_key(fake):
    s = build_server(cfg(internal_api_key="not-the-key-zzz"))
    r = call(s, "get_business_facts", {})
    assert r.is_error and "INTERNAL_API_KEY" in dump(r) and "not-the-key-zzz" not in dump(r)


def test_make_task_pack(fake, server):
    r = call(server, "make_task_pack", {
        "goal": "Autumn midweek stays", "publish_on": "2026-10-06", "scope": {"sites": ["lakeside"]},
        "pieces": [{"channel": "LinkedIn", "kind": "post"}, {"channel": "instagram"}]})
    assert not r.is_error, dump(r)
    out = r.structured_content
    assert out["task_id"] == TID and out["pack"].startswith(f"Task {TID}")
    assert out["share_preview"]["withheld"] == [{"key": "owner-margin", "reason": "restricted"}]
    assert [p["piece_key"] for p in out["pieces"]] == ["p1", "p2"]
    body = fake.bodies["tasks"]
    assert body["pieces"][0] == {"key": "p1", "channel": "linkedin", "kind": "post", "max_chars": None}
    assert body["scope"]["sites"] == ["lakeside"] and body["scope"]["regions"] == []
    assert body["publish_on"] == "2026-10-06"


def test_make_task_pack_validation(fake, server):
    base = {"goal": "Autumn", "publish_on": "2026-10-06", "pieces": [{"channel": "x"}]}
    assert call(server, "make_task_pack", {**base, "pieces": []}).is_error
    assert call(server, "make_task_pack", {**base, "pieces": [{"channel": "x"}] * 11}).is_error
    assert call(server, "make_task_pack", {**base, "pieces": [{"channel": "x", "evil": 1}]}).is_error
    assert call(server, "make_task_pack", {**base, "scope": {"planets": ["mars"]}}).is_error
    assert call(server, "make_task_pack", {**base, "publish_on": "soon"}).is_error
    assert call(server, "make_task_pack", {**base, "goal": "g" * 2001}).is_error
    assert "tasks" not in fake.bodies


def test_submit_answer_happy(fake, server):
    text = "=== 1 LINKEDIN ===\nMidweek [[weekday-rate]]\n=== 2 INSTAGRAM ===\nPartners [[partner-rate]]"
    r = call(server, "submit_answer", {"task_id": TID, "text": text})
    assert not r.is_error, dump(r)
    out = r.structured_content
    assert fake.bodies["paste"] == {"text": text, "provider": "claude"}
    assert fake.bodies["submit"] == {"draft_id": 1}
    assert "control room" in out["message"] and "cannot approve or publish" in out["message"]
    p1, p2 = out["pieces"]
    assert p1["blocked"] is False and p1["calendar_item_id"] == 11 and p1["status"] == "in_review"
    assert p1["findings"][0]["label"] == "match" and p1["findings"][0]["quote"].startswith("Midweek Escape costs")
    assert p2["blocked"] is True and p2["blocking_findings"] == 2 and p2["calendar_item_id"] == 12
    assert {f["label"] for f in p2["findings"]} == {"wrong_scope", "forbidden_phrase"}
    assert all({"sentence", "label", "quote", "why", "blocking", "fact_key"} <= set(f) for f in p2["findings"])
    assert p2["rule_problems"] == [{"source": "platform", "rule": "too_long", "severity": "error", "detail": "2300 chars"}]
    assert "1 of 2 pieces are blocked (p2)" in out["summary"]


def test_submit_answer_hides_internal_values(fake, server):
    r = call(server, "submit_answer", {"task_id": TID, "text": "=== 1 LINKEDIN ===\nx\n=== 2 X ===\ny"})
    text = dump(r)
    assert INT_SENTINEL not in text and "777" not in text
    p2 = r.structured_content["pieces"][1]
    wrong = next(f for f in p2["findings"] if f["label"] == "wrong_scope")
    assert wrong["quote"] is None and "[[partner-rate]]" in wrong["sentence"]
    assert "[[partner-rate]]" in p2["text_with_internal_values_hidden"]


def test_submit_answer_split_problems_stop(fake, server):
    fake.split_problems = True
    r = call(server, "submit_answer", {"task_id": TID, "text": "no markers at all", "provider": "chatgpt"})
    out = r.structured_content
    assert out["submitted"] is False and out["status"] == "split_has_problems" and out["draft_id"] == 1
    assert out["problems"] == ["found 1 marker, the task has 2 pieces"]
    assert "submit_split" in out["next_step"]
    assert "submit" not in fake.bodies


def test_submit_split(fake, server):
    r = call(server, "submit_split", {"task_id": TID, "draft_id": 1, "pieces": [
        {"piece_key": "p1", "text": "one"}, {"piece_key": "p2", "text": "two"}]})
    assert not r.is_error, dump(r)
    assert fake.bodies["split"] == {"pieces": [{"piece_key": "p1", "text": "one"}, {"piece_key": "p2", "text": "two"}]}
    assert fake.bodies["submit"] == {"draft_id": 2}
    assert len(r.structured_content["pieces"]) == 2


def test_submit_errors(fake, server):
    r = call(server, "submit_answer", {"task_id": "T-ZZZZZZ", "text": "x"})
    assert r.is_error and "not found" in dump(r)
    assert call(server, "submit_answer", {"task_id": "../../x", "text": "x"}).is_error
    assert call(server, "submit_answer", {"task_id": TID, "text": ""}).is_error
    assert call(server, "submit_answer", {"task_id": TID, "text": "x" * 40_001}).is_error
    assert call(server, "submit_answer", {"task_id": TID, "text": "x", "provider": "bard"}).is_error
    r = call(server, "submit_split", {"task_id": TID, "draft_id": 98, "pieces": [{"piece_key": "p1", "text": "x"}]})
    assert r.is_error and "already approved" in dump(r)
    assert call(server, "submit_split", {"task_id": TID, "draft_id": 0, "pieces": [{"piece_key": "p1", "text": "x"}]}).is_error
    assert call(server, "submit_split", {"task_id": TID, "draft_id": 1, "pieces": [
        {"piece_key": "p1", "text": "x" * 30_000}, {"piece_key": "p2", "text": "x" * 20_000}]}).is_error


def test_submit_fails_closed_without_sensitivity_map(fake, server):
    fake.facts_v2_down = True
    r = call(server, "submit_answer", {"task_id": TID, "text": "=== 1 LINKEDIN ===\nx"})
    assert r.is_error and "paste" not in fake.bodies  # nothing was pasted or submitted


def test_check_text(fake, server):
    r = call(server, "check_text", {"text": "Our partners get [[partner-rate]].", "publish_on": "2026-10-06",
                                    "scope": {"sites": ["lakeside"]}, "channel": "linkedin"})
    assert not r.is_error, dump(r)
    out = r.structured_content
    assert out["blocked"] is True and [f["label"] for f in out["findings"]] == ["conflict_or_expired", "no_source"]
    assert fake.bodies["check"]["scope"]["sites"] == ["lakeside"] and fake.bodies["check"]["channel"] == "linkedin"
    text = dump(r)
    assert INT_SENTINEL not in text and RES_SENTINEL not in text and "777" not in text and "42.5" not in text
    assert all(f["quote"] is None for f in out["findings"])


def test_check_text_errors(fake, server):
    assert call(server, "check_text", {"text": "x"}).is_error  # publish_on required
    assert call(server, "check_text", {"text": "", "publish_on": "2026-10-06"}).is_error
    assert call(server, "check_text", {"text": "x" * 40_001, "publish_on": "2026-10-06"}).is_error
    assert "check" not in fake.bodies


def test_get_task(fake, server):
    r = call(server, "get_task", {"task_id": TID})
    assert not r.is_error, dump(r)
    out = r.structured_content
    assert out["task_id"] == TID and out["pieces"][0]["channel"] == "linkedin"
    assert out["pieces"][1]["stale_facts"] == ["partner-rate"] and out["pieces"][1]["blocked"] is True
    assert out["drafts"][0]["draft_id"] == 1 and "events" not in out
    assert out["export"]["ready"] is False
    assert INT_SENTINEL not in dump(r) and "777" not in dump(r)
    r = call(server, "get_task", {"task_id": "T-ZZZZZZ"})
    assert r.is_error and "not found" in dump(r)


def test_list_blockers(fake, server):
    out = call(server, "list_blockers").structured_content
    assert out["none"] is False and out["blockers"][0]["kind"] == "missing_facts"


def test_tool_rate_limit(fake):
    s = build_server(cfg(tool_calls_per_min=2))
    assert not call(s, "list_blockers").is_error
    assert not call(s, "list_blockers").is_error
    r = call(s, "list_blockers")
    assert r.is_error and "too many tool calls" in dump(r)


def test_custom_text_cap(fake):
    s = build_server(cfg(max_text_chars=1000))
    assert call(s, "check_text", {"text": "x" * 1001, "publish_on": "2026-10-06"}).is_error
    assert not call(s, "check_text", {"text": "x" * 1000, "publish_on": "2026-10-06"}).is_error
