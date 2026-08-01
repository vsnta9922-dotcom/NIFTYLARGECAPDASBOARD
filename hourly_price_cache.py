"""
hourly_price_cache.py
------------------------
Local on-disk cache of HOURLY OHLCV history per symbol (as Parquet files
under ./hourly_price_cache/), for the new VWAP Support/Resistance strategy.

This is a DIFFERENT lifecycle model from price_cache.py's daily cache:
  - price_cache.py's daily history is EVER-GROWING (seeded once with
    period="max", then only ever appended to) because Yahoo itself can
    supply a stock's full daily history.
  - Yahoo's HOURLY data (interval="60m") is only ever a ROLLING window
    (roughly the last ~2 years, per Yahoo's own API limits - not
    something we control) - there's no period="max" equivalent for it.
    So this cache can't "grow forever" the way the daily one does; it
    just refetches the available rolling window periodically and
    replaces what it has. Any VWAP S/R episode older than that window
    simply isn't visible - a real, disclosed limitation of this
    strategy, not a bug.

Kept completely separate from price_cache.py (different cache directory,
different refresh semantics) so this experimental strategy carries zero
risk to the daily cache every other strategy depends on.

═══════════════════════════════════════════════════════════════════════════
JULY 2026 — SCALING TO NIFTY 100
═══════════════════════════════════════════════════════════════════════════
Added bulk_refresh_hourly_histories() to parallelise the ~100-symbol
universe refresh.  Previously every symbol was fetched individually via
yf.Ticker().history() — ~100 sequential network calls taking 60–120s.
Now chunked yf.download() calls (CHUNK_SIZE=15, same as daily cache)
with hard timeouts reduce the full-universe refresh to ~15–30s.

A _session_refreshed set (mirroring price_cache.py) prevents double-
fetching when get_hourly_history() is called after bulk_refresh.
"""
import concurrent.futures
import logging
import os
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

_log = logging.getLogger("hourly_price_cache")

CACHE_DIR = os.path.join(os.path.dirname(__file__), "hourly_price_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# Yahoo's own limit for interval="60m" is ~730 days - asking for more just
# gets silently truncated, so there's no benefit to requesting further back.
HOURLY_PERIOD = "730d"
HOURLY_INTERVAL = "60m"

# Rolling data like this doesn't need refetching every single call - once
# refreshed, treat it as fresh for the rest of the trading day.
REFRESH_TTL_HOURS = 6

BATCH_TIMEOUT_SECONDS = 45

# ── NIFTY 100 scaling: chunked batch parameters ──────────────────────────
CHUNK_SIZE = 15

# Symbols already refreshed via bulk_refresh_hourly_histories() in THIS
# process run.  get_hourly_history() checks this first so it never re-
# issues an individual network request for a symbol the bulk prefetch
# already just updated.
_session_refreshed = set()


def _cache_path(symbol: str) -> str:
    return os.path.join(CACHE_DIR, f"{symbol}.parquet")


def _load_cached(symbol: str):
    path = _cache_path(symbol)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index)
        return df
    except Exception as e:
        _log.warning("[hourly_price_cache] Failed reading cache for %s: %s", symbol, e)
        return None


def _cache_is_fresh(symbol: str) -> bool:
    path = _cache_path(symbol)
    if not os.path.exists(path):
        return False
    age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))
    return age < timedelta(hours=REFRESH_TTL_HOURS)


def _save_cache(symbol: str, df: pd.DataFrame):
    """Atomic same-directory temp-file + os.replace(), identical reasoning to
    price_cache._save_cache(): a process killed mid-write never leaves a
    corrupt file in place, just an orphaned temp file."""
    final_path = _cache_path(symbol)
    tmp_path = final_path + f".tmp{os.getpid()}"
    try:
        df.to_parquet(tmp_path)
        os.replace(tmp_path, final_path)
    except Exception as e:
        _log.warning("[hourly_price_cache] Failed saving cache for %s: %s", symbol, e)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _fetch_with_timeout(yf_sym: str, timeout_seconds: int = 30):
    """Same hard-timeout guard as price_cache._fetch_ticker_history_with_timeout
    - a network hang never raises an exception on its own, so only a
    wrapped-thread-with-timeout can actually recover from it."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        lambda: yf.Ticker(yf_sym).history(period=HOURLY_PERIOD, interval=HOURLY_INTERVAL)
    )
    try:
        result = future.result(timeout=timeout_seconds)
        executor.shutdown(wait=False)
        return result
    except concurrent.futures.TimeoutError:
        _log.warning("[hourly_price_cache] Fetch timed out after %ss for %s", timeout_seconds, yf_sym)
        executor.shutdown(wait=False)
        return None
    except Exception as e:
        _log.warning("[hourly_price_cache] Fetch failed for %s: %s", yf_sym, e)
        executor.shutdown(wait=False)
        return None


# ───────────────────────────────────────────────────────────────────────────
# BATCH REFRESH — new for NIFTY 100 scaling
# ───────────────────────────────────────────────────────────────────────────

def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _download_with_timeout(tickers_str: str, timeout_seconds: int, **kwargs):
    """
    Runs yf.download() in a background thread and enforces a HARD timeout.
    Deliberately does NOT use the executor as a context manager — see
    price_cache._download_with_timeout() for the full rationale.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(yf.download, tickers=tickers_str, progress=False, **kwargs)
    try:
        result = future.result(timeout=timeout_seconds)
        executor.shutdown(wait=False)
        return result
    except concurrent.futures.TimeoutError:
        _log.warning(
            "[hourly_price_cache] Batch download timed out after %ss for tickers: %s",
            timeout_seconds, tickers_str[:80] + ("..." if len(tickers_str) > 80 else ""),
        )
        executor.shutdown(wait=False)
        return None
    except Exception as e:
        _log.warning("[hourly_price_cache] Batch download failed: %s", e)
        executor.shutdown(wait=False)
        return None


