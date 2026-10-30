"""Model wrapper with two tiers (cheap / strong), fallbacks, time limits, JSON output and token accounting.

Order tried for each call:
  1. this tier's Gemini models (main, then backup)
  2. Groq (a different provider, so Google's busy periods don't affect it), if GROQ_API_KEY is set
  3. the other tier's Gemini models, as a last resort

Every call is logged into a usage tracker so the UI can show what an answer cost.
If no API key is set, `available()` is False and callers fall back to non-AI output.
"""

import contextvars
import json
import logging
import threading
import time
from typing import Any, Literal

from pydantic import BaseModel

from app.config import settings
from app.obs import timed

log = logging.getLogger("finn.llm")

Tier = Literal["cheap", "strong"]

_client = None
_client_lock = threading.Lock()
_working_model: dict[str, str] = {}
# Free tiers are rate-limited per minute, so keep concurrency low.
_slots = threading.Semaphore(3)


class Usage:
    def __init__(self) -> None:
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.models: dict[str, int] = {}
        self._lock = threading.Lock()

    def add(self, model: str, inp: int, out: int) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += inp
            self.output_tokens += out
            self.models[model] = self.models.get(model, 0) + 1

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "models": self.models,
        }


_usage: contextvars.ContextVar[Usage | None] = contextvars.ContextVar("usage", default=None)


def track_usage() -> Usage:
    """Start tracking usage for the current context (a request or a background job)."""
    u = Usage()
    _usage.set(u)
    return u


def available() -> bool:
    return bool(settings.gemini_api_key or settings.groq_api_key)


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            from google import genai

            _client = genai.Client(api_key=settings.gemini_api_key)
        return _client


def _models(tier: Tier) -> list[str]:
    if not settings.gemini_api_key:
        return []
    raw = settings.model_cheap if tier == "cheap" else settings.model_strong
    return [m.strip() for m in raw.split(",") if m.strip()]


GROQ = "groq:"  # prefix that marks a Groq model in the candidate list


def _candidates(tier: Tier, allow_groq: bool = True) -> list[str]:
    """Models to try, in order: this tier's Gemini models (last one that worked first),
    then Groq, then the other tier's Gemini models as a last resort."""
    own = _models(tier)
    known = _working_model.get(tier)
    if known in own:
        own.remove(known)
        own.insert(0, known)
    groq = []
    if allow_groq and settings.groq_api_key:
        groq = [GROQ + (settings.groq_model_cheap if tier == "cheap" else settings.groq_model_strong)]
    other = _models("strong" if tier == "cheap" else "cheap")
    return own + groq + [m for m in other if m not in own]


# Time limits (seconds). Normal calls take 2-13s (cheap) and 18-28s (strong).
CALL_TIMEOUT = {"cheap": 20.0, "strong": 40.0}   # one Gemini request
GEMINI_BUDGET = {"cheap": 35.0, "strong": 60.0}  # all Gemini attempts before we jump to Groq
GROQ_TIMEOUT = 20.0                              # one Groq request (usually 1-5s)
SERVER_RETRIES = 1        # on 503/500: retry the same model once...
SERVER_RETRY_PAUSE = 2.0  # ...after this many seconds, then move to the next model


class Reply:
    """One model answer, the same shape whichever provider gave it."""

    def __init__(self, text: str, model: str, parsed: Any = None, raw: Any = None):
        self.text = text
        self.model = model
        self.parsed = parsed
        self.raw = raw


class _GroqError(Exception):
    pass


