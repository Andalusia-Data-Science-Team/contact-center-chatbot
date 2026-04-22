# Scenario: C1_named_doctor

**Description:** Path C: patient names doctor directly with preferred time
**Run at:** 2026-04-20T15:58:54

---

### Turn 1

**User:** هلا وغلا

**Bot:** السلام عليكم، معك نور من مجموعة أندلسية صحة 😊 اتشرف بالاسم؟

_stage: `None`_

### Turn 2

**User:** عبدالعزيز الحربي

**Bot:** أهلا وسهلا بحضرتك أ/ عبدالعزيز 😊 ازاي أقدر أساعدك؟

_stage: `greeting_done`_

### Turn 3

**User:** ٥٠

**Bot:** حسناً أ/ عبدالعزيز، كم عمرك ٥٠ سنة، ازاي أقدر أساعدك؟

_stage: `complaint`_

### Turn 4

**User:** أبغى موعد مع د. ولاء السعدني بكرا المغرب

**Bot:** ما لقيت الاسم ده في قائمة أطباء **None**. ممكن تختار رقم الطبيب من القائمة؟

_stage: `doctor_list`_

### Turn 5

**User:** تمام

**Bot:** ما لقيت الاسم ده في قائمة أطباء **None**. ممكن تختار رقم الطبيب من القائمة؟

_stage: `doctor_list`_

### Turn 6

**User:** 0544332211

**Bot:** ما لقيت الاسم ده في قائمة أطباء **None**. ممكن تختار رقم الطبيب من القائمة؟

_stage: `doctor_list`_

### Turn 7

**User:** أيوه

**Bot:** للأسف ما في مواعيد في اليوم. تبغى أجرب يوم ثاني؟

_stage: `slot_selection`_

### Turn 8

**User:** مدغلف

**Bot:** حسناً أ/ عبدالعزيز، تم تسجيل تأمينك مع شركة مدغلف. هل هذا صحيح؟

_stage: `slot_selection`_

---

## Final State

- **language**: `ar`
- **booking_stage**: `slot_selection`
- **intent**: `booking`
- **patient_name**: `عبدالعزيز الحربي`
- **patient_age**: `50`
- **complaint_text**: `د. ولاء السعدني`
- **specialty_confirmed**: `False`
- **doctor**: `د. ولاء السعدني`
- **doctor_ar**: `د. ولاء السعدني`
- **requested_date**: `2026-04-21`
- **preferred_time**: `بكرا المغرب`
- **phone**: `0544332211`
- **insured**: `True`
- **insurance_company**: `Medgulf`

## Metrics

- **LLM calls:** 10
- **Tokens:** 26,363 (in: 25,540 / out: 823)
- **Total latency:** 20,299 ms
- **Errors:** 0
