import atexit
import random
import time

import pyodbc
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from queue import Empty, Full, Queue
from config.settings import (
    DB_DRIVER, DB_SERVER, DB_DATABASE, DB_USERNAME, DB_PASSWORD,
    DB_POOL_MAX_SIZE, DB_POOL_MAX_AGE_SECONDS, DB_QUERY_TIMEOUT_SECONDS,
)


# Thread-safe connection pool. Streamlit serves each user session in its own
# thread, so multiple concurrent users share the pool — each query borrows a
# connection, runs, and returns it. The TLS+auth handshake to SQL Server is
# ~100-500ms; without pooling, every DB call paid that cost.
#
# Each pool entry is a (conn, created_at) tuple. On borrow, if the connection
# is at least DB_POOL_MAX_AGE_SECONDS old it is closed and a fresh one is
# opened in its place — pre-empts the SQL Server / firewall idle-killed
# connection pattern that surfaces as "Communication link failure" on the
# next query. Setting DB_POOL_MAX_AGE_SECONDS=0 effectively disables pool
# reuse (every borrow opens a fresh connection); useful for debugging.
_pool: "Queue[tuple[pyodbc.Connection, float]]" = Queue(maxsize=DB_POOL_MAX_SIZE)


def _new_connection() -> pyodbc.Connection:
    conn_str = (
        f"DRIVER={{{DB_DRIVER}}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_DATABASE};"
        f"UID={DB_USERNAME};"
        f"PWD={DB_PASSWORD};"
    )
    return pyodbc.connect(conn_str, timeout=DB_QUERY_TIMEOUT_SECONDS)


def get_connection() -> pyodbc.Connection:
    """Open a fresh (unpooled) connection. Kept for any legacy caller — new
    code should use the `_borrow_conn` context manager below to share the
    pooled connections."""
    return _new_connection()


@contextmanager
def _borrow_conn():
    """Borrow a pooled connection for the duration of the `with` block.

    On exit, the connection is returned to the pool (or closed if the pool is
    full). On exception, the connection is closed rather than returned —
    pyodbc state after a failed query is not safe to reuse. If the borrowed
    pooled connection is at or above DB_POOL_MAX_AGE_SECONDS old it's
    proactively closed and replaced with a fresh one before the `yield`.
    """
    conn = None
    created_at = None

    # Try to reuse a pooled connection first.
    try:
        conn, created_at = _pool.get_nowait()
        # Recycle if the pooled conn is at or above the max age.
        if (time.time() - created_at) >= DB_POOL_MAX_AGE_SECONDS:
            try:
                conn.close()
            except Exception:
                pass
            conn = None
    except Empty:
        pass

    if conn is None:
        conn = _new_connection()
        created_at = time.time()

    try:
        yield conn
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        raise

    try:
        _pool.put_nowait((conn, created_at))
    except Full:
        try:
            conn.close()
        except Exception:
            pass


def _close_pool() -> None:
    """Drain the connection pool at process exit. Best-effort; close failures
    don't propagate. Registered with atexit alongside the SQLite logger's own
    `_close_conn` (item 5.5) so the process leaves no half-open SQL Server
    sockets in CLOSE_WAIT on shutdown."""
    while True:
        try:
            conn, _ = _pool.get_nowait()
        except Empty:
            break
        try:
            conn.close()
        except Exception as e:
            # Best-effort: a half-dead connection shouldn't crash shutdown.
            print(f"[db._close_pool] close failed: {e}")


atexit.register(_close_pool)


# ── Transient-error retry ────────────────────────────────────────────────────
# Mirrors db/crm_database.py's pattern: classify a pyodbc.Error as transient
# by substring-matching its message (SQLSTATE 08S01 / 08001 / 28000 surface as
# free-form strings), retry once with a fresh borrowed connection. Bypasses
# the pool naturally — `_borrow_conn` already drops a failed connection
# instead of returning it, so the retry's `_borrow_conn` either gets a
# different pooled conn or opens a fresh one.
_TRANSIENT_DB_SUBSTRINGS = (
    "communication link failure",
    "tcp provider",
    "timeout expired",
    "connection is closed",
    "connection reset",
    "connection broken",
    "connection forcibly closed",
    "server has closed",
    "lost connection",
    "general network error",
    "transport-level error",
)


def _is_transient_db_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(s in msg for s in _TRANSIENT_DB_SUBSTRINGS)


def _run_with_retry(fn):
    """Run a no-arg callable, retrying once on transient pyodbc errors.

    `fn` should be a closure that opens its own connection via `_borrow_conn`
    so each attempt gets a fresh borrow. Non-transient errors (schema, syntax,
    bad credentials) raise immediately. The first transient failure is logged
    best-effort to the `errors` table before the retry.
    """
    last_err = None
    for attempt in range(2):
        try:
            return fn()
        except pyodbc.Error as e:
            if not _is_transient_db_error(e):
                raise
            last_err = e
            if attempt == 0:
                # Best-effort log of the first failure (lazy import so
                # db.database stays loadable without db.logger).
                try:
                    from db.logger import log_error
                    log_error(
                        "db", e,
                        context={"fn": getattr(fn, "__name__", "?"), "attempt": 1},
                    )
                except Exception:
                    pass
                # Jittered 1s backoff — gives the upstream a moment to recover
                # and decorrelates retries across concurrent sessions.
                time.sleep(max(0.0, 1.0 + random.uniform(-0.3, 0.3)))
                continue
            raise
    if last_err is not None:
        raise last_err


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

