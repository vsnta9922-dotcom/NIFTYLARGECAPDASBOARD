"""
ema_pullback_pattern.py
------------------------
Detects the "EMA Pullback Reentry" pattern using the daily 20/50 EMA pair.

Pattern logic (all on the DAILY timeframe, 20 EMA vs 50 EMA only):

  PHASE 1 — QUALIFICATION (50 trading days minimum):
    • The 20 EMA crosses ABOVE the 50 EMA (golden cross).
    • From that crossover date, the 20 EMA must remain continuously above the
      50 EMA for at least `min_qualify_days` trading days (default 50).
    • During those same days, price's INTRADAY LOW must NEVER touch or go below
      the 50 EMA value for that day (Low > EMA50 on every day).
    • If either condition is violated before min_qualify_days pass, this
      candidate start is discarded and the search resumes after the violation.

  PHASE 2 — X LOCK-IN (running highest High):
    • While qualification is running we track the running highest High from the
      crossover date inclusive.
    • On the FIRST day after qualification where Low <= EMA50 (the "touch"),
      X = max(High) from crossover date through AND INCLUDING that touch day.
    • The touch day itself is also called "touch_date" (the pullback day).

  PHASE 3 — Y TRACKING (lowest Low until 50 EMA reclaims X):
    • From touch_date onward, track the running lowest Low (Y candidate).
    • INVALIDATION: if the 20 EMA crosses BELOW the 50 EMA at any point during
      this phase, the entire episode is discarded and the search resumes.
    • COMPLETION: the first day EMA50 >= X (50 EMA crossing above X level)
      locks in Y = lowest Low from touch_date through that completion day.
    • If we exhaust all history without a completion or invalidation, the episode
      is stored as "signal_pending" (still waiting for 50 EMA to reach X).

  STATUS VALUES:
    "signal_pending"  — qualified and touched, tracking Y; 50 EMA hasn't
                         crossed X yet. Y is provisional/updating.
    "signal_fired"    — 50 EMA has crossed above X. Y is locked in.
                         Pullback to Y is the buy entry.

  POST-SIGNAL STATUS (after signal_fired, same as other strategies):
    "naked"    — Y never retested (Y - retest_pct% band not reached after
                  price moved clearly away).
    "tested"   — Y was retested (price ran clearly away then pulled back to
                  within retest_pct% of Y). Buy entry triggered.
    "failed"   — Price dropped >= fail_pct% below Y. Support broken.

  EPISODE DICT KEYS:
    crossover_date      — date 20 EMA crossed above 50 EMA
    qualify_end_date    — last day of the 50-day clean window (day 50 of
                           continuous 20>50 without Low touching EMA50)
    touch_date          — first day Low <= EMA50 after qualification
    x_price             — highest High from crossover through touch_date (inclusive)
    y_price             — lowest Low from touch_date through completion (or "now")
    y_fix_date          — date Y was locked in (50 EMA >= X), or None
    status              — "signal_pending" | "signal_fired" | "naked" | "tested" | "failed"
    tested_date         — date Y was retested, or None
    tested_price        — close on tested_date, or None
    failed_date         — date failure triggered, or None
    max_runup_pct       — largest % price ran above Y before event (or now)
    days_tracked        — days from touch_date to event (or "now")
    post_event_drawdown_pct    — max % price dipped below Y after retest/fail
    post_event_days_to_recover — days to recover above Y after that dip
"""
import numpy as np
import pandas as pd


