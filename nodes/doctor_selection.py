# nodes/doctor_selection.py
"""
Handles doctor matching from patient input and fetching their available slots.
"""
from datetime import datetime

from state import BookingState
from nodes._helpers import get_last_user_message
from services.doctor_selector import match_doctor, fetch_slots
from services.slot_handler import get_initial_slots
from services.formatter import (
    slot_question, doctor_confirm_message,
    doctor_not_found_message,
)
from utils.datetime_fmt import format_date, format_time, _ISO_DATE_RE
from utils.language import normalize_ar


# Date keywords we deterministically catch at the doctor_list stage when the
# LLM misses extracting requested_date (e.g. "متاح غدا؟").
_DOCTOR_LIST_DATE_KEYWORDS = (
    "اليوم", "بكرا", "بكره", "بكرة", "غدا", "غداً", "today", "tomorrow",
)


def doctor_selection_node(state: BookingState) -> BookingState:
    """
    Processes doctor selection:
    - If patient typed a doctor name → fuzzy match it
    - If doctor is confirmed and slots are needed → fetch slots
    - At doctor_list stage, deterministically handle bare digits (pick by number)
      and date keywords (refetch list) when the LLM misses them
    """
    lang = state.get("language", "en")
    updates = state.get("_llm_updates") or {}

    if state.get("booking_stage") in ("cancelled", "callback_pending"):
        return state

    # Single-doctor branch: only one doctor was listed and we asked the patient
    # to pick "all slots" vs "earliest". The doctor is already auto-selected;
    # the user's reply just decides which slot view to show.
    if state.get("_single_doctor_choice_pending"):
        state["_single_doctor_choice_pending"] = False
        last_msg = (get_last_user_message(state) or "").strip()
        if _wants_all_slots(last_msg):
            state["last_bot_message"] = _show_all_slots_for_doctor(state, lang)
        else:
            state["last_bot_message"] = _handle_slot_fetch(state, lang)
        # Block slot_selection from re-interpreting "أقرب" / "show all" / "نعم"
        # as a time preference and overwriting the reply.
        state["_skip_time_preference_this_turn"] = True
        return state

    # Case 1: Patient typed a doctor name — fuzzy match it.
    # Safety net first: when patients say "دكتور اسنان" / "doctor cardio", the
    # LLM often captures the specialty word as a doctor name. Strip the title
    # prefix and check against the specialty keyword list before treating it
    # as a name — otherwise we ask "do you mean Dr. اسنان?" which is nonsense.
    #
    # Allow a doctor switch when same-day alternatives were just offered: the
    # patient has effectively been told "pick another doctor for today", so a
    # named doctor in the next reply is a switch, not a duplicate selection.
    allow_switch = bool(state.get("_alternatives_offered"))
    if updates.get("doctor_fuzzy_input") and (not state.get("doctor") or allow_switch):
        if allow_switch:
            # Reset selection state so the existing match flow re-runs cleanly
            # against `available_doctors` for the new pick.
            state["doctor"] = None
            state["doctor_ar"] = None
            state["selected_slot"] = None
            state["_proposed_slot"] = None
            state["available_slots"] = []
            state["_alternatives_offered"] = False
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

    # Case 3: Doctor-list safety nets. The LLM mis-classifies bare digits ("4")
    # as time inputs and silently drops date keywords like "متاح غدا؟" — handle
    # both here deterministically before any later node treats "4" as a time.
    if (state.get("booking_stage") == "doctor_list"
            and not state.get("doctor")
            and state.get("available_doctors")):
        last_msg = (get_last_user_message(state) or "").strip()

        # 3a: Bare digit → pick that doctor by index
        if last_msg.isdigit():
            idx = int(last_msg) - 1
            if 0 <= idx < len(state["available_doctors"]):
                doc = state["available_doctors"][idx]
                state["doctor"] = doc.get("Doctor", "")
                state["doctor_ar"] = doc.get("DoctorAR", "")
                state["walk_in_price"] = doc.get("WalkInPrice")
                # Tell slot_selection_node not to re-process the digit as a time.
                # Without this, _slot_safety_net catches "2" as a bare time and
                # _handle_time_preference replies "ما فهمت الوقت", overwriting
                # the slot_question we just generated.
                state["_skip_time_preference_this_turn"] = True
                state["last_bot_message"] = _handle_slot_fetch(state, lang)
                return state

        # 3b: Date keyword → refetch doctor list for the new date
        new_date = state.get("requested_date")
        if not (new_date and _ISO_DATE_RE.match(new_date)):
            # apply_llm_updates didn't capture it — scan the message ourselves
            msg_norm = normalize_ar(last_msg)
            if any(kw in msg_norm for kw in _DOCTOR_LIST_DATE_KEYWORDS):
                from utils.datetime_fmt import resolve_relative_date
                resolved = resolve_relative_date(last_msg)
                if _ISO_DATE_RE.match(resolved):
                    new_date = resolved
                    state["requested_date"] = resolved

        if new_date and _ISO_DATE_RE.match(new_date) and new_date != state.get("date"):
            from services.doctor_selector import fetch_doctors
            from services.formatter import (
                doctor_list_message, no_doctors_on_date_message,
            )
            specialty = state.get("speciality") or state.get("speciality_ar") or ""
            # Patient just asked for a specific date — restrict to that day so
            # the new list either matches their ask or we tell them clearly
            # nothing is available for it, instead of silently rolling forward.
            doctors, used_date = fetch_doctors(specialty, lang, new_date, strict_date=True)
            state["date"] = used_date
            state["available_doctors"] = doctors
            if doctors:
                if len(doctors) == 1:
                    only = doctors[0]
                    state["doctor"] = only.get("Doctor", "") or ""
                    state["doctor_ar"] = only.get("DoctorAR", "") or ""
                    if only.get("WalkInPrice") is not None:
                        state["walk_in_price"] = only.get("WalkInPrice")
                    state["last_bot_message"] = _handle_slot_fetch(state, lang)
                else:
                    state["last_bot_message"] = doctor_list_message(
                        doctors, specialty, lang, used_date,
                    )
            else:
                state["last_bot_message"] = no_doctors_on_date_message(
                    specialty, new_date, lang,
                )
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
            return "ممكن توضحلي أكثر وش التخصص اللي تبغاه؟"
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
        if m.get("matched_by") == "number":
            state["_skip_time_preference_this_turn"] = True
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
    explicit_requested = preferred_date or state.get("requested_date")
    date_to_use = explicit_requested or state.get("date")
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
        # Before falling back to "try another date", surface any other doctors
        # in the same specialty that we already loaded — gives the patient a
        # concrete alternative instead of a dead-end.
        others = _other_doctors_with_availability(state)
        if others:
            from services.formatter import no_slots_with_alternatives_message
            state["_alternatives_offered"] = True
            return no_slots_with_alternatives_message(
                state.get("doctor", ""), state.get("doctor_ar", ""),
                others, lang,
            )
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

    # When the slot fetch silently shifted to a later day, look for OTHER
    # doctors in the same specialty who DO have slots on the requested date.
    # Surfacing those alternatives lets the patient stay on their preferred
    # day with a different doctor instead of being limited to the next-day
    # fallback for the doctor they originally picked. Use date_to_use (the
    # date we asked for — either patient-requested or the date the list was
    # shown for) so this fires even when the patient never explicitly typed
    # a date but the selected doctor's slots silently slipped to a later day.
    state["_alternatives_offered"] = False
    target_for_alternatives = date_to_use
    if (
        target_for_alternatives
        and used_date
        and target_for_alternatives != used_date
    ):
        alternatives = _find_same_day_alternatives(
            state,
            target_date=target_for_alternatives,
            exclude_doctor=state.get("doctor", ""),
        )
        if alternatives:
            from services.formatter import slot_with_alternatives_message
            state["_alternatives_offered"] = True
            # Make the alternatives addressable by name on the next turn — the
            # match_doctor flow only looks at available_doctors, so without this
            # a patient who says "Dr. Y" couldn't switch to an alternative that
            # came from the slow-path DB query.
            existing = state.get("available_doctors") or []
            existing_keys = {
                (d.get("Doctor", "") or "").strip().lower() for d in existing
            }
            for alt in alternatives:
                key = (alt.get("Doctor", "") or "").strip().lower()
                if key and key not in existing_keys:
                    existing.append(alt)
                    existing_keys.add(key)
            state["available_doctors"] = existing
            return slot_with_alternatives_message(
                state["doctor"], state.get("doctor_ar", ""),
                slot_info, alternatives, target_for_alternatives, lang,
            )

    fallback_prefix = _fallback_notice(explicit_requested, used_date, lang)
    return fallback_prefix + slot_question(
        state["doctor"], state.get("doctor_ar", ""), slot_info, lang,
    )


