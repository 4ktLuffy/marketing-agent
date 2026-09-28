"""Login, sessions, CSRF and the login rate limit. One approver, sessions in memory."""
import hashlib
import hmac
import secrets
import time
from collections import deque
from dataclasses import dataclass, field


def same(a: str, b: str) -> bool:
    """Constant-time compare that also hides the length (both sides are hashed first)."""
    return hmac.compare_digest(hashlib.sha256(a.encode()).digest(), hashlib.sha256(b.encode()).digest())


def check_login(settings, user: str, password: str) -> bool:
    ok_user = same(user.strip(), settings.user)
    ok_pass = same(password, settings.password)   # always evaluated: no early exit on the user name
    return bool(settings.password) and ok_user and ok_pass


@dataclass
class Session:
    created: float
    last_seen: float
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))


class Sessions:
    """Session id -> Session. Only a SHA-256 of the id is kept, so a memory dump of this dict
    can't be replayed as a cookie. A restart logs everyone out (one approver: acceptable)."""

    def __init__(self, hours: float, idle_minutes: float, clock=time.time):
        self.max_age = hours * 3600
        self.idle = idle_minutes * 60
        self.clock = clock
        self._by_hash: dict[str, Session] = {}

    @staticmethod
    def _h(sid: str) -> str:
        return hashlib.sha256(sid.encode()).hexdigest()

    def create(self) -> tuple[str, Session]:
        now = self.clock()
        self._by_hash = {h: s for h, s in self._by_hash.items() if self._alive(s, now)}
        sid = secrets.token_urlsafe(32)
        s = Session(created=now, last_seen=now)
        self._by_hash[self._h(sid)] = s
        return sid, s

    def _alive(self, s: Session, now: float) -> bool:
        return now - s.created < self.max_age and now - s.last_seen < self.idle

    def get(self, sid: str | None) -> Session | None:
        if not sid or len(sid) > 100:
            return None
        h = self._h(sid)
        s = self._by_hash.get(h)
        now = self.clock()
        if s is None or not self._alive(s, now):
            self._by_hash.pop(h, None)
            return None
        s.last_seen = now
        return s

    def drop(self, sid: str | None) -> None:
        if sid:
            self._by_hash.pop(self._h(sid), None)


class LoginLimiter:
    """At most `max_failures` failed logins per client in `window` seconds, and 4x that from
    all clients together (one approver: many failures from many addresses is an attack)."""

    def __init__(self, max_failures: int, window: float, clock=time.time):
        self.max, self.window, self.clock = max_failures, window, clock
        self._per_ip: dict[str, deque] = {}
        self._all: deque = deque()

    def _trim(self, q: deque, now: float) -> deque:
        while q and now - q[0] >= self.window:
            q.popleft()
        return q

    def retry_after(self, ip: str) -> int:
        """Seconds until this client may try again; 0 = allowed now."""
        now = self.clock()
        q = self._trim(self._per_ip.get(ip, deque()), now)
        a = self._trim(self._all, now)
        waits = []
        if len(q) >= self.max:
            waits.append(q[0] + self.window - now)
        if len(a) >= self.max * 4:
            waits.append(a[0] + self.window - now)
        return int(max(waits)) + 1 if waits else 0

    def failed(self, ip: str) -> None:
        now = self.clock()
        self._per_ip.setdefault(ip, deque()).append(now)
        self._all.append(now)

    def succeeded(self, ip: str) -> None:
        self._per_ip.pop(ip, None)


def client_ip(request, trusted_proxies: str) -> str:
    """The visitor's address for rate limits. Behind a reverse proxy every request comes from the
    proxy, so 20 bad passwords from anyone would lock the approver out (security review, low).
    X-Forwarded-For is trusted ONLY when the direct peer is in TRUSTED_PROXIES (comma-separated IPs
    or CIDRs); then the right-most address that is not itself a trusted proxy is the client."""
    import ipaddress
    peer = request.client.host if request.client else "?"
    nets = []
    for part in (trusted_proxies or "").split(","):
        part = part.strip()
        if part:
            try:
                nets.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                continue

    def trusted(addr: str) -> bool:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        return any(ip in n for n in nets)

    if not nets or not trusted(peer):
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        if not trusted(hop):
            try:
                return str(ipaddress.ip_address(hop))
            except ValueError:
                return peer
    return peer