def find_ema_pullback_episodes(
    hist: pd.DataFrame,
    ema20: pd.Series,
    ema50: pd.Series,
    min_qualify_days: int = 50,
    retest_pct: float = 5.0,
    fail_pct: float = 8.0,
) -> list:
    """
    Returns a list of episode dicts (oldest first), one per qualifying setup.
    See module docstring for full logic description.
    """
    dates = hist.index
    n = len(dates)
    if n < min_qualify_days + 10:
        return []

    high_arr = hist["High"].to_numpy(dtype=float)
    low_arr  = hist["Low"].to_numpy(dtype=float)
    close_arr = hist["Close"].to_numpy(dtype=float)
    ema20_arr = ema20.to_numpy(dtype=float)
    ema50_arr = ema50.to_numpy(dtype=float)

    # Pre-compute the 20>50 boolean array once
    above = ema20_arr > ema50_arr  # True = 20 EMA above 50 EMA

    episodes = []
    i = 1  # need i-1 for crossover check

    while i < n:
        # --- Find next golden cross (20 EMA crossing ABOVE 50 EMA) ---
        if not (above[i] and not above[i - 1]):
            i += 1
            continue

        crossover_pos = i
        crossover_date = dates[crossover_pos]

        # --- Phase 1: Qualification window ---
        # 20 EMA must stay above 50 EMA AND Low must never touch EMA50
        # for at least min_qualify_days trading days starting from crossover_pos.
        qual_end_pos = crossover_pos  # inclusive last good day
        violated_pos = None
        j = crossover_pos
        days_qualified = 0
        while j < n:
            if not above[j]:
                # 20 EMA dropped below 50 EMA — qualification failed
                violated_pos = j
                break
            if low_arr[j] <= ema50_arr[j]:
                # Intraday low touched 50 EMA during qualification
                violated_pos = j
                break
            days_qualified += 1
            qual_end_pos = j
            if days_qualified >= min_qualify_days:
                break
            j += 1

        if days_qualified < min_qualify_days:
            # Didn't qualify — resume search after the violation
            i = (violated_pos + 1) if violated_pos is not None else (j + 1)
            continue

        qualify_end_date = dates[qual_end_pos]

        # --- Phase 2: X lock-in ---
        # Scan forward from qual_end_pos+1 for the first Low <= EMA50 touch.
        # Keep tracking running highest High from crossover_pos onward.
        running_high = float(np.max(high_arr[crossover_pos: qual_end_pos + 1]))
        touch_pos = None
        k = qual_end_pos + 1
        invalidated_before_touch = False
        while k < n:
            if not above[k]:
                # 20 EMA fell below 50 EMA before a touch even happened.
                # This means the entire setup resets from here.
                invalidated_before_touch = True
                break
            running_high = max(running_high, high_arr[k])
            if low_arr[k] <= ema50_arr[k]:
                touch_pos = k
                # Include touch day's high in X
                running_high = max(running_high, high_arr[k])
                break
            k += 1

        if invalidated_before_touch:
            # 20 crossed below 50 before any touch — restart search at that cross
            i = k
            continue

        if touch_pos is None:
            # Reached end of data without a touch — no episode yet
            break

        x_price = float(running_high)
        touch_date = dates[touch_pos]

        # --- Phase 3: Y tracking ---
        # From touch_pos onward: track running low; watch for:
        #   - 20 EMA crossing below 50 EMA → invalidate
        #   - 50 EMA >= X → signal fires, Y locked in
        running_low = float(low_arr[touch_pos])
        y_price = running_low
        y_fix_date = None
        signal_fired = False
        invalidated_after_touch = False
        m = touch_pos + 1
        while m < n:
            running_low = min(running_low, low_arr[m])
            if not above[m]:
                # 20 EMA crossed below 50 EMA during Y-tracking → invalidate
                invalidated_after_touch = True
                break
            if ema50_arr[m] >= x_price:
                # 50 EMA has crossed above X → signal fires
                signal_fired = True
                y_price = float(running_low)
                y_fix_date = dates[m]
                break
            m += 1

        if invalidated_after_touch:
            # Resume search at the invalidation cross
            i = m
            continue

        if not signal_fired:
            # Still waiting for 50 EMA to cross X (or ran out of data)
            y_price = float(running_low)
            ep = _build_episode(
                crossover_date=crossover_date,
                qualify_end_date=qualify_end_date,
                touch_date=touch_date,
                x_price=x_price,
                y_price=y_price,
                y_fix_date=None,
                status="signal_pending",
                hist=hist,
                retest_pct=retest_pct,
                fail_pct=fail_pct,
            )
            episodes.append(ep)
            break  # last episode in history, no point scanning further

        # Signal fired — classify post-signal retest/failure
        ep = _build_episode(
            crossover_date=crossover_date,
            qualify_end_date=qualify_end_date,
            touch_date=touch_date,
            x_price=x_price,
            y_price=y_price,
            y_fix_date=y_fix_date,
            status="signal_fired",
            hist=hist,
            retest_pct=retest_pct,
            fail_pct=fail_pct,
        )
        episodes.append(ep)

        # Advance search past this episode's completion
        i = m + 1
        # Skip forward until the next potential crossover (next 20-above-50)
        while i < n and above[i]:
            i += 1

    return episodes


