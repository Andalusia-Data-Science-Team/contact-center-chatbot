# nodes/doctor_selection.py
"""
Handles doctor matching from patient input and fetching their available slots.
"""
import re
from state import BookingState
from nodes._helpers import get_last_user_message
from services.doctor_selector import match_doctor, fetch_slots
from services.slot_handler import get_initial_slots
from services.formatter import (
    slot_question, doctor_confirm_message,
    doctor_not_found_message,
)
from utils.datetime_fmt import format_date, format_time
from utils.language import normalize_ar


# Signals the patient is ready to proceed even though they didn't explicitly
# pick a doctor by number or name. When at doctor_list stage and any of these
# show up, auto-pick the first doctor so the flow advances instead of looping
# "تحب تحجز مع أي دكتور؟".
_PROCEED_HINTS = (
    "yes", "ok", "sure", "fine", "perfect", "works", "confirmed",
    "today", "tomorrow", "tonight", "morning", "afternoon", "evening", "night",
    "am", "pm", "cash", "insurance", "insured",
    "نعم", "ايوه", "تمام", "ماشي", "مناسب", "موافق", "يناسبني",
    "اليوم", "بكرا", "بكره", "غدا", "الصبح", "الصباح",
    "الظهر", "العصر", "المغرب", "المساء", "مساء", "الليل", "الليله",
    "كاش", "نقدي", "تامين", "تأمين",
)

_REJECT_HINTS = ("لا", "no", "not", "مش", "مو", "ما")


def _user_signals_proceed(text: str) -> bool:
    """True if the message reads like the patient is ready to proceed
    (time / date / phone / cash-insurance / acceptance) rather than picking
    a specific doctor or rejecting."""
    if not text:
        return False
    t = normalize_ar(text).lower()
    for neg in _REJECT_HINTS:
        if re.search(rf"(?<![\w]){re.escape(neg)}(?![\w])", t):
            return False
    # Phone number (7+ digits) is a clear "proceed" signal
    if re.search(r"\d{7,}", t):
        return True
    for hint in _PROCEED_HINTS:
        if hint in t:
            return True
    return False


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

    # Case 3: Patient is at doctor_list stage, no doctor picked, and their
    # message signals "ready to proceed" (gave a time/phone/insurance/acceptance)
    # rather than picking a specific doctor. Auto-pick the first available
    # doctor so the flow advances instead of looping on "which doctor?".
    if (
        state.get("booking_stage") == "doctor_list"
        and not state.get("doctor")
        and state.get("available_doctors")
    ):
        last_msg = get_last_user_message(state) or ""
        if _user_signals_proceed(last_msg):
            doc = state["available_doctors"][0]
            state["doctor"] = doc.get("Doctor", "")
            state["doctor_ar"] = doc.get("DoctorAR", "")
            state["walk_in_price"] = doc.get("WalkInPrice")
            state["last_bot_message"] = _handle_slot_fetch(state, lang)
            return state

    return state


def _handle_doctor_match(raw_input: str, state: dict, lang: str) -> str:
    # Path C: patient named a doctor directly without prior specialty routing.
    # No doctor list loaded yet — query the DB directly by doctor name.
    if not state.get("available_doctors"):
        return _handle_direct_doctor_lookup(raw_input, state, lang)

    m = match_doctor(raw_input, state.get("available_doctors", []))
    specialty = state.get("speciality") or state.get("speciality_ar", "")

    if m["status"] == "matched":
        doc = m["doctor"]
        state["doctor"] = doc.get("Doctor", "")
        state["doctor_ar"] = doc.get("DoctorAR", "")
        state["walk_in_price"] = doc.get("WalkInPrice")
        return _handle_slot_fetch(state, lang)

    if m["status"] == "confirm":
        state["doctor_confirmation_pending"] = m.get("matched_name", "")
        return doctor_confirm_message(m.get("matched_name", ""), lang)

    return doctor_not_found_message(specialty, lang)


def _handle_direct_doctor_lookup(raw_input: str, state: dict, lang: str) -> str:
    """
    Path C: resolve a doctor name globally by querying availability across all
    specialties. Populates state["doctor"]/["doctor_ar"]/["speciality"] and then
    fetches their slots.
    """
    from db.database import query_availability_with_fallback, aggregate_doctor_slots
    from services.doctor_price import enrich_doctors_with_prices

    raw = (raw_input or "").strip()
    # Strip common Arabic prefixes that aren't part of the name
    for prefix in ("د. ", "د.", "د ", "دكتور ", "الدكتور ", "Dr. ", "Dr.", "Dr "):
        if raw.startswith(prefix):
            raw = raw[len(prefix):].strip()
            break

    if not raw:
        return doctor_not_found_message(None, lang)

    # Try EN name first, then AR
    rows, used_date = query_availability_with_fallback(doctor_en=raw)
    if not rows:
        rows, used_date = query_availability_with_fallback(doctor_ar=raw)

    if not rows:
        return doctor_not_found_message(None, lang)

    doctors = aggregate_doctor_slots(rows)
    if not doctors:
        return doctor_not_found_message(None, lang)

    enrich_doctors_with_prices(doctors)

    # Take the first match (DB query already filtered by exact name)
    doc = doctors[0]
    state["doctor"] = doc.get("Doctor", "")
    state["doctor_ar"] = doc.get("DoctorAR", "")
    state["speciality"] = doc.get("Specialty", "") or state.get("speciality")
    state["speciality_ar"] = doc.get("SpecialtyAR", "") or state.get("speciality_ar")
    state["specialty_confirmed"] = True
    state["date"] = used_date
    state["available_doctors"] = doctors
    state["walk_in_price"] = doc.get("WalkInPrice")

    return _handle_slot_fetch(state, lang)


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
    # Mark the displayed earliest slot as a proposal so any acceptance reply —
    # including one combined with another question (e.g. a price inquiry) —
    # can confirm it deterministically without going back through the LLM.
    first_time = slot_info["first_time"]
    state["_proposed_slot"] = {
        "time_24": first_time.strftime("%H:%M"),
        "time_display": format_time(first_time, lang),
        "date": slot_info["first_date"].strftime("%Y-%m-%d"),
        "date_display": format_date(slot_info["first_date"], lang),
    }
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
