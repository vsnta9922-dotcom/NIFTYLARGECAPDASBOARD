"""
monthly_s1_shift_pattern.py
------------------------------
Detects the "Monthly S1 Shift Up" pattern:

  For each calendar month M (using the standard pivot formula, S1 for
  trading during month M is computed from month M-1's High/Low/Close):

    1. Did price touch or close at/below S1(M) at any point during month M?
       (Low <= S1(M) on any day - this covers both an intraday wick and a
       close-through, since a close below always implies the low was too.)

    2. If yes: X = the lowest Low reached anywhere in month M.

    3. Confirmation: compare S1 for the FOLLOWING month, S1(M+1) - computed
       from month M's own High/Low/Close - against S1(M). Most of the time,
       after a month where price fell to/through S1, the next month's S1
       will be LOWER (the decline drags the whole range down). In the rare
       case where S1(M+1) is actually HIGHER than S1(M), that indicates
       strong responsive buying pushed month M's range up despite the S1
       touch - this is the setup we want to capture.

    4. If confirmed, tracking starts from the beginning of month M+1 (the
       earliest point this could be known in real time) with X as a
       buy-on-revisit level. Tracked forward:
         - max_runup_pct: the largest % price ran up away from X before
           either retesting it or now (if still naked).
         - status: 'naked' (never revisited X), 'tested' (price pulled away
           then came back down within the retest band), or 'failed' (price
           dropped fail_pct% or more below X at any point - support
           decisively broken, no longer considered a valid level).
         - days_tracked: days from the start of tracking to whichever event
           resolved it (or to "now" if still naked).

Every qualifying month is recorded independently - a stock can have several
such episodes over its history.

═══════════════════════════════════════════════════════════════════════════
FIX (this revision) — 'tested' was firing without price ever actually
retesting the level
═══════════════════════════════════════════════════════════════════════════
A prior revision ("NEW SEMANTICS (Aug 2026)") replaced classify_x_level's
retest check with a bare "did Low ever come within retest_pct% of X",
checked from anchor_date onward with NO precondition. Since X is very
often already close to price right when it's established, this fired
"tested" almost immediately from ordinary day-to-day noise, without price
ever having genuinely moved away from X and come back — reported in
practice via the VWAP Support/Resistance strategy (which imports this same
function), e.g. an X level from 29-Jul was marked "tested" within a day or
two despite no real retest having happened. Restored the "confirmed move
away first, THEN retest" two-step check used by every other strategy in
this dashboard (see monthly_pivot_pattern.classify_retest for the
reference pattern this matches): price must first rally to at least
2x retest_pct% above X before a pullback back to within retest_pct% of X
counts as a genuine 'tested' event. 'failed' is unaffected — dropping
fail_pct% below X was, and remains, a failure regardless of what happened
before it.

This function is shared by TWO strategies (Monthly S1 Shift Up, which
calls it directly, and VWAP Support/Resistance, which imports it from
here) — fixing it here fixes both.
"""
import pandas as pd
import numpy as np


def compute_monthly_pivot_table(hist: pd.DataFrame) -> pd.DataFrame:
    """
    Returns a DataFrame indexed by calendar month (Period), columns
    H, L, C, P, R1, S1 - all computed from THAT month's own High/Low/Close.
    To get the S1 that APPLIES to trading during month M, use this table's
    row for month M-1 (i.e. shift forward by one month when applying it).
    """
    df = hist.copy()
    df["_ym"] = df.index.to_period("M")
    monthly = df.groupby("_ym").agg(H=("High", "max"), L=("Low", "min"), C=("Close", "last"))
    monthly["P"] = (monthly["H"] + monthly["L"] + monthly["C"]) / 3
    monthly["R1"] = 2 * monthly["P"] - monthly["L"]
    monthly["S1"] = 2 * monthly["P"] - monthly["H"]
    return monthly


