"""The SSRF guard pins the checked address (security review 2026-09-28, X-1).

Keep identical in 07, 08, 09, 12, 73, 80 (tests/test_safe_http.py), like app/safe_http.py.
No test here touches the real network: DNS is stubbed and the TCP layer is a fake that
records which address was dialled and serves canned HTTP bytes.
"""
import ssl

import httpcore
import pytest

from app import safe_http
from app.safe_http import BlockedURL, GuardedClient, PinnedBackend, vet

PUBLIC = "93.184.215.14"
PUBLIC2 = "93.184.216.34"


class RebindingDNS:
    """Answers `first` to the first lookup of a name and `then` to every later one."""

    def __init__(self, table: dict[str, tuple[list[str], list[str]]]):
        self.table, self.seen = table, []

    def __call__(self, host):
        first, then = self.table[host]
        answer = first if host not in self.seen else then
        self.seen.append(host)
        return answer


class FakeStream(httpcore.NetworkStream):
    def __init__(self, log, response: bytes):
        self.log, self.response = log, response

    def write(self, buffer, timeout=None):
        self.log.append(("write", buffer))

    def read(self, max_bytes, timeout=None):
        out, self.response = self.response, b""
        return out

    def close(self):
        pass

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.log.append(("tls", server_hostname, ssl_context))
        return self

    def get_extra_info(self, info):
        return None


class FakeNet(httpcore.NetworkBackend):
    """Records every dial; answers with canned HTTP responses by dialled address."""

    def __init__(self, responses: dict[str, bytes]):
        self.responses, self.dialled, self.log = responses, [], []

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.dialled.append(host)
        if host not in self.responses:
            raise httpcore.ConnectError(f"no route to {host} in this test")
        return FakeStream(self.log, self.responses[host])

    def sleep(self, seconds):
        pass


def ok(body=b"hello"):
    return b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body)


def redirect(location: str):
    return f"HTTP/1.1 302 Found\r\nLocation: {location}\r\nContent-Length: 0\r\n\r\n".encode()


def client_with(net: FakeNet) -> GuardedClient:
    client = GuardedClient(timeout=5)
    client.pinned.backend.inner = net
    return client


def no_real_dns(host):
    raise AssertionError(f"a test tried a real DNS lookup of {host!r}")


