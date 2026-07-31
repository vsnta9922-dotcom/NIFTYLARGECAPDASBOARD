"""
fundamental_score.py
----------------------
Fetches, caches (weekly), and scores fundamental data for Nifty large-cap
stocks from Yahoo Finance. Designed to complement the technical confluence
score — kept as a SEPARATE score (not blended in) so stale/missing data
never silently corrupts the technical ranking.

═══════════════════════════════════════════════════════════════════════════
DATA SOURCED FROM yf.Ticker(sym).info
═══════════════════════════════════════════════════════════════════════════

Fields pulled (all that are reasonably populated for NSE stocks):

  trailingPE          — trailing 12-month Price/Earnings ratio
  trailingEps         — trailing EPS (used to cross-check PE)
  returnOnEquity      — ROE as a decimal (0.25 = 25%)
  returnOnAssets      — ROA as a decimal
  debtToEquity        — total debt / equity (lower = safer)
  revenueGrowth       — YoY revenue growth (decimal)
  earningsGrowth      — YoY earnings growth (decimal)
  dividendYield       — annual dividend / price (decimal)
  grossMargins        — gross profit margin (decimal)
  operatingMargins    — operating margin (decimal)
  currentRatio        — current assets / current liabilities
  marketCap           — absolute market cap in INR (for sanity checks)
  bookValue           — book value per share (for P/B calculation)
  priceToBook         — P/B ratio

Fields intentionally NOT used:
  forwardPE           — too unreliable for Indian stocks in yf
  beta                — not relevant for long-term structural investing
  shortRatio          — not available for NSE

═══════════════════════════════════════════════════════════════════════════
SCORING FRAMEWORK  (max 100 raw points → normalized to 0–10)
═══════════════════════════════════════════════════════════════════════════

F1. Return on Equity (ROE)                          max 25 pts
    ROE measures how efficiently management compounds shareholders' money.
    The single most important metric for long-term compounding.
    • ROE >= 25%                                    25 pts  (exceptional)
    • ROE 20–24%                                    20 pts  (very good)
    • ROE 15–19%                                    14 pts  (good)
    • ROE 10–14%                                    8  pts  (adequate)
    • ROE 5–9%                                      3  pts  (weak)
    • ROE < 5% or negative                          0  pts  (avoid)

F2. P/E Ratio (valuation)                           max 20 pts
    Penalizes overvaluation — important for entry price discipline.
    P/E is compared relative to the stock's own sector average where
    possible; here we use absolute thresholds since sector data is
    unavailable without a premium data source.
    • P/E <= 15                                     20 pts  (deep value)
    • P/E 15–25                                     16 pts  (fair value)
    • P/E 25–40                                     10 pts  (growth premium)
    • P/E 40–60                                     5  pts  (expensive)
    • P/E 60–100                                    2  pts  (very expensive)
    • P/E > 100 or negative                         0  pts  (speculative/loss)
    • P/E missing                                   8  pts  (neutral — no penalty
                                                             for missing data)

F3. Earnings Growth (momentum)                      max 15 pts
    YoY earnings growth — a growing business justifies a higher P/E.
    • Growth >= 25%                                 15 pts
    • Growth 15–24%                                 12 pts
    • Growth 8–14%                                  8  pts
    • Growth 0–7%                                   4  pts
    • Growth negative                               0  pts
    • Missing                                       6  pts  (neutral)

F4. Debt/Equity (financial safety)                  max 15 pts
    Low debt = more resilient during bear markets, less risk of dilution.
    For financial companies (banks/NBFCs) D/E is naturally high — we
    detect these by checking if D/E > 3 AND marketCap suggests banking.
    (We can't reliably detect sector from yf.info, so we apply a lenient
    threshold for very high D/E stocks.)
    • D/E <= 0.3                                    15 pts  (net cash / minimal debt)
    • D/E 0.3–0.7                                   12 pts  (conservative)
    • D/E 0.7–1.5                                   8  pts  (moderate)
    • D/E 1.5–3.0                                   4  pts  (elevated)
    • D/E 3.0–6.0                                   2  pts  (high — likely financial co.)
    • D/E > 6.0                                     0  pts  (highly leveraged)
    • Missing                                        7  pts  (neutral)

F5. Dividend Yield PENALTY (30% tax bracket)        max 0, min -10 pts
    Dividends are taxed at 30% slab (not 12.5% LTCG). High-dividend
    stocks therefore destroy post-tax returns for your bracket vs.
    equivalent capital appreciation. This is a PURE PENALTY — no bonus
    for low dividends (that's neutral, not good).
    • Yield > 4%                                   -10 pts  (severe trap)
    • Yield 3–4%                                   -6  pts  (significant drag)
    • Yield 2–3%                                   -3  pts  (modest drag)
    • Yield 1–2%                                   -1  pts  (minor)
    • Yield < 1% or zero                            0  pts  (no penalty)

F6. Operating Margin (business quality)             max 10 pts
    High operating margins signal pricing power and business quality.
    • Margin >= 25%                                 10 pts
    • Margin 18–24%                                 8  pts
    • Margin 12–17%                                 6  pts
    • Margin 6–11%                                  3  pts
    • Margin < 6% or negative                       0  pts
    • Missing                                        4  pts  (neutral)

F7. Revenue Growth (business momentum)              max 10 pts
    Consistent revenue growth supports long-term price appreciation.
    • Growth >= 20%                                 10 pts
    • Growth 12–19%                                 8  pts
    • Growth 6–11%                                  5  pts
    • Growth 0–5%                                   2  pts
    • Growth negative                               0  pts
    • Missing                                        4  pts  (neutral)

F8. Current Ratio (short-term safety)               max 5 pts
    Ability to meet near-term obligations. Less important for financial
    cos. but good signal for manufacturing/consumer companies.
    • Ratio >= 2.0                                  5  pts
    • Ratio 1.5–1.9                                 4  pts
    • Ratio 1.0–1.4                                 2  pts
    • Ratio < 1.0                                   0  pts  (potential liquidity risk)
    • Missing                                        2  pts  (neutral)

═══════════════════════════════════════════════════════════════════════════
QUALITY TIER (derived label, not a score component)
═══════════════════════════════════════════════════════════════════════════

Based on the normalized 0-10 fundamental score:
  >= 8.0  → 🏆 Quality Compounder   (buy and hold forever)
  >= 6.5  → ✅ Solid Business        (good long-term hold)
  >= 5.0  → ⚠️  Average Business     (needs strong technical signal)
  >= 3.5  → 🔶 Below Average         (only on deep technical setups)
  < 3.5   → ❌ Weak Fundamentals      (avoid regardless of technicals)

═══════════════════════════════════════════════════════════════════════════
CACHING STRATEGY
═══════════════════════════════════════════════════════════════════════════

Yahoo Finance fundamental data changes at most quarterly (earnings releases).
Fetching it on every dashboard run wastes API quota and adds 30+ seconds.
We cache in a dedicated SQLite table (`fundamental_cache`) with a
`fetched_at` timestamp per symbol. Refresh logic:
  - If data is < FUNDAMENTAL_REFRESH_DAYS (7) old → use cache
  - If older or missing → re-fetch from yf and update cache
  - If fetch fails → use whatever is in cache, even if stale
  - If nothing in cache → return neutral scores (no penalty, no bonus)

Fetching is parallelized across up to MAX_WORKERS=8 threads to keep the
full-universe refresh under 60 seconds even on slow connections.
"""

