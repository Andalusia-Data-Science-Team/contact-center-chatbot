# nodes/_helpers.py
"""
Shared utilities for LangGraph nodes.
Centralizes state update logic so every node applies updates consistently.
"""
import re

from utils.datetime_fmt import resolve_relative_date
from utils.language import normalize_ar


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


_PLACEHOLDER_FRAGMENTS = (
    "not collected", "not selected", "not asked", "not determined",
    "not answered", "not started", "not available",
)


def _is_placeholder_value(v: object) -> bool:
    """True if the LLM echoed back a state_summary placeholder string.

    The conversation prompt renders unset fields as e.g. "not collected yet — ASK
    FOR NAME FIRST" so the model knows what to ask. Some LLM responses copy
    those phrases into state_updates as if they were real values; we should
    ignore those rather than store a placeholder as a name.
    """
    if not isinstance(v, str):
        return False
    lo = v.lower()
    return any(frag in lo for frag in _PLACEHOLDER_FRAGMENTS)


def apply_llm_updates(state: dict, updates: dict) -> None:
    """Apply state_updates dict from the conversation LLM to the booking state."""
    # String fields — set if non-null and non-empty.
    # `requested_date` handled separately below so we can resolve / guard it.
    STRING_FIELDS = [
        "complaint_text", "speciality", "speciality_ar", "doctor", "doctor_ar",
        "date", "preferred_time", "patient_name", "phone", "insurance_company",
        "booking_stage",
    ]
    for f in STRING_FIELDS:
        v = updates.get(f)
        if v is not None and v not in ("null", "") and not _is_placeholder_value(v):
            state[f] = v

    # Requested date: resolve relative words ("بكرا", "tomorrow") to YYYY-MM-DD.
    # Never replace an already-resolved ISO date with raw text the LLM couldn't
    # resolve — that's how the booking date silently regressed (e.g. N4 t7 went
    # from "2026-04-27" back to "يوم الخميس" and ended up booked for today).
    # Also: don't let the LLM silently regress a future date back to today —
    # the LLM sometimes hallucinates requested_date="today" when the user only
    # gave a time-of-day hint like "العصر". Require the current user message
    # to actually mention "today/اليوم/etc." to honour a regression.
    from datetime import date as _date
    new_rd = updates.get("requested_date")
    if new_rd is not None and new_rd not in ("null", ""):
        resolved = resolve_relative_date(new_rd)
        accept = False
        if _ISO_DATE_RE.match(resolved):
            existing = state.get("requested_date") or ""
            existing_is_iso = bool(_ISO_DATE_RE.match(existing))
            if existing_is_iso:
                try_resolved = _date.fromisoformat(resolved)
                existing_date = _date.fromisoformat(existing)
                today = _date.today()
                # Regression check: future → today. Only honour if the user's
                # current message actually mentions "today" — otherwise the
                # LLM is hallucinating a regression from a time-of-day hint.
                if try_resolved == today and existing_date > today:
                    last_user = (get_last_user_message(state) or "").lower()
                    explicit_today = any(
                        kw in last_user
                        for kw in ("today", "اليوم", "النهاردة", "النهارده")
                    )
                    accept = explicit_today
                else:
                    accept = True
            else:
                accept = True
            if accept:
                state["requested_date"] = resolved
        elif not _ISO_DATE_RE.match(state.get("requested_date") or ""):
            # No prior resolved date — keep the raw text so downstream nodes
            # can still try to interpret it.
            state["requested_date"] = resolved

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

    # Inverse safety net: if an insurance company is set, the patient is on
    # insurance — set insured=True if it wasn't extracted. Without this, the
    # bot loops asking "cash or insurance?" even after the patient names a
    # company (e.g. patient says "ميدغولف", company is captured but insured
    # stays None).
    if state.get("insurance_company") and state.get("insured") is None:
        state["insured"] = True

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
