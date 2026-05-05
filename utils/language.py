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


# Egyptian / Levantine markers → Saudi (Khaleeji) replacements.
# The conversation prompt forbids these but the LLM occasionally drifts. This
# post-pass replaces the most common offenders so the user-facing reply stays
# consistent with the agent's documented persona.
# Order matters: longer / more specific patterns first to avoid partial hits.
_DIALECT_REPLACEMENTS = (
    # Future-tense Egyptian "ه/هـ" prefix → Saudi "راح" / "ب"
    (r"\bهنتواصل\b",   "راح نتواصل"),
    (r"\bهكلمك\b",     "راح أكلمك"),
    (r"\bهعمل\b",      "راح أعمل"),
    (r"\bهبعتلك\b",    "راح أبعتلك"),
    # Question / pronoun particles
    (r"\bازاي\b",       "كيف"),
    (r"\bإزاي\b",       "كيف"),
    (r"\bفين\b",        "وين"),
    (r"\bإيه\b",        "وش"),
    (r"\bدلوقتي\b",     "الحين"),
    (r"\bدلوقت\b",      "الحين"),
    # Verbs / wants
    (r"\bعايز\b",       "أبغى"),
    (r"\bعاوز\b",       "أبغى"),
    (r"\bعايزة\b",      "أبغى"),
    (r"\bعاوزة\b",      "أبغى"),
    (r"\bبدي\b",        "أبغى"),
    # Negation / qualifiers
    (r"\bمش\b",         "مو"),
    (r"\bأكتر\b",       "أكثر"),
    (r"\bاكتر\b",       "أكثر"),
    # Affirmatives / reactions
    (r"\bأيوه\b",       "إي"),
    (r"\bحلو\b",        "زين"),
    (r"\bكويس\b",       "زين"),
    (r"\bكده\b",        "كذا"),
    # "tomorrow" — the prompt prefers "بكره", penalises Egyptian "بكرا"
    (r"\bبكرا\b",       "بكره"),
)

import re as _re
_DIALECT_PATTERNS = [(_re.compile(p), repl) for p, repl in _DIALECT_REPLACEMENTS]


def sanitize_dialect(text: str, lang: str) -> str:
    """Replace banned Egyptian / Levantine markers in `text` with their Saudi
    (Khaleeji) equivalents. No-op for non-Arabic conversations."""
    if not text or lang != "ar":
        return text
    out = text
    for pat, repl in _DIALECT_PATTERNS:
        out = pat.sub(repl, out)
    return out
