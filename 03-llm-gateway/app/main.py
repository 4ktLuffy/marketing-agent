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
import jinja2
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field

from app.activity import ActivityLog, provider_label
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
# In-memory activity log (GET /v1/activity): the last ACTIVITY_SIZE calls, metadata only.
ACTIVITY_SIZE = int(os.getenv("ACTIVITY_SIZE", "500"))
# POST /v1/chat/completions (OpenAI-compatible passthrough, used by the n8n chat agent): the
# largest request body accepted. Tool schemas plus 8 turns of history with tool results fit well.
MAX_CHAT_BYTES = int(os.getenv("MAX_CHAT_BYTES", "1000000"))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("llm-gateway")
app = FastAPI(title="llm-gateway")
store = PromptStore(PROMPTS_DIR)
activity = ActivityLog(ACTIVITY_SIZE)
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


def allowed_models() -> set[str]:
    """Models a caller may name: ALLOWED_MODELS, MODEL and models named in prompt files."""
    return ALLOWED_MODELS | {MODEL} | {p.model for p in store.all().values() if p.model}


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


def key_headers() -> dict:
    """Sent to the brand service (05), which wants the same key on reads."""
    key = os.environ.get("INTERNAL_API_KEY")
    return {"X-API-Key": key} if key else {}


class GatewayError(Exception):
    """`kind` is what the activity log records (timeout, unreachable, upstream_5xx, ...): a
    fixed word, never the upstream message, which could quote prompt or output text."""

    def __init__(self, message: str, kind: str = "upstream_error"):
        super().__init__(message)
        self.kind = kind


def _status_kind(code: int) -> str:
    return "rate_limited" if code == 429 else "upstream_5xx" if code >= 500 else "upstream_4xx"


def brand_summary() -> str:
    if not BRAND_URL:
        return ""
    now = time.monotonic()
    if _brand_cache["at"] is not None and now - _brand_cache["at"] < BRAND_TTL:
        return _brand_cache["summary"]
    try:
        r = httpx.get(f"{BRAND_URL}/profile/summary", headers=key_headers(), timeout=5)
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


def facts_max_sensitivity() -> str:
    """Which facts may go into a prompt. With a hosted model the prompt leaves the machine, so
    only public facts unless FACTS_MAX_SENSITIVITY says otherwise; locally internal ones too."""
    v = os.getenv("FACTS_MAX_SENSITIVITY", "").strip().lower()
    if v in ("public", "internal"):
        return v
    return "internal" if os.getenv("LLM_PROVIDER", "ollama").strip().lower() == "ollama" else "public"


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
        r = httpx.get(f"{BRAND_URL}/facts", params={"max_sensitivity": facts_max_sensitivity()},
                      headers=key_headers(), timeout=5)
        r.raise_for_status()
        rows = r.json().get("facts") or []
        text = "\n".join(f"[{f['id']}] {str(f['text']).strip()}" for f in rows if f.get("text"))
        _facts_cache.update(at=now, text=text)
    except (httpx.HTTPError, ValueError, AttributeError, KeyError, TypeError) as exc:
        # Like brand: keep the last known list (empty before the first success), never fail.
        log.warning("brand-service facts unavailable: %s", exc)
    return _facts_cache["text"]


# ---------- brand emoji policy, applied in code to every output
# Models ignore "only these emoji, max 2" in the prompt (4 of 7 misses in the thread/carousel
# eval were emoji; the voice A/B had 2-6 brand errors per 10 posts). The rule is mechanical,
# so the gateway enforces it on every string of every output instead of asking again.
_PICTO = "[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u231A-\u23FF]"
_MOD = "(?:\uFE0F)?(?:[\U0001F3FB-\U0001F3FF])?"
EMOJI_RE = re.compile(rf"[\U0001F1E6-\U0001F1FF]{{2}}|{_PICTO}{_MOD}(?:\u200D{_PICTO}{_MOD})*")
EMOJI_POLICY_SKIP = {p.strip() for p in os.getenv(
    "EMOJI_POLICY_SKIP", "claim_details,detail_check,voice_judge,reflect_rule").split(",") if p.strip()}
_policy_cache: dict = {"at": None, "policy": None}


def _novs(e: str) -> str:
    return e.replace("\ufe0f", "").replace("\ufe0e", "")