def _extract(data, sym, n_total):
    """Pulls symbol `sym`'s OHLCV slice out of a (possibly multi-ticker)
    yf.download() result.  Same logic as price_cache._extract()."""
    yf_sym = f"{sym}.NS"
    try:
        if isinstance(data.columns, pd.MultiIndex):
            if yf_sym not in data.columns.get_level_values(0):
                return None
            sub = data[yf_sym]
        else:
            sub = data
    except (KeyError, TypeError):
        return None
    if sub is None:
        return None
    sub = sub.dropna(how="all")
    if sub.empty:
        return None
    sub.index = pd.to_datetime(sub.index).tz_localize(None)
    return sub


def _normalize_ist(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize index to IST (Asia/Kolkata) then strip tz info."""
    if df.index.tz is not None:
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
    else:
        _log.warning(
            "[hourly_price_cache] fetched index has no timezone info — "
            "assuming it's already IST, but this hasn't been verified against "
            "live data. Run a spot-check if session boundaries look off.",
        )
    return df


def bulk_refresh_hourly_histories(symbols: list):
    """
    Refreshes the local hourly cache for ALL given symbols using chunked,
    threaded, timeout-guarded yf.download() calls.

    Unlike the daily cache (which seeds once with period="max" and then
    only appends), hourly data is a rolling window — we always replace
    the entire cache file for each symbol with the latest available window.

    This should be called ONCE before a loop that then calls
    get_hourly_history() per symbol — those calls will find the cache
    already fresh and skip any further network access.
    """
    to_fetch = []
    for sym in symbols:
        if not _cache_is_fresh(sym):
            to_fetch.append(sym)

    if not to_fetch:
        _log.info("[hourly_price_cache] All %d symbols are fresh — skipping batch refresh.", len(symbols))
        return

    chunks = list(_chunked(to_fetch, CHUNK_SIZE))
    for i, chunk in enumerate(chunks, 1):
        _log.info(
            "[hourly_price_cache] Batch %d/%d (%d symbols)...",
            i, len(chunks), len(chunk),
        )
        tickers_str = " ".join(f"{s}.NS" for s in chunk)
        data = _download_with_timeout(
            tickers_str, BATCH_TIMEOUT_SECONDS,
            period=HOURLY_PERIOD, interval=HOURLY_INTERVAL,
            group_by="ticker", auto_adjust=True, threads=False,
        )
        if data is None:
            continue  # fall through to per-symbol fetch later
        for sym in chunk:
            fresh = _extract(data, sym, len(chunk))
            if fresh is not None and not fresh.empty:
                fresh = _normalize_ist(fresh)
                _save_cache(sym, fresh)
                _session_refreshed.add(sym)


def get_hourly_history(symbol: str, force_refresh: bool = False) -> pd.DataFrame:
    """
    Returns the rolling-window hourly OHLCV DataFrame for `symbol`
    (columns: Open, High, Low, Close, Volume), refetching from Yahoo only
    if the local cache is missing, stale (older than REFRESH_TTL_HOURS), or
    `force_refresh=True`. Returns an empty DataFrame if the fetch fails and
    there's no usable cache to fall back on.

    If `symbol` was already refreshed this session via
    bulk_refresh_hourly_histories(), this just reads the local cache directly.
    """
    if not force_refresh and symbol in _session_refreshed:
        cached = _load_cached(symbol)
        if cached is not None and not cached.empty:
            return cached

    if not force_refresh and _cache_is_fresh(symbol):
        cached = _load_cached(symbol)
        if cached is not None and not cached.empty:
            return cached

    yf_sym = f"{symbol}.NS"
    fresh = _fetch_with_timeout(yf_sym)
    if fresh is not None and not fresh.empty:
        fresh.index = pd.to_datetime(fresh.index)
        fresh = _normalize_ist(fresh)
        _save_cache(symbol, fresh)
        return fresh

    # Fetch failed - fall back to whatever's cached, even if stale, rather
    # than returning nothing.
    cached = _load_cached(symbol)
    return cached if cached is not None else pd.DataFrame()