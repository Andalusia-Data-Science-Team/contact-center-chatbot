"""
Debugging helper — verify the CRM (Dynamics) connection and that walk-in
prices are being retrieved for named doctors.

Usage:
    python -m scripts.check_crm_prices                  # dump all loaded doctors
    python -m scripts.check_crm_prices "Ameer Elsayed"  # look up one by name
"""
import sys
import traceback
from dotenv import load_dotenv

load_dotenv()

from config.settings import (
    CRM_CLIENT_ID,
    CRM_DOCTOR_TABLE,
    CRM_FEE_TABLE,
    CRM_PASSWORD,
    CRM_SERVER,
    CRM_TENANT,
    CRM_USERNAME,
)


def print_config():
    print("── CRM config ──")
    print(f"  CRM_SERVER       = {CRM_SERVER!r}")
    print(f"  CRM_CLIENT_ID    = {CRM_CLIENT_ID!r}")
    print(f"  CRM_TENANT       = {CRM_TENANT!r}")
    print(f"  CRM_USERNAME     = {CRM_USERNAME!r}")
    print(f"  CRM_PASSWORD     = {'***set***' if CRM_PASSWORD else '<EMPTY>'}")
    print(f"  CRM_DOCTOR_TABLE = {CRM_DOCTOR_TABLE!r}")
    print(f"  CRM_FEE_TABLE    = {CRM_FEE_TABLE!r}")
    missing = [n for n, v in [("CRM_SERVER", CRM_SERVER)] if not v]
    if missing:
        print(f"  ⚠️  Missing required vars: {missing}")
        return False
    if not CRM_PASSWORD:
        print("  ℹ️  No CRM_PASSWORD — will try silent cache, then fall back to")
        print("     interactive (browser) login. Your test script uses the same flow.")
    return True


def test_auth():
    print("\n── Auth (MSAL) ──")
    try:
        from db.crm_database import _get_token
        tok = _get_token()
        if tok:
            print(f"  ✅ access token acquired (len={len(tok)})")
            return True
        print("  ❌ token is None — check that _is_configured() sees the vars")
        return False
    except Exception as e:
        print(f"  ❌ auth failed: {e}")
        traceback.print_exc()
        return False


def test_connection():
    # Dataverse TDS often drops the first execute on a fresh connection; go
    # through the retry-aware helper so a one-off drop doesn't fail the check.
    print("\n── Connection (pyodbc + token) ──")
    try:
        from db.crm_database import _run_query_with_retry
        rows = _run_query_with_retry("SELECT 1 AS n")
        print(f"  ✅ connected — SELECT 1 = {rows[0]['n']}")
        return True
    except Exception as e:
        print(f"  ❌ connection failed: {e}")
        traceback.print_exc()
        return False


def test_query():
    print(f"\n── Query {CRM_DOCTOR_TABLE} JOIN {CRM_FEE_TABLE} ──")
    try:
        from db.crm_database import _run_query_with_retry
        rows = _run_query_with_retry(
            f"SELECT TOP 3 "
            f"D.[servhub_doctornameen], D.[cr301_doctornamear], "
            f"F.[cr301_walkinconsultationfees] "
            f"FROM {CRM_DOCTOR_TABLE} D "
            f"LEFT JOIN {CRM_FEE_TABLE} F "
            f"  ON D.[cr301_doctorkey] = F.[cr301_doctorkey] "
            f"WHERE D.[cr301_opdflag] = 'OPD' "
            f"  AND D.[servhub_doctornameen] IS NOT NULL"
        )
        if not rows:
            print("  ⚠️  no matching rows — check table names or OPD filter")
            return False
        print(f"  ✅ got {len(rows)} sample rows:")
        for r in rows:
            print(f"     {r}")
        return True
    except Exception as e:
        print(f"  ❌ query failed: {e}")
        traceback.print_exc()
        return False


def lookup_doctor(query: str):
    print(f"\n── Doctor lookup: {query!r} ──")
    from services.doctor_price import find_crm_doctor, get_walk_in_price
    row = find_crm_doctor(query)
    if row is None:
        print("  ❌ no CRM match — try a different spelling, or check that the name")
        print("     exists in servhub_doctornameen for the loaded rows")
    else:
        print(f"  ✅ matched CRM name: {row.get('DoctorEn')!r}")
        print(f"     walk-in price:    {get_walk_in_price(query)}")


def main() -> int:
    if not print_config():
        return 1
    if not test_auth():
        return 1
    if not test_connection():
        return 1
    if not test_query():
        return 1
    if len(sys.argv) > 1:
        lookup_doctor(" ".join(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