DROP TABLE IF EXISTS #PhysiciansForDay, #FreeSlots;

-- 1) Physicians who have at least one appointment matching the specialty /
-- doctor filter on @ReportDate. The bot only needs identity + specialty
-- labels; we drop the prior query's TotalPatients, FirstAppt, LastAppt and
-- the chain of derived temp tables that fed reporting metrics nothing in
-- the conversational flow ever reads.
SELECT ap.PhysicianID,
       MAX(p.PhysicianEnName)  AS Doctor,
       MAX(p.PhysicianArName)  AS DoctorAR,
       MAX(ap.SpecialtyEnName) AS Specialty,
       MAX(ap.SpecialtyArName) AS SpecialtyAR
INTO #PhysiciansForDay
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

CREATE CLUSTERED INDEX IX_PhysiciansForDay ON #PhysiciansForDay (PhysicianID);

-- 2) Free slots on @ReportDate for those physicians. Inner-joined to
-- #PhysiciansForDay so we never scan slots for doctors outside the filter.
SELECT p.PhysicianID,
       sl.StartDate,
       sl.StartTime
INTO #FreeSlots
FROM opd.BK_Slot sl
LEFT JOIN OPD.BK_Appointment ap ON sl.ID = ap.SlotID
INNER JOIN OPD.BK_PatternInstance pl ON sl.PatternInstanceID = pl.ID
INNER JOIN [OPD].PHS_OPDPattern p ON pl.PatternID = p.ID AND pl.PhysicianID = p.PhysicianID
INNER JOIN #PhysiciansForDay pfd ON pfd.PhysicianID = p.PhysicianID
WHERE ap.SlotID IS NULL
  AND sl.StartDate = @ReportDate
  AND (sl.StartDate > CAST(GETDATE() AS DATE)
       OR (sl.StartDate = CAST(GETDATE() AS DATE) AND sl.StartTime >= CAST(GETDATE() AS TIME)))
  AND p.IsDeleted = 0
  AND (p.EndDate IS NULL OR CONVERT(DATE, pl.StartDateTime) BETWEEN p.StartDate AND p.EndDate);

CREATE CLUSTERED INDEX IX_FreeSlots ON #FreeSlots (PhysicianID, StartDate, StartTime);

-- Final result. LEFT JOIN preserves physicians without free slots so the
-- Python aggregator's "drop rows with no Slot_Date" filter still applies
-- consistently (matching the prior query's behaviour).
SELECT pfd.Specialty,
       pfd.SpecialtyAR,
       pfd.Doctor,
       pfd.DoctorAR,
       pfd.PhysicianID,
       fs.StartDate AS Slot_Date,
       fs.StartTime AS Slot_Time,
       DATEDIFF(DAY, GETDATE(), fs.StartDate) AS DaysFromToday
FROM #PhysiciansForDay pfd
LEFT JOIN #FreeSlots fs ON fs.PhysicianID = pfd.PhysicianID
ORDER BY pfd.Specialty, pfd.Doctor, fs.StartDate, fs.StartTime;

DROP TABLE IF EXISTS #PhysiciansForDay, #FreeSlots;
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
    def _do_query_availability():
        with _borrow_conn() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(SQL_QUERY, (report_date, specialty_ar, specialty_en, doctor_ar, doctor_en))
                columns = [col[0] for col in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
            finally:
                cursor.close()
    return _run_with_retry(_do_query_availability)


def query_availability_with_fallback(
    specialty_en: str = None,
    specialty_ar: str = None,
    doctor_en: str = None,
    doctor_ar: str = None,
    preferred_date: str = None,
    max_days_ahead: int = 7,
) -> tuple[list[dict], str]:
    """
    Try preferred_date first (or today), then search forward up to max_days_ahead.
    Returns (rows, date_used_iso).
    """
    start = date.today()
    if preferred_date:
        try:
            start = datetime.strptime(preferred_date, "%Y-%m-%d").date()
            # Never search in the past — a stale requested_date would otherwise
            # silently downgrade to today's results.
            if start < date.today():
                start = date.today()
        except (ValueError, TypeError):
            pass

    for offset in range(max_days_ahead + 1):
        check_date = start + timedelta(days=offset)
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
    return [], start.strftime("%Y-%m-%d")


def query_doctor_slots(
    report_date: str,
    doctor_en: str = None,
    doctor_ar: str = None,
) -> list:
    def _do_query_doctor_slots():
        with _borrow_conn() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(SLOTS_QUERY, (doctor_en, doctor_ar, report_date))
                columns = [col[0] for col in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
            finally:
                cursor.close()
    return _run_with_retry(_do_query_doctor_slots)


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