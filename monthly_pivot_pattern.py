"""
monthly_pivot_pattern.py
--------------------------
Detects the "Monthly Pivot S1 above 200 EMA" reversal-support pattern.

Mechanics (see README for the full narrative):

  Step 0 - candidate start: the first day the current month's Standard
  Pivot S1 (computed from the PRIOR completed calendar month's High/Low/
  Close, held constant through the current month) is above the daily
  200 EMA.

  Step 1 - qualify (first `min_qualify_months` calendar months only): S1
  must stay above the 200 EMA AND price must not touch either level
  (Low <= S1 or Low <= 200 EMA counts as a touch - this also catches a
  close-through, since a close below a level means the low was too) for
  the whole window. Any violation discards this candidate; the search
  resumes looking for the next candidate start after the violation.

  Step 2 - once qualified, track the running high (candidate X). S1's
  relationship to the 200 EMA no longer matters from here on (it can drop
  back below the 200 EMA on its own without invalidating anything) - we
  are only watching for a touch of S1 (or the 200 EMA, which invalidates).

  Step 3 - the first day price touches S1, X is fixed at the running high
  up to that point. From here we track the running low (candidate Y) and
  wait for the 200 EMA to cross above X.

  Step 4 - if price touches the 200 EMA at ANY point between the candidate
  start and the 200 EMA finally crossing above X, the whole episode is
  discarded and the search resumes after that touch.

  Step 5 - if the 200 EMA crosses above X without that happening, Y locks
  in as the lowest low reached between the X-fix date and the crossing
  date. Episode complete - X and Y are both buy-on-pullback levels.

Only episodes that have passed Step 1 are recorded/returned; the raw
2-month qualifying window itself is too preliminary to be a useful signal.
"""
import pandas as pd
import numpy as np


def compute_monthly_pivots(hist: pd.DataFrame) -> pd.DataFrame:
    """
    Computes the standard monthly pivot P/R1/S1 for every day in `hist`,
    using the PRIOR completed calendar month's High/Low/Close, held constant
    through the current month (a step function that updates once at the
    start of each new month). The first calendar month present in `hist`
    has no prior month to compute from, so its P/R1/S1 will be NaN.
    """
    df = hist.copy()
    df["_ym"] = df.index.to_period("M")
    monthly = df.groupby("_ym").agg(H=("High", "max"), L=("Low", "min"), C=("Close", "last"))
    monthly["P"] = (monthly["H"] + monthly["L"] + monthly["C"]) / 3
    monthly["R1"] = 2 * monthly["P"] - monthly["L"]
    monthly["S1"] = 2 * monthly["P"] - monthly["H"]
    monthly_shifted = monthly[["P", "R1", "S1"]].shift(1)
    result = df[["_ym"]].join(monthly_shifted, on="_ym")
    return result[["P", "R1", "S1"]]


