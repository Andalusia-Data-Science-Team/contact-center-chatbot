# Scenario: A2_vague_symptom

**Description:** Path A: vague complaint requiring clarification
**Run at:** 2026-04-20T16:16:46

---

### Turn 1

**User:** هلا

**Bot:** السلام عليكم، معك نور من مجموعة أندلسية صحة 😊 اتشرف بالاسم؟

_stage: `not started`_

### Turn 2

**User:** نورة القحطاني

**Bot:** أهلا وسهلا بحضرتك نورة 😊 ازاي أقدر أساعدك؟

_stage: `greeting_done`_

### Turn 3

**User:** ٢٨

**Bot:** حسناً، كم عمر المريض؟

_stage: `greeting_done`_

### Turn 4

**User:** تعبانة شوي وما أدري وش فيني

**Bot:** أهلا أ/ نورة، أنا نور من مجموعة أندلسية صحة. ازاي أقدر أساعدك؟

_stage: `complaint`_

### Turn 5

**User:** صداع ودوخه

**Bot:** هل الصداع في جنب واحد ولا في كل الراس؟

_stage: `routing`_

### Turn 6

**User:** أيوه

**Bot:** تمام أ/ نورة القحطاني، حضرتك محتاج تخصص **المخ والأعصاب**. تبغى أعرض لك الأطباء المتاحين اليوم؟

_stage: `routing`_

### Turn 7

**User:** اليوم

**Bot:** تمام أ/ نورة القحطاني، يبدو إنك محتاج تخصص **المخ والأعصاب**. تبغى أعرض لك الأطباء المتاحين؟

_stage: `routing`_

### Turn 8

**User:** بعد المغرب

**Bot:** تمام أ/ نورة القحطاني، يبدو إنك محتاج تخصص **المخ والأعصاب**. تبغى أعرض لك الأطباء المتاحين؟

_stage: `routing`_

### Turn 9

**User:** يصير

**Bot:** تمام أ/ نورة القحطاني، يبدو إنك محتاج تخصص **المخ والأعصاب**. تبغى أعرض لك الأطباء المتاحين؟

_stage: `routing`_

### Turn 10

**User:** 0555123456

**Bot:** تمام أ/ نورة القحطاني، يبدو إنك محتاج تخصص **المخ والأعصاب**. تبغى أعرض لك الأطباء المتاحين؟

_stage: `routing`_

### Turn 11

**User:** لا ما عندي

**Bot:** تحب تحجز مع أي دكتور من القائمة؟ قولي رقم الدكتور أو اسمه.

_stage: `doctor_list`_

---

## Final State

- **language**: `ar`
- **booking_stage**: `doctor_list`
- **intent**: `booking`
- **patient_name**: `نورة القحطاني`
- **patient_age**: `28`
- **complaint_text**: `صداع ودوخه`
- **speciality**: `Neurology`
- **specialty_confirmed**: `False`
- **phone**: `0555123456`
- **insured**: `False`

## Metrics

- **LLM calls:** 19
- **Tokens:** 37,668 (in: 36,841 / out: 827)
- **Total latency:** 70,361 ms
- **Errors:** 0
