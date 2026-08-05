"""[source: 3]
dashboard_core.py
------------------
PILOT extraction — Phase 1 of the StrategyPage architecture refactor.

Contains every reusable, non-UI-flow-control piece the app needs: cached
data loaders, ledger loaders, and chart-builder functions. Every line below
is copied VERBATIM from the original appclaude.py (no calculation, caching,
or chart-generation logic has been altered) — only the module boundary is
new. This is what lets each strategy page (and the untouched legacy
all-in-one page) import the exact same functions instead of duplicating
them.

IMPORTANT — fetch_metrics() is a SHARED, single-pass computation: it
computes ALL seven strategies' ledgers together in one batch (one
bulk_refresh_histories() call + one per-symbol loop + one
levels_store.batch_upsert_all() at the end), driven by every strategy's
sidebar parameters at once. This means switching to a per-strategy
"only the active page's code runs" navigation model does NOT make this
specific step any lazier than it already was — it's wrapped in
@st.cache_data and was already only recomputed when its inputs changed or
the TTL expired, regardless of navigation style. The performance win from
st.navigation is in skipping the UI work (filtering, styling, dataframe
rendering, chart building) for the OTHER strategies you're not looking at,
not in this shared data pass.
"""

"""
Nifty Large-Cap Dashboard
--------------------------
Run locally with:   streamlit run app.py
Then open the browser tab it launches (usually http://localhost:8501).

Data source: Yahoo Finance via yfinance (free, no API key needed).
Symbol universe: Nifty 100 (large cap), auto-refreshed every quarter from NSE.
"""

import logging
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import yfinance as yf

from symbols_fetcher import get_symbols
import price_cache
import levels_store
import five_leg_pattern
import monthly_pivot_pattern
import monthly_s1_shift_pattern
import breakout_pullback_pattern
import ema_pullback_pattern
import supertrend_pattern
import range_breakout_pattern
import confluence_score
import fundamental_score

# Structured logging — messages go to the terminal where Streamlit is running.
# Using %(name)s lets each module's logger identify itself automatically.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_log = logging.getLogger("appclaude")

# --------------------------------------------------------------------------
# PAGE CONFIG

DATA_CACHE_TTL = 15 * 60  # 15 minutes - refresh prices without hammering Yahoo


# --------------------------------------------------------------------------
# DATA FETCHING
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def load_symbol_universe(force_refresh: bool):
    df, fetched_at, source = get_symbols(force_refresh=force_refresh)
    return df, fetched_at, source


def _current_streak(above_series: pd.Series):
    """Days in the current above/below-200EMA state, counting back from the last row."""
    current_state = bool(above_series.iloc[-1])
    days = 0
    for val in above_series.iloc[::-1]:
        if bool(val) == current_state:
            days += 1
        else:
            break
    return days, current_state


def _all_completed_streaks(
    hist: pd.DataFrame, above_series: pd.Series, ema200_series: pd.Series, min_days: int, retest_pct: float
):
    """
    Finds EVERY completed "above 200 EMA for >= min_days" streak in the full
    history (not just the most recent one). For each one, classifies its
    reference level (X) into one of FOUR states based on what's happened
    since the streak ended:

      naked                    - price has never come back near X at all.
      testing_resistance       - price came back near X from below, but the
                                  200 EMA has NOT climbed back above X yet -
                                  a genuine, still-undetermined resistance
                                  test (higher risk).
      reclaimed_pending_retest - the 200 EMA HAS climbed back above X (which,
                                  since the EMA is slow-moving, can only
                                  happen after price has already spent a long
                                  stretch trading above X) - but price hasn't
                                  yet pulled back down near X since reclaiming
                                  it. This is the live "watch for the retest
                                  entry" state - structurally bullish, entry
                                  not yet triggered.
      reclaimed_retested        - EMA has reclaimed X AND price has already
                                  pulled back down near X as support at least
                                  once since reclaiming - the classic
                                  breakout-then-retest setup has already
                                  played out.

    Each check requires a CONFIRMED move away from the threshold first
    (roughly 2x the retest band) before counting any subsequent move back as
    a genuine test/retest - otherwise a price sitting right at the boundary
    at the moment of transition would falsely register as an instant retest.

    Returns a list of dicts, oldest first, each with:
      streak_start, streak_end, x_price, days_to_x, days_from_x_to_end,
      total_streak_days, status, event_date, event_price,
      max_correction_pct, days_to_reclaim
    """
    groups = (above_series != above_series.shift()).cumsum()
    streaks = []
    for gid in groups.unique():
        idx = groups[groups == gid].index
        is_above = bool(above_series.loc[idx[0]])
        if not is_above or len(idx) < min_days:
            continue
        last_pos = hist.index.get_loc(idx[-1])
        if last_pos + 1 >= len(hist):
            continue  # streak hasn't actually ended yet (it's the current ongoing one)

        highs_in_streak = hist.loc[idx, "High"]
        x_price = float(highs_in_streak.max())
        x_date = highs_in_streak.idxmax()
        days_to_x = idx.get_loc(x_date) + 1
        total_streak_days = len(idx)
        days_from_x_to_end = total_streak_days - days_to_x
        streak_end = idx[-1]

        after = hist.loc[hist.index > streak_end]
        ema_after = ema200_series.loc[hist.index > streak_end]

        reclaim_mask = ema_after >= x_price
        event_date, event_price = None, None

        # How deep did price correct from X, and how long did it take the
        # 200 EMA to reclaim X? Computed over the window from streak_end
        # through the reclaim date (if the EMA has reclaimed X) or through
        # "now" (if it hasn't yet - a provisional, still-updating figure).
        # This is useful context regardless of status: it tells you how
        # sharp/prolonged the correction was, which can inform a stop-loss
        # or target expectation for a retest of this level.
        window_end_for_correction = (
            reclaim_mask[reclaim_mask].index[0] if reclaim_mask.any() else hist.index[-1]
        )
        lowest_price_in_window = float(hist.loc[streak_end:window_end_for_correction, "Low"].min())
        max_correction_pct = (x_price - lowest_price_in_window) / x_price * 100
        days_to_reclaim = (
            (window_end_for_correction - streak_end).days if reclaim_mask.any() else None
        )

        retest_drawdown_pct = retest_days_to_recover = None

        if reclaim_mask.any():
            reclaim_date = reclaim_mask[reclaim_mask].index[0]
            confirm_above = x_price * (1 + 2 * retest_pct / 100.0)
            support_threshold = x_price * (1 + retest_pct / 100.0)
            after_reclaim = hist.loc[hist.index > reclaim_date]
            ran_up_mask = after_reclaim["High"] >= confirm_above
            if ran_up_mask.any():
                first_run_up = ran_up_mask[ran_up_mask].index[0]
                after_run = hist.loc[hist.index > first_run_up, "Low"]
                support_mask = after_run <= support_threshold
                if support_mask.any():
                    event_date = support_mask[support_mask].index[0]
                    event_price = float(hist.loc[event_date, "Close"])
                    status = "reclaimed_retested"
                    # How much further did price dip BELOW X during this
                    # retest before recovering back above it, and how long
                    # did that recovery take?
                    dd = monthly_pivot_pattern.compute_post_event_drawdown(hist, x_price, event_date)
                    retest_drawdown_pct = dd["max_drawdown_pct"]
                    retest_days_to_recover = dd["days_to_recover"]
                else:
                    status = "reclaimed_pending_retest"
            else:
                status = "reclaimed_pending_retest"
        else:
            test_threshold = x_price * (1 - retest_pct / 100.0)
            confirm_below = x_price * (1 - 2 * retest_pct / 100.0)
            below_mask = after["Close"] < confirm_below
            if below_mask.any():
                first_confirmed_break = below_mask[below_mask].index[0]
                after_dip = hist.loc[hist.index > first_confirmed_break, "High"]
                resist_mask = after_dip >= test_threshold
                if resist_mask.any():
                    event_date = resist_mask[resist_mask].index[0]
                    event_price = float(hist.loc[event_date, "Close"])
                    status = "testing_resistance"
                else:
                    status = "naked"
            else:
                status = "naked"

        streaks.append(
            {
                "streak_start": idx[0],
                "streak_end": streak_end,
                "x_price": x_price,
                "days_to_x": int(days_to_x),
                "days_from_x_to_end": int(days_from_x_to_end),
                "total_streak_days": int(total_streak_days),
                "status": status,
                "tested_date": event_date,
                "tested_price": event_price,
                "max_correction_pct": max_correction_pct,
                "days_to_reclaim": days_to_reclaim,
                "retest_drawdown_pct": retest_drawdown_pct,
                "retest_days_to_recover": retest_days_to_recover,
            }
        )
    return streaks


def _worst_retest_drawdown(hist: pd.DataFrame, level_pairs: list):
    """
    For a list of (label, price, tested_date) tuples — one per retested level —
    compute post-event drawdown for each and return (drawdown_pct, days_to_recover,
    label) for the WORST case (deepest drawdown). Returns (None, None, None) when
    the list is empty. Used by all strategy blocks to eliminate duplicated drawdown
    aggregation logic.
    """
    if not level_pairs:
        return None, None, None
    candidates = []
    for label, price, tested_date in level_pairs:
        dd = monthly_pivot_pattern.compute_post_event_drawdown(hist, price, tested_date)
        candidates.append((label, dd))
    label, worst = max(candidates, key=lambda c: c[1]["max_drawdown_pct"] or 0)
    return worst["max_drawdown_pct"], worst["days_to_recover"], label


