"""
breakout_pullback_pattern.py
----------------------------
Detects the "Breakout-Pullback" 4-leg pattern using daily 20/50/200 EMA.

Leg 1: 20 EMA below 50 EMA, AND both below 200 EMA.
Leg 2: 20 EMA crosses above 50 EMA, BOTH stay below 200 EMA throughout.
        Track highest CLOSE during Leg 2 as X.
        INVALIDATION: if either 20 or 50 EMA crosses above 200 EMA during Leg 2,
        the whole setup resets — search for a new Leg 1 from the crossing point.
Leg 3: 20 EMA crosses below 50 EMA.
        Track lowest 50 EMA value as Y, lowest price (Low) as Z.
        INVALIDATION: if during Leg 3, both 20 and 50 EMA drop below Leg 1's
        lowest price (Low), the setup resets — search for a new Leg 1.
Leg 4: 20 EMA crosses above 50 EMA.
        SIGNAL fires when price CLOSES above X for the first time during Leg 4.
        If 20 EMA crosses back below 50 EMA during Leg 4 before price closes
        above X, whole setup resets — search for new Leg 1.

After signal fires:
  - Y (lowest 50 EMA in Leg 3) and Z (lowest Low in Leg 3) are buy-on-pullback
    levels, tracked independently.
  - Composite status:
      'signal_fired'     - price closed above X, neither Y nor Z yet tested.
      'partially_tested' - exactly one of Y or Z has been retested.
      'tested'           - both Y and Z have been retested.
      'failed'           - price dropped >fail_pct% below min(Y, Z).
  - Retest band: ±retest_pct% (default 5%) around each level, with a
    confirmed-move-away requirement (2×retest_pct) before a pullback counts.
  - Failure threshold: fail_pct% (default 8%) below min(Y, Z).
  - When a failure occurs, it takes priority over any retest regardless of order.
"""
import pandas as pd
import numpy as np


