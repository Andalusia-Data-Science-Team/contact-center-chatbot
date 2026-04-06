# nodes/patient_info.py
"""
Handles the 'complete' booking stage:
- Verify all required patient info is present
- If missing → bounce back to patient_info stage
- If complete → generate confirmation message
"""
from state import BookingState
from nodes._helpers import is_patient_info_complete
from services.formatter import missing_info_message, confirmation_message, app_download_message


def patient_info_node(state: BookingState) -> BookingState:
    """Check completeness and produce final confirmation or ask for missing info."""
    stage = state.get("booking_stage")

    # Only act on complete or patient_info stages
    if stage not in ("complete", "patient_info"):
        return state

    # Safety: can't confirm without a selected slot
    if not state.get("selected_slot"):
        if stage == "complete":
            state["booking_stage"] = "slot_selection" if state.get("doctor") else "doctor_list"
        return state

    lang = state.get("language", "en")

    # If all info is collected — generate the confirmation card regardless of stage
    if is_patient_info_complete(state):
        state["booking_stage"] = "complete"
        state["last_bot_message"] = confirmation_message(state, lang)
        state["followup_message"] = app_download_message(lang)
    elif stage == "complete":
        # LLM said complete but info is missing — bounce back
        state["booking_stage"] = "patient_info"
        state["last_bot_message"] = missing_info_message(_get_missing_fields(state), lang)

    return state


def _get_missing_fields(state: dict) -> list:
    """Return list of missing required fields."""
    missing = []
    if not state.get("patient_name"):
        missing.append("name")
    if not state.get("phone"):
        missing.append("phone")
    if state.get("insured") is None:
        missing.append("insured")
    elif state.get("insured") and not state.get("insurance_company"):
        missing.append("insurance_company")
    return missing