def find_s1_shift_episodes(hist: pd.DataFrame, retest_pct: float = 5.0, fail_pct: float = 8.0,
                            min_confirm_days: int = 5):
    """
    Returns a list of episode dicts, oldest first, one per qualifying month.
    Each dict has:
      month (Period), x_price, x_date, anchor_date, s1_month, s1_next_month,
      status ('naked'|'tested'|'failed'), tested_date, tested_price,
      failed_date, max_runup_pct, days_tracked
    """
    pivot_table = compute_monthly_pivot_table(hist)
    months = pivot_table.index
    n_months = len(months)
    if n_months < 3:
        return []

    df = hist.copy()
    df["_ym"] = df.index.to_period("M")
    low_arr = hist["Low"].to_numpy()
    high_arr = hist["High"].to_numpy()
    ym_arr = df["_ym"].to_numpy()
    dates = hist.index

    episodes = []
    # Need month M-1 (for S1 applied in M) and month M+1 (for confirmation),
    # so M ranges from the 2nd month to the second-to-last month.
    for pos in range(1, n_months - 1):
        m = months[pos]
        m_prev = months[pos - 1]
        m_next = months[pos + 1]

        s1_applied_in_m = pivot_table.loc[m_prev, "S1"]
        if pd.isna(s1_applied_in_m):
            continue

        month_mask = ym_arr == m
        if not month_mask.any():
            continue
        month_lows = low_arr[month_mask]
        month_dates = dates[month_mask]

        touched = month_lows <= s1_applied_in_m
        if not touched.any():
            continue

        x_price = float(month_lows.min())
        x_date = month_dates[np.argmin(month_lows)]

        s1_next_month = pivot_table.loc[m, "S1"]  # applies to trading in m_next
        if pd.isna(s1_next_month) or not (s1_next_month > s1_applied_in_m):
            continue  # the common case - S1 shifted down or flat, not a signal

        # Confirmed setup. Tracking starts at the beginning of month M+1.
        anchor_date = dates[ym_arr == m_next][0] if (ym_arr == m_next).any() else None
        if anchor_date is None:
            continue

        result = classify_x_level(hist, x_price, anchor_date, retest_pct=retest_pct, fail_pct=fail_pct,
                                   min_confirm_days=min_confirm_days)

        # Episode invalidated: price touched X inside the quiet window.
        if result.get("invalidated"):
            continue

        # s1_shift_pct: how far S1 shifted UP for the following month, expressed
        # as a % of the current month's S1.  The confirmation gate only requires
        # S1(M+1) > S1(M), but the SIZE of the shift matters: a 0.1% shift is
        # a statistical whisker while a 5% shift signals aggressive responsive
        # buying.  Surfacing this lets users filter/rank by confirmation quality.
        s1_shift_pct = round((s1_next_month - s1_applied_in_m) / abs(s1_applied_in_m) * 100, 2) \
            if s1_applied_in_m != 0 else 0.0

        episodes.append({
            "month": m,
            "x_price": x_price,
            "x_date": x_date,
            "anchor_date": anchor_date,
            "s1_month": float(s1_applied_in_m),
            "s1_next_month": float(s1_next_month),
            "s1_shift_pct": s1_shift_pct,
            **result,
        })

    return episodes


