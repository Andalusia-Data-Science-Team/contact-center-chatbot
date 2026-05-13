# llm/client.py
import atexit
import contextvars
import hashlib
import json
import random
import re
import threading
import time
from collections import OrderedDict

import requests
from config.settings import (
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    OPENROUTER_BASE_URL,
    LLM_TIMEOUT_SECONDS,
    OPENROUTER_MODEL_CONVERSATION,
    OPENROUTER_MODEL_ROUTING,
    OPENROUTER_MODEL_INTENT,
    OPENROUTER_MODEL_TRIAGE,
    OPENROUTER_MODEL_TIME_PARSE,
    OPENROUTER_PROMPT_CACHING,
    RESPONSE_CACHE_ENABLED,
)
from db.logger import log_llm_call
from utils.language import normalize_ar, to_ascii_digits


# Per-label model routing. Each label maps to a model env var (all default
# to OPENROUTER_MODEL so leaving config alone preserves "everything on the
# same model" behavior). Ops can route cheap labels (intent, time_parse) to
# smaller models via .env without touching code.
_LABEL_TO_MODEL = {
    "conversation": OPENROUTER_MODEL_CONVERSATION,
    "routing":      OPENROUTER_MODEL_ROUTING,
    "intent":       OPENROUTER_MODEL_INTENT,
    "triage":       OPENROUTER_MODEL_TRIAGE,
    "time_parse":   OPENROUTER_MODEL_TIME_PARSE,
}


def _model_for(label: str) -> str:
    """Resolve a call_llm label to its configured model. Unknown labels fall
    back to OPENROUTER_MODEL — defensive against future labels added at a
    node without updating this map."""
    return _LABEL_TO_MODEL.get(label, OPENROUTER_MODEL)


# ── Response cache (intent + time_parse only) ────────────────────────────────
# Process-local LRU+TTL cache for the two short-classification labels. Both
# run at temperature=0.0 with small fixed prompts, so identical normalised
# input produces identical output — safe to memoise. Conversation, routing,
# and triage are NOT cached: their inputs include large amounts of mutable
# state (last 14 messages, full state summary, complaint text + clarification)
# that make caching unsafe.
#
# Cache hits still emit a row to llm_calls (with cache_hit=1 and zero tokens)
# so the dashboard's request volume and hit-rate stay accurate.

_CACHEABLE_LABELS = ("intent", "time_parse")
_RESPONSE_CACHE_MAX = 1000
_RESPONSE_CACHE_TTL_SECONDS = 300  # 5 min — long enough for repeat sessions, short enough to drop stale.
_response_cache: OrderedDict = OrderedDict()
_response_cache_lock = threading.Lock()


def _cache_key(label: str, model: str, system_prompt, messages):
    """Build a cache key normalised so 'yes'/'Yes'/'YES' and 'نعم'/'نَعَم'
    collide. The key includes the model so per-label routing (item 1.1)
    invalidates cached results when ops switches a label to a new model."""
    last_user = ""
    for m in reversed(messages or []):
        if m.get("role") == "user":
            last_user = m.get("content") or ""
            break
    norm_user = normalize_ar(to_ascii_digits(last_user)).strip()
    payload = (system_prompt or "") + "|" + norm_user
    digest = hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return (label, model, digest)


def _response_cache_get(key):
    """Return the cached value for `key` if present and not expired, else None."""
    now = time.time()
    with _response_cache_lock:
        entry = _response_cache.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if now - stored_at >= _RESPONSE_CACHE_TTL_SECONDS:
            _response_cache.pop(key, None)
            return None
        # LRU recency bump.
        _response_cache.move_to_end(key)
        return value


def _response_cache_put(key, value) -> None:
    """Store `value` under `key`. Evicts oldest entries if over capacity."""
    with _response_cache_lock:
        _response_cache[key] = (time.time(), value)
        _response_cache.move_to_end(key)
        while len(_response_cache) > _RESPONSE_CACHE_MAX:
            _response_cache.popitem(last=False)


# ── Defensive payload-size guard ─────────────────────────────────────────────
# Caps every outgoing LLM payload so a pathologically long user message
# (clipboard dump, copy-paste, prompt injection attempt) can't blow up the
# prompt size and turn one turn into a $0.01+ event. The conversation node
# already does `messages[-14:]` (count cap); this is the character cap.
_MAX_MESSAGE_CHARS = 6000        # per-message — typical patient msg is < 500 chars
_MAX_TOTAL_MESSAGE_CHARS = 24000 # cumulative budget across the message list


