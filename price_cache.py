"""
price_cache.py
---------------
Maintains a local, ever-growing on-disk cache of daily OHLCV history per
symbol (as Parquet files under ./price_cache/).

Why this exists:
  - Re-downloading years of history from Yahoo Finance on every run is slow
    and wastes API calls.
  - More importantly: if we only ever pulled a rolling window (e.g. "5y"),
    any streak/reference-level that happened before that window would
    silently become invisible once enough real time passes - even though
    it's still a perfectly valid level to watch for a retest.

By caching each symbol's full history locally and only ever *appending* new
days to it, the cache keeps growing forever. First run seeds it with the
maximum history Yahoo can supply; every run after that just fetches the
handful of new days since the last cache update and merges them in. Nothing
already in the cache is ever dropped, so reference levels from 5, 7, or 10+
years ago remain visible indefinitely.
"""

import concurrent.futures
import logging
import os
import pandas as pd
import yfinance as yf

_log = logging.getLogger("price_cache")

CACHE_DIR = os.path.join(os.path.dirname(__file__), "price_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# Symbols already refreshed via bulk_refresh_histories() in THIS process run.
# get_full_history() checks this first so it never re-issues an individual
# network request for a symbol the bulk prefetch already just updated.
_session_refreshed = set()

# How many tickers go into a single yf.download() call. Earlier versions of
# this function put ALL ~100+ tickers into one call - that can silently hang
# (not raise an exception, just block forever on the socket) if Yahoo
# throttles or is slow to respond to one huge combined request. Small
# chunks are far less likely to trigger that, and even if one chunk does
# hang, the hard timeout below (not just try/except - a hang never raises
# an exception, so try/except alone can't catch it) bails out of just that
# chunk rather than freezing the whole app.
CHUNK_SIZE = 15
BATCH_TIMEOUT_SECONDS = 45


def _cache_path(symbol: str) -> str:
    return os.path.join(CACHE_DIR, f"{symbol}.parquet")


def _load_cached(symbol: str):
    path = _cache_path(symbol)
    if os.path.exists(path):
        try:
            df = pd.read_parquet(path)
            df.index = pd.to_datetime(df.index)
            return df
        except Exception as e:
            _log.warning("[price_cache] Failed reading cache for %s: %s", symbol, e)
            return None
    return None


def _save_cache(symbol: str, df: pd.DataFrame):
    """
    Writes `symbol`'s cache atomically: to a temp file in the SAME directory
    (so os.replace() is a same-filesystem rename, not a copy), then renamed
    into place. A process killed mid-write (Streamlit hot-reload, OOM-kill,
    Ctrl+C at the wrong moment) leaves the temp file orphaned but the real
    cache file untouched -- never a half-written/corrupt parquet. Without
    this, a truncated write here silently loses however many years of price
    history this symbol's cache held (the whole reason this module exists is
    to preserve history older than what Yahoo's rolling window would show),
    and get_full_history() would have no way to distinguish "genuinely no
    cache yet" from "cache got corrupted" -- it just reseeds either way.
    """
    final_path = _cache_path(symbol)
    tmp_path = final_path + f".tmp{os.getpid()}"
    try:
        df.to_parquet(tmp_path)
        os.replace(tmp_path, final_path)
    except Exception as e:
        _log.warning("[price_cache] Failed saving cache for %s: %s", symbol, e)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _download_with_timeout(tickers_str: str, timeout_seconds: int, **kwargs):
    """
    Runs yf.download() in a background thread and enforces a HARD timeout
    via .result(timeout=...). A plain try/except around yf.download() is not
    enough here - a network hang never raises an exception, it just blocks
    forever, so only a wrapped-thread-with-timeout can actually recover from
    that. Returns None (rather than raising) if the call times out or
    errors, so the caller can safely fall back.

    Important: deliberately does NOT use the executor as a context manager.
    `with ThreadPoolExecutor() as executor:` calls `shutdown(wait=True)` on
    exit, which blocks until every submitted thread actually finishes -
    including one we've already given up on via the .result() timeout. That
    would silently defeat the entire point of the timeout (the function
    would still take as long as the hang itself, just print a message
    first). Calling shutdown(wait=False) instead lets us return immediately;
    the abandoned thread lingers harmlessly in the background until the
    underlying network call eventually resolves or errors on its own.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(yf.download, tickers=tickers_str, progress=False, **kwargs)
    try:
        result = future.result(timeout=timeout_seconds)
        executor.shutdown(wait=False)
        return result
    except concurrent.futures.TimeoutError:
        _log.warning("[price_cache] Batch download timed out after %ss for tickers: %s",
                     timeout_seconds, tickers_str[:80] + ("..." if len(tickers_str) > 80 else ""))
        executor.shutdown(wait=False)
        return None
    except Exception as e:
        _log.warning("[price_cache] Batch download failed: %s", e)
        executor.shutdown(wait=False)
        return None


def bulk_refresh_histories(symbols: list, refetch_buffer_days: int = 7):
    """
    Refreshes the local cache for ALL given symbols using a small number of
    chunked, threaded, timeout-guarded yf.download() calls, instead of one
    sequential request per symbol.

    Symbols with no local cache yet ("seed" symbols) and symbols that
    already have one ("update" symbols) are each split into chunks of
    CHUNK_SIZE tickers. Each chunk is fetched in one call with a hard
    BATCH_TIMEOUT_SECONDS timeout; if a chunk times out or errors, its
    symbols are simply left out of `_session_refreshed` for this run - the
    normal per-symbol fallback logic in get_full_history() will pick them
    up individually the next time they're requested (slower for just that
    chunk, but never blocks the whole app).

    This should be called ONCE, before a loop that then calls
    get_full_history() per symbol - those calls will find the cache
    already fresh (for successfully-refreshed symbols) and skip any further
    network access for them.
    """
    to_seed = []
    to_update = {}

    for sym in symbols:
        cached = _load_cached(sym)
        if cached is None or cached.empty:
            to_seed.append(sym)
        else:
            last_date = cached.index.max()
            start = (last_date - pd.Timedelta(days=refetch_buffer_days)).strftime("%Y-%m-%d")
            to_update[sym] = start

    def _extract(data, sym, n_total):
        """
        Pulls symbol `sym`'s OHLCV slice out of a (possibly multi-ticker)
        yf.download() result.

        yf.download(group_by="ticker") returns a MultiIndex-columned
        DataFrame (top level = ticker) when it was called with MULTIPLE
        tickers, but a flat single-level DataFrame when called with exactly
        ONE ticker -- regardless of how many symbols conceptually "belong"
        to this chunk. The previous version inferred this from `n_total`
        (the chunk's symbol count), which is actually a proxy for "how many
        tickers were requested," not "how many yfinance decided to return
        with grouped columns" -- those coincide almost always, but not for
        a chunk of exactly 1 (e.g. whenever len(symbols) % CHUNK_SIZE == 1,
        which shifts every quarter as NSE's Nifty 100 constituent list is
        reconstituted, so it's a matter of when, not if, this bites).
        Inspecting `data.columns` directly is unambiguous and doesn't rely
        on chunk cardinality at all.
        """
        yf_sym = f"{sym}.NS"
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if yf_sym not in data.columns.get_level_values(0):
                    return None
                sub = data[yf_sym]
            else:
                # Flat columns -- this download only ever covered one ticker,
                # so the whole frame IS that ticker's data (regardless of how
                # many symbols were nominally in the chunk).
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

    if to_seed:
        seed_chunks = list(_chunked(to_seed, CHUNK_SIZE))
        for i, chunk in enumerate(seed_chunks, 1):
            _log.info("[price_cache] Seeding history batch %d/%d (%d symbols)...", i, len(seed_chunks), len(chunk))
            tickers_str = " ".join(f"{s}.NS" for s in chunk)
            data = _download_with_timeout(
                tickers_str, BATCH_TIMEOUT_SECONDS,
                period="max", interval="1d", group_by="ticker", auto_adjust=True, threads=False,
            )
            if data is None:
                continue  # this chunk's symbols fall through to per-symbol fetch later
            for sym in chunk:
                fresh = _extract(data, sym, len(chunk))
                if fresh is not None:
                    _save_cache(sym, fresh)
                    _session_refreshed.add(sym)

    if to_update:
        update_items = list(to_update.items())
        update_chunks = list(_chunked(update_items, CHUNK_SIZE))
        for i, chunk_items in enumerate(update_chunks, 1):
            _log.info("[price_cache] Updating history batch %d/%d (%d symbols)...", i, len(update_chunks), len(chunk_items))
            chunk_syms = [s for s, _ in chunk_items]
            earliest_start = min(start for _, start in chunk_items)
            tickers_str = " ".join(f"{s}.NS" for s in chunk_syms)
            data = _download_with_timeout(
                tickers_str, BATCH_TIMEOUT_SECONDS,
                start=earliest_start, interval="1d", group_by="ticker", auto_adjust=True, threads=False,
            )
            if data is None:
                continue  # falls through to per-symbol fetch later
            for sym in chunk_syms:
                fresh = _extract(data, sym, len(chunk_syms))
                if fresh is not None:
                    cached = _load_cached(sym)
                    if cached is not None and not cached.empty:
                        merged = pd.concat([cached[cached.index < fresh.index.min()], fresh])
                        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                    else:
                        merged = fresh
                    _save_cache(sym, merged)
                    _session_refreshed.add(sym)  # only mark refreshed when data was actually received


def _fetch_ticker_history_with_timeout(yf_sym: str, timeout_seconds: int = 30, **kwargs):
    """
    Same hard-timeout protection as _download_with_timeout (including the
    same non-blocking shutdown(wait=False) - see that function's docstring
    for why `with ThreadPoolExecutor()` would silently defeat the timeout),
    but for a single-ticker yf.Ticker(...).history() call - used by the
    per-symbol fallback path below. A symbol that fell out of a timed-out
    batch (or is being fetched standalone, e.g. by the chart viewer) still
    goes through a real network call here, so it needs the same guard
    against a hang that never raises an exception.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(lambda: yf.Ticker(yf_sym).history(**kwargs))
    try:
        result = future.result(timeout=timeout_seconds)
        executor.shutdown(wait=False)
        return result
    except concurrent.futures.TimeoutError:
        _log.warning("[price_cache] Single-symbol fetch timed out after %ss for %s", timeout_seconds, yf_sym)
        executor.shutdown(wait=False)
        return None
    except Exception as e:
        _log.warning("[price_cache] Single-symbol fetch failed for %s: %s", yf_sym, e)
        executor.shutdown(wait=False)
        return None


def get_full_history(symbol: str, refetch_buffer_days: int = 7) -> pd.DataFrame:
    """
    Returns the full historical daily OHLCV DataFrame for `symbol`
    (columns: Open, High, Low, Close, Volume), merging any locally cached
    history with freshly fetched recent data from Yahoo Finance.

    If `symbol` was already refreshed this session via bulk_refresh_histories(),
    this just reads the local cache directly - no further network call.

    Otherwise (e.g. called standalone for a single symbol, such as the
    chart viewer, or a symbol whose batch in bulk_refresh_histories timed
    out and fell through here):
    - If no cache exists yet: pulls `period="max"` (Yahoo's full available
      history for the symbol) and seeds the cache.
    - If a cache exists: only fetches from a few days before the last
      cached date onward (the small overlap absorbs any late price
      corrections Yahoo sometimes applies to recent days), then merges.
    Every network call here is wrapped with the same hard timeout used in
    the bulk path, so a single stubborn symbol can't hang the whole app.
    """
    if symbol in _session_refreshed:
        cached = _load_cached(symbol)
        if cached is not None and not cached.empty:
            return cached

    yf_sym = f"{symbol}.NS"
    cached = _load_cached(symbol)

    if cached is None or cached.empty:
        fresh = _fetch_ticker_history_with_timeout(yf_sym, period="max", interval="1d", auto_adjust=True)
        if fresh is None or fresh.empty:
            return pd.DataFrame()
        fresh.index = pd.to_datetime(fresh.index).tz_localize(None)
        _save_cache(symbol, fresh)
        return fresh

    last_date = cached.index.max()
    start = (last_date - pd.Timedelta(days=refetch_buffer_days)).strftime("%Y-%m-%d")

    fresh = _fetch_ticker_history_with_timeout(yf_sym, start=start, interval="1d", auto_adjust=True)

    if fresh is not None and not fresh.empty:
        fresh.index = pd.to_datetime(fresh.index).tz_localize(None)
        merged = pd.concat([cached[cached.index < fresh.index.min()], fresh])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        _save_cache(symbol, merged)
        return merged

    return cached