# utils/language.py
import re

ARABIC_PATTERN = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+')


def detect_language(text: str) -> str:
    """Return 'ar' if Arabic characters found, else 'en'."""
    if ARABIC_PATTERN.search(text):
        return "ar"
    return "en"
