# services/doctor_selector.py
"""
Fetch doctors from DB and fuzzy-match patient input to a doctor name.
"""
import re
import time
from datetime import date as _date
from threading import Lock
from typing import Optional
from db.database import (
    query_availability_with_fallback,
    query_doctor_slots_with_fallback,
    aggregate_doctor_slots,
)
from services.doctor_price import enrich_doctors_with_prices
from utils.fuzzy_match import fuzzy_match_doctor

NAME_STOPWORDS = {
    "please", "book", "with", "dr", "dr.", "de", "doctor", "doc", "want", "i", "the", "me", "can",
    "you", "yes", "okay", "ok", "sure", "like", "a", "an", "appointment", "would",
    "دكتور", "الدكتور", "د.", "د", "مع", "أريد", "ممكن", "أبغى", "ابغى", "حجز", "موعد",
}


# Short-TTL shared cache for doctor lookups. Two patients asking for the same
# specialty within 30s share a single round-trip through the heavy SQL_QUERY
# (which builds 7 temp tables and can fan out across 8 days when the requested
# day is empty). Thread-safe so it's correct under Streamlit's concurrent
# session model. 30s is short enough that newly-booked slots aren't stale to
# the second patient for long.
_DOCTOR_CACHE_TTL_SECONDS = 30
_DOCTOR_CACHE_MAX_ENTRIES = 200
_doctor_cache: dict = {}
_doctor_cache_lock = Lock()


def _doctor_cache_get(key: tuple):
    now = time.time()
    with _doctor_cache_lock:
        entry = _doctor_cache.get(key)
        if not entry:
            return None
        entry_time, doctors, used_date = entry
        if now - entry_time >= _DOCTOR_CACHE_TTL_SECONDS:
            return None
        # Return a shallow copy of the list — callers (e.g. _handle_slot_fetch)
        # mutate `available_doctors` in place when surfacing alternatives.
        return list(doctors), used_date