@st.cache_data(show_spinner=False, ttl=DATA_CACHE_TTL)
def fetch_metrics(
    symbols: list,
    min_streak_days: int = 200,
    retest_pct: float = 5.0,
    min_leg_days: int = 5,
    min_qualify_months: int = 2,
    fail_pct: float = 8.0,
    min_qualify_days: int = 50,
    st_params_list: list = None,
    range_breakout_retest_pct: float = 5.0,
    range_breakout_fail_pct: float = 8.0,
):
    """
    Pulls each symbol's full growing local price history (see price_cache.py),
    derives all dashboard metrics, finds every historical streak-high
    reference level (persisting them to the local ledger via levels_store),
    and returns a tidy DataFrame, one row per stock, using the MOST RECENT
    completed streak for the main table's "Retest Level (X)" column.
    """
    if st_params_list is None:
        st_params_list = [(7, 3.0)]
    
    rows = []

    # Collects one dict per symbol with every strategy's ledger rows, so a
    # SINGLE levels_store.batch_upsert_all() call at the end of this function
    # writes everything in one SQLite transaction/connection instead of the
    # 7 separate per-symbol upsert_*_episodes() calls (each opening/closing
    # its own connection) that used to run inline in the loop below.
    ledger_batch = []

    # Batch-refresh every symbol's local price cache in a small number of
    # threaded network calls, instead of one sequential HTTP round-trip per
    # symbol inside the loop below. This is the single biggest lever for
    # "first load" time - the per-symbol get_full_history() calls that
    # follow will find the cache already fresh and do no network I/O at all.
    price_cache.bulk_refresh_histories(symbols)

    for sym in symbols:
        try:
            hist = price_cache.get_full_history(sym)
            hist = hist.dropna(how="all")
            if hist.empty or "Close" not in hist.columns:
                continue
            hist = hist.dropna(subset=["Close"])
            if len(hist) < 220:
                continue

            close = hist["Close"]
            vol = hist["Volume"]

            last_price = float(close.iloc[-1])
            prev_close = float(close.iloc[-2]) if len(close) > 1 else last_price
            day_change_pct = (last_price - prev_close) / prev_close * 100 if prev_close else np.nan

            window_52w = close.tail(252)
            high_52w = float(window_52w.max())
            low_52w = float(window_52w.min())
            pct_from_high = (last_price - high_52w) / high_52w * 100
            pct_from_low = (last_price - low_52w) / low_52w * 100

            # Compute all EMA series once and attach to hist so strategy
            # detectors and chart functions can reuse them without
            # recalculating from scratch.
            hist["EMA20"]  = close.ewm(span=20,  adjust=False).mean()
            hist["EMA50"]  = close.ewm(span=50,  adjust=False).mean()
            hist["EMA200"] = close.ewm(span=200, adjust=False).mean()
            ema20_series  = hist["EMA20"]
            ema50_series  = hist["EMA50"]
            ema200_series = hist["EMA200"]
            ema200 = float(ema200_series.iloc[-1])
            pct_from_ema200 = (last_price - ema200) / ema200 * 100

            ledger_episode_rows = None  # five-leg rows for this symbol; None if detection errors below
            try:
                five_leg_episodes = five_leg_pattern.find_five_leg_episodes(
                    hist, ema20_series, ema50_series, ema200_series, min_leg_days=min_leg_days
                )
                TERMINAL_FIVE_LEG_STATUSES = {"probe_complete", "above_200_complete"}
                ledger_episode_rows = []
                for ep in five_leg_episodes:
                    x_retest_status = x_tested_date = x_tested_price = None
                    y_retest_status = y_tested_date = y_tested_price = None
                    retest_drawdown_pct = retest_days_to_recover = retest_drawdown_level = None
                    if ep["status"] in TERMINAL_FIVE_LEG_STATUSES:
                        x_retest_status, x_tested_date, x_tested_price = monthly_pivot_pattern.classify_retest(
                            hist, ep["x_price"], ep["completion_date"], retest_pct=retest_pct
                        )
                        y_retest_status, y_tested_date, y_tested_price = monthly_pivot_pattern.classify_retest(
                            hist, ep["y_price"], ep["completion_date"], retest_pct=retest_pct
                        )
                        # Report the worst drawdown across all retested levels.
                        _pairs = []
                        if x_retest_status == "tested":
                            _pairs.append(("X", ep["x_price"], x_tested_date))
                        if y_retest_status == "tested":
                            _pairs.append(("Y", ep["y_price"], y_tested_date))
                        retest_drawdown_pct, retest_days_to_recover, retest_drawdown_level = (
                            _worst_retest_drawdown(hist, _pairs)
                        )
                    ledger_episode_rows.append(
                        {
                            "leg1_start": ep["leg1_start"].strftime("%Y-%m-%d"),
                            "qualified_date": ep["qualified_date"].strftime("%Y-%m-%d"),
                            "probe_date": ep["probe_date"].strftime("%Y-%m-%d") if ep["probe_date"] is not None else None,
                            "x_price": ep["x_price"],
                            "y_price": ep["y_price"],
                            "num_legs_observed": ep["num_legs_observed"],
                            "status": ep["status"],
                            "x_retest_status": x_retest_status,
                            "x_tested_date": x_tested_date.strftime("%Y-%m-%d") if x_tested_date is not None else None,
                            "x_tested_price": x_tested_price,
                            "y_retest_status": y_retest_status,
                            "y_tested_date": y_tested_date.strftime("%Y-%m-%d") if y_tested_date is not None else None,
                            "y_tested_price": y_tested_price,
                            "retest_drawdown_pct": retest_drawdown_pct,
                            "retest_days_to_recover": retest_days_to_recover,
                            "retest_drawdown_level": retest_drawdown_level,
                        }
                    )
                # Row-building succeeded; hand off to the batched writer at
                # the end of this function instead of upserting inline here
                # (see levels_store.batch_upsert_all).
            except Exception as e:
                ledger_episode_rows = None
                _log.warning("[five_leg_pattern] Skipping %s", sym, exc_info=True)

            pivot_ledger_rows = None  # monthly-pivot rows for this symbol; None if detection errors below
            try:
                pivots = monthly_pivot_pattern.compute_monthly_pivots(hist)
                s1_series = pivots["S1"]
                pivot_episodes = monthly_pivot_pattern.find_pivot_episodes(
                    hist, s1_series, ema200_series, min_qualify_months=min_qualify_months
                )
                pivot_ledger_rows = []
                for ep in pivot_episodes:
                    x_retest_status = x_tested_date = x_tested_price = None
                    y_retest_status = y_tested_date = y_tested_price = None
                    retest_drawdown_pct = retest_days_to_recover = retest_drawdown_level = None
                    if ep["status"] == "complete":
                        x_retest_status, x_tested_date, x_tested_price = monthly_pivot_pattern.classify_retest(
                            hist, ep["x_price"], ep["y_fix_date"], retest_pct=retest_pct
                        )
                        y_retest_status, y_tested_date, y_tested_price = monthly_pivot_pattern.classify_retest(
                            hist, ep["y_price"], ep["y_fix_date"], retest_pct=retest_pct
                        )
                        _pairs = []
                        if x_retest_status == "tested":
                            _pairs.append(("X", ep["x_price"], x_tested_date))
                        if y_retest_status == "tested":
                            _pairs.append(("Y", ep["y_price"], y_tested_date))
                        retest_drawdown_pct, retest_days_to_recover, retest_drawdown_level = (
                            _worst_retest_drawdown(hist, _pairs)
                        )
                    pivot_ledger_rows.append(
                        {
                            "episode_start": ep["episode_start"].strftime("%Y-%m-%d"),
                            "qualify_end_date": ep["qualify_end_date"].strftime("%Y-%m-%d"),
                            "x_price": ep["x_price"],
                            "x_fix_date": ep["x_fix_date"].strftime("%Y-%m-%d") if ep["x_fix_date"] is not None else None,
                            "y_price": ep["y_price"],
                            "y_fix_date": ep["y_fix_date"].strftime("%Y-%m-%d") if ep["y_fix_date"] is not None else None,
                            "status": ep["status"],
                            "x_retest_status": x_retest_status,
                            "x_tested_date": x_tested_date.strftime("%Y-%m-%d") if x_tested_date is not None else None,
                            "x_tested_price": x_tested_price,
                            "y_retest_status": y_retest_status,
                            "y_tested_date": y_tested_date.strftime("%Y-%m-%d") if y_tested_date is not None else None,
                            "y_tested_price": y_tested_price,
                            "retest_drawdown_pct": retest_drawdown_pct,
                            "retest_days_to_recover": retest_days_to_recover,
                            "retest_drawdown_level": retest_drawdown_level,
                        }
                    )
                # Row-building succeeded; hand off to the batched writer at
                # the end of this function instead of upserting inline here.
            except Exception as e:
                pivot_ledger_rows = None
                _log.warning("[monthly_pivot_pattern] Skipping %s", sym, exc_info=True)

            s1_shift_ledger_rows = None  # s1-shift rows for this symbol; None if detection errors below
            try:
                s1_shift_episodes = monthly_s1_shift_pattern.find_s1_shift_episodes(
                    hist, retest_pct=retest_pct, fail_pct=fail_pct
                )
                s1_shift_ledger_rows = []
                for ep in s1_shift_episodes:
                    post_drawdown_pct = post_days_to_recover = None
                    event_date = ep["tested_date"] if ep["status"] == "tested" else (
                        ep["failed_date"] if ep["status"] == "failed" else None
                    )
                    if event_date is not None:
                        dd = monthly_pivot_pattern.compute_post_event_drawdown(hist, ep["x_price"], event_date)
                        post_drawdown_pct = dd["max_drawdown_pct"]
                        post_days_to_recover = dd["days_to_recover"]
                    s1_shift_ledger_rows.append(
                        {
                            "month": str(ep["month"]),
                            "x_price": ep["x_price"],
                            "x_date": ep["x_date"].strftime("%Y-%m-%d"),
                            "anchor_date": ep["anchor_date"].strftime("%Y-%m-%d"),
                            "s1_month": ep["s1_month"],
                            "s1_next_month": ep["s1_next_month"],
                            "status": ep["status"],
                            "tested_date": ep["tested_date"].strftime("%Y-%m-%d") if ep["tested_date"] is not None else None,
                            "tested_price": ep["tested_price"],
                            "failed_date": ep["failed_date"].strftime("%Y-%m-%d") if ep["failed_date"] is not None else None,
                            "max_runup_pct": ep["max_runup_pct"],
                            "days_tracked": ep["days_tracked"],
                            "post_event_drawdown_pct": post_drawdown_pct,
                            "post_event_days_to_recover": post_days_to_recover,
                        }
                    )
                # Row-building succeeded; hand off to the batched writer at
                # the end of this function instead of upserting inline here.
            except Exception as e:
                s1_shift_ledger_rows = None
                _log.warning("[monthly_s1_shift_pattern] Skipping %s", sym, exc_info=True)

            bp_ledger_rows = None  # breakout-pullback rows for this symbol; None if detection errors below
            try:
                bp_episodes = breakout_pullback_pattern.find_breakout_pullback_episodes(
                    hist, ema20_series, ema50_series, ema200_series,
                    retest_pct=retest_pct, fail_pct=fail_pct
                )
                bp_ledger_rows = []
                for ep in bp_episodes:
                    bp_ledger_rows.append(
                        {
                            "leg1_start": ep["leg1_start"].strftime("%Y-%m-%d"),
                            "leg2_start": ep["leg2_start"].strftime("%Y-%m-%d") if ep["leg2_start"] else None,
                            "leg3_start": ep["leg3_start"].strftime("%Y-%m-%d") if ep["leg3_start"] else None,
                            "leg4_start": ep["leg4_start"].strftime("%Y-%m-%d") if ep["leg4_start"] else None,
                            "signal_date": ep["signal_date"].strftime("%Y-%m-%d") if ep["signal_date"] else None,
                            "x_price": ep["x_price"],
                            "y_price": ep["y_price"],
                            "z_price": ep["z_price"],
                            "leg1_low_price": ep["leg1_low_price"],
                            "status": ep["status"],
                            "y_retest_status": ep["y_retest_status"],
                            "z_retest_status": ep["z_retest_status"],
                            "y_tested_date": ep["y_tested_date"].strftime("%Y-%m-%d") if ep["y_tested_date"] else None,
                            "y_tested_price": ep["y_tested_price"],
                            "z_tested_date": ep["z_tested_date"].strftime("%Y-%m-%d") if ep["z_tested_date"] else None,
                            "z_tested_price": ep["z_tested_price"],
                            "failed_date": ep["failed_date"].strftime("%Y-%m-%d") if ep["failed_date"] else None,
                            "max_runup_pct": ep["max_runup_pct"],
                            "days_tracked": ep["days_tracked"],
                            "post_event_drawdown_pct": ep["post_event_drawdown_pct"],
                            "post_event_days_to_recover": ep["post_event_days_to_recover"],
                        }
                    )
                # Row-building succeeded; hand off to the batched writer at
                # the end of this function instead of upserting inline here.
            except Exception as e:
                bp_ledger_rows = None
                _log.warning("[breakout_pullback_pattern] Skipping %s", sym, exc_info=True)

            ep_ledger_rows = None  # EMA-pullback rows for this symbol; None if detection errors below
            try:
                ep_episodes = ema_pullback_pattern.find_ema_pullback_episodes(
                    hist, ema20_series, ema50_series,
                    min_qualify_days=int(min_qualify_days),
                    retest_pct=retest_pct, fail_pct=fail_pct,
                )
                ep_ledger_rows = []
                for ep in ep_episodes:
                    ep_ledger_rows.append({
                        "crossover_date":   ep["crossover_date"].strftime("%Y-%m-%d"),
                        "qualify_end_date": ep["qualify_end_date"].strftime("%Y-%m-%d") if ep["qualify_end_date"] else None,
                        "touch_date":       ep["touch_date"].strftime("%Y-%m-%d") if ep["touch_date"] else None,
                        "x_price":          ep["x_price"],
                        "y_price":          ep["y_price"],
                        "y_fix_date":       ep["y_fix_date"].strftime("%Y-%m-%d") if ep["y_fix_date"] else None,
                        "status":           ep["status"],
                        "tested_date":      ep["tested_date"].strftime("%Y-%m-%d") if ep["tested_date"] else None,
                        "tested_price":     ep["tested_price"],
                        "failed_date":      ep["failed_date"].strftime("%Y-%m-%d") if ep["failed_date"] else None,
                        "max_runup_pct":    ep["max_runup_pct"],
                        "days_tracked":     ep["days_tracked"],
                        "post_event_drawdown_pct":    ep["post_event_drawdown_pct"],
                        "post_event_days_to_recover": ep["post_event_days_to_recover"],
                    })
                # Row-building succeeded; hand off to the batched writer at
                # the end of this function instead of upserting inline here.
            except Exception as e:
                ep_ledger_rows = None
                _log.warning("[ema_pullback_pattern] Skipping %s", sym, exc_info=True)

            st_ledger_rows = None  # supertrend rows for this symbol; None if detection errors below
            try:
                st_episodes = supertrend_pattern.find_all_supertrend_episodes(
                    hist, ema200_series,
                    st_params=st_params_list,
                    retest_pct=retest_pct,
                    fail_pct=fail_pct,
                )
                st_ledger_rows = []
                for ep in st_episodes:
                    # Use the module-level _ts() helper defined below (avoids
                    # shadowing it with a per-iteration function definition).
                    st_ledger_rows.append({
                        "phase1_start":   _ts(ep["phase1_start"]),
                        "st_period":      ep["st_period"],
                        "st_multiplier":  ep["st_multiplier"],
                        "phase1_end":     _ts(ep.get("phase1_end")),
                        "x_price":        ep.get("x_price"),
                        "x_date":         _ts(ep.get("x_date")),
                        "phase2_start":   _ts(ep.get("phase2_start")),
                        "y_price":        ep.get("y_price"),
                        "y_date":         _ts(ep.get("y_date")),
                        "z_price":        ep.get("z_price"),
                        "z_date":         _ts(ep.get("z_date")),
                        "phase3_start":   _ts(ep.get("phase3_start")),
                        "signal_date":    _ts(ep.get("signal_date")),
                        "x_cleared_date": _ts(ep.get("x_cleared_date")),
                        "status":         ep.get("status"),
                        "x_status":       ep.get("x_status"),
                        "x_tested_date":  _ts(ep.get("x_tested_date")),
                        "x_tested_price": ep.get("x_tested_price"),
                        "x_failed_date":  _ts(ep.get("x_failed_date")),
                        "x_max_runup_pct":ep.get("x_max_runup_pct"),
                        "x_days_tracked": ep.get("x_days_tracked"),
                        "x_drawdown_pct": ep.get("x_drawdown_pct"),
                        "x_recovery_days":ep.get("x_recovery_days"),
                        "y_status":       ep.get("y_status"),
                        "y_tested_date":  _ts(ep.get("y_tested_date")),
                        "y_tested_price": ep.get("y_tested_price"),
                        "y_failed_date":  _ts(ep.get("y_failed_date")),
                        "y_max_runup_pct":ep.get("y_max_runup_pct"),
                        "y_days_tracked": ep.get("y_days_tracked"),
                        "y_drawdown_pct": ep.get("y_drawdown_pct"),
                        "y_recovery_days":ep.get("y_recovery_days"),
                        "z_status":       ep.get("z_status"),
                        "z_tested_date":  _ts(ep.get("z_tested_date")),
                        "z_tested_price": ep.get("z_tested_price"),
                        "z_failed_date":  _ts(ep.get("z_failed_date")),
                        "z_max_runup_pct":ep.get("z_max_runup_pct"),
                        "z_days_tracked": ep.get("z_days_tracked"),
                        "z_drawdown_pct": ep.get("z_drawdown_pct"),
                        "z_recovery_days":ep.get("z_recovery_days"),
                    })
                # Row-building succeeded; hand off to the batched writer at
                # the end of this function instead of upserting inline here.
            except Exception as e:
                st_ledger_rows = None
                _log.warning("[supertrend_pattern] Skipping %s", sym, exc_info=True)

            # --- Range Breakout (5-Leg) Pattern ---
            range_breakout_rows = None
            try:
                rb_episodes = range_breakout_pattern.find_range_breakout_episodes(
                    hist,
                    retest_pct=range_breakout_retest_pct,
                    fail_pct=range_breakout_fail_pct,
                )
                range_breakout_rows = []
                for ep in rb_episodes:
                    range_breakout_rows.append({
                        "leg1_start": _ts(ep["leg1_start"]),
                        "leg1_end": _ts(ep.get("leg1_end")),
                        "leg2_start": _ts(ep.get("leg2_start")),
                        "leg2_end": _ts(ep.get("leg2_end")),
                        "leg3_start": _ts(ep.get("leg3_start")),
                        "leg3_end": _ts(ep.get("leg3_end")),
                        "leg4_start": _ts(ep.get("leg4_start")),
                        "leg4_end": _ts(ep.get("leg4_end")),
                        "leg5_start": _ts(ep.get("leg5_start")),
                        "leg5_end": _ts(ep.get("leg5_end")),
                        "leg1_high": ep.get("leg1_high"),
                        "leg2_low_pivot": ep.get("leg2_low_pivot"),
                        "leg2_low_price": ep.get("leg2_low_price"),
                        "leg3_max_pivot": ep.get("leg3_max_pivot"),
                        "leg4_min_pivot": ep.get("leg4_min_pivot"),
                        "leg4_min_low": ep.get("leg4_min_low"),
                        "leg5_last_pivot": ep.get("leg5_last_pivot"),
                        "leg5_max_pivot": ep.get("leg5_max_pivot"),
                        "status": ep.get("status"),
                        "pattern_type": ep.get("pattern_type"),
                        "breakout_confirmed": 1 if ep.get("breakout_confirmed") else 0,
                        "is_ongoing": 1 if ep.get("is_ongoing") else 0,
                    })
            except Exception as e:
                range_breakout_rows = None
                _log.warning("[range_breakout_pattern] Skipping %s", sym, exc_info=True)

            above_series = close > ema200_series
            days_in_state, currently_above = _current_streak(above_series)
            trend_days_signed = days_in_state if currently_above else -days_in_state

            all_streaks = _all_completed_streaks(hist, above_series, ema200_series, min_streak_days, retest_pct)

            # Persist every completed streak to the local ledger, whatever its status.
            ledger_rows = [
                {
                    **s,
                    "streak_start": s["streak_start"].strftime("%Y-%m-%d"),
                    "streak_end": s["streak_end"].strftime("%Y-%m-%d"),
                    "tested_date": s["tested_date"].strftime("%Y-%m-%d") if s["tested_date"] is not None else None,
                }
                for s in all_streaks
            ]

            # Collect this symbol's rows from all 7 strategies into one dict
            # for the single batched write at the end of the full-universe
            # loop, instead of the 7 separate per-symbol upsert_*() calls
            # (each opening/closing its own SQLite connection) this used to
            # do inline. `None` for any strategy that errored above is
            # handled by batch_upsert_all (that strategy's rows are simply
            # skipped for this symbol this run, same as before).
            ledger_batch.append({
                "symbol": sym,
                "streak_rows": ledger_rows,
                "five_leg_rows": ledger_episode_rows,
                "pivot_rows": pivot_ledger_rows,
                "s1_shift_rows": s1_shift_ledger_rows,
                "breakout_pullback_rows": bp_ledger_rows,
                "ema_pullback_rows": ep_ledger_rows,
                "supertrend_rows": st_ledger_rows,
                "range_breakout_rows": range_breakout_rows,
            })

            # Main table keeps showing the MOST RECENT completed streak, for continuity.
            latest = all_streaks[-1] if all_streaks else None
            if latest:
                streak_high_x = latest["x_price"]
                streak_end = latest["streak_end"]
                days_to_x = latest["days_to_x"]
                days_from_x_to_end = latest["days_from_x_to_end"]
                total_streak_days = latest["total_streak_days"]
                streak_status = latest["status"]
                max_correction_pct = latest["max_correction_pct"]
                days_to_reclaim = latest["days_to_reclaim"]
            else:
                streak_high_x = streak_end = days_to_x = days_from_x_to_end = total_streak_days = None
                streak_status = None
                max_correction_pct = days_to_reclaim = None

            pct_from_x = (
                (last_price - streak_high_x) / streak_high_x * 100
                if streak_high_x
                else np.nan
            )

            # "Unresolved" = the specific outcome we're waiting on hasn't happened yet:
            # naked (never approached), testing_resistance (outcome undetermined), or
            # reclaimed_pending_retest (structurally bullish, retest entry not yet fired).
            # Only reclaimed_retested is "done" - that setup has already played out.
            UNRESOLVED_STATUSES = {"naked", "testing_resistance", "reclaimed_pending_retest"}
            unresolved = [s for s in all_streaks if s["status"] in UNRESOLVED_STATUSES]
            if unresolved:
                nearest_unresolved = min(unresolved, key=lambda s: abs(last_price - s["x_price"]) / s["x_price"])
                nearest_naked_x = nearest_unresolved["x_price"]
                nearest_naked_pct = (last_price - nearest_naked_x) / nearest_naked_x * 100
                nearest_naked_end = nearest_unresolved["streak_end"]
                nearest_naked_status = nearest_unresolved["status"]
            else:
                nearest_naked_x = nearest_naked_pct = nearest_naked_end = nearest_naked_status = None

            last_volume = float(vol.iloc[-1])
            avg_vol_20d = float(vol.tail(20).mean())
            rel_volume = last_volume / avg_vol_20d if avg_vol_20d else np.nan

            rows.append(
                {
                    "Symbol": sym,
                    "Price": last_price,
                    "DayChg%": day_change_pct,
                    "52W High": high_52w,
                    "52W Low": low_52w,
                    "%FromHigh": pct_from_high,
                    "%FromLow": pct_from_low,
                    "200EMA": ema200,
                    "%From200EMA": pct_from_ema200,
                    "TrendDays": trend_days_signed,
                    "CurrentlyAbove200EMA": currently_above,
                    "StreakHighX": streak_high_x,
                    "StreakEndDate": streak_end,
                    "DaysToX": days_to_x,
                    "DaysFromXToEnd": days_from_x_to_end,
                    "StreakTotalDays": total_streak_days,
                    "StreakStatus": streak_status,
                    "MaxCorrectionPct": max_correction_pct,
                    "DaysToReclaim": days_to_reclaim,
                    "%FromX": pct_from_x,
                    "NearestNakedX": nearest_naked_x,
                    "%FromNearestNakedX": nearest_naked_pct,
                    "NearestNakedEnd": nearest_naked_end,
                    "NearestNakedStatus": nearest_naked_status,
                    "NumUnresolvedLevels": len(unresolved),
                    "NumTotalStreaks": len(all_streaks),
                    "Volume": last_volume,
                    "AvgVol20D": avg_vol_20d,
                    "RelVolume": rel_volume,
                }
            )
        except Exception as e:
            _log.warning("[fetch_metrics] Skipping %s", sym, exc_info=True)
            continue

    # Single batched write for every strategy x every symbol, in one SQLite
    # transaction/connection (see levels_store.batch_upsert_all). This
    # replaces what used to be up to 7 x len(symbols) individual
    # upsert_*_episodes() calls, each opening and closing its own
    # connection inline inside the loop above.
    if ledger_batch:
        try:
            levels_store.batch_upsert_all(ledger_batch)
        except Exception as e:
            _log.error("[fetch_metrics] batch_upsert_all failed", exc_info=True)

    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False, ttl=DATA_CACHE_TTL)
