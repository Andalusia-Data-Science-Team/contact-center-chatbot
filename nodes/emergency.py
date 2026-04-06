# nodes/emergency.py
from state import BookingState
from nodes._helpers import get_last_user_message
from utils.emergency import detect_emergency

EMERGENCY_RESPONSE = {
    "en": (
        "🚨 This sounds like it may need **immediate medical attention**. "
        "Please head to the **Andalusia Emergency Department** right away or call emergency services. "
        "Your safety comes first!"
    ),
    "ar": (
        "🚨 هذه الحالة قد تحتاج **رعاية طبية فورية**. "
        "يرجى التوجه فوراً لـ **قسم الطوارئ في مستشفى أندلسية** أو الاتصال بخدمات الطوارئ. "
        "سلامتك أولاً!"
    ),
}


def emergency_node(state: BookingState) -> BookingState:
    """Rule-based emergency detection on the latest user message."""
    msg = get_last_user_message(state)
    if not msg:
        return state

    if detect_emergency(msg):
        state["emergency"] = True
        lang = state.get("language", "en")
        state["last_bot_message"] = EMERGENCY_RESPONSE.get(lang, EMERGENCY_RESPONSE["en"])
    else:
        state["emergency"] = False

    return state
