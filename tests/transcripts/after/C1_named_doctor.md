# Scenario: C1_named_doctor

**Description:** Path C: patient names doctor directly with preferred time
**Run at:** 2026-04-20T16:20:01

---

### Turn 1

**User:** هلا وغلا

**Bot:** السلام عليكم، معك نور من مجموعة أندلسية صحة 😊 اتشرف بالاسم؟

_stage: `not started`_

### Turn 2

**User:** عبدالعزيز الحربي

**Bot:** أهلا وسهلا بحضرتك أ/ عبدالعزيز 😊 ازاي أقدر أساعدك؟

_stage: `greeting_done`_

### Turn 3

**User:** ٥٠

**Bot:** أهلا أ/ عبدالعزيز، كم عمرك ٥٠، ممكن تقول لي ماذا تحتاج؟

_stage: `complaint`_

### Turn 4

**User:** أبغى موعد مع د. ولاء السعدني بكرا المغرب

**Bot:** المواعيد المتاحة لـد. ولاء السعدني:

1. **٧:٤٥ مساءً**

أيهم يناسبك؟

_stage: `slot_selection`_

### Turn 5

**User:** تمام

**Bot:** أقرب موعد مع **د. ولاء السعدني** يوم اليوم الساعة **٧:٤٥ مساءً**.

مناسب لحضرتك؟ أو قولي الوقت اللي يناسبك.

_stage: `slot_selection`_

### Turn 6

**User:** 0544332211

**Bot:** ممكن تقول لي هل هي كاش أو تأمين؟

_stage: `slot_selection`_

### Turn 7

**User:** أيوه

**Bot:** ما فهمت الوقت، ممكن توضح الوقت اللي يناسبك؟

_stage: `slot_selection`_

### Turn 8

**User:** مدغلف

**Bot:** ما فهمت الوقت، ممكن توضح الوقت اللي يناسبك؟

_stage: `slot_selection`_

---

## Final State

- **language**: `ar`
- **booking_stage**: `slot_selection`
- **intent**: `booking`
- **patient_name**: `عبدالعزيز الحربي`
- **patient_age**: `50`
- **specialty_confirmed**: `False`
- **doctor**: `ولاء السعدني`
- **doctor_ar**: `ولاء السعدني`
- **requested_date**: `2026-04-21`
- **preferred_time**: `٧:٤٥ مساءً`
- **phone**: `0544332211`
- **insured**: `True`
- **insurance_company**: `Medgulf`

## Metrics

- **LLM calls:** 10
- **Tokens:** 26,208 (in: 25,580 / out: 628)
- **Total latency:** 29,445 ms
- **Errors:** 0
