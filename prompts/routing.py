# prompts/routing.py

ROUTE_PROMPT_EN = """You are a medical triage specialist at Andalusia Hospital.

Patient complaint: "{complaint}"
Patient age: {age} years old
Additional info from patient: "{clarification}"

Pick the SINGLE best matching specialty from this exact list (copy name exactly):
{specialties}

Rules:
- Age < {ped_threshold}: pick a pediatric specialty when one fits.
  General illness in child → "General Pediatrics"
  Dental in child → "Pedodontic"
  Heart in child → "Pediatric Cardiology"
  Bone/joint in child → "Pediatric Orthopedics"
  Nerve in child → "Pediatric Neurology"
  Surgery in child → "Pediatric Surgery"

Return ONLY valid JSON:
{{"specialty": "<exact name from list>", "confidence": <0.0-1.0>}}"""


ROUTE_PROMPT_AR = """أنت متخصص فرز في مستشفى الأندلس.

شكوى المريض: "{complaint}"
عمر المريض: {age} سنة
معلومة إضافية من المريض: "{clarification}"

اختر أنسب تخصص واحد من هذه القائمة (انسخ الاسم بالضبط بالإنجليزي):
{specialties}

القواعد:
- عمر أقل من {ped_threshold}: اختر تخصص أطفال عند الإمكان.
  أعراض عامة عند طفل → "General Pediatrics"
  أسنان عند طفل → "Pedodontic"
  قلب عند طفل → "Pediatric Cardiology"
  عظام عند طفل → "Pediatric Orthopedics"
  أعصاب عند طفل → "Pediatric Neurology"
  جراحة عند طفل → "Pediatric Surgery"

أعد JSON فقط (الاسم يكون بالإنجليزي بالضبط من القائمة):
{{"specialty": "<exact English name from list>", "confidence": <0.0-1.0>}}"""


TIME_PARSE_PROMPT = """Extract a clock time from this message. Return 24h HH:MM format.

Patient says → return
"after 3" → 15:00
"after 3 PM" → 15:00
"around 4" → 16:00
"2 PM" → 14:00
"morning" → 08:00
"afternoon" → 13:00
"evening" → 18:00
"night" → 20:00
"بعد ٣" → 15:00
"بعد الثلاثة" → 15:00
"مساء" → 18:00
"الصبح" → 08:00
"بعد الظهر" → 13:00
"العصر" → 16:00
"الليل" → 20:00
"حوالي ٦" → 18:00
"أي وقت بعد ٥" → 17:00

Return ONLY: {"time_24h": "HH:MM"} or {"time_24h": null} if no time found."""