def _other_doctors_with_availability(state: dict) -> list:
    """Return up to 3 doctors from `available_doctors` (other than the
    currently selected one) that have a known earliest slot. Used as a
    no-dead-end fallback when the selected doctor returns zero slots.
    """
    selected = (state.get("doctor", "") or "").strip().lower()
    available = state.get("available_doctors") or []
    return [
        d for d in available
        if (d.get("Doctor", "") or "").strip().lower() != selected
        and d.get("Nearest_Time")
    ][:3]


def _find_same_day_alternatives(
    state: dict, target_date: str, exclude_doctor: str = "",
) -> list:
    """Return up to 3 OTHER doctors in the same specialty with slots on
    `target_date`. Prefers the already-loaded `available_doctors` list when
    its dates match; otherwise queries the DB fresh for the specialty + date.
    """
    if not target_date:
        return []

    excl = (exclude_doctor or "").strip().lower()

    def _doc_date_iso(doc: dict) -> str:
        nd = doc.get("Nearest_Date")
        if hasattr(nd, "strftime"):
            try:
                return nd.date().strftime("%Y-%m-%d") if hasattr(nd, "hour") else nd.strftime("%Y-%m-%d")
            except (AttributeError, ValueError):
                return ""
        if isinstance(nd, str) and len(nd) >= 10:
            return nd[:10]
        return ""

    target_iso = (
        target_date if isinstance(target_date, str)
        else target_date.strftime("%Y-%m-%d")
    )

    # Fast path: reuse the already-loaded doctor list when its earliest dates
    # match the target. fetch_doctors filters to a single earliest date, so if
    # the list is for the requested day we get same-day candidates for free.
    available = state.get("available_doctors") or []
    matched = [
        d for d in available
        if _doc_date_iso(d) == target_iso
        and d.get("Nearest_Time")
        and (d.get("Doctor", "") or "").strip().lower() != excl
    ]
    if matched:
        return matched[:3]

    # Slow path: query the DB for the specialty constrained to target_date.
    specialty = state.get("speciality") or state.get("speciality_ar") or ""
    if not specialty:
        return []

    try:
        from db.database import query_availability, aggregate_doctor_slots
        from services.doctor_price import enrich_doctors_with_prices

        rows = query_availability(report_date=target_iso, specialty_en=specialty)
        if not rows:
            rows = query_availability(report_date=target_iso, specialty_ar=specialty)
        if not rows:
            return []

        doctors = aggregate_doctor_slots(rows)
        doctors = [
            d for d in doctors
            if d.get("Nearest_Time")
            and _doc_date_iso(d) == target_iso
            and (d.get("Doctor", "") or "").strip().lower() != excl
        ]
        if not doctors:
            return []
        enrich_doctors_with_prices(doctors)
        return doctors[:3]
    except Exception:
        return []