def fetch_history_for_chart(symbol: str, period="2y"):
    hist = price_cache.get_full_history(symbol)
    if hist.empty:
        return hist
    hist["EMA200"] = hist["Close"].ewm(span=200, adjust=False).mean()
    hist["EMA50"] = hist["Close"].ewm(span=50, adjust=False).mean()
    if period != "max":
        n_days = {"6mo": 126, "1y": 252, "2y": 504, "5y": 1260, "10y": 2520}.get(period)
        if n_days:
            hist = hist.tail(n_days)
    return hist


@st.cache_data(show_spinner=False, ttl=DATA_CACHE_TTL)
def fetch_full_history_with_indicators(symbol: str):
    """
    Returns the full price history for a symbol with all EMAs and the
    monthly pivot S1 step-function precomputed. Used by the per-setup
    click-to-chart functions so each chart can overlay exactly the right
    indicators without re-downloading data.
    """
    hist = price_cache.get_full_history(symbol)
    if hist.empty:
        return hist
    hist = hist.dropna(subset=["Close"])
    close = hist["Close"]
    hist["EMA20"]  = close.ewm(span=20,  adjust=False).mean()
    hist["EMA50"]  = close.ewm(span=50,  adjust=False).mean()
    hist["EMA200"] = close.ewm(span=200, adjust=False).mean()
    # Monthly Pivot S1 step function (prior month's H/L/C, held flat intra-month)
    pivots = monthly_pivot_pattern.compute_monthly_pivots(hist)
    hist["PivotS1"] = pivots["S1"]
    return hist


