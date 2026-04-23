import pyodbc
from datetime import date, datetime, timedelta
from config.settings import DB_DRIVER, DB_SERVER, DB_DATABASE, DB_USERNAME, DB_PASSWORD


def get_connection():
    conn_str = (
        f"DRIVER={{{DB_DRIVER}}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_DATABASE};"
        f"UID={DB_USERNAME};"
        f"PWD={DB_PASSWORD};"
    )
    return pyodbc.connect(conn_str, timeout=15)


SQL_QUERY = """
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

DECLARE @ReportDate  DATE          = ?;
DECLARE @SpecialtyAr NVARCHAR(100) = ?;
DECLARE @SpecialtyEn NVARCHAR(100) = ?;
DECLARE @DoctorAr    NVARCHAR(100) = ?;
DECLARE @DoctorEn    NVARCHAR(100) = ?;

DECLARE @StartOfDay DATETIME = @ReportDate;
DECLARE @EndOfDay   DATETIME = DATEADD(DAY, 1, @ReportDate);

DROP TABLE IF EXISTS #ApptAgg, #SchedIntervals, #WorkIntervals, #WorkMinutesAgg,
                    #SlotsSummary, #RankedFreeSlots, #ActualWorkingHours;

SELECT ap.PhysicianID,
       MAX(p.PhysicianEnName)       AS Doctor,
       MAX(p.PhysicianArName)       AS DoctorAR,
       MAX(ap.SpecialtyEnName)      AS Specialty,
       MAX(ap.SpecialtyArName)      AS SpecialtyAR,
       @ReportDate                  AS ApptDate,
       COUNT(DISTINCT ap.PatientID) AS TotalPatients,
       MIN(ap.StartDateTime)        AS FirstAppt,
       MAX(ap.EndDateTime)          AS LastAppt
INTO #ApptAgg
FROM OPD.BK_Appointment ap
INNER JOIN [OPD].[BK_PatternInstance] pl ON ap.PatternInstanceID = pl.ID
INNER JOIN [OPD].PHS_OPDPattern p ON p.ID = pl.PatternID AND p.PhysicianID = pl.PhysicianID
WHERE ap.StartDateTime >= @StartOfDay AND ap.StartDateTime < @EndOfDay
  AND ap.StatusID NOT IN (6,7) AND p.IsDeleted = 0
  AND (p.EndDate IS NULL OR CONVERT(DATE, pl.StartDateTime) BETWEEN p.StartDate AND p.EndDate)
  AND (@SpecialtyAr IS NULL OR ap.SpecialtyArName = @SpecialtyAr)
  AND (@SpecialtyEn IS NULL OR ap.SpecialtyEnName = @SpecialtyEn)
  AND (@DoctorAr    IS NULL OR p.PhysicianArName  = @DoctorAr)
  AND (@DoctorEn    IS NULL OR p.PhysicianEnName  = @DoctorEn)
GROUP BY ap.PhysicianID;

CREATE CLUSTERED INDEX IX_ApptAgg ON #ApptAgg (PhysicianID);

SELECT ps.PhysicianID, ps.StartDateTime, ps.EndDateTime,
       ps.ID AS PatternInstanceID, p.AllowOverBooking
INTO #SchedIntervals
FROM OPD.BK_PatternInstance ps
INNER JOIN [OPD].PHS_OPDPattern p ON p.ID = ps.PatternID AND p.PhysicianID = ps.PhysicianID
INNER JOIN #ApptAgg a ON ps.PhysicianID = a.PhysicianID
WHERE ps.StartDateTime >= @StartOfDay AND ps.StartDateTime < @EndOfDay
  AND p.IsDeleted = 0
  AND (p.EndDate IS NULL OR CONVERT(DATE, ps.StartDateTime) BETWEEN p.StartDate AND p.EndDate);

CREATE CLUSTERED INDEX IX_SchedIntervals ON #SchedIntervals (PhysicianID, PatternInstanceID);

SELECT a.PhysicianID, a.Doctor, a.DoctorAR, a.Specialty, a.SpecialtyAR,
       a.ApptDate AS WorkDate, si.StartDateTime, si.EndDateTime
INTO #WorkIntervals
FROM #ApptAgg a INNER JOIN #SchedIntervals si ON si.PhysicianID = a.PhysicianID
UNION ALL
SELECT a.PhysicianID, a.Doctor, a.DoctorAR, a.Specialty, a.SpecialtyAR,
       a.ApptDate, a.FirstAppt, a.LastAppt
FROM #ApptAgg a LEFT JOIN #SchedIntervals si ON si.PhysicianID = a.PhysicianID
WHERE si.PhysicianID IS NULL;

CREATE CLUSTERED INDEX IX_WorkIntervals ON #WorkIntervals (PhysicianID);

SELECT wi.PhysicianID,
       MAX(wi.Doctor) AS Doctor, MAX(wi.DoctorAR) AS DoctorAR,
       MAX(wi.Specialty) AS Specialty, MAX(wi.SpecialtyAR) AS SpecialtyAR,
       @ReportDate AS WorkDate,
       DATENAME(WEEKDAY, MAX(wi.WorkDate)) AS WeekDayName,
       SUM(DATEDIFF(MINUTE, wi.StartDateTime, wi.EndDateTime)) AS TotalWorkMinutes
INTO #WorkMinutesAgg FROM #WorkIntervals wi GROUP BY wi.PhysicianID;

CREATE CLUSTERED INDEX IX_WorkMinutesAgg ON #WorkMinutesAgg (PhysicianID);

SELECT pi.PhysicianID AS DRID,
       SUM(sl.SlotUnit) AS plannedslot,
       SUM(CASE WHEN sl.IsOverbooked = 1 THEN sl.SlotUnit ELSE 0 END) AS OverbookedSlots,
       SUM(CASE WHEN ap.SlotID IS NOT NULL THEN sl.SlotUnit ELSE 0 END) AS ActualBookedSlots,
       CAST(AVG(CAST(DATEDIFF(SECOND, sl.StartTime, sl.EndTime) AS FLOAT)) / 60.0 AS DECIMAL(10,2)) AS AvgSlotDurationMin
INTO #SlotsSummary
FROM opd.BK_Slot sl
INNER JOIN #SchedIntervals pi ON sl.PatternInstanceID = pi.PatternInstanceID
LEFT JOIN [OPD].[BK_Appointment] ap ON ap.SlotID = sl.ID AND ap.StatusID NOT IN (6,7)
WHERE sl.StartDate = @ReportDate GROUP BY pi.PhysicianID;

CREATE CLUSTERED INDEX IX_SlotsSummary ON #SlotsSummary (DRID);

WITH FreeSlotsRanked AS (
    SELECT p.PhysicianID, sl.StartDate, sl.StartTime,
           ROW_NUMBER() OVER (PARTITION BY p.PhysicianID ORDER BY sl.StartDate, sl.StartTime) AS rn
    FROM opd.BK_Slot sl
    LEFT JOIN OPD.BK_Appointment ap ON sl.ID = ap.SlotID
    INNER JOIN OPD.BK_PatternInstance pl ON sl.PatternInstanceID = pl.ID
    INNER JOIN [OPD].PHS_OPDPattern p ON pl.PatternID = p.ID AND pl.PhysicianID = p.PhysicianID
    WHERE ap.SlotID IS NULL
      AND (sl.StartDate > CAST(GETDATE() AS DATE)
           OR (sl.StartDate = CAST(GETDATE() AS DATE) AND sl.StartTime >= CAST(GETDATE() AS TIME)))
      AND p.IsDeleted = 0
      AND (p.EndDate IS NULL OR CONVERT(DATE, pl.StartDateTime) BETWEEN p.StartDate AND p.EndDate)
)
SELECT PhysicianID, StartDate, StartTime INTO #RankedFreeSlots FROM FreeSlotsRanked;

CREATE CLUSTERED INDEX IX_RankedFreeSlots ON #RankedFreeSlots (PhysicianID);

SELECT va.PhysicianID,
       MAX(vv.MainSpecialityEnName) AS Specialty,
       MAX(vv.MainSpecialityArName) AS SpecialtyAR
INTO #ActualWorkingHours
FROM [VisitMgt].[VisitService] vs
INNER JOIN [VisitMgt].[Visit] vv ON vs.VisitID = vv.ID
INNER JOIN VisitMgt.VisitAppointment va ON va.VisitID = vs.VisitID
INNER JOIN VisitMgt.Receipt r ON r.VisitID = vs.VisitID
INNER JOIN VisitMgt.ReceiptDetails rd ON rd.ReceiptID = r.ID AND rd.VisitServiceID = vs.ID
WHERE vv.VisitClassificationID = 1
  AND ((vs.ClaimDate >= @StartOfDay AND vs.ClaimDate < @EndOfDay)
       OR (vs.ClaimDate IS NULL AND vs.CreatedDate >= @StartOfDay AND vs.CreatedDate < @EndOfDay))
  AND vs.IsDeleted = 0 AND vv.VisitStatusID != 3
GROUP BY va.PhysicianID;

CREATE CLUSTERED INDEX IX_ActualWorkingHours ON #ActualWorkingHours (PhysicianID);

SELECT ISNULL(ca.Specialty, awh.Specialty)   AS Specialty,
       ISNULL(ca.SpecialtyAR, awh.SpecialtyAR) AS SpecialtyAR,
       ca.Doctor, ca.DoctorAR, ca.WorkDate, ca.PhysicianID,
       sl.AvgSlotDurationMin AS [Avg Slot Duration (Min.)],
       sl.plannedslot, sl.OverbookedSlots,
       (sl.plannedslot - sl.OverbookedSlots) AS PlannedSlots_without_overbooking,
       sl.ActualBookedSlots,
       rfs.StartDate AS Slot_Date, rfs.StartTime AS Slot_Time,
       DATEDIFF(DAY, GETDATE(), rfs.StartDate) AS DaysFromToday
FROM #WorkMinutesAgg ca
LEFT JOIN #ApptAgg a ON a.PhysicianID = ca.PhysicianID
LEFT JOIN #SlotsSummary sl ON sl.DRID = ca.PhysicianID
LEFT JOIN #RankedFreeSlots rfs ON rfs.PhysicianID = ca.PhysicianID
LEFT JOIN #ActualWorkingHours awh ON awh.PhysicianID = ca.PhysicianID
ORDER BY ISNULL(ca.Specialty, awh.Specialty), ca.Doctor, rfs.StartDate, rfs.StartTime;

DROP TABLE IF EXISTS #ApptAgg, #SchedIntervals, #WorkIntervals, #WorkMinutesAgg,
                    #SlotsSummary, #RankedFreeSlots, #ActualWorkingHours;
"""