def find_breakout_pullback_episodes(
    hist, ema20, ema50, ema200,
    retest_pct: float = 5.0,
    fail_pct: float = 8.0,
):
    """
    Returns a list of episode dicts, oldest first.  Only episodes where the
    signal has fired (price closed above X in Leg 4) are stored.  Setups
    still in Leg 4 waiting for the close-above-X are silently skipped (they
    are not yet actionable).

    Each dict has:
      leg1_start, leg2_start, leg3_start, leg4_start, signal_date,
      x_price, y_price, z_price, leg1_low_price,
      status, y_retest_status, z_retest_status,
      y_tested_date, y_tested_price, z_tested_date, z_tested_price,
      failed_date, max_runup_pct, days_tracked,
      post_event_drawdown_pct, post_event_days_to_recover
    """
    dates      = hist.index
    n          = len(dates)
    if n == 0:
        return []

    close_arr  = hist["Close"].to_numpy()
    low_arr    = hist["Low"].to_numpy()
    high_arr   = hist["High"].to_numpy()
    ema20_arr  = ema20.to_numpy()
    ema50_arr  = ema50.to_numpy()
    ema200_arr = ema200.to_numpy()

    ema20_below_50  = ema20_arr < ema50_arr
    both_below_200  = (ema20_arr < ema200_arr) & (ema50_arr < ema200_arr)

    episodes = []
    i = 0

    while i < n:

        # ----------------------------------------------------------------
        # LEG 1: 20 EMA < 50 EMA AND both < 200 EMA
        # ----------------------------------------------------------------
        if not (ema20_below_50[i] and both_below_200[i]):
            i += 1
            continue

        leg1_start = i
        while i < n and ema20_below_50[i] and both_below_200[i]:
            i += 1

        if i >= n:
            break

        leg1_low_price = float(low_arr[leg1_start:i].min())

        # After Leg 1 the next bar must have 20 EMA >= 50 EMA (the crossover)
        # AND both still below 200 EMA to start a valid Leg 2.
        # If either condition fails, skip forward and look for a fresh Leg 1.
        if ema20_below_50[i] or not both_below_200[i]:
            # Still below-50 or 50 already crossed 200 — not a Leg 2 start.
            # Advance one bar so we don't infinite-loop on the same position.
            i += 1
            continue

        # ----------------------------------------------------------------
        # LEG 2: 20 EMA >= 50 EMA, BOTH below 200 EMA.
        # Invalidated the moment either EMA crosses above 200 EMA.
        # X = highest CLOSE during Leg 2.
        # ----------------------------------------------------------------
        leg2_start = i
        x_price    = float("-inf")
        leg2_invalidated = False

        while i < n and not ema20_below_50[i]:
            if not both_below_200[i]:
                # Either EMA crossed above 200 — Leg 2 is invalid.
                # Restart from this position (it may be a valid Leg 1 start later).
                leg2_invalidated = True
                break
            x_price = max(x_price, close_arr[i])
            i += 1

        if leg2_invalidated:
            # i is already at the invalidation point; outer loop will
            # re-evaluate it (it won't match Leg 1 conditions so will i+=1).
            continue

        if i >= n:
            break

        # ----------------------------------------------------------------
        # LEG 3: 20 EMA < 50 EMA (crossunder after Leg 2).
        # Y = lowest 50 EMA, Z = lowest Low.
        # Invalidated if both EMAs drop below Leg 1's lowest Low.
        # ----------------------------------------------------------------
        if not ema20_below_50[i]:
            # Leg 2 ended for an unexpected reason — restart.
            continue

        leg3_start     = i
        y_price        = float("inf")
        z_price        = float("inf")
        leg3_invalidated = False

        while i < n and ema20_below_50[i]:
            y_price = min(y_price, ema50_arr[i])
            z_price = min(z_price, low_arr[i])
            if ema20_arr[i] < leg1_low_price and ema50_arr[i] < leg1_low_price:
                leg3_invalidated = True
                i += 1   # advance past the invalidation bar before restarting
                break
            i += 1

        if leg3_invalidated:
            # Restart Leg 1 search from current i.
            continue

        if i >= n:
            break

        # ----------------------------------------------------------------
        # LEG 4: 20 EMA >= 50 EMA (crossover after Leg 3).
        # Signal fires on the first close > X.
        # If 20 EMA crosses back below 50 EMA before signal: reset.
        # ----------------------------------------------------------------
        # Invariant: Leg 3's while-loop exits only when ema20_below_50[i] is
        # False (or i >= n, handled above) — so we are guaranteed to be at
        # the 20-above-50 crossover that starts Leg 4.  No guard needed here;
        # the previous `if i >= n: break` already covers the other exit path.

        leg4_start   = i
        signal_fired = False
        signal_date  = None

        while i < n and not ema20_below_50[i]:
            if close_arr[i] > x_price:
                signal_fired = True
                signal_date  = dates[i]
                i += 1     # advance past signal bar
                break
            i += 1

        if not signal_fired:
            # Leg 4 ended without close > X (20 EMA crossed back below 50).
            # Whole setup resets — the outer loop re-evaluates from current i.
            continue

        # ----------------------------------------------------------------
        # SIGNAL FIRED — track Y and Z independently going forward.
        # ----------------------------------------------------------------
        result = _track_yz_levels(
            dates, close_arr, low_arr, high_arr,
            float(y_price), float(z_price), signal_date,
            retest_pct, fail_pct,
            x_price=float(x_price),
        )

        # x_vs_200ema_pct: how far X (the Leg-2 breakout close) sits below the
        # 200 EMA at signal time, as a %.  Since Legs 1–3 require both EMAs to
        # stay below the 200 EMA, X is always below it at signal; the gap tells
        # you how much room exists before hitting that structural resistance.
        # A 2% gap vs a 15% gap are very different risk profiles for the same
        # pattern.  Negative value = X below 200 EMA (expected); positive =
        # X above 200 EMA (would be unusual / post-breakout lagged signal).
        signal_pos = dates.get_loc(signal_date)
        ema200_at_signal = float(ema200_arr[signal_pos])
        x_vs_200ema_pct = round((float(x_price) - ema200_at_signal) / ema200_at_signal * 100, 2)

        episodes.append({
            "leg1_start":      dates[leg1_start],
            "leg2_start":      dates[leg2_start],
            "leg3_start":      dates[leg3_start],
            "leg4_start":      dates[leg4_start],
            "signal_date":     signal_date,
            "x_price":         float(x_price),
            "x_vs_200ema_pct": x_vs_200ema_pct,
            "y_price":       float(y_price),
            "z_price":       float(z_price),
            "leg1_low_price": leg1_low_price,
            **result,
        })
        # i is already advanced past signal_date; outer loop continues.

    return episodes


