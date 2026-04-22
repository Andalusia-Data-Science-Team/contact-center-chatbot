# Scenario: A1_symptom_cardiology

**Description:** Path A: symptoms (chest pain + cough) → cardiology routing
**Run at:** 2026-04-20T15:54:05

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

**Bot:** حسناً أ/ فهد، ممكن تساعدني بقول لي ما هو شكواك أو لماذا تحتاج حجز؟

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

**Bot:** _(empty reply)_

_stage: `doctor_list`_

### Turn 9

**User:** 0501234567

**Bot:** حسناً أ/ فهد، ممكن تساعدني بقول لي هل هي كاش أو تأمين؟

_stage: `doctor_list`_

### Turn 10

**User:** أيوه عندي تأمين

**Bot:** حسناً أ/ فهد، من أي شركة التأمين؟

_stage: `doctor_list`_

### Turn 11

**User:** بوبا

**Bot:** _(empty reply)_

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
- **specialty_confirmed**: `False`
- **requested_date**: `2026-04-21`
- **phone**: `0501234567`
- **insured**: `True`
- **insurance_company**: `Bupa`

## Metrics

- **LLM calls:** 17
- **Tokens:** 38,257 (in: 37,364 / out: 893)
- **Total latency:** 75,966 ms
- **Errors:** 2

  - turn 8: empty bot reply
  - turn 11: empty bot reply