def _cap_message_size(messages: list) -> list:
    """Return a copy of `messages` with each entry's string content truncated
    from the start to `_MAX_MESSAGE_CHARS`, and the oldest entries dropped
    until the cumulative budget is met. System prompt is NOT counted here —
    it's authored by us, not user input. Non-string content (e.g. the
    Anthropic-style structured shape from item 1.2) passes through untouched.
    """
    capped = []
    for m in messages or []:
        content = m.get("content") if isinstance(m, dict) else None
        if isinstance(content, str) and len(content) > _MAX_MESSAGE_CHARS:
            # Keep the tail (more recent content) and mark the truncation.
            content = "…" + content[-(_MAX_MESSAGE_CHARS - 1):]
            new_m = {**m, "content": content}
        else:
            new_m = m
        capped.append(new_m)

    # Cumulative cap — drop oldest until under budget. Only string content
    # contributes to the count; structured content (rare) is treated as zero
    # for budgeting since its size is opaque without tokenization.
    def _len(m):
        c = m.get("content") if isinstance(m, dict) else None
        return len(c) if isinstance(c, str) else 0

    total = sum(_len(m) for m in capped)
    while total > _MAX_TOTAL_MESSAGE_CHARS and len(capped) > 1:
        dropped = capped.pop(0)
        total -= _len(dropped)
    return capped


# Module-level HTTP session for keep-alive + connection pooling. Every call
# through call_llm() reuses the underlying TCP+TLS state instead of paying
# the ~130ms handshake cost on each request. urllib3's connection pool inside
# requests.Session is thread-safe — multiple Streamlit threads can share it.
_session = requests.Session()


def _close_session() -> None:
    """Close the shared HTTP session at process exit. Best-effort."""
    try:
        _session.close()
    except Exception as e:
        # Best-effort cleanup — don't crash shutdown on a stale socket.
        print(f"[llm._close_session] close failed: {e}")


atexit.register(_close_session)


def _empty_metrics() -> dict:
    # `started_at` is the wall-clock time the per-turn metrics were initialized
    # (typically when reset_turn_metrics() fires at the start of a turn).
    # Used by get_turn_metrics() to compute `turn_budget_ms` — observability
    # only; no enforcement.
    return {
        "llm_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_latency_ms": 0,
        "calls_detail": [],   # list of {label, input_tokens, output_tokens, latency_ms}
        "started_at": time.time(),
    }


# Per-context (thread- and async-safe) metrics accumulator. Streamlit serves
# each user session in its own thread; a module-level dict would be scrambled
# by concurrent turns. ContextVar gives each thread (and each asyncio task) its
# own dict, so reset/get/in-place writes inside one turn never collide with
# another session's turn.
_turn_metrics_var: contextvars.ContextVar = contextvars.ContextVar(
    "_turn_metrics", default=None,
)


def _get_metrics() -> dict:
    metrics = _turn_metrics_var.get()
    if metrics is None:
        metrics = _empty_metrics()
        _turn_metrics_var.set(metrics)
    return metrics


def reset_turn_metrics():
    """Reset metrics at the start of each turn."""
    _turn_metrics_var.set(_empty_metrics())


def get_turn_metrics() -> dict:
    """Return a copy of current turn metrics, with `turn_budget_ms` computed
    against `started_at` (wall-clock ms from reset to now). Observability
    only — no enforcement; app.py / pages/logs.py can surface this to spot
    slow turns without aborting them."""
    metrics = dict(_get_metrics())
    started_at = metrics.pop("started_at", None)
    if started_at is not None:
        metrics["turn_budget_ms"] = int((time.time() - started_at) * 1000)
    else:
        metrics["turn_budget_ms"] = 0
    return metrics


# ── Per-turn log context ─────────────────────────────────────────────────────
# `log_llm_call` rows are attributed to (session_id, turn_number). app.py sets
# these once per turn via `set_log_context`; call_llm reads them implicitly.
# Same ContextVar pattern as _turn_metrics_var so concurrent sessions stay
# isolated.
_log_context_var: contextvars.ContextVar = contextvars.ContextVar(
    "_log_context", default=None,
)


def set_log_context(session_id, turn_number) -> None:
    """Attribute subsequent `call_llm` invocations to this session + turn."""
    _log_context_var.set({"session_id": session_id, "turn_number": turn_number})


def _get_log_context():
    ctx = _log_context_var.get()
    if not ctx:
        return None, None
    return ctx.get("session_id"), ctx.get("turn_number")


def _hash_prompt(system_prompt, messages) -> str | None:
    """12-char sha1 of (system_prompt + last user message). Not for security —
    only for cache-rate / pattern analysis in the dashboard."""
    try:
        last_user = ""
        for m in reversed(messages or []):
            if m.get("role") == "user":
                last_user = m.get("content") or ""
                break
        payload = (system_prompt or "") + "|" + last_user
        return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:12]
    except Exception:
        return None


