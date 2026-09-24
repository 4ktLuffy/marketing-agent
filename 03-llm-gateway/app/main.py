"""LLM gateway: named prompts in, validated text or JSON out.

Small local models drift out of format. Every LLM call in the marketing agent goes
through here so that rendering, JSON-schema enforcement and retries live in one place.
"""
import hmac
import json
import logging
import os
import re
import time

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field

from app.prompts import PromptStore

# LLM_PROVIDER=ollama (default, local) or openai (any OpenAI-compatible API: Groq,
# OpenRouter, Together, OpenAI...). Keys come only from the environment and are never logged.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").rstrip("/")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
RATE_LIMIT_RETRIES = int(os.getenv("RATE_LIMIT_RETRIES", "4"))
# For reasoning models (e.g. gpt-oss). Measured on Groq gpt-oss-120b: at the default effort
# the social_posts JSON failed 2/2 (empty output); at "low" it passed 4/4 with ~300 tokens.
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
MODEL = os.getenv("MODEL", "qwen2.5:7b")
PROMPTS_DIR = os.getenv("PROMPTS_DIR", "/prompts")
BRAND_URL = os.getenv("BRAND_URL", "").rstrip("/")
LEARNING_URL = os.getenv("LEARNING_URL", "").rstrip("/")
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
NUM_CTX = int(os.getenv("NUM_CTX", "8192"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "240"))
BRAND_TTL = 60.0

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("llm-gateway")
app = FastAPI(title="llm-gateway")
store = PromptStore(PROMPTS_DIR)
# "at" is None until the first successful fetch. (It used to start at 0.0, which on a
# machine booted less than BRAND_TTL seconds ago looked like a fresh cache, so the first
# minute of prompts went out without the brand profile.)
_brand_cache: dict = {"at": None, "summary": ""}
_learning_cache: dict = {"at": None, "summary": ""}
_facts_cache: dict = {"at": None, "text": ""}