import concurrent.futures
import logging
import sqlite3
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

_log = logging.getLogger("fundamental_score")

# ───────────────────────────────────────────────────────────────────────────
# CONFIG
# ───────────────────────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(__file__), "levels_ledger.db")
FUNDAMENTAL_REFRESH_DAYS = 7   # re-fetch weekly
MAX_WORKERS = 8                # parallel yf.Ticker calls
FETCH_TIMEOUT_SECONDS = 15     # per-symbol timeout

# Fields to pull from yf.Ticker.info
YF_FIELDS = [
    "trailingPE", "trailingEps", "returnOnEquity", "returnOnAssets",
    "debtToEquity", "revenueGrowth", "earningsGrowth", "dividendYield",
    "grossMargins", "operatingMargins", "currentRatio",
    "marketCap", "bookValue", "priceToBook",
]


# ───────────────────────────────────────────────────────────────────────────
# DB SCHEMA
# ───────────────────────────────────────────────────────────────────────────

def _ensure_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fundamental_cache (
            symbol              TEXT PRIMARY KEY,
            fetched_at          TEXT,
            trailing_pe         REAL,
            trailing_eps        REAL,
            roe                 REAL,
            roa                 REAL,
            debt_to_equity      REAL,
            revenue_growth      REAL,
            earnings_growth     REAL,
            dividend_yield      REAL,
            gross_margin        REAL,
            operating_margin    REAL,
            current_ratio       REAL,
            market_cap          REAL,
            book_value          REAL,
            price_to_book       REAL,
            fetch_error         TEXT
        )
    """)
    conn.commit()


def _connect_fund() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _ensure_table(conn)
    return conn


# ───────────────────────────────────────────────────────────────────────────
# FETCHER
# ───────────────────────────────────────────────────────────────────────────

def _fetch_one(symbol: str) -> dict:
    """
    Fetch fundamental data for one symbol from Yahoo Finance.
    Returns a dict with all YF_FIELDS (None for missing) plus fetch_error.
    Wrapped in a thread so it can be parallelized and timed out.
    """
    yf_sym = f"{symbol}.NS"
    row = {"symbol": symbol, "fetched_at": datetime.now().isoformat(), "fetch_error": None}
    field_map = {
        "trailingPE":       "trailing_pe",
        "trailingEps":      "trailing_eps",
        "returnOnEquity":   "roe",
        "returnOnAssets":   "roa",
        "debtToEquity":     "debt_to_equity",
        "revenueGrowth":    "revenue_growth",
        "earningsGrowth":   "earnings_growth",
        "dividendYield":    "dividend_yield",
        "grossMargins":     "gross_margin",
        "operatingMargins": "operating_margin",
        "currentRatio":     "current_ratio",
        "marketCap":        "market_cap",
        "bookValue":        "book_value",
        "priceToBook":      "price_to_book",
    }
    for db_col in field_map.values():
        row[db_col] = None
    try:
        info = yf.Ticker(yf_sym).info
        for yf_field, db_col in field_map.items():
            val = info.get(yf_field)
            if val is not None:
                try:
                    row[db_col] = float(val)
                except (TypeError, ValueError):
                    row[db_col] = None
        # ── Dividend yield sanity check ─────────────────────────────────────
        # Yahoo Finance sometimes returns dividendYield as a raw percentage
        # (e.g. 3.84 for 3.84%) rather than a decimal (0.0384).  Impossible
        # for a genuine yield to exceed 1.0 as a decimal, so divide by 100.
        dy_raw = row.get("dividend_yield")
        if dy_raw is not None and not pd.isna(dy_raw) and dy_raw > 1.0:
            row["dividend_yield"] = dy_raw / 100.0
    except Exception as e:
        row["fetch_error"] = str(e)[:200]
    return row


def _fetch_one_safe(symbol: str) -> dict:
    """
    Thin error-safe wrapper around _fetch_one. Called from the outer
    ThreadPoolExecutor in refresh_fundamentals — no nested executor needed
    here because the outer pool already provides parallelism. Per-batch
    timeouts are enforced at the wait() call in refresh_fundamentals.
    Returns a neutral placeholder dict on any exception.
    """
    try:
        return _fetch_one(symbol)
    except Exception as e:
        return {
            "symbol": symbol,
            "fetched_at": datetime.now().isoformat(),
            "fetch_error": str(e)[:200],
            **{col: None for col in [
                "trailing_pe", "trailing_eps", "roe", "roa", "debt_to_equity",
                "revenue_growth", "earnings_growth", "dividend_yield",
                "gross_margin", "operating_margin", "current_ratio",
                "market_cap", "book_value", "price_to_book",
            ]}
        }


def _save_fundamental_rows(conn: sqlite3.Connection, rows: list):
    for row in rows:
        conn.execute("""
            INSERT INTO fundamental_cache
                (symbol, fetched_at, trailing_pe, trailing_eps, roe, roa,
                 debt_to_equity, revenue_growth, earnings_growth, dividend_yield,
                 gross_margin, operating_margin, current_ratio,
                 market_cap, book_value, price_to_book, fetch_error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
                fetched_at=excluded.fetched_at,
                trailing_pe=excluded.trailing_pe,
                trailing_eps=excluded.trailing_eps,
                roe=excluded.roe,
                roa=excluded.roa,
                debt_to_equity=excluded.debt_to_equity,
                revenue_growth=excluded.revenue_growth,
                earnings_growth=excluded.earnings_growth,
                dividend_yield=excluded.dividend_yield,
                gross_margin=excluded.gross_margin,
                operating_margin=excluded.operating_margin,
                current_ratio=excluded.current_ratio,
                market_cap=excluded.market_cap,
                book_value=excluded.book_value,
                price_to_book=excluded.price_to_book,
                fetch_error=excluded.fetch_error
        """, (
            row["symbol"], row["fetched_at"],
            row.get("trailing_pe"), row.get("trailing_eps"),
            row.get("roe"), row.get("roa"),
            row.get("debt_to_equity"), row.get("revenue_growth"),
            row.get("earnings_growth"), row.get("dividend_yield"),
            row.get("gross_margin"), row.get("operating_margin"),
            row.get("current_ratio"), row.get("market_cap"),
            row.get("book_value"), row.get("price_to_book"),
            row.get("fetch_error"),
        ))
    conn.commit()


# ───────────────────────────────────────────────────────────────────────────
# PUBLIC: refresh fundamentals for a list of symbols
# ───────────────────────────────────────────────────────────────────────────

def refresh_fundamentals(
    symbols: list,
    force: bool = False,
    progress_callback=None,
) -> pd.DataFrame:
    """
    Fetches/refreshes fundamental data for all symbols that are stale or
    missing, using parallelized yf.Ticker calls with per-symbol timeouts.

    Parameters
    ----------
    symbols          : list of NSE symbols (without .NS suffix)
    force            : if True, re-fetch ALL symbols regardless of cache age
    progress_callback: optional callable(done, total) for UI progress bars

    Returns
    -------
    DataFrame with one row per symbol, all fundamental fields + scores.
    """
    conn = _connect_fund()
    cutoff = (datetime.now() - timedelta(days=FUNDAMENTAL_REFRESH_DAYS)).isoformat()

    # Load existing cache
    cur = conn.execute("SELECT symbol, fetched_at FROM fundamental_cache")
    cached_dates = {row[0]: row[1] for row in cur.fetchall()}

    # Decide which need refresh
    to_fetch = []
    for sym in symbols:
        if force:
            to_fetch.append(sym)
        elif sym not in cached_dates:
            to_fetch.append(sym)
        elif cached_dates[sym] < cutoff:
            to_fetch.append(sym)

    if to_fetch:
        _log.info("[fundamental_score] Fetching %d symbols (%d already cached)...",
                  len(to_fetch), len(symbols) - len(to_fetch))
        done = 0
        # Per-batch timeout: if a batch takes longer than FETCH_TIMEOUT_SECONDS
        # per symbol in the batch, cancel remaining futures and move on. This
        # replaces the previous pattern of creating one ThreadPoolExecutor per
        # symbol (_fetch_one_with_timeout) which spawned N nested pools
        # simultaneously — one shared pool with a batch-level wait() is correct.
        batch_size = MAX_WORKERS
        for batch_start in range(0, len(to_fetch), batch_size):
            batch = to_fetch[batch_start: batch_start + batch_size]
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                future_to_sym = {ex.submit(_fetch_one_safe, sym): sym for sym in batch}
                batch_timeout = FETCH_TIMEOUT_SECONDS * len(batch)
                done_futs, not_done = concurrent.futures.wait(
                    future_to_sym, timeout=batch_timeout
                )
                batch_rows = []
                for fut in done_futs:
                    batch_rows.append(fut.result())
                    done += 1
                    if progress_callback:
                        progress_callback(done, len(to_fetch))
                # Timed-out futures: store neutral placeholder so the symbol
                # isn't silently skipped — it gets a cache entry with fetch_error.
                for fut in not_done:
                    sym = future_to_sym[fut]
                    _log.warning("[fundamental_score] Timeout for %s — storing placeholder", sym)
                    batch_rows.append({
                        "symbol": sym,
                        "fetched_at": datetime.now().isoformat(),
                        "fetch_error": f"Timeout after {FETCH_TIMEOUT_SECONDS}s",
                        **{col: None for col in [
                            "trailing_pe", "trailing_eps", "roe", "roa",
                            "debt_to_equity", "revenue_growth", "earnings_growth",
                            "dividend_yield", "gross_margin", "operating_margin",
                            "current_ratio", "market_cap", "book_value", "price_to_book",
                        ]}
                    })
                    done += 1
                    if progress_callback:
                        progress_callback(done, len(to_fetch))
                    fut.cancel()
            _save_fundamental_rows(conn, batch_rows)

    # Load full cache for all requested symbols
    placeholders = ",".join("?" * len(symbols))
    cur = conn.execute(
        f"SELECT * FROM fundamental_cache WHERE symbol IN ({placeholders})",
        symbols,
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.close()

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("symbol")


def get_cached_fundamentals(symbols: list) -> pd.DataFrame:
    """
    Read-only: return whatever is in the fundamental_cache for these symbols.
    Does NOT trigger any network fetch. Used by the scoring functions when
    the caller wants to avoid any I/O delay.
    """
    conn = _connect_fund()
    try:
        placeholders = ",".join("?" * len(symbols))
        cur = conn.execute(
            f"SELECT * FROM fundamental_cache WHERE symbol IN ({placeholders})",
            symbols,
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        rows = []
    finally:
        conn.close()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("symbol")


# ───────────────────────────────────────────────────────────────────────────
# SCORING FUNCTIONS
# ───────────────────────────────────────────────────────────────────────────

def _score_roe(roe) -> int:
    """F1 — Return on Equity (max 25)."""
    if roe is None or pd.isna(roe):
        return 8   # neutral — missing ROE is common for holding cos.
    pct = roe * 100  # yf returns as decimal
    if pct >= 25:  return 25
    if pct >= 20:  return 20
    if pct >= 15:  return 14
    if pct >= 10:  return 8
    if pct >= 5:   return 3
    return 0


def _score_pe(pe) -> int:
    """F2 — P/E ratio (max 20)."""
    if pe is None or pd.isna(pe):
        return 8   # neutral
    if pe <= 0:    return 0   # loss-making
    if pe <= 15:   return 20
    if pe <= 25:   return 16
    if pe <= 40:   return 10
    if pe <= 60:   return 5
    if pe <= 100:  return 2
    return 0


def _score_earnings_growth(eg) -> int:
    """F3 — Earnings growth YoY (max 15)."""
    if eg is None or pd.isna(eg):
        return 6   # neutral
    pct = eg * 100
    if pct >= 25:  return 15
    if pct >= 15:  return 12
    if pct >= 8:   return 8
    if pct >= 0:   return 4
    return 0


# Nifty 100 financial company symbols — banks, NBFCs, insurance, financial services.
# For these, D/E is structurally high (they borrow to lend / invest) and is NOT
# an indicator of financial distress. We replace D/E scoring with ROA-based
# scoring for this group, which is the correct solvency metric for financials.
_FINANCIAL_SYMBOLS = frozenset({
    "HDFCBANK", "ICICIBANK", "AXISBANK", "KOTAKBANK", "SBIN", "PNB",
    "BANKBARODA", "CANBK", "UNIONBANK", "BAJFINANCE", "BAJAJFINSV",
    "HDFCLIFE", "SBILIFE", "HDFCAMC", "SHRIRAMFIN", "CHOLAFIN",
    "MUTHOOTFIN", "PFC", "RECLTD", "IRFC", "JIOFIN", "TATACAP",
    "BAJAJHLDNG",
})

# Capital-intensive regulated infrastructure utilities — power transmission,
# generation, and gas infrastructure.  These carry structural project debt
# (long-duration assets repaid from regulated tariff/capacity revenues) that
# looks high on a D/E ratio but is NOT a sign of financial distress.
# Treatment: neutral 7 pts for D/E — no harsh penalty, no reward for low debt.
_INFRA_UTILITY_SYMBOLS = frozenset({
    "POWERGRID", "NTPC", "ADANIPOWER", "ADANIGREEN", "ADANIENSOL",
    "TATAPOWER", "GAIL", "BPCL", "IOC", "ONGC", "COALINDIA",
})



def _score_de(de, symbol: str = "", roa=None) -> int:
    """
    F4 — Debt/Equity ratio (max 15), sector-aware.

    For financial companies (banks, NBFCs, insurance): D/E is structurally
    very high (they use leverage by design) and tells us nothing about
    distress. We swap in ROA-based scoring instead, which IS the right
    safety metric for financial intermediaries.

    Three tracks:
      Financial companies: D/E meaningless; score ROA instead.
      Infrastructure utilities: high D/E is structural (project debt);
        score as neutral 7 pts regardless of actual ratio.
      All others: standard D/E thresholds.
    """
    if symbol in _FINANCIAL_SYMBOLS:
        # For financials: score ROA instead — measures asset efficiency
        # without the distortion of the naturally high leverage ratio.
        if roa is None or pd.isna(roa):
            return 8  # neutral — missing ROA for financial
        pct = roa * 100
        if pct >= 2.0:  return 15   # very efficient (e.g. best private banks)
        if pct >= 1.5:  return 12
        if pct >= 1.0:  return 9
        if pct >= 0.5:  return 5
        if pct >= 0:    return 2
        return 0                      # negative ROA = loss
    # Infrastructure utilities: neutral score — high D/E is structural
    if symbol in _INFRA_UTILITY_SYMBOLS:
        return 7

    # Non-financial, non-utility: standard D/E scoring
    if de is None or pd.isna(de):
        return 7   # neutral
    if de <= 0.3:  return 15
    if de <= 0.7:  return 12
    if de <= 1.5:  return 8
    if de <= 3.0:  return 4
    if de <= 6.0:  return 2
    return 0


def _sanitise_yield(dy):
    """
    Normalise dividendYield to a true decimal fraction (0.0384 = 3.84%).

    Yahoo Finance sometimes returns dividendYield as a raw percentage
    (e.g. 3.84 instead of 0.0384) for Indian stocks.  Any value > 1.0 is
    impossible as a genuine decimal yield (> 100% annual payout), so we
    detect and fix it here.  Applied at both scoring time and display time
    so cached values stored in the raw form are corrected on the next run.
    """
    if dy is None or pd.isna(dy) or dy <= 0:
        return dy
    return dy / 100.0 if dy > 1.0 else dy


def _penalty_dividend(dy) -> int:
    """F5 — Dividend yield PENALTY for 30% bracket (min -10, max 0)."""
    dy = _sanitise_yield(dy)
    if dy is None or pd.isna(dy) or dy <= 0:
        return 0
    pct = dy * 100   # now guaranteed to be a decimal (0.0384 → 3.84)
    if pct > 4:    return -10
    if pct > 3:    return -6
    if pct > 2:    return -3
    if pct > 1:    return -1
    return 0


def _score_op_margin(om) -> int:
    """F6 — Operating margin (max 10)."""
    if om is None or pd.isna(om):
        return 4   # neutral
    pct = om * 100
    if pct >= 25:  return 10
    if pct >= 18:  return 8
    if pct >= 12:  return 6
    if pct >= 6:   return 3
    return 0


def _score_rev_growth(rg) -> int:
    """F7 — Revenue growth YoY (max 10)."""
    if rg is None or pd.isna(rg):
        return 4   # neutral
    pct = rg * 100
    if pct >= 20:  return 10
    if pct >= 12:  return 8
    if pct >= 6:   return 5
    if pct >= 0:   return 2
    return 0


def _score_current_ratio(cr) -> int:
    """F8 — Current ratio (max 5)."""
    if cr is None or pd.isna(cr):
        return 2   # neutral
    if cr >= 2.0:  return 5
    if cr >= 1.5:  return 4
    if cr >= 1.0:  return 2
    return 0


def _quality_tier(score_10: float) -> str:
    """Map normalized 0–10 score to a quality label."""
    if pd.isna(score_10):
        return "❓ No data"
    if score_10 >= 8.0:  return "🏆 Quality Compounder"
    if score_10 >= 6.5:  return "✅ Solid Business"
    if score_10 >= 5.0:  return "⚠️  Average"
    if score_10 >= 3.5:  return "🔶 Below Average"
    return "❌ Weak"


def _data_completeness(fund_row: dict) -> int:
    """
    Count how many of the key fundamental fields are available.
    Used to flag rows where Yahoo data is very sparse — the score
    is less trustworthy when only 2 of 8 fields are populated.
    """
    key_fields = ["roe", "trailing_pe", "earnings_growth", "debt_to_equity",
                  "dividend_yield", "operating_margin", "revenue_growth", "current_ratio"]
    return sum(1 for f in key_fields
               if fund_row.get(f) is not None and not pd.isna(fund_row.get(f, np.nan)))


def score_one_symbol(symbol: str, fund_df: pd.DataFrame) -> dict:
    """
    Score a single symbol's fundamentals. fund_df is indexed by symbol.
    Returns a dict with all individual sub-scores + the raw total.
    """
    empty = {
        "F_roe_pts": None, "F_pe_pts": None, "F_eg_pts": None,
        "F_de_pts": None, "F_div_penalty": None, "F_om_pts": None,
        "F_rg_pts": None, "F_cr_pts": None,
        "F_raw_total": None,
        "F_roe": None, "F_pe": None, "F_eg": None, "F_de": None,
        "F_roa": None, "F_de_display": None,
        "F_div_yield": None, "F_op_margin": None, "F_rev_growth": None,
        "F_data_fields": 0, "F_fetched_at": None,
        "F_is_financial": symbol in _FINANCIAL_SYMBOLS,
    }
    if fund_df.empty or symbol not in fund_df.index:
        return empty

    row = fund_df.loc[symbol].to_dict()

    roe  = row.get("roe")
    pe   = row.get("trailing_pe")
    eg   = row.get("earnings_growth")
    de   = row.get("debt_to_equity")
    dy   = row.get("dividend_yield")
    om   = row.get("operating_margin")
    rg   = row.get("revenue_growth")
    cr   = row.get("current_ratio")

    roa  = row.get("roa")   # used by financial-company D/E bypass

    f1 = _score_roe(roe)
    f2 = _score_pe(pe)
    f3 = _score_earnings_growth(eg)
    f4 = _score_de(de, symbol=symbol, roa=roa)
    f5 = _penalty_dividend(dy)
    f6 = _score_op_margin(om)
    f7 = _score_rev_growth(rg)
    f8 = _score_current_ratio(cr)

    raw = f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8
    completeness = _data_completeness(row)

    # If fewer than 3 key fields are available, cap the score at 5/10
    # by marking the raw total as limited — normalization will handle it
    if completeness < 3:
        raw = min(raw, 50)  # ~neutral on 100-pt scale

    return {
        "F_roe_pts": f1, "F_pe_pts": f2, "F_eg_pts": f3,
        "F_de_pts": f4, "F_div_penalty": f5, "F_om_pts": f6,
        "F_rg_pts": f7, "F_cr_pts": f8,
        "F_raw_total": raw,
        "F_roe":         round(roe * 100, 1)  if roe is not None and not pd.isna(roe)  else None,
        "F_pe":          round(pe, 1)          if pe  is not None and not pd.isna(pe)   else None,
        "F_eg":          round(eg * 100, 1)    if eg  is not None and not pd.isna(eg)   else None,
        "F_de":          round(de, 2)          if de  is not None and not pd.isna(de)   else None,
        "F_roa":         round(roa * 100, 2)   if roa is not None and not pd.isna(roa)  else None,
        "F_div_yield":   round(_sanitise_yield(dy) * 100, 2) if dy is not None and not pd.isna(dy) else None,
        "F_op_margin":   round(om * 100, 1)    if om  is not None and not pd.isna(om)   else None,
        "F_rev_growth":  round(rg * 100, 1)    if rg  is not None and not pd.isna(rg)   else None,
        "F_data_fields": completeness,
        "F_fetched_at":  row.get("fetched_at"),
        "F_is_financial": symbol in _FINANCIAL_SYMBOLS,
        "F_is_infra_utility": symbol in _INFRA_UTILITY_SYMBOLS,
        # F_de_display stores the RAW value only — the app adds labels/icons.
        #   Financial: ROA as a plain float (e.g. 2.78 means 2.78%), or None if unavailable.
        #   Infrastructure utility: sentinel "infra" string.
        #   Others: D/E ratio float, or None if missing.
        "F_de_display": (
            round(roa * 100, 2)
            if symbol in _FINANCIAL_SYMBOLS and roa is not None and not pd.isna(roa)
            else (
                None                              # financial but roa unavailable → "—"
                if symbol in _FINANCIAL_SYMBOLS
                else (
                    "infra"                       # utility → labelled separately
                    if symbol in _INFRA_UTILITY_SYMBOLS
                    else (round(de, 2) if de is not None and not pd.isna(de) else None)
                )
            )
        ),
    }


# ───────────────────────────────────────────────────────────────────────────
# BUILD FUNDAMENTAL SCORES FOR FULL UNIVERSE
# ───────────────────────────────────────────────────────────────────────────

def build_fundamental_scores(symbols: list, fund_df: pd.DataFrame) -> pd.DataFrame:
    """
    Score all symbols and return a DataFrame with normalized 0–10
    Fundamental_Score and quality tier label.

    Parameters
    ----------
    symbols  : ordered list of symbol strings
    fund_df  : output of refresh_fundamentals() or get_cached_fundamentals()

    Returns
    -------
    DataFrame indexed by symbol with columns:
      Fundamental_Score (0–10, normalized), Quality_Tier, all F_* sub-scores,
      and the raw fundamental data values for display.
    """
    if not symbols:
        return pd.DataFrame()

    rows = []
    for sym in symbols:
        scored = score_one_symbol(sym, fund_df)
        scored["symbol"] = sym
        rows.append(scored)

    result = pd.DataFrame(rows).set_index("symbol")

    # Fundamental_Score = raw_total / 10, i.e. the ABSOLUTE scale documented
    # at the top of this module (F1-F8 sum to a 100-point raw total; the
    # Quality Tier cutoffs below are fixed absolute values like ">= 8.0 ->
    # Quality Compounder"). This used to be a universe-relative min-max
    # normalization instead (top stock in THIS run always = 10.0, worst =
    # 0.0), which silently contradicted the documented rubric and meant the
    # "best of a mediocre universe" could land in the top quality tier
    # regardless of its actual absolute fundamentals -- defeating the whole
    # point of confluence_score.py's Quality Gate, which relies on this tier
    # to block "Lumpsum NOW" recommendations for genuinely weak businesses.
    #
    # Raw total ranges from -10 (all-zero scores + max dividend penalty) to
    # 100 (perfect score on every factor), so raw/10 ranges -1.0 to 10.0;
    # clip the display floor at 0 since a "negative quality" score isn't a
    # meaningful concept to show, it's just "as weak as it gets."
    result["Fundamental_Score"] = (result["F_raw_total"] / 10.0).clip(lower=0, upper=10).round(2)

    # Symbols with no data at all -> NaN score (never a synthetic 0 or 5)
    no_data_mask = result["F_data_fields"] == 0
    result.loc[no_data_mask, "Fundamental_Score"] = np.nan

    result["Quality_Tier"] = result["Fundamental_Score"].apply(_quality_tier)

    return result
