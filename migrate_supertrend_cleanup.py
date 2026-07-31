"""
migrate_supertrend_cleanup.py
-------------------------------
One-time reset script for the Supertrend 3-Phase episode ledger, to be run
ONCE after upgrading to the state-machine version of supertrend_pattern.py
(July 2026 rewrite).

WHY A FULL RESET INSTEAD OF A TARGETED PATCH:
  The previous cleanup script re-validated one specific bug (the Phase 1
  "green line >= 200 EMA" gate) by re-running that single check against rows
  already in the DB. That worked because the bug only affected the validity
  of a gate the code checked -- it never changed WHICH day was treated as
  phase1_start.

  The state-machine rewrite goes further: it also enforces the Phase 2
  "red line crossed below 200 EMA" mandatory condition, and changes Phase 3
  completion to require (ST line >= 200 EMA) AND (ST line > X) to hold
  SIMULTANEOUSLY, rather than as two separately-satisfied criteria. Some
  episodes that used to reach "complete" now get discarded during Phase 2 or
  Phase 3, and their phase1_start / phase2_start / phase3_start anchors can
  shift entirely once invalid episodes stop absorbing flips that a valid
  episode should have started from. Patching individual rows in place can no
  longer guarantee correctness -- only a full re-scan with the corrected
  detector can.

  The good news: this is a one-time cost. Going forward, invalid episodes are
  never written to the ledger in the first place (the state machine discards
  them mid-scan), and the dashboard's own upsert logic
  (levels_store._upsert_supertrend_conn) already deletes any row for a symbol
  whose (phase1_start, st_period, st_multiplier) is no longer produced by the
  latest scan. So this script should not need a successor.

WHAT THIS SCRIPT DOES:
  Simply truncates the supertrend_episodes table. It does NOT try to
  re-implement the detection logic -- that logic already lives in (and only
  in) supertrend_pattern.compute_supertrend / find_supertrend_episodes, and
  duplicating it here is exactly the maintenance trap we're trying to avoid.

USAGE:
  Run this ONCE from your project folder AFTER replacing supertrend_pattern.py
  with the state-machine version, and BEFORE the next full dashboard refresh:

      python migrate_supertrend_cleanup.py

  Then run the dashboard once (or your scheduled refresh job) -- it will
  re-populate the table from scratch using the corrected detector for every
  symbol and every (period, multiplier) variant configured in the sidebar.

SAFETY:
  - Only touches the supertrend_episodes table. Does NOT touch any other
    table (reference_levels, five_leg_episodes, ema_pullback_episodes, etc.).
  - Prints a before/after row count so you can confirm the reset happened.
  - Safe to run multiple times -- idempotent (deleting an empty table is a no-op).
  - first_seen_at history is lost for all episodes on this one reset, since
    every row is being re-detected fresh; this is expected and unavoidable
    given the anchor points themselves can shift under the corrected logic.
"""

import sqlite3
import sys
from pathlib import Path

HERE    = Path(__file__).parent
DB_PATH = HERE / "levels_ledger.db"

if not DB_PATH.exists():
    sys.exit(f"[migrate] DB not found at {DB_PATH}")


def main():
    conn = sqlite3.connect(str(DB_PATH))

    before = conn.execute("SELECT COUNT(*) FROM supertrend_episodes").fetchone()[0]
    print(f"[migrate] supertrend_episodes currently has {before} row(s).")

    if before == 0:
        print("[migrate] Nothing to reset. Table is already empty.")
        conn.close()
        return

    try:
        conn.execute("DELETE FROM supertrend_episodes")
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM supertrend_episodes").fetchone()[0]
        print(f"[migrate] Deleted {before - after} row(s). supertrend_episodes now has {after} row(s).")
    except Exception as e:
        conn.rollback()
        print(f"[migrate] ERROR during reset -- rolled back: {e}")
    finally:
        conn.close()

    print("\n[migrate] Done. Run the dashboard once (with the state-machine "
          "supertrend_pattern.py in place) to re-populate all valid episodes.")


if __name__ == "__main__":
    main()
