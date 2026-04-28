# nodes/conversation.py
"""
LLM conversation turn — the "brain" that generates a natural reply
and extracts structured state updates from patient messages.
"""
import re
from datetime import datetime
from state import BookingState
from llm.client import call_llm
from nodes._helpers import apply_llm_updates, get_last_user_message, is_patient_info_complete
from prompts.conversation import CONVERSATION_SYSTEM_PROMPT
from services.formatter import (
    price_cash_message,
    price_insured_message,
    price_no_data_message,
    price_no_doctor_message,
    price_unknown_insurance_message,
)
from utils.datetime_fmt import format_date
from utils.language import normalize_ar

_CASH_WORDS = {"cash", "self pay", "self-pay", "self", "كاش", "نقدي", "نقداً", "نقدا"}
_INS_WORDS = {"insurance", "insured", "تأمين", "تامين"}

# Cancellation phrases — patient wants to abandon the booking. Detected in code
# (not via LLM) so a clear cancel is honoured deterministically.
# IMPORTANT: patterns must use *normalized* Arabic — `normalize_ar` maps
# أ/إ/آ → ا, ى → ي, ة → ه — so e.g. "ابغى" arrives here as "ابغي".
_CANCEL_PATTERNS = (
    r"\b(?:cancel|stop|nevermind|never\s*mind|forget\s*it)\b",
    r"\b(?:don'?t|do\s*not)\s*want\s*to\s*book\b",
    r"\bnot\s*(?:interested|now|today)\b",
    r"خلاص\s*(?:ما|مش|مو|ماني)?\s*(?:ابغي|اريد|عايز|بدي|محتاج|ابي|حابب|حاب)",
    r"(?:ما|مش|مو|ماني)\s*(?:ابغي|اريد|عايز|بدي|ابي|حاب)\s*(?:احجز|اكمل|اتابع)",
    r"الغ(?:اء|ي)\s*الحجز",
    r"\bبطل\s*الحجز\b",
    r"غير\s*رايي",
    r"عدلت\s*عن",
)


def _is_cancellation(text: str) -> bool:
    """True if the patient is clearly cancelling/abandoning the booking.

    Operates on *normalized* Arabic — normalize the text first so the same
    pattern works for "ابغى" (alif-maqsura ya) and "ابغي" (regular ya).
    """
    if not text:
        return False
    t = normalize_ar(text).lower()
    return any(re.search(p, t, re.IGNORECASE) for p in _CANCEL_PATTERNS)

# Word-boundary regexes for the keyword safety net — can be called on any
# length of message, unlike _detect_insurance_answer which is short-phrase only.
_CASH_PATTERN = re.compile(
    r"(?:(?<![\w])(?:cash|self[-\s]?pay|self)(?![\w]))|كاش|نقدي|نقدا",
    re.IGNORECASE,
)
_INS_PATTERN = re.compile(
    r"(?:(?<![\w])(?:insurance|insured)(?![\w]))|تامين|تأمين",
    re.IGNORECASE,
)


def _scan_insurance_keywords(text: str):
    """Return True=insured, False=cash, None=ambiguous or not found.

    Works on arbitrary-length messages (unlike _detect_insurance_answer which
    is for short direct answers to the cash-or-insurance question).
    """
    if not text:
        return None
    t = normalize_ar(text)
    has_cash = bool(_CASH_PATTERN.search(t))
    has_ins = bool(_INS_PATTERN.search(t))
    if has_cash and not has_ins:
        return False
    if has_ins and not has_cash:
        return True
    return None

# Price-intent trigger phrases. When the LLM misses price_inquiry we still
# want to answer the patient's question instead of silently advancing the flow.
# Matches patterns like "how much", "what's the price/cost/fee", "كم سعر",
# "كم الكشف", "بكم", "السعر".
_PRICE_PATTERNS = (
    r"\bhow\s+much\b",
    r"\bwhat(?:'s|\s+is)?\s+the\s+(?:price|cost|fee|charge)\b",
    r"\b(?:price|cost|fee|charge)\b",
    r"كم\s*(?:سعر|الكشف|تكلف|ثمن|يكلف|سعرها|حق)",
    r"بكم",
    r"السعر",
    r"التكلفه",
    r"التكلفة",
    r"سعر\s+الكشف",
)