def _groq(model: str, prompt: str, system: str | None, schema: type[BaseModel] | None, t: dict) -> Reply:
    """One call to Groq's OpenAI-style API. For JSON, the schema goes in the instructions
    and the answer is checked against it."""
    import httpx

    sys_text = system or ""
    if schema is not None:
        sys_text += (
            "\n\nReply with ONLY a JSON object (no prose, no code fences) that matches this JSON Schema:\n"
            + json.dumps(schema.model_json_schema())
        )
    body: dict[str, Any] = {
        "model": model,
        "messages": ([{"role": "system", "content": sys_text.strip()}] if sys_text.strip() else [])
        + [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    if schema is not None:
        body["response_format"] = {"type": "json_object"}
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"  # these models "think" first; keep it short
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        json=body,
        timeout=GROQ_TIMEOUT,
    )
    if r.status_code != 200:
        raise _GroqError(f"{r.status_code}: {r.text[:200]}")
    data = r.json()
    usage = data.get("usage") or {}
    t["in_tok"] = usage.get("prompt_tokens")
    t["out_tok"] = usage.get("completion_tokens")
    text = (data["choices"][0]["message"].get("content") or "").strip()
    parsed = None
    if schema is not None:
        cleaned = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            parsed = schema.model_validate(json.loads(cleaned))
        except Exception as e:  # noqa: BLE001
            raise _GroqError(f"answer didn't match the expected format: {e}") from e
    u = _usage.get()
    if u:
        u.add(GROQ + model, usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0)
    return Reply(text, GROQ + model, parsed)


def _call(
    tier: Tier,
    contents: Any,
    config: Any,
    purpose: str = "",
    schema: type[BaseModel] | None = None,
    system: str | None = None,
    allow_groq: bool = True,
) -> Reply:
    """Try each candidate model until one answers. Failure handling:
    - 503/500 (model busy): wait 2s, retry once, then switch to the next model.
    - 429 (rate limited): switch to the next model straight away, no waiting.
    - 400/403/404 (model missing or not allowed): switch to the next model.
    - no reply within the time limit, or a network error: switch to the next model.
    - Gemini still failing after GEMINI_BUDGET seconds: skip straight to Groq.
    - anything else (e.g. bad API key): give up."""
    import httpx
    from google.genai import errors, types

    chars = len(contents) if isinstance(contents, str) else None
    own = _models(tier)
    candidates = _candidates(tier, allow_groq)
    if config is not None:
        config.http_options = types.HttpOptions(timeout=int(CALL_TIMEOUT[tier] * 1000))
    last_err: Exception | None = None
    started = time.perf_counter()
    for i, model in enumerate(candidates):
        is_groq = model.startswith(GROQ)
        over_budget = time.perf_counter() - started > GEMINI_BUDGET[tier]
        if over_budget and not is_groq:
            log.warning("llm %s: Gemini has had %.0fs, skipping %s", purpose, time.perf_counter() - started, model)
            continue
        nxt = candidates[i + 1] if i + 1 < len(candidates) else None
        switch_note = f"switching to {nxt}" if nxt else "no models left"

        if is_groq:
            if not isinstance(contents, str):
                continue
            try:
                with timed(log, "llm call", purpose=purpose, model=model, tier=tier, prompt_chars=chars, attempt=1) as t:
                    reply = _groq(model[len(GROQ):], contents, system, schema, t)
                log.warning("llm %s answered by Groq (%s) because Gemini was busy or slow", purpose, model[len(GROQ):])
                return reply
            except (_GroqError, httpx.TimeoutException, httpx.TransportError) as e:
                last_err = e
                log.warning("llm %s: Groq failed (%s), %s", purpose, str(e)[:200], switch_note)
                continue

        for attempt in range(1 + SERVER_RETRIES):
            waited = time.perf_counter()
            try:
                with _slots:
                    queued = time.perf_counter() - waited
                    if queued > 0.5:
                        log.info("llm %s waited %.2fs for a free slot", purpose, queued)
                    with timed(log, "llm call", purpose=purpose, model=model, tier=tier, prompt_chars=chars, attempt=attempt + 1) as t:
                        resp = _get_client().models.generate_content(model=model, contents=contents, config=config)
                        meta = resp.usage_metadata
                        if meta:
                            t["in_tok"] = meta.prompt_token_count
                            t["out_tok"] = meta.candidates_token_count
                            t["think_tok"] = meta.thoughts_token_count
                if model in own:  # don't make a cross-tier fallback the tier's default
                    _working_model[tier] = model
                elif i > 0:
                    log.warning("llm %s answered by fallback model %s (other tier)", purpose, model)
                u = _usage.get()
                if u and meta:
                    u.add(
                        model,
                        meta.prompt_token_count or 0,
                        (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
                    )
                return Reply(resp.text or "", model, parsed=resp.parsed, raw=resp)
            except errors.ClientError as e:
                last_err = e
                if e.code == 429:
                    log.warning("llm %s rate-limited on %s, %s\n    ↳ This model hit its usage limit, so we try another model instead of waiting.",
                                purpose, model, switch_note)
                    break
                if e.code in (400, 403, 404):
                    log.warning("llm %s: model %s unavailable (%s), %s: %s", purpose, model, e.code, switch_note, str(e)[:200])
                    break
                raise
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_err = e
                log.warning("llm %s: no reply from %s within %.0fs (%s), %s\n    ↳ The model took too long or the connection dropped, so we try another model.",
                            purpose, model, CALL_TIMEOUT[tier], type(e).__name__, switch_note)
                break
            except errors.ServerError as e:
                last_err = e
                if attempt < SERVER_RETRIES and time.perf_counter() - started < GEMINI_BUDGET[tier]:
                    log.warning("llm %s server error on %s (attempt %d), retrying in %.0fs: %s\n    ↳ Google says this model is busy; one quick retry before trying another model.",
                                purpose, model, attempt + 1, SERVER_RETRY_PAUSE, str(e)[:200])
                    time.sleep(SERVER_RETRY_PAUSE)
                else:
                    log.warning("llm %s server error on %s again, %s", purpose, model, switch_note)
    raise RuntimeError(f"All models failed for {tier} tier: {last_err}")


def generate_json(prompt: str, schema: type[BaseModel], tier: Tier = "cheap", system: str | None = None):
    """Ask the model for JSON that matches a Pydantic schema. Returns a schema instance or None."""
    if not available():
        return None
    from google.genai import types

    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=0.2,
    )
    try:
        reply = _call(tier, prompt, config, purpose=schema.__name__, schema=schema, system=system)
    except Exception as e:  # noqa: BLE001 - AI is an enhancement; never break the request
        log.error("generate_json failed: %s", e)
        return None
    if reply.parsed is not None:
        return reply.parsed
    try:
        return schema.model_validate(json.loads(reply.text or "{}"))
    except Exception:  # noqa: BLE001
        log.warning("generate_json: %s answer from %s didn't match the expected format", schema.__name__, reply.model)
        return None


def generate_text(prompt: str, tier: Tier = "cheap", system: str | None = None) -> str | None:
    if not available():
        return None
    from google.genai import types

    config = types.GenerateContentConfig(system_instruction=system, temperature=0.3)
    try:
        return _call(tier, prompt, config, purpose="text", system=system).text
    except Exception as e:  # noqa: BLE001
        log.error("generate_text failed: %s", e)
        return None


def search_grounded(prompt: str) -> dict | None:
    """Answer using Google Search grounding. Returns {"text", "sources"}.
    Gemini only: Groq has no Google Search."""
    if not settings.gemini_api_key:
        return None
    from google.genai import types

    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2,
    )
    try:
        reply = _call("cheap", prompt, config, purpose="web search (bio)", allow_groq=False)
    except Exception as e:  # noqa: BLE001
        log.error("search_grounded failed: %s", e)
        return None
    sources = []
    try:
        gm = reply.raw.candidates[0].grounding_metadata
        for ch in (gm.grounding_chunks or []) if gm else []:
            if ch.web:
                sources.append({"title": ch.web.title, "url": ch.web.uri})
    except Exception:  # noqa: BLE001
        pass
    return {"text": reply.text or "", "sources": sources[:4]}


