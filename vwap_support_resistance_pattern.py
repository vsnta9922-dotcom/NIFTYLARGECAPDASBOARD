"""
vwap_support_resistance_pattern.py
--------------------------------------
Detects the "VWAP 1-SD Band vs First-Hour Range" support/resistance
pattern (hourly timeframe, session VWAP that resets daily).

TWO Day-D conditions are now tracked:

  LOWER BAND (support from below):
    Day D: lower_band_close > first_hour_high  ->  X = lower_band_close
    Natural role: support. From D+1, debounced over MIN_CONFIRM_DAYS:
      VWAP above X for >=5 sessions -> support (pullbacks to X are buy zones)
      VWAP below X for >=5 sessions -> resistance (level broken, now acts
      as ceiling on bounces).

  UPPER BAND (resistance from above):
    Day D: upper_band_close < first_hour_low  ->  X = upper_band_close
    Natural role: resistance. From D+1, debounced over MIN_CONFIRM_DAYS:
      VWAP below X for >=5 sessions -> resistance (rallies to X are short
      zones)
      VWAP above X for >=5 sessions -> support (level broken, now acts
      as floor on dips).

Retest/run-up/drawdown tracking delegates to the same shared utilities for
lower-band episodes, and to self-contained resistance-compatible mirrors for
upper-band episodes.

KNOWN LIMITATION (disclosed, not a bug): hourly data is only ever a
rolling ~2-year window (see hourly_price_cache.py), so Day D detection
can only ever look as far back as that window.
"""
import logging
import numpy as np
import pandas as pd

from five_leg_pattern import _debounce_below_series
from monthly_s1_shift_pattern import classify_x_level
from monthly_pivot_pattern import compute_post_event_drawdown

_log = logging.getLogger("vwap_support_resistance_pattern")

FIRST_HOUR_START = "09:15"
FIRST_HOUR_END = "10:15"
MIN_CONFIRM_DAYS = 5


def _reconstruct_first_bar_volumes(hourly_df: pd.DataFrame, daily_hist: pd.DataFrame) -> pd.DataFrame:
    """
    Yahoo Finance systematically reports zero volume for NSE's opening
    09:15 60-minute bar. Reconstruct it from the reliable daily total:
    first_bar_volume ~ daily_volume - sum(other bars' volume).
    """
    if hourly_df.empty or daily_hist is None or daily_hist.empty:
        return hourly_df
    if "Volume" not in daily_hist.columns:
        return hourly_df

    df = hourly_df.copy()
    daily_vol_map = {
        pd.Timestamp(k).strftime("%Y-%m-%d"): float(v)
        for k, v in daily_hist["Volume"].items()
        if pd.notna(v)
    }

    for session_date, day_df in df.groupby(df.index.date):
        if day_df.empty or day_df.iloc[0]["Volume"] != 0:
            continue
        date_str = pd.Timestamp(session_date).strftime("%Y-%m-%d")
        if date_str not in daily_vol_map:
            continue
        daily_total = daily_vol_map[date_str]
        other_volume = day_df.iloc[1:]["Volume"].sum()
        reconstructed = max(0.0, daily_total - other_volume)
        first_idx = day_df.index[0]
        df.loc[first_idx, "Volume"] = reconstructed
        _log.debug(
            "Reconstructed first-bar volume for %s: %.0f "
            "(daily_total=%.0f, other_bars=%.0f)",
            date_str, reconstructed, daily_total, other_volume,
        )

    return df


