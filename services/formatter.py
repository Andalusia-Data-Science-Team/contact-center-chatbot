# services/formatter.py
"""
All human-readable message templates.
Fully bilingual — Arabic uses Arabic numerals, translated dates/times/specialties.
"""
from datetime import datetime
from utils.datetime_fmt import format_date, format_time
from config.constants import SPECIALTY_EN_TO_AR


def _spec_display(specialty_en: str, lang: str) -> str:
    """Get the display name for a specialty in the right language."""
    if lang == "ar":
        return SPECIALTY_EN_TO_AR.get(specialty_en, specialty_en)
    return specialty_en


def _ar_day(date_label: str) -> str:
    """Return a natural Arabic day phrase.

    "اليوم" / "غدا" stand on their own — prefixing "يوم اليوم" or "يوم غدا"
    reads as redundant. For other forms (e.g. "الخميس، ٣٠ أبريل") "يوم X" is
    the natural form.
    """
    if date_label in ("اليوم", "غدا"):
        return date_label
    return f"يوم {date_label}"


def _ar_day_for(date_label: str) -> str:
    """Return a natural Arabic phrase for "for / on {day}" usage.

    Maps "اليوم" → "اليوم" and "غدا" → "غدا" (no preposition needed) and
    other days to "ليوم X". Used in the doctor list message
    ("doctors available for {day}").
    """
    if date_label in ("اليوم", "غدا"):
        return date_label
    return f"ليوم {date_label}"


def _time_display(t, lang: str) -> str:
    """Format a time value in the right language."""
    if t is None:
        return ""
    try:
        if isinstance(t, str):
            p = t.split(":")
            t = datetime(2000, 1, 1, int(p[0]), int(p[1])).time()
        return format_time(t, lang)
    except Exception:
        return str(t)


# ── Doctor list ───────────────────────────────────────────────────────────────

def doctor_list_message(doctors: list, specialty: str, lang: str, date_used: str, view_mode: str = None) -> str:
    from config.settings import VIEW_MODE
    mode = view_mode or VIEW_MODE
    date_label = format_date(date_used, lang)
    spec_name = _spec_display(specialty, lang)
    lines = []
    for i, doc in enumerate(doctors, 1):
        name = (doc.get("DoctorAR") or doc.get("Doctor", "?")) if lang == "ar" else doc.get("Doctor", "?")
        prefix = "د. " if lang == "ar" else "Dr. "
        nearest = _time_display(doc.get("Nearest_Time"), lang)
        slot_count = doc.get("AvailableSlots", 0)

        entry = f"{i}. **{prefix}{name}**"

        if mode == "stakeholder":
            if nearest:
                if slot_count == 1:
                    badge = " _(آخر موعد متاح)_" if lang == "ar" else " _(last appointment)_"
                else:
                    badge = ""
                entry += f" — أقرب موعد: {nearest}{badge}" if lang == "ar" else f" — earliest: {nearest}{badge}"
        else:
            count_note = f" ({slot_count} مواعيد)" if lang == "ar" and slot_count else \
                         f" ({slot_count} slots)" if lang == "en" and slot_count else ""
            if nearest:
                entry += f" — أقرب موعد: {nearest}{count_note}" if lang == "ar" else \
                         f" — earliest: {nearest}{count_note}"
        lines.append(entry)

    body = "\n".join(lines)
    if lang == "ar":
        day_phrase = _ar_day_for(date_label)
        return (
            f"تخصص **{spec_name}** — الأطباء المتاحين {day_phrase}:\n\n{body}\n\n"
            f"تحب تحجز مع مين؟"
        )
    if date_label in ("Today", "Tomorrow"):
        en_date_part = date_label.lower()
    else:
        en_date_part = f"on {date_label}"
    return (
        f"For **{spec_name}** — available doctors {en_date_part}:\n\n{body}\n\n"
        f"Which doctor would you prefer?"
    )


def no_doctors_message(specialty: str, lang: str) -> str:
    spec_name = _spec_display(specialty, lang)
    if lang == "ar":
        return (
            f"للأسف ما في أطباء متاحين في **{spec_name}** خلال الأسبوع القادم. "
            f"تبغى تجرب تخصص ثاني أو تتواصل مع الاستقبال؟"
        )
    return (
        f"Unfortunately there are no available doctors in **{spec_name}** over the next 7 days. "
        f"Would you like to try a different specialty or contact reception directly?"
    )


