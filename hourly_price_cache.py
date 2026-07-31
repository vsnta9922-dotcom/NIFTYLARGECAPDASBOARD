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


def get_hourly_history(symbol: str, force_refresh: bool = False) -> pd.DataFrame:
    """
    Returns the rolling-window hourly OHLCV DataFrame for `symbol`
    (columns: Open, High, Low, Close, Volume), refetching from Yahoo only
    if the local cache is missing, stale (older than REFRESH_TTL_HOURS), or
    `force_refresh=True`. Returns an empty DataFrame if the fetch fails and
    there's no usable cache to fall back on.
    """
    if not force_refresh and _cache_is_fresh(symbol):
        cached = _load_cached(symbol)
        if cached is not None and not cached.empty:
            return cached

    yf_sym = f"{symbol}.NS"
    fresh = _fetch_with_timeout(yf_sym)
    if fresh is not None and not fresh.empty:
        fresh.index = pd.to_datetime(fresh.index)
        # Explicitly normalize to IST (Asia/Kolkata) rather than assuming
        # yfinance already hands back exchange-local timestamps for .NS
        # tickers - that assumption was never actually verified against
        # live data. If the index is tz-aware in some OTHER zone (or UTC),
        # converting first is essential: grouping bars by calendar date
        # without this would silently put some bars in the wrong session
        # near midnight boundaries, throwing off that session's VWAP/band
        # (and, since first-hour bucketing now takes the session's first
        # bar directly, could also misidentify which bar IS the first hour).
        # If the index is naive (no tz info at all), we can't safely assume
        # what zone it's already in - leave it as-is and flag it, since
        # silently localizing a naive timestamp risks a wrong assumption in
        # the OTHER direction.
        if fresh.index.tz is not None:
            fresh.index = fresh.index.tz_convert("Asia/Kolkata").tz_localize(None)
        else:
            _log.warning(
                "[hourly_price_cache] %s: fetched index has no timezone info - "
                "assuming it's already IST, but this hasn't been verified against "
                "live data. Run a spot-check against a known chart if session "
                "boundaries look off.", symbol,
            )
        _save_cache(symbol, fresh)
        return fresh

    # Fetch failed - fall back to whatever's cached, even if stale, rather
    # than returning nothing.
    cached = _load_cached(symbol)
    return cached if cached is not None else pd.DataFrame()