def _trim_hist(hist: pd.DataFrame, period: str) -> pd.DataFrame:
    """Trim to the requested lookback while keeping enough leading data for
    EMA warm-up (the full history was used for computation; we only cut display)."""
    n_days = {"6mo": 126, "1y": 252, "2y": 504, "5y": 1260, "10y": 2520}.get(period)
    if n_days and len(hist) > n_days:
        return hist.tail(n_days)
    return hist


def _ts(dt) -> str:
    """Convert any date-like value to an ISO-8601 string (YYYY-MM-DD)."""
    if dt is None:
        return None
    try:
        return pd.Timestamp(dt).strftime("%Y-%m-%d")
    except Exception:
        return None


def _add_vline(fig: go.Figure, dt, label: str, color: str, line_width: int = 1, y_pos: float = 1.0):
    """
    Plotly's add_vline(..., annotation_text=...) computes _mean([x, x]) via
    Python's built-in sum(), which starts from integer 0.  That means:
      sum([Timestamp, Timestamp])  →  TypeError: int + Timestamp  (pandas 2+)
      sum(["2024-01-01", "2024-01-01"])  →  TypeError: int + str
    Neither date strings nor Timestamps work when an annotation is attached.

    The fix: call add_vline WITHOUT annotation_text (just the shape), then
    add the label as a separate add_annotation which never calls _mean at all.

    y_pos: vertical position in paper coordinates (0=bottom, 1=top). Use
    staggered values (1.0, 0.96, 0.92, ...) when multiple vlines are close
    together to prevent label overlap.
    """
    x_str = _ts(dt)
    if x_str is None:
        return
    fig.add_vline(x=x_str, line_dash="dash", line_color=color, line_width=line_width)
    fig.add_annotation(
        x=x_str, y=y_pos, yref="paper", yanchor="top",
        text=label, showarrow=False,
        font=dict(size=10, color=color),
        bgcolor="rgba(255,255,255,0.7)",
        bordercolor=color, borderwidth=1,
        xanchor="left",
    )


def _add_vrect(fig: go.Figure, x0, x1, fillcolor: str, label: str = "",
               border_color: str = "rgba(0,0,0,0)", border_width: float = 0):
    """
    Same Plotly annotation bug applies to add_vrect when annotation_text is
    set — _mean([x0, x1]) fails for both Timestamps and strings.
    Draw the rect without annotation, add label via add_annotation at x0.
    """
    x0_str = _ts(x0)
    x1_str = _ts(x1)
    if x0_str is None or x1_str is None:
        return
    fig.add_vrect(x0=x0_str, x1=x1_str,
                  fillcolor=fillcolor,
                  line_color=border_color, line_width=border_width)
    if label:
        fig.add_annotation(
            x=x0_str, y=0.98, yref="paper", yanchor="top",
            text=label, showarrow=False,
            font=dict(size=9, color="#555"),
            bgcolor="rgba(255,255,255,0.6)",
            xanchor="left",
        )


# ---------------------------------------------------------------------------
# CHART BUILDERS — one per setup, each returns a go.Figure ready to display
# ---------------------------------------------------------------------------

def _base_candle_fig(hist: pd.DataFrame, symbol: str, height: int = 560) -> go.Figure:
    """Shared candlestick + axis layout used by every setup chart."""
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=hist.index, open=hist["Open"], high=hist["High"],
        low=hist["Low"], close=hist["Close"],
        name="Price", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
    ))
    fig.update_layout(
        height=height,
        margin=dict(l=70, r=20, t=36, b=10),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis=dict(tickformat=",.2f", tickprefix="₹", automargin=True),
        hovermode="x unified",
    )
    return fig


def build_streak_chart(hist: pd.DataFrame, symbol: str, row: pd.Series, period: str) -> go.Figure:
    """EMA Streak setup chart — 50 EMA + 200 EMA + all unresolved X levels."""
    h = _trim_hist(hist, period)
    fig = _base_candle_fig(h, symbol)
    fig.add_trace(go.Scatter(x=h.index, y=h["EMA200"], mode="lines", name="200 EMA",
                             line=dict(color="orange", width=1.5)))
    fig.add_trace(go.Scatter(x=h.index, y=h["EMA50"], mode="lines", name="50 EMA",
                             line=dict(color="#5b9bd5", width=1)))

    STATUS_LINE_COLORS = {
        "naked": "#999999",
        "testing_resistance": "#e08300",
        "reclaimed_pending_retest": "#1a7f37",
        "reclaimed_retested": "#2e86c1",
    }
    STATUS_LABELS = {
        "naked": "⚪ naked",
        "testing_resistance": "🟠 testing resistance",
        "reclaimed_pending_retest": "🟢 reclaimed, pending retest",
        "reclaimed_retested": "🔵 reclaimed & retested",
    }
    if pd.notna(row["StreakHighX"]):
        line_color = STATUS_LINE_COLORS.get(row["StreakStatus"], "#8e44ad")
        status_note = STATUS_LABELS.get(row["StreakStatus"], "")
        fig.add_hline(
            y=row["StreakHighX"], line_dash="dot", line_color=line_color,
            annotation_text=f"Latest X = ₹{row['StreakHighX']:.2f} · {status_note}",
            annotation_position="top left",
        )
    # All other unresolved levels for this symbol
    ledger_all = levels_store.get_all_levels()
    if not ledger_all.empty:
        others = ledger_all[
            (ledger_all["symbol"] == symbol)
            & (ledger_all["status"] != "reclaimed_retested")
            & (ledger_all["x_price"] != row.get("StreakHighX", None))
        ]
        for _, lvl in others.iterrows():
            lvl_label = STATUS_LABELS.get(lvl["status"], lvl["status"])
            fig.add_hline(
                y=lvl["x_price"], line_dash="dash",
                line_color=STATUS_LINE_COLORS.get(lvl["status"], "#16a085"),
                line_width=1,
                annotation_text=f"Older X = ₹{lvl['x_price']:.2f} · streak ended {lvl['streak_end']} · {lvl_label}",
                annotation_position="bottom left",
            )
    return fig


