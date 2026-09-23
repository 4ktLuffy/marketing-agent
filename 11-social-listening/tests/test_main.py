import time

import httpx
import respx
from fastapi.testclient import TestClient

from app import main
from app.main import app

client = TestClient(app)
NOW = int(time.time())


def hn_payload():
    return {"hits": [
        {"objectID": "1", "_tags": ["story"], "title": "Ollama 2.0", "url": "https://ollama.com/blog",
         "author": "pg", "points": 120, "num_comments": 40, "created_at_i": NOW - 3600, "story_text": None},
        {"objectID": "2", "_tags": ["comment"], "story_title": "Local LLMs", "author": "dang",
         "points": None, "num_comments": None, "created_at_i": NOW - 60,
         "comment_text": "<p>I use &quot;ollama&quot; daily</p>" + "x" * 800},
        # Older than the window: must be filtered out.
        {"objectID": "3", "_tags": ["story"], "title": "Old", "created_at_i": NOW - 30 * 86400},
    ]}


def reddit_payload():
    return {"data": {"children": [{"data": {
        "title": "Ollama on a Pi", "permalink": "/r/LocalLLaMA/comments/abc/x/", "author": "u1",
        "score": 12, "num_comments": 3, "created_utc": NOW - 600, "selftext": "works **fine**",
    }}]}}


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


@respx.mock
def test_search_merges_sorts_and_normalizes():
    hn = respx.get(main.HN_URL).mock(return_value=httpx.Response(200, json=hn_payload()))
    rd = respx.get(main.REDDIT_URL).mock(return_value=httpx.Response(200, json=reddit_payload()))
    r = client.post("/search", json={"query": "ollama"})
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert [m["source"] for m in body["mentions"]] == ["hackernews", "reddit", "hackernews"]
    comment, post, story = body["mentions"]
    assert comment["title"] == "Local LLMs"
    assert comment["url"] == "https://news.ycombinator.com/item?id=2"
    assert comment["text"].startswith('I use "ollama" daily') and len(comment["text"]) == 500
    assert comment["score"] == 0 and comment["created_at"].endswith("Z")
    assert post["url"] == "https://www.reddit.com/r/LocalLLaMA/comments/abc/x/"
    assert story == {
        "source": "hackernews", "title": "Ollama 2.0", "url": "https://ollama.com/blog", "author": "pg",
        "score": 120, "comments": 40, "created_at": story["created_at"], "text": "",
    }
    params = hn.calls[0].request.url.params
    assert params["tags"] == "(story,comment)" and params["numericFilters"].startswith("created_at_i>")
    assert rd.calls[0].request.headers["user-agent"] == main.REDDIT_USER_AGENT


@respx.mock
def test_reddit_403_is_reported_not_raised():
    respx.get(main.HN_URL).mock(return_value=httpx.Response(200, json=hn_payload()))
    respx.get(main.REDDIT_URL).mock(return_value=httpx.Response(403))
    body = client.post("/search", json={"query": "ollama"}).json()
    assert len(body["mentions"]) == 2
    assert body["errors"][0]["source"] == "reddit" and "403" in body["errors"][0]["error"]


@respx.mock
def test_single_source_only_calls_that_source():
    respx.get(main.HN_URL).mock(side_effect=httpx.ConnectError("down"))
    rd = respx.get(main.REDDIT_URL).mock(return_value=httpx.Response(200, json=reddit_payload()))
    body = client.post("/search", json={"query": "ollama", "sources": ["reddit"], "days": 30}).json()
    assert len(body["mentions"]) == 1 and body["errors"] == []
    assert rd.calls[0].request.url.params["t"] == "month"


def test_unknown_source_is_422():
    assert client.post("/search", json={"query": "x", "sources": ["twitter"]}).status_code == 422