THINK_RE = re.compile(r"<think>.*?</think>", re.S)
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")
# Small models often wrap a whole text answer in ```markdown ... ```; unwrap it.
TEXT_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*)\n```$", re.S)


# Every call costs model time or hosted-model tokens: cap what one request can ask for.
MAX_VARS_CHARS = int(os.getenv("MAX_VARS_CHARS", "60000"))
# A caller may pick a model only from this list (plus MODEL and models named in prompt files):
# otherwise anything on the network could run arbitrary (paid) models through the gateway.
ALLOWED_MODELS = {m.strip() for m in os.getenv("ALLOWED_MODELS", "").split(",") if m.strip()}


class RunRequest(BaseModel):
    prompt: str = Field(max_length=100)
    vars: dict = {}
    model: str | None = Field(default=None, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)


def require_key(x_api_key: str | None = Header(default=None)):
    """Enforced whenever INTERNAL_API_KEY is set (the stack sets it); open for local evals."""
    expected = os.environ.get("INTERNAL_API_KEY")
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(401, "missing or wrong X-API-Key")


class GatewayError(Exception):
    pass


def brand_summary() -> str:
    if not BRAND_URL:
        return ""
    now = time.monotonic()
    if _brand_cache["at"] is not None and now - _brand_cache["at"] < BRAND_TTL:
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
    if _learning_cache["at"] is not None and now - _learning_cache["at"] < BRAND_TTL:
        return _learning_cache["summary"]
    try:
        r = httpx.get(f"{LEARNING_URL}/rules/summary", timeout=5)
        r.raise_for_status()
        _learning_cache.update(at=now, summary=(r.json().get("summary") or "").strip())
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        # Same as brand: keep the last known rules rather than failing the call.
        log.warning("learning-service unavailable: %s", exc)
    return _learning_cache["summary"]


def facts_text() -> str:
    """Approved facts from 05 `/facts` as numbered lines ("[f1] ..."), for prompts that
    declare a `facts` var. Writing prompts that only saw the brand summary invented tasting
    notes, dates and numbers; with the full list they have something true to say instead."""
    if not BRAND_URL:
        return ""
    now = time.monotonic()
    if _facts_cache["at"] is not None and now - _facts_cache["at"] < BRAND_TTL:
        return _facts_cache["text"]
    try:
        r = httpx.get(f"{BRAND_URL}/facts", timeout=5)
        r.raise_for_status()
        rows = r.json().get("facts") or []
        text = "\n".join(f"[{f['id']}] {str(f['text']).strip()}" for f in rows if f.get("text"))
        _facts_cache.update(at=now, text=text)
    except (httpx.HTTPError, ValueError, AttributeError, KeyError, TypeError) as exc:
        # Like brand: keep the last known list (empty before the first success), never fail.
        log.warning("brand-service facts unavailable: %s", exc)
    return _facts_cache["text"]


def brand_text() -> str:
    return "\n\n".join(part for part in (brand_summary(), learning_summary()) if part)


def ollama_chat(model: str, messages: list[dict], temperature: float, schema: dict | None) -> tuple[str, dict]:
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
    data = r.json()
    usage = {"prompt_tokens": data.get("prompt_eval_count", 0), "completion_tokens": data.get("eval_count", 0)}
    return THINK_RE.sub("", data["message"]["content"]).strip(), usage


DURATION_RE = re.compile(r"(?:(\d+)m)?([\d.]+)(ms|s)")


def wait_seconds(r: httpx.Response) -> float:
    """How long a 429 asks us to wait (retry-after, or x-ratelimit-reset-* like "7.6s", "1m2s")."""
    for h in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        v = r.headers.get(h)
        if not v:
            continue
        try:
            return float(v)
        except ValueError:
            m = DURATION_RE.fullmatch(v.strip())
            if m:
                secs = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
                return int(m.group(1) or 0) * 60 + secs
    return 5.0


def openai_chat(model: str, messages: list[dict], temperature: float, schema: dict | None,
                name: str = "output") -> tuple[str, dict]:
    if not OPENAI_BASE_URL or not OPENAI_API_KEY:
        raise GatewayError("LLM_PROVIDER=openai needs OPENAI_BASE_URL and OPENAI_API_KEY")
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    body = {"model": model, "messages": messages, "temperature": temperature}
    if REASONING_EFFORT:
        body["reasoning_effort"] = REASONING_EFFORT
    if schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": {
            "name": re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64], "schema": schema, "strict": False}}
    fell_back = False
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            r = httpx.post(f"{OPENAI_BASE_URL}/chat/completions", json=body, headers=headers, timeout=REQUEST_TIMEOUT)
        except httpx.HTTPError as exc:
            raise GatewayError(f"LLM API unreachable at {OPENAI_BASE_URL}: {type(exc).__name__}") from exc
        if r.status_code == 429:
            text = r.text.lower()
            if "per day" in text or "tokens per day" in text or "(tpd)" in text or "(rpd)" in text:
                raise GatewayError(f"daily limit of the LLM API reached: {r.text[:200]}")
            if attempt < RATE_LIMIT_RETRIES:
                time.sleep(min(wait_seconds(r), 60.0))
                continue
        if r.status_code == 400 and schema is not None and "json_validate_failed" in r.text:
            # Groq checked the output against the schema itself and refused it. Hand the
            # model's attempt to our own validator, which retries with precise feedback.
            try:
                failed = (r.json().get("error") or {}).get("failed_generation")
            except ValueError:
                failed = None
            if failed:
                return THINK_RE.sub("", failed).strip(), {"prompt_tokens": 0, "completion_tokens": 0}
        if r.status_code == 400 and schema is not None and not fell_back and (
                "response_format" in r.text or "json_validate_failed" in r.text):
            # Model without json_schema support: plain JSON mode, schema stated in the prompt.
            fell_back = True
            body["response_format"] = {"type": "json_object"}
            body["messages"] = [{"role": "system", "content": "Respond only with one JSON object that matches "
                                 f"this JSON Schema:\n{json.dumps(schema)}"}] + messages
            continue
        if r.status_code != 200:
            raise GatewayError(f"LLM API {r.status_code}: {r.text[:300]}")
        data = r.json()
        u = data.get("usage") or {}
        usage = {"prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0)}
        content = data["choices"][0]["message"].get("content") or ""
        return THINK_RE.sub("", content).strip(), usage
    raise GatewayError("LLM API still rate-limited after retries")


def chat(model: str, messages: list[dict], temperature: float, schema: dict | None, name: str) -> tuple[str, dict]:
    if LLM_PROVIDER == "openai":
        return openai_chat(model, messages, temperature, schema, name)
    return ollama_chat(model, messages, temperature, schema)


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
    if LLM_PROVIDER == "openai":
        return {"status": "ok", "provider": "openai", "base_url": OPENAI_BASE_URL, "model": MODEL,
                "key_set": bool(OPENAI_API_KEY)}
    try:
        ok = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3).status_code == 200
    except httpx.HTTPError:
        ok = False
    return {"status": "ok", "provider": "ollama", "model": MODEL, "ollama": ok}


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


@app.post("/v1/run", dependencies=[Depends(require_key)])
def run(req: RunRequest):
    prompt = store.all().get(req.prompt)
    if prompt is None:
        raise HTTPException(404, f"unknown prompt '{req.prompt}'")
    if len(json.dumps(req.vars, ensure_ascii=False)) > MAX_VARS_CHARS:
        raise HTTPException(413, f"vars are larger than {MAX_VARS_CHARS} characters")
    if req.model and req.model not in ALLOWED_MODELS | {MODEL} | {p.model for p in store.all().values() if p.model}:
        raise HTTPException(403, f"model '{req.model}' is not allowed here; set ALLOWED_MODELS on the gateway")
    missing = prompt.missing(req.vars)
    if missing:
        raise HTTPException(422, f"missing required vars: {missing}")

    variables = dict(req.vars)
    if "brand" not in variables:
        variables["brand"] = brand_text()
    if "facts" not in variables and "facts" in prompt.required_vars + prompt.optional_vars:
        variables["facts"] = facts_text()
    system, user = prompt.render(variables)
    model = req.model or prompt.model or MODEL
    temperature = req.temperature if req.temperature is not None else prompt.temperature
    validator = Draft202012Validator(prompt.schema) if prompt.output == "json" else None

    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": user}
    ]
    started = time.monotonic()
    problem = None
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw, u = chat(model, messages, temperature, prompt.schema, prompt.name)
            usage = {k: usage[k] + (u.get(k) or 0) for k in usage}
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
            # One line per call, so token spend on a hosted provider can be summed from the logs.
            log.info("prompt=%s model=%s attempts=%d prompt_tokens=%d completion_tokens=%d",
                     prompt.name, model, attempt, usage["prompt_tokens"], usage["completion_tokens"])
            return {
                "prompt": prompt.name,
                "model": model,
                "output": output,
                "attempts": attempt,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "usage": usage,
            }
        log.info("prompt=%s attempt=%d rejected: %s", prompt.name, attempt, problem)
        # Show the model its own answer and what was wrong with it.
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": f"That output was rejected: {problem}. "
                                        "Answer again, fixing only that problem."},
        ]
    raise HTTPException(502, f"no valid output after {MAX_ATTEMPTS} attempts; last problem: {problem}")
