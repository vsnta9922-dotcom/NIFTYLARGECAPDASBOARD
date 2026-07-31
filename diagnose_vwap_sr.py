"""
diagnose_vwap_sr.py
-----------------------
Spot-check the VWAP S/R pipeline against a known chart.

Usage:
    python diagnose_vwap_sr.py SBIN 2026-07-30
    python diagnose_vwap_sr.py --clear-cache

Prints lower-band AND upper-band Day D checks for the target date.
"""
import os
import sys

import hourly_price_cache
import price_cache
from vwap_support_resistance_pattern import compute_session_summary


def _clear_streamlit_cache():
    import streamlit as st
    cache_dir = os.path.join(os.path.expanduser("~"), ".streamlit", "cache")
    if not os.path.exists(cache_dir):
        print(f"No Streamlit cache directory found at {cache_dir}")
        return
    removed = 0
    for root, dirs, files in os.walk(cache_dir):
        for f in files:
            if "vwap" in f.lower() or "_load_ledger" in f.lower():
                try:
                    os.remove(os.path.join(root, f))
                    removed += 1
                except OSError:
                    pass
    print(f"Cleared {removed} VWAP-related cache file(s). Restart the Streamlit app to rebuild.")


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--clear-cache", "-c"):
        _clear_streamlit_cache()
        sys.exit(0)

    if len(sys.argv) < 2:
        print("Usage: python diagnose_vwap_sr.py SYMBOL [YYYY-MM-DD]")
        print("       python diagnose_vwap_sr.py --clear-cache")
        sys.exit(1)

    symbol = sys.argv[1].upper()
    target_date = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Fetching hourly data for {symbol}...")
    hourly_df = hourly_price_cache.get_hourly_history(symbol, force_refresh=True)
    if hourly_df.empty:
        print("No hourly data returned - check the symbol or your network connection.")
        sys.exit(1)

    print(f"Fetching daily data for {symbol}...")
    daily_hist = price_cache.get_full_history(symbol)
    if daily_hist.empty:
        print("Warning: no daily history found - volume reconstruction will be skipped.")

    if target_date:
        day_bars = hourly_df.loc[hourly_df.index.strftime("%Y-%m-%d") == target_date]
    else:
        last_date = hourly_df.index[-1].strftime("%Y-%m-%d")
        day_bars = hourly_df.loc[hourly_df.index.strftime("%Y-%m-%d") == last_date]
        target_date = last_date

    print(f"\n=== Hourly bars for {symbol} on {target_date} ===")
    if day_bars.empty:
        print("No bars found for that date.")
        sys.exit(1)
    print(day_bars[["Open", "High", "Low", "Close", "Volume"]].to_string())

    print(f"\n=== Computed first-hour bar ===")
    print(day_bars.iloc[[0]][["Open", "High", "Low", "Close"]].to_string())
    print(f"first_hour_high = {float(day_bars.iloc[0]['High']):.2f}")
    print(f"first_hour_low  = {float(day_bars.iloc[0]['Low']):.2f}")

    first_bar_vol = float(day_bars.iloc[0]["Volume"])
    if first_bar_vol == 0 and not daily_hist.empty:
        daily_row = daily_hist.loc[daily_hist.index.strftime("%Y-%m-%d") == target_date]
        if not daily_row.empty:
            daily_total = float(daily_row["Volume"].iloc[0])
            other_vol = float(day_bars.iloc[1:]["Volume"].sum())
            reconstructed = max(0.0, daily_total - other_vol)
            print(f"\n⚠️  First-bar volume is ZERO in raw Yahoo feed.")
            print(f"    Daily total volume = {daily_total:,.0f}")
            print(f"    Sum of other bars  = {other_vol:,.0f}")
            print(f"    Reconstructed      = {reconstructed:,.0f}")

    print(f"\n=== Computed session VWAP / bands, end of day ===")
    summary = compute_session_summary(hourly_df, daily_hist)
    if target_date not in summary.index.strftime("%Y-%m-%d"):
        print("Session summary has no row for this date.")
        sys.exit(1)
    row = summary.loc[summary.index.strftime("%Y-%m-%d") == target_date].iloc[0]
    print(f"vwap_close       = {row['vwap_close']:.2f}")
    print(f"lower_band_close = {row['lower_band_close']:.2f}")
    print(f"upper_band_close = {row['upper_band_close']:.2f}")
    print(f"\n📐 Band formula: deviations from FINAL VWAP ({row['vwap_close']:.2f})")

    print(f"\n{'='*60}")
    print("DAY D SIGNAL CHECKS (min_gap_pct = 0.5%)")
    print(f"{'='*60}")

    # Lower band
    lb_gap = (row["lower_band_close"] - row["first_hour_high"]) / row["first_hour_high"] * 100
    lb_fire = lb_gap > 0.5
    print(f"\n[LOWER BAND]  lower_band ({row['lower_band_close']:.2f}) vs first_hour_high ({row['first_hour_high']:.2f})")
    print(f"              gap = {lb_gap:+.3f}%  {'✅ PASSES 0.5% threshold' if lb_fire else '❌ FAILS 0.5% threshold (too noisy)'}")

    # Upper band
    ub_gap = (row["first_hour_low"] - row["upper_band_close"]) / row["first_hour_low"] * 100
    ub_fire = ub_gap > 0.5
    print(f"\n[UPPER BAND]  upper_band ({row['upper_band_close']:.2f}) vs first_hour_low ({row['first_hour_low']:.2f})")
    print(f"              gap = {ub_gap:+.3f}%  {'✅ PASSES 0.5% threshold' if ub_fire else '❌ FAILS 0.5% threshold (too noisy)'}")

    if row["has_zero_volume_bar"]:
        print("\n⚠️  WARNING: at least one bar still has zero volume after reconstruction.")

    print(f"\n>>> Compare VWAP/band values directly against your chart for {target_date}.")
    print(f">>> If signals show YES but dashboard doesn't list them, run:")
    print(f">>>   python diagnose_vwap_sr.py --clear-cache")
    print(f">>> Then restart Streamlit.")


if __name__ == "__main__":
    main()