def slot_question(doctor_en: str, doctor_ar: str, slot_info: dict, lang: str) -> str:
    doc = (doctor_ar or doctor_en) if lang == "ar" else doctor_en
    prefix = "د. " if lang == "ar" else "Dr. "
    date_label = format_date(slot_info["first_date"], lang)
    first_time = format_time(slot_info["first_time"], lang)

    if lang == "ar":
        day_phrase = _ar_day(date_label)
        return (
            f"أقرب موعد مع **{prefix}{doc}** {day_phrase} الساعة **{first_time}**.\n\n"
            f"مناسب لحضرتك؟ أو قولي الوقت اللي يناسبك."
        )
    return (
        f"The earliest slot with **{prefix}{doc}** is {date_label} at **{first_time}**.\n\n"
        f"Does that work for you? Or let me know your preferred time."
    )


def more_slots_message(doctor_en: str, doctor_ar: str, all_slots: list, lang: str,
                       filter_label: str = "") -> str:
    doc = (doctor_ar or doctor_en) if lang == "ar" else doctor_en
    prefix = "د. " if lang == "ar" else "Dr. "
    lines = [f"{i}. **{format_time(t, lang)}**" for i, (_, t) in enumerate(all_slots, 1)]
    body = "\n".join(lines)

    # Surface the date the slots belong to so a fallback / date-change shift
    # (e.g. patient asked "متاح اليوم؟" but only tomorrow has openings) is
    # visible instead of the patient assuming the slots are for today.
    date_label = ""
    if all_slots:
        date_label = format_date(all_slots[0][0], lang)

    if lang == "ar":
        period = f" ({filter_label})" if filter_label else ""
        date_part = f" {_ar_day_for(date_label)}" if date_label else ""
        return f"المواعيد المتاحة لـ{prefix}{doc}{date_part}{period}:\n\n{body}\n\nأيهم يناسبك؟"
    period = f" ({filter_label})" if filter_label else ""
    if date_label in ("Today", "Tomorrow"):
        date_part = f" {date_label.lower()}"
    elif date_label:
        date_part = f" on {date_label}"
    else:
        date_part = ""
    return f"Available slots for {prefix}{doc}{date_part}{period}:\n\n{body}\n\nWhich works for you?"


def slot_confirmed_message(doctor_en: str, doctor_ar: str,
                           slot_date: str, slot_time: str,
                           lang: str, is_fallback: bool = False) -> str:
    doc = (doctor_ar or doctor_en) if lang == "ar" else doctor_en
    prefix = "د. " if lang == "ar" else "Dr. "
    if lang == "ar":
        if is_fallback:
            return (
                f"أقرب موعد متاح هو **{slot_date} الساعة {slot_time}** مع {prefix}{doc}.\n\n"
                f"مناسب؟ لو أيوه، ممكن رقم جوالك وكاش ام تأمين؟"
            )
        return (
            f"تمام، لتأكيد الحجز مع {prefix}{doc} {_ar_day(slot_date)} الساعة {slot_time}، "
            f"نرجو تزويدنا برقم الجوال، وهل ستكون الزيارة كاش أم تأمين؟"
        )
    if is_fallback:
        return (
            f"The closest available slot is **{slot_date} at {slot_time}** with {prefix}{doc}.\n\n"
            f"Does that work? If so, I'll need your phone number and whether it's cash or insurance."
        )
    return (
        f"Your slot with {prefix}{doc} is set for **{slot_date} at {slot_time}**.\n\n"
        f"I just need your phone number, and will it be cash or insurance?"
    )


def no_slots_message(doctor_en: str, doctor_ar: str, lang: str) -> str:
    doc = (doctor_ar or doctor_en) if lang == "ar" else doctor_en
    prefix = "د. " if lang == "ar" else "Dr. "
    if lang == "ar":
        return f"للأسف {prefix}{doc} ما عنده مواعيد متاحة حالياً. تبغى تختار دكتور ثاني؟"
    return f"Unfortunately {prefix}{doc} has no available slots right now. Would you like to choose another doctor?"


def doctor_confirm_message(matched_name: str, lang: str) -> str:
    if lang == "ar":
        return f"تقصد **د. {matched_name}**؟"
    return f"Did you mean **Dr. {matched_name}**?"


