"""
diagnose_five_leg.py
----------------------
Run this directly in your project folder (same place as app.py) to see
EXACTLY what legs the 5-leg pattern detector is finding for a given stock,
bypassing Streamlit and its caching entirely.

Usage:
    python diagnose_five_leg.py DLF
    python diagnose_five_leg.py DLF 10      (use min_leg_days=10 instead of 5)
"""
import sys
import pandas as pd

import price_cache
from five_leg_pattern import _get_legs, find_five_leg_episodes

symbol = sys.argv[1] if len(sys.argv) > 1 else "DLF"
min_leg_days = int(sys.argv[2]) if len(sys.argv) > 2 else 5

hist = price_cache.get_full_history(symbol)
hist = hist.dropna(subset=["Close"])
close = hist["Close"]

ema20 = close.ewm(span=20, adjust=False).mean()
ema50 = close.ewm(span=50, adjust=False).mean()
ema200 = close.ewm(span=200, adjust=False).mean()

print(f"=== {symbol} | min_leg_days={min_leg_days} | {len(hist)} bars, "
      f"{hist.index.min().date()} to {hist.index.max().date()} ===\n")

legs = _get_legs(hist, ema20, ema50, min_leg_days)
print(f"{'#':<3} {'Start':<12} {'End':<12} {'Dir':<5} {'Days':<5} "
      f"{'EMA20 ext':<10} {'EMA50 ext':<10} {'Price ext':<10}")
for idx, leg in enumerate(legs):
    days = (leg["end"] - leg["start"]).days
    print(f"{idx:<3} {leg['start'].date()!s:<12} {leg['end'].date()!s:<12} "
          f"{leg['direction']:<5} {days:<5} "
          f"{leg['ema20_extreme']:<10.2f} {leg['ema50_extreme']:<10.2f} {leg['price_extreme']:<10.2f}")

print(f"\nTotal legs found: {len(legs)}\n")

episodes = find_five_leg_episodes(hist, ema20, ema50, ema200, min_leg_days=min_leg_days)
print(f"Episodes detected: {len(episodes)}")
for ep in episodes:
    print(ep)

# --- Extra diagnostic: for each episode, show the leg immediately BEFORE
# leg 1 (the "prior leg" used by the invalidation rule), leg 2 itself, and
# whether leg 2 should have violated the prior high - so we can directly
# verify the invalidation rule fired (or didn't) with real numbers instead
# of guessing from a screenshot.
print("\n=== Invalidation-rule check for each episode ===")
leg_starts = [leg["start"] for leg in legs]
for ep in episodes:
    leg1_start = ep["leg1_start"]
    try:
        leg1_pos = leg_starts.index(leg1_start)
    except ValueError:
        print(f"Could not locate leg1 at {leg1_start.date()} in legs list - skipping")
        continue
    leg1 = legs[leg1_pos]
    leg2 = legs[leg1_pos + 1] if leg1_pos + 1 < len(legs) else None
    prior_leg = legs[leg1_pos - 1] if leg1_pos > 0 else None

    print(f"\nEpisode leg1_start={leg1_start.date()}  (legs[{leg1_pos}])")
    if prior_leg is None:
        print("  No prior leg exists (leg1 is the very first leg in history) - invalidation rule skipped.")
    else:
        print(f"  Prior leg:  {prior_leg['start'].date()} to {prior_leg['end'].date()} | "
              f"{prior_leg['direction']} | ema_extreme={prior_leg['ema_extreme']:.2f} | "
              f"price_extreme={prior_leg['price_extreme']:.2f}")
        if leg2 is not None:
            print(f"  Leg 2:      {leg2['start'].date()} to {leg2['end'].date()} | "
                  f"{leg2['direction']} | ema_extreme={leg2['ema_extreme']:.2f} | "
                  f"price_extreme={leg2['price_extreme']:.2f}")
            violates_ema = leg2["ema_extreme"] > prior_leg["ema_extreme"]
            violates_price = leg2["price_extreme"] > prior_leg["price_extreme"]
            print(f"  Leg2 ema_extreme > prior ema_extreme?   {leg2['ema_extreme']:.2f} > "
                  f"{prior_leg['ema_extreme']:.2f}  -> {violates_ema}")
            print(f"  Leg2 price_extreme > prior price_extreme? {leg2['price_extreme']:.2f} > "
                  f"{prior_leg['price_extreme']:.2f}  -> {violates_price}")
            print(f"  Should this have been INVALIDATED? {violates_ema or violates_price}")