def classify_x_level(hist: pd.DataFrame, x_price: float, anchor_date,
                      retest_pct: float = 5.0, fail_pct: float = 8.0,
                      min_confirm_days: int = 5):
    """
    Tracks price relative to a support level `x_price` from `anchor_date`
    onward, classifying it as:

      'naked'  - Price did NOT touch X (Low <= X) during the mandatory
                 quiet window (first min_confirm_days trading days after
                 anchor_date) AND has not touched X or failed since then.

      'tested' - The quiet window completed cleanly (no touch of X during
                 those first min_confirm_days days), AND price subsequently
                 touched X (Low <= X) on any single day after the window
                 closes.  Just one touch is enough — no "move-away first"
                 precondition once the window is satisfied.

      'failed' - Price dropped fail_pct% or more below X at any point from
                 anchor_date onward (including inside the quiet window).
                 'failed' takes priority over everything: a failure that
                 happens inside the quiet window is still a failure, and if
                 the same day would trigger both 'tested' and 'failed',
                 'failed' wins.

    Two-phase logic (lower-band / support):
      Phase 1 — Quiet window: days 1 … min_confirm_days (trading days,
                not calendar days).  If Low <= X on ANY of these days the
                level was immediately violated; status stays 'naked' only
                if NO touch occurred.  A fail_pct breach here → 'failed'.
      Phase 2 — After the window: a single day where Low <= X → 'tested'.
                A fail_pct breach at any point → 'failed'.
                Whichever triggers first wins; ties go to 'failed'.

    Also returns max_runup_pct (largest % High ran above X before the
    resolving event, or before "now" if still naked — capped ±1000% to
    filter data artefacts) and days_tracked.
    """
    # Normalize anchor to midnight to avoid tz / timestamp mismatches.
    anchor_norm = pd.Timestamp(anchor_date).normalize()
    # D+1 onward — Day D itself establishes the level and cannot be the
    # first observation.
    after = hist.loc[hist.index.normalize() > anchor_norm]
    if after.empty or pd.isna(x_price):
        return {
            "status": "naked", "tested_date": None, "tested_price": None,
            "failed_date": None, "max_runup_pct": 0.0, "days_tracked": 0,
        }

    dates = after.index
    lows = after["Low"].to_numpy()
    highs = after["High"].to_numpy()
    closes = after["Close"].to_numpy()
    n = len(dates)

    fail_threshold = x_price * (1 - fail_pct / 100.0)
    running_high = np.maximum.accumulate(highs)

    # ── Phase 1: quiet window (first min_confirm_days trading days) ───────
    # If price touches X (Low <= x_price) at any point here the window is
    # broken.  A fail_pct breach → 'failed' immediately.
    quiet_end = min(min_confirm_days, n)  # index of first post-window day

    for i in range(quiet_end):
        if lows[i] <= fail_threshold:
            # Failure inside quiet window — resolve immediately.
            days_tracked = int((dates[i] - anchor_norm).days)
            max_runup_pct = float((running_high[: i + 1].max() - x_price) / x_price * 100)
            max_runup_pct = max(-1000.0, min(1000.0, max_runup_pct))
            return {
                "status": "failed",
                "tested_date": None,
                "tested_price": None,
                "failed_date": dates[i],
                "max_runup_pct": max_runup_pct,
                "days_tracked": days_tracked,
            }
        if lows[i] <= x_price:
            # Touch inside the quiet window — level was NOT respected during
            # the mandatory hold-off period.  Mark as invalidated so callers
            # can skip this episode entirely.  days_tracked reflects how far
            # in we got before the touch, for debugging.
            return {
                "status": "naked", "invalidated": True,
                "tested_date": None, "tested_price": None,
                "failed_date": None, "max_runup_pct": 0.0,
                "days_tracked": int((dates[i] - anchor_norm).days),
            }

    # Quiet window passed without a touch.  If there were fewer rows than
    # min_confirm_days, we're still inside the window — stay 'naked'.
    if n <= quiet_end:
        max_runup_pct = float((running_high.max() - x_price) / x_price * 100)
        max_runup_pct = max(-1000.0, min(1000.0, max_runup_pct))
        days_tracked = int((dates[-1] - anchor_norm).days)
        return {
            "status": "naked", "tested_date": None, "tested_price": None,
            "failed_date": None, "max_runup_pct": max_runup_pct,
            "days_tracked": days_tracked,
        }

    # ── Phase 2: post-window tracking ────────────────────────────────────
    # Scan from quiet_end onward.  First touch (Low <= X) → 'tested'.
    # First fail (Low <= fail_threshold) → 'failed'.  Ties → 'failed'.
    tested_pos = None
    fail_pos = None

    for i in range(quiet_end, n):
        if lows[i] <= fail_threshold and fail_pos is None:
            fail_pos = i
        if lows[i] <= x_price and tested_pos is None:
            tested_pos = i
        # Stop as soon as both candidates are found (or the earlier one is).
        if fail_pos is not None and tested_pos is not None:
            break
        if fail_pos is not None and (tested_pos is None or fail_pos <= tested_pos):
            break

    candidates = []
    if fail_pos is not None:
        candidates.append(("failed", fail_pos))
    if tested_pos is not None:
        candidates.append(("tested", tested_pos))

    if candidates:
        # Sort by position; 'failed' wins ties (it sorts first because we
        # added it first and Python's sort is stable, but make it explicit).
        candidates.sort(key=lambda c: (c[1], c[0] != "failed"))
        status, pos = candidates[0]
        event_date = dates[pos]
        event_price = float(closes[pos])
        days_tracked = int((event_date - anchor_norm).days)
        max_runup_pct = float((running_high[: pos + 1].max() - x_price) / x_price * 100)
        max_runup_pct = max(-1000.0, min(1000.0, max_runup_pct))
        return {
            "status": status,
            "tested_date": event_date if status == "tested" else None,
            "tested_price": event_price if status == "tested" else None,
            "failed_date": event_date if status == "failed" else None,
            "max_runup_pct": max_runup_pct,
            "days_tracked": days_tracked,
        }

    # Nothing triggered — still naked.
    max_runup_pct = float((running_high.max() - x_price) / x_price * 100)
    max_runup_pct = max(-1000.0, min(1000.0, max_runup_pct))
    days_tracked = int((dates[-1] - anchor_norm).days)
    return {
        "status": "naked", "tested_date": None, "tested_price": None,
        "failed_date": None, "max_runup_pct": max_runup_pct, "days_tracked": days_tracked,
    }
