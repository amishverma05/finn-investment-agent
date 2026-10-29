"""Gemini wrapper with two tiers (cheap / strong), retries, JSON output and token accounting.

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
    return bool(settings.gemini_api_key)


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            from google import genai

            _client = genai.Client(api_key=settings.gemini_api_key)
        return _client


def _candidates(tier: Tier) -> list[str]:
    raw = settings.model_cheap if tier == "cheap" else settings.model_strong
    names = [m.strip() for m in raw.split(",") if m.strip()]
    known = _working_model.get(tier)
    if known in names:
        names.remove(known)
        names.insert(0, known)
    return names


def _call(tier: Tier, contents: Any, config: Any, purpose: str = ""):
    from google.genai import errors

    chars = len(contents) if isinstance(contents, str) else None
    last_err: Exception | None = None
    for model in _candidates(tier):
        for attempt in range(4):
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
                _working_model[tier] = model
                u = _usage.get()
                if u and meta:
                    u.add(
                        model,
                        meta.prompt_token_count or 0,
                        (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
                    )
                return resp
            except errors.ClientError as e:
                last_err = e
                if e.code == 429:  # rate limited: back off and retry
                    pause = min(8 * (attempt + 1), 30)
                    log.warning("llm %s rate-limited on %s, sleeping %ss before retry", purpose, model, pause)
                    time.sleep(pause)
                    continue
                if e.code in (400, 403, 404):  # model missing or not allowed: try the next one
                    log.warning("model %s failed (%s), trying next: %s", model, e.code, str(e)[:200])
                    break
                raise
            except errors.ServerError as e:
                last_err = e
                log.warning("llm %s server error on %s (attempt %d): %s", purpose, model, attempt + 1, str(e)[:200])
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"All {tier} models failed: {last_err}")


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
        resp = _call(tier, prompt, config, purpose=schema.__name__)
    except Exception as e:  # noqa: BLE001 - AI is an enhancement; never break the request
        log.error("generate_json failed: %s", e)
        return None
    if resp.parsed is not None:
        return resp.parsed
    try:
        return schema.model_validate(json.loads(resp.text or "{}"))
    except Exception:  # noqa: BLE001
        return None


def generate_text(prompt: str, tier: Tier = "cheap", system: str | None = None) -> str | None:
    if not available():
        return None
    from google.genai import types

    config = types.GenerateContentConfig(system_instruction=system, temperature=0.3)
    try:
        return _call(tier, prompt, config, purpose="text").text
    except Exception as e:  # noqa: BLE001
        log.error("generate_text failed: %s", e)
        return None


def search_grounded(prompt: str) -> dict | None:
    """Answer using Google Search grounding. Returns {"text", "sources"}."""
    if not available():
        return None
    from google.genai import types

    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2,
    )
    try:
        resp = _call("cheap", prompt, config, purpose="web search (bio)")
    except Exception as e:  # noqa: BLE001
        log.error("search_grounded failed: %s", e)
        return None
    sources = []
    try:
        gm = resp.candidates[0].grounding_metadata
        for ch in (gm.grounding_chunks or []) if gm else []:
            if ch.web:
                sources.append({"title": ch.web.title, "url": ch.web.uri})
    except Exception:  # noqa: BLE001
        pass
    return {"text": resp.text or "", "sources": sources[:4]}