def _looks_like_price_inquiry(text: str) -> bool:
    if not text:
        return False
    t = normalize_ar(text)
    return any(re.search(p, t, re.IGNORECASE) for p in _PRICE_PATTERNS)

# Broader acceptance detector than _helpers.is_acceptance — matches acceptance
# tokens anywhere in the sentence, so combined messages like "yes it works how
# much does it cost" are recognised as accepting the currently proposed slot.
_ACCEPT_TOKENS = (
    "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "works", "confirmed",
    "confirm", "alright", "great", "perfect", "fine",
    "نعم", "ايوه", "أيوه", "تمام", "ماشي", "مناسب", "موافق", "اوك", "أوك",
)
_NEGATION_TOKENS = ("no", "not", "don't", "dont", "لا", "مش", "مو", "ما")


def _contains_acceptance(text: str) -> bool:
    """True if text contains an acceptance token anywhere (word-boundary safe).

    Broader than is_acceptance — matches combined messages like
    "yes it works how much does it cost" that accept a proposed slot while
    also asking something else.

    Returns False if the message also specifies a different time (a digit),
    because "تمام الساعة 8" is specifying a new time, not accepting the
    currently proposed one.
    """
    if not text:
        return False
    t = normalize_ar(text).lower()
    # A digit in the message means the user is likely asking for a specific
    # time, not accepting the earlier proposal — leave it to the slot logic.
    if re.search(r"\d", t):
        return False
    # Bail out if the sentence also contains a clear negation
    for neg in _NEGATION_TOKENS:
        if re.search(rf"(?<![\w]){re.escape(neg)}(?![\w])", t):
            return False
    for tok in _ACCEPT_TOKENS:
        if re.search(rf"(?<![\w]){re.escape(tok)}(?![\w])", t):
            return True
    return False


def _maybe_confirm_proposed_slot(state: dict) -> None:
    """Silently confirm the earliest proposed slot and advance to patient_info.

    Used when the patient accepts a slot while simultaneously asking about
    price — the price flow takes over the reply, but we still need to lock
    in the slot so the next turn collects phone/info instead of re-asking
    the time.
    """
    if state.get("selected_slot"):
        return
    proposed = state.get("_proposed_slot")
    if not proposed:
        return
    state["selected_slot"] = proposed.get("time_24")
    if proposed.get("date"):
        state["date"] = proposed["date"]
    state["_proposed_slot"] = None
    state["booking_stage"] = "patient_info"


