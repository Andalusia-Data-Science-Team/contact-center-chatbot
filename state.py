# state.py
from typing import TypedDict, Optional, List, Any


class BookingState(TypedDict):
    messages: List[dict]
    language: Optional[str]             # "en" | "ar"
    emergency: bool

    intent: Optional[str]
    intent_confidence: Optional[float]

    patient_age: Optional[int]

    complaint_text: Optional[str]
    speciality: Optional[str]           # English specialty name
    speciality_ar: Optional[str]        # Arabic specialty name
    speciality_confidence: Optional[float]

    doctor: Optional[str]               # English doctor name
    doctor_ar: Optional[str]            # Arabic doctor name
    doctor_confirmation_pending: Optional[str]

    date: Optional[str]                 # Current date context (YYYY-MM-DD)
    requested_date: Optional[str]       # Patient-requested date for slot search
    preferred_time: Optional[str]       # Patient's preferred time (free text)
    selected_slot: Optional[str]        # Confirmed slot time (HH:MM 24h)

    triage_question_asked: bool         # True after the one clarifying question is asked
    specialty_confirmed: bool           # True after patient confirms the matched specialty
    routing_clarification: Optional[str]
    available_doctors: List[Any]
    available_slots: List[Any]

    patient_name: Optional[str]
    phone: Optional[str]
    insured: Optional[bool]
    insurance_company: Optional[str]

    booking_stage: Optional[str]
    last_bot_message: Optional[str]
    followup_message: Optional[str]     # Second message sent after the main reply (e.g., app download prompt)
    _llm_updates: Optional[dict]          # transient: LLM state_updates for current turn
    _proposed_slot: Optional[dict]         # transient: slot proposed but not yet confirmed

    # ── Dev metrics (accumulated across the full session) ──
    _session_total_input_tokens: int
    _session_total_output_tokens: int
    _session_total_llm_calls: int
    _session_total_latency_ms: int
    _session_turns: int
    _last_turn_metrics: Optional[dict]    # metrics from the most recent turn


def initial_state() -> BookingState:
    return BookingState(
        messages=[],
        language=None,
        emergency=False,
        intent=None,
        intent_confidence=None,
        patient_age=None,
        complaint_text=None,
        speciality=None,
        speciality_ar=None,
        speciality_confidence=None,
        doctor=None,
        doctor_ar=None,
        doctor_confirmation_pending=None,
        date=None,
        requested_date=None,
        preferred_time=None,
        selected_slot=None,
        triage_question_asked=False,     # Will be asked only for symptom-based routing
        specialty_confirmed=False,       # Must confirm specialty before showing doctors
        routing_clarification=None,
        available_doctors=[],
        available_slots=[],
        patient_name=None,
        phone=None,
        insured=None,
        insurance_company=None,
        booking_stage=None,
        last_bot_message=None,
        followup_message=None,
        _llm_updates=None,
        _proposed_slot=None,
        _session_total_input_tokens=0,
        _session_total_output_tokens=0,
        _session_total_llm_calls=0,
        _session_total_latency_ms=0,
        _session_turns=0,
        _last_turn_metrics=None,
    )
