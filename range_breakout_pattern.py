"""
range_breakout_pattern.py
--------------------------
Detects the "Monthly Pivot Range Breakout" (5-leg) pattern using daily
timeframe with monthly standard pivot points.

Pivot computation:
  P(M) = (High(M-1) + Low(M-1) + Close(M-1)) / 3
  P_applied(M) = P(M) — the pivot displayed/used during month M.

Legs are defined by month-over-month pivot direction:
  Up-leg   : P_applied(M) > P_applied(M-1) for consecutive months
  Down-leg : P_applied(M) < P_applied(M-1) for consecutive months

Pattern structure (5 legs):
  Leg 1 (Up)  : Initial uptrend. Leg1_High = max(daily High during Leg 1).
  Leg 2 (Down): Correction. Leg2_LowPivot = min(P_applied), Leg2_LowPrice = min(daily Low).
  Leg 3 (Up)  : Breakout attempt above Leg1_High.
  Leg 4 (Down): Retest of Leg2_Low area.
  Leg 5 (Up)  : Confirms breakout when P_applied closes above Leg1_High.

Signal appears once Leg 4 has completed its retest of Leg2 Low.
Buy levels: retests to Leg1_High or Leg2_LowPivot / Leg2_LowPrice.

Pattern variants:
  regular              — clean 5-leg breakout
  false_breakout_leg3  — Leg 3 went significantly (> retest_pct%) above Leg1_High
                         then reversed into Leg 4
  false_breakdown_leg4 — Leg 4 went significantly (> fail_pct%) below Leg2_Low
                         but recovered, then Leg 5 confirmed
  false_both           — both false breakout and false breakdown occurred

Only episodes where Leg 4 has touched the Leg2 Low retest zone are recorded.
"""
import pandas as pd
import numpy as np


def compute_monthly_pivot_table(hist: pd.DataFrame) -> pd.DataFrame:
    """
    Computes monthly H, L, C and the pivot P that applies to each month.
    P(M) = (H(M-1) + L(M-1) + C(M-1)) / 3  ->  displayed during month M.
    """
    df = hist.copy()
    df["_ym"] = df.index.to_period("M")
    monthly = df.groupby("_ym").agg(
        H=("High", "max"),
        L=("Low", "min"),
        C=("Close", "last"),
    )
    monthly["P"] = (monthly["H"] + monthly["L"] + monthly["C"]) / 3
    monthly["P_applied"] = monthly["P"].shift(1)
    return monthly


def _get_monthly_legs(monthly: pd.DataFrame) -> list:
    """
    Groups consecutive months with the same pivot direction into legs.
    Returns a list of dicts:
      {start_month, end_month, direction, month_periods}
    Flat months (P unchanged) break the sequence.
    """
    p = monthly["P_applied"].dropna()
    if len(p) < 2:
        return []

    # directions[i] = direction of month p.index[i+1] vs p.index[i]
    diffs = p.diff().iloc[1:]
    directions = np.where(diffs > 0, "up", np.where(diffs < 0, "down", "flat"))

    legs = []
    current_dir = None
    leg_start_idx = None

    for rel_idx, d in enumerate(directions):
        month_idx = rel_idx + 1  # p.index[month_idx] is the month being classified

        if d == "flat":
            if current_dir is not None:
                legs.append({
                    "start_month": p.index[leg_start_idx],
                    "end_month": p.index[month_idx - 1],
                    "direction": current_dir,
                    "month_periods": list(p.index[leg_start_idx:month_idx]),
                })
                current_dir = None
            continue

        if current_dir is None:
            current_dir = d
            leg_start_idx = month_idx
        elif d == current_dir:
            continue
        else:
            legs.append({
                "start_month": p.index[leg_start_idx],
                "end_month": p.index[month_idx - 1],
                "direction": current_dir,
                "month_periods": list(p.index[leg_start_idx:month_idx]),
            })
            current_dir = d
            leg_start_idx = month_idx

    # Close last open leg
    if current_dir is not None:
        legs.append({
            "start_month": p.index[leg_start_idx],
            "end_month": p.index[len(directions)],
            "direction": current_dir,
            "month_periods": list(p.index[leg_start_idx:len(directions) + 1]),
        })

    return legs