def find_pivot_episodes(hist, s1_series, ema200_series, min_qualify_months: int = 2):
    """
    Returns a list of episode dicts, oldest first, one per qualifying setup.
    Each dict has:
      episode_start, qualify_end_date, x_price, x_fix_date,
      y_price, y_fix_date, status
    status is one of:
      'tracking_x'            - qualified, watching the running high,
                                 S1 not yet touched (X not fixed).
      'x_fixed_pending_cross' - X fixed, watching the running low (Y),
                                 waiting for the 200 EMA to cross above X.
      'complete'              - X and Y both fixed.

    Performance note: this operates on plain numpy arrays (converted once up
    front) rather than repeated pandas .loc[] scalar lookups inside the
    day-by-day loops - the same logic, but roughly an order of magnitude
    faster at realistic history lengths (thousands of days x 100+ stocks
    adds up fast with per-call pandas overhead).
    """
    dates = hist.index
    n = len(dates)
    if n == 0:
        return []

    high_arr = hist["High"].to_numpy()
    low_arr = hist["Low"].to_numpy()
    s1_arr = s1_series.to_numpy()
    ema_arr = ema200_series.to_numpy()
    s1_notna = ~pd.isna(s1_arr)

    # Precompute, for each index, the array position that is >= 1 calendar
    # month*min_qualify_months ahead - avoids repeated Timestamp arithmetic
    # inside the hot loop.
    qualify_target_dates = dates + pd.DateOffset(months=min_qualify_months)
    qualify_target_pos = dates.searchsorted(qualify_target_dates)

    episodes = []
    i = 0

    while i < n:
        if not s1_notna[i] or not (s1_arr[i] > ema_arr[i]):
            i += 1
            continue

        episode_start = dates[i]
        qualify_target_j = qualify_target_pos[i]
        j = i
        qualified = False
        failed_at = None
        while j < n:
            if j >= qualify_target_j:
                qualified = True
                break
            if not s1_notna[j] or not (s1_arr[j] > ema_arr[j]) or low_arr[j] <= s1_arr[j] or low_arr[j] <= ema_arr[j]:
                failed_at = j
                break
            j += 1
        else:
            break  # ran out of data before qualifying or failing

        if not qualified:
            i = failed_at + 1
            continue

        qualify_end_date = dates[j - 1] if j > i else episode_start

        # Phase 2: track running high (X candidate); watch for S1 touch
        # (fixes X) or 200 EMA touch (invalidates).
        k = j
        running_high = float(high_arr[i:j].max()) if j > i else None
        x_price = None
        x_fix_date = None
        x_fix_pos = None
        invalidated = False
        while k < n:
            highk = high_arr[k]
            running_high = highk if running_high is None else max(running_high, highk)

            if low_arr[k] <= ema_arr[k]:
                invalidated = True
                break
            if s1_notna[k] and low_arr[k] <= s1_arr[k]:
                x_price = running_high
                x_fix_date = dates[k]
                x_fix_pos = k
                break
            k += 1
        else:
            episodes.append({
                "episode_start": episode_start, "qualify_end_date": qualify_end_date,
                "x_price": running_high, "x_fix_date": None,
                "y_price": None, "y_fix_date": None, "status": "tracking_x",
            })
            break

        if invalidated:
            i = k + 1
            continue

        # Phase 3: X fixed - track running low (Y candidate); watch for the
        # 200 EMA crossing above X (completes) or touching price (invalidates).
        m = x_fix_pos + 1
        running_low = float(low_arr[x_fix_pos])
        y_price = None
        y_fix_date = None
        invalidated2 = False
        while m < n:
            lowm = low_arr[m]
            running_low = min(running_low, lowm)

            if lowm <= ema_arr[m]:
                invalidated2 = True
                break
            if ema_arr[m] >= x_price:
                y_price = running_low
                y_fix_date = dates[m]
                break
            m += 1
        else:
            episodes.append({
                "episode_start": episode_start, "qualify_end_date": qualify_end_date,
                "x_price": x_price, "x_fix_date": x_fix_date,
                "y_price": running_low, "y_fix_date": None, "status": "x_fixed_pending_cross",
            })
            break

        if invalidated2:
            i = m + 1
            continue

        episodes.append({
            "episode_start": episode_start, "qualify_end_date": qualify_end_date,
            "x_price": x_price, "x_fix_date": x_fix_date,
            "y_price": y_price, "y_fix_date": y_fix_date, "status": "complete",
        })
        i = m + 1

    return episodes


def classify_retest(hist: pd.DataFrame, level: float, since_date, retest_pct: float = 5.0):
    """
    Standalone utility: classifies whether `level` has been genuinely retested
    (price pulled away from it and came back within retest_pct%) since
    `since_date`.  Uses the same confirmed-move-away-then-return logic as all
    other strategies to avoid false positives from a level that is still close
    at the start of the window.

    NOTE: this function is NOT called from within find_pivot_episodes — that
    function manages its own phase-based retest tracking internally.  This
    utility exists for external callers (e.g. appclaude.py or a future
    backtester) that need a one-off retest classification for a Monthly Pivot
    S1 level after the fact.

    Returns (status, tested_date, tested_price) where status is 'naked' or
    'tested'.
    """
    if since_date is None or pd.isna(level):
        return "naked", None, None
    after = hist.loc[hist.index > since_date]
    if after.empty:
        return "naked", None, None

    threshold = level * (1 + retest_pct / 100.0)  # retest = price coming back down near/at level
    # Confirm price first moved clearly AWAY (above) the level by more than
    # the retest band, then look for it coming back down within the band.
    confirm_above = level * (1 + 2 * retest_pct / 100.0)
    ran_up_mask = after["High"] >= confirm_above
    if not ran_up_mask.any():
        return "naked", None, None
    first_run_up = ran_up_mask[ran_up_mask].index[0]
    after_run = hist.loc[hist.index > first_run_up, "Low"]
    retest_mask = after_run <= threshold
    if retest_mask.any():
        tested_date = retest_mask[retest_mask].index[0]
        tested_price = float(hist.loc[tested_date, "Close"])
        return "tested", tested_date, tested_price
    return "naked", None, None


