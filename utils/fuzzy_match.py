# utils/fuzzy_match.py
from rapidfuzz import process, fuzz
from config.settings import FUZZY_AUTO_CORRECT_THRESHOLD, FUZZY_CONFIRM_THRESHOLD


def fuzzy_match_doctor(input_name: str, doctor_list: list[str]) -> dict:
    """
    Match input_name against a list of doctor names.
    Tries: full names → first names → last names → any name part.
    Returns: {"status": "auto_correct"|"confirm"|"not_found", "matched_name": str|None, "score": float}
    """
    if not doctor_list or not input_name:
        return {"status": "not_found", "matched_name": None, "score": 0.0}

    # Pass 1: match against full names
    result = _best_match(input_name, doctor_list)
    if result["status"] != "not_found":
        return result

    # Pass 2: match against first names only
    first_to_full = {}
    for full in doctor_list:
        first = full.split()[0] if full.strip() else full
        if first not in first_to_full:
            first_to_full[first] = full

    first_names = list(first_to_full.keys())
    result = _best_match(input_name, first_names)
    if result["status"] != "not_found":
        result["matched_name"] = first_to_full[result["matched_name"]]
        return result

    # Pass 3: match against last names (e.g. "Badwy" → "Mahmoud ElBadawy")
    last_to_full = {}
    for full in doctor_list:
        parts = full.split()
        if len(parts) >= 2:
            last = parts[-1]
            if last not in last_to_full:
                last_to_full[last] = full

    if last_to_full:
        last_names = list(last_to_full.keys())
        result = _best_match(input_name, last_names)
        if result["status"] != "not_found":
            result["matched_name"] = last_to_full[result["matched_name"]]
            return result

    # Pass 4: match against any name part (e.g. "Badwy" → part of "Mahmoud ElBadawy")
    part_to_full = {}
    for full in doctor_list:
        for part in full.split():
            if len(part) >= 3 and part not in part_to_full:
                part_to_full[part] = full

    if part_to_full:
        all_parts = list(part_to_full.keys())
        result = _best_match(input_name, all_parts)
        if result["status"] != "not_found":
            result["matched_name"] = part_to_full[result["matched_name"]]
            return result

    return {"status": "not_found", "matched_name": None, "score": 0.0}


def _best_match(query: str, candidates: list[str]) -> dict:
    """Run fuzzy match and classify the result."""
    result = process.extractOne(
        query,
        candidates,
        scorer=fuzz.token_sort_ratio,
    )

    if result is None:
        return {"status": "not_found", "matched_name": None, "score": 0.0}

    matched_name, score, _ = result

    if score >= FUZZY_AUTO_CORRECT_THRESHOLD:
        return {"status": "auto_correct", "matched_name": matched_name, "score": score}
    elif score >= FUZZY_CONFIRM_THRESHOLD:
        return {"status": "confirm", "matched_name": matched_name, "score": score}
    else:
        return {"status": "not_found", "matched_name": None, "score": score}
