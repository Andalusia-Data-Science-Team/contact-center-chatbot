# nodes/intent.py
from state import BookingState
from nodes._helpers import get_last_user_message
from llm.client import call_llm
from config.settings import INTENT_SWITCH_CONFIDENCE_THRESHOLD
from prompts.intent import INTENT_SYSTEM_PROMPT


def intent_node(state: BookingState) -> BookingState:
    """Classify the latest user message into an intent."""
    msg = get_last_user_message(state)
    if not msg:
        return state

    current_intent = state.get("intent")
    booking_stage = state.get("booking_stage") or "none"

    # Hard lock: mid-booking → always booking, no LLM call needed
    if current_intent == "booking" and booking_stage not in ("none", None, ""):
        state["intent"] = "booking"
        state["intent_confidence"] = 1.0
        return state

    result = call_llm(
        messages=[{"role": "user", "content": msg}],
        system_prompt=INTENT_SYSTEM_PROMPT.format(booking_stage=booking_stage),
        temperature=0.0,
        max_tokens=64,
        json_mode=True,
        label="intent",
    )

    new_intent = result.get("intent", "irrelevant")
    new_confidence = float(result.get("confidence", 0.0))

    # Only switch intent if confidence is high enough
    if current_intent and current_intent != new_intent:
        if new_confidence >= INTENT_SWITCH_CONFIDENCE_THRESHOLD:
            state["intent"] = new_intent
            state["intent_confidence"] = new_confidence
    else:
        state["intent"] = new_intent
        state["intent_confidence"] = new_confidence

    return state
