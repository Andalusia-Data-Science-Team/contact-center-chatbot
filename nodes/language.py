# nodes/language.py
from state import BookingState
from nodes._helpers import get_last_user_message
from utils.language import detect_language


def language_node(state: BookingState) -> BookingState:
    """
    Detect language from the latest user message.
    Arabic chars are unambiguous — once detected, lock to Arabic.
    """
    msg = get_last_user_message(state)
    if not msg:
        return state

    detected = detect_language(msg)

    if detected == "ar":
        state["language"] = "ar"
    elif state.get("language") != "ar":
        state["language"] = "en"

    return state
