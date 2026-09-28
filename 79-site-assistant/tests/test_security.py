"""Security review 2026-09-28 (docs/security-review-2026-09-28.md): each test failed before its fix."""
import json

from app import main
from tests.test_main import AUTH, SITE, chat, client, env, ok, world  # noqa: F401  (fixtures)


def notify_texts(world) -> list[str]:
    return [json.loads(c.request.content)["text"] for c in world.notify.calls]


# SA-1: a visitor's words are pasted into the team's Slack/Discord/Teams notification.
def test_notification_cannot_ping_everyone_or_hide_a_link(world):
    evil = "<!channel> @everyone @here urgent: <https://evil.example/login|Reset your admin password> [open](https://evil.example)"
    ok(chat(evil, action="handoff"))
    text = notify_texts(world)[0]
    for bad in ("<!channel>", "@everyone", "@here", "<https://", "](https://"):
        assert bad not in text, bad
    assert "urgent" in text  # the message itself still reaches the team


def test_notification_keeps_plain_text(world):
    ok(chat("Can someone call me back about a 12-person office order?", action="handoff"))
    assert "Can someone call me back about a 12-person office order?" in notify_texts(world)[0]


# SA-2: every new session (or /consent call) could send one more notification: a flood.
def test_notifications_are_capped_per_hour(world, monkeypatch):
    monkeypatch.setenv("NOTIFY_MAX_PER_HOUR", "3")
    monkeypatch.setenv("RATE_IP_PER_MINUTE", "100")
    for i in range(5):
        ok(chat(f"question {i}", action="handoff"))
    assert len(world.notify.calls) == 3
    handoffs = client.get("/admin/handoffs", headers=AUTH).json()
    assert len(handoffs) == 5  # every handoff is still recorded for the team
    capped = [h for h in handoffs if not h["notified"]]
    assert len(capped) == 2 and all("NOTIFY_MAX_PER_HOUR" in h["notify_error"] for h in capped)


def test_repeated_consent_does_not_renotify_the_same_email(world, monkeypatch):
    monkeypatch.setenv("RATE_IP_PER_MINUTE", "100")
    s = ok(chat("talk to a human please"))["session_id"]
    for _ in range(3):
        r = client.post("/consent", json={"session_id": s, "email": "dee@shop.co", "agree": True},
                        headers={"Origin": SITE})
        assert r.status_code == 200
    assert len(world.notify.calls) == 2  # the handoff, then the email once


# SA-3: per-IP limits keyed on a full IPv6 address: one /64 has 2^64 of them.
def test_ipv6_clients_share_their_64_prefix_limit(world, monkeypatch):
    monkeypatch.setenv("TRUST_PROXY", "true")
    monkeypatch.setenv("RATE_IP_PER_MINUTE", "1")
    h = {"Origin": SITE}
    post = lambda ip: client.post("/chat", json={"message": "Are you human?"}, headers=h | {"X-Forwarded-For": ip})  # noqa: E731
    assert post("2001:db8:1:2::1").status_code == 200
    assert post("2001:db8:1:2:ffff::9").status_code == 429  # same /64
    assert post("2001:db8:1:3::1").status_code == 200       # another /64: another client
    assert post("203.0.113.7").status_code == 200           # IPv4 unchanged
    assert post("203.0.113.8").status_code == 200


def test_client_key_unit():
    assert main.rate_key("2001:db8:1:2:3:4:5:6") == "2001:db8:1:2::/64"
    assert main.rate_key("::ffff:203.0.113.9") == "203.0.113.9"
    assert main.rate_key("203.0.113.9") == "203.0.113.9"
    assert main.rate_key("not an ip") == "not an ip"