def _build_episode(
    crossover_date,
    qualify_end_date,
    touch_date,
    x_price: float,
    y_price: float,
    y_fix_date,
    status: str,
    hist: pd.DataFrame,
    retest_pct: float,
    fail_pct: float,
) -> dict:
    """
    Assembles the full episode dict. For signal_fired episodes, classifies
    post-signal retest/failure of Y using the same logic as other strategies.
    """
    tested_date = tested_price = failed_date = None
    max_runup_pct = 0.0
    days_tracked = 0
    post_event_drawdown_pct = post_event_days_to_recover = None

    anchor = y_fix_date if y_fix_date is not None else touch_date

    if status == "signal_fired" and y_price is not None and anchor is not None:
        result = _classify_y_level(hist, y_price, anchor, retest_pct=retest_pct, fail_pct=fail_pct)
        status = result["status"]  # overwrite to naked/tested/failed
        tested_date = result["tested_date"]
        tested_price = result["tested_price"]
        failed_date = result["failed_date"]
        max_runup_pct = result["max_runup_pct"]
        days_tracked = result["days_tracked"]

        # Post-event drawdown measurement
        event_date = tested_date if status == "tested" else (failed_date if status == "failed" else None)
        if event_date is not None and y_price is not None:
            dd = _compute_post_event_drawdown(hist, y_price, event_date)
            post_event_drawdown_pct = dd["max_drawdown_pct"]
            post_event_days_to_recover = dd["days_to_recover"]

    # qualify_duration_days: trading days the clean 20>50 streak (with Low
    # never touching EMA50) actually ran before the pullback touch.  The
    # min_qualify_days floor is a binary gate, but a 120-day streak is
    # structurally much stronger than a 50-day one.  This lets users filter
    # or rank episodes by streak quality directly.
    qualify_duration_days = int(
        (pd.Timestamp(touch_date) - pd.Timestamp(crossover_date)).days
    ) if touch_date is not None and crossover_date is not None else 0

    return {
        "crossover_date": crossover_date,
        "qualify_end_date": qualify_end_date,
        "touch_date": touch_date,
        "qualify_duration_days": qualify_duration_days,
        "x_price": x_price,
        "y_price": y_price,
        "y_fix_date": y_fix_date,
        "status": status,
        "tested_date": tested_date,
        "tested_price": tested_price,
        "failed_date": failed_date,
        "max_runup_pct": max_runup_pct,
        "days_tracked": days_tracked,
        "post_event_drawdown_pct": post_event_drawdown_pct,
        "post_event_days_to_recover": post_event_days_to_recover,
    }


