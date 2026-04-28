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
from utils.datetime_fmt import format_date, format_time
from utils.language import normalize_ar


def doctor_selection_node(state: BookingState) -> BookingState:
    """
    Processes doctor selection:
    - If patient typed a doctor name → fuzzy match it
    - If doctor is confirmed and slots are needed → fetch slots
    """
    lang = state.get("language", "en")
    updates = state.get("_llm_updates") or {}

    if state.get("booking_stage") in ("cancelled", "callback_pending"):
        return state

    # Case 1: Patient typed a doctor name — fuzzy match it.
    # Safety net first: when patients say "دكتور اسنان" / "doctor cardio", the
    # LLM often captures the specialty word as a doctor name. Strip the title
    # prefix and check against the specialty keyword list before treating it
    # as a name — otherwise we ask "do you mean Dr. اسنان?" which is nonsense.
    if updates.get("doctor_fuzzy_input") and not state.get("doctor"):
        raw_doctor = (updates["doctor_fuzzy_input"] or "").strip()
        stripped = raw_doctor
        for prefix in ("د. ", "د.", "د ", "دكتور ", "الدكتور ", "Dr. ", "Dr.", "Dr "):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()
                break
        from nodes.routing import _is_direct_specialty_request
        if stripped and _is_direct_specialty_request(stripped):
            state["last_bot_message"] = _redirect_to_specialty(stripped, state, lang)
            return state

        state["last_bot_message"] = _handle_doctor_match(
            updates["doctor_fuzzy_input"], state, lang
        )
        return state

    # Case 2: Doctor confirmed, need to fetch slots
    if updates.get("needs_slot_query") and state.get("doctor"):
        state["last_bot_message"] = _handle_slot_fetch(state, lang)
        return state

    return state


def _redirect_to_specialty(specialty_word: str, state: dict, lang: str) -> str:
    """Patient said "دكتور [specialty]" — route via specialty instead of by name.

    Mirrors the wording of routing._route_and_confirm so the next user turn
    ("ايوه") cleanly enters the specialty-confirmation acceptance branch.
    """
    from services.router import route_specialty
    from utils.datetime_fmt import format_date, today_iso
    from config.constants import SPECIALTY_EN_TO_AR

    state["complaint_text"] = specialty_word
    state["doctor"] = None
    state["doctor_ar"] = None
    state["specialty_confirmed"] = False
    if state.get("_llm_updates") is not None:
        state["_llm_updates"]["doctor_fuzzy_input"] = None

    result = route_specialty(
        complaint=specialty_word,
        age=state.get("patient_age") or 30,
        lang=lang,
    )
    specialty = result.get("specialty", "")
    if not specialty:
        # Fallback: ask the patient to clarify rather than treating as a name.
        if lang == "ar":
            return "ممكن توضحلي أكتر إيش التخصص اللي تبغاه؟"
        return "Could you tell me a bit more about which specialty you need?"

    state["speciality"] = specialty
    state["booking_stage"] = "routing"

    spec_display = SPECIALTY_EN_TO_AR.get(specialty, specialty) if lang == "ar" else specialty
    date_for_label = state.get("requested_date") or state.get("date") or today_iso()
    date_label = format_date(date_for_label, lang)
    raw_name = state.get("patient_name") or ""
    # Guard against the LLM echoing back placeholder strings from the prompt's
    # state_summary (e.g. "not collected yet — ASK FOR NAME FIRST").
    patient = raw_name if raw_name and "not collected" not in raw_name.lower() else ""

    if lang == "ar":
        name_part = f" أ/ {patient}" if patient else ""
        return (
            f"تمام{name_part}، حضرتك محتاج تخصص **{spec_display}**. "
            f"تبغى أعرض لك الأطباء المتاحين {date_label}؟"
        )
    return (
        f"Got it — you'll need **{spec_display}**. "
        f"Would you like to see the available doctors for {date_label}?"
    )


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


