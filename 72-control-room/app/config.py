"""Settings from the environment, read once when the app is created."""
import os
from dataclasses import dataclass


def _url(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip().rstrip("/")


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
    session_hours: float
    idle_minutes: float
    undo_seconds: float
    cookie_secure: str        # auto | true | false
    login_max_failures: int
    login_window_s: float
    decision_timeout_s: float

    @property
    def internal_urls(self) -> list[str]:
        return [self.n8n_url, self.calendar_url, self.campaigns_url, self.learning_url, self.engine_url,
                self.rules_url, self.status_url, self.cards_url, self.video_url, self.clips_url]


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
        session_hours=_float("SESSION_HOURS", 12),
        idle_minutes=_float("SESSION_IDLE_MINUTES", 120),
        undo_seconds=max(0.0, _float("UNDO_SECONDS", 5)),
        cookie_secure=os.environ.get("COOKIE_SECURE", "auto").strip().lower() or "auto",
        login_max_failures=max(1, int(_float("LOGIN_MAX_FAILURES", 5))),
        login_window_s=_float("LOGIN_WINDOW_MINUTES", 15) * 60,
        decision_timeout_s=_float("DECISION_TIMEOUT_SECONDS", 600),
    )
