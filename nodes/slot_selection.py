# nodes/slot_selection.py
"""
Handles the 'slot_selection' booking stage:
- Show all slots on request
- Re-fetch slots for a different date
- Parse time preference → find nearest slot → confirm

Updated to match real Andalusia agent style:
- Warmer, more concise messages
- "ممكن رقم جوالك؟ وكاش ام تأمين؟" after slot confirmation
"""
from state import BookingState
from nodes._helpers import is_acceptance, is_patient_info_complete
from services.doctor_selector import fetch_slots
from services.slot_handler import (
    get_initial_slots, get_all_slots, parse_time_preference, find_nearest_slot,
    parse_time_filter,
)
from services.formatter import (
    more_slots_message, slot_confirmed_message, confirmation_message,
)
from utils.datetime_fmt import format_date, format_time


def slot_selection_node(state: BookingState) -> BookingState:
    """
    Process slot-related actions.
    Activates when:
    - booking_stage == 'slot_selection' (normal flow)
    - Patient gives a preferred_time while a doctor and slots exist (reschedule/change)
    """
    lang = state.get("language", "en")
    updates = state.get("_llm_updates") or {}
    stage = state.get("booking_stage")

    # Must be in an active booking with a doctor to do anything here
    if not state.get("doctor"):
        return state

    # ── Safety net: detect slot-related requests the LLM missed ──
    # If LLM generated a non-empty reply but the user's message contains
    # time/slot keywords, override the LLM and handle it in code
    if stage in ("slot_selection", "patient_info") and state.get("available_slots"):
        updates = _slot_safety_net(state, updates)
        state["_llm_updates"] = updates

    # ── Show all/filtered slots on the same date ──
    # Also allow from patient_info stage (patient wants to change their slot before confirming)
    has_filter = bool(updates.get("slots_filter"))
    if (stage in ("slot_selection", "patient_info")
            and (updates.get("wants_more_slots") or has_filter)
            and state.get("available_slots")):
        # If coming from patient_info, patient wants to change their slot — reset
        if stage == "patient_info":
            state["selected_slot"] = None
            state["booking_stage"] = "slot_selection"
        filter_text = updates.get("slots_filter") or ""
        time_filter = parse_time_filter(filter_text) if filter_text else {"start": None, "end": None, "label": ""}

        all_slots = get_all_slots(
            state["available_slots"],
            start_time=time_filter.get("start"),
            end_time=time_filter.get("end"),
        )

        if not all_slots and filter_text:
            # No slots match the filter — tell patient and ask what they want to do
            filter_label = time_filter.get("label", "")
            doc = state.get("doctor", "")
            doc_ar = state.get("doctor_ar", "")
            doc_name = (doc_ar or doc) if lang == "ar" else doc
            prefix = "د. " if lang == "ar" else "Dr. "
            if lang == "ar":
                state["last_bot_message"] = (
                    f"للأسف {prefix}{doc_name} ما عنده مواعيد في فترة {filter_label}.\n\n"
                    f"تبغى أعرض لك كل المواعيد المتاحة؟ أو تفضل وقت ثاني؟"
                )
            else:
                state["last_bot_message"] = (
                    f"Unfortunately {prefix}{doc_name} has no slots available {filter_label}.\n\n"
                    f"Would you like to see all available slots? Or prefer a different time?"
                )
        else:
            filter_label = time_filter.get("label", "")
            state["last_bot_message"] = more_slots_message(
                state["doctor"], state.get("doctor_ar", ""), all_slots, lang,
                filter_label=filter_label,
            )
        return state

    # ── Patient asks for a different date → re-fetch slots ──
    if (stage == "slot_selection"
            and updates.get("requested_date")
            and not state.get("selected_slot")):
        state["last_bot_message"] = _handle_slot_fetch(
            state, lang, preferred_date=updates["requested_date"]
        )
        return state

    # ── Patient gives a time preference ──
    # BUT NOT on the same turn the doctor was just selected (needs_slot_query flag)
    if (updates.get("preferred_time")
            and state.get("available_slots")
            and stage in ("slot_selection", "patient_info", "complete")
            and not updates.get("needs_slot_query")):  # Don't override doctor_selection's slot_question

        # Check if patient is just accepting the shown earliest slot
        if not state.get("selected_slot"):
            slot_info = get_initial_slots(state["available_slots"])
            if slot_info and is_acceptance(updates["preferred_time"]):
                return _confirm_initial_slot(state, slot_info, lang)

        # Find nearest slot to their preference
        state["last_bot_message"] = _handle_time_preference(
            updates["preferred_time"], state, lang
        )
        return state

    return state