def _classify_y_level(
    hist: pd.DataFrame,
    y_price: float,
    anchor_date,
    retest_pct: float = 5.0,
    fail_pct: float = 8.0,
) -> dict:
    """
    Classifies how price has behaved relative to Y since anchor_date.
    Mirrors monthly_s1_shift_pattern.classify_x_level logic exactly:
      'naked'  — never moved clearly away then returned.
      'tested' — moved >=2x retest_pct above Y then came back within retest_pct%.
      'failed' — dropped fail_pct% or more below Y.
    Whichever triggers first wins; failed beats tested on same day.
    """
    after = hist.loc[hist.index >= anchor_date]
    if after.empty or y_price is None or pd.isna(y_price):
        return {
            "status": "naked",
            "tested_date": None, "tested_price": None,
            "failed_date": None, "max_runup_pct": 0.0, "days_tracked": 0,
        }

    dates  = after.index
    lows   = after["Low"].to_numpy(dtype=float)
    highs  = after["High"].to_numpy(dtype=float)
    closes = after["Close"].to_numpy(dtype=float)

    fail_threshold    = y_price * (1 - fail_pct / 100.0)
    retest_threshold  = y_price * (1 + retest_pct / 100.0)
    confirm_away      = y_price * (1 + 2 * retest_pct / 100.0)

    running_high = np.maximum.accumulate(highs)

    fail_mask = lows <= fail_threshold
    fail_pos  = int(np.argmax(fail_mask)) if fail_mask.any() else None

    ran_away_mask = highs >= confirm_away
    retest_pos = None
    if ran_away_mask.any():
        first_away = int(np.argmax(ran_away_mask))
        if first_away + 1 < len(lows):
            sub_lows = lows[first_away + 1:]
            sub_mask = sub_lows <= retest_threshold
            if sub_mask.any():
                retest_pos = first_away + 1 + int(np.argmax(sub_mask))

    candidates = []
    if fail_pos is not None:
        candidates.append(("failed", fail_pos))
    if retest_pos is not None:
        candidates.append(("tested", retest_pos))

    if candidates:
        candidates.sort(key=lambda c: c[1])
        ev_status, pos = candidates[0]
        event_date  = dates[pos]
        event_price = float(closes[pos])
        days_tracked = int((event_date - anchor_date).days)
        max_runup_pct = float((running_high[: pos + 1].max() - y_price) / y_price * 100)
        return {
            "status": ev_status,
            "tested_date":  event_date if ev_status == "tested"  else None,
            "tested_price": event_price if ev_status == "tested" else None,
            "failed_date":  event_date if ev_status == "failed"  else None,
            "max_runup_pct": max_runup_pct,
            "days_tracked": days_tracked,
        }

    max_runup_pct = float((running_high.max() - y_price) / y_price * 100)
    days_tracked  = int((dates[-1] - anchor_date).days)
    return {
        "status": "naked",
        "tested_date": None, "tested_price": None,
        "failed_date": None,
        "max_runup_pct": max_runup_pct,
        "days_tracked": days_tracked,
    }


def _compute_post_event_drawdown(hist: pd.DataFrame, level: float, event_date) -> dict:
    """
    Measures how much further price dropped below `level` after `event_date`,
    and how long it took to recover. Mirrors monthly_pivot_pattern logic.
    """
    empty = {
        "max_drawdown_pct": None, "lowest_price": None, "lowest_date": None,
        "recovered": None, "recovery_date": None, "days_to_recover": None,
    }
    if event_date is None or level is None or pd.isna(level):
        return empty

    after = hist.loc[hist.index >= event_date]
    if after.empty:
        return empty

    lows   = after["Low"].to_numpy(dtype=float)
    closes = after["Close"].to_numpy(dtype=float)
    adates = after.index

    breach_mask = lows < level
    if not breach_mask.any():
        return {
            "max_drawdown_pct": 0.0,
            "lowest_price": float(lows.min()),
            "lowest_date": adates[int(np.argmin(lows))],
            "recovered": True, "recovery_date": event_date, "days_to_recover": 0,
        }

    first_breach = int(np.argmax(breach_mask))
    after_breach_closes = closes[first_breach:]
    after_breach_dates  = adates[first_breach:]
    recovery_mask = after_breach_closes >= level

    if recovery_mask.any():
        rec_rel   = int(np.argmax(recovery_mask))
        rec_date  = after_breach_dates[rec_rel]
        days_rec  = int((rec_date - event_date).days)
        win_lows  = lows[: first_breach + rec_rel + 1]
        win_dates = adates[: first_breach + rec_rel + 1]
        recovered = True
    else:
        rec_date = None
        days_rec = None
        win_lows  = lows
        win_dates = adates
        recovered = False

    low_pos = int(np.argmin(win_lows))
    return {
        "max_drawdown_pct": max(0.0, (level - float(win_lows[low_pos])) / level * 100),
        "lowest_price":     float(win_lows[low_pos]),
        "lowest_date":      win_dates[low_pos],
        "recovered":        recovered,
        "recovery_date":    rec_date,
        "days_to_recover":  days_rec,
    }
