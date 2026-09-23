import asyncio
import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import DEFAULT_SERVICES, app

client = TestClient(app)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.delenv("SERVICES", raising=False)
    monkeypatch.delenv("OLLAMA_URL", raising=False)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


@respx.mock
def test_default_services_all_up():
    respx.get(url__regex=r"http://[a-z-]+:8000/health").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    body = client.get("/status").json()
    assert body["checked_at"].endswith("Z") and body["all_ok"] is True
    names = [s["name"] for s in body["services"]]
    assert names == DEFAULT_SERVICES
    first = body["services"][0]
    assert first == {"name": "llm-gateway", "url": "http://llm-gateway:8000", "ok": True,
                     "latency_ms": first["latency_ms"], "error": None}
    assert isinstance(first["latency_ms"], int)


@respx.mock
def test_failures_are_reported_per_service(monkeypatch):
    monkeypatch.setenv("SERVICES", "a=http://a:8000, b=http://b:9000/ ,c=http://c:8000,d=http://d:8000")
    respx.get("http://a:8000/health").mock(return_value=httpx.Response(200))
    respx.get("http://b:9000/health").mock(return_value=httpx.Response(503))
    respx.get("http://c:8000/health").mock(side_effect=httpx.ConnectTimeout("slow"))
    respx.get("http://d:8000/health").mock(side_effect=httpx.ConnectError("Name or service not known"))
    body = client.get("/status").json()
    by = {s["name"]: s for s in body["services"]}
    assert body["all_ok"] is False
    assert by["a"]["ok"] and by["a"]["error"] is None
    assert by["b"] == {**by["b"], "ok": False, "error": "HTTP 503", "url": "http://b:9000"}
    assert not by["c"]["ok"] and by["c"]["error"] == "timeout after 3s"
    assert not by["d"]["ok"] and "ConnectError" in by["d"]["error"]


@respx.mock
def test_bare_name_defaults_to_port_8000(monkeypatch):
    monkeypatch.setenv("SERVICES", "readability")
    respx.get("http://readability:8000/health").mock(return_value=httpx.Response(200))
    assert client.get("/status").json()["services"][0]["url"] == "http://readability:8000"


@respx.mock
def test_ollama_checked_when_configured(monkeypatch):
    monkeypatch.setenv("SERVICES", "a=http://a:8000")
    monkeypatch.setenv("OLLAMA_URL", "http://host.docker.internal:11434/")
    respx.get("http://a:8000/health").mock(return_value=httpx.Response(200))
    tags = respx.get("http://host.docker.internal:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": []}))
    body = client.get("/status").json()
    assert tags.called
    assert body["services"][-1]["name"] == "ollama" and body["services"][-1]["ok"]


@respx.mock
def test_checks_run_concurrently(monkeypatch):
    monkeypatch.setenv("SERVICES", ",".join(f"s{i}=http://s{i}:8000" for i in range(5)))

    async def slow(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200)

    respx.get(url__regex=r"http://s\d:8000/health").mock(side_effect=slow)
    start = time.perf_counter()
    assert client.get("/status").json()["all_ok"]
    assert time.perf_counter() - start < 0.8  # sequential would be >= 1.0s


@respx.mock
def test_html_page(monkeypatch):
    monkeypatch.setenv("SERVICES", "good=http://good:8000,<bad>=http://bad:8000")
    respx.get("http://good:8000/health").mock(return_value=httpx.Response(200))
    respx.get("http://bad:8000/health").mock(return_value=httpx.Response(500))
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    h = r.text
    assert '<meta http-equiv="refresh" content="30">' in h
    assert h.count('class="dot up"') == 1 and h.count('class="dot down"') == 1
    assert "&lt;bad&gt;" in h and "<bad>" not in h
    assert "1 of 2 up" in h and "HTTP 500" in h