def conversation_node(state: BookingState) -> BookingState:
    """
    Call the LLM to get a reply and state_updates.
    Applies updates to state and stores the reply.
    """
    lang = state.get("language", "en")
    messages = state.get("messages", [])

    # Already cancelled — don't re-engage the booking LLM. Just acknowledge.
    if state.get("booking_stage") == "cancelled":
        state["_llm_updates"] = {}
        if lang == "ar":
            state["last_bot_message"] = (
                "الحجز ملغي. لو حابب تبدأ حجز جديد، قولي. 🌿"
            )
        else:
            state["last_bot_message"] = (
                "The booking has been cancelled. Let me know if you'd like to start a new one. 🌿"
            )
        return state

    # Callback already promised — don't re-prompt for booking details. The
    # contact-center team owns the next step.
    if state.get("booking_stage") == "callback_pending":
        state["_llm_updates"] = {}
        if lang == "ar":
            state["last_bot_message"] = (
                "تمام، سجلنا طلبك وفريق خدمة العملاء راح يتواصل مع حضرتك قريباً 🌿"
            )
        else:
            state["last_bot_message"] = (
                "All set — your request is logged and the contact-center team will reach out shortly 🌿"
            )
        return state

    # Cancellation safety net — patient said "خلاص ما ابغى احجز", "cancel", etc.
    # Acknowledge gracefully and short-circuit the rest of the booking pipeline.
    last_user_msg_early = get_last_user_message(state) or ""
    if _is_cancellation(last_user_msg_early) and state.get("booking_stage") not in (
        None, "", "not started", "complete", "cancelled"
    ):
        state["booking_stage"] = "cancelled"
        state["_llm_updates"] = {}
        if lang == "ar":
            state["last_bot_message"] = (
                "تمام، تم إلغاء الحجز. لو احتجت أي مساعدة في وقت ثاني، تواصل معنا 🌿"
            )
        else:
            state["last_bot_message"] = (
                "No problem — your booking has been cancelled. Reach out anytime "
                "if you'd like to book later 🌿"
            )
        return state

    # If we just asked "cash or insurance?" to answer a price question, and the
    # user's reply is a clear cash/insurance answer, handle it in code — the LLM
    # at slot_selection stage would otherwise misread "cash" as a time input.
    if state.get("_pending_price_followup"):
        last_msg = get_last_user_message(state) or ""
        answer = _detect_insurance_answer(last_msg)
        if answer is not None:
            state["insured"] = answer
            state["_pending_price_followup"] = False
            # If a slot was proposed before the price detour, lock it in now
            # so the reply can move on to phone collection instead of looping
            # back to slot selection.
            _maybe_confirm_proposed_slot(state)
            state["last_bot_message"] = _handle_price_inquiry(state)
            state["_llm_updates"] = {}
            return state
        # Ambiguous reply — clear the flag and fall through to normal LLM handling
        state["_pending_price_followup"] = False

    result = call_llm(
        messages=messages[-14:],
        system_prompt=CONVERSATION_SYSTEM_PROMPT.format(
            lang=lang,
            state_summary=_state_summary(state),
            data_summary=_data_summary(state),
        ),
        temperature=0.3,
        max_tokens=600,
        json_mode=True,
        label="conversation",
    )

    reply = result.get("reply", "")
    updates = result.get("state_updates", {})

    # Safety net: reject ambiguous insured extraction.
    # When the bot just asked "كاش ام تأمين؟" and the patient replies with a bare
    # "أيوه" / "yes" / "نعم" / "no" / "لا", the LLM sometimes silently extracts
    # insured=False. The patient never clarified — re-ask instead of guessing.
    if updates.get("insured") is not None and state.get("insured") is None:
        msg = (last_user_msg_early or "").strip()
        has_clear_signal = _scan_insurance_keywords(msg) is not None
        is_short = len(msg.split()) <= 2
        if is_short and not has_clear_signal:
            updates["insured"] = None
            # Override the LLM reply with an explicit re-ask so the patient
            # can give a clear answer.
            if state.get("booking_stage") in ("patient_info", "slot_selection") \
                    and state.get("phone"):
                if lang == "ar":
                    reply = "محتاج أعرف، الكشف كاش ولا تأمين؟"
                else:
                    reply = "Just to confirm — is this cash or insurance?"

    apply_llm_updates(state, updates)

    # Guard: never jump to patient_info or complete without a confirmed slot
    if state.get("booking_stage") in ("patient_info", "complete") and not state.get("selected_slot"):
        state["booking_stage"] = "slot_selection" if state.get("doctor") else "doctor_list"

    # Guard: never jump to slot_selection without a doctor. The LLM sometimes
    # advances the stage when the patient sends a bare number ("4") at
    # doctor_list — that number is a doctor index, not a time. Push back so
    # doctor_selection_node's pick-by-number safety net can run.
    if state.get("booking_stage") == "slot_selection" and not state.get("doctor"):
        state["booking_stage"] = "doctor_list" if state.get("available_doctors") else "complaint"

    # Guard: never jump past routing while a specialty is set but unconfirmed.
    # Without this, "بعد المغرب" after the specialty-confirmation question
    # makes the LLM set booking_stage=slot_selection directly, skipping the
    # routing node's implicit-accept path and leaving no doctors loaded.
    if (
        state.get("booking_stage") in ("doctor_list", "slot_selection", "patient_info", "complete")
        and state.get("speciality")
        and not state.get("specialty_confirmed")
        and not state.get("available_doctors")
    ):
        state["booking_stage"] = "routing"

    last_user_msg = get_last_user_message(state) or ""

    # Safety net: if the LLM missed extracting `insured` but the user's message
    # clearly says "كاش" or "تأمين" (e.g. combined messages like
    # "09123456 - كاش وعايزه سعر الكشف"), set it here so downstream branches
    # don't ask the cash-or-insurance question again.
    if state.get("insured") is None:
        detected = _scan_insurance_keywords(last_user_msg)
        if detected is not None:
            state["insured"] = detected

    # Safety net: if the LLM missed a price inquiry ("كم سعر الكشف؟",
    # "how much does it cost") but the user's message clearly asks about cost,
    # force the price_inquiry flag so we handle it below.
    if not updates.get("price_inquiry") and _looks_like_price_inquiry(last_user_msg):
        updates["price_inquiry"] = True

    # Price inquiry takes over the reply deterministically so the LLM can't pick
    # the wrong branch (e.g. assume insurance when it's unknown). Also neutralise
    # updates that would advance the booking stage while the patient is still
    # asking about cost.
    if updates.get("price_inquiry"):
        updates["needs_slot_query"] = False
        updates["doctor_fuzzy_input"] = None
        # Don't let a bundled time preference advance slot selection this turn
        # — the price answer is the priority, and if the user also accepted a
        # slot we handle it via _maybe_confirm_proposed_slot below.
        updates["preferred_time"] = None
        updates["wants_more_slots"] = False
        updates["slots_filter"] = None
        # Sentinel so the slot_selection safety net doesn't repopulate these
        # from the same user message and overwrite our price reply.
        updates["_price_handled"] = True
        # If the same message also accepts the proposed slot (e.g. "yes it
        # works how much" / "تمام الساعة 8 كم السعر"), lock the slot in before
        # answering the price so we don't lose the confirmation to the price
        # detour.
        if _contains_acceptance(last_user_msg):
            _maybe_confirm_proposed_slot(state)
        reply = _handle_price_inquiry(state)

    # Store reply and raw updates for downstream nodes to inspect
    state["last_bot_message"] = reply
    state["_llm_updates"] = updates  # transient — used by downstream nodes this turn

    return state


