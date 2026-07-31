"""
verify_price_cache_integrity.py
---------------------------------
ONE-TIME check to run after applying the price_cache.py atomic-write fix.

WHY:
  Before the fix, a process killed mid-write (Streamlit hot-reload, OOM-kill,
  Ctrl+C) could leave a truncated/corrupt .parquet file behind for whichever
  symbol was being saved at that moment. The fix prevents this from
  happening AGAIN, but can't repair damage that already happened under the
  old code. This script finds any pre-existing corruption so you know
  whether anything needs reseeding, rather than silently carrying a broken
  cache file forward.

WHAT IT DOES:
  - Tries to read every symbol's cached parquet file exactly the way
    price_cache._load_cached() does.
  - Reports any file that fails to read, or reads but is empty/has an
    unexpected shape (missing OHLC columns).
  - With --fix, deletes any bad files found. This does NOT lose data
    permanently: get_full_history() will simply reseed that symbol with
    Yahoo's full available history (period="max") the next time it's
    requested -- exactly the same self-healing path that already runs
    today when a cache file is missing or unreadable.
  - Read-only by default (no --fix) -- just reports, changes nothing.

USAGE:
    python verify_price_cache_integrity.py            # report only
    python verify_price_cache_integrity.py --fix       # report + delete bad files
"""
import argparse
import os
import sys

import pandas as pd

import price_cache

REQUIRED_COLUMNS = {"Open", "High", "Low", "Close"}


def check_all(fix: bool):
    if not os.path.isdir(price_cache.CACHE_DIR):
        print(f"[verify] No cache directory found at {price_cache.CACHE_DIR} -- nothing to check.")
        return

    files = sorted(f for f in os.listdir(price_cache.CACHE_DIR) if f.endswith(".parquet"))
    if not files:
        print("[verify] No cached symbols found -- nothing to check.")
        return

    print(f"[verify] Checking {len(files)} cached symbol(s) in {price_cache.CACHE_DIR} ...\n")

    bad = []
    for fname in files:
        symbol = fname[: -len(".parquet")]
        path = os.path.join(price_cache.CACHE_DIR, fname)
        try:
            df = pd.read_parquet(path)
        except Exception as e:
            bad.append((symbol, path, f"failed to read: {e}"))
            continue

        if df.empty:
            bad.append((symbol, path, "reads OK but is empty"))
            continue

        missing_cols = REQUIRED_COLUMNS - set(df.columns)
        if missing_cols:
            bad.append((symbol, path, f"missing expected columns: {sorted(missing_cols)}"))
            continue

    if not bad:
        print(f"[verify] All {len(files)} cache file(s) look healthy. Nothing to do.")
        return

    print(f"[verify] Found {len(bad)} problem file(s):\n")
    for symbol, path, reason in bad:
        print(f"  {symbol:15s} {reason}")

    if fix:
        print(f"\n[verify] --fix passed: deleting {len(bad)} bad file(s). "
              f"These symbols will reseed with full history on next use.")
        for symbol, path, _ in bad:
            try:
                os.remove(path)
                print(f"  Deleted {path}")
            except OSError as e:
                print(f"  Could not delete {path}: {e}")
    else:
        print(f"\n[verify] Re-run with --fix to delete these {len(bad)} file(s) "
              f"(each will reseed automatically with full history the next time "
              f"the dashboard requests that symbol -- no manual reseed step needed).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="Delete any corrupted cache files found.")
    args = parser.parse_args()
    check_all(fix=args.fix)