SLOTS_QUERY = """
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

DECLARE @DoctorEn   NVARCHAR(100) = ?;
DECLARE @DoctorAr   NVARCHAR(100) = ?;
DECLARE @ReportDate DATE          = ?;

SELECT sl.StartDate, sl.StartTime
FROM opd.BK_Slot sl
LEFT JOIN OPD.BK_Appointment ap ON sl.ID = ap.SlotID
INNER JOIN OPD.BK_PatternInstance pl ON sl.PatternInstanceID = pl.ID
INNER JOIN [OPD].PHS_OPDPattern p ON pl.PatternID = p.ID AND pl.PhysicianID = p.PhysicianID
WHERE ap.SlotID IS NULL
  AND sl.StartDate = @ReportDate
  AND (@DoctorEn IS NULL OR p.PhysicianEnName = @DoctorEn)
  AND (@DoctorAr IS NULL OR p.PhysicianArName = @DoctorAr)
  AND (sl.StartDate > CAST(GETDATE() AS DATE)
       OR (sl.StartDate = CAST(GETDATE() AS DATE) AND sl.StartTime >= CAST(GETDATE() AS TIME)))
  AND p.IsDeleted = 0
ORDER BY sl.StartDate, sl.StartTime;
"""


def query_availability(
    report_date: str,
    specialty_en: str = None,
    specialty_ar: str = None,
    doctor_en: str = None,
    doctor_ar: str = None,
) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(SQL_QUERY, (report_date, specialty_ar, specialty_en, doctor_ar, doctor_en))
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()


