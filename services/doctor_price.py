"""
Match doctors from the operational hospital DB (PhysicianEnName) to records in
the Dynamics CRM (servhub_doctornameen), and expose their walk-in / cash price.

Matching runs four passes (same philosophy as utils/fuzzy_match.py):
  1. exact match on fully-normalized name
  2. token_sort_ratio fuzzy match on full names
  3. fall back to matching on last name only
  4. fall back to matching on first name only
so that differences in spelling, word order, or dropped middle names between
the two systems don't hide the price.
"""
from typing import Optional

from rapidfuzz import fuzz, process

from db.crm_database import fetch_all_doctor_prices

# Price-lookup threshold is intentionally looser than the doctor-selection
# threshold — a 75-point token match is solid enough for a read-only price
# lookup, and both systems often have noisy middle-name variance.
CRM_MATCH_THRESHOLD = 75

_lookup_cache: dict = {"by_full": None, "by_last": None, "by_first": None, "cache_key": None}


def _normalize(name: str) -> str:
    return " ".join((name or "").lower().strip().split())


_TITLE_PREFIXES = ("dr. ", "dr ", "doctor ", "doc ", "prof. ", "prof ", "mr. ", "mr ")


def _strip_title(name: str) -> str:
    n = _normalize(name)
    for prefix in _TITLE_PREFIXES:
        if n.startswith(prefix):
            return n[len(prefix):].strip()
    return n


def _build_indexes(crm_rows: list[dict]) -> tuple[dict, dict, dict]:
    by_full: dict = {}
    by_last: dict = {}
    by_first: dict = {}
    for row in crm_rows:
        full = _strip_title(row.get("DoctorEn") or "")
        if not full:
            continue
        by_full.setdefault(full, row)
        parts = full.split()
        if not parts:
            continue
        # First partial pass — first name
        by_first.setdefault(parts[0], row)
        # Last name
        if len(parts) >= 2:
            by_last.setdefault(parts[-1], row)
    return by_full, by_last, by_first


def _get_indexes() -> tuple[dict, dict, dict]:
    crm_rows = fetch_all_doctor_prices()
    cache_key = id(crm_rows)
    if _lookup_cache["cache_key"] != cache_key:
        b1, b2, b3 = _build_indexes(crm_rows)
        _lookup_cache["by_full"] = b1
        _lookup_cache["by_last"] = b2
        _lookup_cache["by_first"] = b3
        _lookup_cache["cache_key"] = cache_key
    return (
        _lookup_cache["by_full"] or {},
        _lookup_cache["by_last"] or {},
        _lookup_cache["by_first"] or {},
    )


def _fuzzy_pick(query: str, index: dict, threshold: int) -> Optional[dict]:
    if not index or not query:
        return None
    keys = list(index.keys())
    result = process.extractOne(query, keys, scorer=fuzz.token_sort_ratio)
    if result is None:
        return None
    matched, score, _ = result
    if score >= threshold:
        return index[matched]
    return None


def find_crm_doctor(doctor_en: str) -> Optional[dict]:
    """
    Look up a CRM doctor record by English name. Returns the CRM row or None.
    Runs four progressively-looser passes.
    """
    if not doctor_en:
        return None

    by_full, by_last, by_first = _get_indexes()
    if not by_full:
        return None

    target = _strip_title(doctor_en)

    # Pass 1: exact full-name match
    if target in by_full:
        return by_full[target]

    # Pass 2: fuzzy full-name match
    hit = _fuzzy_pick(target, by_full, CRM_MATCH_THRESHOLD)
    if hit:
        return hit

    parts = target.split()
    if not parts:
        return None

    # Pass 3: last-name match (catches "Ahmed Marzban" ~ "A. M. Marzban")
    if len(parts) >= 2:
        last = parts[-1]
        if last in by_last:
            return by_last[last]
        hit = _fuzzy_pick(last, by_last, CRM_MATCH_THRESHOLD)
        if hit:
            return hit

    # Pass 4: first-name fallback
    first = parts[0]
    if first in by_first:
        return by_first[first]
    return _fuzzy_pick(first, by_first, CRM_MATCH_THRESHOLD)


def get_walk_in_price(doctor_en: str) -> Optional[float]:
    """Return the walk-in (cash) price for the given doctor, or None."""
    row = find_crm_doctor(doctor_en)
    if not row:
        return None
    price = row.get("WalkInPrice")
    if price is None:
        return None
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def enrich_doctors_with_prices(doctors: list[dict]) -> list[dict]:
    """Attach WalkInPrice to each doctor dict in the list (mutates and returns)."""
    if not doctors:
        return doctors
    for doc in doctors:
        if "WalkInPrice" in doc and doc["WalkInPrice"] is not None:
            continue
        price = get_walk_in_price(doc.get("Doctor", ""))
        doc["WalkInPrice"] = price
    return doctors