def build_ledger_level_chart(hist: pd.DataFrame, symbol: str, level_row: pd.Series, period: str) -> go.Figure:
    """
    Chart for a SINGLE selected row from the Reference Level Ledger (not
    necessarily the symbol's most recent streak - the ledger holds every
    completed streak ever recorded, any age).

    Reuses the same candle + 50/200 EMA base as build_streak_chart(), but
    keys off the ledger row's own schema (x_price / status / streak_end)
    rather than the main table's row schema (StreakHighX / StreakStatus),
    since the two are different DataFrames with different column names.

    The selected level is drawn as a solid, clearly-labeled line; every
    OTHER unresolved level recorded for this symbol is drawn thinner/dashed
    for context, exactly as build_streak_chart() does for the main table.
    """
    h = _trim_hist(hist, period)
    fig = _base_candle_fig(h, symbol)
    fig.add_trace(go.Scatter(x=h.index, y=h["EMA200"], mode="lines", name="200 EMA",
                             line=dict(color="orange", width=1.5)))
    fig.add_trace(go.Scatter(x=h.index, y=h["EMA50"], mode="lines", name="50 EMA",
                             line=dict(color="#5b9bd5", width=1)))

    STATUS_LINE_COLORS = {
        "naked": "#999999",
        "testing_resistance": "#e08300",
        "reclaimed_pending_retest": "#1a7f37",
        "reclaimed_retested": "#2e86c1",
    }
    STATUS_LABELS = {
        "naked": "⚪ naked",
        "testing_resistance": "🟠 testing resistance",
        "reclaimed_pending_retest": "🟢 reclaimed, pending retest",
        "reclaimed_retested": "🔵 reclaimed & retested",
    }

    x_price = level_row["x_price"]
    status = level_row["status"]
    streak_end = level_row["streak_end"]
    if pd.notna(x_price):
        line_color = STATUS_LINE_COLORS.get(status, "#8e44ad")
        status_note = STATUS_LABELS.get(status, "")
        streak_end_str = pd.to_datetime(streak_end).strftime("%d-%b-%Y") if pd.notna(streak_end) else "?"
        fig.add_hline(
            y=x_price, line_dash="dot", line_color=line_color, line_width=2,
            annotation_text=f"Selected X = ₹{x_price:.2f} · streak ended {streak_end_str} · {status_note}",
            annotation_position="top left",
        )

    # Every OTHER unresolved level for this symbol, for context - same
    # convention as build_streak_chart().
    ledger_all = levels_store.get_all_levels()
    if not ledger_all.empty:
        others = ledger_all[
            (ledger_all["symbol"] == symbol)
            & (ledger_all["status"] != "reclaimed_retested")
            & (ledger_all["x_price"] != x_price)
        ]
        for _, lvl in others.iterrows():
            lvl_label = STATUS_LABELS.get(lvl["status"], lvl["status"])
            fig.add_hline(
                y=lvl["x_price"], line_dash="dash",
                line_color=STATUS_LINE_COLORS.get(lvl["status"], "#16a085"),
                line_width=1,
                annotation_text=f"Other X = ₹{lvl['x_price']:.2f} · streak ended {lvl['streak_end']} · {lvl_label}",
                annotation_position="bottom left",
            )
    return fig


def render_multi_chart_grid(symbols: list, layout: str, period: str, key_prefix: str) -> None:
    """
    Renders a grid of mini candlestick+EMA50/200 charts for up to 6 symbols.

    Shared by the Multi-Chart Comparison section and the Reference Level
    Ledger's "compare selected" picker, so the grid/candle/EMA logic lives
    in exactly one place. `key_prefix` must be unique per caller so Plotly
    widget keys never collide between the two sections.

    `merged` (Symbol/CompanyName/Price/DayChg% lookup table) is a
    module-level global built once the dashboard's main metrics are
    computed; this function is only ever called after that point.
    """
    if not symbols:
        st.info("👆 Select one or more stocks above to compare their charts side by side.")
        return

    max_slots = int(layout)
    shown_symbols = symbols[:max_slots]
    if len(symbols) > max_slots:
        st.caption(
            f"⚠️ You selected {len(symbols)} stocks but the layout is set to "
            f"{max_slots} - showing the first {max_slots}. Switch the layout to see more at once."
        )

    # Grid: 1 -> single column; 2 -> two columns; 4 -> 2x2; 6 -> 3x2
    cols_per_row = {"1": 1, "2": 2, "4": 2, "6": 3}[layout]
    for row_start in range(0, len(shown_symbols), cols_per_row):
        row_symbols = shown_symbols[row_start:row_start + cols_per_row]
        row_cols = st.columns(len(row_symbols))
        for col, sym in zip(row_cols, row_symbols):
            with col:
                mc_hist = fetch_history_for_chart(sym, period=period)
                if mc_hist.empty:
                    st.warning(f"No data for {sym}")
                    continue
                mc_row = merged[merged["Symbol"] == sym]
                company_name = mc_row["CompanyName"].iloc[0] if not mc_row.empty else ""
                last_price = mc_row["Price"].iloc[0] if not mc_row.empty else mc_hist["Close"].iloc[-1]
                day_chg = mc_row["DayChg%"].iloc[0] if not mc_row.empty else np.nan
                st.markdown(
                    f"**{sym}** — {company_name}  \n"
                    f"₹{last_price:,.2f}  "
                    + (f":green[{day_chg:+.2f}%]" if pd.notna(day_chg) and day_chg >= 0
                       else f":red[{day_chg:+.2f}%]" if pd.notna(day_chg) else "")
                )
                mc_fig = go.Figure()
                mc_fig.add_trace(
                    go.Candlestick(
                        x=mc_hist.index, open=mc_hist["Open"], high=mc_hist["High"],
                        low=mc_hist["Low"], close=mc_hist["Close"], name="Price",
                        showlegend=False,
                    )
                )
                mc_fig.add_trace(
                    go.Scatter(x=mc_hist.index, y=mc_hist["EMA200"], mode="lines", name="200 EMA",
                               line=dict(color="orange", width=1), showlegend=False)
                )
                mc_fig.add_trace(
                    go.Scatter(x=mc_hist.index, y=mc_hist["EMA50"], mode="lines", name="50 EMA",
                               line=dict(color="blue", width=1), showlegend=False)
                )
                mc_fig.update_layout(
                    height=280,
                    margin=dict(l=40, r=10, t=10, b=10),
                    xaxis_rangeslider_visible=False,
                    yaxis=dict(tickformat=",.0f", automargin=True),
                )
                st.plotly_chart(mc_fig, use_container_width=True, key=f"{key_prefix}_{sym}")


def render_strategy_multi_chart_grid(selected: list, chart_builder, layout: str, period: str,
                                      key_prefix: str, caption_fn=None, extra_kwargs_fn=None,
                                      grid_height: int = 340) -> None:
    """
    Shared grid renderer for the six per-strategy multi-chart comparisons
    (5-Leg, Monthly Pivot S1, S1 Shift, Breakout-Pullback, EMA Pullback,
    Supertrend). Each panel is built with that strategy's OWN chart_builder
    (e.g. build_five_leg_chart), so the strategy's specific levels/legs/
    phases are marked on every panel - not a generic EMA-only chart.

    Consolidates what used to be six separately hand-written copies of the
    same "cap at 6 -> cols_per_row grid -> fetch -> build -> plot" loop, so
    a fix (or a copy-paste slip) only has to happen in one place.

    `selected`: list of (symbol, ep_row) tuples - ep_row is that strategy's
    own episode row.
    `chart_builder`: one of the build_*_chart functions; called as
    chart_builder(hist, symbol, ep_row, period, **extra_kwargs).
    `caption_fn`: optional callable(symbol, ep_row) -> str, rendered via
    st.markdown() above each panel (e.g. "Leg1 12-Mar-2024 · reclaimed").
    Omit for no caption.
    `extra_kwargs_fn`: optional callable(ep_row) -> dict of additional
    keyword args the builder needs beyond (hist, symbol, ep_row, period)
    - e.g. Supertrend's per-episode st_period/st_multiplier, or 5-Leg's
    min_leg_days (constant across the page, but still passed this way for
    a uniform call signature).
    `grid_height`: builders default to a large single-chart height (560px);
    overridden here post-hoc via fig.update_layout() so multiple panels
    stay readable side by side, without touching any builder's signature.
    """
    if not selected:
        st.info("👆 Click a row above for its single-chart view, or check 2-6 rows to compare them side by side.")
        return

    max_slots = int(layout)
    shown = selected[:max_slots]
    if len(selected) > max_slots:
        st.caption(
            f"⚠️ You checked {len(selected)} rows but the layout is set to "
            f"{max_slots} - showing the first {max_slots}. Switch the layout to see more at once."
        )

    cols_per_row = {"1": 1, "2": 2, "4": 2, "6": 3}[layout]
    for row_start in range(0, len(shown), cols_per_row):
        row_items = shown[row_start:row_start + cols_per_row]
        row_cols = st.columns(len(row_items))
        for col, (sym, ep_row) in zip(row_cols, row_items):
            with col:
                hist = fetch_full_history_with_indicators(sym)
                if hist.empty:
                    st.warning(f"No data for {sym}")
                    continue
                if caption_fn is not None:
                    st.markdown(caption_fn(sym, ep_row))
                else:
                    st.markdown(f"**{sym}**")
                extra_kwargs = extra_kwargs_fn(ep_row) if extra_kwargs_fn else {}
                fig = chart_builder(hist, sym, ep_row, period, **extra_kwargs)
                fig.update_layout(height=grid_height, margin=dict(l=40, r=10, t=30, b=10))
                st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_{sym}")