def _handle_price_inquiry(state: dict) -> str:
    lang = state.get("language", "en")
    doctor_en = state.get("doctor") or ""
    doctor_ar = state.get("doctor_ar") or ""
    price = state.get("walk_in_price")
    insured = state.get("insured")

    if insured is True:
        reply = price_insured_message(lang)
    elif not doctor_en and not doctor_ar:
        # Cash path: need a specific doctor to quote a number.
        reply = price_no_doctor_message(lang)
    elif insured is False:
        if price is None:
            reply = price_no_data_message(doctor_en, doctor_ar, lang)
        else:
            reply = price_cash_message(doctor_en, doctor_ar, float(price), lang)
    else:
        # insured is None (unknown) — quote the cash fee and ask cash-or-insurance.
        # Flag so the next turn treats "cash" / "insurance" as the price answer
        # rather than a slot input.
        state["_pending_price_followup"] = True
        reply = price_unknown_insurance_message(doctor_en, doctor_ar, price, lang)

    # If a slot is already locked but we still need phone/info, nudge the
    # conversation forward so we don't stall on the price answer.
    if (
        state.get("selected_slot")
        and not state.get("_pending_price_followup")
        and not is_patient_info_complete(state)
        and not state.get("phone")
    ):
        if lang == "ar":
            reply += "\n\nممكن رقم جوالك عشان أأكد الحجز؟"
        else:
            reply += "\n\nCould I have your phone number to confirm the booking?"

    return reply


