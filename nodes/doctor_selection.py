# nodes/doctor_selection.py
"""
Handles doctor matching from patient input and fetching their available slots.
"""
from state import BookingState
from services.doctor_selector import match_doctor, fetch_slots
from services.slot_handler import get_initial_slots
from services.formatter import (
    slot_question, doctor_confirm_message,
    doctor_not_found_message,
)
from utils.datetime_fmt import format_date


def doctor_selection_node(state: BookingState) -> BookingState:
    """
    Processes doctor selection:
    - If patient typed a doctor name → fuzzy match it
    - If doctor is confirmed and slots are needed → fetch slots
    """
    lang = state.get("language", "en")
    updates = state.get("_llm_updates") or {}

    # Case 1: Patient typed a doctor name — fuzzy match it
    if updates.get("doctor_fuzzy_input") and not state.get("doctor"):
        state["last_bot_message"] = _handle_doctor_match(
            updates["doctor_fuzzy_input"], state, lang
        )
        return state

    # Case 2: Doctor confirmed, need to fetch slots
    if updates.get("needs_slot_query") and state.get("doctor"):
        state["last_bot_message"] = _handle_slot_fetch(state, lang)
        return state

    return state


def _handle_doctor_match(raw_input: str, state: dict, lang: str) -> str:
    m = match_doctor(raw_input, state.get("available_doctors", []))
    specialty = state.get("speciality") or state.get("speciality_ar", "")

    if m["status"] == "matched":
        doc = m["doctor"]
        state["doctor"] = doc.get("Doctor", "")
        state["doctor_ar"] = doc.get("DoctorAR", "")
        return _handle_slot_fetch(state, lang)

    if m["status"] == "confirm":
        state["doctor_confirmation_pending"] = m.get("matched_name", "")
        return doctor_confirm_message(m.get("matched_name", ""), lang)

    return doctor_not_found_message(specialty, lang)


def _handle_slot_fetch(state: dict, lang: str, preferred_date: str = None) -> str:
    date_to_use = preferred_date or state.get("date")
    slots, used_date = fetch_slots(state["doctor"], state.get("doctor_ar"), date_to_use)

    # Fallback: if DB slot query returned nothing but we have availability data,
    # extract slots from the raw availability rows stored in available_doctors.
    # This handles cases where the doctor name format differs between queries.
    if not slots:
        slots = _extract_slots_from_availability(state)
        if slots:
            used_date = date_to_use or state.get("date", "")

    state["date"] = used_date
    state["available_slots"] = slots

    if not slots:
        date_label = format_date(used_date, lang)
        if lang == "ar":
            return f"للأسف ما في مواعيد متاحة في {date_label}. تبغى أجرب يوم ثاني؟"
        return f"No slots available on {date_label}. Would you like to try another date?"

    slot_info = get_initial_slots(slots)
    state["booking_stage"] = "slot_selection"
    return slot_question(state["doctor"], state.get("doctor_ar", ""), slot_info, lang)


def _extract_slots_from_availability(state: dict) -> list:
    """
    When the dedicated slots query fails (name mismatch between queries),
    reconstruct slot data from the availability rows we already have.
    The available_doctors list was built from raw rows that contain Slot_Date and Slot_Time.
    We need to go back to the raw availability data — but we don't store it.
    Instead, use the doctor's known slot info to query by PhysicianID if available,
    or try alternate name formats.
    """
    doctor_en = state.get("doctor", "")
    doctor_ar = state.get("doctor_ar", "")

    if not doctor_en and not doctor_ar:
        return []

    # Try with swapped params (EN name as AR param and vice versa)
    from db.database import query_doctor_slots_with_fallback
    date_to_use = state.get("date")

    # Try EN name only (AR as None)
    slots, _ = query_doctor_slots_with_fallback(
        doctor_en=doctor_en or None, doctor_ar=None, preferred_date=date_to_use
    )
    if slots:
        return slots

    # Try AR name only (EN as None)
    if doctor_ar:
        slots, _ = query_doctor_slots_with_fallback(
            doctor_en=None, doctor_ar=doctor_ar, preferred_date=date_to_use
        )
        if slots:
            return slots

    # Try EN name in AR field (some DBs store names inconsistently)
    if doctor_en:
        slots, _ = query_doctor_slots_with_fallback(
            doctor_en=None, doctor_ar=doctor_en, preferred_date=date_to_use
        )
        if slots:
            return slots

    return []
