# Scenario: A1_symptom_cardiology

**Description:** Path A: symptoms (chest pain + cough) → cardiology routing
**Run at:** 2026-04-20T16:15:06

---

### Turn 1

**User:** السلام عليكم

**Bot:** معك نور من مجموعة أندلسية صحة 😊 اتشرف بالاسم؟

_stage: `not started`_

### Turn 2

**User:** فهد العتيبي

**Bot:** أهلا وسهلا بحضرتك أ/ فهد 😊 ازاي أقدر أساعدك؟

_stage: `greeting_done`_

### Turn 3

**User:** ٣٥ سنة

**Bot:** أهلا أ/ فهد، ازاي أقدر أساعدك؟

_stage: `complaint`_

### Turn 4

**User:** عندي وجع في صدري ومعاي كحه من كم يوم

**Bot:** هل الكحة جافة أو منتجة للبلغم؟

_stage: `routing`_

### Turn 5

**User:** أيوه صح

**Bot:** تمام أ/ فهد العتيبي، حضرتك محتاج تخصص **الصدر**. تبغى أعرض لك الأطباء المتاحين اليوم؟

_stage: `routing`_

### Turn 6

**User:** بكرا

**Bot:** تمام أ/ فهد العتيبي، يبدو إنك محتاج تخصص **الصدر**. تبغى أعرض لك الأطباء المتاحين؟

_stage: `routing`_

### Turn 7

**User:** المغرب

**Bot:** تمام أ/ فهد العتيبي، يبدو إنك محتاج تخصص **الصدر**. تبغى أعرض لك الأطباء المتاحين؟

_stage: `routing`_

### Turn 8

**User:** تمام مناسب

**Bot:** تخصص **الصدر** — الأطباء المتاحين ليوم اليوم:

1. **د. عيد العجمي** — أقرب موعد: ٥:٤٣ مساءً (23 مواعيد)
2. **د. نجوى الحلوانى** — أقرب موعد: ٩:٠٠ صباحاً (25 مواعيد)
3. **د. نعمت السيوفي** — أقرب موعد: ٩:٠٠ صباحاً (30 مواعيد)

تحب تحجز مع مين؟

_stage: `doctor_list`_

### Turn 9

**User:** 0501234567

**Bot:** ممكن تقول لي هل هي كاش أو تأمين؟

_stage: `doctor_list`_

### Turn 10

**User:** أيوه عندي تأمين

**Bot:** تمام، أ/ فهد. من أي شركة التأمين؟

_stage: `doctor_list`_

### Turn 11

**User:** بوبا

**Bot:** تحب تحجز مع أي دكتور من القائمة؟ قولي رقم الدكتور أو اسمه.

_stage: `doctor_list`_

---

## Final State

- **language**: `ar`
- **booking_stage**: `doctor_list`
- **intent**: `booking`
- **patient_name**: `فهد العتيبي`
- **patient_age**: `35`
- **complaint_text**: `وجع في الصدر وكحه`
- **speciality**: `Chest`
- **specialty_confirmed**: `True`
- **requested_date**: `2026-04-21`
- **phone**: `0501234567`
- **insured**: `True`
- **insurance_company**: `Bupa`

## Metrics

- **LLM calls:** 17
- **Tokens:** 38,503 (in: 37,701 / out: 802)
- **Total latency:** 81,810 ms
- **Errors:** 0
