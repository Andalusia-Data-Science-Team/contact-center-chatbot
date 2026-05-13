# nodes/routing.py
"""
Handles the 'routing' booking stage with 3 intelligent paths:

PATH 1 — Patient describes symptoms (e.g. "pain in my hand"):
  → Ask ONE clarifying question to narrow down the specialty
  → Route to specialty
  → Confirm specialty with patient ("Sounds like you need Orthopedics, want to see doctors for today?")
  → Show doctor list

PATH 2 — Patient names a specialty (e.g. "I need a dermatologist"):
  → Skip triage, route directly
  → Confirm specialty ("Got it, Dermatology! Want to see available doctors for today?")
  → Show doctor list

PATH 3 — Patient names a specific doctor (e.g. "booking with Dr. Ahmad"):
  → Handled by conversation_node setting doctor_fuzzy_input → doctor_selection_node
  → This node does nothing

The specialty confirmation step makes the flow feel more natural —
the bot doesn't just dump a doctor list out of nowhere.
"""
import re
from state import BookingState
from nodes._helpers import get_last_user_message, is_acceptance
from llm.client import call_llm
from services.router import route_specialty
from services.doctor_selector import fetch_doctors
from services.formatter import (
    doctor_list_message, no_doctors_message, no_doctors_on_date_message,
)
from prompts.triage import TRIAGE_PROMPT_EN, TRIAGE_PROMPT_AR
from utils.datetime_fmt import format_date, resolve_relative_date
from utils.language import normalize_ar
from config.constants import SPECIALTY_EN_TO_AR


# Adult specialty to fall back to when a pediatric routing returns zero
# doctors. Names on the right MUST match the hospital DB's SpecialtyEnName
# column. If a pediatric specialty in `config/constants.py` doesn't actually
# exist in the DB (verify via `scripts/verify_pediatric_specialties.py`), this
# table prevents the patient from hitting a dead-end — they still get matched
# with an adult doctor in the closest body system.
_ADULT_FALLBACK_FOR_PED = {
    "Pediatric Cardiology":           "Cardiology",
    "Pediatric Endocrinology":        "Endocrinology",
    "Pediatric Gastroenterology":     "Gastroenterology",
    "Pediatric hematology":           "Hematology",            # DB casing
    "Pediatric Nephrology":           "Nephrology",
    "Pediatric Neurology":            "Neurology",
    "Pediatric Orthopedics":          "Orthopedics",
    "Pediatric Rheumatology":         "Rheumatology",
    "Pediatric surgery":              "General Surgery",       # DB casing
    "Pediatric Allergy & Immunology": "Allergy & Immunology",
    "Pediatric medicine":             "Internal Medicine",
    "Pedodontic":                     "Dental Services",
    "NICU":                           "General Pediatrics",
}


# Tokens that signal the patient is moving forward (giving a date/time) while
# implicitly accepting the specialty. Without this, "بكرا" after the specialty
# confirmation question is misread as "no, different specialty" and loops.
_DATE_TIME_HINTS = (
    "today", "tomorrow", "tonight", "morning", "afternoon", "evening", "night",
    "am", "pm",
    "اليوم", "بكرا", "بكره", "غدا", "غدًا", "الليله", "الليلة",
    "الصبح", "الصباح", "صباح", "الظهر", "بعد الظهر", "العصر", "بعد العصر",
    "المغرب", "المسا", "المساء", "مساء", "الليل",
)


def _looks_like_date_time_hint(text: str) -> bool:
    """True if the message reads like a date/time preference rather than a
    different specialty or a rejection. Used to treat 'بكرا' as implicit
    acceptance of the proposed specialty."""
    if not text:
        return False
    t = normalize_ar(text).lower()
    # Presence of digits = explicit time (e.g. '٨ مساء', '8 pm')
    if re.search(r"\d", t):
        return True
    for tok in _DATE_TIME_HINTS:
        if tok in t:
            return True
    return False