def build_five_leg_chart(hist: pd.DataFrame, symbol: str, ep_row: pd.Series, period: str,

                          min_leg_days: int = 5) -> go.Figure:
    """
    5-Leg EMA Reversal chart.
    Overlays: 20 EMA (purple), 50 EMA (blue), 200 EMA (orange).
    Horizontal bands: X (lowest 200 EMA, dashed orange) and Y (lowest price,
    dashed red), both with a ±retest shading band so the support zone is
    immediately visible.
    Leg-period shading: alternating green/red rectangles for each detected
    leg over the episode window, so the structure is easy to read at a glance.
    Vertical marker at leg1_start, qualified_date, and probe_date (if any).
    """
    full = hist.copy()
    h = _trim_hist(hist, period)
    fig = _base_candle_fig(h, symbol)

    # EMAs
    fig.add_trace(go.Scatter(x=h.index, y=h["EMA20"],  mode="lines", name="20 EMA",
                             line=dict(color="#9b59b6", width=1)))
    fig.add_trace(go.Scatter(x=h.index, y=h["EMA50"],  mode="lines", name="50 EMA",
                             line=dict(color="#5b9bd5", width=1.2)))
    fig.add_trace(go.Scatter(x=h.index, y=h["EMA200"], mode="lines", name="200 EMA",
                             line=dict(color="orange", width=1.5)))

    # X and Y horizontal levels
    x_price = ep_row.get("x_price")
    y_price = ep_row.get("y_price")
    if pd.notna(x_price):
        fig.add_hline(y=x_price, line_dash="dot", line_color="orange", line_width=1.5,
                      annotation_text=f"X (Lowest 200EMA) ₹{x_price:.2f}",
                      annotation_position="top right")
    if pd.notna(y_price):
        fig.add_hline(y=y_price, line_dash="dot", line_color="#e74c3c", line_width=1.5,
                      annotation_text=f"Y (Lowest Price) ₹{y_price:.2f}",
                      annotation_position="bottom right")

    # Leg shading — recompute legs from the full history (not trimmed)
    try:
        ema20_full  = full["EMA20"]
        ema50_full  = full["EMA50"]
        legs = five_leg_pattern._get_legs(full, ema20_full, ema50_full, min_leg_days)
        leg1_start = pd.to_datetime(ep_row.get("leg1_start"))
        chart_start = h.index[0]
        chart_end   = h.index[-1]
        episode_legs = [lg for lg in legs if lg["start"] >= leg1_start][:7]
        for li, lg in enumerate(episode_legs):
            if lg["end"] < chart_start or lg["start"] > chart_end:
                continue
            fc     = "rgba(46,204,113,0.10)" if lg["direction"] == "up" else "rgba(231,76,60,0.10)"
            border = "rgba(46,204,113,0.5)"  if lg["direction"] == "up" else "rgba(231,76,60,0.5)"
            _add_vrect(fig,
                       x0=max(lg["start"], chart_start),
                       x1=min(lg["end"],   chart_end),
                       fillcolor=fc, label=f"L{li+1}",
                       border_color=border, border_width=0.5)
    except Exception:
        pass  # shading is decorative; never crash the chart over it

    # Key date markers
    leg1_start_dt = pd.to_datetime(ep_row.get("leg1_start"))
    qual_dt       = pd.to_datetime(ep_row.get("qualified_date"))
    probe_dt      = pd.to_datetime(ep_row.get("probe_date")) if ep_row.get("probe_date") else None

    for dt, label, color in [
        (leg1_start_dt, "Leg 1 Start",     "#e67e22"),
        (qual_dt,       "5-Leg Qualified", "#27ae60"),
        (probe_dt,      "Golden Cross",    "#2980b9"),
    ]:
        if dt is None or pd.isna(dt):
            continue
        if dt < h.index[0] or dt > h.index[-1]:
            continue
        _add_vline(fig, dt, label, color)
    return fig


def build_pivot_s1_chart(hist: pd.DataFrame, symbol: str, ep_row: pd.Series, period: str) -> go.Figure:
    """
    Monthly Pivot S1 setup chart.
    Overlays: 200 EMA (orange), S1 step-function (teal step line).
    Horizontal levels: X (running high, dashed green) and Y (running low, dashed red).
    Vertical markers: episode_start, x_fix_date (S1 touch), y_fix_date (200EMA cross).
    """
    h = _trim_hist(hist, period)
    fig = _base_candle_fig(h, symbol)

    fig.add_trace(go.Scatter(x=h.index, y=h["EMA200"], mode="lines", name="200 EMA",
                             line=dict(color="orange", width=1.5)))
    # S1 as a step-function — use line_shape="hv" so it holds flat within the month
    fig.add_trace(go.Scatter(x=h.index, y=h["PivotS1"], mode="lines", name="Monthly Pivot S1",
                             line=dict(color="#16a085", width=1.2, dash="dot"),
                             line_shape="hv"))

    x_price = ep_row.get("x_price")
    y_price = ep_row.get("y_price")
    if pd.notna(x_price):
        fig.add_hline(y=x_price, line_dash="dash", line_color="#27ae60", line_width=1.5,
                      annotation_text=f"X (Running High) ₹{x_price:.2f}",
                      annotation_position="top right")
    if pd.notna(y_price):
        fig.add_hline(y=y_price, line_dash="dash", line_color="#e74c3c", line_width=1.5,
                      annotation_text=f"Y (Running Low) ₹{y_price:.2f}",
                      annotation_position="bottom right")

    # Shade the qualifying window (episode_start → x_fix_date) in pale teal
    ep_start  = pd.to_datetime(ep_row.get("episode_start"))
    x_fix_dt  = pd.to_datetime(ep_row.get("x_fix_date"))  if ep_row.get("x_fix_date")  else None
    y_fix_dt  = pd.to_datetime(ep_row.get("y_fix_date"))  if ep_row.get("y_fix_date")  else None

    chart_start = h.index[0]
    chart_end   = h.index[-1]

    if ep_start and x_fix_dt and ep_start >= chart_start:
        _add_vrect(fig,
                   x0=max(ep_start, chart_start),
                   x1=min(x_fix_dt,  chart_end),
                   fillcolor="rgba(22,160,133,0.08)",
                   label="Qualifying window")

    for dt, label, color in [
        (ep_start, "Episode Start",          "#16a085"),
        (x_fix_dt, "S1 Touch → X Fixed",    "#27ae60"),
        (y_fix_dt, "200EMA Cross → Y Fixed", "#2980b9"),
    ]:
        if dt is None or pd.isna(dt):
            continue
        if dt < chart_start or dt > chart_end:
            continue
        _add_vline(fig, dt, label, color)
    return fig


def build_s1_shift_chart(hist: pd.DataFrame, symbol: str, ep_row: pd.Series, period: str) -> go.Figure:
    """
    Monthly S1 Shift Up setup chart.
    Overlays: 200 EMA (orange).
    S1 for the setup month (S1_M, dashed teal) and S1 for the following month
    (S1_M+1, solid teal) drawn as horizontal bands over their respective months.
    Horizontal level: X (month low, dashed red).
    Vertical markers: x_date (month low), anchor_date (tracking start), tested/failed date.
    """
    h = _trim_hist(hist, period)
    fig = _base_candle_fig(h, symbol)

    fig.add_trace(go.Scatter(x=h.index, y=h["EMA200"], mode="lines", name="200 EMA",
                             line=dict(color="orange", width=1.5)))
    # Full S1 step line for context
    fig.add_trace(go.Scatter(x=h.index, y=h["PivotS1"], mode="lines", name="Monthly Pivot S1",
                             line=dict(color="#16a085", width=1, dash="dot"),
                             line_shape="hv"))

    x_price      = ep_row.get("x_price")
    s1_month     = ep_row.get("s1_month")
    s1_next      = ep_row.get("s1_next_month")
    anchor_date  = pd.to_datetime(ep_row.get("anchor_date"))   if ep_row.get("anchor_date")   else None
    x_date       = pd.to_datetime(ep_row.get("x_date"))        if ep_row.get("x_date")        else None
    tested_date  = pd.to_datetime(ep_row.get("tested_date"))   if ep_row.get("tested_date")   else None
    failed_date  = pd.to_datetime(ep_row.get("failed_date"))   if ep_row.get("failed_date")   else None

    # S1 of the setup month as a horizontal band annotation
    if pd.notna(s1_month):
        fig.add_hline(y=s1_month, line_dash="dot", line_color="#16a085", line_width=1,
                      annotation_text=f"S1(M) ₹{s1_month:.2f}", annotation_position="top left")
    if pd.notna(s1_next):
        fig.add_hline(y=s1_next, line_dash="dot", line_color="#1abc9c", line_width=1.5,
                      annotation_text=f"S1(M+1) ₹{s1_next:.2f} ↑ Shift Up", annotation_position="bottom left")
    if pd.notna(x_price):
        fig.add_hline(y=x_price, line_dash="dash", line_color="#e74c3c", line_width=1.5,
                      annotation_text=f"X (Month Low) ₹{x_price:.2f}", annotation_position="bottom right")

    chart_start = h.index[0]
    chart_end   = h.index[-1]

    for dt, label, color in [
        (x_date,      "Month Low (X)",  "#e74c3c"),
        (anchor_date, "Tracking Start", "#8e44ad"),
        (tested_date, "Retested",       "#27ae60"),
        (failed_date, "Failed",         "#c0392b"),
    ]:
        if dt is None or pd.isna(dt):
            continue
        if dt < chart_start or dt > chart_end:
            continue
        _add_vline(fig, dt, label, color)
    return fig


