# services/router.py
"""
Medical specialty routing — enhanced for accuracy.

Strategy:
  1. RULE-BASED PRE-FILTER: symptom keywords → narrow to 3-5 candidate specialties
  2. LLM ROUTING: pick the best from the narrowed list (not 75 options)
  3. VALIDATION: confidence check + fallback to broader list if needed

This is much more accurate than throwing 75 specialties at the LLM and hoping.
"""
from llm.client import call_llm
from config.constants import (
    SPECIALTIES_EN, SPECIALTIES_AR,
    PEDIATRIC_SPECIALTIES_EN, PEDIATRIC_SPECIALTIES_AR,
)
from config.settings import PEDIATRIC_AGE_THRESHOLD
from prompts.routing import ROUTE_PROMPT_EN, ROUTE_PROMPT_AR

# ══════════════════════════════════════════════════════════════════════════════
# SYMPTOM → SPECIALTY MAPPING
# Maps symptom keywords to candidate specialties (EN names, mapped to AR below)
# Each entry: keyword → list of possible specialties, ordered by likelihood
# ══════════════════════════════════════════════════════════════════════════════

SYMPTOM_MAP = {
    # ── Head & Brain ──
    "headache":          ["Neurology", "Internal Medicine"],
    "migraine":          ["Neurology"],
    "dizziness":         ["Neurology", "E.N.T."],
    "seizure":           ["Neurology"],
    "memory":            ["Neurology", "Neuropsychiatry"],
    "stroke":            ["Neurology", "Interventional Neurology"],
    "brain":             ["Neurology", "Neuro Surgery"],
    "nerve":             ["Neurology", "Neuro Surgery"],
    "numbness":          ["Neurology", "Orthopedics"],
    "tingling":          ["Neurology", "Orthopedics"],
    "paralysis":         ["Neurology", "Neuro Surgery"],

    # ── Eyes ──
    "eye":               ["Ophthalmology"],
    "vision":            ["Ophthalmology"],
    "blurry":            ["Ophthalmology"],
    "glasses":           ["Ophthalmology"],
    "cataract":          ["Ophthalmology"],
    "redness eye":       ["Ophthalmology"],

    # ── Ear, Nose, Throat ──
    "ear":               ["E.N.T."],
    "hearing":           ["E.N.T.", "Audiological medicine"],
    "nose":              ["E.N.T."],
    "sinus":             ["E.N.T."],
    "throat":            ["E.N.T."],
    "tonsil":            ["E.N.T."],
    "voice":             ["E.N.T."],
    "snoring":           ["E.N.T."],
    "swallowing":        ["E.N.T."],

    # ── Chest & Lungs ──
    "chest pain":        ["Cardiology", "Chest"],
    "heart":             ["Cardiology", "Interventional Cardiology"],
    "palpitation":       ["Cardiology"],
    "blood pressure":    ["Cardiology", "Internal Medicine"],
    "cholesterol":       ["Cardiology", "Internal Medicine"],
    "cough":             ["Chest", "E.N.T."],
    "breathing":         ["Chest", "Cardiology"],
    "asthma":            ["Chest"],
    "shortness of breath": ["Chest", "Cardiology"],
    "lung":              ["Chest"],

    # ── Stomach & Digestive ──
    "stomach":           ["Internal Medicine"],
    "abdomen":           ["Internal Medicine", "General Surgery"],
    "abdominal":         ["Internal Medicine", "General Surgery"],
    "nausea":            ["Internal Medicine"],
    "vomiting":          ["Internal Medicine"],
    "diarrhea":          ["Internal Medicine"],
    "constipation":      ["Internal Medicine"],
    "heartburn":         ["Internal Medicine"],
    "acid reflux":       ["Internal Medicine"],
    "liver":             ["Internal Medicine"],
    "gallbladder":       ["General Surgery", "Internal Medicine"],
    "hernia":            ["Hernia Surgery", "General Surgery"],
    "hemorrhoid":        ["General Surgery"],
    "colon":             ["General Surgery", "Internal Medicine"],
    "appendix":          ["General Surgery"],

    # ── Bones & Joints ──
    "bone":              ["Orthopedics"],
    "joint":             ["Orthopedics", "Rheumatology"],
    "knee":              ["Orthopedics"],
    "shoulder":          ["Orthopedics"],
    "back pain":         ["Orthopedics", "Spine Surgery"],
    "spine":             ["Spine Surgery", "Orthopedics"],
    "disc":              ["Spine Surgery", "Orthopedics"],
    "hip":               ["Orthopedics"],
    "ankle":             ["Orthopedics"],
    "wrist":             ["Orthopedics"],
    "hand pain":         ["Orthopedics"],
    "arm pain":          ["Orthopedics"],
    "leg pain":          ["Orthopedics"],
    "fracture":          ["Orthopedics"],
    "broken":            ["Orthopedics"],
    "arthritis":         ["Rheumatology", "Orthopedics"],
    "muscle":            ["Orthopedics", "Rheumatology"],
    "sports injury":     ["Orthopedics"],
    "swelling joint":    ["Rheumatology", "Orthopedics"],

    # ── Skin ──
    "skin":              ["Dermatology and Cosmatology"],
    "rash":              ["Dermatology and Cosmatology"],
    "acne":              ["Dermatology and Cosmatology"],
    "eczema":            ["Dermatology and Cosmatology"],
    "psoriasis":         ["Dermatology and Cosmatology"],
    "hair loss":         ["Dermatology and Cosmatology"],
    "itching":           ["Dermatology and Cosmatology", "Allergy & Immunology"],
    "mole":              ["Dermatology and Cosmatology"],
    "cosmetic":          ["Cosmetology", "Plastic Surgery"],
    "botox":             ["Cosmetology", "Dermatology and Cosmatology"],
    "filler":            ["Cosmetology", "Dermatology and Cosmatology"],

    # ── Urinary & Male ──
    "urine":             ["Urology"],
    "urinary":           ["Urology"],
    "kidney":            ["Nephrology", "Urology"],
    "kidney stone":      ["Urology"],
    "prostate":          ["Urology"],
    "bladder":           ["Urology"],
    "erectile":          ["Urology and Andrology", "Andrology"],
    "infertility male":  ["Andrology", "Urology and Andrology"],

    # ── Women's Health ──
    "pregnancy":         ["OBE & GYN"],
    "pregnant":          ["OBE & GYN"],
    "gynecol":           ["OBE & GYN"],
    "period":            ["OBE & GYN"],
    "menstrual":         ["OBE & GYN"],
    "ovary":             ["OBE & GYN"],
    "breast":            ["Breast Surgery", "OBE & GYN"],
    "breast lump":       ["Breast Surgery"],
    "infertility":       ["IVF", "OBE & GYN"],
    "ivf":               ["IVF"],

    # ── Children ──
    "child":             ["General Pediatrics"],
    "baby":              ["General Pediatrics"],
    "infant":            ["General Pediatrics"],
    "newborn":           ["General Pediatrics"],
    "fever child":       ["General Pediatrics"],
    "vaccination":       ["General Pediatrics"],

    # ── Mental Health ──
    "depression":        ["Psychiatry"],
    "anxiety":           ["Psychiatry"],
    "sleep":             ["Psychiatry", "Neurology"],
    "insomnia":          ["Psychiatry", "Neurology"],
    "stress":            ["Psychiatry"],
    "panic":             ["Psychiatry"],
    "mental":            ["Psychiatry"],

    # ── Teeth & Dental ──
    "tooth":             ["Dental Services"],
    "teeth":             ["Dental Services"],
    "dental":            ["Dental Services"],
    "gum":               ["Dental Services"],
    "braces":            ["Orthodontic"],
    "root canal":        ["Endodontic"],
    "crown":             ["Dental Services"],
    "implant dental":    ["Dental Services"],
    "wisdom tooth":      ["Oral Surgery"],
    "jaw":               ["Maxillofacial Surgery", "Oral Surgery"],

    # ── Hormones & Endocrine ──
    "diabetes":          ["Endocrinology", "Internal Medicine"],
    "thyroid":           ["Endocrinology"],
    "hormone":           ["Endocrinology"],
    "sugar":             ["Endocrinology", "Internal Medicine"],
    "weight":            ["Nutrition", "Bariatric Surgery", "Endocrinology"],
    "obesity":           ["Bariatric Surgery", "Nutrition"],
    "diet":              ["Nutrition"],

    # ── Blood & Immune ──
    "anemia":            ["Hematology", "Internal Medicine"],
    "blood disorder":    ["Hematology"],
    "allergy":           ["Allergy & Immunology"],
    "immune":            ["Allergy & Immunology"],
    "cancer":            ["Medical Oncology", "Surgical Oncology"],
    "tumor":             ["Medical Oncology", "Surgical Oncology"],

    # ── General ──
    "fever":             ["Internal Medicine", "General Pediatrics"],
    "fatigue":           ["Internal Medicine", "Endocrinology"],
    "checkup":           ["Family Medicine", "Internal Medicine"],
    "general":           ["Family Medicine", "Internal Medicine"],
    "physical therapy":  ["Physiotherapy"],
    "rehabilitation":    ["Physiotherapy"],
}

# Arabic symptom keywords mapping to the same English specialty names
SYMPTOM_MAP_AR = {
    # ── رأس وأعصاب ──
    "صداع":              ["Neurology", "Internal Medicine"],
    "شقيقة":             ["Neurology"],
    "دوخة":              ["Neurology", "E.N.T."],
    "تشنج":              ["Neurology"],
    "أعصاب":             ["Neurology", "Neuro Surgery"],
    "تنميل":             ["Neurology", "Orthopedics"],
    "خدر":               ["Neurology", "Orthopedics"],
    "مخ":                ["Neurology", "Neuro Surgery"],

    # ── عيون ──
    "عين":               ["Ophthalmology"],
    "عيون":              ["Ophthalmology"],
    "نظر":               ["Ophthalmology"],
    "بصر":               ["Ophthalmology"],

    # ── أنف وأذن وحنجرة ──
    "أذن":               ["E.N.T."],
    "اذن":               ["E.N.T."],
    "سمع":               ["E.N.T.", "Audiological medicine"],
    "أنف":               ["E.N.T."],
    "انف":               ["E.N.T."],
    "جيوب":              ["E.N.T."],
    "حنجرة":             ["E.N.T."],
    "لوز":               ["E.N.T."],
    "بلع":               ["E.N.T."],
    "شخير":              ["E.N.T."],
    "حلق":               ["E.N.T."],
    "زور":               ["E.N.T."],

    # ── صدر وقلب ──
    "ألم صدر":           ["Cardiology", "Chest"],
    "الم صدر":           ["Cardiology", "Chest"],
    "قلب":               ["Cardiology"],
    "خفقان":             ["Cardiology"],
    "ضغط":               ["Cardiology", "Internal Medicine"],
    "كحة":               ["Chest", "E.N.T."],
    "سعال":              ["Chest"],
    "ربو":               ["Chest"],
    "ضيق تنفس":          ["Chest", "Cardiology"],
    "نفس":               ["Chest", "Cardiology"],
    "رئة":               ["Chest"],

    # ── بطن وهضم ──
    "معدة":              ["Internal Medicine"],
    "بطن":               ["Internal Medicine", "General Surgery"],
    "غثيان":             ["Internal Medicine"],
    "استفراغ":           ["Internal Medicine"],
    "إسهال":             ["Internal Medicine"],
    "امساك":             ["Internal Medicine"],
    "حرقان":             ["Internal Medicine"],
    "ارتجاع":            ["Internal Medicine"],
    "كبد":               ["Internal Medicine"],
    "مرارة":             ["General Surgery", "Internal Medicine"],
    "فتق":               ["Hernia Surgery", "General Surgery"],
    "بواسير":            ["General Surgery"],
    "قولون":             ["Internal Medicine"],
    "زائدة":             ["General Surgery"],

    # ── عظام ومفاصل ──
    "عظام":              ["Orthopedics"],
    "مفصل":              ["Orthopedics", "Rheumatology"],
    "ركبة":              ["Orthopedics"],
    "كتف":               ["Orthopedics"],
    "ظهر":               ["Orthopedics", "Spine Surgery"],
    "عمود فقري":         ["Spine Surgery", "Orthopedics"],
    "ديسك":              ["Spine Surgery", "Orthopedics"],
    "غضروف":             ["Spine Surgery", "Orthopedics"],
    "ورك":               ["Orthopedics"],
    "كاحل":              ["Orthopedics"],
    "معصم":              ["Orthopedics"],
    "يد":                ["Orthopedics"],
    "ذراع":              ["Orthopedics"],
    "رجل":               ["Orthopedics"],
    "ساق":               ["Orthopedics"],
    "كسر":               ["Orthopedics"],
    "روماتيزم":          ["Rheumatology", "Orthopedics"],
    "عضلات":             ["Orthopedics"],
    "عضل":               ["Orthopedics"],

    # ── جلد ──
    "جلد":               ["Dermatology and Cosmatology"],
    "جلدية":             ["Dermatology and Cosmatology"],
    "طفح":               ["Dermatology and Cosmatology"],
    "حبوب":              ["Dermatology and Cosmatology"],
    "اكزيما":            ["Dermatology and Cosmatology"],
    "صدفية":             ["Dermatology and Cosmatology"],
    "تساقط شعر":         ["Dermatology and Cosmatology"],
    "حكة":               ["Dermatology and Cosmatology", "Allergy & Immunology"],
    "شامة":              ["Dermatology and Cosmatology"],
    "تجميل":             ["Cosmetology", "Plastic Surgery"],

    # ── مسالك بولية ──
    "بول":               ["Urology"],
    "مسالك":             ["Urology"],
    "كلى":               ["Nephrology", "Urology"],
    "حصوة":              ["Urology"],
    "بروستاتا":          ["Urology"],
    "مثانة":             ["Urology"],
    "انتصاب":            ["Urology and Andrology", "Andrology"],
    "ذكورة":             ["Andrology", "Urology and Andrology"],

    # ── نساء وتوليد ──
    "حمل":               ["OBE & GYN"],
    "حامل":              ["OBE & GYN"],
    "نساء":              ["OBE & GYN"],
    "دورة":              ["OBE & GYN"],
    "مبيض":              ["OBE & GYN"],
    "رحم":               ["OBE & GYN"],
    "ثدي":               ["Breast Surgery", "OBE & GYN"],
    "عقم":               ["IVF", "OBE & GYN"],
    "تأخر حمل":          ["IVF"],

    # ── أطفال ──
    "طفل":               ["General Pediatrics"],
    "رضيع":              ["General Pediatrics"],
    "مولود":             ["General Pediatrics"],
    "تطعيم":             ["General Pediatrics"],
    "حرارة طفل":         ["General Pediatrics"],

    # ── نفسية ──
    "اكتئاب":            ["Psychiatry"],
    "قلق":               ["Psychiatry"],
    "نوم":               ["Psychiatry", "Neurology"],
    "أرق":               ["Psychiatry", "Neurology"],
    "توتر":              ["Psychiatry"],
    "نفسي":              ["Psychiatry"],
    "نفسية":             ["Psychiatry"],

    # ── أسنان ──
    "سن":                ["Dental Services"],
    "أسنان":             ["Dental Services"],
    "اسنان":             ["Dental Services"],
    "ضرس":               ["Dental Services"],
    "لثة":               ["Dental Services"],
    "تقويم":             ["Orthodontic"],
    "جذور":              ["Endodontic"],
    "عصب سن":            ["Endodontic"],
    "فك":                ["Maxillofacial Surgery"],

    # ── غدد وهرمونات ──
    "سكر":               ["Endocrinology", "Internal Medicine"],
    "سكري":              ["Endocrinology", "Internal Medicine"],
    "غدة":               ["Endocrinology"],
    "غدد":               ["Endocrinology"],
    "درقية":             ["Endocrinology"],
    "هرمون":             ["Endocrinology"],
    "وزن":               ["Nutrition", "Bariatric Surgery"],
    "سمنة":              ["Bariatric Surgery", "Nutrition"],
    "تغذية":             ["Nutrition"],
    "حمية":              ["Nutrition"],

    # ── دم ومناعة ──
    "انيميا":            ["Hematology", "Internal Medicine"],
    "فقر دم":            ["Hematology", "Internal Medicine"],
    "حساسية":            ["Allergy & Immunology"],
    "مناعة":             ["Allergy & Immunology"],
    "ورم":               ["Medical Oncology", "Surgical Oncology"],
    "سرطان":             ["Medical Oncology", "Surgical Oncology"],

    # ── عام ──
    "حرارة":             ["Internal Medicine"],
    "تعب":               ["Internal Medicine", "Endocrinology"],
    "فحص":               ["Family Medicine", "Internal Medicine"],
    "كشف عام":           ["Family Medicine"],
    "علاج طبيعي":        ["Physiotherapy"],
    "تأهيل":             ["Physiotherapy"],
}

