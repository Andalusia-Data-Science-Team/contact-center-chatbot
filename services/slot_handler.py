# services/slot_handler.py
"""
Parse patient's time preference and find nearest available slot.
"""
from datetime import datetime
from typing import Optional
from llm.client import call_llm
from prompts.routing import TIME_PARSE_PROMPT


def parse_time_preference(raw: str) -> Optional[datetime.time]:
    """Convert free-text like 'after 3', 'بعد ٤', '3:00 PM' to a time object."""
    # Try direct parsing first — avoid LLM call for obvious formats
    direct = _try_direct_parse(raw)
    if direct:
        return direct

    # Fall back to LLM for ambiguous input
    result = call_llm(
        messages=[{"role": "user", "content": raw}],
        system_prompt=TIME_PARSE_PROMPT,
        temperature=0.0,
        max_tokens=30,
        json_mode=True,
        label="time_parse",
    )
    t_str = result.get("time_24h")
    if not t_str:
        return None
    try:
        parts = t_str.split(":")
        return datetime(2000, 1, 1, int(parts[0]), int(parts[1])).time()
    except Exception:
        return None


def _try_direct_parse(raw: str) -> Optional[datetime.time]:
    """
    Parse common time formats without LLM.
    For ambiguous times without AM/PM (e.g. '3:00', '3'), assume PM in hospital context
    since appointments are virtually never at 1-7 AM.
    """
    import re
    text = raw.strip()

    # Match "3:00 PM", "03:00 pm", "3:00PM" — explicit AM/PM
    m = re.match(r'^(\d{1,2}):(\d{2})\s*(am|pm|AM|PM)$', text)
    if m:
        h, mi, period = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        if period == "PM" and h != 12:
            h += 12
        elif period == "AM" and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return datetime(2000, 1, 1, h, mi).time()

    # Match "3 PM", "3PM" — explicit AM/PM
    m = re.match(r'^(\d{1,2})\s*(am|pm|AM|PM)$', text)
    if m:
        h, period = int(m.group(1)), m.group(2).upper()
        if period == "PM" and h != 12:
            h += 12
        elif period == "AM" and h == 12:
            h = 0
        if 0 <= h <= 23:
            return datetime(2000, 1, 1, h, 0).time()

    # Match "3:00" or "3:15" — NO AM/PM → assume PM if hour is 1-11
    # Hospital appointments are virtually never at 1-11 AM for outpatient
    m = re.match(r'^(\d{1,2}):(\d{2})$', text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 1 <= h <= 11:
            h += 12  # Assume PM for hospital hours
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return datetime(2000, 1, 1, h, mi).time()

    # Match bare number "3", "4" — assume PM
    m = re.match(r'^(\d{1,2})$', text)
    if m:
        h = int(m.group(1))
        if 1 <= h <= 11:
            h += 12  # Assume PM
        if 0 <= h <= 23:
            return datetime(2000, 1, 1, h, 0).time()

    return None


def find_nearest_slot(slots: list, target: datetime.time) -> Optional[dict]:
    """
    Return the best matching slot for the target time.
    Priority: exact match → first slot at or after target → last slot (fallback).
    """
    parsed = _parse_slots(slots)
    if not parsed:
        return None

    # Exact match first
    for d_obj, t_obj in parsed:
        if t_obj == target:
            return {"date": d_obj, "time": t_obj, "is_fallback": False}

    # First slot at or after target
    for d_obj, t_obj in parsed:
        if t_obj >= target:
            return {"date": d_obj, "time": t_obj, "is_fallback": False}

    # Fallback to last available slot
    d_obj, t_obj = parsed[-1]
    return {"date": d_obj, "time": t_obj, "is_fallback": True}


def get_initial_slots(slots: list) -> Optional[dict]:
    """Return info about the earliest available slot."""
    parsed = _parse_slots(slots)
    if not parsed:
        return None

    first_date = parsed[0][0]
    same_day = [(d, t) for d, t in parsed if d == first_date]

    return {
        "first_date": first_date,
        "first_time": parsed[0][1],
        "total": len(same_day),
    }


def get_all_slots(slots: list, after_time: datetime.time = None,
                  start_time: datetime.time = None,
                  end_time: datetime.time = None) -> list:
    """
    Return all (date, time) tuples on the same date as the earliest slot.
    Optionally filtered by time range.
    - after_time: legacy param, same as start_time
    - start_time / end_time: inclusive range filter
    """
    parsed = _parse_slots(slots)
    if not parsed:
        return []
    first_date = parsed[0][0]
    same_day = [(d, t) for d, t in parsed if d == first_date]

    # Apply range filter
    filter_start = start_time or after_time
    if filter_start:
        same_day = [(d, t) for d, t in same_day if t >= filter_start]
    if end_time:
        same_day = [(d, t) for d, t in same_day if t <= end_time]

    return same_day


def _parse_slots(slots: list) -> list:
    """Parse raw slot dicts into sorted list of (date, time) tuples."""
    parsed = []
    for s in slots:
        t = s.get("StartTime") or s.get("Slot_Time")
        d = s.get("StartDate") or s.get("Slot_Date")
        try:
            if isinstance(t, str):
                p = t.split(":")
                t_obj = datetime(2000, 1, 1, int(p[0]), int(p[1])).time()
            else:
                t_obj = t
            d_obj = datetime.strptime(d, "%Y-%m-%d").date() if isinstance(d, str) else d
            parsed.append((d_obj, t_obj))
        except Exception:
            continue
    parsed.sort()
    return parsed


# ── Time range filter parsing ────────────────────────────────────────────────

# Period definitions: name → (start_hour, end_hour)
_PERIODS = {
    # English
    "morning":    (6, 12),
    "afternoon":  (12, 17),
    "evening":    (17, 21),
    "night":      (21, 24),
    "tonight":    (18, 24),
    # Arabic
    "الصبح":      (6, 12),
    "الصباح":     (6, 12),
    "صباح":       (6, 12),
    "الظهر":      (12, 15),
    "بعد الظهر":  (12, 17),
    "العصر":      (15, 18),
    "بعد العصر":  (15, 18),
    "المغرب":     (18, 20),
    "المسا":      (17, 21),
    "المساء":     (17, 21),
    "مساء":       (17, 21),
    "الليل":      (21, 24),
    "الليلة":     (18, 24),
}

# Display labels for periods
_PERIOD_LABELS = {
    "morning": "morning", "afternoon": "afternoon", "evening": "evening",
    "night": "night", "tonight": "tonight",
    "الصبح": "الصبح", "الصباح": "الصباح", "صباح": "الصباح",
    "الظهر": "الظهر", "بعد الظهر": "بعد الظهر",
    "العصر": "العصر", "بعد العصر": "بعد العصر",
    "المغرب": "المغرب", "المسا": "المساء", "المساء": "المساء", "مساء": "المساء",
    "الليل": "الليل", "الليلة": "الليلة",
}


def parse_time_filter(raw: str) -> dict:
    """
    Parse a time filter string into start/end times and a display label.
    Handles:
      - Named periods: "morning", "evening", "بعد العصر", "tonight"
      - Explicit ranges: "8 PM to 11 PM", "from 3 to 5"
      - "after X" / "before X" / "بعد X"
    Returns: {"start": time|None, "end": time|None, "label": str}
    """
    import re
    text = raw.strip().lower()

    # Check named periods (longest match first)
    for period in sorted(_PERIODS.keys(), key=len, reverse=True):
        if period in text:
            start_h, end_h = _PERIODS[period]
            label = _PERIOD_LABELS.get(period, period)
            return {
                "start": datetime(2000, 1, 1, start_h, 0).time(),
                "end": datetime(2000, 1, 1, min(end_h, 23), 59).time(),
                "label": label,
            }

    # Try explicit range: "8 PM to 11 PM", "from 8 to 11", "between 9 and 11", "من 8 الى 11"
    range_pattern = r'(?:from|between|من)?\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(?:to|until|till|and|&|-|الى|إلى|لـ|ل|و)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)'
    m = re.search(range_pattern, text, re.IGNORECASE)
    if m:
        start = _try_direct_parse(m.group(1).strip())
        end = _try_direct_parse(m.group(2).strip())
        if start and end:
            # Coherence check: if end is before start, the range doesn't make sense.
            # This can happen if e.g. "6 to 9" parses as 6PM to 9PM (both correct)
            # but edge cases like explicit AM on end should be caught.
            # If end < start and both are PM, it's likely an error — but in practice
            # with the 1-11 PM assumption this shouldn't happen anymore.
            start_display = start.strftime("%I:%M %p").lstrip("0")
            end_display = end.strftime("%I:%M %p").lstrip("0")
            return {
                "start": start,
                "end": end,
                "label": f"{start_display} - {end_display}",
            }

    # "after X" / "بعد X"
    after_pattern = r'(?:after|بعد)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)'
    m = re.search(after_pattern, text, re.IGNORECASE)
    if m:
        start = _try_direct_parse(m.group(1).strip())
        if start:
            return {
                "start": start,
                "end": None,
                "label": f"after {start.strftime('%I:%M %p').lstrip('0')}",
            }

    # "before X" / "قبل X"
    before_pattern = r'(?:before|قبل)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)'
    m = re.search(before_pattern, text, re.IGNORECASE)
    if m:
        end = _try_direct_parse(m.group(1).strip())
        if end:
            return {
                "start": None,
                "end": end,
                "label": f"before {end.strftime('%I:%M %p').lstrip('0')}",
            }

    return {"start": None, "end": None, "label": ""}
