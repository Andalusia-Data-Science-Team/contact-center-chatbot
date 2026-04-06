# utils/datetime_fmt.py
from datetime import date, datetime, timedelta


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


def resolve_relative_date(raw: str) -> str:
    """Convert 'tomorrow', 'بكرا', etc. to YYYY-MM-DD. Pass through if already formatted."""
    from datetime import date, timedelta

    today = date.today()
    r = raw.strip().lower()

    tomorrow_words = {"tomorrow", "غداً", "غدا", "بكرا", "بكره"}
    day_after_words = {"day after tomorrow", "بعد غد", "بعد غداً"}

    if r in tomorrow_words:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if r in day_after_words:
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")
    if r == "today":
        return today.strftime("%Y-%m-%d")
    return raw