def doctor_not_found_message(specialty: str, lang: str) -> str:
    # When specialty is missing (Path C before routing), don't render the word "None"
    if not specialty:
        if lang == "ar":
            return (
                "للأسف ما قدرت ألاقي الدكتور المطلوب. "
                "ممكن توضحلي وش التخصص أو نوع الكشف المطلوب؟"
            )
        return (
            "Sorry, I couldn't find that doctor. "
            "Could you tell me the specialty or what kind of appointment you need?"
        )
    spec_name = _spec_display(specialty, lang)
    if lang == "ar":
        return f"ما لقيت الاسم ده في قائمة أطباء **{spec_name}**. ممكن تختار رقم الطبيب من القائمة؟"
    return f"I couldn't find that name in the **{spec_name}** doctors list. Could you pick a number from the list?"


def missing_info_message(missing: list, lang: str) -> str:
    if lang == "ar":
        field_map = {
            "name": "اسمك الكامل",
            "phone": "رقم جوالك",
            "insured": "كاش ام تأمين؟",
            "insurance_company": "اسم شركة التأمين",
        }
        fields = " و".join(field_map[f] for f in missing)
        return f"محتاج منك {fields}"
    field_map = {
        "name": "your full name",
        "phone": "your phone number",
        "insured": "cash or insurance?",
        "insurance_company": "your insurance company name",
    }
    fields = " and ".join(field_map[f] for f in missing)
    return f"I just need {fields} to complete your booking."


def _format_price(amount: float, lang: str) -> str:
    if lang == "ar":
        from utils.datetime_fmt import _to_arabic_numerals
        return _to_arabic_numerals(f"{amount:g}")
    return f"{amount:g}"


def _price_line(state: dict, lang: str) -> str:
    """Walk-in price line shown only to cash / self-pay patients."""
    if state.get("insured"):
        return ""
    price = state.get("walk_in_price")
    if price is None:
        return ""
    try:
        amount = float(price)
    except (TypeError, ValueError):
        return ""
    if lang == "ar":
        return f"\n💵 سعر الكشف (كاش): {_format_price(amount, lang)}"
    return f"\n💵 Consultation fee (cash): {_format_price(amount, lang)}"


# ── Price inquiry responses ───────────────────────────────────────────────────

def price_insured_message(lang: str) -> str:
    if lang == "ar":
        return (
            "التأمين يغطي الكشف إن شاء الله. "
            "أي فرق بسيط (لو وُجد) يتأكد منه الاستقبال عند الحضور."
        )
    return (
        "Your insurance covers the consultation — any co-pay, if applicable, "
        "is confirmed at the reception."
    )


def price_cash_message(doctor_en: str, doctor_ar: str, amount: float, lang: str) -> str:
    doc = (doctor_ar or doctor_en) if lang == "ar" else doctor_en
    prefix = "د. " if lang == "ar" else "Dr. "
    amt = _format_price(amount, lang)
    if lang == "ar":
        return f"سعر الكشف كاش لـ {prefix}{doc} هو **{amt}**."
    return f"The cash consultation fee with {prefix}{doc} is **{amt}**."


def price_unknown_insurance_message(doctor_en: str, doctor_ar: str, amount, lang: str) -> str:
    """Asked about price before telling us insurance — answer + clarify."""
    doc = (doctor_ar or doctor_en) if lang == "ar" else doctor_en
    prefix = "د. " if lang == "ar" else "Dr. "
    if amount is not None:
        amt = _format_price(float(amount), lang)
        if lang == "ar":
            return (
                f"سعر الكشف كاش لـ {prefix}{doc} هو **{amt}**. "
                f"هل الدفع كاش أم عندك تأمين؟"
            )
        return (
            f"The cash consultation fee with {prefix}{doc} is **{amt}**. "
            f"Will it be cash or do you have insurance?"
        )
    # No price on file — still ask cash/insurance so we know the billing path
    if lang == "ar":
        return (
            "السعر بيختلف حسب طريقة الدفع. هل الدفع كاش أم عندك تأمين؟ "
            "لو تأمين، التأمين يغطي الكشف."
        )
    return (
        "The price depends on billing. Is it cash or do you have insurance? "
        "If insured, your insurance covers the consultation."
    )


