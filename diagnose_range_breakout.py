"""
diagnose_range_breakout.py
--------------------------
Standalone diagnostic. Run: python diagnose_range_breakout.py RELIANCE.NS
"""
import yfinance as yf
from range_breakout_pattern import find_range_breakout_episodes, compute_monthly_pivot_table, _get_monthly_legs


def diagnose(symbol="RELIANCE.NS", period="5y"):
    print(f"\n{'='*60}")
    print(f"Diagnosing Range Breakout for {symbol}")
    print(f"{'='*60}")

    print(f"\nFetching {period} of daily data...")
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=period)
    if hist.empty or len(hist) < 220:
        print("ERROR: Insufficient data!")
        return

    print(f"  Rows: {len(hist)} | {hist.index[0].date()} to {hist.index[-1].date()}")

    monthly = compute_monthly_pivot_table(hist)
    p = monthly["P_applied"].dropna()
    print(f"\nMonthly pivots: {len(p)} months")
    print(f"  First: {p.iloc[0]:.2f} at {p.index[0]}")
    print(f"  Last:  {p.iloc[-1]:.2f} at {p.index[-1]}")

    legs = _get_monthly_legs(monthly)
    print(f"\nLegs found: {len(legs)}")
    for i, leg in enumerate(legs):
        print(f"  Leg {i+1}: {leg['direction']:>4} | {leg['start_month']} to {leg['end_month']} | {len(leg['month_periods'])} mo")

    print(f"\nScanning with retest_pct=5.0, fail_pct=8.0...")
    eps = find_range_breakout_episodes(hist, retest_pct=5.0, fail_pct=8.0)
    print(f"  EPISODES FOUND: {len(eps)}")

    for i, ep in enumerate(eps):
        print(f"\n  Episode {i+1}: {ep['status']} | {ep['pattern_type']}")
        print(f"    Leg1 High={ep['leg1_high']:.2f}  Leg2 Low Pivot={ep['leg2_low_pivot']:.2f}  Leg2 Low Price={ep['leg2_low_price']:.2f}")
        print(f"    Leg3 Max Pivot={ep['leg3_max_pivot']:.2f}  Leg4 Min Low={ep['leg4_min_low']:.2f}")
        if ep['leg5_start']:
            print(f"    Leg5 Last Pivot={ep['leg5_last_pivot']:.2f}  Breakout={ep['breakout_confirmed']}")

    if not eps:
        print("\n  No episodes. Trying looser retest_pct=10.0...")
        eps2 = find_range_breakout_episodes(hist, retest_pct=10.0, fail_pct=8.0)
        print(f"  With 10% retest: {len(eps2)} episodes")

        print("\n  Checking leg sequence for up-down-up-down...")
        seq = [l['direction'] for l in legs]
        for i in range(len(seq)-3):
            if seq[i:i+4] == ['up','down','up','down']:
                print(f"    Found up-down-up-down at legs {i+1}-{i+4}")
                l1 = legs[i]
                l2 = legs[i+1]
                l3 = legs[i+2]
                l4 = legs[i+3]
                # Reconstruct levels
                df = hist.copy(); df["_ym"] = df.index.to_period("M")
                l1d = df[df["_ym"].isin(l1["month_periods"])]
                l2d = df[df["_ym"].isin(l2["month_periods"])]
                l3p = monthly["P_applied"][monthly.index.isin(l3["month_periods"])].max()
                l1h = l1d["High"].max()
                l2lp = monthly["P_applied"][monthly.index.isin(l2["month_periods"])].min()
                l4ml = l2d["Low"].min()
                print(f"      Leg1 High={l1h:.2f}  Leg3 Max Pivot={l3p:.2f}  Breaks? {l3p > l1h}")
                print(f"      Leg2 Low Pivot={l2lp:.2f}  Leg4 Min Low={l4ml:.2f}  Retest? {l4ml <= l2lp*1.05:.2f}")


if __name__ == "__main__":
    import sys
    diagnose(sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS")