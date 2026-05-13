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
import re

from langgraph.graph import StateGraph, END
from state import BookingState
from config.settings import LANGGRAPH_CHECKPOINTER
from nodes.language import language_node
from nodes.emergency import emergency_node
from nodes.intent import intent_node
from nodes.response import response_node
from nodes.conversation import conversation_node
from nodes.routing import routing_node
from nodes.doctor_selection import doctor_selection_node
from nodes.slot_selection import slot_selection_node
from nodes.patient_info import patient_info_node


# ── Challenge / contradiction detection ──────────────────────────────────────
# When the patient pushes back ("you said today!", "ما انتى قلتيلى اليوم متاح")
# the bot should acknowledge instead of stoically repeating the same answer.
# Patterns are tight on purpose — generic words like "ليش" alone don't qualify;
# the patient must reference what we said earlier.
_CHALLENGE_PATTERNS_AR = [
    re.compile(r"(ما|بس)\s*ا?نت[يى]?\s*قلت[يى]?(لي|لى|ل[يى])?"),
    re.compile(r"\bقلت[يى]?\s*ل?[يى]?\s*(اول|قبل|الحين|تو)"),
    re.compile(r"\bكنت[يى]?\s*قلت[يى]?"),
    re.compile(r"\bمتناقض|تناقض|عكس\s*كلامك"),
    re.compile(r"\bلي?ش\s*(متغير|اختلف|تغير)"),
    re.compile(r"\bكيف\s*كذا\b"),
]
_CHALLENGE_PATTERNS_EN = [
    re.compile(r"\bbut\s+you\s+(said|told)\b", re.IGNORECASE),
    re.compile(r"\byou\s+(just\s+)?(said|told)\b", re.IGNORECASE),
    re.compile(r"\bwait[,!\s]+you\s+(said|told)\b", re.IGNORECASE),
    re.compile(r"\bcontradict", re.IGNORECASE),
    re.compile(r"\bthat[''s]?\s+not\s+what\s+you\s+(said|told)\b", re.IGNORECASE),
]


def _detect_user_challenge(state: BookingState) -> bool:
    """True if the latest patient message is pushing back on what we said."""
    messages = state.get("messages") or []
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if not last_user:
        return False
    text = (last_user.get("content") or "").strip()
    if not text:
        return False
    # Try AR patterns on raw text (Arabic regex doesn't need normalization here
    # since the patterns already accept common spelling variants).
    for pat in _CHALLENGE_PATTERNS_AR:
        if pat.search(text):
            return True
    for pat in _CHALLENGE_PATTERNS_EN:
        if pat.search(text):
            return True
    return False


def _apology_prefix(lang: str) -> str:
    if lang == "ar":
        return "آسفة على اللخبطة"
    return "Apologies for the confusion"


# ── Repetition / loop detection ──────────────────────────────────────────────
# If the bot is about to send the same reply it just sent, the patient is
# stuck in a loop ("اليوم"/"مين متاح اليوم"/"بس اليوم") and rephrasing won't
# help — break out with an escalation that offers a callback or change of
# strategy instead of repeating the same words.

def _normalize_for_repeat(text: str) -> str:
    """Strip apology prefix and whitespace so we compare the message body."""
    t = (text or "").strip()
    for prefix in ("آسفة على اللخبطة", "Apologies for the confusion"):
        if t.startswith(prefix):
            # Drop the prefix and the dash separator we add in _safety_net.
            t = t[len(prefix):].lstrip(" —-").strip()
    return t


def _is_repeating(state: BookingState, new_reply: str) -> bool:
    """True if `new_reply` matches the most recent assistant message."""
    body = _normalize_for_repeat(new_reply)
    if not body:
        return False
    messages = state.get("messages") or []
    recent_assistant = [
        _normalize_for_repeat(m.get("content"))
        for m in messages if m.get("role") == "assistant"
    ]
    # Compare against the last assistant message only — one repeat is enough
    # to break the cycle before the patient grows more frustrated.
    return bool(recent_assistant) and recent_assistant[-1] == body