# ---------- Streaming JSON (show the answer while it's being written) ----------

def partial_json(text: str) -> dict | None:
    """Best-effort parse of an unfinished JSON object, e.g. '{"a": "hel' -> {"a": "hel"}.
    Closes the open string and brackets; if that isn't valid yet (cut mid-key or mid-number),
    falls back to the last complete item before a comma."""
    stack: list[str] = []
    in_str = esc = False
    cut: tuple[int, list[str]] | None = None
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
        elif ch == ",":
            cut = (i, list(stack))

    def close(body: str, open_: list[str], string_open: bool) -> dict | None:
        if string_open:
            body = body.rstrip("\\") + '"'
        try:
            out = json.loads(body + "".join(reversed(open_)))
        except ValueError:
            return None
        return out if isinstance(out, dict) else None

    return close(text, stack, in_str) or (close(text[: cut[0]], cut[1], False) if cut else None)


def _complete_items(data: dict, schema: type[BaseModel]) -> dict:
    """Drop list items that are still half-written (missing required fields), so the page
    only shows promises/claims once they're whole. Plain strings are kept as they stream."""
    import typing

    out = {}
    for name, value in data.items():
        field = schema.model_fields.get(name)
        if field is None:
            continue
        args = typing.get_args(field.annotation)
        item = args[0] if args else None
        if isinstance(value, list) and isinstance(item, type) and issubclass(item, BaseModel):
            need = [n for n, f in item.model_fields.items() if f.is_required()]
            value = [v for v in value if isinstance(v, dict) and all(k in v for k in need)]
        out[name] = value
    return out


