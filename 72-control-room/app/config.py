"""Settings from the environment, read once when the app is created."""
import os
from dataclasses import dataclass, field


def _url(name: str, default: str) -> str:
    """Unset -> default. Set but empty -> "" = that service isn't installed (core profile)."""
    v = os.environ.get(name)
    return default if v is None else v.strip().rstrip("/")


def _gateways(raw: str) -> list[tuple[str, str]]:
    """ACTIVITY_GATEWAYS: "label=url,label=url" or plain "url,url" (labelled "gateway 2", ...).
    An item with an empty URL is skipped (e.g. "assistant=" on a profile without that gateway)."""
    out = []
    for part in (x.strip() for x in (raw or "").split(",")):
        label, sep, url = part.partition("=") if "=" in part.split("://")[0] else ("", "", part)
        url = url.strip().rstrip("/")
        if url.startswith(("http://", "https://")):
            out.append(((label.strip() or f"gateway {len(out) + 2}")[:40], url))
    return out


def _profile(raw: str | None) -> str:
    """core | growth | full from COMPOSE_PROFILES (the largest one wins; unset = whole stack = full)."""
    p = {x.strip() for x in (raw if raw is not None else "full").split(",")}
    return "full" if "full" in p else "growth" if "growth" in p else "core"


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    user: str
    password: str
    reviewer: str
    control_key: str          # sent to the n8n decision webhook (X-Control-Key)
    internal_key: str         # INTERNAL_API_KEY: reschedule (19 PATCH) and resume (61)
    n8n_url: str              # internal base, e.g. http://n8n:5678 (never shown to the browser)
    n8n_public_url: str       # what the browser may open: the n8n chat page
    calendar_url: str
    campaigns_url: str
    learning_url: str
    engine_url: str
    rules_url: str
    status_url: str
    cards_url: str
    video_url: str
    clips_url: str
    ads_url: str              # 84 ads-sync (performance page ads panel); "" = not installed
    brand_url: str            # 05 brand service (brand setup pages)
    gateway_url: str          # 03 LLM gateway (voice interview -> voice_profile)
    voice_timeout_s: float
    session_hours: float
    idle_minutes: float
    undo_seconds: float
    cookie_secure: str        # auto | true | false
    login_max_failures: int
    login_window_s: float
    decision_timeout_s: float
    ad_library_url: str = ""  # 78 ad-library-sync (positioning map); "" = not installed
    report_url: str = ""      # 21 report-builder (Download of an item as HTML); "" = not installed
    feed_url: str = ""        # 87 feed-optimizer (product feed page); "" = not installed
    extractor_url: str = ""   # 07 page-extractor (onboarding: web pages and PDFs); "" = paste text only
    tasks_url: str = ""       # 88 task-bridge (tasks, packs, evidence, blockers); "" = not installed
    # FACT_OWNER_KEY: sent as X-Owner-Key to 05 ONLY to confirm/retire facts, import, apply a starter
    # kit and confirm/dismiss its rules. Never rendered; "" = those buttons are switched off.
    fact_owner_key: str = ""
    # Activity page: more gateways to read /v1/activity from, as (label, url); GATEWAY_URL is "main".
    activity_gateways: list = field(default_factory=list)
    install_profile: str = "full"   # core | growth | full: which workflows the installer imported
    activity_days: float = 7        # how far back the Activity timeline reads calendar changes
    # Client approval links: the address clients open (https://review.example.com); "" = this request's.
    control_public_url: str = ""
    client_max_failures: int = 10   # wrong PINs / unknown links per address per window
    client_window_s: float = 900
    client_session_minutes: float = 30

    @property
    def internal_urls(self) -> list[str]:
        return [u for u in [self.n8n_url, self.calendar_url, self.campaigns_url, self.learning_url, self.engine_url,
                self.rules_url, self.status_url, self.cards_url, self.video_url, self.clips_url, self.ads_url,
                self.brand_url, self.gateway_url, self.ad_library_url, self.report_url, self.feed_url, self.tasks_url, self.extractor_url,
                *(u for _, u in self.activity_gateways)] if u]


