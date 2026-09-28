"""DNS rebinding end to end (security review 2026-09-28, X-1), through this service's own fetch.

`rebind.test` answers a public address to the first lookup and 127.0.0.1 to every later one.
A real HTTP listener on 127.0.0.1 stands in for an internal service. The OS resolver is
simulated inside socket.create_connection (where httpx resolves a name when it is not pinned):
only 127.0.0.1 is really dialled, every other address is recorded and refused, so nothing
leaves this machine. Before the fix the guard checked the first answer and httpx connected to
the second, and the listener was hit.
"""
import http.server
import socket
import threading

import pytest

from app import enrich

PUBLIC = "93.184.215.14"


@pytest.fixture
def listener():
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = b"<html><title>internal</title></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], hits
    server.shutdown()
    server.server_close()


@pytest.fixture
def rebinding(monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    lookups, dialled = [], []

    def lookup(host):
        lookups.append(host)
        return [PUBLIC] if len(lookups) == 1 else ["127.0.0.1"]

    guard = getattr(enrich, "safe_http", enrich)  # app.enrich before the fix, app.safe_http after
    monkeypatch.setattr(guard, "resolve", lookup)
    real = socket.create_connection

    def create_connection(address, *args, **kwargs):
        host, port = address[:2]
        ip = lookup(host)[0] if host == "rebind.test" else host
        dialled.append(ip)
        if ip != "127.0.0.1":
            raise OSError(f"test network: {ip} is not reachable")
        return real((ip, port), *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", create_connection)
    return dialled


def test_dns_rebinding_never_reaches_the_private_address(listener, rebinding, tmp_path):
    port, hits = listener
    try:
        enrich.robots(f"http://rebind.test:{port}", "rebind.test")
    except enrich.EnrichError:
        pass
    assert hits == []
    assert rebinding == [PUBLIC]  # dialled the checked address only