def _confirm_initial_slot(state: dict, slot_info: dict, lang: str) -> BookingState:
    """Patient accepted the displayed earliest slot."""
    t = slot_info["first_time"]
    time_24 = t.strftime("%H:%M")
    time_str = format_time(t, lang)
    date_str = format_date(slot_info["first_date"], lang)

    state["selected_slot"] = time_24
    state["date"] = slot_info["first_date"].strftime("%Y-%m-%d")

    # If patient info is already collected (slot change), go straight to confirmation
    if is_patient_info_complete(state):
        state["booking_stage"] = "complete"
        state["last_bot_message"] = confirmation_message(state, lang)
        return state

    state["booking_stage"] = "patient_info"

    doc = state.get("doctor", "")
    doc_ar = state.get("doctor_ar", "")
    prefix = "د. " if lang == "ar" else "Dr. "
    name = (doc_ar or doc) if lang == "ar" else doc
    patient = state.get("patient_name", "")

    if lang == "ar":
        # Real agent style: confirm slot then ask for phone + insurance
        patient_greeting = f" أ/ {patient}" if patient else ""
        state["last_bot_message"] = (
            f"تمام{patient_greeting}، موعدك مع {prefix}{name} يوم {date_str} الساعة {time_str}.\n\n"
            f"ممكن رقم جوالك؟ وكاش ام تأمين؟"
        )
    else:
        patient_greeting = f"{patient}, " if patient else ""
        state["last_bot_message"] = (
            f"Great {patient_greeting}your slot with {prefix}{name} is set for {date_str} at {time_str}.\n\n"
            f"I just need your phone number, and will it be cash or insurance?"
        )
    return state


def _handle_time_preference(preferred_raw: str, state: dict, lang: str) -> str:
    """Parse free-text time and find nearest available slot."""

    # Check if patient is accepting a previously proposed slot
    proposed = state.get("_proposed_slot")
    if proposed and is_acceptance(preferred_raw):
        # Patient accepted the proposed slot — confirm it
        state["selected_slot"] = proposed["time_24"]
        state["date"] = proposed["date"]
        state["_proposed_slot"] = None  # Clear proposal

        if is_patient_info_complete(state):
            state["booking_stage"] = "complete"
            return confirmation_message(state, lang)

        state["booking_stage"] = "patient_info"
        return slot_confirmed_message(
            state["doctor"], state.get("doctor_ar", ""),
            proposed["date_display"], proposed["time_display"], lang,
            is_fallback=False,
        )

    # Clear any old proposal
    state["_proposed_slot"] = None

    target = parse_time_preference(preferred_raw)
    if not target:
        return (
            "ما فهمت الوقت، ممكن توضح أكتر؟"
            if lang == "ar"
            else "I didn't catch the time — could you clarify?"
        )

    nearest = find_nearest_slot(state["available_slots"], target)
    if not nearest:
        return "للأسف ما في مواعيد متاحة." if lang == "ar" else "Sorry, no slots available."

    time_str = format_time(nearest["time"], lang)
    time_24 = nearest["time"].strftime("%H:%M")
    date_str = format_date(nearest["date"], lang)
    is_exact = (nearest["time"] == target)

    # If NOT an exact match, propose and wait for confirmation
    if not is_exact:
        # Store the proposal so we can confirm it next turn
        state["_proposed_slot"] = {
            "time_24": time_24,
            "time_display": time_str,
            "date": nearest["date"].strftime("%Y-%m-%d"),
            "date_display": date_str,
        }

        doc = state.get("doctor", "")
        doc_ar = state.get("doctor_ar", "")
        doc_name = (doc_ar or doc) if lang == "ar" else doc
        prefix = "د. " if lang == "ar" else "Dr. "
        target_display = format_time(target, lang)

        if lang == "ar":
            return (
                f"أقرب موعد لـ {target_display} مع {prefix}{doc_name} هو **{time_str}**.\n\n"
                f"يناسبك؟ أو قولي وقت ثاني."
            )
        return (
            f"The closest slot to {target_display} with {prefix}{doc_name} is **{time_str}**.\n\n"
            f"Does that work? Or let me know another time."
        )

    # Exact match — confirm directly
    state["selected_slot"] = time_24
    state["date"] = nearest["date"].strftime("%Y-%m-%d")

    if is_patient_info_complete(state):
        state["booking_stage"] = "complete"
        return confirmation_message(state, lang)

    state["booking_stage"] = "patient_info"

    return slot_confirmed_message(
        state["doctor"], state.get("doctor_ar", ""),
        date_str, time_str, lang,
        is_fallback=False,
    )