def load() -> Settings:
    user = os.environ.get("CONTROL_USER", "").strip() or "approver"
    return Settings(
        user=user,
        password=os.environ.get("CONTROL_PASSWORD", ""),
        reviewer=os.environ.get("CONTROL_REVIEWER", "").strip() or user,
        control_key=os.environ.get("CONTROL_ROOM_KEY", ""),
        internal_key=os.environ.get("INTERNAL_API_KEY", ""),
        n8n_url=_url("N8N_BASE_URL", "http://n8n:5678"),
        n8n_public_url=_url("N8N_PUBLIC_URL", "http://localhost:5678"),
        calendar_url=_url("CALENDAR_URL", "http://content-calendar:8000"),
        campaigns_url=_url("CAMPAIGNS_URL", "http://campaign-service:8000"),
        learning_url=_url("LEARNING_URL", "http://learning-service:8000"),
        engine_url=_url("ENGINE_URL", "http://content-engine:8000"),
        rules_url=_url("RULES_URL", "http://platform-rules:8000"),
        status_url=_url("STATUS_URL", "http://status-page:8000"),
        cards_url=_url("CARDS_URL", "http://image-cards:8000"),
        video_url=_url("VIDEO_URL", "http://video-assembly:8000"),
        clips_url=_url("CLIPS_URL", "http://clip-finder:8000"),
        # Optional (growth profile): unset or empty = the ads panel says "not installed".
        ads_url=_url("ADS_URL", ""),
        brand_url=_url("BRAND_URL", "http://brand-service:8000"),
        gateway_url=_url("GATEWAY_URL", "http://llm-gateway:8000"),
        ad_library_url=_url("AD_LIBRARY_URL", "http://ad-library-sync:8000"),
        report_url=_url("REPORT_URL", "http://report-builder:8000"),
        # Optional (full profile): unset or empty = the product feed page says "not installed".
        feed_url=_url("FEED_URL", ""),
        # 07 page extractor (core): the onboarding page reads web pages and PDFs through it.
        extractor_url=_url("EXTRACTOR_URL", "http://page-extractor:8000"),
        # 88 task bridge (core): unset = the stack's container; empty = the Tasks pages say "not installed".
        tasks_url=_url("TASKS_URL", "http://task-bridge:8000"),
        fact_owner_key=os.environ.get("FACT_OWNER_KEY", "").strip(),
        activity_gateways=_gateways(os.environ.get("ACTIVITY_GATEWAYS", "")),
        install_profile=_profile(os.environ.get("INSTALL_PROFILE")),
        activity_days=max(1.0, _float("ACTIVITY_DAYS", 7)),
        control_public_url=_url("CONTROL_PUBLIC_URL", ""),
        client_max_failures=max(1, int(_float("CLIENT_PIN_MAX_FAILURES", 10))),
        client_window_s=_float("CLIENT_PIN_WINDOW_MINUTES", 15) * 60,
        client_session_minutes=max(1.0, _float("CLIENT_SESSION_MINUTES", 30)),
        voice_timeout_s=_float("VOICE_TIMEOUT_SECONDS", 300),
        session_hours=_float("SESSION_HOURS", 12),
        idle_minutes=_float("SESSION_IDLE_MINUTES", 120),
        undo_seconds=max(0.0, _float("UNDO_SECONDS", 5)),
        cookie_secure=os.environ.get("COOKIE_SECURE", "auto").strip().lower() or "auto",
        login_max_failures=max(1, int(_float("LOGIN_MAX_FAILURES", 5))),
        login_window_s=_float("LOGIN_WINDOW_MINUTES", 15) * 60,
        decision_timeout_s=_float("DECISION_TIMEOUT_SECONDS", 600),
    )
