# services/doctor_selector.py
"""
Fetch doctors from DB and fuzzy-match patient input to a doctor name.
"""
import re
from typing import Optional
from db.database import (
    query_availability_with_fallback,
    query_doctor_slots_with_fallback,
    aggregate_doctor_slots,
)
from utils.fuzzy_match import fuzzy_match_doctor

NAME_STOPWORDS = {
    "please", "book", "with", "dr", "dr.", "de", "doctor", "doc", "want", "i", "the", "me", "can",
    "you", "yes", "okay", "ok", "sure", "like", "a", "an", "appointment", "would",
    "دكتور", "الدكتور", "د.", "د", "مع", "أريد", "ممكن", "أبغى", "ابغى", "حجز", "موعد",
}


def fetch_doctors(specialty: str, lang: str, preferred_date: str = None) -> tuple:
    """
    Query DB for doctors in the given specialty.
    Returns (doctor_list, date_used). Filters out doctors with no slot data.
    Note: routing now always returns EN specialty names, so try EN first regardless of lang.
    """
    # Always try EN first (routing returns EN names)
    rows, used_date = query_availability_with_fallback(
        specialty_en=specialty, specialty_ar=None
    )
    # Fallback: try as AR name in case it's an AR specialty somehow
    if not rows:
        rows, used_date = query_availability_with_fallback(
            specialty_en=None, specialty_ar=specialty
        )

    doctors = aggregate_doctor_slots(rows)
    doctors = [d for d in doctors if d.get("Nearest_Date") and d.get("Nearest_Time")]
    return doctors, used_date


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
        return {"status": "matched", "doctor": by_number}

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