def _escalation_message(state: BookingState, lang: str) -> str:
    """Reply offering a path forward when we'd otherwise repeat ourselves."""
    stage = state.get("booking_stage")
    doctor = state.get("doctor")
    if lang == "ar":
        if stage in ("slot_selection", "patient_info") and doctor:
            return (
                "آسفة، أحس إني أكرر نفس الكلام. "
                "تبغى أعرض لك أيام ثانية، أحجز لك مع دكتور ثاني، "
                "أو نسجل بياناتك ويتواصل معك فريق خدمة العملاء؟"
            )
        if stage == "doctor_list":
            return (
                "آسفة على اللخبطة. تبغى أعرض لك القائمة من جديد، "
                "أو نسجل بياناتك ويتواصل معك فريق خدمة العملاء؟"
            )
        return (
            "آسفة، يبدو إني عالقة في نفس النقطة. "
            "تبغى نسجل بياناتك ويتواصل معك فريق خدمة العملاء عشان نساعدك أحسن؟"
        )
    if stage in ("slot_selection", "patient_info") and doctor:
        return (
            "Sorry, I notice I'm repeating myself. "
            "Would you like to try a different day, switch to another doctor, "
            "or have our team contact you to help?"
        )
    if stage == "doctor_list":
        return (
            "Apologies — would you like me to show the list again, "
            "or have our team reach out to help you book?"
        )
    return (
        "Sorry, I seem to be stuck on the same answer. "
        "Would you like our team to contact you so we can help you better?"
    )


# ── Routing functions ────────────────────────────────────────────────────────

def route_after_emergency(state: BookingState) -> str:
    return "end" if state.get("emergency") else "intent"


def route_after_intent(state: BookingState) -> str:
    intent = state.get("intent")
    if intent in ("booking", "greeting"):
        return "booking_pipeline"
    return "non_booking_response"


# Cap on in-memory message history. SQLite logging is independent of this —
# full audit history lives in `chat_logs`. The cap exists to keep long-lived
# sessions (especially the future WhatsApp deployment, where sessions can
# span days) from growing unbounded in memory. 50 entries ≈ 25 turns, which
# comfortably covers conversation_node's messages[-14:] slice and the
# repetition-detection scan in this file.
MAX_MESSAGES_RETAINED = 50


def _trim_messages(state: BookingState) -> None:
    """Cap state['messages'] to the most recent MAX_MESSAGES_RETAINED entries.

    Called from `_safety_net` once per turn. No-op when the list is at or
    below the cap. The SQLite `chat_logs` table is the source of truth for
    audit history; the in-memory list only needs enough context for the
    conversation LLM (14) and the repetition detector (recent assistants).
    """
    msgs = state.get("messages") or []
    if len(msgs) > MAX_MESSAGES_RETAINED:
        state["messages"] = msgs[-MAX_MESSAGES_RETAINED:]


def _clear_transient_fields(state: BookingState) -> None:
    """Null out per-turn-only state fields at end of every turn.

    Centralises the cleanup so future contributors don't have to grep across
    nodes to find what's cleared and what persists. Each entry below is a
    *per-turn* flag that should NOT survive into the next user message.

    Multi-turn state fields are intentionally NOT cleared here:
      - `_proposed_slot`        — set in turn N, consumed in N+1 (acceptance)
      - `_alternatives_offered` — gates doctor switching across turns
      - `_single_doctor_choice_pending` — flag from end-of-turn N to start of N+1
      - `_partial_doctor_name` / `_partial_doctor_attempts` — escalation memory
      - `_walk_in_price_doctor` / `_pending_price_followup` — multi-turn cache
    Touch those only if you also update their consumers.
    """
    # Raw LLM updates dict — single-turn by design. Dropping this also kills
    # any keys nested inside it (e.g. `_price_handled`) so they don't need
    # separate clearing.
    state["_llm_updates"] = None
    # "I just showed the proposed slot — don't auto-confirm yet."
    state["_proposal_shown_this_turn"] = False
    # "Doctor was picked by bare digit; skip the slot safety net's bare-digit
    # → time misread." Normally consumed via `state.pop` in slot_selection;
    # cleared here too as belt-and-braces if a turn ever skips slot_selection.
    state["_skip_time_preference_this_turn"] = False