STREAM_TIMEOUT = 60.0  # seconds for a whole streamed answer (it keeps arriving, so allow more)


def stream_json(prompt: str, schema: type[BaseModel], tier: Tier = "strong", system: str | None = None,
                on_partial: Any = None, purpose: str | None = None):
    """Like generate_json, but streams the answer from Gemini and calls on_partial(dict)
    with what's been written so far (about twice a second). If streaming fails before
    finishing, falls back to the normal call (with its backup models and Groq)."""
    if not settings.gemini_api_key or on_partial is None:
        return generate_json(prompt, schema, tier, system)
    from google.genai import types

    purpose = purpose or schema.__name__
    model = _candidates(tier, allow_groq=False)[0]
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=0.2,
        http_options=types.HttpOptions(timeout=int(STREAM_TIMEOUT * 1000)),
    )
    buf = ""
    try:
        with _slots:
            with timed(log, "llm stream", purpose=purpose, model=model, tier=tier, prompt_chars=len(prompt)) as t:
                start = time.perf_counter()
                last_emit = 0.0
                meta = None
                for chunk in _get_client().models.generate_content_stream(model=model, contents=prompt, config=config):
                    if chunk.usage_metadata:
                        meta = chunk.usage_metadata
                    piece = chunk.text or ""
                    if not piece:
                        continue
                    if not buf:
                        t["first_text_s"] = round(time.perf_counter() - start, 1)
                        log.info("llm %s: first words after %.1fs, streaming to the page", purpose, t["first_text_s"])
                    buf += piece
                    now = time.perf_counter()
                    if now - last_emit >= 0.5:
                        last_emit = now
                        p = partial_json(buf)
                        if p:
                            on_partial(_complete_items(p, schema))
                if meta:
                    t["in_tok"] = meta.prompt_token_count
                    t["out_tok"] = meta.candidates_token_count
                    t["think_tok"] = meta.thoughts_token_count
        result = schema.model_validate(json.loads(buf))
    except Exception as e:  # noqa: BLE001
        log.warning("llm %s: streaming from %s failed after %d chars (%s), using the normal call instead",
                    purpose, model, len(buf), str(e)[:200])
        return generate_json(prompt, schema, tier, system)
    _working_model[tier] = model
    u = _usage.get()
    if u and meta:
        u.add(model, meta.prompt_token_count or 0,
              (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0))
    return result