def build_breakout_pullback_chart(hist: pd.DataFrame, symbol: str, ep_row: pd.Series, period: str) -> go.Figure:
    """
    Breakout-Pullback 4-Leg chart.
    Overlays: 20 EMA (purple), 50 EMA (blue), 200 EMA (orange).
    Horizontal levels: X (highest close in Leg 2, dashed green),
                       Y (lowest 50 EMA in Leg 3, dashed teal),
                       Z (lowest price in Leg 3, dashed red).
    Vertical markers: leg1_start, leg2_start, leg3_start, leg4_start, signal_date.
    Leg shading: alternating rectangles for each leg.
    """
    h = _trim_hist(hist, period)
    fig = _base_candle_fig(h, symbol)

    fig.add_trace(go.Scatter(x=h.index, y=h["EMA20"], mode="lines", name="20 EMA",
                             line=dict(color="#9b59b6", width=1)))
    fig.add_trace(go.Scatter(x=h.index, y=h["EMA50"], mode="lines", name="50 EMA",
                             line=dict(color="#5b9bd5", width=1.2)))
    fig.add_trace(go.Scatter(x=h.index, y=h["EMA200"], mode="lines", name="200 EMA",
                             line=dict(color="orange", width=1.5)))

    x_price = ep_row.get("x_price")
    y_price = ep_row.get("y_price")
    z_price = ep_row.get("z_price")
    leg1_low = ep_row.get("leg1_low_price")

    if pd.notna(x_price):
        fig.add_hline(y=x_price, line_dash="dash", line_color="#27ae60", line_width=1.5,
                      annotation_text=f"X (Leg 2 High Close) ₹{x_price:.2f}",
                      annotation_position="top right")
    if pd.notna(y_price):
        fig.add_hline(y=y_price, line_dash="dash", line_color="#16a085", line_width=1.5,
                      annotation_text=f"Y (Leg 3 Low 50EMA) ₹{y_price:.2f}",
                      annotation_position="bottom right")
    if pd.notna(z_price):
        fig.add_hline(y=z_price, line_dash="dash", line_color="#e74c3c", line_width=1.5,
                      annotation_text=f"Z (Leg 3 Low Price) ₹{z_price:.2f}",
                      annotation_position="bottom right")
    if pd.notna(leg1_low):
        fig.add_hline(y=leg1_low, line_dash="dot", line_color="#95a5a6", line_width=1,
                      annotation_text=f"Leg 1 Low ₹{leg1_low:.2f}",
                      annotation_position="top left")

    # Vertical markers for key dates — staggered y-positions to prevent overlap
    y_positions = [1.00, 0.96, 0.92, 0.88, 0.84]
    for idx, (dt_key, label, color) in enumerate([
        ("leg1_start", "Leg 1 Start", "#e67e22"),
        ("leg2_start", "Leg 2 Start", "#9b59b6"),
        ("leg3_start", "Leg 3 Start", "#2980b9"),
        ("leg4_start", "Leg 4 Start", "#27ae60"),
        ("signal_date", "Signal Fired", "#e74c3c"),
    ]):
        dt = pd.to_datetime(ep_row.get(dt_key)) if ep_row.get(dt_key) else None
        if dt is not None and not pd.isna(dt):
            _add_vline(fig, dt, label, color, y_pos=y_positions[idx])

    return fig


def build_ema_pullback_chart(hist: pd.DataFrame, symbol: str, ep_row: pd.Series, period: str) -> go.Figure:
    """
    EMA Pullback Reentry chart.
    Overlays: 20 EMA (purple), 50 EMA (blue).
    Horizontal levels: X (highest High during qualifying run, dashed green),
                       Y (lowest Low in pullback window, dashed red).
    Shading: qualifying clean window (pale green), pullback-to-signal window (pale blue).
    Vertical markers: crossover_date, qualify_end_date, touch_date, y_fix_date,
                      tested_date / failed_date.
    """
    h = _trim_hist(hist, period)
    fig = _base_candle_fig(h, symbol)

    # EMAs
    fig.add_trace(go.Scatter(x=h.index, y=h["EMA20"], mode="lines", name="20 EMA",
                             line=dict(color="#9b59b6", width=1)))
    fig.add_trace(go.Scatter(x=h.index, y=h["EMA50"], mode="lines", name="50 EMA",
                             line=dict(color="#5b9bd5", width=1.4)))

    x_price = ep_row.get("x_price")
    y_price = ep_row.get("y_price")

    if pd.notna(x_price):
        fig.add_hline(y=x_price, line_dash="dash", line_color="#27ae60", line_width=1.8,
                      annotation_text=f"X (Highest High) ₹{x_price:.2f}",
                      annotation_position="top right")
    if pd.notna(y_price):
        fig.add_hline(y=y_price, line_dash="dash", line_color="#e74c3c", line_width=1.8,
                      annotation_text=f"Y (Lowest Low) ₹{y_price:.2f}",
                      annotation_position="bottom right")

    crossover_dt    = pd.to_datetime(ep_row.get("crossover_date"))    if ep_row.get("crossover_date")    else None
    qualify_end_dt  = pd.to_datetime(ep_row.get("qualify_end_date"))  if ep_row.get("qualify_end_date")  else None
    touch_dt        = pd.to_datetime(ep_row.get("touch_date"))        if ep_row.get("touch_date")        else None
    y_fix_dt        = pd.to_datetime(ep_row.get("y_fix_date"))        if ep_row.get("y_fix_date")        else None
    tested_dt       = pd.to_datetime(ep_row.get("tested_date"))       if ep_row.get("tested_date")       else None
    failed_dt       = pd.to_datetime(ep_row.get("failed_date"))       if ep_row.get("failed_date")       else None

    chart_start = h.index[0]
    chart_end   = h.index[-1]

    # Shade the qualifying clean window (crossover → touch_date) pale green
    if crossover_dt and touch_dt and crossover_dt <= chart_end:
        _add_vrect(fig,
                   x0=max(crossover_dt, chart_start),
                   x1=min(touch_dt, chart_end),
                   fillcolor="rgba(39,174,96,0.08)",
                   label="Qualifying window")

    # Shade the pullback-to-signal window (touch → y_fix) pale blue
    if touch_dt and y_fix_dt and touch_dt <= chart_end:
        _add_vrect(fig,
                   x0=max(touch_dt, chart_start),
                   x1=min(y_fix_dt, chart_end),
                   fillcolor="rgba(41,128,185,0.08)",
                   label="Y tracking")

    # Key date vertical markers (stagger y positions to avoid label overlap)
    y_positions = [1.00, 0.96, 0.92, 0.88, 0.84, 0.80]
    markers = [
        (crossover_dt,   "20/50 Cross",       "#9b59b6"),
        (qualify_end_dt, "Qual. End",          "#27ae60"),
        (touch_dt,       "50 EMA Touch → X",  "#e67e22"),
        (y_fix_dt,       "Signal Fired → Y",  "#2980b9"),
        (tested_dt,      "Y Retested",         "#1abc9c"),
        (failed_dt,      "Failed",             "#c0392b"),
    ]
    for idx, (dt, label, color) in enumerate(markers):
        if dt is None or pd.isna(dt):
            continue
        if dt < chart_start or dt > chart_end:
            continue
        _add_vline(fig, dt, label, color, y_pos=y_positions[idx])

    return fig


def build_range_breakout_chart(
    hist: pd.DataFrame,
    symbol: str,
    ep_row: pd.Series,
    period: str = "1y",
    **kwargs,
) -> go.Figure:
    hist = _trim_hist(hist, period)
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.03, row_heights=[0.75, 0.25],
    )
    fig.add_trace(go.Candlestick(
        x=hist.index, open=hist["Open"], high=hist["High"],
        low=hist["Low"], close=hist["Close"], name=symbol,
    ), row=1, col=1)

    # Monthly pivot step line
    monthly = hist.copy()
    monthly["_ym"] = monthly.index.to_period("M")
    mdf = monthly.groupby("_ym").agg(
        H=("High", "max"), L=("Low", "min"), C=("Close", "last")
    )
    mdf["P"] = (mdf["H"] + mdf["L"] + mdf["C"]) / 3
    mdf["P_applied"] = mdf["P"].shift(1)
    mdf = mdf.dropna(subset=["P_applied"])
    daily_pivot = hist.copy()
    daily_pivot["_ym"] = daily_pivot.index.to_period("M")
    daily_pivot = daily_pivot.merge(
        mdf[["P_applied"]].rename(columns={"P_applied": "_pivot"}),
        left_on="_ym", right_index=True, how="left"
    )
    fig.add_trace(go.Scatter(
        x=daily_pivot.index, y=daily_pivot["_pivot"],
        mode="lines", line=dict(color="#00BCD4", width=1.5),
        name="Monthly Pivot", line_shape="hv",
    ), row=1, col=1)

    # Leg levels
    leg1_high = ep_row.get("leg1_high")
    leg2_low_pivot = ep_row.get("leg2_low_pivot")
    leg2_low_price = ep_row.get("leg2_low_price")

    if pd.notna(leg1_high):
        fig.add_hline(y=leg1_high, line_dash="dash", line_color="#2E7D32",
                      annotation_text="Leg1 High", row=1, col=1)
    if pd.notna(leg2_low_pivot):
        fig.add_hline(y=leg2_low_pivot, line_dash="dash", line_color="#C62828",
                      annotation_text="Leg2 Low Pivot", row=1, col=1)
    if pd.notna(leg2_low_price):
        fig.add_hline(y=leg2_low_price, line_dash="dot", line_color="#C62828",
                      annotation_text="Leg2 Low Price", row=1, col=1)

    # Leg shading
    leg_colors = ["rgba(46,125,50,0.08)", "rgba(198,40,40,0.08)",
                  "rgba(46,125,50,0.08)", "rgba(198,40,40,0.08)",
                  "rgba(46,125,50,0.08)"]
    leg_starts = ["leg1_start", "leg2_start", "leg3_start", "leg4_start", "leg5_start"]
    leg_ends = ["leg1_end", "leg2_end", "leg3_end", "leg4_end", "leg5_end"]
    for i, (s_col, e_col, color) in enumerate(zip(leg_starts, leg_ends, leg_colors)):
        s = ep_row.get(s_col)
        e = ep_row.get(e_col)
        if pd.notna(s) and pd.notna(e):
            s_str = pd.Timestamp(s).strftime("%Y-%m-%d")
            e_str = pd.Timestamp(e).strftime("%Y-%m-%d")
            fig.add_vrect(x0=s_str, x1=e_str, fillcolor=color, line_width=0,
                          annotation_text=f"Leg {i+1}", row=1, col=1)
    # Volume
    if "Volume" in hist.columns:
        colors = ["green" if hist["Close"].iloc[i] >= hist["Open"].iloc[i] else "red"
                  for i in range(len(hist))]
        fig.add_trace(go.Bar(x=hist.index, y=hist["Volume"], marker_color=colors,
                             showlegend=False), row=2, col=1)

    fig.update_layout(
        title=f"{symbol} — Range Breakout (5-Leg)",
        xaxis_rangeslider_visible=False,
        height=650, template="plotly_white",
    )
    return fig

