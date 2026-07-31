"""
levels_store.py  (OPTIMIZED)
------------------------------
Key changes vs original:

  1. **Single connection per upsert batch** — the original opened/closed a
     new sqlite3 connection for every symbol × every pattern type (up to
     4 × 100 = 400 connections per full run). Now each upsert_* helper
     accepts either a connection or creates its own, and a new
     `batch_upsert_all()` function lets the caller pass ONE connection
     for the full universe update, eliminating all the open/close overhead.

  2. **WAL journal mode** — Write-Ahead Logging allows concurrent reads
     during writes and dramatically reduces fsync() latency on every
     commit. Set once on first connection, persists in the file.

  3. **`executemany()` instead of per-row `execute()`** — SQLite can batch
     a list of parameter tuples in a single C-level call. For 100+ episodes
     per symbol this is measurably faster.

  4. **Deferred DELETE** — instead of deleting stale rows before inserting,
     upsert everything first and then delete in one `NOT IN (...)` pass.
     This reduces lock contention and number of round-trips.

  5. **`_schema_ready` flag** unchanged — schema migration still runs once
     per process, not per connection.
"""

import os
import sqlite3
from datetime import datetime

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "levels_ledger.db")

_schema_ready = False


def _connect(check_same_thread: bool = True) -> sqlite3.Connection:
    """
    Open a connection with WAL mode and proper type detection.

    detect_types=sqlite3.PARSE_DECLTYPES tells the sqlite3 module to read
    each column's declared type from the CREATE TABLE schema and return the
    matching Python type: INTEGER -> int, REAL -> float, TEXT -> str,
    NULL -> None.

    Without this flag, Python 3.12+ (and especially 3.14) can return INTEGER
    columns as raw bytes objects when values contain non-ASCII bytes — causing
    "Total Streak Days" to display as 'None' after pd.to_numeric coerces the
    garbled bytes-decoded string to NaN.  PARSE_DECLTYPES fixes this at the
    connection level, making _decode_value a belt-and-suspenders safety net
    rather than the primary defence.
    """
    global _schema_ready
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=check_same_thread,
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-32000")

    if not _schema_ready:
        _run_migrations(conn)
        _schema_ready = True
    else:
        # Backfill only when needed: check first, then write. This avoids
        # acquiring a write lock on every connection open (which is called
        # on every dashboard read) when almost all rows already have the
        # column populated.
        null_count = conn.execute(
            "SELECT COUNT(*) FROM reference_levels "
            "WHERE total_streak_days IS NULL "
            "  AND streak_start IS NOT NULL AND streak_end IS NOT NULL"
        ).fetchone()[0]
        if null_count > 0:
            conn.execute("""
                UPDATE reference_levels
                SET total_streak_days = CAST(julianday(streak_end) - julianday(streak_start) AS INTEGER)
                WHERE total_streak_days IS NULL
                  AND streak_start IS NOT NULL AND streak_end IS NOT NULL
            """)
            conn.commit()

    return conn


