"""
diagnose_monthly_pivot.py
----------------------------
Run this directly in your project folder (same place as app.py) to see
EXACTLY what the Monthly Pivot S1 detector is finding for a given stock,
bypassing Streamlit and its caching entirely.

Usage:
    python diagnose_monthly_pivot.py POWERGRID
    python diagnose_monthly_pivot.py POWERGRID 2    (min_qualify_months=2)
"""
import sys
import pandas as pd

import price_cache
from monthly_pivot_pattern import compute_monthly_pivots, find_pivot_episodes

symbol = sys.argv[1] if len(sys.argv) > 1 else "POWERGRID"
min_qualify_months = int(sys.argv[2]) if len(sys.argv) > 2 else 2

hist = price_cache.get_full_history(symbol)
hist = hist.dropna(subset=["Close"])
close = hist["Close"]
ema200 = close.ewm(span=200, adjust=False).mean()

pivots = compute_monthly_pivots(hist)
s1 = pivots["S1"]

print(f"=== {symbol} | min_qualify_months={min_qualify_months} | {len(hist)} bars, "
      f"{hist.index.min().date()} to {hist.index.max().date()} ===\n")

# Show the monthly pivot table itself (one row per calendar month) so you
# can cross-check S1/R1/P against what your charting tool shows.
df = hist.copy()
df["_ym"] = df.index.to_period("M")
monthly = df.groupby("_ym").agg(H=("High", "max"), L=("Low", "min"), C=("Close", "last"))
monthly["P"] = (monthly["H"] + monthly["L"] + monthly["C"]) / 3
monthly["R1"] = 2 * monthly["P"] - monthly["L"]
monthly["S1"] = 2 * monthly["P"] - monthly["H"]
print("Monthly H/L/C and computed pivots (S1 shown here applies to the FOLLOWING month):")
print(monthly.tail(24).to_string())
print()

# Show daily S1 vs 200 EMA for a recent window, so you can see exactly where
# S1 sat relative to the 200 EMA day by day, and whether/when either was
# touched.
recent = pd.DataFrame({
    "Close": close, "Low": hist["Low"], "High": hist["High"],
    "S1": s1, "EMA200": ema200,
})
recent["S1_above_EMA"] = recent["S1"] > recent["EMA200"]
recent["Touched_S1"] = recent["Low"] <= recent["S1"]
recent["Touched_EMA"] = recent["Low"] <= recent["EMA200"]
print("Daily detail for the last 500 trading days (or fewer if less history):")
print(recent.tail(500).to_string())
print()

episodes = find_pivot_episodes(hist, s1, ema200, min_qualify_months=min_qualify_months)
print(f"Episodes detected: {len(episodes)}")
for ep in episodes:
    print(ep)

# Extra: explicitly scan for any date where S1 crossed above the 200 EMA and
# report whether that crossing ever led anywhere - useful for spotting a
# "should have been detected but wasn't" case like the one you flagged
# around Aug 2023.
print("\n=== All S1-crosses-above-200EMA events in history (candidate starts) ===")
s1_above = s1 > ema200
crosses = s1_above & ~s1_above.shift(1).fillna(False)
cross_dates = hist.index[crosses]
for d in cross_dates:
    print(f"  {d.date()}  S1={s1.loc[d]:.2f}  EMA200={ema200.loc[d]:.2f}")