# Specialties that should NEVER be shown to patients as routing targets
NON_BOOKABLE = {
    "Activities", "Laboratory", "Procedure Room", "Screening", "Radiology",
    "Breast Imaging", "Interventional Radiology", "Anesthesiology",
    "أنشطة", "المختبر", "غرفة الإجراءات الطبية", "الفحص المبدئي",
    "الأشعة", "الأشعة التداخلية", "التخدير", "تصوير الثدي",
}


def get_routing_question(complaint: str, age: int, lang: str) -> str:
    """Generate one targeted medical question for this complaint."""
    from prompts.triage import TRIAGE_PROMPT_EN, TRIAGE_PROMPT_AR
    prompt = TRIAGE_PROMPT_AR if lang == "ar" else TRIAGE_PROMPT_EN
    result = call_llm(
        messages=[{"role": "user", "content": complaint}],
        system_prompt=prompt.format(complaint=complaint, age=age),
        temperature=0.2,
        max_tokens=150,
        json_mode=True,
        label="triage",
    )
    return result.get("question", "").strip()


def route_specialty(complaint: str, age: int, lang: str,
                    clarification: str = "") -> dict:
    """
    Route complaint to specialty using 2-stage approach:
      1. Rule-based pre-filter → narrow to candidate specialties
      2. LLM picks the best from the narrowed list

    ALWAYS uses EN specialty names for routing, regardless of conversation language.
    The AR specialty names in constants.py are NOT aligned with EN names (different
    lengths, different sort order), so zip-mapping would produce wrong results.
    The DB and doctor fetching all use EN names anyway.
    """
    combined_text = f"{complaint} {clarification}".strip().lower()

    # Stage 1: Keyword pre-filter (always returns EN names)
    candidates = _keyword_prefilter(combined_text, lang, age)

    # Stage 2: LLM routing — always use EN specialty names
    # The prompt is in the patient's language, but the specialty LIST is always EN
    if candidates:
        spec_list = candidates
    else:
        spec_list = [s for s in (SPECIALTIES_EN + PEDIATRIC_SPECIALTIES_EN) if s not in NON_BOOKABLE]

    prompt = ROUTE_PROMPT_AR if lang == "ar" else ROUTE_PROMPT_EN

    result = call_llm(
        messages=[{"role": "user", "content": complaint}],
        system_prompt=prompt.format(
            complaint=complaint,
            age=age,
            clarification=clarification or "No additional info provided",
            specialties="\n".join(f"- {s}" for s in spec_list),
            ped_threshold=PEDIATRIC_AGE_THRESHOLD,
        ),
        temperature=0.0,
        max_tokens=100,
        json_mode=True,
        label="routing",
    )

    raw = result.get("specialty", "").strip()
    confidence = float(result.get("confidence", 0))

    # Validate against full list (LLM might return EN name even for AR conversation)
    all_valid = SPECIALTIES_EN + SPECIALTIES_AR + PEDIATRIC_SPECIALTIES_EN + PEDIATRIC_SPECIALTIES_AR
    validated = _validate(raw, all_valid)

    # Block non-bookable specialties
    if validated in NON_BOOKABLE:
        validated = ""

    return {"specialty": validated, "confidence": confidence}


