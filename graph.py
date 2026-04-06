# graph.py
"""
LangGraph wiring — defines the full conversation pipeline.

Pipeline:
  language → emergency → intent → [booking pipeline | non-booking response]

Booking pipeline (sequential — each node checks if it should act):
  conversation → routing → doctor_selection → slot_selection → patient_info

Each booking node is a "filter": it checks the current booking_stage
and LLM updates, acts if relevant, and passes through otherwise.

Updated flow to match real Andalusia agent behavior:
- Name collection first (greeting_done stage)
- No triage questions
- Direct routing from complaint to doctor list
"""
from langgraph.graph import StateGraph, END
from state import BookingState
from nodes.language import language_node
from nodes.emergency import emergency_node
from nodes.intent import intent_node
from nodes.response import response_node
from nodes.conversation import conversation_node
from nodes.routing import routing_node
from nodes.doctor_selection import doctor_selection_node
from nodes.slot_selection import slot_selection_node
from nodes.patient_info import patient_info_node


# ── Routing functions ────────────────────────────────────────────────────────

def route_after_emergency(state: BookingState) -> str:
    return "end" if state.get("emergency") else "intent"


def route_after_intent(state: BookingState) -> str:
    intent = state.get("intent")
    if intent in ("booking", "greeting"):
        return "booking_pipeline"
    return "non_booking_response"


def _safety_net(state: BookingState) -> BookingState:
    """
    Final pass: if reply is still empty after all booking nodes,
    provide a fallback prompt.
    """
    reply = state.get("last_bot_message") or ""
    if not reply.strip():
        lang = state.get("language", "en")
        stage = state.get("booking_stage")

        if stage == "routing":
            if lang == "ar":
                state["last_bot_message"] = "ممكن توضحلي وش المشكلة عشان أقدر أساعدك؟"
            else:
                state["last_bot_message"] = "Could you tell me what you need help with so I can find the right doctor?"
        elif stage in (None, "", "none"):
            # First interaction — greet and ask for name
            if lang == "ar":
                state["last_bot_message"] = "السلام عليكم، معك نور من مجموعة أندلسية صحة 😊 اتشرف بالاسم؟"
            else:
                state["last_bot_message"] = "Hello! I'm Nour from Andalusia Health Group 😊 May I have your name please?"
        elif stage == "greeting_done":
            patient = state.get("patient_name", "")
            if lang == "ar":
                greeting = f"أ/ {patient}" if patient else ""
                state["last_bot_message"] = f"أهلا وسهلا بحضرتك {greeting}، ازاي أقدر أساعدك؟"
            else:
                state["last_bot_message"] = f"Welcome {patient}! How can I help you today?"

    # Clean up transient data (but NOT followup_message — app.py reads it after)
    state["_llm_updates"] = None
    return state


# ── Graph construction ───────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    graph = StateGraph(BookingState)

    # ── Shared pipeline ──
    graph.add_node("language", language_node)
    graph.add_node("emergency", emergency_node)
    graph.add_node("intent", intent_node)
    graph.add_node("non_booking_response", response_node)

    # ── Booking pipeline (sequential chain) ──
    graph.add_node("conversation", conversation_node)
    graph.add_node("routing", routing_node)
    graph.add_node("doctor_selection", doctor_selection_node)
    graph.add_node("slot_selection", slot_selection_node)
    graph.add_node("patient_info", patient_info_node)
    graph.add_node("safety_net", _safety_net)

    # ── Edges ──
    graph.set_entry_point("language")
    graph.add_edge("language", "emergency")

    graph.add_conditional_edges(
        "emergency",
        route_after_emergency,
        {"end": END, "intent": "intent"},
    )

    graph.add_conditional_edges(
        "intent",
        route_after_intent,
        {
            "booking_pipeline": "conversation",
            "non_booking_response": "non_booking_response",
        },
    )

    graph.add_edge("non_booking_response", END)

    # Booking pipeline: sequential chain of filter nodes
    graph.add_edge("conversation", "routing")
    graph.add_edge("routing", "doctor_selection")
    graph.add_edge("doctor_selection", "slot_selection")
    graph.add_edge("slot_selection", "patient_info")
    graph.add_edge("patient_info", "safety_net")
    graph.add_edge("safety_net", END)

    return graph.compile()


compiled_graph = build_graph()
