"""
reset_all_signals.py
---------------------
Truncates ALL generated signals from levels_ledger.db so the next dashboard
refresh recomputes everything from scratch using the current detection code.

WHAT IS RESET (all 7 signal tables):
  reference_levels            — Supertrend streak-based levels
  monthly_pivot_episodes      — Monthly Pivot S1 episodes
  monthly_s1_shift_episodes   — Monthly S1 Shift Up episodes
  five_leg_episodes           — 5-Leg EMA Reversal episodes
  breakout_pullback_episodes  — Breakout-Pullback 4-Leg episodes
  ema_pullback_episodes       — EMA Pullback Reentry episodes
  supertrend_episodes         — Supertrend 3-Phase episodes

WHAT IS NOT TOUCHED:
  price_cache/*.parquet       — Raw OHLCV history. Keeping this means the
                                next refresh recomputes signals from already-
                                cached price data with NO Yahoo re-downloads
                                (except for the few new days since last run).
  symbols_cache.json          — Nifty 100 constituent list. Unaffected.
  DB schema                   — Tables and columns are preserved. Only rows
                                are deleted. New columns added this session
                                (s1_shift_pct, qualify_duration_days,
                                x_vs_200ema_pct) are kept in the schema so
                                the next refresh writes into them correctly.
  first_seen_at history       — Lost for all episodes, since every row is
                                being deleted. This is unavoidable and
                                expected — all episodes will look "new" after
                                the reset.

WHEN TO USE THIS:
  - After a significant code change to any detection strategy (e.g. the
    _extends() docstring clarification, mandatory_condition initialisation
    fix) where you want a guaranteed clean slate rather than relying on
    upsert-and-purge to converge.
  - Any time you suspect stale or corrupt rows and want to be certain the
    dashboard reflects only what the current code produces.
  - One-time use after applying the new columns (s1_shift_pct,
    qualify_duration_days, x_vs_200ema_pct) — existing rows will have NULL
    for these; a full reset ensures every row gets the real computed value
    on the next refresh rather than displaying NULL until that symbol
    happens to be re-scanned.

USAGE:
    python reset_all_signals.py          # dry run — shows counts, does nothing
    python reset_all_signals.py --confirm  # actually deletes all rows

SAFETY:
  - Dry-run by default (--confirm required to write).
  - Prints before/after row counts for every table.
  - Wrapped in a single transaction — if anything fails, the whole
    reset is rolled back and the DB is left exactly as it was.
  - Idempotent: running it on an already-empty DB is a no-op.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

HERE    = Path(__file__).parent
DB_PATH = HERE / "levels_ledger.db"

TABLES = [
    "reference_levels",
    "monthly_pivot_episodes",
    "monthly_s1_shift_episodes",
    "five_leg_episodes",
    "breakout_pullback_episodes",
    "ema_pullback_episodes",
    "supertrend_episodes",
]


def count_rows(conn: sqlite3.Connection) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def main(confirm: bool):
    if not DB_PATH.exists():
        sys.exit(f"[reset] DB not found at {DB_PATH} — nothing to do.")

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    before = count_rows(conn)
    total_before = sum(before.values())

    print(f"[reset] levels_ledger.db — {total_before} total signal row(s) across all tables\n")
    col_w = max(len(t) for t in TABLES) + 2
    for t, n in before.items():
        print(f"  {t:<{col_w}} {n:>6} row(s)")

    if total_before == 0:
        print("\n[reset] All tables already empty — nothing to do.")
        conn.close()
        return

    if not confirm:
        print(f"\n[reset] DRY RUN — no changes made.")
        print(f"[reset] Re-run with --confirm to delete all {total_before} row(s).")
        conn.close()
        return

    print(f"\n[reset] --confirm passed. Deleting all {total_before} row(s) in one transaction...")
    try:
        for t in TABLES:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        sys.exit(f"[reset] ERROR — rolled back, DB unchanged: {e}")

    after = count_rows(conn)
    conn.close()

    total_after = sum(after.values())
    print("\n[reset] After reset:")
    for t, n in after.items():
        print(f"  {t:<{col_w}} {n:>6} row(s)")

    if total_after == 0:
        print(f"\n[reset] Done. All {total_before} signal row(s) deleted.")
        print("[reset] Schema (tables + columns) is intact.")
        print("[reset] Run the dashboard once to regenerate all signals from cached price history.")
    else:
        print(f"\n[reset] WARNING: {total_after} row(s) remain after reset — unexpected.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete all signal rows. Without this flag, only a dry run is performed.",
    )
    args = parser.parse_args()
    main(confirm=args.confirm)