@pytest.fixture(autouse=True)
def guard_on(monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(safe_http, "resolve", no_real_dns)


def test_rebinding_name_is_dialled_only_at_the_checked_address(monkeypatch):
    dns = RebindingDNS({"rebind.test": ([PUBLIC], ["127.0.0.1"])})
    monkeypatch.setattr(safe_http, "resolve", dns)
    net = FakeNet({PUBLIC: ok()})
    with client_with(net) as client:
        assert client.get("http://rebind.test/").text == "hello"
    assert dns.seen == ["rebind.test"]  # one lookup, used for both the check and the connect
    assert net.dialled == [PUBLIC]


def test_every_redirect_hop_is_checked_and_pinned(monkeypatch):
    dns = RebindingDNS({"a.test": ([PUBLIC], ["127.0.0.1"]), "rebind.test": ([PUBLIC2], ["10.0.0.5"])})
    monkeypatch.setattr(safe_http, "resolve", dns)
    net = FakeNet({PUBLIC: redirect("http://rebind.test/next"), PUBLIC2: ok()})
    with client_with(net) as client:
        first = client.get("http://a.test/")
        assert first.is_redirect  # not followed by the client
        second = client.get(str(first.url.join(first.headers["location"])))
    assert second.text == "hello"
    assert net.dialled == [PUBLIC, PUBLIC2]


def test_redirect_hop_to_a_private_name_is_refused_before_dialling(monkeypatch):
    monkeypatch.setattr(safe_http, "resolve", {"a.test": [PUBLIC], "in.test": ["10.0.0.5"]}.get)
    net = FakeNet({PUBLIC: redirect("http://in.test/admin")})
    with client_with(net) as client:
        first = client.get("http://a.test/")
        with pytest.raises(BlockedURL):
            client.get(str(first.url.join(first.headers["location"])))
    assert net.dialled == [PUBLIC]


def test_client_refuses_to_follow_redirects_itself():
    with pytest.raises(ValueError):
        GuardedClient(follow_redirects=True)
    with GuardedClient() as client, pytest.raises(ValueError):
        client.get("http://a.test/", follow_redirects=True)


def test_tls_uses_the_host_name_while_dialling_the_pinned_ip(monkeypatch):
    monkeypatch.setattr(safe_http, "resolve", lambda host: [PUBLIC])
    net = FakeNet({PUBLIC: ok()})
    with client_with(net) as client:
        r = client.get("https://example.com/page")
    assert r.status_code == 200
    assert net.dialled == [PUBLIC]
    (tls,) = [e for e in net.log if e[0] == "tls"]
    _, server_hostname, ctx = tls
    assert server_hostname == "example.com"  # SNI and certificate name
    assert ctx.check_hostname is True and ctx.verify_mode == ssl.CERT_REQUIRED
    request = next(e[1] for e in net.log if e[0] == "write")
    assert b"Host: example.com" in request


def test_ipv6_answers(monkeypatch):
    # any private address among the A/AAAA answers blocks the name
    for bad in ("::1", "fe80::1", "fd00::1", "64:ff9b::7f00:1", "2002:7f00:1::", "::ffff:10.0.0.1", "::127.0.0.1"):
        monkeypatch.setattr(safe_http, "resolve", lambda host, b=bad: ["2606:4700::6810:84e5", b])
        with pytest.raises(BlockedURL):
            vet("https://dual.test/")
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["2606:4700::6810:84e5", PUBLIC])
    net = FakeNet({"2606:4700::6810:84e5": ok()})
    with client_with(net) as client:
        assert client.get("http://dual.test/").status_code == 200
    assert net.dialled == ["2606:4700::6810:84e5"]
    for literal in ("http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://[64:ff9b::a9fe:a9fe]/"):
        with pytest.raises(BlockedURL):
            vet(literal)
    net = FakeNet({"2606:4700::1": ok()})
    with client_with(net) as client:
        assert client.get("http://[2606:4700::1]:8080/").status_code == 200
    assert net.dialled == ["2606:4700::1"]


def test_falls_back_to_the_next_checked_address_only(monkeypatch):
    monkeypatch.setattr(safe_http, "resolve", lambda host: [PUBLIC, PUBLIC2])
    net = FakeNet({PUBLIC2: ok()})  # the first address does not answer
    with client_with(net) as client:
        assert client.get("http://two.test/").status_code == 200
    assert net.dialled == [PUBLIC, PUBLIC2]


def test_backend_refuses_a_host_that_was_not_checked():
    backend = PinnedBackend(inner=FakeNet({}))
    with pytest.raises(BlockedURL):
        backend.connect_tcp("unchecked.test", 80)
    backend.pin("x.test", ["127.0.0.1"])  # a bad pin is caught again right before dialling
    with pytest.raises(BlockedURL):
        backend.connect_tcp("x.test", 80)
    assert backend.inner.dialled == []


def test_scheme_and_parse_errors_are_blocked():
    for url in ("ftp://example.com/x", "file:///etc/passwd", "http:///nohost", "http://exa mple.com/"):
        with pytest.raises(BlockedURL):
            vet(url)


def test_allow_private_urls_switches_the_guard_off(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_URLS", "true")
    net = FakeNet({"localhost": ok()})
    with client_with(net) as client:
        assert client.get("http://localhost/").status_code == 200
    assert net.dialled == ["localhost"]


def test_transport_really_uses_the_pinned_backend():
    # fails if an httpx/httpcore upgrade stops honouring the swapped network backend
    with GuardedClient() as client:
        assert client.pinned._pool._network_backend is client.pinned.backend
