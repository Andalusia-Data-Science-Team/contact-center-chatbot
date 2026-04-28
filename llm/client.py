# llm/client.py
import json
import re
import time
import requests
from config.settings import OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_BASE_URL

# ── Global metrics accumulator (reset per turn by app.py) ──
_turn_metrics = {
    "llm_calls": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_latency_ms": 0,
    "calls_detail": [],   # list of {label, input_tokens, output_tokens, latency_ms}
}


def reset_turn_metrics():
    """Reset metrics at the start of each turn."""
    _turn_metrics["llm_calls"] = 0
    _turn_metrics["total_input_tokens"] = 0
    _turn_metrics["total_output_tokens"] = 0
    _turn_metrics["total_latency_ms"] = 0
    _turn_metrics["calls_detail"] = []


def get_turn_metrics() -> dict:
    """Return a copy of current turn metrics."""
    return dict(_turn_metrics)


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

    payload_messages = []
    if system_prompt:
        payload_messages.append({"role": "system", "content": system_prompt})
    payload_messages.extend(messages)

    payload = {
        "model": OPENROUTER_MODEL,
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

    for json_attempt in range(json_max_attempts):
        start_time = time.time()
        response = None
        for attempt in range(3):
            try:
                response = requests.post(
                    f"{OPENROUTER_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30,
                )
                break
            except _TRANSIENT_EXC:
                if attempt < 2:
                    time.sleep(1 + attempt)  # 1s, 2s
                    continue
                raise
        latency_ms = int((time.time() - start_time) * 1000)
        response.raise_for_status()

        resp_json = response.json()
        content = resp_json["choices"][0]["message"]["content"].strip()

        usage = resp_json.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        _turn_metrics["llm_calls"] += 1
        _turn_metrics["total_input_tokens"] += input_tokens
        _turn_metrics["total_output_tokens"] += output_tokens
        _turn_metrics["total_latency_ms"] += latency_ms
        _turn_metrics["calls_detail"].append({
            "label": label,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
        })

        if not json_mode:
            return content

        content = _clean_json(content)
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            if json_attempt < json_max_attempts - 1:
                print(f"[call_llm:{label}] malformed JSON on attempt {json_attempt + 1}/{json_max_attempts}, retrying. Error: {e}")
                continue
            print(f"[call_llm:{label}] all {json_max_attempts} JSON attempts failed. Last content (truncated): {content[:300]}")
            raise


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
