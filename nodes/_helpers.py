# nodes/_helpers.py
"""
Shared utilities for LangGraph nodes.
Centralizes state update logic so every node applies updates consistently.
"""
from utils.datetime_fmt import resolve_relative_date
from utils.language import normalize_ar


def apply_llm_updates(state: dict, updates: dict) -> None:
    """Apply state_updates dict from the conversation LLM to the booking state."""
    # String fields — set if non-null and non-empty
    STRING_FIELDS = [
        "complaint_text", "speciality", "speciality_ar", "doctor", "doctor_ar",
        "date", "preferred_time", "patient_name", "phone", "insurance_company",
        "booking_stage", "requested_date",
    ]
    for f in STRING_FIELDS:
        v = updates.get(f)
        if v is not None and v not in ("null", ""):
            state[f] = v

    # Resolve relative date words to actual YYYY-MM-DD
    rd = state.get("requested_date", "")
    if rd and rd not in ("null", ""):
        state["requested_date"] = resolve_relative_date(rd)

    # Integer: patient_age
    age = updates.get("patient_age")
    if age is not None and age != "null":
        try:
            state["patient_age"] = int(age)
        except (ValueError, TypeError):
            pass

    # Boolean: insured — handle many LLM output formats
    ins = updates.get("insured")
    if ins is True:
        state["insured"] = True
    elif ins is False:
        state["insured"] = False
    elif isinstance(ins, str):
        ins_lower = ins.lower().strip()
        if ins_lower in ("true", "yes", "نعم", "تأمين", "تامين", "insurance", "insured"):
            state["insured"] = True
        elif ins_lower in ("false", "no", "لا", "not insured", "none", "no insurance",
                           "كاش", "cash", "self-pay", "self pay", "ما عندي", "ما عندي تأمين"):
            state["insured"] = False

    # Safety net: if insured=True but no insurance_company, try to match from user message
    if state.get("insured") is True and not state.get("insurance_company"):
        _try_match_insurance_from_message(state)

    # Populate walk-in (cash) price once per doctor. Track the name we looked up
    # so switching doctors forces a fresh lookup.
    doc_en = state.get("doctor")
    if doc_en and state.get("_walk_in_price_doctor") != doc_en:
        try:
            from services.doctor_price import get_walk_in_price
            state["walk_in_price"] = get_walk_in_price(doc_en)
        except Exception:
            state["walk_in_price"] = None
        state["_walk_in_price_doctor"] = doc_en
        if state["walk_in_price"] is None:
            print(f"[price] {doc_en!r}: NO walk-in fee on file — will promise callback")
        else:
            print(f"[price] {doc_en!r}: walk-in = {state['walk_in_price']}")


def get_last_user_message(state: dict) -> str | None:
    """Extract the latest user message from the conversation."""
    messages = state.get("messages", [])
    user_messages = [m for m in messages if m["role"] == "user"]
    return user_messages[-1]["content"] if user_messages else None


# Acceptance words — all stored normalized (no hamza, ta-marbuta unified).
# User input is normalized with the same rules before matching, so Saudi
# variants like "أيوه" match "ايوه" after normalization.
ACCEPTANCE_WORDS = {
    "yes", "ok", "okay", "sure", "fine", "great", "perfect", "works", "good",
    "that works", "yes please", "sounds good", "that's fine", "confirmed",
    "confirm", "yep", "yup", "yeah", "alright",
    "نعم", "اوك", "موافق", "تمام", "زين", "ماشي", "اكيد", "ايوه", "ايه",
    "ابشر", "مناسب", "يناسبني", "كويس", "حلو", "طيب",
    "ان شاء الله", "ان شاءالله",
}

ACCEPTANCE_PARTIAL = {"yes", "ok", "sure", "نعم", "تمام", "زين", "ماشي", "مناسب", "طيب", "ايوه"}


def is_acceptance(text: str) -> bool:
    """True if the patient accepted a shown option rather than requesting something different."""
    if not text:
        return False
    t = normalize_ar(text)
    if t in ACCEPTANCE_WORDS:
        return True
    return len(t.split()) <= 3 and any(w in t for w in ACCEPTANCE_PARTIAL)


def is_patient_info_complete(state: dict) -> bool:
    """True if all required patient info (name, phone, insurance) is already collected."""
    if not state.get("patient_name"):
        return False
    if not state.get("phone"):
        return False
    if state.get("insured") is None:
        return False
    if state.get("insured") and not state.get("insurance_company"):
        return False
    return True


def _try_match_insurance_from_message(state: dict) -> None:
    """
    Safety net: if LLM set insured=True but didn't extract the company name,
    scan the last user message for known insurance company names.
    Uses fuzzy matching so "medright" matches "MedRight", "boba" matches "Bupa", etc.
    """
    from config.constants import INSURANCE_COMPANIES

    last_msg = get_last_user_message(state)
    if not last_msg:
        return

    msg_lower = last_msg.lower().strip()

    # Build a clean list of (canonical_name, search_variants)
    # Group EN and AR names, create lowercase variants
    canonical_names = {}
    for name in INSURANCE_COMPANIES:
        lower = name.lower()
        # Map each name to itself as canonical (EN names are canonical)
        if lower not in canonical_names:
            canonical_names[lower] = name

    # Direct substring match first (most reliable)
    for lower_name, canonical in canonical_names.items():
        if lower_name in msg_lower:
            state["insurance_company"] = canonical
            return

    # Fuzzy match for misspellings (only if we have rapidfuzz)
    try:
        from rapidfuzz import process, fuzz
        candidates = list(canonical_names.keys())
        # Extract words from message that aren't common words
        skip_words = {"insurance", "insured", "تأمين", "تامين", "my", "company", "is", "شركة"}
        words = [w for w in msg_lower.split() if w not in skip_words and not w.isdigit() and len(w) > 2]

        for word in words:
            result = process.extractOne(word, candidates, scorer=fuzz.ratio, score_cutoff=70)
            if result:
                matched_lower, score, _ = result
                state["insurance_company"] = canonical_names[matched_lower]
                return
    except ImportError:
        pass
