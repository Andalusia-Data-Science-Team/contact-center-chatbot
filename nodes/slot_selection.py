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

    if stage in ("cancelled", "callback_pending"):
        return state

    # Doctor was just picked by bare digit this turn (e.g. "2" at doctor_list).
    # The LLM and the slot safety net both treat that digit as a time, which
    # would overwrite the slot_question reply with "ما فهمت الوقت". Skip the
    # whole node for this turn — the patient hasn't expressed any time intent.
    if state.pop("_skip_time_preference_this_turn", False):
        return state

    # ── Safety net: "who's available today" → re-show the doctor list ──
    # When the patient has a doctor selected but explicitly asks WHO is
    # available, that's a request to reset the choice and see all options
    # again — not a time/slot intent. Without this, the slot-selection logic
    # keeps proposing the same fallback time and the conversation loops.
    if state.get("doctor") and stage in ("slot_selection", "patient_info"):
        from nodes._helpers import get_last_user_message
        msg = (get_last_user_message(state) or "").strip()
        if _is_who_is_available_request(msg):
            return _reshow_doctor_list_for_date(state, msg, lang)

    # Must be in an active booking with a doctor to do anything here
    if not state.get("doctor"):
        return state

    # ── Auto-confirm proposed slot when user signals proceed ──
    # If a slot was proposed last turn and the user's reply is a phone number,
    # a cash/insurance answer, or an acceptance token — lock it in now instead
    # of looping on "does this time work?".
    if (
        stage in ("slot_selection", "patient_info")
        and state.get("_proposed_slot")
        and not state.get("selected_slot")
        and not state.get("_proposal_shown_this_turn")
    ):
        from nodes._helpers import get_last_user_message
        last_msg = get_last_user_message(state) or ""
        if _message_accepts_proposal(last_msg):
            proposed = state["_proposed_slot"]
            state["selected_slot"] = proposed["time_24"]
            state["date"] = proposed["date"]
            state["_proposed_slot"] = None
            if is_patient_info_complete(state):
                state["booking_stage"] = "complete"
                state["last_bot_message"] = confirmation_message(state, lang)
            else:
                state["booking_stage"] = "patient_info"
                # Only replace the LLM reply if it's empty — the LLM may have
                # already answered a bundled question (e.g. cash-or-insurance).
                if not (state.get("last_bot_message") or "").strip():
                    state["last_bot_message"] = slot_confirmed_message(
                        state["doctor"], state.get("doctor_ar", ""),
                        proposed["date_display"], proposed["time_display"], lang,
                        is_fallback=False,
                    )
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

        # If the same message also requested a different date ("tomorrow evening")
        # refetch slots for that date FIRST so the filter applies to the right
        # day. Otherwise the patient sees today's evening slots when they meant
        # tomorrow's.
        new_date = state.get("requested_date")
        if new_date and new_date != state.get("date") and state.get("doctor"):
            state["selected_slot"] = None
            state["_proposed_slot"] = None
            _handle_slot_fetch(state, lang, preferred_date=new_date)
            # _handle_slot_fetch repopulates state["available_slots"] / state["date"];
            # fall through to filter the new list.

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
            # The initial "nearest slot" proposal no longer reflects what the
            # patient is looking at — clear it so a later acceptance doesn't
            # silently lock the stale slot.
            state["_proposed_slot"] = None
            state["last_bot_message"] = more_slots_message(
                state["doctor"], state.get("doctor_ar", ""), all_slots, lang,
                filter_label=filter_label,
            )
        return state

    # ── Patient asks for a different date → re-fetch slots ──
    # Use the RESOLVED date from state (apply_llm_updates resolved "بكرا"→ISO)
    # rather than the raw LLM value in updates — passing "بكرا"/"today" through
    # query_doctor_slots_with_fallback fails strptime and falls back to today,
    # silently booking the wrong day.
    # Skip the refetch when the resolved date matches state.date — the LLM
    # often re-emits requested_date redundantly even when no date change was
    # asked for, and refetching would just churn through the same DB query.
    if (stage in ("slot_selection", "patient_info")
            and updates.get("requested_date")
            and state.get("doctor")):
        resolved_date = state.get("requested_date") or updates["requested_date"]
        if resolved_date and resolved_date != state.get("date"):
            # Mid-flow date change: an earlier slot was already auto-picked
            # (often for today) but the patient now says "يوم الخميس".
            # Clear the auto-pick so the booking lands on the right day.
            state["selected_slot"] = None
            state["_proposed_slot"] = None
            state["booking_stage"] = "slot_selection"
            state["last_bot_message"] = _handle_slot_fetch(
                state, lang, preferred_date=resolved_date,
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
        # Defer to slot_confirmed_message so this confirm-via-acceptance path
        # stays in lockstep with the time-preference confirm path.
        state["last_bot_message"] = slot_confirmed_message(
            doc, doc_ar, date_str, time_str, lang, is_fallback=False,
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
            "ما فهمت الوقت، ممكن توضح أكثر؟"
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
        state["_proposal_shown_this_turn"] = True

        doc = state.get("doctor", "")
        doc_ar = state.get("doctor_ar", "")
        doc_name = (doc_ar or doc) if lang == "ar" else doc
        prefix = "د. " if lang == "ar" else "Dr. "
        target_display = format_time(target, lang)

        # is_fallback=True means no slot exists at/after the target → the
        # offered slot is BEFORE what the patient asked for. Don't call that
        # the "nearest" slot — it misleads the patient.
        if nearest.get("is_fallback"):
            if lang == "ar":
                return (
                    f"للأسف ما في مواعيد متاحة بعد {target_display} مع {prefix}{doc_name}.\n\n"
                    f"أقرب موعد قبلها هو **{time_str}**. تناسبك؟ أو تبغى يوم ثاني؟"
                )
            return (
                f"There are no slots available after {target_display} with {prefix}{doc_name}.\n\n"
                f"The closest earlier slot is **{time_str}**. Does that work, or would you like a different day?"
            )

        if lang == "ar":
            return (
                f"أقرب موعد بعد {target_display} مع {prefix}{doc_name} هو **{time_str}**.\n\n"
                f"يناسبك؟ أو قولي وقت ثاني."
            )
        return (
            f"The next slot after {target_display} with {prefix}{doc_name} is **{time_str}**.\n\n"
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
    from nodes.doctor_selection import _fallback_notice

    # Mirrors doctor_selection._handle_slot_fetch: honour requested_date so a
    # mid-flow date change ("ابغى يوم بكرا") actually refetches for that date.
    explicit_requested = preferred_date or state.get("requested_date")
    date_to_use = explicit_requested or state.get("date")
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
        # Before falling back to "try another date", surface other loaded
        # doctors in the same specialty so the patient has a concrete next
        # step. Mirrors the same dead-end fallback in doctor_selection_node.
        from nodes.doctor_selection import _other_doctors_with_availability
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
            return f"للأسف ما في مواعيد في {date_label}. تبغى أجرب يوم ثاني؟"
        return f"No slots available on {date_label}. Would you like to try another date?"

    slot_info = get_initial_slots(slots)
    state["booking_stage"] = "slot_selection"

    # Mark the displayed slot as a proposal so a follow-up "نعم" / "yes" can
    # confirm it through the auto-accept path in slot_selection_node — without
    # this, the patient sees "هل يناسبك؟" but acceptance has nothing to lock.
    # Mirrors the proposal setup in doctor_selection._handle_slot_fetch.
    first_time = slot_info["first_time"]
    state["_proposed_slot"] = {
        "time_24": first_time.strftime("%H:%M"),
        "time_display": format_time(first_time, lang),
        "date": slot_info["first_date"].strftime("%Y-%m-%d"),
        "date_display": format_date(slot_info["first_date"], lang),
    }
    state["_proposal_shown_this_turn"] = True

    # When the date the patient asked for has no slots and the search rolled
    # forward, surface other doctors with same-day availability — same logic
    # as doctor_selection._handle_slot_fetch. Without this, a mid-flow "اليوم"
    # request just keeps proposing tomorrow with no other path forward.
    state["_alternatives_offered"] = False
    target_for_alternatives = date_to_use
    if (
        target_for_alternatives
        and used_date
        and target_for_alternatives != used_date
    ):
        from nodes.doctor_selection import _find_same_day_alternatives
        alternatives = _find_same_day_alternatives(
            state,
            target_date=target_for_alternatives,
            exclude_doctor=state.get("doctor", ""),
        )
        if alternatives:
            from services.formatter import slot_with_alternatives_message
            state["_alternatives_offered"] = True
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


# ── Re-list doctors when patient explicitly asks who's available ────────────

# Phrases that ask for the doctor list ("who is available?" / "any other
# doctors?") rather than a slot for the current doctor. These need to bypass
# the time-extraction logic and re-show the list.
_WHO_AVAILABLE_PATTERNS = (
    # Arabic: "مين متاح", "مين عنده", "ايش الأطباء", "ايش الدكاتره", "اطباء ثانيين"
    r"مين\s*(?:متاح|عنده|موجود|في|فيه)",
    r"اي(?:ش)?\s*(?:الاطباء|الدكاتر|الدكاتره|الدكاترة|الاطبا|دكاتر)",
    r"(?:اطباء|دكاتره|دكاترة)\s*(?:تاني|ثاني|ثانيين|متاحين|متوفرين)",
    r"دكتور\s*(?:تاني|ثاني|آخر|ثانى)",
    r"(?:احد|واحد)\s*ثاني",
    # English
    r"\bwho(?:'?s|\s+is)\s+available\b",
    r"\bany\s+(?:other|another)\s+doctor",
    r"\bother\s+doctors?\b",
    r"\bdifferent\s+doctor\b",
    r"\bshow\s+(?:me\s+)?(?:the\s+)?(?:other|all|available)\s+doctors?",
)


def _is_who_is_available_request(text: str) -> bool:
    if not text:
        return False
    from utils.language import normalize_ar
    t = normalize_ar(text).lower()
    return any(re.search(p, t, re.IGNORECASE) for p in _WHO_AVAILABLE_PATTERNS)


def _reshow_doctor_list_for_date(state: dict, raw_msg: str, lang: str):
    """Reset the current doctor pick and show the doctor list — honouring any
    date the patient mentioned in the same message ('مين متاح اليوم' → today,
    'مين متاح بكره' → tomorrow, otherwise keep state['date'])."""
    from utils.datetime_fmt import resolve_relative_date, _ISO_DATE_RE
    from services.doctor_selector import fetch_doctors
    from services.formatter import (
        doctor_list_message, no_doctors_on_date_message,
    )

    specialty = state.get("speciality") or state.get("speciality_ar") or ""

    # Pull the date out of the same message if any. resolve_relative_date
    # falls back to the input as-is when no relative date keyword is found.
    resolved = resolve_relative_date(raw_msg)
    target_date = resolved if _ISO_DATE_RE.match(resolved) else state.get("date")

    # Clear doctor + slot selection so the existing doctor_list flow can take
    # over from the next user message ("1" / "Dr. X").
    state["doctor"] = None
    state["doctor_ar"] = None
    state["available_slots"] = []
    state["selected_slot"] = None
    state["_proposed_slot"] = None
    state["_proposal_shown_this_turn"] = False
    state["_alternatives_offered"] = False
    state["walk_in_price"] = None
    state["_walk_in_price_doctor"] = None

    if not specialty:
        # Without a specialty (Path C: doctor was looked up by name directly)
        # we have nothing to re-list. Tell the patient and keep the doctor
        # cleared so they can re-state who they want.
        if lang == "ar":
            state["last_bot_message"] = (
                "ممكن تقولي التخصص اللي تبغاه عشان أعرض لك الأطباء المتاحين؟"
            )
        else:
            state["last_bot_message"] = (
                "Could you tell me the specialty so I can show you the available doctors?"
            )
        state["booking_stage"] = "complaint"
        return state

    # Strict on the requested date: the patient is explicitly asking who is
    # available on that day — don't silently roll forward.
    strict = bool(target_date and _ISO_DATE_RE.match(target_date or ""))
    doctors, used_date = fetch_doctors(
        specialty, lang, target_date, strict_date=strict,
    )
    state["date"] = used_date
    state["available_doctors"] = doctors
    state["booking_stage"] = "doctor_list"
    if doctors:
        state["last_bot_message"] = doctor_list_message(
            doctors, specialty, lang, used_date,
        )
        from nodes.doctor_selection import auto_select_if_single_doctor
        auto_select_if_single_doctor(state, doctors)
    elif strict:
        state["last_bot_message"] = no_doctors_on_date_message(
            specialty, target_date, lang,
        )
    else:
        if lang == "ar":
            state["last_bot_message"] = "للأسف ما في أطباء متاحين حالياً. تبغى تجرب يوم ثاني؟"
        else:
            state["last_bot_message"] = "No doctors available right now. Want to try another date?"
    return state


# ── Safety net: catch slot requests the LLM missed ──────────────────────────

_TIME_PERIOD_KEYWORDS = {
    "tonight", "evening", "morning", "afternoon", "night", "this evening",
    "الليلة", "المساء", "مساء", "الصبح", "بعد الظهر", "العصر", "بعد العصر",
    "الليل", "صباح",
    # Saudi prayer-time references (very common as rough time anchors)
    "المغرب", "بعد المغرب", "الفجر", "الظهر", "بعد الظهر", "العشاء", "بعد العشاء",
}

# "Today" / "tomorrow" / weekday names — handled as requested_date, not time filter
_RELATIVE_DATE_KEYWORDS = {
    "today", "tomorrow", "اليوم", "بكرا", "بكره", "بكرة", "غدا", "غداً",
    # Weekday names — patient picks a future day explicitly
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "الإثنين", "الاثنين", "الإتنين", "الاتنين",
    "الثلاثاء", "الثلاثا",
    "الأربعاء", "الاربعاء", "الأربعا", "الاربعا",
    "الخميس", "الجمعة", "الجمعه", "السبت", "الأحد", "الاحد",
}

_TIME_INDICATORS = {
    "after", "at", "around", "before", "بعد", "قبل", "حوالي", "الساعة", "عند",
}

import re
# Accept both ASCII and Arabic-Indic digits (٠-٩ are U+0660..U+0669)
_DIGIT_CLASS = r'[\d\u0660-\u0669]'

from utils.language import normalize_ar as _normalize_ar_for_proposal

# Signals the patient is moving forward (phone, insurance answer, acceptance)
# while a proposed slot is outstanding. Used to auto-confirm the proposed slot
# so the flow doesn't stall at "do you want this time?".
_PROPOSAL_PROCEED_TOKENS = (
    "yes", "yeah", "yep", "ok", "okay", "sure", "fine", "works", "perfect",
    "cash", "insurance", "insured",
    "نعم", "ايوه", "أيوه", "تمام", "ماشي", "مناسب", "موافق", "يناسبني",
    "كاش", "نقدي", "تامين", "تأمين",
)
_PROPOSAL_REJECT_TOKENS = ("no", "not", "don't", "dont", "لا", "مش", "مو", "ما")


def _message_accepts_proposal(text: str) -> bool:
    """True if the message should lock in an outstanding `_proposed_slot`.

    Accepts on: explicit 'yes' tokens, phone numbers, or short cash/insurance
    answers. Rejects on negations or short digits (new time request).
    """
    if not text:
        return False
    t = _normalize_ar_for_proposal(text).lower()
    for neg in _PROPOSAL_REJECT_TOKENS:
        if re.search(rf"(?<![\w]){re.escape(neg)}(?![\w])", t):
            return False
    if re.search(r"\d{7,}", t):
        return True
    if re.search(r"(?<!\d)\d{1,2}(?!\d)", t):
        return False
    for tok in _PROPOSAL_PROCEED_TOKENS:
        if re.search(rf"(?<![\w]){re.escape(tok)}(?![\w])", t):
            return True
    return False

_TIME_PATTERN = re.compile(
    rf'\b(?:at|after|around|before|بعد|قبل|حوالي|الساعة)\s*{_DIGIT_CLASS}{{1,2}}',
    re.IGNORECASE,
)
_BARE_TIME_PATTERN = re.compile(
    rf'\b{_DIGIT_CLASS}{{1,2}}(?::{_DIGIT_CLASS}{{2}})?\s*(?:am|pm|AM|PM|ص|م|صباحا|صباحاً|مساء|مساءً)\b'
)


def _slot_safety_net(state: dict, updates: dict) -> dict:
    """
    If the LLM failed to set slot-related state_updates but the user's message
    clearly contains a time request, fix the updates so the slot_selection logic fires.
    """
    from nodes._helpers import get_last_user_message
    from utils.language import normalize_ar, to_ascii_digits

    # Skip entirely if conversation_node already handled the turn as a price
    # inquiry — re-extracting "8 مساء" here would overwrite that reply.
    if updates.get("_price_handled"):
        return updates

    last_msg = get_last_user_message(state)
    if not last_msg:
        return updates

    # Normalize for keyword matching (strip hamza, ta-marbuta, Arabic digits → ASCII)
    msg_norm = normalize_ar(last_msg)
    # Also keep a digit-normalized copy for regex (preserves Arabic letters)
    msg_digits_ascii = to_ascii_digits(last_msg).lower().strip()

    # Relative-date detection runs BEFORE the wants_more_slots/preferred_time
    # guard. Combined messages like "متاح اليوم؟" / "show today's slots" set
    # wants_more_slots in the LLM output but often miss requested_date — without
    # this, the date never switches and the bot displays the prior day's slots
    # while later confirming the booking for the wrong date.
    # Pass the original user message to resolve_relative_date so it can match
    # weekday names and longer phrases ("يوم الخميس", "بكره ان شاء الله").
    if not updates.get("requested_date"):
        for kw in sorted(_RELATIVE_DATE_KEYWORDS, key=len, reverse=True):
            if kw in msg_norm:
                from utils.datetime_fmt import resolve_relative_date, _ISO_DATE_RE
                resolved = resolve_relative_date(last_msg)
                if not _ISO_DATE_RE.match(resolved):
                    # Fallback for safety — shouldn't happen since the keyword is
                    # in _RELATIVE_DATE_KEYWORDS and resolve_relative_date knows
                    # all of them, but keep flow safe rather than passing raw text.
                    resolved = resolve_relative_date(
                        "today" if kw in ("today", "اليوم") else "tomorrow"
                    )
                updates["requested_date"] = resolved
                # Mirror into state so _handle_slot_fetch (which prefers the
                # resolved state value) sees it on this same turn.
                state["requested_date"] = resolved
                # Only clear the prior reply if the date actually changed —
                # otherwise we'd blank out a correct slot_question (already shown
                # for this date) and fall through to the generic "ما فهمت الوقت"
                # fallback when the downstream no-op guard skips the refetch.
                if resolved != state.get("date"):
                    state["last_bot_message"] = ""
                break

    # Beyond date detection, intervene only when LLM didn't already set the
    # other slot flags — otherwise we'd overwrite the LLM's own classification.
    if updates.get("wants_more_slots") or updates.get("preferred_time") or updates.get("slots_filter"):
        return updates

    # Specific time wins over period-only filter. "8 مساء" / "7 pm" is a
    # specific slot request, not a "show me the evening list" request — check
    # this BEFORE the bare period-keyword branch below.
    if _BARE_TIME_PATTERN.search(msg_digits_ascii):
        m = _BARE_TIME_PATTERN.search(msg_digits_ascii)
        updates["preferred_time"] = m.group(0).strip()
        state["last_bot_message"] = ""
        return updates

    # Check for time period keywords → wants_more_slots + slots_filter
    for period in sorted(_TIME_PERIOD_KEYWORDS, key=len, reverse=True):
        if period in msg_norm or period in last_msg.lower():
            updates["wants_more_slots"] = True
            updates["slots_filter"] = period
            state["last_bot_message"] = ""  # Clear LLM's reply
            return updates

    # Check for "show all" / "show available" type requests
    show_keywords = {"show all", "show slot", "show available", "all slot", "what else",
                     "list", "available", "عرض", "المتاح", "كل المواعيد"}
    for kw in show_keywords:
        if kw in msg_norm:
            updates["wants_more_slots"] = True
            state["last_bot_message"] = ""
            return updates

    # Check for specific time patterns ("after 7", "at 8", "8 PM", "٨ مساء")
    if _TIME_PATTERN.search(msg_digits_ascii) or _BARE_TIME_PATTERN.search(msg_digits_ascii):
        match = _TIME_PATTERN.search(msg_digits_ascii) or _BARE_TIME_PATTERN.search(msg_digits_ascii)
        if match:
            updates["preferred_time"] = match.group(0).strip()
            state["last_bot_message"] = ""
            return updates

    # Check for bare numbers that look like times (e.g. "7", "8", "٨")
    # Only if the message is very short (1-3 words) to avoid false positives
    words = msg_digits_ascii.split()
    if len(words) <= 3:
        for word in words:
            if re.match(r'^\d{1,2}(:\d{2})?$', word):
                updates["preferred_time"] = word
                state["last_bot_message"] = ""
                return updates

    return updates
