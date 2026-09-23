import httpx
import respx

from evalsuite.checks import run_check, select

SERVICES = {"brand": "http://brand.test", "rules": "http://rules.test"}


def test_select_paths():
    out = {"posts": [{"channel": "x", "text": "a"}, {"channel": "linkedin", "text": "b"}]}
    assert select(out, "posts[*].text") == ["a", "b"]
    assert select(out, "") == [out]
    assert select(["h1", "h2"], "[*]") == ["h1", "h2"]
    assert select(out, "missing[*]") == []


def test_max_chars_fails_when_over():
    ok, detail = run_check({"type": "max_chars", "path": "h[*]", "value": 5}, {"h": ["short", "too long"]}, {}, SERVICES)
    assert not ok and "8" in detail


def test_numbers_from_input_catches_invented_stat():
    ok, detail = run_check({"type": "numbers_from_input", "path": ""}, "Plans from $18, 93% love it",
                           {"text": "Plans from $18"}, SERVICES)
    assert not ok and "93" in detail
    ok, _ = run_check({"type": "numbers_from_input", "path": ""}, "Plans from $18", {"text": "Plans from $18"}, SERVICES)
    assert ok


def test_channels_match():
    out = {"posts": [{"channel": "X"}, {"channel": "linkedin"}]}
    assert run_check({"type": "channels_match", "path": "posts[*].channel", "var": "c"}, out, {"c": "x, linkedin"}, SERVICES)[0]
    assert not run_check({"type": "channels_match", "path": "posts[*].channel", "var": "c"}, out, {"c": "x"}, SERVICES)[0]


def test_empty_path_match_is_a_failure():
    ok, detail = run_check({"type": "contains", "path": "nope", "value": "a"}, {"x": 1}, {}, SERVICES)
    assert not ok and "matched nothing" in detail


@respx.mock
def test_brand_ok_uses_service_errors_only():
    respx.post("http://brand.test/check").mock(return_value=httpx.Response(200, json={
        "ok": False, "violations": [{"rule": "banned_phrase", "detail": "guaranteed", "severity": "error"},
                                    {"rule": "emoji", "detail": "many", "severity": "warn"}]}))
    ok, detail = run_check({"type": "brand_ok", "path": ""}, "Guaranteed!", {}, SERVICES)
    assert not ok and "guaranteed" in detail and "many" not in detail


@respx.mock
def test_platform_ok_reads_channel_from_item():
    route = respx.post("http://rules.test/validate").mock(return_value=httpx.Response(200, json={
        "ok": False, "violations": [{"rule": "length", "detail": "301 > 280"}]}))
    ok, detail = run_check({"type": "platform_ok", "path": "posts[*]"}, {"posts": [{"channel": " X", "text": "t"}]}, {}, SERVICES)
    assert not ok and "301" in detail
    assert b'"channel":"x"' in route.calls[0].request.content.replace(b" ", b"")


def test_numbers_from_brand_summary_are_allowed():
    services = SERVICES | {"brand_summary": "Roasted within 48 hours."}
    ok, _ = run_check({"type": "numbers_from_input", "path": ""}, "Shipped within 48 hours", {}, services)
    assert ok
    ok, detail = run_check({"type": "numbers_from_input", "path": ""}, "Shipped within 24 hours", {}, services)
    assert not ok and "24" in detail


def test_equals():
    assert run_check({"type": "equals", "path": "generalizable", "value": False}, {"generalizable": False}, {}, SERVICES)[0]
    assert not run_check({"type": "equals", "path": "generalizable", "value": False}, {"generalizable": True}, {}, SERVICES)[0]