def build_supertrend_chart(hist: pd.DataFrame, symbol: str, ep_row: pd.Series,
                            period: str, st_period: int = 7, st_multiplier: float = 3.0) -> go.Figure:
    """
    Supertrend 3-Phase chart.
    Overlays: 200 EMA (orange), Supertrend line (green/red per direction).
    Horizontal levels: X (Phase 1 high, solid green), Y (Phase 2 low, solid red),
                       Z (lowest 200 EMA, dashed orange).
    Phase shading: Phase 1 = pale green, Phase 2 = pale red, Phase 3 = pale blue.
    Vertical markers: phase1_start, phase1_end, phase3_start, signal_date, x_cleared_date.
    """
    full = hist.copy()
    h = _trim_hist(hist, period)
    fig = _base_candle_fig(h, symbol)

    # 200 EMA
    fig.add_trace(go.Scatter(x=h.index, y=h["EMA200"], mode="lines", name="200 EMA",
                             line=dict(color="orange", width=1.5)))

    # Supertrend line — colour-split into bull (green) and bear (red) segments
    try:
        st_df_full = supertrend_pattern.compute_supertrend(full, period=st_period, multiplier=float(st_multiplier))
        st_h = st_df_full.loc[h.index[0]:h.index[-1]] if not st_df_full.empty else st_df_full
        if not st_h.empty:
            st_line = st_h["supertrend"]
            st_dir  = st_h["direction"]
            # Split into green and red segments
            bull_y = st_line.where(st_dir == 1)
            bear_y = st_line.where(st_dir == -1)
            fig.add_trace(go.Scatter(x=st_h.index, y=bull_y, mode="lines",
                                     name=f"ST({st_period},{st_multiplier}) BUY",
                                     line=dict(color="#27ae60", width=1.8),
                                     connectgaps=False))
            fig.add_trace(go.Scatter(x=st_h.index, y=bear_y, mode="lines",
                                     name=f"ST({st_period},{st_multiplier}) SELL",
                                     line=dict(color="#e74c3c", width=1.8),
                                     connectgaps=False))
    except Exception:
        pass

    x_price = ep_row.get("x_price")
    y_price = ep_row.get("y_price")
    z_price = ep_row.get("z_price")

    if pd.notna(x_price):
        fig.add_hline(y=x_price, line_dash="solid", line_color="#27ae60", line_width=1.8,
                      annotation_text=f"X (Phase 1 High) ₹{x_price:.2f}",
                      annotation_position="top right")
    if pd.notna(y_price):
        fig.add_hline(y=y_price, line_dash="solid", line_color="#e74c3c", line_width=1.8,
                      annotation_text=f"Y (Phase 2 Low) ₹{y_price:.2f}",
                      annotation_position="bottom right")
    if pd.notna(z_price):
        fig.add_hline(y=z_price, line_dash="dot", line_color="orange", line_width=1.5,
                      annotation_text=f"Z (Lowest 200 EMA) ₹{z_price:.2f}",
                      annotation_position="bottom left")

    phase1_start_dt  = pd.to_datetime(ep_row.get("phase1_start"))  if ep_row.get("phase1_start")  else None
    phase1_end_dt    = pd.to_datetime(ep_row.get("phase1_end"))    if ep_row.get("phase1_end")    else None
    phase2_start_dt  = pd.to_datetime(ep_row.get("phase2_start"))  if ep_row.get("phase2_start")  else None
    phase3_start_dt  = pd.to_datetime(ep_row.get("phase3_start"))  if ep_row.get("phase3_start")  else None
    signal_dt        = pd.to_datetime(ep_row.get("signal_date"))   if ep_row.get("signal_date")   else None
    x_cleared_dt     = pd.to_datetime(ep_row.get("x_cleared_date"))if ep_row.get("x_cleared_date")else None

    chart_start = h.index[0]
    chart_end   = h.index[-1]

    # Phase shading
    if phase1_start_dt and phase1_end_dt and phase1_start_dt <= chart_end:
        _add_vrect(fig, x0=max(phase1_start_dt, chart_start), x1=min(phase1_end_dt, chart_end),
                   fillcolor="rgba(39,174,96,0.08)", label="Phase 1 (Bull)")
    if phase2_start_dt and phase3_start_dt and phase2_start_dt <= chart_end:
        _add_vrect(fig, x0=max(phase2_start_dt, chart_start), x1=min(phase3_start_dt, chart_end),
                   fillcolor="rgba(231,76,60,0.08)", label="Phase 2 (Bear)")
    if phase3_start_dt and chart_end >= phase3_start_dt:
        end_shade = min(x_cleared_dt, chart_end) if x_cleared_dt else chart_end
        _add_vrect(fig, x0=max(phase3_start_dt, chart_start), x1=end_shade,
                   fillcolor="rgba(41,128,185,0.08)", label="Phase 3 (Recovery)")

    # Vertical markers
    y_positions = [1.00, 0.96, 0.92, 0.88, 0.84]
    for idx, (dt, label, color) in enumerate([
        (phase1_start_dt, "P1 Start",     "#27ae60"),
        (phase1_end_dt,   "P1 End / P2",  "#e74c3c"),
        (phase3_start_dt, "ST Buy Flip",  "#2980b9"),
        (signal_dt,       "ST > 200 EMA", "#8e44ad"),
        (x_cleared_dt,    "ST/Price > X", "#f39c12"),
    ]):
        if dt is None or pd.isna(dt): continue
        if dt < chart_start or dt > chart_end: continue
        _add_vline(fig, dt, label, color, y_pos=y_positions[idx])

    return fig

# The three ledger tables are only ever WRITTEN by fetch_metrics (via the
# upsert_* calls inside it). Reading them fresh from SQLite on every single
# widget interaction (e.g. just moving a filter dropdown) is pure wasted
# work, since Streamlit reruns the whole script on any interaction - these
# wrappers cache the read using the SAME TTL as fetch_metrics, so a filter
# change only re-runs the (cheap) filtering/styling below, not the SQLite
# read itself.
@st.cache_data(show_spinner=False, ttl=DATA_CACHE_TTL)
def load_streak_ledger():
    return levels_store.get_all_levels()


@st.cache_data(show_spinner=False, ttl=DATA_CACHE_TTL)
def load_five_leg_ledger():
    return levels_store.get_five_leg_episodes()


@st.cache_data(show_spinner=False, ttl=DATA_CACHE_TTL)
def load_pivot_ledger():
    return levels_store.get_pivot_episodes()


@st.cache_data(show_spinner=False, ttl=DATA_CACHE_TTL)
def load_s1_shift_ledger():
    return levels_store.get_s1_shift_episodes()


@st.cache_data(show_spinner=False, ttl=DATA_CACHE_TTL)
def load_breakout_pullback_ledger():
    return levels_store.get_breakout_pullback_episodes()


@st.cache_data(show_spinner=False, ttl=DATA_CACHE_TTL)
def load_ema_pullback_ledger():
    return levels_store.get_ema_pullback_episodes()


@st.cache_data(show_spinner=False, ttl=DATA_CACHE_TTL)
def load_supertrend_ledger():
    return levels_store.get_supertrend_episodes()


@st.cache_data(show_spinner=False, ttl=DATA_CACHE_TTL)
def load_range_breakout_ledger(status: str = None, pattern_type: str = None) -> pd.DataFrame:
    df = levels_store.get_range_breakout_episodes(status=status, pattern_type=pattern_type)
    if df.empty:
        return df
    for col in ["leg1_start", "leg1_end", "leg2_start", "leg2_end",
                "leg3_start", "leg3_end", "leg4_start", "leg4_end",
                "leg5_start", "leg5_end", "first_seen_at", "last_checked_at"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in ["leg1_high", "leg2_low_pivot", "leg2_low_price",
                "leg3_max_pivot", "leg4_min_pivot", "leg4_min_low",
                "leg5_last_pivot", "leg5_max_pivot"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "breakout_confirmed" in df.columns:
        df["breakout_confirmed"] = df["breakout_confirmed"].astype(bool)
    if "is_ongoing" in df.columns:
        df["is_ongoing"] = df["is_ongoing"].astype(bool)
    return df


@st.cache_data(show_spinner=False, ttl=3600 * 6)   # 6-hour cache — fundamentals change slowly
def load_fundamental_cache_cached(symbols_key: str):
    """
    Reads the fundamental_cache table from SQLite.
    symbols_key is a hash of the symbol list — used as a cache-bust key only.
    The actual data comes from SQLite, not from this key.
    """
    return fundamental_score.get_cached_fundamentals(
        [s.strip() for s in symbols_key.split(",")]
    )


@st.cache_data(show_spinner=False, ttl=DATA_CACHE_TTL)
def load_confluence_table_cached(
    metrics_key: str,          # hashable cache-bust key (e.g. str(metrics_df shape + last refresh))
    streak_key: str,
    st_key: str,
):
    """
    Thin caching wrapper — the actual scoring is in confluence_score.py.
    Keys are just cache-busting strings so Streamlit re-runs when data changes.
    Returns cached result; rebuilding takes ~50–200 ms for 100 symbols.
    """
    return None  # placeholder replaced at call site


def _combine_retest(x_status, y_status):
    """
    'retested' only if BOTH X and Y have been retested; otherwise
    'pending retest' - so a single glance at a combined status tells you
    whether there's still an open pullback opportunity on EITHER level.
    Shared by the 5-Leg and Monthly Pivot S1 sections (both track X/Y
    retest status independently and fold it into one display status).

    IMPORTANT -- this function assumes each status is only ever "naked" or
    "tested" (that's all monthly_pivot_pattern.classify_retest / the 5-Leg
    detector ever produce). It deliberately does NOT handle "failed": some
    OTHER strategies in this dashboard (EMA Pullback, Breakout-Pullback,
    Monthly S1 Shift, Supertrend) use a fail_pct threshold and CAN return
    "failed" from their own retest classifiers. If this helper were ever
    reused for one of those, a "failed" status would silently fall into the
    `else` branch and display as "pending retest" -- identical to a level
    nobody has even approached yet, even though "failed" means the level
    already broke down. That's a meaningfully different risk state, so this
    guard makes the mismatch loud (an exception) instead of a quietly wrong
    label if this function is ever pointed at the wrong data source.
    """
    if x_status not in ("naked", "tested") or y_status not in ("naked", "tested"):
        raise ValueError(
            f"_combine_retest() only supports naked/tested statuses (5-Leg and "
            f"Monthly Pivot S1 use it) -- got x_status={x_status!r}, y_status={y_status!r}. "
            f"If you're wiring this up for a strategy whose classifier can return "
            f"'failed' (EMA Pullback, Breakout-Pullback, S1 Shift, Supertrend), "
            f"add an explicit 'failed' branch here instead of reusing this helper as-is."
        )
    return "retested" if (x_status == "tested" and y_status == "tested") else "pending retest"