"""
Verify the pediatric specialty names in config/constants.py exist in the live
hospital DB. Prints any mismatches so we can correct the constants (or the
ped_map in services/router.py) before they cause silent dead-ends.

Run on a machine that can reach the DB:
    python scripts/verify_pediatric_specialties.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from db.database import get_connection
from config.constants import PEDIATRIC_SPECIALTIES_EN


DISTINCT_QUERY = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;
SELECT DISTINCT ap.SpecialtyEnName
FROM OPD.BK_Appointment ap
WHERE ap.SpecialtyEnName IS NOT NULL
ORDER BY ap.SpecialtyEnName;
"""


def main():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(DISTINCT_QUERY)
        rows = [r[0] for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()

    db_set = set(rows)
    print(f"DB has {len(db_set)} distinct SpecialtyEnName values\n")

    print("=== Pediatric specialties from constants.py ===")
    missing = []
    present = []
    for name in PEDIATRIC_SPECIALTIES_EN:
        if name in db_set:
            present.append(name)
            print(f"  OK     {name!r}")
        else:
            missing.append(name)
            print(f"  MISSING {name!r}")

    if missing:
        print("\n=== Possible matches in DB for missing names ===")
        for name in missing:
            tokens = [t.lower() for t in name.split() if len(t) > 2]
            candidates = [
                d for d in db_set
                if any(t in d.lower() for t in tokens)
            ]
            print(f"  {name!r} → {candidates or 'NO close match found'}")

    print(f"\n{len(present)} present, {len(missing)} missing")
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