def _keyword_prefilter(text: str, lang: str, age: int) -> list:
    """
    Match symptom keywords to candidate specialties.
    Returns a deduplicated list of EN specialty names, ordered by match frequency.
    If age < pediatric threshold, also adds pediatric variants.
    """
    from collections import Counter

    # Search both EN and AR keywords regardless of language
    hits = Counter()
    for keyword, specs in SYMPTOM_MAP.items():
        if keyword in text:
            for i, s in enumerate(specs):
                hits[s] += (len(specs) - i)  # Higher weight for first-listed specialty

    for keyword, specs in SYMPTOM_MAP_AR.items():
        if keyword in text:
            for i, s in enumerate(specs):
                hits[s] += (len(specs) - i)

    if not hits:
        return []

    # Sort by frequency and take top candidates
    candidates = [s for s, _ in hits.most_common(6)]

    # Add pediatric variants if patient is a child
    if age and age < PEDIATRIC_AGE_THRESHOLD:
        ped_map = {
            "Orthopedics": "Pediatric Orthopedics",
            "Cardiology": "Pediatric Cardiology",
            "Neurology": "Pediatric Neurology",
            "General Pediatrics": "General Pediatrics",
            "Internal Medicine": "General Pediatrics",
            "Family Medicine": "General Pediatrics",
            "Dental Services": "Pedodontic",
        }
        ped_additions = []
        for c in candidates:
            if c in ped_map:
                ped_additions.append(ped_map[c])
        candidates = list(dict.fromkeys(ped_additions + candidates))  # dedupe, peds first

    # Filter out non-bookable
    candidates = [c for c in candidates if c not in NON_BOOKABLE]

    return candidates


def _get_ar_names(en_candidates: list) -> list:
    """Convert EN specialty names to AR names for AR conversations."""
    en_to_ar = dict(zip(SPECIALTIES_EN, SPECIALTIES_AR))
    ped_en_to_ar = dict(zip(PEDIATRIC_SPECIALTIES_EN, PEDIATRIC_SPECIALTIES_AR))
    en_to_ar.update(ped_en_to_ar)

    result = []
    for en in en_candidates:
        ar = en_to_ar.get(en)
        if ar:
            result.append(ar)
        else:
            result.append(en)  # Fallback to EN if no mapping
    return result


def _validate(name: str, valid_list: list) -> str:
    """Fuzzy-validate a specialty name against the known list."""
    if not name:
        return ""
    if name in valid_list:
        return name
    lo = name.lower().strip()
    for s in valid_list:
        if s.lower().strip() == lo:
            return s
    for s in valid_list:
        if lo in s.lower() or s.lower() in lo:
            return s
    return ""