def _fallback_notice(requested: str, used_date: str, lang: str) -> str:
    """Return a leading sentence when the slot fetch silently shifted the day.

    Without this, the patient asks "متاح غدا؟" and the bot replies with
    "يوم الخميس، ٣٠ أبريل" — looking like a hallucination — when really
    tomorrow had no openings and the fallback jumped ahead.
    """
    if not requested or not used_date or requested == used_date:
        return ""
    try:
        req_dt = datetime.strptime(requested, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return ""
    req_label = format_date(req_dt, lang)
    if lang == "ar":
        return f"للأسف ما في مواعيد متاحة {req_label}. "
    en_label = req_label.lower() if req_label in ("Today", "Tomorrow") else f"on {req_label}"
    return f"No slots available {en_label}. "


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


# ── Single-doctor auto-selection + response handling ─────────────────────────

def auto_select_if_single_doctor(state: dict, doctors: list) -> bool:
    """When only one doctor is in the list, auto-select them and mark the
    state so the next turn handles the "all slots vs earliest" choice instead
    of treating the user's reply as a doctor pick.

    Returns True if auto-selection happened (caller may want to use a different
    follow-up flow), False otherwise.
    """
    if len(doctors) != 1:
        return False
    only = doctors[0]
    state["doctor"] = only.get("Doctor", "") or ""
    state["doctor_ar"] = only.get("DoctorAR", "") or ""
    if only.get("WalkInPrice") is not None:
        state["walk_in_price"] = only.get("WalkInPrice")
    state["_single_doctor_choice_pending"] = True
    return True


# Keywords that indicate the patient wants to see ALL available slots
# (vs. accepting the earliest). Normalized form (after normalize_ar).
_SHOW_ALL_KEYWORDS = (
    "كل", "جميع", "متاحه", "متاح", "المواعيد", "مواعيد",
    "اعرض", "اعرضي", "اعرضها", "ورني", "وريني", "قائمه", "قائمة",
    "all", "available", "show", "list", "see them", "see all", "options",
)

# Keywords that indicate the patient wants the EARLIEST slot — these win
# even if "available"-type words are also present (e.g. "أقرب موعد متاح").
_EARLIEST_KEYWORDS = (
    "اقرب", "اول", "earliest", "first", "soonest", "nearest",
)


def _wants_all_slots(text: str) -> bool:
    """Detect 'show me all available slots' intent for the single-doctor case."""
    if not text:
        return False
    t = normalize_ar(text).lower()
    if any(w in t for w in _EARLIEST_KEYWORDS):
        return False
    return any(kw in t for kw in _SHOW_ALL_KEYWORDS)


def _show_all_slots_for_doctor(state: dict, lang: str) -> str:
    """Fetch slots for the (already-selected) doctor and render the full list.

    Mirrors `_handle_slot_fetch`'s setup so the next turn can confirm any
    chosen slot via the existing slot_selection flow, but returns the
    `more_slots_message` view instead of the single-slot proposal.
    """
    from services.slot_handler import get_all_slots
    from services.formatter import more_slots_message

    explicit_requested = state.get("requested_date")
    date_to_use = explicit_requested or state.get("date")
    slots, used_date = fetch_slots(
        state["doctor"], state.get("doctor_ar"), date_to_use,
    )

    if not slots:
        slots = _extract_slots_from_availability(state)
        if slots:
            used_date = date_to_use or state.get("date", "")

    state["date"] = used_date
    state["available_slots"] = slots

    if not slots:
        # No slots → fall back to the same dead-end-avoidance _handle_slot_fetch
        # uses (alternative doctors, "try another day").
        others = _other_doctors_with_availability(state)
        if others:
            from services.formatter import no_slots_with_alternatives_message
            state["_alternatives_offered"] = True
            return no_slots_with_alternatives_message(
                state.get("doctor", ""), state.get("doctor_ar", ""),
                others, lang,
            )
        date_label = format_date(used_date, lang)
        if lang == "ar":
            return f"للأسف ما في مواعيد متاحة في {date_label}. تبغى أجرب يوم ثاني؟"
        return f"No slots available on {date_label}. Would you like to try another date?"

    state["booking_stage"] = "slot_selection"
    # Mark the earliest slot as a proposal so a later "تمام" / "yes" can confirm
    # it cleanly through the existing acceptance flow.
    slot_info = get_initial_slots(slots)
    first_time = slot_info["first_time"]
    state["_proposed_slot"] = {
        "time_24": first_time.strftime("%H:%M"),
        "time_display": format_time(first_time, lang),
        "date": slot_info["first_date"].strftime("%Y-%m-%d"),
        "date_display": format_date(slot_info["first_date"], lang),
    }
    state["_proposal_shown_this_turn"] = True

    all_slots = get_all_slots(slots)
    return more_slots_message(
        state["doctor"], state.get("doctor_ar", ""), all_slots, lang,
    )
