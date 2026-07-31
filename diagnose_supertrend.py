"""
diagnose_supertrend.py
------------------------
Run in your project folder to see exactly what the Supertrend 3-Phase
detector finds for a given stock and variant.

Usage:
    python diagnose_supertrend.py ASIANPAINT
    python diagnose_supertrend.py ASIANPAINT 7 3       (period=7, mult=3.0)
    python diagnose_supertrend.py ASIANPAINT 10 3 5 8  (+ retest=5%, fail=8%)
"""
import sys
import pandas as pd
import price_cache
from supertrend_pattern import compute_supertrend, find_supertrend_episodes

symbol      = sys.argv[1] if len(sys.argv) > 1 else "ASIANPAINT"
st_period   = int(sys.argv[2])   if len(sys.argv) > 2 else 7
st_mult     = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
retest_pct  = float(sys.argv[4]) if len(sys.argv) > 4 else 5.0
fail_pct    = float(sys.argv[5]) if len(sys.argv) > 5 else 8.0

hist = price_cache.get_full_history(symbol)
hist = hist.dropna(subset=["Close"])
close = hist["Close"]
ema200 = close.ewm(span=200, adjust=False).mean()

st_df = compute_supertrend(hist, period=st_period, multiplier=st_mult)

print(f"=== {symbol} | ST({st_period}, {st_mult}) | retest={retest_pct}% fail={fail_pct}% | "
      f"{len(hist)} bars, {hist.index.min().date()} → {hist.index.max().date()} ===\n")

# Show the last 120 bars of the Supertrend signal for verification
recent = pd.DataFrame({
    "Close": close, "High": hist["High"], "Low": hist["Low"],
    "EMA200": ema200,
    "ST_line": st_df["supertrend"],
    "ST_dir": st_df["direction"],  # +1=buy, -1=sell
})
recent["ST_above_EMA200"] = st_df["supertrend"] >= ema200
print("Last 120 bars:")
print(recent.tail(120).to_string())
print()

episodes = find_supertrend_episodes(
    hist, ema200,
    st_period=st_period, st_multiplier=st_mult,
    retest_pct=retest_pct, fail_pct=fail_pct,
)
print(f"Episodes detected: {len(episodes)}\n")
for i, ep in enumerate(episodes, 1):
    print(f"--- Episode {i} | ST({ep['st_period']}, {ep['st_multiplier']}) ---")
    print(f"  Phase 1 start  : {ep['phase1_start'].date()}")
    print(f"  Phase 1 end    : {ep['phase1_end'].date() if ep.get('phase1_end') else '—'}")
    print(f"  X (P1 high)    : ₹{ep['x_price']:.2f} on {ep['x_date'].date() if ep.get('x_date') else '—'}")
    print(f"  Phase 2 start  : {ep['phase2_start'].date() if ep.get('phase2_start') else '—'}")
    print(f"  Y (P2 low)     : ₹{ep['y_price']:.2f} on {ep['y_date'].date() if ep.get('y_date') else '—'}")
    print(f"  Z (low 200 EMA): ₹{ep['z_price']:.2f} on {ep['z_date'].date() if ep.get('z_date') else '—'}")
    print(f"  Phase 3 start  : {ep['phase3_start'].date() if ep.get('phase3_start') else '—'}")
    print(f"  Signal date    : {ep['signal_date'].date() if ep.get('signal_date') else '—'}  (ST line >= 200 EMA)")
    print(f"  X cleared date : {ep['x_cleared_date'].date() if ep.get('x_cleared_date') else '—'}  (ST/price >= X)")
    print(f"  Status         : {ep['status']}")
    if ep['status'] == 'complete':
        for lvl in ('x', 'y', 'z'):
            print(f"  {lvl.upper()} retest: {ep.get(lvl+'_status','—')}  "
                  f"tested={ep.get(lvl+'_tested_date', '—')}  "
                  f"failed={ep.get(lvl+'_failed_date', '—')}  "
                  f"runup={ep.get(lvl+'_max_runup_pct', 0):.1f}%")
    print()

# Show all ST direction flips in full history for cross-check
print("=== All Supertrend direction flips in full history ===")
dirs = st_df["direction"]
flips = dirs != dirs.shift(1)
flips.iloc[0] = False  # first row is never a flip
for d in hist.index[flips]:
    new_dir = "BUY (+1)" if dirs.loc[d] == 1 else "SELL (-1)"
    print(f"  {d.date()}  → {new_dir}  ST={st_df.loc[d,'supertrend']:.2f}  EMA200={ema200.loc[d]:.2f}")
