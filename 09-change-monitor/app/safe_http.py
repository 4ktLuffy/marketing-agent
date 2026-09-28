"""SSRF guard for outbound HTTP: public http(s) only, and the connection goes to the address
that was checked.

Keep this file identical in: 07-page-extractor, 08-rss-watcher, 09-change-monitor,
12-seo-auditor, 73-clip-finder, 80-lead-hub (all app/safe_http.py). The copy in 07 is the
canonical one: change it there, then copy it to the others. Each deploy has its own
test_safe_http.py that exercises DNS rebinding.

Why (security review 2026-09-28, X-1): checking the name and then letting httpx resolve it
again to connect is a time-of-check/time-of-use gap. A name with TTL 0 can answer a public
address to the check and 127.0.0.1 or 10.x to the connect (DNS rebinding). Here the name is
resolved once, every address is checked, and the TCP connection is made to one of those exact
addresses. TLS still uses the host name for SNI and certificate verification, because the
URL (and so the Host header and httpcore's server_hostname) is never rewritten; only the
network backend is told which IP to dial.

Use GuardedClient for every request to a URL that came from outside. It checks and pins
each request it sends and refuses to follow redirects itself: follow them by hand, so every
hop goes through send() again (checked and pinned again).
"""
import ipaddress
import os
import re
import socket
import typing

import httpcore
import httpx


class BlockedURL(ValueError):
    """The URL is not allowed: wrong scheme, or it resolves to a non-public address."""


class FetchError(Exception):
    """The upstream could not be fetched."""


def resolve(host: str) -> list[str]:
    """Every address the hostname resolves to. Tests patch this."""
    return [info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]


def private_allowed() -> bool:
    """ALLOW_PRIVATE_URLS=true switches the guard off (local tests and trusted setups only)."""
    return os.getenv("ALLOW_PRIVATE_URLS", "").lower() == "true"


def _is_public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%")[0])  # drop an IPv6 zone id
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # is_global is False for loopback, private, link-local, reserved, unspecified, CGNAT.
    # IPv6 forms that embed an IPv4 address (NAT64, 6to4, IPv4-compatible/mapped) are judged
    # by that IPv4 address: is_global alone can call them global (security audit, low).
    if ip.version == 6:
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and ip in ipaddress.ip_network("64:ff9b::/96"):
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is None and ip in ipaddress.ip_network("::/96"):
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            return embedded.is_global and not embedded.is_multicast
    return ip.is_global and not ip.is_multicast


def vet(url: "str | httpx.URL") -> tuple[str, list[str] | None]:
    """(host, addresses) for an allowed URL; raise BlockedURL otherwise.

    The URL is parsed by httpx, the same parser that sends the request, so the checked host
    is the host httpx connects to. `host` is the ASCII form (punycode, IPv6 without brackets),
    as httpcore passes it to the network backend. Addresses are None when
    ALLOW_PRIVATE_URLS=true (nothing is checked or pinned then).
    """
    try:
        parsed = url if isinstance(url, httpx.URL) else httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError) as exc:
        raise BlockedURL(f"invalid URL: {exc}") from exc
    if parsed.scheme not in ("http", "https"):
        raise BlockedURL("only http and https URLs are allowed")
    host = parsed.raw_host.decode("ascii").lower()
    if not host:
        raise BlockedURL("URL has no host")
    if private_allowed():
        return host, None
    try:
        addrs = [str(ipaddress.ip_address(host))]
    except ValueError:
        if not re.fullmatch(r"[a-z0-9_.-]+", host):
            raise BlockedURL(f"invalid host name {host!r}") from None
        try:
            addrs = resolve(host)
        except (OSError, UnicodeError) as exc:
            raise FetchError(f"cannot resolve host {host!r}") from exc
    if not addrs:
        raise FetchError(f"cannot resolve host {host!r}")
    for addr in addrs:
        if not _is_public(addr):
            raise BlockedURL(
                f"{host} resolves to non-public address {addr}; "
                "set ALLOW_PRIVATE_URLS=true to allow it"
            )
    return host, addrs


def check_url(url: str) -> None:
    """Raise BlockedURL unless the URL is http(s) and every address of its host is public.

    An early check with a clear error. It does not protect the connection by itself (the name
    can resolve differently later); GuardedClient does."""
    vet(url)


class PinnedBackend(httpcore.NetworkBackend):
    """Dials only addresses that vet() approved for the host httpcore asks for."""

    def __init__(self, inner: httpcore.NetworkBackend | None = None):
        self.inner = inner or httpcore.SyncBackend()
        self.pins: dict[str, list[str]] = {}

    def pin(self, host: str, addrs: list[str]) -> None:
        self.pins[host.lower()] = list(addrs)

    def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                    local_address: str | None = None,
                    socket_options: typing.Iterable[typing.Any] | None = None) -> httpcore.NetworkStream:
        addrs = self.pins.get(host.lower())
        if addrs is None:
            if not private_allowed():
                raise BlockedURL(f"{host} was not checked before connecting")
            addrs = [host]  # guard off: normal resolution
        else:
            for addr in addrs:  # checked again right before dialling
                if not private_allowed() and not _is_public(addr):
                    raise BlockedURL(f"{host} resolves to non-public address {addr}")
        last: Exception | None = None
        for addr in addrs:
            try:
                return self.inner.connect_tcp(addr, port, timeout=timeout,
                                              local_address=local_address, socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        assert last is not None
        raise last

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise BlockedURL("unix sockets are not allowed")

    def sleep(self, seconds: float) -> None:
        self.inner.sleep(seconds)


class PinnedTransport(httpx.HTTPTransport):
    """httpx's normal transport (TLS verified against the URL's host name) whose connections
    go through a PinnedBackend."""

    def __init__(self, backend: PinnedBackend | None = None, **kwargs):
        kwargs.setdefault("trust_env", False)
        super().__init__(**kwargs)
        self.backend = backend or PinnedBackend()
        pool = getattr(self, "_pool", None)
        if type(pool) is not httpcore.ConnectionPool or not hasattr(pool, "_network_backend"):
            # An httpx/httpcore upgrade changed the internals: fail closed, never unpinned.
            raise RuntimeError("cannot pin addresses with this httpx/httpcore version")
        pool._network_backend = self.backend


class GuardedClient(httpx.Client):
    """httpx.Client that checks and pins every request and never follows redirects itself."""

    def __init__(self, **kwargs):
        for key in ("transport", "mounts", "proxy", "follow_redirects"):
            if kwargs.get(key):
                raise ValueError(f"GuardedClient does not take {key}=")
        kwargs["trust_env"] = False
        verify = kwargs.pop("verify", True)
        self.pinned = PinnedTransport(verify=verify)
        super().__init__(transport=self.pinned, follow_redirects=False, **kwargs)

    def send(self, request: httpx.Request, *, stream: bool = False, auth=httpx.USE_CLIENT_DEFAULT,
             follow_redirects=httpx.USE_CLIENT_DEFAULT) -> httpx.Response:
        if follow_redirects is True:
            raise ValueError("follow redirects by hand, so each hop is checked again")
        host, addrs = vet(request.url)
        if addrs is not None:
            self.pinned.backend.pin(host, addrs)
        return super().send(request, stream=stream, auth=auth, follow_redirects=False)
