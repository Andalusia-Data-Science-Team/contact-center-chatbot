# utils/fuzzy_match.py
from rapidfuzz import process, fuzz
from config.settings import FUZZY_AUTO_CORRECT_THRESHOLD, FUZZY_CONFIRM_THRESHOLD
from utils.language import normalize_ar


def fuzzy_match_doctor(input_name: str, doctor_list: list[str]) -> dict:
    """
    Match input_name against a list of doctor names.
    Tries: full names → first names → last names → any name part.
    Returns: {"status": "auto_correct"|"confirm"|"not_found", "matched_name": str|None, "score": float}

    Arabic input is normalized (أ/إ/آ → ا, ة → ه, ى → ي) before matching so
    hamza/ya/ta-marbuta variants score equivalently. The returned matched_name
    is the ORIGINAL string from doctor_list so callers can look up the doctor
    record by their stored name.
    """
    if not doctor_list or not input_name:
        return {"status": "not_found", "matched_name": None, "score": 0.0}

    norm_input = normalize_ar(input_name)
    if not norm_input:
        return {"status": "not_found", "matched_name": None, "score": 0.0}

    # Pass 1: match against full names
    full_norm_to_orig = {}
    for full in doctor_list:
        norm = normalize_ar(full)
        if norm and norm not in full_norm_to_orig:
            full_norm_to_orig[norm] = full

    result = _best_match(norm_input, list(full_norm_to_orig.keys()))
    if result["status"] != "not_found":
        result["matched_name"] = full_norm_to_orig[result["matched_name"]]
        return result

    # Pass 2: match against first names only
    first_to_full = {}
    for full in doctor_list:
        first = full.split()[0] if full.strip() else full
        first_norm = normalize_ar(first)
        if first_norm and first_norm not in first_to_full:
            first_to_full[first_norm] = full

    result = _best_match(norm_input, list(first_to_full.keys()))
    if result["status"] != "not_found":
        result["matched_name"] = first_to_full[result["matched_name"]]
        return result

    # Pass 3: match against last names (e.g. "Badwy" → "Mahmoud ElBadawy")
    last_to_full = {}
    for full in doctor_list:
        parts = full.split()
        if len(parts) >= 2:
            last_norm = normalize_ar(parts[-1])
            if last_norm and last_norm not in last_to_full:
                last_to_full[last_norm] = full

    if last_to_full:
        result = _best_match(norm_input, list(last_to_full.keys()))
        if result["status"] != "not_found":
            result["matched_name"] = last_to_full[result["matched_name"]]
            return result

    # Pass 4: match against any name part (e.g. "Badwy" → part of "Mahmoud ElBadawy")
    part_to_full = {}
    for full in doctor_list:
        for part in full.split():
            part_norm = normalize_ar(part)
            if len(part_norm) >= 3 and part_norm not in part_to_full:
                part_to_full[part_norm] = full

    if part_to_full:
        result = _best_match(norm_input, list(part_to_full.keys()))
        if result["status"] != "not_found":
            result["matched_name"] = part_to_full[result["matched_name"]]
            return result

    return {"status": "not_found", "matched_name": None, "score": 0.0}


def _best_match(query: str, candidates: list[str]) -> dict:
    """Run fuzzy match and classify the result.

    Uses token_set_ratio so a multi-token query like "احمد عامر" matches the
    candidate whose tokens contain the query's tokens — without being penalised
    for the candidate's extra tokens. token_sort_ratio penalised long correct
    matches so heavily that "احمد عامر" was scoring higher against the shorter
    "احمد شعبان" than against the correct "احمد عامر سعيد الصيعري".
    """
    result = process.extractOne(
        query,
        candidates,
        scorer=fuzz.token_set_ratio,
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