def compute_post_event_drawdown(hist: pd.DataFrame, level: float, event_date):
    """
    Given a support `level` and the date a test/retest/failure event was
    first detected, measures how much FURTHER price dropped below the level
    after that point, and whether/when it recovered back above it. Shared
    across all four pattern systems - the streak-based ledger, 5-Leg, the
    Monthly Pivot S1 setup, and the Monthly S1 Shift Up setup - wherever a
    "tested"/"failed" event is recorded but we don't yet know how deep the
    dip actually went, or how long recovery took.

    Important: a "retest" event is usually detected once price merely comes
    WITHIN a band near the level (e.g. level*(1+retest_pct%)) - not
    necessarily below the level itself. So price can easily still be
    trading ABOVE the level on event_date. A naive "first close >= level"
    search starting at event_date would then trivially "succeed" on day
    one, completely missing a genuine deeper dip that happens afterward.
    To avoid that, this function first checks whether price ever actually
    BREACHES below `level` at all after event_date:
      - If it never does, the level simply held - drawdown is 0% and
        recovery is immediate (event_date itself).
      - If it does breach, recovery is defined as the first close back at
        or above `level` AFTER that breach began, and the drawdown is the
        lowest Low reached between event_date and that recovery date.

    Returns a dict:
      max_drawdown_pct   - how far below `level` price dipped (negative %),
                           or 0.0 if it never went below at all.
      lowest_price        - the actual lowest price reached in the window.
      lowest_date         - the date of that low.
      recovered           - bool, whether a close back >= level has happened.
      recovery_date        - date of that recovery, or None.
      days_to_recover      - days from event_date to recovery_date, or None
                            if not yet recovered.
    """
    empty_result = {
        "max_drawdown_pct": None, "lowest_price": None, "lowest_date": None,
        "recovered": None, "recovery_date": None, "days_to_recover": None,
    }
    if event_date is None or level is None or pd.isna(level):
        return empty_result

    after = hist.loc[hist.index >= event_date]
    if after.empty:
        return empty_result

    lows = after["Low"].to_numpy()
    closes = after["Close"].to_numpy()
    dates = after.index

    breach_mask = lows < level
    if not breach_mask.any():
        # Level held throughout - never actually traded below it.
        return {
            "max_drawdown_pct": 0.0, "lowest_price": float(lows.min()),
            "lowest_date": dates[int(np.argmin(lows))],
            "recovered": True, "recovery_date": event_date, "days_to_recover": 0,
        }

    first_breach_pos = int(np.argmax(breach_mask))
    after_breach_closes = closes[first_breach_pos:]
    after_breach_dates = dates[first_breach_pos:]
    recovery_mask = after_breach_closes >= level

    if recovery_mask.any():
        recovery_rel_pos = int(np.argmax(recovery_mask))
        recovery_date = after_breach_dates[recovery_rel_pos]
        days_to_recover = int((recovery_date - event_date).days)
        recovered = True
        window_lows = lows[: first_breach_pos + recovery_rel_pos + 1]
        window_dates = dates[: first_breach_pos + recovery_rel_pos + 1]
    else:
        recovery_date = None
        days_to_recover = None
        recovered = False
        window_lows = lows
        window_dates = dates

    lowest_pos = int(np.argmin(window_lows))
    lowest_price = float(window_lows[lowest_pos])
    lowest_date = window_dates[lowest_pos]
    max_drawdown_pct = -max(0.0, (level - lowest_price) / level * 100)

    return {
        "max_drawdown_pct": max_drawdown_pct,
        "lowest_price": lowest_price,
        "lowest_date": lowest_date,
        "recovered": recovered,
        "recovery_date": recovery_date,
        "days_to_recover": days_to_recover,
    }
