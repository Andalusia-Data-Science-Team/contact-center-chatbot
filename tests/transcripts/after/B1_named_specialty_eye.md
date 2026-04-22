# Scenario: B1_named_specialty_eye

**Description:** Path B: patient names specialty directly (ophthalmology)
**Run at:** 2026-04-20T16:17:57

---

### Turn 1

**User:** مساء الخير

**Bot:** مساء النور، معك نور من مجموعة أندلسية صحة 😊 اتشرف بالاسم؟

_stage: `not started`_

### Turn 2

**User:** سعود الدوسري

**Bot:** أهلا وسهلا بحضرتك أ/ سعود 😊 ازاي أقدر أساعدك؟

_stage: `greeting_done`_

### Turn 3

**User:** ٤٠

**Bot:** أهلا أ/ سعود، كم عمرك ٤٠، ازاي أقدر أساعدك؟

_stage: `complaint`_

### Turn 4

**User:** أبغى أحجز عند دكتور عيون

**Bot:** تمام أ/ سعود الدوسري، حضرتك محتاج تخصص **طب العيون**. تبغى أعرض لك الأطباء المتاحين اليوم؟

_stage: `routing`_

### Turn 5

**User:** أيوه

**Bot:** تخصص **طب العيون** — الأطباء المتاحين ليوم اليوم:

1. **د. ولاء السعدني** — أقرب موعد: ٦:٤٥ مساءً (2 مواعيد)

تحب تحجز مع مين؟

_stage: `doctor_list`_

### Turn 6

**User:** بكرا ان شاء الله

**Bot:** تحب تحجز مع أي دكتور من القائمة؟ قولي رقم الدكتور أو اسمه.

_stage: `doctor_list`_

### Turn 7

**User:** بعد العصر

**Bot:** تحب تحجز مع أي دكتور من القائمة؟ قولي رقم الدكتور أو اسمه.

_stage: `doctor_list`_

### Turn 8

**User:** مناسب

**Bot:** أقرب موعد مع **د. ولاء السعدني** يوم اليوم الساعة **٧:٤٥ مساءً**.

مناسب لحضرتك؟ أو قولي الوقت اللي يناسبك.

_stage: `slot_selection`_

### Turn 9

**User:** 0533445566

**Bot:** ممكن تقول لي هل هي كاش أو تأمين؟

_stage: `slot_selection`_

### Turn 10

**User:** ايوه

**Bot:** ما فهمت الوقت، ممكن توضح الوقت اللي يناسبك؟

_stage: `slot_selection`_

### Turn 11

**User:** تعاونية

**Bot:** ما فهمت الوقت، ممكن توضح الوقت اللي يناسبك؟

_stage: `slot_selection`_

---

## Final State

- **language**: `ar`
- **booking_stage**: `slot_selection`
- **intent**: `booking`
- **patient_name**: `سعود الدوسري`
- **patient_age**: `40`
- **complaint_text**: `دكتور عيون`
- **speciality**: `Ophthalmology`
- **specialty_confirmed**: `True`
- **doctor**: `WALAA ELSADANY`
- **doctor_ar**: `ولاء السعدني`
- **requested_date**: `2026-04-21`
- **preferred_time**: `afternoon`
- **phone**: `0533445566`
- **insured**: `True`
- **insurance_company**: `التعاونية`

## Metrics

- **LLM calls:** 14
- **Tokens:** 36,231 (in: 35,596 / out: 635)
- **Total latency:** 63,114 ms
- **Errors:** 0