def routing_node(state: BookingState) -> BookingState:
    """
    Runs when booking_stage == 'routing'.
    Implements the 3-path routing logic.
    """
    lang = state.get("language", "en")

    if state.get("booking_stage") in ("cancelled", "callback_pending"):
        return state
    if state.get("booking_stage") != "routing":
        return state
    if not state.get("complaint_text"):
        return state

    # CRITICAL: Clear any reply the conversation node may have generated.
    # When stage is "routing", this node owns the reply entirely.
    state["last_bot_message"] = ""

    # If specialty already determined, handle confirmation flow
    if state.get("speciality") or state.get("speciality_ar"):
        return _handle_specialty_confirmation(state, lang)

    # Determine if this is a symptom-based or specialty-based complaint
    complaint = state["complaint_text"]
    is_direct_specialty = _is_direct_specialty_request(complaint)

    if is_direct_specialty:
        # PATH 2: Direct specialty — skip triage, route immediately
        return _route_and_confirm(state, lang)
    else:
        # PATH 1: Symptoms — ask clarifying question first
        return _handle_symptom_routing(state, lang)


def _handle_symptom_routing(state: BookingState, lang: str) -> BookingState:
    """PATH 1: Patient described symptoms → ask triage question → route."""

    # Step 1: Ask ONE clarifying question (first time)
    if not state.get("triage_question_asked"):
        question = _get_triage_question(
            state["complaint_text"], state.get("patient_age") or 30, lang
        )
        state["triage_question_asked"] = True
        # Add patient name for warmth
        patient = state.get("patient_name", "")
        if lang == "ar":
            name_part = f" أ/ {patient}" if patient else ""
            state["last_bot_message"] = f"{question}"
        else:
            state["last_bot_message"] = f"{question}"
        return state

    # Step 2: Patient answered the triage question — capture and route
    messages = state.get("messages", [])
    user_msgs = [m for m in messages if m["role"] == "user"]
    if user_msgs and not state.get("routing_clarification"):
        state["routing_clarification"] = user_msgs[-1]["content"]

    return _route_and_confirm(state, lang)


def _route_and_confirm(state: BookingState, lang: str) -> BookingState:
    """Route to specialty and ask patient to confirm before showing doctors."""

    result = route_specialty(
        complaint=state["complaint_text"],
        age=state.get("patient_age") or 30,
        lang=lang,
        clarification=state.get("routing_clarification") or "",
    )

    specialty = result["specialty"]
    if not specialty:
        if lang == "ar":
            state["last_bot_message"] = "ممكن توضحلي أكثر وش المشكلة بالضبط عشان أقدر أساعدك؟"
        else:
            state["last_bot_message"] = "Could you describe the issue in a bit more detail so I can match you with the right doctor?"
        return state

    # Store specialty — always EN since routing now always returns EN names
    state["speciality"] = specialty
    state["speciality_confidence"] = result["confidence"]

    # Ask patient to confirm the specialty before showing doctors.
    # Only mention a date when the patient already said one — without an
    # explicit ask, we don't know yet what's available, so promising "today"
    # would be a lie when only later days have openings.
    patient = state.get("patient_name", "")
    explicit_date = state.get("requested_date") or state.get("date")
    date_label = format_date(explicit_date, lang) if explicit_date else ""

    # Show specialty in patient's language (SPECIALTY_EN_TO_AR is already
    # imported at module top).
    spec_display = SPECIALTY_EN_TO_AR.get(specialty, specialty) if lang == "ar" else specialty

    if lang == "ar":
        name_part = f" أ/ {patient}" if patient else ""
        date_suffix = f" {date_label}" if date_label else ""
        state["last_bot_message"] = (
            f"تمام{name_part}، حضرتك محتاج تخصص **{spec_display}**. "
            f"تبغى أعرض لك الأطباء المتاحين{date_suffix}؟"
        )
    else:
        date_suffix = f" for {date_label}" if date_label else ""
        state["last_bot_message"] = (
            f"Based on what you've described, you'll need **{spec_display}**. "
            f"Would you like to see the available doctors{date_suffix}?"
        )
    return state


