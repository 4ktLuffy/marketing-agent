"""Platform rules: character limits and hashtag caps per channel. No LLM."""
import re

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="platform-rules")

# limit: hard max length (error above it).
# url_length: every http(s) URL counts as this many chars (link shorteners on X/Mastodon).
# max_hashtags: error above it. warn_above / warn_below: soft bounds (warning only).
RULES: dict[str, dict] = {
    "x": {"limit": 280, "url_length": 23,
          "note": "URLs count as 23 chars (t.co)"},
    "linkedin": {"limit": 3000},
    "instagram": {"limit": 2200, "max_hashtags": 30},
    "facebook": {"limit": 63206},
    "threads": {"limit": 500},
    "mastodon": {"limit": 500, "url_length": 23,
                 "note": "default instance limit; URLs count as 23 chars"},
    "email_subject": {"limit": 78, "warn_above": 60,
                      "note": "60 recommended so it is not cut off on mobile; 78 hard max"},
    "google_ads_headline": {"limit": 30},
    "google_ads_description": {"limit": 90},
    "meta_description": {"limit": 160, "warn_below": 70,
                         "note": "Google truncates around 160; under 70 wastes the snippet"},
}

URL_RE = re.compile(r"https?://\S+")
HASHTAG_RE = re.compile(r"(?<![\w#&])#\w+")


def measure(text: str, rule: dict) -> int:
    """Length in Unicode code points, with URLs weighted where the platform does so."""
    url_length = rule.get("url_length")
    if url_length is None:
        return len(text)
    urls = URL_RE.findall(text)
    return len(URL_RE.sub("", text)) + url_length * len(urls)


class ValidateRequest(BaseModel):
    channel: str
    text: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/rules")
def rules():
    return {
        "length_unit": "unicode code points (emoji = 1, not 2 as X counts them)",
        "channels": RULES,
    }


@app.post("/validate")
def validate(req: ValidateRequest):
    channel = req.channel.strip().lower()
    rule = RULES.get(channel)
    if rule is None:
        raise HTTPException(
            422, f"unknown channel '{req.channel}'; valid channels: {', '.join(RULES)}"
        )

    length = measure(req.text, rule)
    hashtags = len(HASHTAG_RE.findall(req.text))
    violations = []

    def add(rule_id, detail, severity="error"):
        violations.append({"rule": rule_id, "detail": detail, "severity": severity})

    if not req.text.strip():
        add("empty", "text is empty")
    if length > rule["limit"]:
        add("too_long", f"{length} chars, limit {rule['limit']} (over by {length - rule['limit']})")
    elif "warn_above" in rule and length > rule["warn_above"]:
        add("above_recommended", f"{length} chars, recommended max {rule['warn_above']}", "warn")
    if "warn_below" in rule and 0 < length < rule["warn_below"]:
        add("below_recommended", f"{length} chars, recommended min {rule['warn_below']}", "warn")
    if "max_hashtags" in rule and hashtags > rule["max_hashtags"]:
        add("too_many_hashtags", f"{hashtags} hashtags, max {rule['max_hashtags']}")

    return {
        "ok": not any(v["severity"] == "error" for v in violations),
        "channel": channel,
        "length": length,
        "limit": rule["limit"],
        "hashtags": hashtags,
        "violations": violations,
    }
