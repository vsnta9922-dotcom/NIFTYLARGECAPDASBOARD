"""
symbols_fetcher.py
------------------
Fetches the current Nifty 100 (large-cap) constituent list directly from NSE's
official archives, caches it locally with a timestamp, and only re-fetches
when the cache is older than REFRESH_DAYS (default: 90, i.e. quarterly).

This means additions/removals from the index (which NSE reviews every
quarter) get picked up automatically the next time the cache expires,
without you having to touch any code.

If NSE's site is unreachable (rate-limited, network issue, site redesign),
we fall back to the last good cached list, and if there's no cache at all,
to a small hardcoded backup list so the app never fully breaks.
"""

import json
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

CACHE_FILE = os.path.join(os.path.dirname(__file__), "symbols_cache.json")
REFRESH_DAYS = 90  # ~1 quarter

# Official NSE index constituent CSVs (large-cap universe).
# Nifty 100 = Nifty 50 + Nifty Next 50, which NSE treats as "large cap".
NSE_INDEX_CSV_URLS = {
    "NIFTY100": "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
}

# Small hardcoded emergency fallback (approx, used only if everything else
# fails: NSE unreachable AND no local symbols_cache.json exists at all --
# e.g. a brand-new install with no internet access). Real company names and
# industries here (not just the symbol repeated in both fields) so the
# dashboard is still presentable in the worst case, not showing "RELIANCE"
# as its own "company name" with a blank industry.
#
# NOTE: TATAMOTORS was replaced with TMPV (Tata Motors Passenger Vehicles
# Ltd.) -- the original TATAMOTORS ticker no longer exists following Tata
# Motors' 2024 demerger into separate Commercial Vehicles (TMCV) and
# Passenger Vehicles (TMPV) listings. A stale ticker here would simply fail
# to fetch from Yahoo Finance if this fallback path were ever actually hit.
BACKUP_SYMBOLS = [
    ("RELIANCE", "Reliance Industries Ltd.", "Oil Gas & Consumable Fuels"),
    ("TCS", "Tata Consultancy Services Ltd.", "Information Technology"),
    ("HDFCBANK", "HDFC Bank Ltd.", "Financial Services"),
    ("ICICIBANK", "ICICI Bank Ltd.", "Financial Services"),
    ("INFY", "Infosys Ltd.", "Information Technology"),
    ("ITC", "ITC Ltd.", "Fast Moving Consumer Goods"),
    ("SBIN", "State Bank of India", "Financial Services"),
    ("BHARTIARTL", "Bharti Airtel Ltd.", "Telecommunication"),
    ("LT", "Larsen & Toubro Ltd.", "Construction"),
    ("KOTAKBANK", "Kotak Mahindra Bank Ltd.", "Financial Services"),
    ("HINDUNILVR", "Hindustan Unilever Ltd.", "Fast Moving Consumer Goods"),
    ("AXISBANK", "Axis Bank Ltd.", "Financial Services"),
    ("BAJFINANCE", "Bajaj Finance Ltd.", "Financial Services"),
    ("MARUTI", "Maruti Suzuki India Ltd.", "Automobile and Auto Components"),
    ("ASIANPAINT", "Asian Paints Ltd.", "Consumer Durables"),
    ("SUNPHARMA", "Sun Pharmaceutical Industries Ltd.", "Healthcare"),
    ("TITAN", "Titan Company Ltd.", "Consumer Durables"),
    ("ULTRACEMCO", "UltraTech Cement Ltd.", "Construction Materials"),
    ("NESTLEIND", "Nestle India Ltd.", "Fast Moving Consumer Goods"),
    ("WIPRO", "Wipro Ltd.", "Information Technology"),
    ("ADANIENT", "Adani Enterprises Ltd.", "Metals & Mining"),
    ("ADANIPORTS", "Adani Ports and Special Economic Zone Ltd.", "Services"),
    ("HCLTECH", "HCL Technologies Ltd.", "Information Technology"),
    ("TMPV", "Tata Motors Passenger Vehicles Ltd.", "Automobile and Auto Components"),
    ("TATASTEEL", "Tata Steel Ltd.", "Metals & Mining"),
    ("POWERGRID", "Power Grid Corporation of India Ltd.", "Power"),
    ("NTPC", "NTPC Ltd.", "Power"),
    ("ONGC", "Oil & Natural Gas Corporation Ltd.", "Oil Gas & Consumable Fuels"),
    ("COALINDIA", "Coal India Ltd.", "Oil Gas & Consumable Fuels"),
    ("M&M", "Mahindra & Mahindra Ltd.", "Automobile and Auto Components"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,*/*",
}


def _fetch_from_nse():
    """Try to download the Nifty 100 list from NSE. Returns a DataFrame or None."""
    url = NSE_INDEX_CSV_URLS["NIFTY100"]
    session = requests.Session()
    try:
        # NSE requires an initial hit to the homepage to set cookies
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=8)
        time.sleep(0.5)
        resp = session.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        from io import StringIO

        df = pd.read_csv(StringIO(resp.text))
        df.columns = [c.strip() for c in df.columns]
        # Expected columns include 'Symbol', 'Company Name', 'Industry'
        if "Symbol" not in df.columns:
            return None
        df = df.rename(columns={"Company Name": "CompanyName"})
        keep_cols = [c for c in ["Symbol", "CompanyName", "Industry"] if c in df.columns]
        df = df[keep_cols].dropna(subset=["Symbol"])
        df["Symbol"] = df["Symbol"].str.strip()
        return df.reset_index(drop=True)
    except Exception as e:
        print(f"[symbols_fetcher] NSE fetch failed: {e}")
        return None


def _load_cache():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        df = pd.DataFrame(data["symbols"])
        return df, fetched_at
    except Exception as e:
        print(f"[symbols_fetcher] Cache read failed: {e}")
        return None


def _save_cache(df: pd.DataFrame):
    """
    Writes symbols_cache.json atomically: to a temp file in the same
    directory, then renamed into place -- same reasoning as
    price_cache._save_cache(). Lower stakes here (this file is small and
    always re-fetchable from NSE, with BACKUP_SYMBOLS as a last resort), but
    the fix is the same one line of extra care and keeps the two caches in
    this codebase consistent.
    """
    data = {
        "fetched_at": datetime.now().isoformat(),
        "symbols": df.to_dict(orient="records"),
    }
    tmp_path = CACHE_FILE + f".tmp{os.getpid()}"
    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, CACHE_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def get_symbols(force_refresh: bool = False):
    """
    Returns (df, fetched_at, source) where:
      df        - DataFrame with columns Symbol, CompanyName, Industry
      fetched_at - datetime when this list was last fetched from NSE
      source    - "nse" | "cache" | "backup"
    Auto re-fetches from NSE if cache is missing or older than REFRESH_DAYS.
    """
    cached = _load_cache()

    needs_refresh = force_refresh
    if cached is not None:
        _, fetched_at = cached
        if datetime.now() - fetched_at > timedelta(days=REFRESH_DAYS):
            needs_refresh = True
    else:
        needs_refresh = True

    if needs_refresh:
        df = _fetch_from_nse()
        if df is not None and len(df) > 0:
            _save_cache(df)
            return df, datetime.now(), "nse"
        elif cached is not None:
            df, fetched_at = cached
            return df, fetched_at, "cache (NSE unreachable, using last saved list)"
        else:
            df = pd.DataFrame(BACKUP_SYMBOLS, columns=["Symbol", "CompanyName", "Industry"])
            return df, datetime.now(), "backup (NSE unreachable, no cache found)"
    else:
        df, fetched_at = cached
        return df, fetched_at, "cache"


if __name__ == "__main__":
    df, fetched_at, source = get_symbols()
    print(f"Source: {source}, fetched_at: {fetched_at}, count: {len(df)}")
    print(df.head(10))