def query_availability_with_fallback(
    specialty_en: str = None,
    specialty_ar: str = None,
    doctor_en: str = None,
    doctor_ar: str = None,
    max_days_ahead: int = 7,
) -> tuple[list[dict], str]:
    """
    Try today first, then search forward up to max_days_ahead.
    Returns (rows, date_used_iso).
    """
    today = date.today()
    for offset in range(max_days_ahead + 1):
        check_date = today + timedelta(days=offset)
        date_str = check_date.strftime("%Y-%m-%d")
        rows = query_availability(
            report_date=date_str,
            specialty_en=specialty_en,
            specialty_ar=specialty_ar,
            doctor_en=doctor_en,
            doctor_ar=doctor_ar,
        )
        if rows:
            return rows, date_str
    return [], today.strftime("%Y-%m-%d")


def query_doctor_slots(
    report_date: str,
    doctor_en: str = None,
    doctor_ar: str = None,
) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(SLOTS_QUERY, (doctor_en, doctor_ar, report_date))
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()


def query_doctor_slots_with_fallback(
    doctor_en: str = None,
    doctor_ar: str = None,
    preferred_date: str = None,
    max_days_ahead: int = 7,
) -> tuple[list[dict], str]:
    """
    Try preferred_date first (or today), then search forward up to max_days_ahead.
    Returns (slots, date_used_iso).
    """
    start = date.today()
    if preferred_date:
        try:
            start = datetime.strptime(preferred_date, "%Y-%m-%d").date()
        except:
            pass

    for offset in range(max_days_ahead + 1):
        check_date = start + timedelta(days=offset)
        date_str = check_date.strftime("%Y-%m-%d")
        slots = query_doctor_slots(report_date=date_str, doctor_en=doctor_en, doctor_ar=doctor_ar)
        if slots:
            return slots, date_str
    return [], start.strftime("%Y-%m-%d")