def _safety_net(state: BookingState) -> BookingState:
    """
    Final pass: if reply is still empty after all booking nodes,
    provide a fallback prompt so the user never sees a blank bubble.
    """
    reply = state.get("last_bot_message") or ""
    if not reply.strip():
        lang = state.get("language", "en")
        stage = state.get("booking_stage")

        if stage == "cancelled":
            # Cancellation reply is owned by conversation_node; if it somehow
            # got cleared, fall back to a polite confirmation rather than the
            # generic "could you rephrase" clarifier.
            if lang == "ar":
                state["last_bot_message"] = "تمام، تم إلغاء الحجز 🌿"
            else:
                state["last_bot_message"] = "Your booking has been cancelled 🌿"
        elif stage == "callback_pending":
            # Callback promised by doctor_selection_node — keep the
            # acknowledgment consistent if the reply was wiped.
            if lang == "ar":
                state["last_bot_message"] = (
                    "سجلنا طلبك وفريق خدمة العملاء راح يتواصل مع حضرتك قريباً 🌿"
                )
            else:
                state["last_bot_message"] = (
                    "Your request is logged — the contact-center team will reach out shortly 🌿"
                )
        elif stage == "routing":
            if lang == "ar":
                state["last_bot_message"] = "ممكن توضحلي وش المشكلة عشان أقدر أساعدك؟"
            else:
                state["last_bot_message"] = "Could you tell me what you need help with so I can find the right doctor?"
        elif stage in (None, "", "none"):
            if lang == "ar":
                state["last_bot_message"] = "السلام عليكم، معك نور من مجموعة أندلسية صحة 😊 اتشرف بالاسم؟"
            else:
                state["last_bot_message"] = "Hello! I'm Nour from Andalusia Health Group 😊 May I have your name please?"
        elif stage == "greeting_done":
            patient = state.get("patient_name", "")
            if lang == "ar":
                greeting = f"أ/ {patient}" if patient else ""
                state["last_bot_message"] = f"أهلا وسهلا بحضرتك {greeting}، كيف أقدر أساعدك؟"
            else:
                state["last_bot_message"] = f"Welcome {patient}! How can I help you today?"
        elif stage == "complaint":
            patient = state.get("patient_name", "")
            if lang == "ar":
                name_part = f" أ/ {patient}" if patient else ""
                state["last_bot_message"] = (
                    f"تمام{name_part} — ممكن تقولي وش المشكلة اللي محتاج كشف عليها؟ "
                    f"أو تخصص الدكتور اللي تبغى تحجز معه؟"
                )
            else:
                state["last_bot_message"] = (
                    f"Got it — could you tell me what you're dealing with, "
                    f"or which specialty you'd like to book with?"
                )
        elif stage == "doctor_list":
            if lang == "ar":
                state["last_bot_message"] = "تحب تحجز مع أي دكتور من القائمة؟ قولي رقم الدكتور أو اسمه."
            else:
                state["last_bot_message"] = "Which doctor would you like to book with? You can reply with the number or name."
        elif stage == "slot_selection":
            if lang == "ar":
                state["last_bot_message"] = "ما فهمت الوقت، ممكن توضح الوقت اللي يناسبك؟"
            else:
                state["last_bot_message"] = "I didn't catch the time — could you share the time that works for you?"
        elif stage == "patient_info":
            if lang == "ar":
                state["last_bot_message"] = "باقي خطوة بسيطة — ممكن رقم جوالك؟ وكاش ام تأمين؟"
            else:
                state["last_bot_message"] = "One last step — could I have your phone number, and is it cash or insurance?"
        elif stage == "complete":
            if lang == "ar":
                state["last_bot_message"] = "تم تأكيد الحجز 🌿"
            else:
                state["last_bot_message"] = "Your booking is confirmed 🌿"
        else:
            # Unknown stage — generic clarifier so user is never blocked
            if lang == "ar":
                state["last_bot_message"] = "ممكن تعيد طلبك بطريقة ثانية؟"
            else:
                state["last_bot_message"] = "Could you rephrase that?"

    # If the bot is about to send the same reply it just sent, break the loop
    # with an escalation message instead of repeating ourselves verbatim. Run
    # this BEFORE the apology prefix is added — otherwise a prefixed-vs-bare
    # version of the same message would slip through as "different".
    lang = state.get("language", "en")
    current_reply = state.get("last_bot_message") or ""
    if _is_repeating(state, current_reply):
        state["last_bot_message"] = _escalation_message(state, lang)
        # Stop further booking advancement on this stale path.
        if state.get("booking_stage") not in ("complete", "cancelled"):
            state["booking_stage"] = "callback_pending"

    # If the patient pushed back on something we said, acknowledge it before
    # delivering the reply. Avoids the loop where the bot repeats the same
    # answer in the face of "ما انتى قلتيلى اليوم متاح!".
    if _detect_user_challenge(state):
        reply = state.get("last_bot_message") or ""
        prefix = _apology_prefix(lang)
        # Only prepend once — don't stack apologies if the reply already
        # starts with one.
        if reply and not reply.lstrip().startswith(prefix):
            state["last_bot_message"] = f"{prefix} — {reply}"

    # Last-mile dialect sanitization. The conversation prompt forbids Egyptian
    # / Levantine markers in Saudi-language replies, but the LLM occasionally
    # drifts ("ازاي" / "عايز" / "هكلمك"). Replace before delivery so the
    # patient never sees off-dialect copy regardless of LLM compliance.
    from utils.language import sanitize_dialect
    if state.get("last_bot_message"):
        state["last_bot_message"] = sanitize_dialect(
            state["last_bot_message"], lang,
        )
    if state.get("followup_message"):
        state["followup_message"] = sanitize_dialect(
            state["followup_message"], lang,
        )

    # Bound the in-memory messages list before clearing transients. Cap
    # prevents long-lived sessions from growing unbounded; full audit
    # history is preserved in the SQLite `chat_logs` table.
    _trim_messages(state)

    # Clean up per-turn transient fields. `followup_message` is intentionally
    # NOT cleared here — app.py reads it after the graph returns.
    _clear_transient_fields(state)
    return state