# ---------------------------------------------------------------------------
# Level tracker — Y and Z tracked independently, then combined
# ---------------------------------------------------------------------------

def _track_yz_levels(
    dates, close_arr, low_arr, high_arr,
    y_price: float, z_price: float,
    anchor_date,
    retest_pct: float,
    fail_pct: float,
    x_price: float = None,
) -> dict:
    """
    Track both Y and Z independently from anchor_date (signal_date + 1 bar)
    through the full remaining history.

    Key design decisions:
      - Y and Z are tracked to their final state at the END of history, not
        just to the first event. This means "tested" (both retested) is
        correctly reachable even when Y and Z retested on different days.
      - Failure is checked against min(Y, Z) and takes priority: once failed,
        status = 'failed' regardless of whether either level was retested
        before the failure.
      - max_runup_pct is capped at the failure date if a failure occurred,
        so it reflects the largest gain the position could have achieved
        before getting stopped out — not an inflated post-failure figure.
      - post_event_drawdown is measured from the first retest event (or
        failure event, whichever comes first) to be consistent with the
        other setups in the dashboard.
    """
    # Find anchor position (signal_date is guaranteed to be in dates).
    anchor_pos = dates.get_loc(anchor_date)
    start_pos  = anchor_pos + 1   # start tracking from the bar AFTER signal

    if start_pos >= len(dates):
        return _empty_result(anchor_date, dates[-1])

    after_dates  = dates[start_pos:]
    after_lows   = low_arr[start_pos:]
    after_highs  = high_arr[start_pos:]
    after_closes = close_arr[start_pos:]
    m = len(after_dates)

    if m == 0:
        return _empty_result(anchor_date, dates[-1])

    lower_level   = min(y_price, z_price)
    fail_thresh   = lower_level * (1 - fail_pct  / 100.0)

    # --- Failure ---
    fail_mask = after_lows <= fail_thresh
    fail_pos  = int(np.argmax(fail_mask)) if fail_mask.any() else None

    # --- Y retest (requires confirmed move away first) ---
    y_retest_pos = _find_retest_pos(after_highs, after_lows, y_price, retest_pct)
    # --- Z retest ---
    z_retest_pos = _find_retest_pos(after_highs, after_lows, z_price, retest_pct)

    # Failure takes absolute priority — anything that happened AFTER the
    # failure date is irrelevant.
    effective_end = fail_pos if fail_pos is not None else m - 1

    # Apply the failure cut-off to retest positions.
    y_effective = y_retest_pos if (y_retest_pos is not None and y_retest_pos <= effective_end) else None
    z_effective = z_retest_pos if (z_retest_pos is not None and z_retest_pos <= effective_end) else None

    # Max run-up: from X (highest Close in Leg 2, the breakout level) to the
    # highest High reached from the signal bar onward through the effective end
    # of tracking.  We deliberately seed peak_high with the signal bar's own
    # High (not its Close) because the intraday range of the breakout bar is
    # genuinely achievable and should be included in the run-up measurement.
    # This means max_runup_pct can be slightly positive even on the signal bar
    # itself when High > Close — that is correct and intentional.
    base          = x_price if x_price is not None else lower_level
    signal_high_v = high_arr[anchor_pos]
    after_window  = after_highs[:effective_end + 1]
    peak_high     = max(signal_high_v, float(after_window.max()) if len(after_window) else signal_high_v)
    max_runup_pct = round(float((peak_high - base) / base * 100), 2)

    days_tracked = int((after_dates[effective_end] - anchor_date).days)

    # --- Composite status ---
    if fail_pos is not None and fail_pos <= effective_end:
        status     = "failed"
        failed_date = after_dates[fail_pos]
        # Post-event drawdown from failure point
        dd = _compute_post_drawdown(after_lows, after_closes, after_dates, lower_level, fail_pos)
        y_status = "naked"
        z_status = "naked"
        y_td = y_tp = z_td = z_tp = None
        # Overwrite with any retests that happened BEFORE failure
        if y_effective is not None:
            y_status = "tested"
            y_td     = after_dates[y_effective]
            y_tp     = float(after_closes[y_effective])
        if z_effective is not None:
            z_status = "tested"
            z_td     = after_dates[z_effective]
            z_tp     = float(after_closes[z_effective])
    else:
        failed_date = None
        dd          = {"max_drawdown_pct": None, "days_to_recover": None}

        y_status = "naked"
        z_status = "naked"
        y_td = y_tp = z_td = z_tp = None

        if y_effective is not None:
            y_status = "tested"
            y_td     = after_dates[y_effective]
            y_tp     = float(after_closes[y_effective])
        if z_effective is not None:
            z_status = "tested"
            z_td     = after_dates[z_effective]
            z_tp     = float(after_closes[z_effective])

        if y_status == "tested" and z_status == "tested":
            status = "tested"
            # Post-event drawdown from the earlier of the two retest dates
            first_retest = min(y_effective, z_effective)
            dd = _compute_post_drawdown(after_lows, after_closes, after_dates, lower_level, first_retest)
        elif y_status == "tested" or z_status == "tested":
            status = "partially_tested"
            retest_p = y_effective if y_effective is not None else z_effective
            dd = _compute_post_drawdown(after_lows, after_closes, after_dates, lower_level, retest_p)
        else:
            status = "signal_fired"

    return {
        "status":                     status,
        "y_retest_status":            y_status,
        "z_retest_status":            z_status,
        "y_tested_date":              y_td,
        "y_tested_price":             y_tp,
        "z_tested_date":              z_td,
        "z_tested_price":             z_tp,
        "failed_date":                failed_date,
        "max_runup_pct":              max_runup_pct,
        "days_tracked":               days_tracked,
        "post_event_drawdown_pct":    dd["max_drawdown_pct"],
        "post_event_days_to_recover": dd["days_to_recover"],
    }


