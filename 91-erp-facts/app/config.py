"""Settings read once at startup. Refuses to start holding a key that could confirm facts."""
import os
from dataclasses import dataclass, field

FORBIDDEN_KEYS = ("APPROVER_KEY", "FACT_OWNER_KEY")
FORBIDDEN_SUFFIXES = ("APPROVER_KEY", "OWNER_KEY")


class ConfigError(RuntimeError):
    pass


def forbidden_keys_present(env) -> list[str]:
    names = {n for n in env if n in FORBIDDEN_KEYS or n.upper().endswith(FORBIDDEN_SUFFIXES)}
    return sorted(n for n in names if (env.get(n) or "").strip())


@dataclass(frozen=True)
class Config:
    internal_api_key: str = field(repr=False)
    brand_url: str = "http://brand-service:8000"
    mappings_file: str = "config/mappings.example.yaml"
    sync_every_min: int = 0
    max_records: int = 2000
    upstream_timeout: float = 30.0


def load(env=None) -> Config:
    env = os.environ if env is None else env
    bad = forbidden_keys_present(env)
    if bad:
        raise ConfigError(f"refusing to start: {', '.join(bad)} is set. This service only creates drafts; "
                          f"confirming facts is for a person, in 05.")
    key = (env.get("INTERNAL_API_KEY") or "").strip()
    if not key:
        raise ConfigError("INTERNAL_API_KEY is not set")
    url = (env.get("BRAND_URL") or "http://brand-service:8000").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ConfigError("BRAND_URL must start with http:// or https://")

    def num(name, default, lo, hi, cast=int):
        raw = (env.get(name) or "").strip()
        if not raw:
            return default
        try:
            return min(max(cast(raw), lo), hi)
        except ValueError:
            raise ConfigError(f"{name} must be a number, got {raw!r}") from None

    return Config(internal_api_key=key, brand_url=url,
                  mappings_file=(env.get("MAPPINGS_FILE") or "config/mappings.example.yaml").strip(),
                  sync_every_min=num("SYNC_EVERY_MIN", 0, 0, 10080),
                  max_records=num("MAX_RECORDS", 2000, 1, 20000),
                  upstream_timeout=num("UPSTREAM_TIMEOUT", 30.0, 1.0, 300.0, float))
