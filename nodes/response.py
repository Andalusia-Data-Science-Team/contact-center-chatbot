# nodes/response.py
from state import BookingState

NON_BOOKING_RESPONSES = {
    "en": {
        "inquiry":     "I'd be happy to help with your question! This module is coming soon. For now, please contact our reception directly.",
        "complaint":   "I'm sorry to hear that. Your feedback is important to us. This module is coming soon — please contact our reception for immediate assistance.",
        "cancelation": "I understand you'd like to cancel. This module is coming soon. Please contact reception to cancel your appointment.",
        "reschedule":  "Sure, I can see you'd like to reschedule. This module is coming soon. Please contact reception to change your appointment.",
        "irrelevant":  "I'm here to help with booking doctor appointments at Andalusia Health. How can I assist you today?",
    },
    "ar": {
        "inquiry":     "يسعدني أساعدك! هذه الخدمة قيد التطوير حالياً. ممكن تتواصل مع الاستقبال مباشرة.",
        "complaint":   "نعتذر عن أي إزعاج. رأيك يهمنا. هذه الخدمة قيد التطوير — ممكن تتواصل مع الاستقبال.",
        "cancelation": "تمام، تبغى تلغي الموعد. هذه الخدمة قيد التطوير حالياً. ممكن تتواصل مع الاستقبال لإلغاء الموعد.",
        "reschedule":  "تمام، تبغى تغير الموعد. هذه الخدمة قيد التطوير حالياً. ممكن تتواصل مع الاستقبال لتغيير الموعد.",
        "irrelevant":  "أنا هنا لمساعدتك في حجز مواعيد الأطباء في مجموعة أندلسية صحة. كيف أقدر أساعدك؟",
    },
}


def response_node(state: BookingState) -> BookingState:
    """Handle non-booking intents with warm, helpful responses."""
    lang = state.get("language", "en")
    intent = state.get("intent")
    booking_stage = state.get("booking_stage")

    # Safety net: if mid-booking, never show a canned non-booking response
    if booking_stage and booking_stage not in ("none", None):
        if lang == "ar":
            state["last_bot_message"] = "عذراً، ممكن توضحلي أكثر؟ أنا أساعدك في إتمام حجزك."
        else:
            state["last_bot_message"] = "Sorry, could you clarify? I'm helping you complete your booking."
        return state

    responses = NON_BOOKING_RESPONSES.get(lang, NON_BOOKING_RESPONSES["en"])
    state["last_bot_message"] = responses.get(intent, responses["irrelevant"])
    return state