def _handle_specialty_confirmation(state: BookingState, lang: str) -> BookingState:
    """Patient has a specialty set — check if they confirmed it."""

    if state.get("specialty_confirmed"):
        # Already confirmed and doctors should have been loaded.
        # If we're still here, something went wrong — move to doctor_list to unstick.
        if state.get("available_doctors"):
            state["booking_stage"] = "doctor_list"
        else:
            # No doctors were loaded — reset and let patient try again
            state["speciality"] = None
            state["speciality_ar"] = None
            state["specialty_confirmed"] = False
            state["booking_stage"] = "complaint"
            patient = state.get("patient_name", "")
            if lang == "ar":
                name_part = f" أ/ {patient}" if patient else ""
                state["last_bot_message"] = f"عذراً{name_part}، ما لقينا أطباء متاحين حالياً. ممكن توضحلي أكثر وش تحتاج؟"
            else:
                state["last_bot_message"] = f"Sorry {patient}, no doctors are currently available for that specialty. Could you describe what you need so I can try a different match?"
        return state

    # Check if last user message is an acceptance
    last_msg = get_last_user_message(state)
    if not last_msg:
        return state

    # Treat a bare date/time hint ("بكرا", "اليوم", "٨ مساء") as implicit
    # acceptance — the patient is moving forward and telling us WHEN, not
    # rejecting the specialty. Also capture it as requested_date / preferred_time
    # so the downstream slot selection uses it.
    implicit_accept = False
    if not is_acceptance(last_msg) and _looks_like_date_time_hint(last_msg):
        implicit_accept = True
        resolved = resolve_relative_date(last_msg)
        if resolved and re.match(r"^\d{4}-\d{2}-\d{2}$", resolved):
            state["requested_date"] = resolved
        else:
            state["preferred_time"] = last_msg

    if is_acceptance(last_msg) or implicit_accept:
        # Patient confirmed — fetch doctors and show list
        state["specialty_confirmed"] = True
        specialty = state.get("speciality") or state.get("speciality_ar") or ""

        # Honour requested_date if the patient said "بكرا" / "يوم الخميس" before
        # confirming the specialty. Falls back to today's context otherwise.
        preferred = state.get("requested_date") or state.get("date")
        # When the patient explicitly asked for a date, constrain the search
        # to that exact day — otherwise a "no eye doctors today" situation
        # silently shifts to "eye doctors tomorrow" and the patient feels
        # ignored. Without an explicit date, keep the forward-search fallback.
        strict = bool(state.get("requested_date"))
        doctors, used_date = fetch_doctors(specialty, lang, preferred, strict_date=strict)

        # Defensive fallback for pediatric subspecialties whose name in the
        # bot's constants may not exactly match the hospital DB's
        # SpecialtyEnName column. If a pediatric routing finds zero doctors
        # AND we have no rows at all (not just none on the requested day),
        # retry with the adult counterpart so the patient still gets matched
        # with a real doctor instead of "no one is available".
        # Only applies in non-strict mode — in strict-date mode the empty
        # result is genuine "no doctors on that day".
        if not doctors and not strict:
            adult = _ADULT_FALLBACK_FOR_PED.get(specialty)
            if adult:
                doctors, used_date = fetch_doctors(adult, lang, preferred, strict_date=False)
                if doctors:
                    specialty = adult
                    state["speciality"] = adult

        state["date"] = used_date
        state["available_doctors"] = doctors

        if doctors:
            if len(doctors) == 1:
                # Skip the "which doctor?" CTA when there's only one option —
                # auto-pick them and jump straight to the slot proposal. The
                # slot_question already names the doctor, so the patient still
                # sees who they're booking with.
                only = doctors[0]
                state["doctor"] = only.get("Doctor", "") or ""
                state["doctor_ar"] = only.get("DoctorAR", "") or ""
                if only.get("WalkInPrice") is not None:
                    state["walk_in_price"] = only.get("WalkInPrice")
                from nodes.doctor_selection import _handle_slot_fetch
                state["last_bot_message"] = _handle_slot_fetch(state, lang)
            else:
                state["booking_stage"] = "doctor_list"
                state["last_bot_message"] = doctor_list_message(doctors, specialty, lang, used_date)
        elif strict:
            # Specialty exists but has nothing on the requested day. Stay in
            # routing so the patient can pick a different date — don't drop
            # to callback_pending (the specialty itself isn't unavailable).
            # Clear requested_date so a follow-up "yes/ok" doesn't immediately
            # re-query the same dead day; the patient will give a new date or
            # let the forward-search fallback pick the next available one.
            state["specialty_confirmed"] = False
            state["requested_date"] = None
            state["last_bot_message"] = no_doctors_on_date_message(
                specialty, preferred, lang,
            )
        else:
            # No doctors available in next 7 days — promise a callback and
            # mark callback_pending so the conversation doesn't loop on
            # routing → no_doctors → re-routing → no_doctors. conversation_node
            # respects callback_pending and replies gracefully on subsequent
            # turns; routing_node early-returns from this stage.
            state["speciality"] = None
            state["speciality_ar"] = None
            state["specialty_confirmed"] = False
            state["complaint_text"] = None
            state["routing_clarification"] = None
            state["booking_stage"] = "callback_pending"
            state["last_bot_message"] = no_doctors_message(specialty, lang)
    else:
        # Patient didn't confirm — maybe they want a different specialty
        # Re-route with their new input as clarification
        state["routing_clarification"] = last_msg
        # Reset specialty to re-route
        state["speciality"] = None
        state["speciality_ar"] = None
        state["speciality_confidence"] = None

        result = route_specialty(
            complaint=state["complaint_text"],
            age=state.get("patient_age") or 30,
            lang=lang,
            clarification=last_msg,
        )

        specialty = result["specialty"]
        if specialty:
            state["speciality"] = specialty
            state["speciality_confidence"] = result["confidence"]

            patient = state.get("patient_name", "")
            spec_display = SPECIALTY_EN_TO_AR.get(specialty, specialty) if lang == "ar" else specialty
            if lang == "ar":
                name_part = f" أ/ {patient}" if patient else ""
                state["last_bot_message"] = (
                    f"تمام{name_part}، يبدو إنك محتاج تخصص **{spec_display}**. "
                    f"تبغى أعرض لك الأطباء المتاحين؟"
                )
            else:
                state["last_bot_message"] = (
                    f"Got it! Sounds like you need **{spec_display}**. "
                    f"Want me to show you the available doctors?"
                )
        else:
            if lang == "ar":
                state["last_bot_message"] = "ممكن توضحلي أكثر وش تحتاج بالضبط؟"
            else:
                state["last_bot_message"] = "Could you tell me a bit more about what you need?"

    return state


