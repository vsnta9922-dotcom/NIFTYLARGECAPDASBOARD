"""
diagnose_breakout_pullback.py
------------------------------
Run this directly in your project folder to see exactly what the
Breakout-Pullback 4-Leg detector is finding for a given stock.

Usage:
    python diagnose_breakout_pullback.py TCS
    python diagnose_breakout_pullback.py TCS 5 8    (retest_pct=5, fail_pct=8)
"""
import sys
import pandas as pd

import price_cache
from breakout_pullback_pattern import find_breakout_pullback_episodes

symbol = sys.argv[1] if len(sys.argv) > 1 else "TCS"
retest_pct = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
fail_pct = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0

hist = price_cache.get_full_history(symbol)
hist = hist.dropna(subset=["Close"])
close = hist["Close"]

ema20 = close.ewm(span=20, adjust=False).mean()
ema50 = close.ewm(span=50, adjust=False).mean()
ema200 = close.ewm(span=200, adjust=False).mean()

print(f"=== {symbol} | retest_pct={retest_pct}% | fail_pct={fail_pct}% | {len(hist)} bars, "
      f"{hist.index.min().date()} to {hist.index.max().date()} ===\n")

recent = pd.DataFrame({
    "Close": close, "Low": hist["Low"], "High": hist["High"],
    "EMA20": ema20, "EMA50": ema50, "EMA200": ema200,
})
recent["20_below_50"] = recent["EMA20"] < recent["EMA50"]
recent["both_below_200"] = (recent["EMA20"] < recent["EMA200"]) & (recent["EMA50"] < recent["EMA200"])
print("Recent EMA states (last 100 bars):")
print(recent.tail(100).to_string())
print()

episodes = find_breakout_pullback_episodes(hist, ema20, ema50, ema200, retest_pct=retest_pct, fail_pct=fail_pct)
print(f"Episodes detected: {len(episodes)}\n")
for ep in episodes:
    print(f"  Leg1: {ep['leg1_start'].date()}")
    print(f"  Leg2: {ep['leg2_start'].date() if ep['leg2_start'] else 'N/A'}")
    print(f"  Leg3: {ep['leg3_start'].date() if ep['leg3_start'] else 'N/A'}")
    print(f"  Leg4: {ep['leg4_start'].date() if ep['leg4_start'] else 'N/A'}")
    print(f"  Signal: {ep['signal_date'].date() if ep['signal_date'] else 'N/A'}")
    print(f"  X (Leg2 High Close): ₹{ep['x_price']:.2f}")
    print(f"  Y (Leg3 Low 50EMA):  ₹{ep['y_price']:.2f}")
    print(f"  Z (Leg3 Low Price):  ₹{ep['z_price']:.2f}")
    print(f"  Leg1 Low (invalidation): ₹{ep['leg1_low_price']:.2f}")
    print(f"  Status: {ep['status']}")
    print(f"  Y Retest: {ep['y_retest_status']} ({ep['y_tested_date'].date() if ep['y_tested_date'] else 'N/A'})")
    print(f"  Z Retest: {ep['z_retest_status']} ({ep['z_tested_date'].date() if ep['z_tested_date'] else 'N/A'})")
    print(f"  Failed: {ep['failed_date'].date() if ep['failed_date'] else 'N/A'}")
    print(f"  Max Run-up: {ep['max_runup_pct']:.1f}% | Days Tracked: {ep['days_tracked']}")
    print()