def _handle_partial_doctor_name(raw: str, state: dict, lang: str) -> str:
    """
    Escalating response when patient gives only a single short doctor name.
    1st turn: ask for full name OR specialty (so we can show alternatives).
    2nd turn (same partial name repeated): hint that we can list doctors by
        specialty, and signal that a callback is available if they insist.
    3rd turn (still the same partial name): promise contact-center callback
        and set a `callback_pending` terminal stage so the rest of the
        booking pipeline disengages.
    """
    norm = normalize_ar(raw).lower()
    prev = state.get("_partial_doctor_name") or ""
    if norm and norm == prev:
        attempts = (state.get("_partial_doctor_attempts") or 0) + 1
    else:
        attempts = 1
    state["_partial_doctor_name"] = norm
    state["_partial_doctor_attempts"] = attempts

    if attempts == 1:
        if lang == "ar":
            return (
                f"ما لقيت دكتور باسم \"{raw}\" بس. ممكن تقولي اسمه كامل، "
                f"أو تقولي تخصصه (مثلاً: عيون، جلدية، اسنان، باطنة) "
                f"عشان أعرض لك الأطباء المتاحين؟"
            )
        return (
            f"I couldn't find a doctor by just \"{raw}\". Could you give me "
            f"the full name, or tell me their specialty (e.g. ophthalmology, "
            f"dermatology, dentistry) so I can show you the available doctors?"
        )

    if attempts == 2:
        if lang == "ar":
            return (
                f"للأسف لسه ما لقيت \"{raw}\" في النظام. لو تعرف تخصصه "
                f"قوللي عشان أعرض لك الأطباء المتاحين في نفس التخصص. "
                f"ولو محتاج هذا الدكتور بالذات، نقدر نسجل بياناتك "
                f"وفريق خدمة العملاء يتواصل مع حضرتك."
            )
        return (
            f"I still can't find \"{raw}\" in the system. If you know the "
            f"specialty, tell me and I'll show you available doctors there. "
            f"Or if you need this specific doctor, we can pass your details "
            f"to the contact-center team to call you back."
        )

    # 3rd+ attempt — patient is insisting on the same partial name.
    state["booking_stage"] = "callback_pending"
    state["_partial_doctor_attempts"] = 0
    state["_partial_doctor_name"] = None
    phone = state.get("phone")
    if lang == "ar":
        if phone:
            tail = f"على الرقم {phone} في أقرب وقت."
        else:
            tail = "بمجرد تسجيل رقم تواصل."
        return (
            f"للأسف ما قدرنا نلاقي الدكتور \"{raw}\" في نظامنا. "
            f"سجلنا طلبك وفريق خدمة العملاء راح يتواصل مع حضرتك "
            f"{tail} شكراً لتواصلك مع أندلسية صحة 🌿"
        )
    if phone:
        tail = f"on {phone} as soon as possible."
    else:
        tail = "once you share a contact number."
    return (
        f"I'm sorry, we couldn't locate Dr. \"{raw}\" in our system. "
        f"I've logged your request and our contact-center team will reach "
        f"out to you {tail} Thank you for choosing Andalusia Health 🌿"
    )


def _rollback_to_complaint(state: dict) -> None:
    """Path C lookup failed — roll booking_stage back to 'complaint' so the
    safety_net doesn't loop on 'pick a doctor from the list' when no list exists."""
    if state.get("booking_stage") in ("doctor_list", "slot_selection") \
            and not state.get("available_doctors"):
        state["booking_stage"] = "complaint"


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
        _rollback_to_complaint(state)
        return doctor_not_found_message(None, lang)

    # Guard: a single short first name (e.g. "ولاء", "محمد") is too ambiguous
    # for the exact-match DB query — and the 7-day fallback cascade against an
    # exact-match query is extremely slow (~20s × 16 attempts = 320s) when it
    # finds nothing. Escalate the conversation instead of looping on the same
    # "full name please" prompt.
    if len(raw.split()) == 1 and len(raw) < 7:
        _rollback_to_complaint(state)
        return _handle_partial_doctor_name(raw, state, lang)

    # Try EN name first, then AR
    rows, used_date = query_availability_with_fallback(doctor_en=raw)
    if not rows:
        rows, used_date = query_availability_with_fallback(doctor_ar=raw)

    if not rows:
        _rollback_to_complaint(state)
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
    state["_partial_doctor_name"] = None
    state["_partial_doctor_attempts"] = 0

    return _handle_slot_fetch(state, lang)


def _handle_slot_fetch(state: dict, lang: str, preferred_date: str = None) -> str:
    # Honour the patient's requested date (e.g. "بكرا") if they gave one before
    # the slot lookup — otherwise use the current date context.
    date_to_use = preferred_date or state.get("requested_date") or state.get("date")
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
    # Flag so slot_selection_node's auto-confirm doesn't fire this turn —
    # the user hasn't seen the "does this time work?" question yet.
    state["_proposal_shown_this_turn"] = True
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