# ── Graph construction ───────────────────────────────────────────────────────

# Worst-case path through the pipeline is ~9 node executions per turn
# (language → emergency → intent → conversation → routing → doctor_selection
# → slot_selection → patient_info → safety_net). The LangGraph default is 25;
# setting it explicitly at invoke time documents the expected envelope and
# makes any future cycle regression abort at a known threshold instead of
# silently chewing CPU.
RECURSION_LIMIT = 25


def build_graph(checkpointer=None) -> StateGraph:
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

    return graph.compile(checkpointer=checkpointer) if checkpointer else graph.compile()


# Optional LangGraph checkpointer. Off by default (LANGGRAPH_CHECKPOINTER=none
# in config.settings); flip to "memory" in `.env` to enable an in-process
# MemorySaver. State is keyed on the `thread_id` passed via the `configurable`
# config dict at invoke time (see app.py). This is the lever for future
# WhatsApp / FastAPI deployment where stateless workers need to look up state
# by session ID — flag it on once a durable backend is wired in.
_checkpointer = None
if LANGGRAPH_CHECKPOINTER == "memory":
    # Lazy import — only required when the flag is on.
    from langgraph.checkpoint.memory import MemorySaver
    _checkpointer = MemorySaver()
    print(f"[graph] LangGraph checkpointer enabled: memory")

compiled_graph = build_graph(checkpointer=_checkpointer)
