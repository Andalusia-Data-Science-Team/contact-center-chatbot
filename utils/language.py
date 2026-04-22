# utils/language.py
import re

ARABIC_PATTERN = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+')


def detect_language(text: str) -> str:
    """Return 'ar' if Arabic characters found, else 'en'."""
    if ARABIC_PATTERN.search(text):
        return "ar"
    return "en"


# Arabic-Indic digits → ASCII digits (٠-٩ → 0-9)
_AR_DIGIT_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# Hamza variants on alif and ya → bare forms. ة → ه. ى → ي.
_HAMZA_TRANS = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا",
    "ؤ": "و",
    "ئ": "ي", "ى": "ي",
    "ة": "ه",
})


def normalize_ar(text: str) -> str:
    """
    Normalize Arabic text for equality / substring matching:
    - Strip hamza (أ/إ/آ → ا, ؤ → و, ئ/ى → ي)
    - Normalize ta-marbuta (ة → ه)
    - Convert Arabic-Indic digits to ASCII (٠-٩ → 0-9)
    - Lowercase + strip
    Does NOT remove diacritics (tashkeel) — user input rarely has them.
    """
    if not text:
        return ""
    return text.translate(_HAMZA_TRANS).translate(_AR_DIGIT_TRANS).lower().strip()


def to_ascii_digits(text: str) -> str:
    """Convert Arabic-Indic digits to ASCII digits without other normalization."""
    return text.translate(_AR_DIGIT_TRANS) if text else text
