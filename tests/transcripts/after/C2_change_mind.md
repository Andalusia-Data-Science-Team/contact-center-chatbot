# Scenario: C2_change_mind

**Description:** Patient changes mind mid-flow (switch specialty)
**Run at:** 2026-04-20T16:20:59

---

### Turn 1

**User:** هلا

**Bot:** السلام عليكم، معك نور من مجموعة أندلسية صحة 😊 اتشرف بالاسم؟

_stage: `None`_

### Turn 2

**User:** منال العتيبي

**Bot:** أهلا وسهلا بحضرتك أ/ منال 😊 ازاي أقدر أساعدك؟

_stage: `greeting_done`_

### Turn 3

**User:** ٣٢

**Bot:** حسناً أ/ منال، كم عمر المريض؟

_stage: `greeting_done`_

### Turn 4

**User:** أبغى دكتور جلدية

**Bot:** تمام أ/ منال العتيبي، حضرتك محتاج تخصص **الأمراض الجلدية**. تبغى أعرض لك الأطباء المتاحين اليوم؟

_stage: `routing`_

### Turn 5

**User:** لا استني، خليها باطنة أحسن

**Bot:** تمام أ/ منال العتيبي، يبدو إنك محتاج تخصص **الأمراض الجلدية**. تبغى أعرض لك الأطباء المتاحين؟

_stage: `routing`_

### Turn 6

**User:** ايوه

**Bot:** تخصص **الأمراض الجلدية** — الأطباء المتاحين ليوم اليوم:

1. **د. عبدالرحمن أحمد** — أقرب موعد: ٦:٢٠ مساءً (3 مواعيد)
2. **د. صفاء أبو العزم** — أقرب موعد: ١٢:٣٠ مساءً (21 مواعيد)

تحب تحجز مع مين؟

_stage: `doctor_list`_

### Turn 7

**User:** بكرا

**Bot:** تحب تحجز مع أي دكتور من القائمة؟ قولي رقم الدكتور أو اسمه.

_stage: `doctor_list`_

### Turn 8

**User:** الصبح

**Bot:** تحب تحجز مع أي دكتور من القائمة؟ قولي رقم الدكتور أو اسمه.

_stage: `doctor_list`_

### Turn 9

**User:** مناسب

**Bot:** تحب تحجز مع أي دكتور من القائمة؟ قولي رقم الدكتور أو اسمه.

_stage: `doctor_list`_

### Turn 10

**User:** 0577112233

**Bot:** ممكن تقول لي هل هي كاش أو تأمين؟

_stage: `doctor_list`_

### Turn 11

**User:** أيوه

**Bot:** تحب تحجز مع أي دكتور من القائمة؟ قولي رقم الدكتور أو اسمه.

_stage: `doctor_list`_

### Turn 12

**User:** اكسا

**Bot:** تحب تحجز مع أي دكتور من القائمة؟ قولي رقم الدكتور أو اسمه.

_stage: `doctor_list`_

---

## Final State

- **language**: `ar`
- **booking_stage**: `doctor_list`
- **intent**: `booking`
- **patient_name**: `منال العتيبي`
- **patient_age**: `32`
- **complaint_text**: `جلدية`
- **speciality**: `Dermatology and Cosmatology`
- **specialty_confirmed**: `True`
- **requested_date**: `2026-04-21`
- **phone**: `0577112233`
- **insured**: `True`
- **insurance_company**: `AXA`

## Metrics

- **LLM calls:** 16
- **Tokens:** 39,903 (in: 39,379 / out: 524)
- **Total latency:** 71,949 ms
- **Errors:** 0