def _is_clinic_placeholder(doctor_en: str, doctor_ar: str) -> bool:
    """True if the 'doctor' row is actually a clinic/station placeholder.

    The Dotcare availability table occasionally exposes walk-in clinics under
    the Physician column (e.g. "عيادة محطة الفرسان", "Clinic Jazan"). These
    aren't bookable doctors — filter them out before they surface in the
    doctor list.
    """
    en = (doctor_en or "").strip().lower()
    ar = (doctor_ar or "").strip()
    if en.startswith("clinic") or " clinic " in f" {en} ":
        return True
    if ar.startswith("عيادة") or ar.startswith("عياده"):
        return True
    return False


def aggregate_doctor_slots(rows: list) -> list:
    from collections import defaultdict
    doctors = defaultdict(list)
    for row in rows:
        doc = (row.get("Doctor") or "").strip()
        doc_ar = (row.get("DoctorAR") or "").strip()
        if not doc:
            continue
        if _is_clinic_placeholder(doc, doc_ar):
            continue
        doctors[doc].append(row)

    result = []
    for doctor_name, doc_rows in doctors.items():
        def sort_key(r):
            nd = r.get("Slot_Date")
            nt = r.get("Slot_Time")
            try:
                if isinstance(nd, str):
                    nd = datetime.strptime(nd, "%Y-%m-%d").date()
                if isinstance(nt, str):
                    parts = nt.split(":")
                    nt = datetime(2000, 1, 1, int(parts[0]), int(parts[1])).time()
                return (nd or date.max, nt or datetime.max.time())
            except:
                return (date.max, datetime.max.time())

        sorted_rows = sorted(doc_rows, key=sort_key)
        first = sorted_rows[0]
        # Only count slots on the SAME date as the nearest slot (not across fallback days)
        nearest_date = first.get("Slot_Date")
        same_day_count = sum(
            1 for r in doc_rows
            if r.get("Slot_Date") == nearest_date and nearest_date is not None
        )

        result.append({
            "Doctor":      doctor_name,
            "DoctorAR":    (first.get("DoctorAR") or "").strip(),
            "Specialty":   (first.get("Specialty") or "").strip(),
            "SpecialtyAR": (first.get("SpecialtyAR") or "").strip(),
            "PhysicianID": first.get("PhysicianID"),
            "Nearest_Date": first.get("Slot_Date"),
            "Nearest_Time": first.get("Slot_Time"),
            "DaysFromNearest": first.get("DaysFromToday"),
            "AvailableSlots": same_day_count,
            "PlannedSlots": first.get("PlannedSlots_without_overbooking"),
            "ActualBookedSlots": first.get("ActualBookedSlots"),
        })

    return result