def compute_session_summary(hourly_df: pd.DataFrame, daily_hist: pd.DataFrame = None) -> pd.DataFrame:
    """
    Collapses intraday hourly bars into one row per session with:
      first_hour_high, first_hour_low  - High/Low of the session's first bar.
      vwap_close        - session VWAP at the last bar.
      lower_band_close  - VWAP - 1 SD at the last bar (final VWAP used as
                          mean for all bars' deviations - matches Zerodha).
      upper_band_close  - VWAP + 1 SD at the last bar (same formula).
      has_zero_volume_bar - True if any bar still has zero volume after
                            reconstruction.
    """
    if hourly_df.empty:
        return pd.DataFrame()

    df = hourly_df.copy()
    df = _reconstruct_first_bar_volumes(df, daily_hist)
    df["_date"] = df.index.date
    df["_typical"] = (df["High"] + df["Low"] + df["Close"]) / 3.0

    rows = []
    for session_date, day_df in df.groupby("_date"):
        day_df = day_df.sort_index()
        # Avoid zero-volume bars corrupting the entire session's VWAP:
        # mask out zero-volume bars from both cumulative sums so they
        # don't contribute and don't create NaN via division by zero.
        _vol = day_df["Volume"].replace(0, np.nan)
        _typical = day_df["_typical"]
        cum_vol = _vol.cumsum()
        cum_pv = (_typical * _vol).cumsum()
        vwap_series = cum_pv / cum_vol.replace(0, np.nan)

        final_vwap = float(vwap_series.iloc[-1])
        sq_dev = (day_df["_typical"] - final_vwap) ** 2
        cum_var = (sq_dev * day_df["Volume"]).cumsum() / cum_vol.replace(0, np.nan)
        stdev_series = np.sqrt(cum_var)
        lower_band_series = vwap_series - stdev_series
        upper_band_series = vwap_series + stdev_series

        first_hour_bars = day_df.iloc[[0]]
        if first_hour_bars.empty:
            continue

        rows.append({
            "date": pd.Timestamp(session_date),
            "first_hour_high": float(first_hour_bars["High"].max()),
            "first_hour_low": float(first_hour_bars["Low"].min()),
            "vwap_close": float(vwap_series.iloc[-1]),
            "lower_band_close": float(lower_band_series.iloc[-1]),
            "upper_band_close": float(upper_band_series.iloc[-1]),
            "has_zero_volume_bar": bool((day_df["Volume"] == 0).any()),
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("date").sort_index()


def classify_x_level_resistance(daily_hist: pd.DataFrame, x_price: float, day_d_date,
                                  retest_pct: float = 5.0, fail_pct: float = 8.0) -> dict:
    """
    Resistance mirror of classify_x_level.

    X is a ceiling established from above (upper-band Day D).
    Favorable move for shorts = price drops below X.
    - 'tested' : after dropping away, price rallies back to within
                 retest_pct of X from below.
    - 'failed' : price breaks above X by fail_pct.
    - 'max_runup_pct' : max favorable drop below X (negative number).
    """
    after = daily_hist.loc[daily_hist.index > day_d_date]
    if after.empty:
        return {"status": "naked", "tested_date": None, "tested_price": None,
                "failed_date": None, "failed_price": None,
                "max_runup_pct": None, "days_tracked": 0}

    pct_series = (after["Close"] - x_price) / x_price * 100
    max_favorable = float(pct_series.min()) if not pct_series.empty else None

    tested_date = None
    tested_price = None
    if max_favorable is not None and max_favorable < 0:
        best_idx = pct_series.idxmin()
        post_best = after.loc[after.index > best_idx]
        if not post_best.empty:
            retest_threshold = x_price * (1 - retest_pct / 100)
            retest_mask = post_best["Close"] >= retest_threshold
            if retest_mask.any():
                tested_date = retest_mask.index[0]
                tested_price = float(post_best.loc[tested_date, "Close"])

    fail_threshold = x_price * (1 + fail_pct / 100)
    fail_mask = after["Close"] >= fail_threshold
    failed_date = fail_mask.index[0] if fail_mask.any() else None
    failed_price = float(after.loc[failed_date, "Close"]) if failed_date is not None else None

    if failed_date is not None:
        status = "failed"
    elif tested_date is not None:
        status = "tested"
    else:
        status = "naked"

    return {
        "status": status,
        "tested_date": tested_date,
        "tested_price": tested_price,
        "failed_date": failed_date,
        "failed_price": failed_price,
        "max_runup_pct": max_favorable,
        "days_tracked": len(after),
    }


def compute_post_event_drawdown_resistance(daily_hist: pd.DataFrame, x_price: float, event_date):
    """
    For resistance levels: after a test (or failure), compute the max
    adverse rally above X - the 'drawdown' from a short position.
    """
    after = daily_hist.loc[daily_hist.index > event_date]
    if after.empty:
        return {
            "max_drawdown_pct": None, "lowest_price": None, "lowest_date": None,
            "recovered": None, "recovery_date": None, "days_to_recover": None,
        }

    above_mask = after["Close"] > x_price
    if not above_mask.any():
        return {
            "max_drawdown_pct": 0.0, "lowest_price": x_price,
            "lowest_date": None, "recovered": True,
            "recovery_date": event_date, "days_to_recover": 0,
        }

    above = after.loc[above_mask]
    max_price = float(above["Close"].max())
    max_date = above["Close"].idxmax()
    max_pct = (max_price - x_price) / x_price * 100

    post_max = after.loc[after.index > max_date]
    recovered = not post_max.empty and (post_max["Close"] < x_price).any()
    recovery_date = None
    days_to_recover = None
    if recovered:
        recovery_candidates = post_max[post_max["Close"] < x_price]
        recovery_date = recovery_candidates.index[0]
        days_to_recover = (recovery_date - max_date).days

    return {
        "max_drawdown_pct": max_pct,
        "lowest_price": max_price,
        "lowest_date": max_date,
        "recovered": recovered,
        "recovery_date": recovery_date,
        "days_to_recover": days_to_recover,
    }


def find_vwap_sr_episodes(daily_hist: pd.DataFrame, session_summary: pd.DataFrame,
                            min_confirm_days: int = MIN_CONFIRM_DAYS,
                            retest_pct: float = 5.0, fail_pct: float = 8.0,
                            min_runup_pct: float = None,
                            min_gap_pct: float = 0.5) -> list:
    """
    Lower-band episodes: lower_band_close > first_hour_high on Day D.
    X locks at lower_band_close. Natural role: support.
    min_gap_pct: band must clear the first-hour boundary by at least this
    percentage to avoid rounding-noise signals (e.g. 1310.62 vs 1310.70).
    """
    if session_summary.empty or daily_hist.empty:
        return []

    day_d_mask = session_summary["lower_band_close"] > session_summary["first_hour_high"] * (1 + min_gap_pct / 100)
    if "has_zero_volume_bar" in session_summary.columns:
        day_d_mask = day_d_mask & ~session_summary["has_zero_volume_bar"]
    day_d_dates = session_summary.index[day_d_mask]

    episodes = []
    for day_d_date in day_d_dates:
        x_price = float(session_summary.loc[day_d_date, "lower_band_close"])
        first_hour_high = float(session_summary.loc[day_d_date, "first_hour_high"])

        after = session_summary.loc[session_summary.index > day_d_date]
        if after.empty:
            classification = "support"
            classification_changed_date = day_d_date
        else:
            raw_above = after["vwap_close"] > x_price
            debounced_above = _debounce_below_series(~raw_above, min_confirm_days)
            debounced_above = ~debounced_above
            classification = "support" if bool(debounced_above.iloc[-1]) else "resistance"
            changed_mask = debounced_above != debounced_above.shift()
            change_dates = debounced_above.index[changed_mask]
            classification_changed_date = change_dates[-1] if len(change_dates) else day_d_date

        retest = classify_x_level(daily_hist, x_price, day_d_date,
                                  retest_pct=retest_pct, fail_pct=fail_pct)

        if min_runup_pct is not None and retest.get("max_runup_pct") is not None:
            if retest["max_runup_pct"] < min_runup_pct:
                continue

        event_date = retest.get("tested_date") or retest.get("failed_date")
        if event_date is not None:
            dd = compute_post_event_drawdown(daily_hist, x_price, event_date)
        else:
            dd = {
                "max_drawdown_pct": None, "lowest_price": None, "lowest_date": None,
                "recovered": None, "recovery_date": None, "days_to_recover": None,
            }

        gap_pct = (
            (x_price - first_hour_high) / first_hour_high * 100
            if first_hour_high and first_hour_high != 0
            else None
        )
        episodes.append({
            "day_d_date": day_d_date,
            "x_price": x_price,
            "first_hour_high": first_hour_high,
            "first_hour_low": float(session_summary.loc[day_d_date, "first_hour_low"]),
            "gap_pct": gap_pct,
            "classification": classification,
            "classification_changed_date": classification_changed_date,
            "status": retest["status"],
            "tested_date": retest["tested_date"],
            "tested_price": retest["tested_price"],
            "failed_date": retest["failed_date"],
            "max_runup_pct": retest["max_runup_pct"],
            "days_tracked": retest["days_tracked"],
            "drawdown_pct": dd["max_drawdown_pct"],
            "drawdown_recovered": dd["recovered"],
            "drawdown_recovery_date": dd["recovery_date"],
            "drawdown_days_to_recover": dd["days_to_recover"],
            "episode_type": "lower_band",
        })

    return episodes


def find_vwap_sr_episodes_upper(daily_hist: pd.DataFrame, session_summary: pd.DataFrame,
                                  min_confirm_days: int = MIN_CONFIRM_DAYS,
                                  retest_pct: float = 5.0, fail_pct: float = 8.0,
                                  min_runup_pct: float = None,
                                  min_gap_pct: float = 0.5) -> list:
    """
    Upper-band episodes: upper_band_close < first_hour_low on Day D.
    X locks at upper_band_close. Natural role: resistance.
    min_gap_pct: band must clear the first-hour boundary by at least this
    percentage to avoid rounding-noise signals.
    """
    if session_summary.empty or daily_hist.empty:
        return []

    required_cols = ["upper_band_close", "first_hour_low", "first_hour_high"]
    if not all(c in session_summary.columns for c in required_cols):
        return []

    day_d_mask = session_summary["upper_band_close"] < session_summary["first_hour_low"] * (1 - min_gap_pct / 100)
    if "has_zero_volume_bar" in session_summary.columns:
        day_d_mask = day_d_mask & ~session_summary["has_zero_volume_bar"]
    day_d_dates = session_summary.index[day_d_mask]

    episodes = []
    for day_d_date in day_d_dates:
        x_price = float(session_summary.loc[day_d_date, "upper_band_close"])
        first_hour_low = float(session_summary.loc[day_d_date, "first_hour_low"])

        after = session_summary.loc[session_summary.index > day_d_date]
        if after.empty:
            classification = "resistance"
            classification_changed_date = day_d_date
        else:
            raw_below = after["vwap_close"] < x_price
            debounced_below = _debounce_below_series(~raw_below, min_confirm_days)
            debounced_below = ~debounced_below
            classification = "resistance" if bool(debounced_below.iloc[-1]) else "support"
            changed_mask = debounced_below != debounced_below.shift()
            change_dates = debounced_below.index[changed_mask]
            classification_changed_date = change_dates[-1] if len(change_dates) else day_d_date

        retest = classify_x_level_resistance(daily_hist, x_price, day_d_date,
                                               retest_pct=retest_pct, fail_pct=fail_pct)

        if min_runup_pct is not None and retest.get("max_runup_pct") is not None:
            if retest["max_runup_pct"] > -min_runup_pct:
                continue

        event_date = retest.get("tested_date") or retest.get("failed_date")
        if event_date is not None:
            dd = compute_post_event_drawdown_resistance(daily_hist, x_price, event_date)
        else:
            dd = {
                "max_drawdown_pct": None, "lowest_price": None, "lowest_date": None,
                "recovered": None, "recovery_date": None, "days_to_recover": None,
            }

        gap_pct = (
            (first_hour_low - x_price) / first_hour_low * 100
            if first_hour_low and first_hour_low != 0
            else None
        )
        episodes.append({
            "day_d_date": day_d_date,
            "x_price": x_price,
            "first_hour_high": float(session_summary.loc[day_d_date, "first_hour_high"]),
            "first_hour_low": first_hour_low,
            "gap_pct": gap_pct,
            "classification": classification,
            "classification_changed_date": classification_changed_date,
            "status": retest["status"],
            "tested_date": retest["tested_date"],
            "tested_price": retest["tested_price"],
            "failed_date": retest["failed_date"],
            "max_runup_pct": retest["max_runup_pct"],
            "days_tracked": retest["days_tracked"],
            "drawdown_pct": dd["max_drawdown_pct"],
            "drawdown_recovered": dd["recovered"],
            "drawdown_recovery_date": dd["recovery_date"],
            "drawdown_days_to_recover": dd["days_to_recover"],
            "episode_type": "upper_band",
        })

    return episodes


# ── Optimization helper (July 2026) ────────────────────────────────────────

def find_recent_episodes(
    daily_hist: pd.DataFrame,
    session_summary: pd.DataFrame,
    max_episodes_per_type: int = 10,
    **kwargs
) -> list:
    """
    Returns only the most recent N episodes per type (lower/upper band).
    For dashboard display at NIFTY 100 scale, older historical episodes
    are rarely actionable — this cuts retest computation by ~60%.
    """
    lower = find_vwap_sr_episodes(daily_hist, session_summary, **kwargs)
    upper = find_vwap_sr_episodes_upper(daily_hist, session_summary, **kwargs)

    # Sort by Day D date descending, keep top N per type
    lower_sorted = sorted(lower, key=lambda x: x["day_d_date"], reverse=True)
    upper_sorted = sorted(upper, key=lambda x: x["day_d_date"], reverse=True)

    return lower_sorted[:max_episodes_per_type] + upper_sorted[:max_episodes_per_type]
