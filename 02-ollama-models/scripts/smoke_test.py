"""Check the three things the agent needs from Ollama, using only the stdlib.

1. mkt-agent emits a real tool call (not prose) when a tool fits.
2. mkt-writer returns schema-valid JSON with `format`.
3. The embedding model returns vectors.

  python3 scripts/smoke_test.py            # default http://localhost:11434
  OLLAMA_HOST=http://gpu-box:11434 python3 scripts/smoke_test.py
"""
import json
import os
import sys
import urllib.request

HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
EMBED_MODEL = os.getenv("EMBED_MODEL", "qwen3-embedding:0.6b")


def post(path, body):
    req = urllib.request.Request(HOST + path, json.dumps(body).encode(), {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def check(name, fn):
    try:
        detail = fn()
        print(f"PASS {name}: {detail}")
        return True
    except Exception as exc:  # report and keep going
        print(f"FAIL {name}: {exc}")
        return False


def tool_call():
    tool = {
        "type": "function",
        "function": {
            "name": "write_social_posts",
            "description": "Write social media posts for given channels",
            "parameters": {
                "type": "object",
                "required": ["topic", "channels"],
                "properties": {
                    "topic": {"type": "string"},
                    "channels": {"type": "string", "description": "comma-separated, e.g. x, linkedin"},
                },
            },
        },
    }
    r = post("/api/chat", {
        "model": "mkt-agent", "stream": False, "tools": [tool],
        "messages": [{"role": "user", "content": "Draft an X and LinkedIn post about our new decaf."}],
    })
    calls = r["message"].get("tool_calls") or []
    assert calls, f"no tool call, got text: {r['message']['content'][:120]!r}"
    fn = calls[0]["function"]
    assert fn["name"] == "write_social_posts", fn
    assert "decaf" in json.dumps(fn["arguments"]).lower(), fn["arguments"]
    return f"{fn['name']}({json.dumps(fn['arguments'])})"


def structured_json():
    schema = {"type": "object", "required": ["headlines"],
              "properties": {"headlines": {"type": "array", "minItems": 3, "items": {"type": "string"}}}}
    r = post("/api/chat", {
        "model": "mkt-writer", "stream": False, "format": schema,
        "messages": [{"role": "user", "content": "Three headlines for a coffee subscription."}],
    })
    data = json.loads(r["message"]["content"])
    assert len(data["headlines"]) >= 3, data
    return data["headlines"][0]


def embeddings():
    r = post("/api/embed", {"model": EMBED_MODEL, "input": ["fresh coffee", "remote work"]})
    vecs = r["embeddings"]
    assert len(vecs) == 2 and len(vecs[0]) > 100, "unexpected embedding shape"
    return f"2 vectors x {len(vecs[0])} dims"


if __name__ == "__main__":
    results = [check("tool calling (mkt-agent)", tool_call),
               check("structured JSON (mkt-writer)", structured_json),
               check(f"embeddings ({EMBED_MODEL})", embeddings)]
    sys.exit(0 if all(results) else 1)
