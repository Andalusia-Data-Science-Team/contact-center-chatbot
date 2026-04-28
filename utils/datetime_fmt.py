# utils/datetime_fmt.py
import re
from datetime import date, datetime, timedelta


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── Arabic day and month names ──
_AR_DAYS = {
    "Monday": "الإثنين", "Tuesday": "الثلاثاء", "Wednesday": "الأربعاء",
    "Thursday": "الخميس", "Friday": "الجمعة", "Saturday": "السبت", "Sunday": "الأحد",
}

_AR_MONTHS = {
    "January": "يناير", "February": "فبراير", "March": "مارس",
    "April": "أبريل", "May": "مايو", "June": "يونيو",
    "July": "يوليو", "August": "أغسطس", "September": "سبتمبر",
    "October": "أكتوبر", "November": "نوفمبر", "December": "ديسمبر",
}

# ── Arabic numeral conversion ──
_EN_TO_AR_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def _to_arabic_numerals(text: str) -> str:
    """Convert English digits to Arabic-Indic numerals."""
    return text.translate(_EN_TO_AR_DIGITS)


def format_time(t, lang: str = "en") -> str:
    """Format a time value. Arabic returns ٠٩:٤٥ مساءً, English returns 09:45 PM."""
    if t is None:
        return "N/A"
    if isinstance(t, str):
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                t = datetime.strptime(t, fmt).time()
                break
            except ValueError:
                continue
        else:
            return t

    if lang == "ar":
        h = t.hour
        m = t.minute
        if h == 0:
            period = "صباحاً"
            h12 = 12
        elif h < 12:
            period = "صباحاً"
            h12 = h
        elif h == 12:
            period = "مساءً"
            h12 = 12
        else:
            period = "مساءً"
            h12 = h - 12
        time_str = f"{h12}:{m:02d} {period}"
        return _to_arabic_numerals(time_str)

    return t.strftime("%I:%M %p")


def format_date(d, lang: str = "en") -> str:
    """Format a date. Arabic returns 'اليوم', 'بكرا', or 'الإثنين، ١٠ مارس'."""
    if d is None:
        return "N/A"
    if isinstance(d, str):
        try:
            d = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            return d
    today = date.today()

    if lang == "ar":
        if d == today:
            return "اليوم"
        if d == today + timedelta(days=1):
            return "بكرا"
        day_name = _AR_DAYS.get(d.strftime("%A"), d.strftime("%A"))
        month_name = _AR_MONTHS.get(d.strftime("%B"), d.strftime("%B"))
        day_num = _to_arabic_numerals(str(d.day))
        return f"{day_name}، {day_num} {month_name}"

    if d == today:
        return "Today"
    if d == today + timedelta(days=1):
        return "Tomorrow"
    return d.strftime("%A, %B %d")


def today_iso() -> str:
    """Return today's date as YYYY-MM-DD string."""
    return date.today().strftime("%Y-%m-%d")


_WEEKDAY_OFFSETS = {
    # English
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    # Arabic — both common spellings
    "الإثنين": 0, "الاثنين": 0, "الإتنين": 0, "الاتنين": 0,
    "الثلاثاء": 1, "الثلاثا": 1,
    "الأربعاء": 2, "الاربعاء": 2, "الأربعا": 2, "الاربعا": 2,
    "الخميس": 3,
    "الجمعة": 4, "الجمعه": 4,
    "السبت": 5,
    "الأحد": 6, "الاحد": 6,
}

_TOMORROW_WORDS = ("tomorrow", "غداً", "غدا", "بكرا", "بكره", "بكرة")
_TODAY_WORDS = ("today", "اليوم", "النهاردة", "النهارده")
_DAY_AFTER_WORDS = ("day after tomorrow", "بعد غد", "بعد غداً", "بعد بكرا", "بعد بكره")


def resolve_relative_date(raw: str) -> str:
    """Convert 'tomorrow', 'بكرا', 'يوم الخميس', etc. to YYYY-MM-DD.

    Matches keywords inside longer phrases — patients say "بكره ان شاء الله"
    or "بكرا إن شاء الله يكون مناسباً", not just "بكرا" by itself.
    Pass through unchanged if no keyword found.
    """
    from datetime import date, timedelta

    if not raw:
        return raw

    today = date.today()
    r = raw.strip().lower()

    if _ISO_DATE_RE.match(r):
        return r

    # Day-after first (longer phrases beat shorter ones — "بعد بكرا" must match
    # before "بكرا" matches the same string).
    for kw in _DAY_AFTER_WORDS:
        if kw in r:
            return (today + timedelta(days=2)).strftime("%Y-%m-%d")

    for kw in _TOMORROW_WORDS:
        if kw in r:
            return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    for kw in _TODAY_WORDS:
        if kw in r:
            return today.strftime("%Y-%m-%d")

    # Weekday names → next occurrence of that weekday (today counts only if
    # explicitly mentioned alongside "today", which the loop above already
    # handled).
    for name, target_dow in _WEEKDAY_OFFSETS.items():
        if name in r:
            current_dow = today.weekday()
            days_ahead = (target_dow - current_dow) % 7
            if days_ahead == 0:
                days_ahead = 7  # "Thursday" said on Thursday → next Thursday
            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    return raw
