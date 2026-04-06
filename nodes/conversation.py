# nodes/conversation.py
"""
LLM conversation turn — the "brain" that generates a natural reply
and extracts structured state updates from patient messages.
"""
from datetime import datetime
from state import BookingState
from llm.client import call_llm
from nodes._helpers import apply_llm_updates
from prompts.conversation import CONVERSATION_SYSTEM_PROMPT
from utils.datetime_fmt import format_date


def conversation_node(state: BookingState) -> BookingState:
    """
    Call the LLM to get a reply and state_updates.
    Applies updates to state and stores the reply.
    """
    lang = state.get("language", "en")
    messages = state.get("messages", [])

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
    apply_llm_updates(state, updates)

    # Guard: never jump to patient_info or complete without a confirmed slot
    if state.get("booking_stage") in ("patient_info", "complete") and not state.get("selected_slot"):
        state["booking_stage"] = "slot_selection" if state.get("doctor") else "doctor_list"

    # Store reply and raw updates for downstream nodes to inspect
    state["last_bot_message"] = reply
    state["_llm_updates"] = updates  # transient — used by downstream nodes this turn

    return state


def _state_summary(state: dict) -> str:
    insured = (
        "YES" if state.get("insured") is True
        else "NO" if state.get("insured") is False
        else "not answered yet — must ask"
    )
    return "\n".join([
        f"stage:          {state.get('booking_stage') or 'not started'}",
        f"patient_name:   {state.get('patient_name') or 'not collected yet — ASK FOR NAME FIRST'}",
        f"complaint:      {state.get('complaint_text') or 'not collected'}",
        f"patient_age:    {state.get('patient_age') if state.get('patient_age') is not None else 'not asked yet'}",
        f"specialty:      {state.get('speciality') or state.get('speciality_ar') or 'not determined'}",
        f"doctor:         {state.get('doctor') or state.get('doctor_ar') or 'not selected'}",
        f"selected_slot:  {state.get('selected_slot') or 'not selected yet'}",
        f"phone:          {state.get('phone') or 'not collected'}",
        f"insured:        {insured}",
        f"insurance_co:   {state.get('insurance_company') or 'not collected'}",
        f"doctors_loaded: {len(state.get('available_doctors', []))} in list",
    ])


def _data_summary(state: dict) -> str:
    parts = []
    doctors = state.get("available_doctors", [])
    if doctors:
        lines = []
        for d in doctors:
            en = d.get("Doctor", "?")
            ar = d.get("DoctorAR", "")
            nd = format_date(d.get("Nearest_Date"))
            lines.append(f"  {en}" + (f" / {ar}" if ar else "") + f" | {nd}")
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
