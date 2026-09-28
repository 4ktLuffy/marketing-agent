"""Login rate limits use the real visitor address behind a trusted proxy, and ignore
X-Forwarded-For from anyone else (it would let an attacker pick their own address)."""
from types import SimpleNamespace

from app.security import client_ip


def req(peer, xff=None):
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers={"x-forwarded-for": xff} if xff else {})


def test_no_trusted_proxies_uses_the_peer_and_ignores_the_header():
    assert client_ip(req("203.0.113.9", "1.2.3.4"), "") == "203.0.113.9"


def test_untrusted_peer_cannot_spoof_its_address():
    assert client_ip(req("203.0.113.9", "1.2.3.4"), "172.16.0.0/12") == "203.0.113.9"


def test_behind_a_trusted_proxy_each_visitor_is_counted_separately():
    assert client_ip(req("172.18.0.5", "198.51.100.7"), "172.16.0.0/12") == "198.51.100.7"
    assert client_ip(req("172.18.0.5", "198.51.100.8"), "172.16.0.0/12") == "198.51.100.8"


def test_rightmost_untrusted_hop_wins_so_a_client_cannot_prepend_a_fake():
    # the client sent "X-Forwarded-For: 9.9.9.9"; the proxy appended the real address
    assert client_ip(req("172.18.0.5", "9.9.9.9, 198.51.100.7"), "172.16.0.0/12") == "198.51.100.7"


def test_garbage_header_falls_back_to_the_peer():
    assert client_ip(req("172.18.0.5", "not-an-ip"), "172.16.0.0/12") == "172.18.0.5"