def price_no_data_message(doctor_en: str, doctor_ar: str, lang: str) -> str:
    doc = (doctor_ar or doctor_en) if lang == "ar" else doctor_en
    prefix = "د. " if lang == "ar" else "Dr. "
    if lang == "ar":
        return (
            f"سعر الكشف كاش لـ {prefix}{doc} مش متاح عندي حالياً، "
            f"بس هنتواصل معاك ونبلغك بالسعر في أقرب وقت."
        )
    return (
        f"I don't have the cash price for {prefix}{doc} on file right now — "
        f"our team will contact you shortly with it."
    )


def price_no_doctor_message(lang: str) -> str:
    if lang == "ar":
        return (
            "السعر بيختلف من دكتور للتاني. لو تحب، اختار الدكتور من القائمة "
            "وأقدر أطلعلك السعر بالظبط."
        )
    return (
        "Prices vary by doctor. Pick a doctor from the list and I can share the "
        "exact cash fee."
    )


def confirmation_message(state: dict, lang: str) -> str:
    doc_en = state.get("doctor", "")
    doc_ar = state.get("doctor_ar", "")
    doc = (doc_ar or doc_en) if lang == "ar" else doc_en
    spec_en = state.get("speciality", "")
    spec = _spec_display(spec_en, lang) if spec_en else ""
    slot_raw = state.get("selected_slot", "")
    try:
        p = slot_raw.split(":")
        t_obj = datetime(2000, 1, 1, int(p[0]), int(p[1])).time()
        slot_display = format_time(t_obj, lang)
    except Exception:
        slot_display = slot_raw
    # Render the actual booked date. `state["date"]` is the date the slots were
    # fetched for (set by fetch_slots fallback) and is always a YYYY-MM-DD ISO
    # string — it's the source of truth for what the patient is booked into.
    appt_date = format_date(state.get("date"), lang)
    name = state.get("patient_name", "")
    insured = state.get("insured")
    insurance = state.get("insurance_company", "")
    prefix = "د. " if lang == "ar" else "Dr. "
    price_line = _price_line(state, lang)

    if lang == "ar":
        ins_text = insurance if (insured and insurance) else "تأمين" if insured else "كاش"
        return (
            f"تم تأكيد الحجز أ/ {name} ✅\n\n"
            f"📋 **تفاصيل الموعد:**\n"
            f"👨‍⚕️ مع {prefix}{doc}\n"
            f"🏥 عيادة {spec}\n"
            f"📅 {_ar_day(appt_date)}\n"
            f"🕐 الساعة {slot_display}\n"
            f"💳 {ins_text}"
            f"{price_line}\n\n"
            f"واستأذنك الحضور قبل الميعاد بـ **ربع ساعة** لاستكمال إجراءات الحجز مع الاستقبال.\n\n"
            f"اي استفسار آخر أقدر أساعدك به؟ 😊\n"
            f"نور — مجموعة أندلسية صحة"
        )

    ins_text = insurance if (insured and insurance) else "Insurance" if insured else "Cash / Self-pay"
    return (
        f"Your booking is confirmed, {name}! ✅\n\n"
        f"📋 **Appointment Details:**\n"
        f"👨‍⚕️ {prefix}{doc}\n"
        f"🏥 {spec}\n"
        f"📅 {appt_date}\n"
        f"🕐 {slot_display}\n"
        f"💳 {ins_text}"
        f"{price_line}\n\n"
        f"Please arrive **15 minutes** early to complete check-in at reception.\n\n"
        f"Is there anything else I can help with? 😊\n"
        f"Nour — Andalusia Health Group"
    )


def app_download_message(lang: str) -> str:
    """Separate follow-up message about the Dotcare app — sent after confirmation."""
    if lang == "ar":
        return (
            "توفيراً لوقتك، تقدر تحجز أي كشفية أو متابعة بشكل مباشر من خلال تطبيق **Dotcare for Health and Lifestyle** 📱\n\n"
            "حمّله من هنا:\n"
            "• Google Play: https://play.google.com/store/apps/details?id=net.andalusiagroup.andalusiabooking\n"
            "• App Store: https://apps.apple.com/eg/app/dotcare-for-health-lifestyle/id972029385"
        )
    return (
        "By the way, you can book any follow-up or new appointment directly through the **Dotcare for Health and Lifestyle** app 📱\n\n"
        "Download it here:\n"
        "• Google Play: https://play.google.com/store/apps/details?id=net.andalusiagroup.andalusiabooking\n"
        "• App Store: https://apps.apple.com/eg/app/dotcare-for-health-lifestyle/id972029385"
    )