def call_llm(
    messages: list,
    system_prompt: str = None,
    temperature: float = 0.1,
    max_tokens: int = 512,
    json_mode: bool = False,
    label: str = "llm_call",
) -> str | dict:
    """
    Call OpenRouter LLM.
    Returns the assistant reply as a string.
    If json_mode=True, returns parsed JSON dict.
    Tracks token usage and latency in _turn_metrics.
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    # Defensive size cap before assembling the payload. Truncates oversized
    # individual messages and drops the oldest if cumulative chars exceed the
    # budget. Pathological inputs (clipboard dumps, prompt injection) can't
    # inflate the LLM call.
    messages = _cap_message_size(messages)

    payload_messages = []
    if system_prompt:
        if OPENROUTER_PROMPT_CACHING:
            # Structured content with Anthropic-style cache marker. The provider
            # caches this prefix; subsequent requests reusing the same system
            # text are charged at the cached-input rate. Non-supporting
            # providers ignore the marker without error. Off by default
            # (OPENROUTER_PROMPT_CACHING=0 in `.env`) — leaves the payload
            # byte-identical to pre-Stage-4 behavior. Note: our
            # `estimated_cost_usd` in llm_calls assumes the standard input
            # rate and will OVER-estimate when cache hits occur; cache-aware
            # cost accounting is a follow-up item.
            payload_messages.append({
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
            })
        else:
            payload_messages.append({"role": "system", "content": system_prompt})
    payload_messages.extend(messages)

    payload = {
        "model": _model_for(label),
        "messages": payload_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    # Retry transient network errors (SSL resets, timeouts) with exponential
    # backoff. Without this a single ConnectionResetError crashes the whole
    # booking — in testing this was the #1 cause of aborted scenarios.
    _TRANSIENT_EXC = (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
    )

    # When json_mode is on, the LLM occasionally emits malformed JSON
    # (broken \uXXXX escapes, truncated strings). Re-rolling almost always
    # produces valid JSON, so retry the whole call up to 3 times.
    json_max_attempts = 3 if json_mode else 1

    session_id, turn_number = _get_log_context()
    prompt_hash = _hash_prompt(system_prompt, messages)
    model_used = payload["model"]

    # Response cache lookup — short-classification labels only. Same normalised
    # input + same model + same system prompt → return cached result. Logs a
    # cache_hit=1 row for dashboard visibility (zero tokens / latency / cost).
    cache_key = None
    if RESPONSE_CACHE_ENABLED and label in _CACHEABLE_LABELS:
        cache_key = _cache_key(label, model_used, system_prompt, messages)
        cached = _response_cache_get(cache_key)
        if cached is not None:
            log_llm_call(
                session_id=session_id, turn_number=turn_number,
                node_name=label, model=model_used,
                input_tokens=0, output_tokens=0, latency_ms=0,
                status="ok", prompt_hash=prompt_hash, cache_hit=True,
            )
            return cached

    for json_attempt in range(json_max_attempts):
        start_time = time.time()
        response = None
        try:
            for attempt in range(3):
                try:
                    response = _session.post(
                        f"{OPENROUTER_BASE_URL}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=LLM_TIMEOUT_SECONDS,
                    )
                    break
                except _TRANSIENT_EXC:
                    if attempt < 2:
                        # Jitter the backoff to decorrelate retries across
                        # concurrent sessions. Average wait stays at 1s / 2s;
                        # the symmetric jitter just smooths the thundering-herd
                        # pattern when many threads hit the same transient blip.
                        base = 1 + attempt
                        spread = 0.3 if attempt == 0 else 0.5
                        time.sleep(max(0.0, base + random.uniform(-spread, spread)))
                        continue
                    raise
            latency_ms = int((time.time() - start_time) * 1000)
            response.raise_for_status()
        except Exception as e:
            # Network exhaustion or HTTP error — log it and re-raise so the
            # exception still bubbles up to app.py's catch-all (existing
            # behavior preserved).
            err_latency_ms = int((time.time() - start_time) * 1000)
            log_llm_call(
                session_id=session_id, turn_number=turn_number,
                node_name=label, model=model_used,
                input_tokens=0, output_tokens=0, latency_ms=err_latency_ms,
                status="error", error_type=type(e).__name__,
                error_message=str(e), prompt_hash=prompt_hash,
            )
            raise

        resp_json = response.json()
        # OpenRouter / upstream providers occasionally return `content: null`
        # (e.g. when the model produced no text or only a tool-call). Without
        # the `or ""` guard, `.strip()` would crash with AttributeError on the
        # NoneType — bypassing every retry / fallback path below. Defensive
        # accessors collapse missing/null fields to empty string so the
        # existing "empty content" branch (lines below) can do its job.
        content = (
            (resp_json.get("choices") or [{}])[0]
            .get("message", {})
            .get("content")
            or ""
        ).strip()

        usage = resp_json.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        metrics = _get_metrics()
        metrics["llm_calls"] += 1
        metrics["total_input_tokens"] += input_tokens
        metrics["total_output_tokens"] += output_tokens
        metrics["total_latency_ms"] += latency_ms
        metrics["calls_detail"].append({
            "label": label,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
        })

        if not json_mode:
            log_llm_call(
                session_id=session_id, turn_number=turn_number,
                node_name=label, model=model_used,
                input_tokens=input_tokens, output_tokens=output_tokens,
                latency_ms=latency_ms, status="ok",
                prompt_hash=prompt_hash,
            )
            if cache_key is not None:
                _response_cache_put(cache_key, content)
            return content

        content = _clean_json(content)

        # An LLM response that's only `<think>...</think>` (stripped by
        # _clean_json) or pure whitespace leaves us with an empty string,
        # which json.loads rejects with the unhelpful "Expecting value: line
        # 1 column 1 (char 0)" error. Treat that as malformed and retry.
        if not content:
            if json_attempt < json_max_attempts - 1:
                print(f"[call_llm:{label}] empty content on attempt {json_attempt + 1}/{json_max_attempts}, retrying.")
                log_llm_call(
                    session_id=session_id, turn_number=turn_number,
                    node_name=label, model=model_used,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    latency_ms=latency_ms, status="json_retry",
                    error_type="EmptyContent",
                    error_message=f"Empty content on attempt {json_attempt + 1}/{json_max_attempts}",
                    prompt_hash=prompt_hash,
                )
                continue
            print(f"[call_llm:{label}] all {json_max_attempts} attempts returned empty content; falling back to safe default.")
            log_llm_call(
                session_id=session_id, turn_number=turn_number,
                node_name=label, model=model_used,
                input_tokens=input_tokens, output_tokens=output_tokens,
                latency_ms=latency_ms, status="fallback",
                error_type="EmptyContent",
                error_message=f"All {json_max_attempts} attempts returned empty content",
                prompt_hash=prompt_hash,
            )
            return _safe_default(label)

        try:
            result = json.loads(content)
            if not isinstance(result, dict):
                # Some upstream providers / quantized backends occasionally
                # return a top-level array or scalar when the prompt asked for
                # an object. Treat as malformed so the existing retry /
                # fallback path can handle it — otherwise downstream
                # `result.get(...)` calls crash on the wrong type.
                raise json.JSONDecodeError(
                    f"expected JSON object, got {type(result).__name__}",
                    content,
                    0,
                )
            log_llm_call(
                session_id=session_id, turn_number=turn_number,
                node_name=label, model=model_used,
                input_tokens=input_tokens, output_tokens=output_tokens,
                latency_ms=latency_ms, status="ok",
                prompt_hash=prompt_hash,
            )
            if cache_key is not None:
                _response_cache_put(cache_key, result)
            return result
        except json.JSONDecodeError as e:
            if json_attempt < json_max_attempts - 1:
                print(f"[call_llm:{label}] malformed JSON on attempt {json_attempt + 1}/{json_max_attempts}, retrying. Error: {e}")
                log_llm_call(
                    session_id=session_id, turn_number=turn_number,
                    node_name=label, model=model_used,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    latency_ms=latency_ms, status="json_retry",
                    error_type="JSONDecodeError",
                    error_message=str(e),
                    prompt_hash=prompt_hash,
                )
                continue
            # Final failure: don't crash the booking. Log and return a safe
            # default so the pipeline's stage-aware safety_net can recover.
            print(f"[call_llm:{label}] all {json_max_attempts} JSON attempts failed; falling back to safe default. Last content (truncated): {content[:300]}")
            log_llm_call(
                session_id=session_id, turn_number=turn_number,
                node_name=label, model=model_used,
                input_tokens=input_tokens, output_tokens=output_tokens,
                latency_ms=latency_ms, status="fallback",
                error_type="JSONDecodeError",
                error_message=str(e),
                prompt_hash=prompt_hash,
            )
            return _safe_default(label)


def _safe_default(label: str) -> dict:
    """Return a shape-appropriate empty result when JSON parsing fails after
    all retries. Each call site has different expected keys; returning the
    right shape lets downstream code use `.get()` without KeyErrors and lets
    the stage-aware safety_net fill in the actual user-facing reply.
    """
    if label == "conversation":
        return {"reply": "", "state_updates": {}}
    if label == "routing":
        return {"specialty": "", "confidence": 0}
    if label == "triage":
        return {"question": ""}
    if label == "intent":
        return {"intent": "booking", "confidence": 0}
    return {}


def _clean_json(content: str) -> str:
    """Strip markdown fences and <think> tags from LLM JSON output."""
    # Remove <think>...</think> blocks
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)

    # Remove markdown code fences
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]

    return content.strip()
