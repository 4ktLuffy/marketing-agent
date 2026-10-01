"""No tool can approve, publish, confirm, retire, export or reconcile; no approver/owner key at startup."""
import pathlib
import re

import pytest

from app import config, upstream
from app.__main__ import main

from .conftest import TID, call

APP = pathlib.Path(__file__).resolve().parent.parent / "app"
ENV = {"INTERNAL_API_KEY": "k" * 20, "BRAND_URL": "http://brand.test", "TASK_BRIDGE_URL": "http://tasks.test"}


def test_every_tool_leaves_forbidden_routes_untouched(fake, server):
    call(server, "get_business_facts", {"site": "lakeside"})
    call(server, "make_task_pack", {"goal": "Autumn", "publish_on": "2026-10-06", "pieces": [{"channel": "x"}]})
    call(server, "submit_answer", {"task_id": TID, "text": "=== 1 X ===\nhi"})
    call(server, "submit_split", {"task_id": TID, "draft_id": 1, "pieces": [{"piece_key": "p1", "text": "hi"}]})
    call(server, "check_text", {"text": "hi", "publish_on": "2026-10-06"})
    call(server, "get_task", {"task_id": TID})
    call(server, "list_blockers")
    # hostile arguments that try to steer a path
    call(server, "get_task", {"task_id": "T-ABC234/export"})
    call(server, "submit_answer", {"task_id": "T-ABC234/../../reconcile", "text": "x"})
    touched = {name for name, route in fake.routes.items() if route.called}
    assert touched == set(), touched


@pytest.mark.parametrize("method,path", [
    ("POST", "/facts/v2/weekday-rate/confirm"), ("POST", "/facts/v2/weekday-rate/retire"),
    ("POST", "/facts/v2/import"), ("POST", "/facts/v2"), ("PUT", "/facts/v2/weekday-rate"),
    ("GET", f"/tasks/{TID}/export"), ("POST", "/reconcile"), ("POST", "/items/1/status"),
    ("GET", f"/tasks/{TID}/../export"), ("POST", f"/tasks/{TID}/submit/../approve"),
])
def test_allowlist_refuses(method, path):
    for service in ("brand", "tasks"):
        assert not upstream.allowed(service, method, path)


def test_allowlist_is_exactly_the_tool_paths():
    assert {(s, m) for s, m, _ in upstream.ALLOWED} == {("brand", "GET"), ("tasks", "POST"), ("tasks", "GET")}
    assert len(upstream.ALLOWED) == 14          # 9 tool paths + occasions, templates (render, task), quote, audit
    assert ("tasks", "GET", r"/occasions") in upstream.ALLOWED


def test_code_has_no_approve_or_owner_paths():
    bad = re.compile(r"/(confirm|retire|approve|publish|export|reconcile|status|import|items)\b|"
                     r"x-approver-key|x-owner-key", re.IGNORECASE)
    for f in APP.glob("*.py"):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            assert not bad.search(line), f"{f.name}:{n}: {line.strip()}"
    # the approver/owner key names appear only in the startup guard
    for f in APP.glob("*.py"):
        text = f.read_text()
        if f.name != "config.py":
            assert "APPROVER_KEY" not in text and "OWNER_KEY" not in text, f.name
    assert "os.environ" not in (APP / "server.py").read_text() + (APP / "upstream.py").read_text()


@pytest.mark.parametrize("name", ["APPROVER_KEY", "FACT_OWNER_KEY", "CLIENT_APPROVER_KEY", "owner_key"])
def test_refuses_to_start_with_approver_or_owner_key(name):
    for transport in ("stdio", "http"):
        with pytest.raises(config.ConfigError) as e:
            config.load({**ENV, name: "s3cret-value"}, transport)
        msg = str(e.value)
        assert name in msg and "s3cret-value" not in msg and "refusing to start" in msg


def test_empty_forbidden_key_is_unset():
    assert config.load({**ENV, "APPROVER_KEY": "", "FACT_OWNER_KEY": "  "}).internal_api_key == "k" * 20


def test_main_exits_with_clear_error(monkeypatch, capsys):
    for k, v in {**ENV, "APPROVER_KEY": "value-9f8e7d"}.items():
        monkeypatch.setenv(k, v)
    assert main(["stdio"]) == 1
    err = capsys.readouterr().err
    assert "APPROVER_KEY is set" in err and "refusing to start" in err and "value-9f8e7d" not in err
    assert main(["bogus"]) == 2


@pytest.mark.parametrize("missing", ["INTERNAL_API_KEY", "BRAND_URL", "TASK_BRIDGE_URL"])
def test_required_settings(missing):
    env = {k: v for k, v in ENV.items() if k != missing}
    with pytest.raises(config.ConfigError, match=missing):
        config.load(env)


def test_repr_hides_secrets():
    c = config.load({**ENV, "MCP_TOKEN": "t" * 30}, "http")
    assert "k" * 20 not in repr(c) and "t" * 30 not in repr(c)