def _handle_slot_fetch(state: dict, lang: str, preferred_date: str = None) -> str:
    """Fetch slots for a specific date."""
    from services.slot_handler import get_initial_slots
    from services.formatter import slot_question
    from db.database import query_doctor_slots_with_fallback

    date_to_use = preferred_date or state.get("date")
    slots, used_date = fetch_slots(state["doctor"], state.get("doctor_ar"), date_to_use)

    # Fallback: try alternate name combinations if primary query returned nothing
    if not slots:
        doctor_en = state.get("doctor", "")
        doctor_ar = state.get("doctor_ar", "")

        # Try EN only
        if doctor_en:
            slots, used_date = query_doctor_slots_with_fallback(
                doctor_en=doctor_en, doctor_ar=None, preferred_date=date_to_use
            )
        # Try AR only
        if not slots and doctor_ar:
            slots, used_date = query_doctor_slots_with_fallback(
                doctor_en=None, doctor_ar=doctor_ar, preferred_date=date_to_use
            )
        # Try EN in AR field
        if not slots and doctor_en:
            slots, used_date = query_doctor_slots_with_fallback(
                doctor_en=None, doctor_ar=doctor_en, preferred_date=date_to_use
            )

    state["date"] = used_date
    state["available_slots"] = slots

    if not slots:
        date_label = format_date(used_date, lang)
        if lang == "ar":
            return f"للأسف ما في مواعيد في {date_label}. تبغى أجرب يوم ثاني؟"
        return f"No slots available on {date_label}. Would you like to try another date?"

    slot_info = get_initial_slots(slots)
    state["booking_stage"] = "slot_selection"
    return slot_question(state["doctor"], state.get("doctor_ar", ""), slot_info, lang)


# ── Safety net: catch slot requests the LLM missed ──────────────────────────

_TIME_PERIOD_KEYWORDS = {
    "tonight", "evening", "morning", "afternoon", "night", "this evening",
    "الليلة", "المساء", "مساء", "الصبح", "بعد الظهر", "العصر", "بعد العصر",
    "الليل", "صباح",
}

_TIME_INDICATORS = {
    "after", "at", "around", "before", "بعد", "قبل", "حوالي", "الساعة", "عند",
}

import re
_TIME_PATTERN = re.compile(r'\b(?:at|after|around|before|بعد|قبل|حوالي|الساعة)\s*\d{1,2}', re.IGNORECASE)
_BARE_TIME_PATTERN = re.compile(r'\b\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM)\b')


def _slot_safety_net(state: dict, updates: dict) -> dict:
    """
    If the LLM failed to set slot-related state_updates but the user's message
    clearly contains a time request, fix the updates so the slot_selection logic fires.
    """
    from nodes._helpers import get_last_user_message

    # Only intervene if LLM didn't already set the right flags
    if updates.get("wants_more_slots") or updates.get("preferred_time") or updates.get("slots_filter"):
        return updates

    last_msg = get_last_user_message(state)
    if not last_msg:
        return updates

    msg_lower = last_msg.lower().strip()

    # Check for time period keywords → wants_more_slots + slots_filter
    for period in sorted(_TIME_PERIOD_KEYWORDS, key=len, reverse=True):
        if period in msg_lower:
            updates["wants_more_slots"] = True
            updates["slots_filter"] = period
            state["last_bot_message"] = ""  # Clear LLM's reply
            return updates

    # Check for "show all" / "show available" type requests
    show_keywords = {"show all", "show slot", "show available", "all slot", "what else",
                     "list", "available", "عرض", "المتاح", "كل المواعيد"}
    for kw in show_keywords:
        if kw in msg_lower:
            updates["wants_more_slots"] = True
            state["last_bot_message"] = ""
            return updates

    # Check for specific time patterns ("after 7", "at 8", "8 PM")
    if _TIME_PATTERN.search(msg_lower) or _BARE_TIME_PATTERN.search(msg_lower):
        # Extract the time text
        match = _TIME_PATTERN.search(msg_lower) or _BARE_TIME_PATTERN.search(msg_lower)
        if match:
            updates["preferred_time"] = match.group(0).strip()
            state["last_bot_message"] = ""
            return updates

    # Check for bare numbers that look like times (e.g. "7", "8")
    # Only if the message is very short (1-3 words) to avoid false positives
    words = msg_lower.split()
    if len(words) <= 3:
        for word in words:
            if re.match(r'^\d{1,2}(:\d{2})?$', word):
                updates["preferred_time"] = word
                state["last_bot_message"] = ""
                return updates

    return updates
