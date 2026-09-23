"""LLM gateway: named prompts in, validated text or JSON out.

Small local models drift out of format. Every LLM call in the marketing agent goes
through here so that rendering, JSON-schema enforcement and retries live in one place.
"""
import json
import logging
import os
import re
import time

import httpx
from fastapi import FastAPI, HTTPException
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from app.prompts import PromptStore

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
MODEL = os.getenv("MODEL", "qwen2.5:7b")
PROMPTS_DIR = os.getenv("PROMPTS_DIR", "/prompts")
BRAND_URL = os.getenv("BRAND_URL", "").rstrip("/")
LEARNING_URL = os.getenv("LEARNING_URL", "").rstrip("/")
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
NUM_CTX = int(os.getenv("NUM_CTX", "8192"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "240"))
BRAND_TTL = 60.0

log = logging.getLogger("llm-gateway")
app = FastAPI(title="llm-gateway")
store = PromptStore(PROMPTS_DIR)
_brand_cache: dict = {"at": 0.0, "summary": ""}
_learning_cache: dict = {"at": 0.0, "summary": ""}

THINK_RE = re.compile(r"<think>.*?</think>", re.S)
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")
# Small models often wrap a whole text answer in ```markdown ... ```; unwrap it.
TEXT_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*)\n```$", re.S)


class RunRequest(BaseModel):
    prompt: str
    vars: dict = {}
    model: str | None = None
    temperature: float | None = None


class GatewayError(Exception):
    pass


def brand_summary() -> str:
    if not BRAND_URL:
        return ""
    now = time.monotonic()
    if now - _brand_cache["at"] < BRAND_TTL:
        return _brand_cache["summary"]
    try:
        r = httpx.get(f"{BRAND_URL}/profile/summary", timeout=5)
        r.raise_for_status()
        _brand_cache.update(at=now, summary=r.json().get("summary", ""))
    except httpx.HTTPError as exc:
        # Keep serving with the last known summary rather than failing every call.
        log.warning("brand-service unavailable: %s", exc)
    return _brand_cache["summary"]


def learning_summary() -> str:
    """Active rules learned from reviewer edits (deploy 46); appended to the brand text."""
    if not LEARNING_URL:
        return ""
    now = time.monotonic()
    if now - _learning_cache["at"] < BRAND_TTL:
        return _learning_cache["summary"]
    try:
        r = httpx.get(f"{LEARNING_URL}/rules/summary", timeout=5)
        r.raise_for_status()
        _learning_cache.update(at=now, summary=(r.json().get("summary") or "").strip())
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        # Same as brand: keep the last known rules rather than failing the call.
        log.warning("learning-service unavailable: %s", exc)
    return _learning_cache["summary"]


def brand_text() -> str:
    return "\n\n".join(part for part in (brand_summary(), learning_summary()) if part)


def ollama_chat(model: str, messages: list[dict], temperature: float, schema: dict | None) -> str:
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": NUM_CTX},
    }
    if schema is not None:
        body["format"] = schema  # Ollama structured outputs: constrains decoding to the schema
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=REQUEST_TIMEOUT)
    except httpx.HTTPError as exc:
        raise GatewayError(f"ollama unreachable at {OLLAMA_URL}: {exc}") from exc
    if r.status_code != 200:
        raise GatewayError(f"ollama {r.status_code}: {r.text[:300]}")
    return THINK_RE.sub("", r.json()["message"]["content"]).strip()


def check_json(raw: str, validator: Draft202012Validator):
    try:
        data = json.loads(FENCE_RE.sub("", raw))
    except json.JSONDecodeError as exc:
        return None, f"not valid JSON ({exc.msg} at char {exc.pos})"
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        where = "/".join(str(p) for p in e.path) or "(root)"
        return None, f"schema violation at {where}: {e.message}"
    return data, None


@app.get("/health")
def health():
    try:
        ok = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3).status_code == 200
    except httpx.HTTPError:
        ok = False
    return {"status": "ok", "model": MODEL, "ollama": ok}


@app.get("/v1/prompts")
def list_prompts():
    return [
        {
            "name": p.name,
            "description": p.description,
            "output": p.output,
            "required_vars": p.required_vars,
            "optional_vars": p.optional_vars,
        }
        for p in store.all().values()
    ]


@app.post("/v1/run")
def run(req: RunRequest):
    prompt = store.all().get(req.prompt)
    if prompt is None:
        raise HTTPException(404, f"unknown prompt '{req.prompt}'")
    missing = prompt.missing(req.vars)
    if missing:
        raise HTTPException(422, f"missing required vars: {missing}")

    variables = dict(req.vars)
    if "brand" not in variables:
        variables["brand"] = brand_text()
    system, user = prompt.render(variables)
    model = req.model or prompt.model or MODEL
    temperature = req.temperature if req.temperature is not None else prompt.temperature
    validator = Draft202012Validator(prompt.schema) if prompt.output == "json" else None

    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": user}
    ]
    started = time.monotonic()
    problem = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = ollama_chat(model, messages, temperature, prompt.schema)
        except GatewayError as exc:
            raise HTTPException(502, str(exc)) from exc

        if validator is not None:
            output, problem = check_json(raw, validator)
        elif prompt.max_chars and len(raw) > prompt.max_chars:
            output, problem = None, f"too long: {len(raw)} chars, limit {prompt.max_chars}"
        elif not raw:
            output, problem = None, "empty output"
        else:
            output, problem = TEXT_FENCE_RE.sub(r"\1", raw).strip(), None

        if problem is None:
            return {
                "prompt": prompt.name,
                "model": model,
                "output": output,
                "attempts": attempt,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
        log.info("prompt=%s attempt=%d rejected: %s", prompt.name, attempt, problem)
        # Show the model its own answer and what was wrong with it.
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": f"That output was rejected: {problem}. "
                                        "Answer again, fixing only that problem."},
        ]
    raise HTTPException(502, f"no valid output after {MAX_ATTEMPTS} attempts; last problem: {problem}")