def _is_direct_specialty_request(complaint: str) -> bool:
    """
    Check if the complaint is a direct specialty name rather than symptoms.
    E.g. "dermatologist", "جلدية", "أسنان", "عيون", "orthopedics"
    """
    direct_keywords_en = {
        "dermatologist", "dermatology", "dentist", "dental", "orthodontic",
        "ophthalmology", "eye doctor", "eye", "ent", "ear nose throat",
        "cardiologist", "cardiology", "heart", "orthopedics", "orthopedic",
        "bone doctor", "urologist", "urology", "gynecologist", "gynecology",
        "obgyn", "ob-gyn", "pediatrician", "pediatrics", "neurologist",
        "neurology", "psychiatrist", "psychiatry", "endocrinologist",
        "gastroenterology", "pulmonology", "chest doctor", "rheumatology",
        "nephrology", "kidney", "oncology", "nutrition", "nutritionist",
        "physiotherapy", "physical therapy", "plastic surgery", "cosmetic",
        "internal medicine", "family medicine", "general surgery",
        "ivf", "icsi", "fertility", "infertility", "test tube",
    }
    direct_keywords_ar = {
        "جلدية", "أسنان", "اسنان", "عيون", "أنف وأذن", "انف واذن",
        "قلب", "عظام", "مسالك", "نساء وتوليد", "نسا", "أطفال", "اطفال",
        "أعصاب", "اعصاب", "نفسية", "غدد", "باطنة", "باطنية",
        "جهاز هضمي", "صدرية", "كلى", "أورام", "تغذية",
        "علاج طبيعي", "تجميل", "جراحة", "أسرة",
        "تقويم", "جذور", "جلد",
        # IVF / fertility — must skip the generic-injection triage that the
        # word "حقن" alone otherwise triggers.
        "حقن مجهري", "حقن مجهرى", "اطفال انابيب", "أطفال أنابيب",
        "تلقيح صناعي", "تلقيح", "عقم", "تأخر حمل", "تاخر حمل",
    }

    lower = complaint.lower().strip()
    # Check if the complaint IS basically just a specialty name
    for kw in direct_keywords_en:
        if kw in lower:
            return True
    for kw in direct_keywords_ar:
        if kw in lower:
            return True
    return False


def _get_triage_question(complaint: str, age: int, lang: str) -> str:
    """Ask ONE targeted clarifying question to improve routing accuracy."""
    prompt = TRIAGE_PROMPT_AR if lang == "ar" else TRIAGE_PROMPT_EN
    result = call_llm(
        messages=[{"role": "user", "content": complaint}],
        system_prompt=prompt.format(complaint=complaint, age=age),
        temperature=0.2,
        max_tokens=100,
        json_mode=True,
        label="triage",
    )
    return result.get("question", "").strip()