def _detect_insurance_answer(text: str):
    """Return True=insured, False=cash, None=ambiguous. Short-phrase detector only."""
    if not text:
        return None
    t = normalize_ar(text).lower().strip().strip(".,!?؟")
    # Ignore long sentences — only short direct answers should short-circuit
    if len(t.split()) > 5:
        return None
    if t in _CASH_WORDS or any(w == t for w in _CASH_WORDS):
        return False
    if t in _INS_WORDS or any(w == t for w in _INS_WORDS):
        return True
    # Substring match as safety net
    if any(w in t for w in _CASH_WORDS):
        return False
    if any(w in t for w in _INS_WORDS):
        return True
    return None


def _state_summary(state: dict) -> str:
    insured = (
        "YES" if state.get("insured") is True
        else "NO" if state.get("insured") is False
        else "not answered yet — must ask"
    )
    wp = state.get("walk_in_price")
    walk_in = f"{wp:g}" if wp is not None else "NONE — team will call patient with price"
    return "\n".join([
        f"stage:          {state.get('booking_stage') or 'not started'}",
        f"patient_name:   {state.get('patient_name') or 'not collected yet — ASK FOR NAME FIRST'}",
        f"complaint:      {state.get('complaint_text') or 'not collected'}",
        f"patient_age:    {state.get('patient_age') if state.get('patient_age') is not None else 'not asked yet'}",
        f"specialty:      {state.get('speciality') or state.get('speciality_ar') or 'not determined'}",
        f"doctor:         {state.get('doctor') or state.get('doctor_ar') or 'not selected'}",
        f"walk_in_price:  {walk_in}",
        f"selected_slot:  {state.get('selected_slot') or 'not selected yet'}",
        f"phone:          {state.get('phone') or 'not collected'}",
        f"insured:        {insured}",
        f"insurance_co:   {state.get('insurance_company') or 'not collected'}",
        f"doctors_loaded: {len(state.get('available_doctors', []))} in list",
    ])


def _data_summary(state: dict) -> str:
    parts = []

    # Walk-in / cash price context for the currently-selected doctor (if any).
    # Only relevant when the patient is paying cash — if insured, the LLM must
    # NOT quote the price (see conversation prompt rules).
    doc = state.get("doctor")
    price = state.get("walk_in_price")
    if doc and price is not None:
        parts.append(
            f"Selected doctor walk-in (cash) price: {price:g} (only mention if patient asks "
            f"and insured is False or unknown; do NOT quote to insured patients)."
        )

    doctors = state.get("available_doctors", [])
    if doctors:
        lines = []
        for d in doctors:
            en = d.get("Doctor", "?")
            ar = d.get("DoctorAR", "")
            nd = format_date(d.get("Nearest_Date"))
            p = d.get("WalkInPrice")
            price_note = f" | walk-in {float(p):g}" if p is not None else ""
            lines.append(f"  {en}" + (f" / {ar}" if ar else "") + f" | {nd}{price_note}")
        parts.append("Available doctors:\n" + "\n".join(lines))

    slots = state.get("available_slots", [])
    if slots:
        lines = []
        for s in slots[:8]:
            t = s.get("StartTime") or s.get("Slot_Time")
            d = s.get("StartDate") or s.get("Slot_Date")
            try:
                p = t.split(":")
                t_obj = datetime(2000, 1, 1, int(p[0]), int(p[1])).time()
                lines.append(f"  {format_date(d)} at {t_obj.strftime('%I:%M %p')}")
            except Exception:
                pass
        parts.append("Doctor slots:\n" + "\n".join(lines))

    return "\n\n".join(parts) if parts else "No data loaded yet."
