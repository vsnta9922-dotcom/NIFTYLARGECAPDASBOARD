"""
diagnose_ema_pullback.py
--------------------------
Run this directly in your project folder to see exactly what the
EMA Pullback Reentry detector is finding for a given stock.

Usage:
    python diagnose_ema_pullback.py TCS
    python diagnose_ema_pullback.py TCS 30         (min_qualify_days=30)
    python diagnose_ema_pullback.py TCS 50 5 8     (qualify=50, retest=5%, fail=8%)
"""
import sys
import pandas as pd

import price_cache
from ema_pullback_pattern import find_ema_pullback_episodes

symbol           = sys.argv[1] if len(sys.argv) > 1 else "TCS"
min_qualify_days = int(sys.argv[2])   if len(sys.argv) > 2 else 50
retest_pct       = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
fail_pct         = float(sys.argv[4]) if len(sys.argv) > 4 else 8.0

hist = price_cache.get_full_history(symbol)
hist = hist.dropna(subset=["Close"])
close = hist["Close"]

ema20 = close.ewm(span=20, adjust=False).mean()
ema50 = close.ewm(span=50, adjust=False).mean()

print(
    f"=== {symbol} | min_qualify_days={min_qualify_days} | "
    f"retest_pct={retest_pct}% | fail_pct={fail_pct}% | "
    f"{len(hist)} bars, {hist.index.min().date()} to {hist.index.max().date()} ===\n"
)

# Show 20/50 EMA state for last 150 bars
recent = pd.DataFrame({
    "Close": close, "Low": hist["Low"], "High": hist["High"],
    "EMA20": ema20, "EMA50": ema50,
})
recent["20_above_50"] = ema20 > ema50
recent["Low_touches_50"] = hist["Low"] <= ema50
print("Recent EMA states (last 150 bars):")
print(recent.tail(150).to_string())
print()

episodes = find_ema_pullback_episodes(
    hist, ema20, ema50,
    min_qualify_days=min_qualify_days,
    retest_pct=retest_pct,
    fail_pct=fail_pct,
)
print(f"Episodes detected: {len(episodes)}\n")
for i, ep in enumerate(episodes, 1):
    print(f"--- Episode {i} ---")
    print(f"  Crossover date (20 EMA crosses above 50 EMA): {ep['crossover_date'].date()}")
    print(f"  Qualify end date (day {min_qualify_days} of clean window): {ep['qualify_end_date'].date() if ep['qualify_end_date'] else 'N/A'}")
    print(f"  Touch date (50 EMA pullback, X locked):       {ep['touch_date'].date() if ep['touch_date'] else 'N/A'}")
    print(f"  X (highest High crossover → touch):           ₹{ep['x_price']:.2f}" if ep['x_price'] else "  X: N/A")
    print(f"  Y (lowest Low touch → signal):                ₹{ep['y_price']:.2f}" if ep['y_price'] else "  Y: N/A")
    print(f"  Y fix date (50 EMA crosses above X):          {ep['y_fix_date'].date() if ep['y_fix_date'] else 'N/A (pending)'}")
    print(f"  Status:         {ep['status']}")
    print(f"  Tested date:    {ep['tested_date'].date() if ep['tested_date'] else 'N/A'}")
    print(f"  Failed date:    {ep['failed_date'].date() if ep['failed_date'] else 'N/A'}")
    print(f"  Max run-up:     {ep['max_runup_pct']:.1f}%")
    print(f"  Days tracked:   {ep['days_tracked']}")
    print(f"  Post-event drawdown: {ep['post_event_drawdown_pct']:.1f}%" if ep['post_event_drawdown_pct'] is not None else "  Post-event drawdown: N/A")
    print(f"  Post-event recovery: {ep['post_event_days_to_recover']} days" if ep['post_event_days_to_recover'] is not None else "  Post-event recovery: N/A")
    print()

# Quick sanity check: show all 20/50 golden crosses in history
above = ema20 > ema50
crosses = above & ~above.shift(1).fillna(False)
cross_dates = hist.index[crosses]
print(f"=== All 20/50 golden crosses in full history ({len(cross_dates)} total) ===")
for d in cross_dates:
    print(f"  {d.date()}  EMA20={ema20.loc[d]:.2f}  EMA50={ema50.loc[d]:.2f}")