def _month_periods_to_days(df: pd.DataFrame, month_periods: list) -> pd.DataFrame:
    """Return daily rows whose _ym is in month_periods."""
    return df[df["_ym"].isin(month_periods)].copy()


def _p_values_for_months(monthly: pd.DataFrame, month_periods: list) -> np.ndarray:
    """Return P_applied values for the given month periods."""
    s = monthly["P_applied"]
    return s[s.index.isin(month_periods)].dropna().to_numpy()


def find_range_breakout_episodes(
    hist: pd.DataFrame,
    retest_pct: float = 5.0,
    fail_pct: float = 8.0,
) -> list:
    """
    Detects 5-leg range breakout episodes.

    Returns a list of episode dicts, oldest first, one per qualifying pattern.
    Only episodes where Leg 4 has touched the Leg2 Low retest zone are recorded.

    Each dict contains:
      leg1_start, leg1_end, leg2_start, leg2_end, leg3_start, leg3_end,
      leg4_start, leg4_end, leg5_start, leg5_end,
      leg1_high, leg2_low_pivot, leg2_low_price,
      leg3_max_pivot, leg4_min_pivot, leg4_min_low,
      leg5_last_pivot, leg5_max_pivot,
      status, pattern_type, breakout_confirmed, is_ongoing
    """
    monthly = compute_monthly_pivot_table(hist)
    legs = _get_monthly_legs(monthly)
    if len(legs) < 4:
        return []

    df = hist.copy()
    df["_ym"] = df.index.to_period("M")
    p_applied = monthly["P_applied"].dropna()
    last_month = p_applied.index[-1]

    episodes = []
    i = 0
    n = len(legs)

    while i <= n - 4:
        # We need at least 4 legs to evaluate a pattern (Leg 1-4).
        # Leg 5 is optional for in-progress patterns.
        if not (legs[i]["direction"] == "up" and
                legs[i + 1]["direction"] == "down" and
                legs[i + 2]["direction"] == "up" and
                legs[i + 3]["direction"] == "down"):
            i += 1
            continue

        leg1, leg2, leg3, leg4 = legs[i], legs[i + 1], legs[i + 2], legs[i + 3]
        leg5 = None

        # --- Compute key levels ---
        leg1_days = _month_periods_to_days(df, leg1["month_periods"])
        leg2_days = _month_periods_to_days(df, leg2["month_periods"])
        leg3_days = _month_periods_to_days(df, leg3["month_periods"])
        leg4_days = _month_periods_to_days(df, leg4["month_periods"])

        if any(d.empty for d in (leg1_days, leg2_days, leg3_days, leg4_days)):
            i += 1
            continue

        leg1_high = float(leg1_days["High"].max())

        leg2_p_vals = _p_values_for_months(monthly, leg2["month_periods"])
        leg2_low_pivot = float(leg2_p_vals.min()) if len(leg2_p_vals) else float("inf")
        leg2_low_price = float(leg2_days["Low"].min())

        # --- Leg 3 must break above Leg1_High ---
        leg3_p_vals = _p_values_for_months(monthly, leg3["month_periods"])
        leg3_max_pivot = float(leg3_p_vals.max()) if len(leg3_p_vals) else 0.0
        if leg3_max_pivot <= leg1_high:
            i += 1
            continue

        # --- Leg 4 must touch Leg2 Low retest zone ---
        leg4_p_vals = _p_values_for_months(monthly, leg4["month_periods"])
        leg4_min_pivot = float(leg4_p_vals.min()) if len(leg4_p_vals) else float("inf")
        leg4_min_low = float(leg4_days["Low"].min())

        leg4_touched_low = (
            (leg4_min_pivot <= leg2_low_pivot) or
            (leg4_min_low <= leg2_low_price)
        )

        if not leg4_touched_low:
            i += 1
            continue

        # --- Determine pattern type ---
        false_breakout = leg3_max_pivot > leg1_high * (1 + retest_pct / 100.0)
        false_breakdown = (
            (leg4_min_pivot < leg2_low_pivot * (1 - fail_pct / 100.0)) or
            (leg4_min_low < leg2_low_price * (1 - fail_pct / 100.0))
        )

        if false_breakout and false_breakdown:
            pattern_type = "false_both"
        elif false_breakout:
            pattern_type = "false_breakout_leg3"
        elif false_breakdown:
            pattern_type = "false_breakdown_leg4"
        else:
            pattern_type = "regular"

        # --- Leg 5 (optional) ---
        has_leg5 = (i + 4 < n) and (legs[i + 4]["direction"] == "up")
        leg5_days = pd.DataFrame()
        leg5_last_pivot = np.nan
        leg5_max_pivot = np.nan
        breakout_confirmed = False

        if has_leg5:
            leg5 = legs[i + 4]
            leg5_days = _month_periods_to_days(df, leg5["month_periods"])
            leg5_p_vals = _p_values_for_months(monthly, leg5["month_periods"])
            if len(leg5_p_vals):
                leg5_last_pivot = float(leg5_p_vals[-1])
                leg5_max_pivot = float(leg5_p_vals.max())
                breakout_confirmed = leg5_last_pivot > leg1_high

        # --- Status ---
        if breakout_confirmed:
            status = "leg5_completed"
        elif has_leg5:
            status = "leg5_progress"
        else:
            status = "leg4_retest_done"

        # --- Structural invalidation check ---
        # If current price has broken decisively below the range floor,
        # the pattern is dead regardless of pivot direction.
        # Use leg2_low_price (actual traded low) as the primary floor.
        range_floor = leg2_low_price
        last_close = float(hist["Close"].iloc[-1])
        last_low = float(hist["Low"].iloc[-1])

        # Invalidate if close is below the floor buffer, OR if both close AND low
        # are below (catches sustained breakdowns even if latest day bounced slightly)
        invalidated = (
            last_close < range_floor * (1 - fail_pct / 100.0) or
            (last_low < range_floor * (1 - fail_pct / 100.0) and
             last_close < range_floor * (1 - fail_pct / 200.0))
        )

        if invalidated:
            status = "invalidated"
            is_ongoing = False
            breakout_confirmed = False

        # Normal ongoing check (only if not invalidated)
        if status != "invalidated":
            is_ongoing = (
                (has_leg5 and leg5["end_month"] == last_month) or
                (not has_leg5 and leg4["end_month"] == last_month)
            )
        else:
            is_ongoing = False
        episodes.append({
            "leg1_start": leg1_days.index[0],
            "leg1_end": leg1_days.index[-1],
            "leg2_start": leg2_days.index[0],
            "leg2_end": leg2_days.index[-1],
            "leg3_start": leg3_days.index[0],
            "leg3_end": leg3_days.index[-1],
            "leg4_start": leg4_days.index[0],
            "leg4_end": leg4_days.index[-1],
            "leg5_start": leg5_days.index[0] if not leg5_days.empty else None,
            "leg5_end": leg5_days.index[-1] if not leg5_days.empty else None,
            "leg1_high": leg1_high,
            "leg2_low_pivot": leg2_low_pivot,
            "leg2_low_price": leg2_low_price,
            "leg3_max_pivot": leg3_max_pivot,
            "leg4_min_pivot": leg4_min_pivot,
            "leg4_min_low": leg4_min_low,
            "leg5_last_pivot": leg5_last_pivot,
            "leg5_max_pivot": leg5_max_pivot,
            "status": status,
            "pattern_type": pattern_type,
            "breakout_confirmed": breakout_confirmed,
            "is_ongoing": is_ongoing,
        })

        # Advance past this episode to avoid overlapping detections
        i += 5 if has_leg5 else 4

    return episodes
