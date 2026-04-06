# utils/emergency.py
from config.constants import EMERGENCY_KEYWORDS


def detect_emergency(text: str) -> bool:
    """Check if text contains any emergency keywords."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in EMERGENCY_KEYWORDS)