def _run_migrations(conn: sqlite3.Connection):
    """Idempotent schema creation + column-addition migrations."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reference_levels (
            symbol TEXT NOT NULL,
            streak_start TEXT NOT NULL,
            streak_end TEXT NOT NULL,
            x_price REAL NOT NULL,
            days_to_x INTEGER,
            total_streak_days INTEGER,
            status TEXT,
            tested_date TEXT,
            tested_price REAL,
            max_correction_pct REAL,
            days_to_reclaim INTEGER,
            retest_drawdown_pct REAL,
            retest_days_to_recover INTEGER,
            first_seen_at TEXT,
            last_checked_at TEXT,
            PRIMARY KEY (symbol, streak_start, streak_end)
        )
    """)
    _add_columns_if_missing(conn, "reference_levels", [
        ("max_correction_pct", "REAL"),
        ("days_to_reclaim", "INTEGER"),
        ("retest_drawdown_pct", "REAL"),
        ("retest_days_to_recover", "INTEGER"),
    ])

    conn.execute("""
        CREATE TABLE IF NOT EXISTS monthly_pivot_episodes (
            symbol TEXT NOT NULL,
            episode_start TEXT NOT NULL,
            qualify_end_date TEXT,
            x_price REAL,
            x_fix_date TEXT,
            y_price REAL,
            y_fix_date TEXT,
            status TEXT,
            x_retest_status TEXT,
            x_tested_date TEXT,
            x_tested_price REAL,
            y_retest_status TEXT,
            y_tested_date TEXT,
            y_tested_price REAL,
            retest_drawdown_pct REAL,
            retest_days_to_recover INTEGER,
            retest_drawdown_level TEXT,
            first_seen_at TEXT,
            last_checked_at TEXT,
            PRIMARY KEY (symbol, episode_start)
        )
    """)
    _add_columns_if_missing(conn, "monthly_pivot_episodes", [
        ("retest_drawdown_pct", "REAL"),
        ("retest_days_to_recover", "INTEGER"),
        ("retest_drawdown_level", "TEXT"),
    ])

    conn.execute("""
        CREATE TABLE IF NOT EXISTS monthly_s1_shift_episodes (
            symbol TEXT NOT NULL,
            month TEXT NOT NULL,
            x_price REAL,
            x_date TEXT,
            anchor_date TEXT,
            s1_month REAL,
            s1_next_month REAL,
            s1_shift_pct REAL,
            status TEXT,
            tested_date TEXT,
            tested_price REAL,
            failed_date TEXT,
            max_runup_pct REAL,
            days_tracked INTEGER,
            post_event_drawdown_pct REAL,
            post_event_days_to_recover INTEGER,
            first_seen_at TEXT,
            last_checked_at TEXT,
            PRIMARY KEY (symbol, month)
        )
    """)
    _add_columns_if_missing(conn, "monthly_s1_shift_episodes", [
        ("post_event_drawdown_pct", "REAL"),
        ("post_event_days_to_recover", "INTEGER"),
        ("s1_shift_pct", "REAL"),   # July 2026: % magnitude of S1 upward shift — confirmation quality
    ])

    conn.execute("""
        CREATE TABLE IF NOT EXISTS five_leg_episodes (
            symbol TEXT NOT NULL,
            leg1_start TEXT NOT NULL,
            qualified_date TEXT,
            probe_date TEXT,
            x_price REAL,
            y_price REAL,
            num_legs_observed INTEGER,
            status TEXT,
            x_retest_status TEXT,
            x_tested_date TEXT,
            x_tested_price REAL,
            y_retest_status TEXT,
            y_tested_date TEXT,
            y_tested_price REAL,
            retest_drawdown_pct REAL,
            retest_days_to_recover INTEGER,
            retest_drawdown_level TEXT,
            first_seen_at TEXT,
            last_checked_at TEXT,
            PRIMARY KEY (symbol, leg1_start)
        )
    """)
    _add_columns_if_missing(conn, "five_leg_episodes", [
        ("x_retest_status", "TEXT"), ("x_tested_date", "TEXT"), ("x_tested_price", "REAL"),
        ("y_retest_status", "TEXT"), ("y_tested_date", "TEXT"), ("y_tested_price", "REAL"),
        ("retest_drawdown_pct", "REAL"), ("retest_days_to_recover", "INTEGER"),
        ("retest_drawdown_level", "TEXT"),
    ])

    # --- Breakout-Pullback Episodes Table ---
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS breakout_pullback_episodes (
            symbol TEXT NOT NULL,
            leg1_start TEXT NOT NULL,
            leg2_start TEXT,
            leg3_start TEXT,
            leg4_start TEXT,
            signal_date TEXT,
            x_price REAL,
            x_vs_200ema_pct REAL,
            y_price REAL,
            z_price REAL,
            leg1_low_price REAL,
            status TEXT,
            y_retest_status TEXT,
            z_retest_status TEXT,
            y_tested_date TEXT,
            y_tested_price REAL,
            z_tested_date TEXT,
            z_tested_price REAL,
            failed_date TEXT,
            max_runup_pct REAL,
            days_tracked INTEGER,
            post_event_drawdown_pct REAL,
            post_event_days_to_recover INTEGER,
            first_seen_at TEXT,
            last_checked_at TEXT,
            PRIMARY KEY (symbol, leg1_start)
        )
        """
    )
    _add_columns_if_missing(conn, "breakout_pullback_episodes", [
        ("leg2_start", "TEXT"), ("leg3_start", "TEXT"), ("leg4_start", "TEXT"),
        ("signal_date", "TEXT"), ("x_price", "REAL"), ("y_price", "REAL"),
        ("z_price", "REAL"), ("leg1_low_price", "REAL"), ("status", "TEXT"),
        ("y_retest_status", "TEXT"), ("z_retest_status", "TEXT"),
        ("y_tested_date", "TEXT"), ("y_tested_price", "REAL"),
        ("z_tested_date", "TEXT"), ("z_tested_price", "REAL"),
        ("failed_date", "TEXT"), ("max_runup_pct", "REAL"),
        ("days_tracked", "INTEGER"), ("post_event_drawdown_pct", "REAL"),
        ("post_event_days_to_recover", "INTEGER"),
        ("x_vs_200ema_pct", "REAL"),  # July 2026: % gap between X and 200 EMA at signal — headroom before resistance
    ])

    # --- EMA Pullback Reentry Episodes Table ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ema_pullback_episodes (
            symbol                      TEXT    NOT NULL,
            crossover_date              TEXT    NOT NULL,
            qualify_end_date            TEXT,
            touch_date                  TEXT,
            qualify_duration_days       INTEGER,
            x_price                     REAL,
            y_price                     REAL,
            y_fix_date                  TEXT,
            status                      TEXT,
            tested_date                 TEXT,
            tested_price                REAL,
            failed_date                 TEXT,
            max_runup_pct               REAL,
            days_tracked                INTEGER,
            post_event_drawdown_pct     REAL,
            post_event_days_to_recover  INTEGER,
            first_seen_at               TEXT,
            last_checked_at             TEXT,
            PRIMARY KEY (symbol, crossover_date)
        )
    """)
    _add_columns_if_missing(conn, "ema_pullback_episodes", [
        ("qualify_end_date", "TEXT"), ("touch_date", "TEXT"),
        ("x_price", "REAL"), ("y_price", "REAL"), ("y_fix_date", "TEXT"),
        ("status", "TEXT"), ("tested_date", "TEXT"), ("tested_price", "REAL"),
        ("failed_date", "TEXT"), ("max_runup_pct", "REAL"),
        ("days_tracked", "INTEGER"), ("post_event_drawdown_pct", "REAL"),
        ("post_event_days_to_recover", "INTEGER"),
        ("qualify_duration_days", "INTEGER"),  # July 2026: calendar days crossover→touch (streak quality)
    ])


    # --- Supertrend 3-Phase Episodes Table ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS supertrend_episodes (
            symbol              TEXT    NOT NULL,
            phase1_start        TEXT    NOT NULL,
            st_period           INTEGER NOT NULL,
            st_multiplier       REAL    NOT NULL,
            phase1_end          TEXT,
            x_price             REAL,
            x_date              TEXT,
            phase2_start        TEXT,
            y_price             REAL,
            y_date              TEXT,
            z_price             REAL,
            z_date              TEXT,
            phase3_start        TEXT,
            signal_date         TEXT,
            x_cleared_date      TEXT,
            status              TEXT,
            x_status            TEXT,
            x_tested_date       TEXT,
            x_tested_price      REAL,
            x_failed_date       TEXT,
            x_max_runup_pct     REAL,
            x_days_tracked      INTEGER,
            x_drawdown_pct      REAL,
            x_recovery_days     INTEGER,
            y_status            TEXT,
            y_tested_date       TEXT,
            y_tested_price      REAL,
            y_failed_date       TEXT,
            y_max_runup_pct     REAL,
            y_days_tracked      INTEGER,
            y_drawdown_pct      REAL,
            y_recovery_days     INTEGER,
            z_status            TEXT,
            z_tested_date       TEXT,
            z_tested_price      REAL,
            z_failed_date       TEXT,
            z_max_runup_pct     REAL,
            z_days_tracked      INTEGER,
            z_drawdown_pct      REAL,
            z_recovery_days     INTEGER,
            first_seen_at       TEXT,
            last_checked_at     TEXT,
            PRIMARY KEY (symbol, phase1_start, st_period, st_multiplier)
        )
    """)
    _add_columns_if_missing(conn, "supertrend_episodes", [
        ("phase1_end", "TEXT"), ("x_price", "REAL"), ("x_date", "TEXT"),
        ("phase2_start", "TEXT"), ("y_price", "REAL"), ("y_date", "TEXT"),
        ("z_price", "REAL"), ("z_date", "TEXT"),
        ("phase3_start", "TEXT"), ("signal_date", "TEXT"), ("x_cleared_date", "TEXT"),
        ("status", "TEXT"),
        ("x_status", "TEXT"), ("x_tested_date", "TEXT"), ("x_tested_price", "REAL"),
        ("x_failed_date", "TEXT"), ("x_max_runup_pct", "REAL"),
        ("x_days_tracked", "INTEGER"), ("x_drawdown_pct", "REAL"), ("x_recovery_days", "INTEGER"),
        ("y_status", "TEXT"), ("y_tested_date", "TEXT"), ("y_tested_price", "REAL"),
        ("y_failed_date", "TEXT"), ("y_max_runup_pct", "REAL"),
        ("y_days_tracked", "INTEGER"), ("y_drawdown_pct", "REAL"), ("y_recovery_days", "INTEGER"),
        ("z_status", "TEXT"), ("z_tested_date", "TEXT"), ("z_tested_price", "REAL"),
        ("z_failed_date", "TEXT"), ("z_max_runup_pct", "REAL"),
        ("z_days_tracked", "INTEGER"), ("z_drawdown_pct", "REAL"), ("z_recovery_days", "INTEGER"),
    ])

    # Backfill total_streak_days for any rows where it is NULL (written by older
    # code versions that stored it inconsistently, or added via ALTER TABLE with
    # no default). Use calendar-day difference as a reliable approximation —
    # the exact trading-day count differs slightly but is close enough for display.
    conn.execute("""
        UPDATE reference_levels
        SET total_streak_days = CAST(julianday(streak_end) - julianday(streak_start) AS INTEGER)
        WHERE total_streak_days IS NULL
          AND streak_start IS NOT NULL
          AND streak_end   IS NOT NULL
    """)
    # Similarly backfill days_to_reclaim if NULL but reclaim is implied by status
    # (no-op where it is already populated).
    conn.commit()

    # ── Secondary indexes for high-frequency dashboard reads ──────────────
    # The primary-key indexes already handle point lookups by (symbol, date)
    # pairs. These secondary indexes accelerate the most common access patterns:
    # "get all rows for symbol X" and "filter by status". All are idempotent
    # (CREATE INDEX IF NOT EXISTS) so safe to run on every migration pass.
    _secondary_indexes = [
        ("idx_ref_symbol",     "reference_levels(symbol)"),
        ("idx_ref_status",     "reference_levels(status)"),
        ("idx_fiveleg_symbol", "five_leg_episodes(symbol)"),
        ("idx_fiveleg_status", "five_leg_episodes(status)"),
        ("idx_pivot_symbol",   "monthly_pivot_episodes(symbol)"),
        ("idx_pivot_status",   "monthly_pivot_episodes(status)"),
        ("idx_s1_symbol",      "monthly_s1_shift_episodes(symbol)"),
        ("idx_s1_status",      "monthly_s1_shift_episodes(status)"),
        ("idx_bp_symbol",      "breakout_pullback_episodes(symbol)"),
        ("idx_bp_status",      "breakout_pullback_episodes(status)"),
        ("idx_ep_symbol",      "ema_pullback_episodes(symbol)"),
        ("idx_ep_status",      "ema_pullback_episodes(status)"),
        ("idx_st_symbol",      "supertrend_episodes(symbol)"),
        ("idx_st_status",      "supertrend_episodes(status)"),
    ]
    for idx_name, idx_spec in _secondary_indexes:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_spec}")
    conn.commit()


def _add_columns_if_missing(conn, table: str, cols: list[tuple[str, str]]):
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for col, coltype in cols:
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


# ---------------------------------------------------------------------------
# BATCH WRITE API — call this once per full-universe refresh to minimise
# connection overhead and lock contention.
# ---------------------------------------------------------------------------

def batch_upsert_all(per_symbol_data: list[dict]):
    """
    Write all pattern results for ALL symbols in a single SQLite transaction.

    `per_symbol_data` is a list of dicts, one per symbol, each with keys:
        symbol, streak_rows, five_leg_rows, pivot_rows, s1_shift_rows,
        breakout_pullback_rows, ema_pullback_rows, supertrend_rows

    All strategies are optional per-symbol (pass None or omit the key to skip
    a strategy for that symbol, e.g. if it errored during detection) -- but
    every one of the 7 pattern tables is covered here now. Previously this
    function only batched 4 of the 7 strategies (streak/five_leg/pivot/
    s1_shift); breakout-pullback, EMA pullback, and Supertrend were added to
    the dashboard later and always went through their own legacy
    upsert_*_episodes() calls instead, each opening/closing its own
    connection per symbol -- silently defeating the whole point of this
    function for exactly the newest and highest-row-count strategies. That
    gap is closed here: all 7 upsert-and-purge operations for every symbol
    now run inside a single BEGIN...COMMIT block, eliminating what was
    ~7 x N_symbols individual connections/transactions down to 1.
    """
    conn = _connect()
    now = datetime.now().isoformat()
    try:
        # BEGIN IMMEDIATE acquires the write lock upfront rather than
        # escalating from a deferred transaction mid-flight. This prevents
        # SQLITE_BUSY errors when a concurrent process (e.g. a Streamlit
        # hot-reload) opens the DB during the batch write.
        conn.execute("BEGIN IMMEDIATE")
        for item in per_symbol_data:
            sym = item["symbol"]
            if item.get("streak_rows") is not None:
                _upsert_streaks_conn(conn, sym, item["streak_rows"], now)
            if item.get("five_leg_rows") is not None:
                _upsert_five_leg_conn(conn, sym, item["five_leg_rows"], now)
            if item.get("pivot_rows") is not None:
                _upsert_pivot_conn(conn, sym, item["pivot_rows"], now)
            if item.get("s1_shift_rows") is not None:
                _upsert_s1_shift_conn(conn, sym, item["s1_shift_rows"], now)
            if item.get("breakout_pullback_rows") is not None:
                _upsert_breakout_pullback_conn(conn, sym, item["breakout_pullback_rows"], now)
            if item.get("ema_pullback_rows") is not None:
                _upsert_ema_pullback_conn(conn, sym, item["ema_pullback_rows"], now)
            if item.get("supertrend_rows") is not None:
                _upsert_supertrend_conn(conn, sym, item["supertrend_rows"], now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal helpers — operate on an already-open connection (no commit/close).
# ---------------------------------------------------------------------------

def _upsert_streaks_conn(conn, symbol: str, rows: list, now: str):
    params = [
        (
            symbol, r["streak_start"], r["streak_end"], r["x_price"],
            r.get("days_to_x"), r.get("total_streak_days"), r.get("status"),
            r.get("tested_date"), r.get("tested_price"),
            r.get("max_correction_pct"), r.get("days_to_reclaim"),
            r.get("retest_drawdown_pct"), r.get("retest_days_to_recover"),
            symbol, r["streak_start"], r["streak_end"], now,
            now,
        )
        for r in rows
    ]
    conn.executemany(
        """
        INSERT INTO reference_levels
            (symbol, streak_start, streak_end, x_price, days_to_x,
             total_streak_days, status, tested_date, tested_price,
             max_correction_pct, days_to_reclaim, retest_drawdown_pct,
             retest_days_to_recover, first_seen_at, last_checked_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,
            COALESCE((SELECT first_seen_at FROM reference_levels
                       WHERE symbol=? AND streak_start=? AND streak_end=?), ?),
            ?)
        ON CONFLICT(symbol, streak_start, streak_end) DO UPDATE SET
            x_price=excluded.x_price,
            days_to_x=excluded.days_to_x,
            total_streak_days=excluded.total_streak_days,
            status=excluded.status,
            tested_date=excluded.tested_date,
            tested_price=excluded.tested_price,
            max_correction_pct=excluded.max_correction_pct,
            days_to_reclaim=excluded.days_to_reclaim,
            retest_drawdown_pct=excluded.retest_drawdown_pct,
            retest_days_to_recover=excluded.retest_days_to_recover,
            last_checked_at=excluded.last_checked_at
        """,
        params,
    )
    if rows:
        keep = [(r["streak_start"], r["streak_end"]) for r in rows]
        placeholders = ",".join("(?,?)" for _ in keep)
        flat_params = [x for pair in keep for x in pair]
        conn.execute(
            f"DELETE FROM reference_levels WHERE symbol=? AND (streak_start,streak_end) NOT IN ({placeholders})",
            [symbol, *flat_params],
        )
    else:
        conn.execute("DELETE FROM reference_levels WHERE symbol=?", (symbol,))


def _upsert_five_leg_conn(conn, symbol: str, rows: list, now: str):
    params = [
        (
            symbol, r["leg1_start"], r.get("qualified_date"), r.get("probe_date"),
            r.get("x_price"), r.get("y_price"), r.get("num_legs_observed"), r.get("status"),
            r.get("x_retest_status"), r.get("x_tested_date"), r.get("x_tested_price"),
            r.get("y_retest_status"), r.get("y_tested_date"), r.get("y_tested_price"),
            r.get("retest_drawdown_pct"), r.get("retest_days_to_recover"), r.get("retest_drawdown_level"),
            symbol, r["leg1_start"], now,
            now,
        )
        for r in rows
    ]
    conn.executemany(
        """
        INSERT INTO five_leg_episodes
            (symbol, leg1_start, qualified_date, probe_date, x_price, y_price,
             num_legs_observed, status, x_retest_status, x_tested_date, x_tested_price,
             y_retest_status, y_tested_date, y_tested_price,
             retest_drawdown_pct, retest_days_to_recover, retest_drawdown_level,
             first_seen_at, last_checked_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            COALESCE((SELECT first_seen_at FROM five_leg_episodes
                       WHERE symbol=? AND leg1_start=?), ?),
            ?)
        ON CONFLICT(symbol, leg1_start) DO UPDATE SET
            qualified_date=excluded.qualified_date,
            probe_date=excluded.probe_date,
            x_price=excluded.x_price, y_price=excluded.y_price,
            num_legs_observed=excluded.num_legs_observed,
            status=excluded.status,
            x_retest_status=excluded.x_retest_status,
            x_tested_date=excluded.x_tested_date, x_tested_price=excluded.x_tested_price,
            y_retest_status=excluded.y_retest_status,
            y_tested_date=excluded.y_tested_date, y_tested_price=excluded.y_tested_price,
            retest_drawdown_pct=excluded.retest_drawdown_pct,
            retest_days_to_recover=excluded.retest_days_to_recover,
            retest_drawdown_level=excluded.retest_drawdown_level,
            last_checked_at=excluded.last_checked_at
        """,
        params,
    )
    if rows:
        keep_keys = [r["leg1_start"] for r in rows]
        placeholders = ",".join("?" * len(keep_keys))
        conn.execute(
            f"DELETE FROM five_leg_episodes WHERE symbol=? AND leg1_start NOT IN ({placeholders})",
            [symbol, *keep_keys],
        )
    else:
        conn.execute("DELETE FROM five_leg_episodes WHERE symbol=?", (symbol,))


def _upsert_pivot_conn(conn, symbol: str, rows: list, now: str):
    params = [
        (
            symbol, r["episode_start"], r.get("qualify_end_date"),
            r.get("x_price"), r.get("x_fix_date"), r.get("y_price"), r.get("y_fix_date"),
            r.get("status"), r.get("x_retest_status"), r.get("x_tested_date"), r.get("x_tested_price"),
            r.get("y_retest_status"), r.get("y_tested_date"), r.get("y_tested_price"),
            r.get("retest_drawdown_pct"), r.get("retest_days_to_recover"), r.get("retest_drawdown_level"),
            symbol, r["episode_start"], now,
            now,
        )
        for r in rows
    ]
    conn.executemany(
        """
        INSERT INTO monthly_pivot_episodes
            (symbol, episode_start, qualify_end_date, x_price, x_fix_date,
             y_price, y_fix_date, status, x_retest_status, x_tested_date,
             x_tested_price, y_retest_status, y_tested_date, y_tested_price,
             retest_drawdown_pct, retest_days_to_recover, retest_drawdown_level,
             first_seen_at, last_checked_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            COALESCE((SELECT first_seen_at FROM monthly_pivot_episodes
                       WHERE symbol=? AND episode_start=?), ?),
            ?)
        ON CONFLICT(symbol, episode_start) DO UPDATE SET
            qualify_end_date=excluded.qualify_end_date,
            x_price=excluded.x_price, x_fix_date=excluded.x_fix_date,
            y_price=excluded.y_price, y_fix_date=excluded.y_fix_date,
            status=excluded.status,
            x_retest_status=excluded.x_retest_status,
            x_tested_date=excluded.x_tested_date, x_tested_price=excluded.x_tested_price,
            y_retest_status=excluded.y_retest_status,
            y_tested_date=excluded.y_tested_date, y_tested_price=excluded.y_tested_price,
            retest_drawdown_pct=excluded.retest_drawdown_pct,
            retest_days_to_recover=excluded.retest_days_to_recover,
            retest_drawdown_level=excluded.retest_drawdown_level,
            last_checked_at=excluded.last_checked_at
        """,
        params,
    )
    if rows:
        keep_keys = [r["episode_start"] for r in rows]
        placeholders = ",".join("?" * len(keep_keys))
        conn.execute(
            f"DELETE FROM monthly_pivot_episodes WHERE symbol=? AND episode_start NOT IN ({placeholders})",
            [symbol, *keep_keys],
        )
    else:
        conn.execute("DELETE FROM monthly_pivot_episodes WHERE symbol=?", (symbol,))


def _upsert_s1_shift_conn(conn, symbol: str, rows: list, now: str):
    params = [
        (
            symbol, r["month"], r.get("x_price"), r.get("x_date"), r.get("anchor_date"),
            r.get("s1_month"), r.get("s1_next_month"), r.get("s1_shift_pct"),
            r.get("status"),
            r.get("tested_date"), r.get("tested_price"), r.get("failed_date"),
            r.get("max_runup_pct"), r.get("days_tracked"),
            r.get("post_event_drawdown_pct"), r.get("post_event_days_to_recover"),
            symbol, r["month"], now,
            now,
        )
        for r in rows
    ]
    conn.executemany(
        """
        INSERT INTO monthly_s1_shift_episodes
            (symbol, month, x_price, x_date, anchor_date, s1_month, s1_next_month,
             s1_shift_pct, status, tested_date, tested_price, failed_date, max_runup_pct,
             days_tracked, post_event_drawdown_pct, post_event_days_to_recover,
             first_seen_at, last_checked_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            COALESCE((SELECT first_seen_at FROM monthly_s1_shift_episodes
                       WHERE symbol=? AND month=?), ?),
            ?)
        ON CONFLICT(symbol, month) DO UPDATE SET
            x_price=excluded.x_price, x_date=excluded.x_date,
            anchor_date=excluded.anchor_date,
            s1_month=excluded.s1_month, s1_next_month=excluded.s1_next_month,
            s1_shift_pct=excluded.s1_shift_pct,
            status=excluded.status,
            tested_date=excluded.tested_date, tested_price=excluded.tested_price,
            failed_date=excluded.failed_date,
            max_runup_pct=excluded.max_runup_pct, days_tracked=excluded.days_tracked,
            post_event_drawdown_pct=excluded.post_event_drawdown_pct,
            post_event_days_to_recover=excluded.post_event_days_to_recover,
            last_checked_at=excluded.last_checked_at
        """,
        params,
    )
    if rows:
        keep_keys = [r["month"] for r in rows]
        placeholders = ",".join("?" * len(keep_keys))
        conn.execute(
            f"DELETE FROM monthly_s1_shift_episodes WHERE symbol=? AND month NOT IN ({placeholders})",
            [symbol, *keep_keys],
        )
    else:
        conn.execute("DELETE FROM monthly_s1_shift_episodes WHERE symbol=?", (symbol,))


def _upsert_ema_pullback_conn(conn, symbol: str, rows: list, now: str):
    params = [
        (
            symbol, r["crossover_date"],
            r.get("qualify_end_date"), r.get("touch_date"),
            r.get("qualify_duration_days"),
            r.get("x_price"), r.get("y_price"), r.get("y_fix_date"),
            r.get("status"), r.get("tested_date"), r.get("tested_price"),
            r.get("failed_date"), r.get("max_runup_pct"), r.get("days_tracked"),
            r.get("post_event_drawdown_pct"), r.get("post_event_days_to_recover"),
            symbol, r["crossover_date"], now,
            now,
        )
        for r in rows
    ]
    conn.executemany(
        """
        INSERT INTO ema_pullback_episodes
            (symbol, crossover_date, qualify_end_date, touch_date,
             qualify_duration_days,
             x_price, y_price, y_fix_date, status,
             tested_date, tested_price, failed_date,
             max_runup_pct, days_tracked,
             post_event_drawdown_pct, post_event_days_to_recover,
             first_seen_at, last_checked_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            COALESCE((SELECT first_seen_at FROM ema_pullback_episodes
                       WHERE symbol=? AND crossover_date=?), ?),
            ?)
        ON CONFLICT(symbol, crossover_date) DO UPDATE SET
            qualify_end_date=excluded.qualify_end_date,
            touch_date=excluded.touch_date,
            qualify_duration_days=excluded.qualify_duration_days,
            x_price=excluded.x_price, y_price=excluded.y_price,
            y_fix_date=excluded.y_fix_date,
            status=excluded.status,
            tested_date=excluded.tested_date, tested_price=excluded.tested_price,
            failed_date=excluded.failed_date,
            max_runup_pct=excluded.max_runup_pct, days_tracked=excluded.days_tracked,
            post_event_drawdown_pct=excluded.post_event_drawdown_pct,
            post_event_days_to_recover=excluded.post_event_days_to_recover,
            last_checked_at=excluded.last_checked_at
        """,
        params,
    )
    if rows:
        keep_keys = [r["crossover_date"] for r in rows]
        placeholders = ",".join("?" * len(keep_keys))
        conn.execute(
            f"DELETE FROM ema_pullback_episodes WHERE symbol=? AND crossover_date NOT IN ({placeholders})",
            [symbol, *keep_keys],
        )
    else:
        conn.execute("DELETE FROM ema_pullback_episodes WHERE symbol=?", (symbol,))



def _upsert_supertrend_conn(conn, symbol: str, rows: list, now: str):
    def _ts(v):
        if v is None or (hasattr(v, '__class__') and v.__class__.__name__ in ('NaTType',)):
            return None
        try:
            import pandas as pd
            ts = pd.Timestamp(v)
            return None if pd.isna(ts) else ts.strftime("%Y-%m-%d")
        except Exception:
            return str(v) if v else None

    params = [
        (
            symbol, r["phase1_start"], r["st_period"], r["st_multiplier"],
            _ts(r.get("phase1_end")), r.get("x_price"), _ts(r.get("x_date")),
            _ts(r.get("phase2_start")), r.get("y_price"), _ts(r.get("y_date")),
            r.get("z_price"), _ts(r.get("z_date")),
            _ts(r.get("phase3_start")), _ts(r.get("signal_date")), _ts(r.get("x_cleared_date")),
            r.get("status"),
            r.get("x_status"), _ts(r.get("x_tested_date")), r.get("x_tested_price"),
            _ts(r.get("x_failed_date")), r.get("x_max_runup_pct"), r.get("x_days_tracked"),
            r.get("x_drawdown_pct"), r.get("x_recovery_days"),
            r.get("y_status"), _ts(r.get("y_tested_date")), r.get("y_tested_price"),
            _ts(r.get("y_failed_date")), r.get("y_max_runup_pct"), r.get("y_days_tracked"),
            r.get("y_drawdown_pct"), r.get("y_recovery_days"),
            r.get("z_status"), _ts(r.get("z_tested_date")), r.get("z_tested_price"),
            _ts(r.get("z_failed_date")), r.get("z_max_runup_pct"), r.get("z_days_tracked"),
            r.get("z_drawdown_pct"), r.get("z_recovery_days"),
            # first_seen_at COALESCE params
            symbol, r["phase1_start"], r["st_period"], r["st_multiplier"], now,
            now,
        )
        for r in rows
    ]
    conn.executemany("""
        INSERT INTO supertrend_episodes
            (symbol, phase1_start, st_period, st_multiplier,
             phase1_end, x_price, x_date, phase2_start, y_price, y_date,
             z_price, z_date, phase3_start, signal_date, x_cleared_date, status,
             x_status, x_tested_date, x_tested_price, x_failed_date,
             x_max_runup_pct, x_days_tracked, x_drawdown_pct, x_recovery_days,
             y_status, y_tested_date, y_tested_price, y_failed_date,
             y_max_runup_pct, y_days_tracked, y_drawdown_pct, y_recovery_days,
             z_status, z_tested_date, z_tested_price, z_failed_date,
             z_max_runup_pct, z_days_tracked, z_drawdown_pct, z_recovery_days,
             first_seen_at, last_checked_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            COALESCE((SELECT first_seen_at FROM supertrend_episodes
                       WHERE symbol=? AND phase1_start=? AND st_period=? AND st_multiplier=?), ?),
            ?)
        ON CONFLICT(symbol, phase1_start, st_period, st_multiplier) DO UPDATE SET
            phase1_end=excluded.phase1_end, x_price=excluded.x_price, x_date=excluded.x_date,
            phase2_start=excluded.phase2_start, y_price=excluded.y_price, y_date=excluded.y_date,
            z_price=excluded.z_price, z_date=excluded.z_date,
            phase3_start=excluded.phase3_start, signal_date=excluded.signal_date,
            x_cleared_date=excluded.x_cleared_date, status=excluded.status,
            x_status=excluded.x_status, x_tested_date=excluded.x_tested_date,
            x_tested_price=excluded.x_tested_price, x_failed_date=excluded.x_failed_date,
            x_max_runup_pct=excluded.x_max_runup_pct, x_days_tracked=excluded.x_days_tracked,
            x_drawdown_pct=excluded.x_drawdown_pct, x_recovery_days=excluded.x_recovery_days,
            y_status=excluded.y_status, y_tested_date=excluded.y_tested_date,
            y_tested_price=excluded.y_tested_price, y_failed_date=excluded.y_failed_date,
            y_max_runup_pct=excluded.y_max_runup_pct, y_days_tracked=excluded.y_days_tracked,
            y_drawdown_pct=excluded.y_drawdown_pct, y_recovery_days=excluded.y_recovery_days,
            z_status=excluded.z_status, z_tested_date=excluded.z_tested_date,
            z_tested_price=excluded.z_tested_price, z_failed_date=excluded.z_failed_date,
            z_max_runup_pct=excluded.z_max_runup_pct, z_days_tracked=excluded.z_days_tracked,
            z_drawdown_pct=excluded.z_drawdown_pct, z_recovery_days=excluded.z_recovery_days,
            last_checked_at=excluded.last_checked_at
        """, params)

    if rows:
        keep = [(r["phase1_start"], r["st_period"], r["st_multiplier"]) for r in rows]
        placeholders = ",".join("(?,?,?)" for _ in keep)
        flat = [x for t in keep for x in t]
        conn.execute(
            f"DELETE FROM supertrend_episodes WHERE symbol=? AND (phase1_start, st_period, st_multiplier) NOT IN ({placeholders})",
            [symbol, *flat],
        )
    else:
        conn.execute("DELETE FROM supertrend_episodes WHERE symbol=?", (symbol,))

# ---------------------------------------------------------------------------
# Legacy per-symbol API — kept for backward compatibility (e.g. standalone
# diagnostic scripts). Each creates/closes its own connection.
# ---------------------------------------------------------------------------

def upsert_five_leg_episodes(symbol: str, episodes: list):
    conn = _connect()
    now = datetime.now().isoformat()
    try:
        _upsert_five_leg_conn(conn, symbol, episodes, now)
        conn.commit()
    finally:
        conn.close()


def upsert_levels(symbol: str, rows: list):
    conn = _connect()
    now = datetime.now().isoformat()
    try:
        _upsert_streaks_conn(conn, symbol, rows, now)
        conn.commit()
    finally:
        conn.close()


def upsert_pivot_episodes(symbol: str, episodes: list):
    conn = _connect()
    now = datetime.now().isoformat()
    try:
        _upsert_pivot_conn(conn, symbol, episodes, now)
        conn.commit()
    finally:
        conn.close()


def upsert_s1_shift_episodes(symbol: str, episodes: list):
    conn = _connect()
    now = datetime.now().isoformat()
    try:
        _upsert_s1_shift_conn(conn, symbol, episodes, now)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# READ helpers
# ---------------------------------------------------------------------------

def _decode_value(v):
    """
    Decode a single sqlite3 cell to a Python-native type.

    With detect_types=sqlite3.PARSE_DECLTYPES on the connection, sqlite3
    already returns int/float/str/None for properly-typed columns, so this
    function is a belt-and-suspenders safety net for any edge cases.

    For bytes values (which should no longer appear with PARSE_DECLTYPES but
    may still occur for untyped expressions or legacy rows):
      - Try UTF-8 decode (covers proper text with high bytes like ©, æ).
      - If that fails, try latin-1 (lossless for all 0x00-0xFF values).
      - Do NOT try to interpret bytes as binary integers — that is ambiguous
        and unreliable; PARSE_DECLTYPES handles integer typing correctly.
    """
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return v.decode("latin-1")
    return v


def _read_sql(conn, q: str, params: tuple = ()) -> pd.DataFrame:
    """
    Read SQL into a DataFrame via the raw sqlite3 cursor, bypassing
    pd.read_sql_query entirely to avoid the UnicodeDecodeError that
    its internal ensure_string_array raises on bytes values.
    """
    cur = conn.execute(q, params) if params else conn.execute(q)
    columns = [desc[0] for desc in cur.description]
    rows = [tuple(_decode_value(cell) for cell in row) for row in cur.fetchall()]
    return pd.DataFrame(rows, columns=columns)


def get_all_levels(status: str = None) -> pd.DataFrame:
    conn = _connect()
    q = "SELECT * FROM reference_levels"
    params: tuple = ()
    if status:
        q += " WHERE status = ?"
        params = (status,)
    try:
        return _read_sql(conn, q, params)
    finally:
        conn.close()


def get_five_leg_episodes(status: str = None) -> pd.DataFrame:
    conn = _connect()
    q = "SELECT * FROM five_leg_episodes"
    params: tuple = ()
    if status:
        q += " WHERE status = ?"
        params = (status,)
    try:
        return _read_sql(conn, q, params)
    finally:
        conn.close()


def get_pivot_episodes(status: str = None) -> pd.DataFrame:
    conn = _connect()
    q = "SELECT * FROM monthly_pivot_episodes"
    params: tuple = ()
    if status:
        q += " WHERE status = ?"
        params = (status,)
    try:
        return _read_sql(conn, q, params)
    finally:
        conn.close()


def get_s1_shift_episodes(status: str = None) -> pd.DataFrame:
    conn = _connect()
    q = "SELECT * FROM monthly_s1_shift_episodes"
    params: tuple = ()
    if status:
        q += " WHERE status = ?"
        params = (status,)
    try:
        return _read_sql(conn, q, params)
    finally:
        conn.close()


def _upsert_breakout_pullback_conn(conn, symbol: str, episodes: list, now: str):
    """
    Connection-scoped helper (no commit/close) -- callable both from
    batch_upsert_all() (one shared connection/transaction for the whole
    universe) and from the legacy per-symbol upsert_breakout_pullback_episodes()
    wrapper below. Uses executemany() rather than a per-row execute() loop,
    matching every other strategy's connection-scoped helper.
    """
    params = [
        (
            symbol, ep["leg1_start"], ep.get("leg2_start"), ep.get("leg3_start"),
            ep.get("leg4_start"), ep.get("signal_date"), ep.get("x_price"),
            ep.get("x_vs_200ema_pct"),
            ep.get("y_price"), ep.get("z_price"), ep.get("leg1_low_price"),
            ep.get("status"), ep.get("y_retest_status"), ep.get("z_retest_status"),
            ep.get("y_tested_date"), ep.get("y_tested_price"),
            ep.get("z_tested_date"), ep.get("z_tested_price"),
            ep.get("failed_date"), ep.get("max_runup_pct"), ep.get("days_tracked"),
            ep.get("post_event_drawdown_pct"), ep.get("post_event_days_to_recover"),
            symbol, ep["leg1_start"], now,
            now,
        )
        for ep in episodes
    ]
    conn.executemany(
        """
        INSERT INTO breakout_pullback_episodes
            (symbol, leg1_start, leg2_start, leg3_start, leg4_start, signal_date,
             x_price, x_vs_200ema_pct, y_price, z_price, leg1_low_price, status,
             y_retest_status, z_retest_status, y_tested_date, y_tested_price,
             z_tested_date, z_tested_price, failed_date, max_runup_pct,
             days_tracked, post_event_drawdown_pct, post_event_days_to_recover,
             first_seen_at, last_checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            COALESCE((SELECT first_seen_at FROM breakout_pullback_episodes
                       WHERE symbol=? AND leg1_start=?), ?),
            ?)
        ON CONFLICT(symbol, leg1_start) DO UPDATE SET
            leg2_start=excluded.leg2_start,
            leg3_start=excluded.leg3_start,
            leg4_start=excluded.leg4_start,
            signal_date=excluded.signal_date,
            x_price=excluded.x_price,
            x_vs_200ema_pct=excluded.x_vs_200ema_pct,
            y_price=excluded.y_price,
            z_price=excluded.z_price,
            leg1_low_price=excluded.leg1_low_price,
            status=excluded.status,
            y_retest_status=excluded.y_retest_status,
            z_retest_status=excluded.z_retest_status,
            y_tested_date=excluded.y_tested_date,
            y_tested_price=excluded.y_tested_price,
            z_tested_date=excluded.z_tested_date,
            z_tested_price=excluded.z_tested_price,
            failed_date=excluded.failed_date,
            max_runup_pct=excluded.max_runup_pct,
            days_tracked=excluded.days_tracked,
            post_event_drawdown_pct=excluded.post_event_drawdown_pct,
            post_event_days_to_recover=excluded.post_event_days_to_recover,
            last_checked_at=excluded.last_checked_at
        """,
        params,
    )

    if episodes:
        current_keys = [ep["leg1_start"] for ep in episodes]
        placeholders = ",".join("?" * len(current_keys))
        conn.execute(
            f"DELETE FROM breakout_pullback_episodes WHERE symbol = ? AND leg1_start NOT IN ({placeholders})",
            (symbol, *current_keys),
        )
    else:
        conn.execute("DELETE FROM breakout_pullback_episodes WHERE symbol = ?", (symbol,))


def upsert_breakout_pullback_episodes(symbol: str, episodes: list):
    """
    Legacy per-symbol API (own connection, own commit) -- kept for backward
    compatibility (e.g. standalone diagnostic scripts). Prefer routing
    through batch_upsert_all() for the full-universe refresh path.
    """
    conn = _connect()
    now = datetime.now().isoformat()
    try:
        _upsert_breakout_pullback_conn(conn, symbol, episodes, now)
        conn.commit()
    finally:
        conn.close()


def get_breakout_pullback_episodes(status: str = None) -> pd.DataFrame:
    conn = _connect()
    q = "SELECT * FROM breakout_pullback_episodes"
    params: tuple = ()
    if status:
        q += " WHERE status = ?"
        params = (status,)
    try:
        df = _read_sql(conn, q, params)
    finally:
        conn.close()
    return df


def upsert_ema_pullback_episodes(symbol: str, episodes: list):
    """
    Syncs the ledger for `symbol` with the EMA Pullback Reentry episodes the
    current run detected: upserts each one (preserving first_seen_at), and
    DELETES any previously-stored episode for this symbol that the current
    run no longer produces.
    """
    conn = _connect()
    now = datetime.now().isoformat()
    try:
        _upsert_ema_pullback_conn(conn, symbol, episodes, now)
        conn.commit()
    finally:
        conn.close()


def get_ema_pullback_episodes(status: str = None) -> pd.DataFrame:
    conn = _connect()
    q = "SELECT * FROM ema_pullback_episodes"
    params: tuple = ()
    if status:
        q += " WHERE status = ?"
        params = (status,)
    try:
        return _read_sql(conn, q, params)
    finally:
        conn.close()


def upsert_supertrend_episodes(symbol: str, episodes: list):
    """Syncs supertrend 3-phase episodes for symbol."""
    conn = _connect()
    now = datetime.now().isoformat()
    try:
        _upsert_supertrend_conn(conn, symbol, episodes, now)
        conn.commit()
    finally:
        conn.close()


def get_supertrend_episodes(status: str = None, st_period: int = None, st_multiplier: float = None) -> pd.DataFrame:
    conn = _connect()
    clauses = []
    params = []
    if status:
        clauses.append("status = ?"); params.append(status)
    if st_period is not None:
        clauses.append("st_period = ?"); params.append(int(st_period))
    if st_multiplier is not None:
        clauses.append("st_multiplier = ?"); params.append(float(st_multiplier))
    q = "SELECT * FROM supertrend_episodes"
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    try:
        return _read_sql(conn, q, tuple(params))
    finally:
        conn.close()


if __name__ == "__main__":
    print(get_all_levels().head(20))