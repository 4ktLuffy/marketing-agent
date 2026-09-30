"""Settings, read once at startup, and the checks that refuse to start an unsafe server.

The connector is allowed exactly one secret: INTERNAL_API_KEY (to read facts from 05 and to create
tasks, paste and submit drafts in 88). It must never hold a key that approves, publishes, confirms
or retires anything, so it refuses to start when one is present in its environment.
"""
import os
from dataclasses import dataclass, field

# Keys that approve copy (19, 72) or confirm or retire facts (05). Present = refuse to start.
FORBIDDEN_KEYS = ("APPROVER_KEY", "FACT_OWNER_KEY")
FORBIDDEN_SUFFIXES = ("APPROVER_KEY", "OWNER_KEY")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
MIN_TOKEN_CHARS = 24


class ConfigError(RuntimeError):
    """The server must not start with this configuration."""


def _int(env: dict, name: str, default: int, lo: int, hi: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from None
    return min(max(v, lo), hi)


def _float(env: dict, name: str, default: float, lo: float, hi: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    return min(max(v, lo), hi)


def _bool(env: dict, name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def _url(env: dict, name: str) -> str:
    v = (env.get(name) or "").strip().rstrip("/")
    if not v:
        raise ConfigError(f"{name} is not set")
    if not v.startswith(("http://", "https://")):
        raise ConfigError(f"{name} must start with http:// or https://")
    return v


def forbidden_keys_present(env: dict) -> list[str]:
    """Names (never values) of approver/owner keys set to a non-empty value."""
    names = {n for n in env if n in FORBIDDEN_KEYS or n.upper().endswith(FORBIDDEN_SUFFIXES)}
    return sorted(n for n in names if (env.get(n) or "").strip())


@dataclass(frozen=True)
class Config:
    internal_api_key: str = field(repr=False)
    brand_url: str
    task_bridge_url: str
    upstream_timeout: float = 150.0
    max_text_chars: int = 40_000
    tool_calls_per_min: int = 30
    # HTTP only
    host: str = "127.0.0.1"
    port: int = 8000
    token: str | None = field(default=None, repr=False)
    token_in_path: bool = False
    allowed_hosts: tuple[str, ...] = ()
    http_requests_per_min: int = 120
    max_body_bytes: int = 1_048_576
    json_response: bool = True


def load(env: dict | None = None, transport: str = "stdio") -> Config:
    """Read and check the settings. Raises ConfigError with a message a person can act on."""
    env = dict(os.environ if env is None else env)
    bad = forbidden_keys_present(env)
    if bad:
        raise ConfigError(
            "refusing to start: " + ", ".join(bad) + " is set. This connector gives a chatbot tools, so it "
            "must never hold a key that approves, publishes, confirms or retires anything. Remove "
            + ("it" if len(bad) == 1 else "them") + " from this service's environment; approval happens "
            "only in the control room (72), by a person.")
    key = (env.get("INTERNAL_API_KEY") or "").strip()
    if not key:
        raise ConfigError("INTERNAL_API_KEY is not set (the same value as in 01-marketing-stack/.env)")
    cfg = dict(
        internal_api_key=key,
        brand_url=_url(env, "BRAND_URL"),
        task_bridge_url=_url(env, "TASK_BRIDGE_URL"),
        upstream_timeout=_float(env, "UPSTREAM_TIMEOUT", 150.0, 1.0, 600.0),
        max_text_chars=_int(env, "MAX_TEXT_CHARS", 40_000, 1_000, 200_000),
        tool_calls_per_min=_int(env, "TOOL_CALLS_PER_MIN", 30, 1, 10_000),
    )
    if transport == "stdio":
        return Config(**cfg)

    host = (env.get("MCP_HOST") or "127.0.0.1").strip()
    token = (env.get("MCP_TOKEN") or "").strip() or None
    if token is not None and len(token) < MIN_TOKEN_CHARS:
        raise ConfigError(f"MCP_TOKEN is too short: use at least {MIN_TOKEN_CHARS} random characters "
                          "(for example: python -c 'import secrets; print(secrets.token_urlsafe(32))')")
    if host not in LOCAL_HOSTS and token is None:
        raise ConfigError(f"refusing to listen on {host} without MCP_TOKEN: any address other than localhost "
                          "can be reached from outside this machine. Set MCP_TOKEN, or MCP_HOST=127.0.0.1.")
    token_in_path = _bool(env, "MCP_TOKEN_IN_PATH", False)
    if token_in_path and token is None:
        raise ConfigError("MCP_TOKEN_IN_PATH=true needs MCP_TOKEN")
    hosts = tuple(h.strip() for h in (env.get("MCP_ALLOWED_HOSTS") or "").split(",") if h.strip())
    return Config(**cfg, host=host, port=_int(env, "MCP_PORT", 8000, 1, 65535), token=token,
                  token_in_path=token_in_path, allowed_hosts=hosts,
                  http_requests_per_min=_int(env, "HTTP_REQUESTS_PER_MIN", 120, 1, 100_000),
                  max_body_bytes=_int(env, "MAX_BODY_BYTES", 1_048_576, 16_384, 4_194_304),
                  json_response=_bool(env, "MCP_JSON_RESPONSE", True))