def emoji_policy() -> dict | None:
    """{"allowed": set | None, "max": int | None} from 05 /profile, cached like the summary."""
    if not BRAND_URL:
        return None
    now = time.monotonic()
    if _policy_cache["at"] is not None and now - _policy_cache["at"] < BRAND_TTL:
        return _policy_cache["policy"]
    try:
        r = httpx.get(f"{BRAND_URL}/profile", headers=key_headers(), timeout=5)
        r.raise_for_status()
        pol = r.json().get("emoji_policy") or {}
        allowed = {_novs(e) for e in pol.get("allowed") or []} or None
        mx = pol.get("max_per_post")
        _policy_cache.update(at=now, policy={"allowed": allowed, "max": int(mx) if mx is not None else None})
    except Exception as exc:  # noqa: BLE001 - no policy only means no clean-up, never a failed run
        log.warning("brand-service profile unavailable (emoji policy): %s", exc)
    return _policy_cache["policy"]


def apply_emoji_policy(value, policy: dict, removed: list):
    """Every string: drop emoji outside the allowed set, then keep at most `max` per string."""
    if isinstance(value, dict):
        return {k: apply_emoji_policy(v, policy, removed) for k, v in value.items()}
    if isinstance(value, list):
        return [apply_emoji_policy(v, policy, removed) for v in value]
    if not isinstance(value, str) or not EMOJI_RE.search(value):
        return value
    kept = 0

    def keep(m):
        nonlocal kept
        e = m.group(0)
        if (policy["allowed"] is not None and _novs(e) not in policy["allowed"]) or \
                (policy["max"] is not None and kept >= policy["max"]):
            removed.append(e)
            return ""
        kept += 1
        return e
    out = EMOJI_RE.sub(keep, value)
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"[ \t]+([.,!?;:])", r"\1", out)).strip() if out != value else value


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
        kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "unreachable"
        raise GatewayError(f"ollama unreachable at {OLLAMA_URL}: {exc}", kind) from exc
    if r.status_code != 200:
        raise GatewayError(f"ollama {r.status_code}: {r.text[:300]}", _status_kind(r.status_code))
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
        raise GatewayError("LLM_PROVIDER=openai needs OPENAI_BASE_URL and OPENAI_API_KEY", "not_configured")
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
            kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "unreachable"
            raise GatewayError(f"LLM API unreachable at {OPENAI_BASE_URL}: {type(exc).__name__}", kind) from exc
        if r.status_code == 429:
            text = r.text.lower()
            if "per day" in text or "tokens per day" in text or "(tpd)" in text or "(rpd)" in text:
                raise GatewayError(f"daily limit of the LLM API reached: {r.text[:200]}", "daily_limit")
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
            raise GatewayError(f"LLM API {r.status_code}: {r.text[:300]}", _status_kind(r.status_code))
        data = r.json()
        u = data.get("usage") or {}
        usage = {"prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0)}
        content = data["choices"][0]["message"].get("content") or ""
        return THINK_RE.sub("", content).strip(), usage
    raise GatewayError("LLM API still rate-limited after retries", "rate_limited")


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


@app.get("/v1/activity", dependencies=[Depends(require_key)])
def get_activity(since: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=5000)):
    """Calls in flight, the last calls (newest first; `since` = only ids above it) and today's
    totals per model (UTC day). Metadata only: no prompt text, vars or output."""
    return activity.snapshot(since, limit)


def _problem_kind(problem: str | None) -> str:
    p = problem or ""
    return ("invalid_json" if p.startswith("not valid JSON") else "schema_mismatch" if p.startswith("schema")
            else "too_long" if p.startswith("too long") else "empty_output" if p.startswith("empty") else "invalid_output")


