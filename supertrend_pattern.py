"""
supertrend_pattern.py
-----------------------
Detects the "Supertrend + 200 EMA Three-Phase Reversal" pattern.

REWRITE NOTE (July 2026):
  The episode detector below is implemented as an explicit STATE MACHINE
  (see find_supertrend_episodes) rather than a sequential scanner with
  patched-on validity gates. The previous version anchored Phase 1 correctly
  at the SELL->BUY flip but (a) never enforced the Phase 2 "red line crossed
  below 200 EMA" requirement at all, and (b) split Phase 3 completion into
  two independently-tracked criteria (ST-line-vs-EMA, then ST-line-OR-Close-
  vs-X) instead of the single "both conditions satisfied together" rule
  originally specified. Both bugs are fixed here structurally, in the state
  transitions themselves, so no after-the-fact SQL patch is needed if the
  logic changes again -- states are either entered validly or the whole
  episode is discarded and re-searched.

SUPERTREND FORMULA (ATR-based):
  ATR(period) computed as Wilder/RMA smoothing (same as TradingView default).
  Upper Band = (High+Low)/2 + multiplier * ATR
  Lower Band = (High+Low)/2 - multiplier * ATR
  Supertrend direction: +1 (buy/green, price above lower band) or -1 (sell/red)
  The Supertrend LINE itself is:
    - Lower Band when direction == +1  (support line, green)
    - Upper Band when direction == -1  (resistance line, red)

STATE MACHINE -- THREE PHASES (per the Asian Paints example on the chart):

  STATE: SEARCHING
    Waiting for a SELL -> BUY direction flip. On that flip, transition to
    PHASE1 and set phase1_start = flip day, x_price = High of that day.

  STATE: PHASE1 -- BULL PHASE
    - Direction stays +1. Track the running highest High (-> X) every day.
    - On the day direction flips to -1 (SELL), Phase 1 ends the day before
      the flip (phase1_end).
    - VALIDATION (only evaluated at the moment SELL triggers):
        st_line[phase1_end] >= ema200[phase1_end]   (green line >= 200 EMA
        on the last day of Phase 1 -- it does NOT need to have crossed above
        the EMA during Phase 1; it may already have started above it).
      If this fails, the ENTIRE episode is discarded (not just Phase 2+) and
      the scan resumes searching for the next SELL->BUY flip strictly after
      this SELL flip -- i.e. this SELL flip is not reused as a Phase-3 start
      for a discarded Phase 1.

  STATE: PHASE2 -- BEAR PHASE (entered only if PHASE1 validation passed)
    - Direction stays -1. Track running lowest Low (-> Y) and running lowest
      200 EMA (-> Z) every day.
    - MANDATORY CONDITION: at least one day during Phase 2 must have
        st_line[day] <= ema200[day]   (red line at/below the 200 EMA).
      This is checked continuously while in PHASE2, not after the fact.
    - On the day direction flips back to +1 (BUY):
        - If the mandatory condition was never satisfied during this Phase 2,
          the ENTIRE episode (Phase 1 + Phase 2) is discarded. The BUY flip
          that just occurred is re-evaluated as a fresh Phase 1 candidate
          (SEARCHING is re-entered at this same index, not past it).
        - If satisfied, transition to PHASE3 with phase3_start = flip day.
    - NOT STORED while still active: a Phase 2 that hasn't yet flipped back
      to BUY has no phase3_start and an unresolved mandatory condition -- it
      isn't actionable yet (nothing to buy, nothing confirmed), so nothing
      is appended to the episode list for it. This mirrors how
      ema_pullback_pattern and breakout_pullback_pattern silently skip a
      setup that hasn't reached its own qualifying flip yet, instead of
      surfacing a "still forming" row with no real levels.

  STATE: PHASE3 -- RECOVERY / COMPLETION (entered only if PHASE2's mandatory
  condition passed)
    - Direction stays +1. Two milestones tracked independently:
        (a) st_line[day] >= ema200[day]   (green line >= 200 EMA -- may
            already be true on phase3_start) -> ema_cross_pos
        (b) st_line[day] >  x_price       (green line clears X) -> x_clear_pos
      Because the ST line (lower band) only ever ratchets upward for as long
      as direction stays +1, once either milestone is reached it stays true
      for the rest of this Phase 3 run. Completion happens on
      max(ema_cross_pos, x_clear_pos) -- the day the slower of the two
      finally catches up to the faster one.
    - CRUCIALLY, we do not wait for both before storing anything. (a) can be
      an open-ended wait with no bound on how long it takes; the episode is
      only surfaced once (a) has actually happened, at which point it's a
      bounded, watchable "confirmed reversal, waiting on X" setup:
        - Neither (a) nor (b) yet -> not stored. Same treatment as an active
          Phase 2: not yet actionable, silently skipped.
        - (a) true, (b) not yet, history runs out -> stored as
          "phase3_pending" (signal_date = ema_cross_date, x_cleared_date =
          None). This is the useful "watching for X" state.
        - Both true -> stored as "complete" (signal_date = ema_cross_date,
          x_cleared_date = the later, separate date X actually cleared).
    - If direction flips back to -1 (SELL) before completion, the whole
      episode is discarded -- regardless of whether the EMA cross had
      already happened -- and the scan resumes searching for the next
      SELL->BUY flip from that new SELL flip onward, exactly like a fresh
      Phase 1 search. A failed attempt is never stored, even a partial one.

  EPISODE OUTPUTS:
    phase1_start      -- day of the validating SELL->BUY flip (Phase 1 start)
    phase1_end        -- last day of Phase 1 (day before ST flips sell)
    x_price           -- highest High during Phase 1
    x_date            -- date of that high
    phase2_start      -- first day of Phase 2 (ST sell)
    y_price           -- lowest Low during Phase 2
    y_date            -- date of that low
    z_price           -- lowest 200 EMA during Phase 2
    z_date            -- date of that 200 EMA low
    phase3_start      -- day ST flipped back to BUY (Phase 2 mandatory condition already satisfied)
    signal_date       -- day ST line first crossed >= 200 EMA (the ema_cross milestone)
    x_cleared_date    -- day ST line first crossed > X (None while still "phase3_pending")
    status            -- see STATUS VALUES below
    st_period         -- Supertrend ATR period used
    st_multiplier     -- Supertrend ATR multiplier used

  STATUS VALUES (only two -- Phase 2, and pre-EMA-cross Phase 3, are never stored):
    "phase3_pending"  -- Phase 2 validated, ST flipped buy, AND ST line has
                         already crossed >= 200 EMA (signal_date is set).
                         Only waiting on ST line > X now. X/Y/Z are already
                         confirmed and live at this stage -- x_cleared_date
                         is the only thing still open.
    "complete"        -- both Phase 3 milestones reached. All three
                         levels (X, Y, Z) are live and retest-tracked.
      Then, from x_cleared_date onward, per-level retest classification
      (mirrors ema_pullback_pattern / monthly_s1_shift_pattern exactly):
    "x_status", "y_status", "z_status" each: "naked" | "tested" | "failed"
      plus, per level: {level}_max_runup_pct (largest % price ran away from
      the level BEFORE it was retested/failed, or "now" if still naked),
      {level}_days_tracked, {level}_drawdown_pct and {level}_recovery_days
      (how far price dipped below the level after the retest/failure event,
      and how long it took to recover) -- the same stat set every other
      pattern module in this dashboard exposes.

  Episodes that fail Phase 1 validation or Phase 2's mandatory condition are
  NEVER stored -- they are discarded during the scan itself, so the ledger

  only ever contains structurally valid episodes. This is what makes the
  standalone cleanup/migration script largely unnecessary going forward.

MULTI-PARAMETER SUPPORT:
  The function accepts a list of (period, multiplier) pairs so the dashboard can
  run multiple Supertrend variants simultaneously and display them all in one table.
  The PRIMARY KEY in the DB is (symbol, phase1_start, st_period, st_multiplier)
  so each variant gets its own row -- users can filter the table by variant.
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# SUPERTREND COMPUTATION
# ---------------------------------------------------------------------------

def compute_supertrend(hist: pd.DataFrame, period: int = 7, multiplier: float = 3.0):
    """
    Computes the Supertrend indicator using Wilder/RMA ATR smoothing
    (identical to TradingView's default Supertrend implementation).

    Returns a DataFrame with columns:
      supertrend   — the Supertrend line value (lower band when bullish,
                     upper band when bearish)
      direction    — +1 (bullish/buy) or -1 (bearish/sell)

    Uses numpy for performance; operates on full history passed in.
    """
    high  = hist["High"].to_numpy(dtype=float)
    low   = hist["Low"].to_numpy(dtype=float)
    close = hist["Close"].to_numpy(dtype=float)
    n     = len(close)

    # True Range
    prev_close = np.empty(n)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum(high - low,
         np.maximum(np.abs(high - prev_close),
                    np.abs(low  - prev_close)))

    # Wilder RMA (same as EMA with alpha = 1/period)
    alpha = 1.0 / period
    atr = np.empty(n)
    atr[0] = tr[0]
    for i in range(1, n):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i - 1]

    hl2 = (high + low) / 2.0
    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr

    upper = np.empty(n)
    lower = np.empty(n)
    upper[0] = upper_basic[0]
    lower[0] = lower_basic[0]

    # Band adjustment: bands can only tighten, never expand (TradingView logic)
    for i in range(1, n):
        upper[i] = upper_basic[i] if (upper_basic[i] < upper[i - 1] or close[i - 1] > upper[i - 1]) else upper[i - 1]
        lower[i] = lower_basic[i] if (lower_basic[i] > lower[i - 1] or close[i - 1] < lower[i - 1]) else lower[i - 1]

    # Direction & supertrend line
    direction   = np.empty(n, dtype=int)
    supertrend  = np.empty(n)
    direction[0] = 1
    supertrend[0] = lower[0]

    for i in range(1, n):
        if supertrend[i - 1] == upper[i - 1]:
            # Was bearish
            direction[i] = 1 if close[i] > upper[i] else -1
        else:
            # Was bullish
            direction[i] = -1 if close[i] < lower[i] else 1
        supertrend[i] = lower[i] if direction[i] == 1 else upper[i]

    result = pd.DataFrame(
        {"supertrend": supertrend, "direction": direction},
        index=hist.index,
    )
    return result


# ---------------------------------------------------------------------------
# RETEST CLASSIFIER (shared logic, mirrors other pattern modules)
# ---------------------------------------------------------------------------

def _classify_level(
    hist: pd.DataFrame,
    level: float,
    anchor_date,
    retest_pct: float = 5.0,
    fail_pct: float = 8.0,
) -> dict:
    """
    Classify how price has behaved relative to `level` since `anchor_date`.
    Returns dict with keys: status, tested_date, tested_price, failed_date,
    max_runup_pct, days_tracked.
    """
    after = hist.loc[hist.index >= anchor_date]
    if after.empty or level is None or pd.isna(level):
        return {"status": "naked", "tested_date": None, "tested_price": None,
                "failed_date": None, "max_runup_pct": 0.0, "days_tracked": 0}

    dates  = after.index
    lows   = after["Low"].to_numpy(dtype=float)
    highs  = after["High"].to_numpy(dtype=float)
    closes = after["Close"].to_numpy(dtype=float)

    fail_threshold   = level * (1 - fail_pct  / 100.0)
    retest_threshold = level * (1 + retest_pct / 100.0)
    confirm_away     = level * (1 + 2 * retest_pct / 100.0)

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
    if fail_pos  is not None: candidates.append(("failed", fail_pos))
    if retest_pos is not None: candidates.append(("tested", retest_pos))

    if candidates:
        candidates.sort(key=lambda c: c[1])
        ev_status, pos = candidates[0]
        event_date  = dates[pos]
        event_price = float(closes[pos])
        days_tracked = int((event_date - anchor_date).days)
        max_runup_pct = float((running_high[:pos + 1].max() - level) / level * 100)
        return {
            "status":       ev_status,
            "tested_date":  event_date if ev_status == "tested"  else None,
            "tested_price": event_price if ev_status == "tested" else None,
            "failed_date":  event_date if ev_status == "failed"  else None,
            "max_runup_pct": max_runup_pct,
            "days_tracked":  days_tracked,
        }

    max_runup_pct = float((running_high.max() - level) / level * 100)
    days_tracked  = int((dates[-1] - anchor_date).days)
    return {"status": "naked", "tested_date": None, "tested_price": None,
            "failed_date": None, "max_runup_pct": max_runup_pct,
            "days_tracked": days_tracked}


def _post_event_drawdown(hist: pd.DataFrame, level: float, event_date) -> dict:
    """Max drawdown below level after event_date, and recovery time."""
    empty = {"max_drawdown_pct": None, "lowest_price": None, "lowest_date": None,
             "recovered": None, "recovery_date": None, "days_to_recover": None}
    if event_date is None or level is None or pd.isna(level):
        return empty
    after = hist.loc[hist.index >= event_date]
    if after.empty:
        return empty
    lows   = after["Low"].to_numpy(dtype=float)
    closes = after["Close"].to_numpy(dtype=float)
    adates = after.index
    breach = lows < level
    if not breach.any():
        return {"max_drawdown_pct": 0.0, "lowest_price": float(lows.min()),
                "lowest_date": adates[int(np.argmin(lows))],
                "recovered": True, "recovery_date": event_date, "days_to_recover": 0}
    first_b = int(np.argmax(breach))
    rec_mask = closes[first_b:] >= level
    if rec_mask.any():
        rec_rel  = int(np.argmax(rec_mask))
        rec_date = adates[first_b + rec_rel]
        days_rec = int((rec_date - event_date).days)
        win_lows  = lows[:first_b + rec_rel + 1]
        win_dates = adates[:first_b + rec_rel + 1]
        recovered = True
    else:
        rec_date = None; days_rec = None
        win_lows  = lows; win_dates = adates; recovered = False
    low_pos = int(np.argmin(win_lows))
    return {"max_drawdown_pct": max(0.0, (level - float(win_lows[low_pos])) / level * 100),
            "lowest_price": float(win_lows[low_pos]), "lowest_date": win_dates[low_pos],
            "recovered": recovered, "recovery_date": rec_date, "days_to_recover": days_rec}


# ---------------------------------------------------------------------------
# MAIN EPISODE FINDER -- explicit state machine (SEARCHING / PHASE1 / PHASE2 / PHASE3)
# ---------------------------------------------------------------------------

def find_supertrend_episodes(
    hist: pd.DataFrame,
    ema200: pd.Series,
    st_period: int = 7,
    st_multiplier: float = 3.0,
    retest_pct: float = 5.0,
    fail_pct: float = 8.0,
) -> list:
    """
    Detects all Three-Phase Supertrend + 200 EMA reversal episodes for a
    single (period, multiplier) variant. Returns list of episode dicts.

    Implemented as a state machine: SEARCHING -> PHASE1 -> PHASE2 -> PHASE3.
    An episode only gets recorded in the ledger once it has passed every gate
    up to its current state; if a gate fails, the episode is discarded
    entirely (never appended) and the scan resumes from the flip that
    invalidated it, treating that flip as a fresh candidate. See the module
    docstring for the full state-transition spec.
    """
    if len(hist) < st_period * 3 + 10:
        return []

    st_df = compute_supertrend(hist, period=st_period, multiplier=float(st_multiplier))
    st_line = st_df["supertrend"].to_numpy(dtype=float)
    st_dir  = st_df["direction"].to_numpy(dtype=int)
    ema200_arr = ema200.to_numpy(dtype=float)
    high_arr  = hist["High"].to_numpy(dtype=float)
    low_arr   = hist["Low"].to_numpy(dtype=float)
    dates = hist.index
    n = len(dates)

    episodes = []
    i = 1  # start at 1 so we can check direction flip from i-1

    while i < n:
        # ------------------------------------------------------------
        # STATE: SEARCHING -- find a SELL(-1) -> BUY(+1) flip.
        # ------------------------------------------------------------
        if not (st_dir[i] == 1 and st_dir[i - 1] == -1):
            i += 1
            continue

        phase1_start = dates[i]
        x_price = float(high_arr[i])
        x_pos   = i

        # ------------------------------------------------------------
        # STATE: PHASE1 -- track running high (X) until SELL flip.
        # ------------------------------------------------------------
        j = i
        phase1_end_pos = None
        while j < n:
            if st_dir[j] == -1:
                phase1_end_pos = j - 1
                break
            if high_arr[j] > x_price:
                x_price = float(high_arr[j])
                x_pos   = j
            j += 1

        if phase1_end_pos is None:
            # ST never flipped sell -- ongoing bull phase, no completed setup yet.
            break

        # VALIDATION GATE (Phase 1 -> Phase 2): green ST line on the last day
        # of Phase 1 must be >= 200 EMA on that same day. If it fails, discard
        # the whole episode and resume SEARCHING from the sell-flip day `j`
        # (not past it) so a later valid episode starting there isn't skipped.
        if st_line[phase1_end_pos] < ema200_arr[phase1_end_pos]:
            i = j
            continue

        phase1_end = dates[phase1_end_pos]
        x_date     = dates[x_pos]

        # ------------------------------------------------------------
        # STATE: PHASE2 -- track running low (Y), running lowest EMA (Z),
        # and the MANDATORY condition (red ST line <= 200 EMA at least once).
        # ------------------------------------------------------------
        phase2_start_pos = phase1_end_pos + 1
        if phase2_start_pos >= n:
            break

        phase2_start = dates[phase2_start_pos]
        y_price = float(low_arr[phase2_start_pos])
        y_pos   = phase2_start_pos
        z_price = float(ema200_arr[phase2_start_pos])
        z_pos   = phase2_start_pos
        # Mandatory condition tracked uniformly inside the loop (including the
        # first bar) so there is no risk of the pre-loop initialisation
        # diverging from the in-loop check.
        mandatory_condition_met = False

        k = phase2_start_pos
        phase3_start_pos = None
        while k < n:
            if st_dir[k] == 1 and st_dir[k - 1] == -1:
                phase3_start_pos = k
                break
            if low_arr[k] < y_price:
                y_price = float(low_arr[k])
                y_pos   = k
            if ema200_arr[k] < z_price:
                z_price = float(ema200_arr[k])
                z_pos   = k
            if st_line[k] <= ema200_arr[k]:
                mandatory_condition_met = True
            k += 1

        y_date = dates[y_pos]
        z_date = dates[z_pos]

        if phase3_start_pos is None:
            # Still in Phase 2, ran out of history before a BUY flip -- not
            # yet actionable (no phase3_start, no confirmed mandatory
            # condition outcome). Silently skip, same as how ema_pullback and
            # breakout_pullback don't store a setup still waiting on its
            # qualifying flip. Nothing to record; stop scanning this symbol.
            break

        # MANDATORY GATE (Phase 2 -> Phase 3): if the red ST line never
        # touched/crossed the 200 EMA during this Phase 2, discard the whole
        # episode (Phase 1 + Phase 2). Re-enter SEARCHING at phase3_start_pos
        # itself, since that BUY flip is a legitimate fresh Phase 1 candidate.
        if not mandatory_condition_met:
            i = phase3_start_pos
            continue

        phase3_start = dates[phase3_start_pos]

        # ------------------------------------------------------------
        # STATE: PHASE3 -- two separate milestones, tracked independently:
        #   (a) ST line crosses >= 200 EMA  (ema_cross_pos)
        #   (b) ST line crosses  >  X       (x_clear_pos)
        # Completion requires BOTH to have happened; because the ST line
        # (lower band) only ratchets upward for as long as direction stays
        # +1, once either milestone is reached it stays true for the rest
        # of this Phase 3 run -- so completion_pos = max(ema_cross_pos,
        # x_clear_pos), the day the SLOWER of the two finally catches up.
        #
        # Crucially: we do NOT wait for both before storing anything. (a)
        # alone can be an open-ended wait (nothing bounds how long price
        # takes to clear X), so an episode is only worth surfacing once (a)
        # has actually happened -- from then on it's a bounded, watchable
        # "waiting on X" setup. Before that, it's silently skipped, same
        # as Phase 2 while still active.
        # ------------------------------------------------------------
        m = phase3_start_pos
        ema_cross_pos  = None
        x_clear_pos    = None
        completion_pos = None
        phase3_died_at = None
        while m < n:
            if st_dir[m] == -1:
                phase3_died_at = m
                break
            if ema_cross_pos is None and st_line[m] >= ema200_arr[m]:
                ema_cross_pos = m
            if x_clear_pos is None and st_line[m] > x_price:
                x_clear_pos = m
            if ema_cross_pos is not None and x_clear_pos is not None:
                completion_pos = max(ema_cross_pos, x_clear_pos)
                break
            m += 1

        if phase3_died_at is not None:
            # Failed recovery attempt -- discard entirely (whether or not the
            # EMA cross had already happened), resume SEARCHING from the new
            # sell flip. Same treatment as a Phase1/Phase2 gate failure:
            # nothing about a failed attempt is stored.
            i = phase3_died_at
            continue

        if completion_pos is not None:
            ema_cross_date = dates[ema_cross_pos]
            x_cleared_date = dates[x_clear_pos]
            episodes.append(_make_episode(
                hist=hist, ema200=ema200,
                phase1_start=phase1_start, phase1_end=phase1_end,
                x_price=x_price, x_date=x_date,
                phase2_start=phase2_start, y_price=y_price, y_date=y_date,
                z_price=z_price, z_date=z_date,
                phase3_start=phase3_start, signal_date=ema_cross_date,
                x_cleared_date=x_cleared_date,
                completion_date=dates[completion_pos],
                status="complete",
                st_period=st_period, st_multiplier=st_multiplier,
                retest_pct=retest_pct, fail_pct=fail_pct,
            ))
            # Resume SEARCHING for the next episode strictly after this one.
            i = completion_pos + 1
            continue

        # Ran out of history before completion.
        if ema_cross_pos is not None:
            # ST has ALREADY crossed the 200 EMA -- the open-ended part of
            # the wait is over. Only waiting on X now, which is a bounded,
            # watchable setup. This is the case worth surfacing.
            ema_cross_date = dates[ema_cross_pos]
            episodes.append(_make_episode(
                hist=hist, ema200=ema200,
                phase1_start=phase1_start, phase1_end=phase1_end,
                x_price=x_price, x_date=x_date,
                phase2_start=phase2_start, y_price=y_price, y_date=y_date,
                z_price=z_price, z_date=z_date,
                phase3_start=phase3_start, signal_date=ema_cross_date,
                x_cleared_date=None,
                status="phase3_pending",
                st_period=st_period, st_multiplier=st_multiplier,
                retest_pct=retest_pct, fail_pct=fail_pct,
            ))
        # else: ST hasn't crossed the 200 EMA yet -- open-ended wait, not yet
        # actionable, silently skipped (same treatment as an active Phase 2).
        break

    return episodes


def _make_episode(
    hist, ema200,
    phase1_start, phase1_end,
    x_price, x_date,
    phase2_start, y_price, y_date,
    z_price, z_date,
    phase3_start, signal_date, x_cleared_date,
    status,
    st_period, st_multiplier,
    retest_pct, fail_pct,
    completion_date=None,
) -> dict:
    """
    Assembles the full episode dict and — for complete episodes — runs
    the retest/failure classifier for each of the three levels X, Y, Z.
    The anchor date for retest tracking is `completion_date` (the moment
    ALL three levels became "live" buy targets simultaneously, i.e. the
    LATER of the EMA-cross and X-clear milestones — not necessarily
    x_cleared_date itself, since X can in rare cases clear before the ST
    line has caught up to the 200 EMA). Falls back to x_cleared_date if
    completion_date isn't supplied, for backward compatibility.
    """
    x_status = y_status = z_status = "naked"
    x_tested_date = x_tested_price = x_failed_date = None
    y_tested_date = y_tested_price = y_failed_date = None
    z_tested_date = z_tested_price = z_failed_date = None
    x_max_runup = y_max_runup = z_max_runup = 0.0
    x_days = y_days = z_days = 0
    x_drawdown = y_drawdown = z_drawdown = None
    x_recovery = y_recovery = z_recovery = None

    if status == "complete" and (completion_date is not None or x_cleared_date is not None):
        anchor = completion_date if completion_date is not None else x_cleared_date

        rx = _classify_level(hist, x_price, anchor, retest_pct, fail_pct)
        x_status      = rx["status"]
        x_tested_date = rx["tested_date"]
        x_tested_price= rx["tested_price"]
        x_failed_date = rx["failed_date"]
        x_max_runup   = rx["max_runup_pct"]
        x_days        = rx["days_tracked"]
        ev = x_tested_date if x_status == "tested" else x_failed_date
        if ev:
            dd = _post_event_drawdown(hist, x_price, ev)
            x_drawdown = dd["max_drawdown_pct"]; x_recovery = dd["days_to_recover"]

        ry = _classify_level(hist, y_price, anchor, retest_pct, fail_pct)
        y_status      = ry["status"]
        y_tested_date = ry["tested_date"]
        y_tested_price= ry["tested_price"]
        y_failed_date = ry["failed_date"]
        y_max_runup   = ry["max_runup_pct"]
        y_days        = ry["days_tracked"]
        ev = y_tested_date if y_status == "tested" else y_failed_date
        if ev:
            dd = _post_event_drawdown(hist, y_price, ev)
            y_drawdown = dd["max_drawdown_pct"]; y_recovery = dd["days_to_recover"]

        rz = _classify_level(hist, z_price, anchor, retest_pct, fail_pct)
        z_status      = rz["status"]
        z_tested_date = rz["tested_date"]
        z_tested_price= rz["tested_price"]
        z_failed_date = rz["failed_date"]
        z_max_runup   = rz["max_runup_pct"]
        z_days        = rz["days_tracked"]
        ev = z_tested_date if z_status == "tested" else z_failed_date
        if ev:
            dd = _post_event_drawdown(hist, z_price, ev)
            z_drawdown = dd["max_drawdown_pct"]; z_recovery = dd["days_to_recover"]

    return {
        "phase1_start":    phase1_start,
        "phase1_end":      phase1_end,
        "x_price":         x_price,
        "x_date":          x_date,
        "phase2_start":    phase2_start,
        "y_price":         y_price,
        "y_date":          y_date,
        "z_price":         z_price,
        "z_date":          z_date,
        "phase3_start":    phase3_start,
        "signal_date":     signal_date,
        "x_cleared_date":  x_cleared_date,
        "status":          status,
        "st_period":       st_period,
        "st_multiplier":   float(st_multiplier),
        # Per-level retest results
        "x_status":        x_status,
        "x_tested_date":   x_tested_date,
        "x_tested_price":  x_tested_price,
        "x_failed_date":   x_failed_date,
        "x_max_runup_pct": x_max_runup,
        "x_days_tracked":  x_days,
        "x_drawdown_pct":  x_drawdown,
        "x_recovery_days": x_recovery,
        "y_status":        y_status,
        "y_tested_date":   y_tested_date,
        "y_tested_price":  y_tested_price,
        "y_failed_date":   y_failed_date,
        "y_max_runup_pct": y_max_runup,
        "y_days_tracked":  y_days,
        "y_drawdown_pct":  y_drawdown,
        "y_recovery_days": y_recovery,
        "z_status":        z_status,
        "z_tested_date":   z_tested_date,
        "z_tested_price":  z_tested_price,
        "z_failed_date":   z_failed_date,
        "z_max_runup_pct": z_max_runup,
        "z_days_tracked":  z_days,
        "z_drawdown_pct":  z_drawdown,
        "z_recovery_days": z_recovery,
    }


def find_all_supertrend_episodes(
    hist: pd.DataFrame,
    ema200: pd.Series,
    st_params: list,          # list of (period, multiplier) tuples
    retest_pct: float = 5.0,
    fail_pct: float = 8.0,
) -> list:
    """
    Runs find_supertrend_episodes for each (period, multiplier) pair in
    st_params and returns the combined flat list of all episode dicts.
    Duplicate (period, multiplier) pairs are skipped.
    """
    seen = set()
    all_episodes = []
    for period, multiplier in st_params:
        key = (int(period), float(multiplier))
        if key in seen:
            continue
        seen.add(key)
        try:
            eps = find_supertrend_episodes(
                hist, ema200,
                st_period=int(period),
                st_multiplier=float(multiplier),
                retest_pct=retest_pct,
                fail_pct=fail_pct,
            )
            all_episodes.extend(eps)
        except Exception as e:
            print(f"[supertrend_pattern] Error for ({period},{multiplier}): {e}")
    return all_episodes