def _find_retest_pos(highs, lows, level: float, retest_pct: float):
    """
    Returns the position (integer index into highs/lows arrays) of the first
    genuine retest of `level`, or None.

    A genuine retest requires:
      1. Price first moves clearly AWAY above the level by 2×retest_pct%.
      2. Price then pulls back to within retest_pct% of the level (i.e.
         Low <= level × (1 + retest_pct/100)).

    This two-step requirement prevents a level that is still nearby at the
    signal date from immediately registering as "retested".
    """
    confirm_away   = level * (1 + 2 * retest_pct / 100.0)
    retest_thresh  = level * (1 + retest_pct  / 100.0)

    away_mask = highs >= confirm_away
    if not away_mask.any():
        return None

    first_away = int(np.argmax(away_mask))
    if first_away + 1 >= len(lows):
        return None

    sub_lows = lows[first_away + 1:]
    back_mask = sub_lows <= retest_thresh
    if not back_mask.any():
        return None

    return first_away + 1 + int(np.argmax(back_mask))


def _compute_post_drawdown(lows, closes, dates, level: float, event_pos: int) -> dict:
    """
    Measures how far below `level` price dipped after `event_pos`, and how
    long recovery (first close >= level) took.
    """
    w_lows   = lows[event_pos:]
    w_closes = closes[event_pos:]
    w_dates  = dates[event_pos:]

    if len(w_lows) == 0:
        return {"max_drawdown_pct": None, "days_to_recover": None}

    breach_mask = w_lows < level
    if not breach_mask.any():
        return {"max_drawdown_pct": 0.0, "days_to_recover": 0}

    first_breach = int(np.argmax(breach_mask))
    sub_closes   = w_closes[first_breach:]
    sub_dates    = w_dates[first_breach:]

    recovery_mask = sub_closes >= level
    if recovery_mask.any():
        rec_rel    = int(np.argmax(recovery_mask))
        rec_date   = sub_dates[rec_rel]
        window_low = float(w_lows[:first_breach + rec_rel + 1].min())
        days_to_recover = int((rec_date - w_dates[0]).days)
    else:
        window_low      = float(w_lows.min())
        days_to_recover = None

    max_dd = max(0.0, (level - window_low) / level * 100)
    return {"max_drawdown_pct": round(max_dd, 2), "days_to_recover": days_to_recover}


def _empty_result(anchor_date, last_date) -> dict:
    days = int((last_date - anchor_date).days)
    return {
        "status":                     "signal_fired",
        "y_retest_status":            "naked",
        "z_retest_status":            "naked",
        "y_tested_date":              None,
        "y_tested_price":             None,
        "z_tested_date":              None,
        "z_tested_price":             None,
        "failed_date":                None,
        "max_runup_pct":              0.0,
        "days_tracked":               days,
        "post_event_drawdown_pct":    None,
        "post_event_days_to_recover": None,
    }