def _doctor_cache_put(key: tuple, doctors: list, used_date: str) -> None:
    with _doctor_cache_lock:
        _doctor_cache[key] = (time.time(), doctors, used_date)
        if len(_doctor_cache) > _DOCTOR_CACHE_MAX_ENTRIES:
            # Drop the 20% oldest entries so the dict can't grow unbounded.
            stale = sorted(_doctor_cache.items(), key=lambda kv: kv[1][0])
            for k, _ in stale[: _DOCTOR_CACHE_MAX_ENTRIES // 5]:
                _doctor_cache.pop(k, None)


def fetch_doctors(
    specialty: str,
    lang: str,
    preferred_date: str = None,
    strict_date: bool = False,
) -> tuple:
    """
    Query DB for doctors in the given specialty.
    Returns (doctor_list, date_used). Filters out doctors with no slot data.
    Note: routing now always returns EN specialty names, so try EN first regardless of lang.

    When `strict_date` is True, the search is constrained to `preferred_date`
    only — no forward fallback. Used when the patient explicitly asked for a
    specific day ("اليوم", "يوم الخميس") so we don't silently shift to a later
    day they didn't ask about.
    """
    cache_key = (specialty or "", lang, preferred_date or "", bool(strict_date))
    cached = _doctor_cache_get(cache_key)
    if cached is not None:
        return cached

    max_days = 0 if strict_date else 7
    # Always try EN first (routing returns EN names)
    rows, used_date = query_availability_with_fallback(
        specialty_en=specialty, specialty_ar=None,
        preferred_date=preferred_date, max_days_ahead=max_days,
    )
    # Fallback: try as AR name in case it's an AR specialty somehow
    if not rows:
        rows, used_date = query_availability_with_fallback(
            specialty_en=None, specialty_ar=specialty,
            preferred_date=preferred_date, max_days_ahead=max_days,
        )

    doctors = aggregate_doctor_slots(rows)
    doctors = [d for d in doctors if d.get("Nearest_Date") and d.get("Nearest_Time")]

    # FreeSlotsRanked is now bound to @ReportDate at the SQL level, so all
    # surviving rows already have Nearest_Date == @ReportDate. This block is
    # kept as a defensive anchor: if any row leaks through with a different
    # date (e.g. data inconsistency), drop it instead of letting the header
    # label and per-doctor times disagree.
    if doctors:
        def _to_date(nd):
            if hasattr(nd, "isoformat"):  # datetime.date
                return nd if not hasattr(nd, "hour") else nd.date()
            if isinstance(nd, str) and len(nd) >= 10:
                try:
                    return _date.fromisoformat(nd[:10])
                except ValueError:
                    return None
            return None

        slot_dates = [d for d in (_to_date(doc.get("Nearest_Date")) for doc in doctors) if d]
        if slot_dates:
            real_earliest = min(slot_dates)
            # In strict_date mode, anchor to the requested day instead of the
            # min — the FreeSlotsRanked CTE picks each doctor's earliest free
            # slot across ALL future dates, so a doctor scheduled to work today
            # but already fully booked still surfaces with Nearest_Date=tomorrow.
            # Honouring that would silently shift the list off the requested day.
            anchor = real_earliest
            if strict_date and preferred_date:
                try:
                    from datetime import datetime as _dt
                    anchor = _dt.strptime(preferred_date, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    pass
            used_date = anchor.strftime("%Y-%m-%d")
            doctors = [d for d in doctors if _to_date(d.get("Nearest_Date")) == anchor]

    enrich_doctors_with_prices(doctors)
    _doctor_cache_put(cache_key, doctors, used_date)
    # Hand callers a fresh list so their in-place mutations (e.g. appending
    # alternative doctors in _handle_slot_fetch) don't poison the cache.
    return list(doctors), used_date


def fetch_slots(doctor_en: str, doctor_ar: str = None, preferred_date: str = None) -> tuple:
    """Fetch individual free slots for a specific doctor."""
    return query_doctor_slots_with_fallback(
        doctor_en=doctor_en or None,
        doctor_ar=doctor_ar or None,
        preferred_date=preferred_date,
    )


def match_doctor(raw_input: str, available_doctors: list) -> dict:
    """
    Match patient's free-text input to a doctor in available_doctors.
    Returns: {status: "matched"|"confirm"|"not_found", doctor: dict|None, matched_name: str}
    """
    if not available_doctors:
        return {"status": "not_found", "doctor": None}

    by_number = _pick_by_number(raw_input, available_doctors)
    if by_number:
        return {"status": "matched", "doctor": by_number, "matched_by": "number"}

    words = raw_input.split()
    cleaned = " ".join(w for w in words if w.lower().strip(".,?!؟") not in NAME_STOPWORDS)
    if not cleaned:
        return {"status": "not_found", "doctor": None}

    en_names = [d["Doctor"] for d in available_doctors]
    match = fuzzy_match_doctor(cleaned, en_names)

    if match["status"] == "not_found":
        ar_names = [d["DoctorAR"] for d in available_doctors if d.get("DoctorAR")]
        if ar_names:
            ar_match = fuzzy_match_doctor(cleaned, ar_names)
            if ar_match["status"] != "not_found":
                match = ar_match
                doc = next((d for d in available_doctors
                            if d.get("DoctorAR") == match.get("matched_name")), None)
                if doc:
                    status = "matched" if match["status"] == "auto_correct" else "confirm"
                    return {"status": status, "doctor": doc,
                            "matched_name": match.get("matched_name", "")}
        return {"status": "not_found", "doctor": None}

    doc = next((d for d in available_doctors
                if d["Doctor"] == match.get("matched_name")), None)
    status = "matched" if match["status"] == "auto_correct" else "confirm"
    return {"status": status, "doctor": doc,
            "matched_name": match.get("matched_name", "")}


def _pick_by_number(text: str, available: list) -> Optional[dict]:
    t = text.strip()
    if t.isdigit():
        idx = int(t) - 1
        if 0 <= idx < len(available):
            return available[idx]
    words = {
        "first": 0, "second": 1, "third": 2, "fourth": 3,
        "1st": 0, "2nd": 1, "3rd": 2, "4th": 3,
        "الأول": 0, "الثاني": 1, "الثالث": 2, "الرابع": 3,
        "اول": 0, "ثاني": 1, "ثالث": 2,
    }
    for word, idx in words.items():
        if re.search(rf'\b{re.escape(word)}\b', text, re.IGNORECASE):
            if idx < len(available):
                return available[idx]
    return None