@app.post("/v1/run", dependencies=[Depends(require_key)])
def run(req: RunRequest, x_caller: str | None = Header(default=None)):
    prompt = store.all().get(req.prompt)
    if prompt is None:
        raise HTTPException(404, f"unknown prompt '{req.prompt}'")
    if len(json.dumps(req.vars, ensure_ascii=False)) > MAX_VARS_CHARS:
        raise HTTPException(413, f"vars are larger than {MAX_VARS_CHARS} characters")
    if req.model and req.model not in allowed_models():
        raise HTTPException(403, f"model '{req.model}' is not allowed here; set ALLOWED_MODELS on the gateway")
    missing = prompt.missing(req.vars)
    if missing:
        raise HTTPException(422, f"missing required vars: {missing}")

    variables = dict(req.vars)
    if "brand" not in variables:
        variables["brand"] = brand_text()
    if "facts" not in variables and "facts" in prompt.required_vars + prompt.optional_vars:
        variables["facts"] = facts_text()
    try:
        system, user = prompt.render(variables)
    except (jinja2.TemplateError, TypeError, ValueError, AttributeError) as exc:
        # A var of the wrong shape (e.g. `channels` as a list where the template splits a string)
        # is the caller's error: 422 with the reason, not a 500 (agency leak test, night 6).
        raise HTTPException(422, f"prompt '{prompt.name}' could not be filled from these vars: {exc}") from None
    model = req.model or prompt.model or MODEL
    temperature = req.temperature if req.temperature is not None else prompt.temperature
    validator = Draft202012Validator(prompt.schema) if prompt.output == "json" else None

    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": user}
    ]
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    outcome = {"ok": False, "error": "internal", "retries": 0}
    call_id = activity.start(prompt.name, x_caller, model, provider_label(LLM_PROVIDER, OPENAI_BASE_URL))
    try:
        return _run(prompt, model, temperature, validator, messages, usage, outcome)
    finally:
        activity.finish(call_id, ok=outcome["ok"], error=outcome["error"], retries=outcome["retries"],
                        tokens_in=usage["prompt_tokens"], tokens_out=usage["completion_tokens"])


def _run(prompt, model, temperature, validator, messages, usage, outcome):
    started = time.monotonic()
    problem = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        outcome["retries"] = attempt - 1
        try:
            raw, u = chat(model, messages, temperature, prompt.schema, prompt.name)
            for k in usage:
                usage[k] += u.get(k) or 0
        except GatewayError as exc:
            outcome["error"] = exc.kind
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
            removed: list = []
            policy = emoji_policy() if prompt.name not in EMOJI_POLICY_SKIP else None
            if policy and (policy["allowed"] is not None or policy["max"] is not None):
                output = apply_emoji_policy(output, policy, removed)
            outcome.update(ok=True, error=None)
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
                **({"emoji_removed": removed} if removed else {}),
            }
        log.info("prompt=%s attempt=%d rejected: %s", prompt.name, attempt, problem)
        # Show the model its own answer and what was wrong with it.
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": f"That output was rejected: {problem}. "
                                        "Answer again, fixing only that problem."},
        ]
    outcome["error"] = _problem_kind(problem)
    raise HTTPException(502, f"no valid output after {MAX_ATTEMPTS} attempts; last problem: {problem}")


# ---------- OpenAI-compatible chat passthrough (the n8n chat agent, deploy 24)
# The body goes to the provider's own OpenAI-compatible endpoint unchanged (messages, tools,
# tool_calls, stream), except that the model must be on the allowlist. No prompt, brand or
# retries are added: the caller (n8n's agent) owns the conversation. The activity log gets the
# same metadata as /v1/run (prompt name "chat"), never messages or output.

def require_chat_key(x_api_key: str | None = Header(default=None),
                     authorization: str | None = Header(default=None)):
    """X-API-Key like /v1/run, or `Authorization: Bearer <INTERNAL_API_KEY>` (what n8n's
    OpenAI credential and any OpenAI client send). Constant-time compare for both."""
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        return
    bearer = authorization[7:].strip() if authorization and authorization[:7].lower() == "bearer " else None
    for given in (x_api_key, bearer):
        if given and hmac.compare_digest(given.encode(), expected.encode()):
            return
    raise HTTPException(401, "missing or wrong API key (X-API-Key or Authorization: Bearer)")


def _oai_error(status: int, message: str, kind: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind, "code": kind}}, status_code=status,
                        headers=headers)


def _chat_upstream() -> tuple[str, dict] | None:
    """(URL, headers) of the provider's chat endpoint, or None when the hosted one is not set up."""
    if LLM_PROVIDER == "openai":
        if not OPENAI_BASE_URL or not OPENAI_API_KEY:
            return None
        return f"{OPENAI_BASE_URL}/chat/completions", {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    return f"{OLLAMA_URL}/v1/chat/completions", {}


def _usage(obj) -> tuple[int, int]:
    u = obj.get("usage") if isinstance(obj, dict) else None
    if not isinstance(u, dict):
        return 0, 0
    return int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)


def _upstream_error(r: httpx.Response, body: bytes) -> tuple[JSONResponse | Response, str]:
    """An upstream refusal as (response to the caller, activity error kind). 4xx and 429 keep
    their status (so the caller's client can retry a 429); 5xx become 502. The upstream body
    goes back to the caller only, never into the log."""
    text = body[:2000].decode("utf-8", "replace")
    kind = _status_kind(r.status_code)
    if r.status_code == 429 and any(k in text.lower() for k in ("per day", "(tpd)", "(rpd)")):
        kind = "daily_limit"
    if r.status_code >= 500:
        return _oai_error(502, f"upstream model error {r.status_code}: {text[:300]}", kind), kind
    headers = {h: r.headers[h] for h in ("retry-after",) if h in r.headers}
    return Response(body, status_code=r.status_code, headers=headers,
                    media_type=r.headers.get("content-type", "application/json")), kind


@app.get("/v1/models", dependencies=[Depends(require_chat_key)])
def list_models():
    """OpenAI-style model list: the models a caller may use here. n8n's OpenAI credential test
    and its model picker call this."""
    return {"object": "list", "data": [{"id": m, "object": "model", "created": 0, "owned_by": "llm-gateway"}
                                       for m in sorted(allowed_models())]}


@app.post("/v1/chat/completions", dependencies=[Depends(require_chat_key)])
async def chat_completions(request: Request, x_caller: str | None = Header(default=None)):
    raw = await request.body()
    if len(raw) > MAX_CHAT_BYTES:
        return _oai_error(413, f"request body is larger than {MAX_CHAT_BYTES} bytes", "too_large")
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _oai_error(400, "body is not valid JSON", "invalid_request")
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list) or not body["messages"]:
        return _oai_error(400, "body must be a JSON object with a non-empty `messages` list", "invalid_request")
    model = body.get("model") or MODEL
    if not isinstance(model, str) or len(model) > 200:
        return _oai_error(400, "`model` must be a string", "invalid_request")
    if model not in allowed_models():
        return _oai_error(403, f"model '{model}' is not allowed here; set ALLOWED_MODELS on the gateway",
                          "model_not_allowed")
    body["model"] = model
    stream = body.get("stream") is True
    upstream = _chat_upstream()
    call_id = activity.start("chat", x_caller, model, provider_label(LLM_PROVIDER, OPENAI_BASE_URL))
    if upstream is None:
        activity.finish(call_id, ok=False, error="not_configured")
        return _oai_error(502, "LLM_PROVIDER=openai needs OPENAI_BASE_URL and OPENAI_API_KEY", "not_configured")
    url, headers = upstream
    client = httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=10.0))
    try:
        r = await client.send(client.build_request("POST", url, json=body, headers=headers), stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "unreachable"
        activity.finish(call_id, ok=False, error=kind)
        return _oai_error(504 if kind == "timeout" else 502, f"model backend {kind}", kind)

    if r.status_code != 200 or not stream:
        try:
            content = await r.aread()
        except httpx.HTTPError as exc:
            kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "unreachable"
            activity.finish(call_id, ok=False, error=kind)
            return _oai_error(504 if kind == "timeout" else 502, f"model backend {kind}", kind)
        finally:
            await r.aclose()
            await client.aclose()
        if r.status_code != 200:
            resp, kind = _upstream_error(r, content)
            activity.finish(call_id, ok=False, error=kind)
            return resp
        try:
            tin, tout = _usage(json.loads(content))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            tin = tout = 0
        activity.finish(call_id, ok=True, tokens_in=tin, tokens_out=tout)
        return Response(content, status_code=200, media_type=r.headers.get("content-type", "application/json"))

    async def relay():
        """Upstream SSE bytes out unchanged; usage read from the chunks on the way (the final
        chunk carries it when the caller asked for stream_options.include_usage)."""
        ok, kind, tokens, buf = False, "client_closed", [0, 0], b""
        try:
            async for chunk in r.aiter_raw():
                yield chunk
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    line = line.strip()
                    if line.startswith(b"data:") and b'"usage"' in line:
                        try:
                            tin, tout = _usage(json.loads(line[5:]))
                        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                            continue
                        if tin or tout:
                            tokens[:] = [tin, tout]
            ok, kind = True, None
        except httpx.HTTPError as exc:
            kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "unreachable"
        finally:
            await r.aclose()
            await client.aclose()
            activity.finish(call_id, ok=ok, error=kind, tokens_in=tokens[0], tokens_out=tokens[1])

    return StreamingResponse(relay(), status_code=200,
                             media_type=r.headers.get("content-type", "text/event-stream"),
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
