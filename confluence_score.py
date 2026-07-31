"""
confluence_score.py
---------------------
Computes a "Confluence Master Score" for every symbol in the universe by
aggregating signals across ALL seven strategy ledgers into a single ranked
table. Designed to auto-refresh every time fetch_metrics() runs — no manual
CSV export/import needed.

═══════════════════════════════════════════════════════════════════════════
PHILOSOPHY
═══════════════════════════════════════════════════════════════════════════

The other AI's CSV-export approach is fragile: seven files, manually
uploaded, with descriptions that mis-map the strategies. This module reads
directly from the SAME SQLite database the dashboard already writes to, so
the score is always in sync with the latest scan.

For long-term SIP / lumpsum investing (3-5+ year horizon, 30% tax bracket):
  • Capital gains (LTCG after 1 year) taxed at 12.5% → prefer growth over yield
  • Dividends taxed at slab rate (30%) → penalize high-dividend traps
  • Goal: buy structurally strong stocks near proven support, not at tops

═══════════════════════════════════════════════════════════════════════════
SCORING FRAMEWORK  (max raw score = 100 points before normalization)
═══════════════════════════════════════════════════════════════════════════

SECTION A — STRATEGY CONFLUENCE  (max 56 pts)
  Each strategy contributes independently. A stock that triggers across
  multiple timeframes/strategies has real multi-layer support.

  A1. 200 EMA Streak (reference_levels table)           max 12 pts
      • "reclaimed_pending_retest" status               +6  (best: confirmed long-term support, not yet tested)
      • "reclaimed_retested" (tested and held)          +8  (proven support — tested and bounced)
      • "testing_resistance" (approaching from below)   +4  (early, unconfirmed)
      • "naked" (never approached)                      +2  (level exists, no test yet)
      • Streak total days >= 200                        +2  (longer streaks = stronger historical momentum)
      • Streak total days >= 365                        +2  (extra for truly sustained runs)

  A2. 5-Leg EMA Reversal (five_leg_episodes)            max 10 pts
      • "probe_complete" status                         +8  (full reversal confirmed)
      • "above_200_complete"                            +6  (completed above 200 EMA — rarer, bullish)
      • "pattern_forming" / "above_200_forming"         +3  (still forming — note but don't over-weight)
      • Current price within 10% of Y (buy level)      +2  (proximity bonus — actionable NOW)

  A3. Monthly Pivot S1 (monthly_pivot_episodes)         max 8 pts
      • "complete" status                               +5
      • "x_fixed_pending_cross"                         +3
      • "tracking_x"                                    +1
      • Current price within 8% of Y                   +3  (proximity bonus)

  A4. Monthly S1 Shift Up (monthly_s1_shift_episodes)   max 8 pts
      • "tested" (retested and held)                    +6
      • "naked" (signal active, not yet retested)       +4
      • "failed" (support broken)                       -2  (penalty)
      • Current price within 8% of X                   +2  (proximity bonus)

  A5. Breakout-Pullback 4-Leg (breakout_pullback)       max 8 pts
      • signal_date is set (signal fired)               +5
      • Y-retest "tested"                               +2  (level held)
      • Z-retest "tested"                               +1  (secondary level held)
      • "failed"                                        -3  (penalty)
      • Current price within 8% of Y or Z              +2

  A6. EMA Pullback Reentry (ema_pullback_episodes)      max 6 pts
      • "naked" (signal fired, Y pristine)              +5  (cleanest — never tested)
      • "tested" (retested and held)                    +4  (confirmed support)
      • "failed"                                        -2
      • "signal_pending"                                +2  (forming, not complete)
      • Current price within 8% of Y                   +1

  A7. Supertrend 3-Phase (supertrend_episodes)          max 4 pts per variant,
                                                         max 8 pts total (2 variants)
      For each variant that has a "complete" episode:
      • "complete" status                               +3
      • Y or Z status "tested" (held)                  +1
      • Current price within 8% of Y or Z              +1
      • "phase3_pending_st_ema" or "signal_fired"      +1  (partial credit)

SECTION B — TREND HEALTH  (max 24 pts)
  B1. Currently above 200 EMA                           +6 / 0
  B2. TrendDays (consecutive days in current EMA state):
      • Above 200 EMA for 1–49 days                    +2  (fresh breakout)
      • Above 200 EMA for 50–199 days                  +4  (sustained trend)
      • Above 200 EMA for 200+ days                    +6  (structural bull)
      • Below 200 EMA (streak = negative): no points
  B3. %From200EMA:
      • Between 0% and +10% above EMA                  +6  (ideal buy zone near EMA)
      • Between +10% and +20%                           +4  (still OK, slightly extended)
      • Between +20% and +35%                           +2  (extended, caution)
      • >+35% above or any below                       +0
  B4. Day change % (momentum confirmation):
      • DayChg% > +1%                                  +2
      • DayChg% between 0% and +1%                     +1
      • Negative day change                             +0
  B5. %FromHigh (how far below 52W high — opportunity):
      • Between -5% and -20%  (healthy pullback)       +4
      • Between -20% and -35% (deeper pullback)        +2
      • <-35% or >-5% (extreme decline or near-top)    +0

SECTION C — PROXIMITY URGENCY  (max 20 pts)
  "Is price actually near a buy level RIGHT NOW?"
  Finds the closest active (non-failed) buy level across ALL strategies
  and scores based on proximity. This distinguishes a "theoretically good
  stock" from one that is actually at an entry point today.

  Closest level within:
  • 0–3% of current price                              +20  (pull the trigger)
  • 3–6%                                               +15  (very close)
  • 6–10%                                              +10  (approaching)
  • 10–15%                                             +5   (on radar)
  • >15% or no levels found                            +0

═══════════════════════════════════════════════════════════════════════════
NORMALIZATION
═══════════════════════════════════════════════════════════════════════════
Raw scores are min-max normalized to a 0–10 scale ACROSS the current
universe, so the top stock always shows 10.0 and relative differences are
preserved. This makes the table intuitive and re-ranks automatically as
new signals fire or fade.

═══════════════════════════════════════════════════════════════════════════
COMBINED MASTER SCORE (technical + fundamental)
═══════════════════════════════════════════════════════════════════════════
When fundamental scores are available, a Combined_Score is produced:
  Combined_Score = 0.60 × Technical_Score + 0.40 × Fundamental_Score

Rationale for 60/40 split:
  • Technical score already captures entry timing and trend quality (the
    "when to buy" question). The 60% weight ensures a stock with perfect
    technicals but patchy fundamental data still scores reasonably.
  • Fundamental score captures business quality (the "what to buy"
    question). 40% weight means a stock must have at least decent
    fundamentals to rank highly — pure momentum plays get penalized.
  • If fundamental data is missing (F_data_fields = 0), Combined_Score
    falls back to the Technical_Score alone with a visual warning.

SIP vs LUMPSUM RECOMMENDATION (based on Combined_Score + proximity):
  • Combined >= 7.5 AND within 5% of buy level        → "🟢 Lumpsum NOW"
  • Combined >= 6.0 AND within 10%                    → "🟡 Lumpsum on dip"
  • Combined >= 5.0                                    → "🔵 SIP (add monthly)"
  • Combined >= 3.0                                    → "⚪ Watchlist"
  • Combined < 3.0                                     → "⛔ Avoid"

QUALITY GATE (two-tier):
  ❌ Weak        → capped at "🔵 SIP monthly". No lumpsum of any kind.
                   A structurally weak business does not get a buy-on-dip
                   recommendation regardless of technical setup.
  🔶 Below Avg  → capped at "🟡 Lumpsum on dip". Good technicals can flag
                   it as worth buying on a pullback, but never "Lumpsum NOW".
  Average+      → normal score/proximity thresholds apply.
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta


# ───────────────────────────────────────────────────────────────────────────
# SCORING CONFIGURATION
# All numeric weights live here. To re-tune, change the values in this dict
# rather than hunting through the scoring functions below.
# ───────────────────────────────────────────────────────────────────────────

SCORE_CONFIG = {
    # Section A1 — 200 EMA Streak
    "streak_status_pts": {
        "reclaimed_retested":       8,
        "reclaimed_pending_retest": 6,
        "testing_resistance":       4,
        "naked":                    2,
    },
    "streak_days_bonus_365": 4,   # extra pts for streak >= 365 days
    "streak_days_bonus_200": 2,   # extra pts for streak >= 200 days

    # Section A2 — 5-Leg EMA Reversal
    "five_leg_status_pts": {
        "probe_complete":     8,
        "above_200_complete": 6,
        "pattern_forming":    3,
        "above_200_forming":  3,
    },
    "five_leg_proximity_pct":   10,   # % within Y to earn proximity bonus
    "five_leg_proximity_bonus":  2,

    # Section A3 — Monthly Pivot S1
    "pivot_status_pts": {
        "complete":              5,
        "x_fixed_pending_cross": 3,
        "tracking_x":            1,
    },
    "pivot_proximity_pct":   8,
    "pivot_proximity_bonus": 3,

    # Section A4 — Monthly S1 Shift Up
    "s1_status_pts": {"tested": 6, "naked": 4, "failed": -2},
    "s1_proximity_pct":   8,
    "s1_proximity_bonus": 2,

    # Section A5 — Breakout-Pullback 4-Leg
    "bp_signal_pts":         5,
    "bp_y_retest_bonus":     2,
    "bp_z_retest_bonus":     1,
    "bp_failed_penalty":    -3,
    "bp_proximity_pct":      8,
    "bp_proximity_bonus":    2,

    # Section A6 — EMA Pullback Reentry
    "ep_status_pts": {"naked": 5, "tested": 4, "failed": -2, "signal_pending": 2},
    "ep_proximity_pct":   8,
    "ep_proximity_bonus": 1,

    # Section A7 — Supertrend 3-Phase (per variant, capped at max_total)
    "st_complete_pts":       3,
    "st_tested_bonus":       1,   # bonus if Y or Z status is "tested"
    "st_partial_pts":        1,   # for phase3_pending_st_ema / signal_fired
    "st_proximity_pct":      8,
    "st_proximity_bonus":    1,
    "st_max_total":          8,   # cap across all variants

    # Section B — Trend Health
    "b1_above_pts":          6,
    "b2_days_200_pts":       6,
    "b2_days_50_pts":        4,
    "b2_days_1_pts":         2,
    "b3_ema_0_10_pts":       6,
    "b3_ema_10_20_pts":      4,
    "b3_ema_20_35_pts":      2,
    "b4_day_chg_1_pts":      2,
    "b4_day_chg_0_pts":      1,
    "b5_pullback_5_20_pts":  4,
    "b5_pullback_20_35_pts": 2,

    # Section C — Proximity Urgency (closest buy level)
    "proximity_within_3":   20,
    "proximity_within_6":   15,
    "proximity_within_10":  10,
    "proximity_within_15":   5,

    # Combined score blend
    "technical_weight":    0.60,
    "fundamental_weight":  0.40,

    # Verdict thresholds
    "verdict_lumpsum_now_score":  7.5,
    "verdict_lumpsum_now_prox":   5.0,
    "verdict_lumpsum_dip_score":  6.0,
    "verdict_lumpsum_dip_prox":  10.0,
    "verdict_sip_score":          5.0,
    "verdict_watchlist_score":    3.0,
}


# ───────────────────────────────────────────────────────────────────────────
# HELPERS
# ───────────────────────────────────────────────────────────────────────────

def _pct_from(price, level):
    """% distance of price from level (positive = above, negative = below)."""
    if level is None or pd.isna(level) or level == 0:
        return np.nan
    return (price - level) / level * 100


def _abs_pct(price, level):
    v = _pct_from(price, level)
    return abs(v) if not pd.isna(v) else np.nan


def _proximity_pts(pct_away_abs):
    """Points for being within a certain % of a buy level."""
    if pd.isna(pct_away_abs):
        return 0
    cfg = SCORE_CONFIG
    if pct_away_abs <= 3:
        return cfg["proximity_within_3"]
    if pct_away_abs <= 6:
        return cfg["proximity_within_6"]
    if pct_away_abs <= 10:
        return cfg["proximity_within_10"]
    if pct_away_abs <= 15:
        return cfg["proximity_within_15"]
    return 0


def _latest_episode(df: pd.DataFrame, symbol: str, date_col: str = None):
    """Return the most recent (by date_col) row for symbol, or None."""
    sub = df[df["symbol"] == symbol] if "symbol" in df.columns else pd.DataFrame()
    if sub.empty:
        return None
    if date_col and date_col in sub.columns:
        sub = sub.sort_values(date_col, ascending=False)
    return sub.iloc[0]


_FAILED_STATUSES = {"failed", "x_failed", "y_failed", "z_failed"}


def _best_episode(df: pd.DataFrame, symbol: str, date_col: str = None):
    """
    Return the MOST RECENTLY STARTED episode for a symbol that isn't 'failed'.
    Uses explicit set membership (not str.contains) to avoid matching partial
    strings like 'pre_signal_failed_leg'. Falls back to most recent if all
    episodes are failed.
    """
    sub = df[df["symbol"] == symbol] if "symbol" in df.columns else pd.DataFrame()
    if sub.empty:
        return None
    if "status" in sub.columns:
        non_failed = sub[~sub["status"].isin(_FAILED_STATUSES)]
    else:
        non_failed = sub
    pool = non_failed if not non_failed.empty else sub
    if date_col and date_col in pool.columns:
        pool = pool.sort_values(date_col, ascending=False)
    return pool.iloc[0]


# ───────────────────────────────────────────────────────────────────────────
# SECTION A — per-strategy scoring
# ───────────────────────────────────────────────────────────────────────────

def _score_streak(row_metrics, sub: pd.DataFrame) -> dict:
    price = row_metrics["Price"]
    pts   = 0

    if sub is None or sub.empty:
        return {"A1_pts": 0, "A1_status": None, "A1_x": None, "A1_pct": None}

    # Find best unresolved (most actionable) streak
    priority = {"reclaimed_retested": 5, "reclaimed_pending_retest": 4,
                "testing_resistance": 3, "naked": 2}
    sub = sub.copy()
    sub["_pri"] = sub["status"].map(priority).fillna(0)
    best = sub.sort_values("_pri", ascending=False).iloc[0]
    status = best.get("status")
    x = best.get("x_price")
    total_days = best.get("total_streak_days") or 0

    cfg = SCORE_CONFIG
    status_pts = cfg["streak_status_pts"].get(status, 0)
    pts += status_pts
    if total_days >= 365:
        pts += cfg["streak_days_bonus_365"]
    elif total_days >= 200:
        pts += cfg["streak_days_bonus_200"]

    pct = _pct_from(price, x)
    return {"A1_pts": pts, "A1_status": status,
            "A1_x": x, "A1_pct": pct, "A1_streak_days": total_days}


def _score_five_leg(row_metrics, sub: pd.DataFrame) -> dict:
    price = row_metrics["Price"]
    pts   = 0

    if sub is None or sub.empty:
        return {"A2_pts": 0, "A2_status": None, "A2_y": None, "A2_pct": None}

    cfg = SCORE_CONFIG
    status_pts = cfg["five_leg_status_pts"]
    sub = sub.copy()
    sub["_sp"] = sub["status"].map(status_pts).fillna(0)
    best = sub.sort_values("_sp", ascending=False).iloc[0]
    status = best.get("status")
    pts += status_pts.get(status, 0)
    y = best.get("y_price")
    pct = _abs_pct(price, y)
    if not pd.isna(pct) and pct <= cfg["five_leg_proximity_pct"]:
        pts += cfg["five_leg_proximity_bonus"]

    return {"A2_pts": pts, "A2_status": status, "A2_y": y, "A2_pct": _pct_from(price, y)}


def _score_monthly_pivot(row_metrics, sub: pd.DataFrame) -> dict:
    price = row_metrics["Price"]
    pts   = 0

    if sub is None or sub.empty:
        return {"A3_pts": 0, "A3_status": None, "A3_y": None, "A3_pct": None}

    cfg = SCORE_CONFIG
    status_pts = cfg["pivot_status_pts"]
    sub = sub.copy()
    sub["_sp"] = sub["status"].map(status_pts).fillna(0)
    best = sub.sort_values("_sp", ascending=False).iloc[0]
    status = best.get("status")
    pts += status_pts.get(status, 0)
    y = best.get("y_price")
    x = best.get("x_price")
    level = y if (y and not pd.isna(y)) else x
    pct = _abs_pct(price, level)
    if not pd.isna(pct) and pct <= cfg["pivot_proximity_pct"]:
        pts += cfg["pivot_proximity_bonus"]

    return {"A3_pts": pts, "A3_status": status,
            "A3_y": y, "A3_pct": _pct_from(price, level)}


def _score_s1_shift(row_metrics, sub: pd.DataFrame) -> dict:
    price = row_metrics["Price"]
    pts   = 0

    if sub is None or sub.empty:
        return {"A4_pts": 0, "A4_status": None, "A4_x": None, "A4_pct": None}

    cfg = SCORE_CONFIG
    if "anchor_date" in sub.columns:
        sub = sub.sort_values("anchor_date", ascending=False)
    best = sub.iloc[0]
    status = best.get("status")
    x = best.get("x_price")

    pts += cfg["s1_status_pts"].get(status, 0)
    pct = _abs_pct(price, x)
    if not pd.isna(pct) and pct <= cfg["s1_proximity_pct"]:
        pts += cfg["s1_proximity_bonus"]

    return {"A4_pts": pts, "A4_status": status,
            "A4_x": x, "A4_pct": _pct_from(price, x)}


def _score_breakout_pullback(row_metrics, sub: pd.DataFrame) -> dict:
    price = row_metrics["Price"]
    pts   = 0

    if sub is None or sub.empty:
        return {"A5_pts": 0, "A5_status": None, "A5_y": None, "A5_pct": None}

    cfg = SCORE_CONFIG
    if "signal_date" in sub.columns:
        sub = sub.sort_values("signal_date", ascending=False, na_position="last")
    best = sub.iloc[0]
    status = best.get("status")

    has_signal = pd.notna(best.get("signal_date"))
    if has_signal:
        pts += cfg["bp_signal_pts"]
    y_ret = best.get("y_retest_status")
    z_ret = best.get("z_retest_status")
    if y_ret == "tested":
        pts += cfg["bp_y_retest_bonus"]
    if z_ret == "tested":
        pts += cfg["bp_z_retest_bonus"]
    if status == "failed":
        pts += cfg["bp_failed_penalty"]

    y = best.get("y_price")
    z = best.get("z_price")
    best_pct = min(
        _abs_pct(price, y) if y else np.inf,
        _abs_pct(price, z) if z else np.inf,
    )
    if best_pct <= cfg["bp_proximity_pct"]:
        pts += cfg["bp_proximity_bonus"]
    closest = y if (_abs_pct(price, y) or np.inf) < (_abs_pct(price, z) or np.inf) else z

    return {"A5_pts": pts, "A5_status": status,
            "A5_y": y, "A5_pct": _pct_from(price, closest)}


def _score_ema_pullback(row_metrics, sub: pd.DataFrame) -> dict:
    price = row_metrics["Price"]
    pts   = 0

    if sub is None or sub.empty:
        return {"A6_pts": 0, "A6_status": None, "A6_y": None, "A6_pct": None}

    cfg = SCORE_CONFIG
    status_pts = cfg["ep_status_pts"]
    sub = sub.copy()
    sub["_sp"] = sub["status"].map(status_pts).fillna(0)
    best = sub.sort_values("_sp", ascending=False).iloc[0]
    status = best.get("status")
    pts += status_pts.get(status, 0)
    y = best.get("y_price")
    pct = _abs_pct(price, y)
    if not pd.isna(pct) and pct <= cfg["ep_proximity_pct"]:
        pts += cfg["ep_proximity_bonus"]

    return {"A6_pts": pts, "A6_status": status,
            "A6_y": y, "A6_pct": _pct_from(price, y)}


def _score_supertrend(row_metrics, sub: pd.DataFrame) -> dict:
    price = row_metrics["Price"]
    pts   = 0

    if sub is None or sub.empty:
        return {"A7_pts": 0, "A7_status": None, "A7_y": None, "A7_pct": None}

    cfg = SCORE_CONFIG
    total_pts = 0
    best_y = None
    best_y_pct = np.inf
    best_status = None

    # Score each unique variant independently, cap total at st_max_total
    for _, ep in sub.iterrows():
        status = ep.get("status")
        vpts = 0
        if status == "complete":
            vpts += cfg["st_complete_pts"]
            for lvl in ("y_status", "z_status"):
                if ep.get(lvl) == "tested":
                    vpts += cfg["st_tested_bonus"]
                    break
        elif status in ("phase3_pending_st_ema", "signal_fired"):
            vpts += cfg["st_partial_pts"]

        y = ep.get("y_price")
        z = ep.get("z_price")
        for lvl in (y, z):
            pct = _abs_pct(price, lvl)
            if not pd.isna(pct) and pct <= cfg["st_proximity_pct"]:
                vpts += cfg["st_proximity_bonus"]
            if not pd.isna(pct) and pct < best_y_pct:
                best_y_pct = pct
                best_y = lvl
                best_status = status

        total_pts += vpts

    pts = min(total_pts, cfg["st_max_total"])
    pct_signed = _pct_from(price, best_y)

    return {"A7_pts": pts, "A7_status": best_status,
            "A7_y": best_y, "A7_pct": pct_signed}


# ───────────────────────────────────────────────────────────────────────────
# SECTION B — trend health
# ───────────────────────────────────────────────────────────────────────────

def _score_trend(row_metrics) -> dict:
    cfg = SCORE_CONFIG
    pts = 0
    above = row_metrics.get("CurrentlyAbove200EMA", False)
    trend_days = row_metrics.get("TrendDays", 0) or 0
    pct_from_ema = row_metrics.get("%From200EMA", np.nan)
    day_chg = row_metrics.get("DayChg%", np.nan)
    pct_from_high = row_metrics.get("%FromHigh", np.nan)

    # B1
    if above:
        pts += cfg["b1_above_pts"]

    # B2 — only points if above
    if above:
        if trend_days >= 200:
            pts += cfg["b2_days_200_pts"]
        elif trend_days >= 50:
            pts += cfg["b2_days_50_pts"]
        elif trend_days >= 1:
            pts += cfg["b2_days_1_pts"]

    # B3 — proximity to 200 EMA
    if not pd.isna(pct_from_ema):
        if 0 <= pct_from_ema <= 10:
            pts += cfg["b3_ema_0_10_pts"]
        elif 10 < pct_from_ema <= 20:
            pts += cfg["b3_ema_10_20_pts"]
        elif 20 < pct_from_ema <= 35:
            pts += cfg["b3_ema_20_35_pts"]

    # B4 — daily momentum
    if not pd.isna(day_chg):
        if day_chg > 1:
            pts += cfg["b4_day_chg_1_pts"]
        elif day_chg > 0:
            pts += cfg["b4_day_chg_0_pts"]

    # B5 — pullback from 52W high
    if not pd.isna(pct_from_high):
        if -20 <= pct_from_high <= -5:
            pts += cfg["b5_pullback_5_20_pts"]
        elif -35 <= pct_from_high < -20:
            pts += cfg["b5_pullback_20_35_pts"]

    return {"B_pts": pts}

    return {"B_pts": pts}


# ───────────────────────────────────────────────────────────────────────────
# SECTION C — proximity urgency (closest buy level across ALL strategies)
# ───────────────────────────────────────────────────────────────────────────

def _score_proximity(price, level_scores: dict) -> dict:
    """
    Collect all buy levels from all strategy scores and find the closest one.
    level_scores: dict of {Ax_y, Ax_pct} values already computed.

    Only levels where price is AT or ABOVE the level receive proximity points.
    A level that price has already breached downward (negative pct = price below
    level) is now overhead resistance, not support — proximity to it from below
    does not make it an actionable buy level, so it scores 0 proximity points.
    It is still recorded as the closest level for display purposes so the user
    can see the distance, but the C_pts contribution is zero.
    """
    all_levels = []
    for key in ("A1_x", "A2_y", "A3_y", "A4_x", "A5_y", "A6_y", "A7_y"):
        lvl = level_scores.get(key)
        if lvl and not pd.isna(lvl):
            all_levels.append(float(lvl))

    if not all_levels:
        return {"C_pts": 0, "closest_level": None, "closest_pct": None}

    pcts = [_pct_from(price, lvl) for lvl in all_levels]
    valid = [(p, l) for p, l in zip(pcts, all_levels) if not pd.isna(p)]
    if not valid:
        return {"C_pts": 0, "closest_level": None, "closest_pct": None}

    # Closest level by absolute distance (for display — always shown)
    best_pct_abs, best_level = min(valid, key=lambda x: abs(x[0]))

    # Proximity POINTS only from levels where price >= level (approaching from above).
    # Price below a level means the level has been breached — it's now resistance.
    above_levels = [(p, l) for p, l in valid if p >= 0]
    if above_levels:
        scoring_pct_abs = min(abs(p) for p, _ in above_levels)
    else:
        scoring_pct_abs = None  # all levels breached — no proximity credit

    pts = _proximity_pts(scoring_pct_abs) if scoring_pct_abs is not None else 0

    return {
        "C_pts": pts,
        "closest_level": best_level,
        "closest_pct": best_pct_abs,   # signed: negative means price already below
    }


# ───────────────────────────────────────────────────────────────────────────
# VERDICT
# ───────────────────────────────────────────────────────────────────────────

def _verdict(score_10: float, closest_pct, quality_tier: str = None) -> str:
    """
    SIP vs lumpsum recommendation based on combined score, proximity, and
    fundamental quality tier.

    Quality Gate (applied before score thresholds):
      Weak        -> never above SIP monthly (no lumpsum of any kind)
      Below Avg   -> never above Lumpsum on dip (no Lumpsum NOW)
      Average+    -> normal score/proximity thresholds apply
    """
    cfg  = SCORE_CONFIG
    prox = abs(closest_pct) if (closest_pct is not None and not pd.isna(closest_pct)) else 999
    weak      = (quality_tier == "❌ Weak")          if quality_tier else False
    below_avg = (quality_tier == "🔶 Below Average") if quality_tier else False

    if weak:
        if score_10 >= cfg["verdict_sip_score"]:
            return "🔵 SIP monthly"
        if score_10 >= cfg["verdict_watchlist_score"]:
            return "⚪ Watchlist"
        return "⛔ Avoid / skip"

    if below_avg:
        if score_10 >= cfg["verdict_lumpsum_dip_score"] and prox <= cfg["verdict_lumpsum_dip_prox"]:
            return "🟡 Lumpsum on dip"
        if score_10 >= cfg["verdict_sip_score"]:
            return "🔵 SIP monthly"
        if score_10 >= cfg["verdict_watchlist_score"]:
            return "⚪ Watchlist"
        return "⛔ Avoid / skip"

    if score_10 >= cfg["verdict_lumpsum_now_score"] and prox <= cfg["verdict_lumpsum_now_prox"]:
        return "🟢 Lumpsum NOW"
    if score_10 >= cfg["verdict_lumpsum_dip_score"] and prox <= cfg["verdict_lumpsum_dip_prox"]:
        return "🟡 Lumpsum on dip"
    if score_10 >= cfg["verdict_sip_score"]:
        return "🔵 SIP monthly"
    if score_10 >= cfg["verdict_watchlist_score"]:
        return "⚪ Watchlist"
    return "⛔ Avoid / skip"


def _strategy_flags(scores: dict) -> str:
    """
    Compact string listing which strategies fired for this symbol —
    makes it easy to see at a glance why a stock ranked where it did.
    Example: "ST✅ EMA✅ 5L✅ BP⚠ Piv—"
    """
    flags = []
    for code, key, label in [
        ("A7", "A7_status", "ST"),
        ("A1", "A1_status", "200EMA"),
        ("A2", "A2_status", "5-Leg"),
        ("A6", "A6_status", "EP"),
        ("A5", "A5_status", "BP"),
        ("A3", "A3_status", "Piv"),
        ("A4", "A4_status", "S1Sh"),
    ]:
        st = scores.get(key)
        if st is None:
            flags.append(f"{label}—")
        elif "fail" in str(st).lower():
            flags.append(f"{label}⚠")
        elif st in ("naked", "signal_pending", "tracking_x", "pattern_forming",
                    "above_200_forming", "phase3_pending_st_ema"):
            flags.append(f"{label}◷")
        else:
            flags.append(f"{label}✅")
    return " | ".join(flags)


# ───────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ───────────────────────────────────────────────────────────────────────────

def build_confluence_table(
    metrics_df: pd.DataFrame,
    streak_df: pd.DataFrame,
    five_df: pd.DataFrame,
    pivot_df: pd.DataFrame,
    s1_shift_df: pd.DataFrame,
    bp_df: pd.DataFrame,
    ep_df: pd.DataFrame,
    st_df: pd.DataFrame,
    top_n: int = 30,
    fund_scores_df: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Main scoring function. Call this once after all ledger data has been
    loaded. Returns a ranked DataFrame ready for display.

    Parameters
    ----------
    metrics_df     : output of fetch_metrics() — Symbol, Price, CurrentlyAbove200EMA,
                     TrendDays, %From200EMA, DayChg%, %FromHigh, %FromLow.
    *_df           : ledger DataFrames from levels_store.get_*() functions.
    top_n          : how many stocks to show (default 30).
    fund_scores_df : optional output of fundamental_score.build_fundamental_scores().
                     If provided, blends into Combined_Score (60% tech + 40% fund).
                     If None or empty, Combined_Score = Technical_Score.

    Returns
    -------
    DataFrame with one row per symbol, sorted by Combined_Score descending.
    """
    if metrics_df.empty:
        return pd.DataFrame()

    # ── Pre-group all ledger tables by symbol (one pass each) ────────────
    # Doing df[df["symbol"] == sym] inside the scoring loop would scan the
    # full DataFrame for every symbol × strategy combination — ~700 mask
    # operations for 100 symbols × 7 strategies. Building a dict keyed by
    # symbol once is 3–8× faster for the scoring pass.
    def _by_sym(df: pd.DataFrame) -> dict:
        if df is None or df.empty or "symbol" not in df.columns:
            return {}
        return {sym: grp for sym, grp in df.groupby("symbol", sort=False)}

    streak_by_sym = _by_sym(streak_df)
    five_by_sym   = _by_sym(five_df)
    pivot_by_sym  = _by_sym(pivot_df)
    s1_by_sym     = _by_sym(s1_shift_df)
    bp_by_sym     = _by_sym(bp_df)
    ep_by_sym     = _by_sym(ep_df)
    st_by_sym     = _by_sym(st_df)

    _empty = pd.DataFrame()

    rows = []
    for _, mrow in metrics_df.iterrows():
        sym   = mrow["Symbol"]
        price = mrow.get("Price", np.nan)
        if pd.isna(price) or price <= 0:
            continue

        # ---- Section A scores — pass pre-filtered sub DataFrames ----
        a1 = _score_streak(mrow,              streak_by_sym.get(sym, _empty))
        a2 = _score_five_leg(mrow,            five_by_sym.get(sym, _empty))
        a3 = _score_monthly_pivot(mrow,       pivot_by_sym.get(sym, _empty))
        a4 = _score_s1_shift(mrow,            s1_by_sym.get(sym, _empty))
        a5 = _score_breakout_pullback(mrow,   bp_by_sym.get(sym, _empty))
        a6 = _score_ema_pullback(mrow,        ep_by_sym.get(sym, _empty))
        a7 = _score_supertrend(mrow,          st_by_sym.get(sym, _empty))

        all_scores = {**a1, **a2, **a3, **a4, **a5, **a6, **a7}

        # ---- Section B score ----
        b = _score_trend(mrow)

        # ---- Section C score ----
        c = _score_proximity(price, all_scores)

        # ---- Count how many strategies are active (not None/failed) ----
        active_strategies = sum(1 for key in
            ("A1_status", "A2_status", "A3_status", "A4_status",
             "A5_status", "A6_status", "A7_status")
            if all_scores.get(key) is not None
               and "fail" not in str(all_scores.get(key, "")).lower())

        # ---- Raw total ----
        a_total = (a1["A1_pts"] + a2["A2_pts"] + a3["A3_pts"] + a4["A4_pts"]
                   + a5["A5_pts"] + a6["A6_pts"] + a7["A7_pts"])
        raw_total = a_total + b["B_pts"] + c["C_pts"]

        rows.append({
            "Symbol":       sym,
            "Price":        price,
            "raw_score":    raw_total,
            # Sub-scores for display
            "Strategy_Score": a_total,
            "Trend_Score":    b["B_pts"],
            "Proximity_Score": c["C_pts"],
            # Active strategy count
            "Strategies_Active": active_strategies,
            # Closest level metadata
            "Closest_Level": c["closest_level"],
            "Closest_Pct":   c["closest_pct"],
            # Per-strategy status flags
            "Flags":         _strategy_flags(all_scores),
            # Per-strategy detail (for tooltip / expander display)
            **{k: v for k, v in all_scores.items() if "_pts" not in k},
            # Trend data passthrough
            "Above200EMA":   mrow.get("CurrentlyAbove200EMA", False),
            "TrendDays":     mrow.get("TrendDays", 0),
            "%From200EMA":   mrow.get("%From200EMA", np.nan),
            "%FromHigh":     mrow.get("%FromHigh", np.nan),
            "DayChg%":       mrow.get("DayChg%", np.nan),
        })

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)

    # ── Normalize technical raw_score to 0–10 ──
    rmin = result["raw_score"].min()
    rmax = result["raw_score"].max()
    if rmax > rmin:
        result["Technical_Score"] = (
            (result["raw_score"] - rmin) / (rmax - rmin) * 10
        ).round(2)
    else:
        result["Technical_Score"] = 5.0

    # Keep Master_Score as alias for Technical_Score (backward compat)
    result["Master_Score"] = result["Technical_Score"]

    # ── Merge fundamental scores ──
    has_fund = (
        fund_scores_df is not None
        and not fund_scores_df.empty
        and "Fundamental_Score" in fund_scores_df.columns
    )

    if has_fund:
        # fund_scores_df is indexed by symbol
        fund_lookup = fund_scores_df["Fundamental_Score"].to_dict()
        tier_lookup = fund_scores_df.get("Quality_Tier", pd.Series()).to_dict() if "Quality_Tier" in fund_scores_df.columns else {}
        fields_lookup = fund_scores_df.get("F_data_fields", pd.Series()).to_dict() if "F_data_fields" in fund_scores_df.columns else {}

        # Pull per-symbol fundamental display fields into result.
        # Includes F_de_display (raw value — app adds labels/icons),
        # F_is_financial, F_is_infra_utility so the D/E or ROA column
        # and tooltip can render correctly.
        for col in ["F_roe", "F_pe", "F_eg", "F_de", "F_roa", "F_de_display",
                    "F_div_yield", "F_op_margin", "F_rev_growth",
                    "F_data_fields", "F_fetched_at",
                    "F_is_financial", "F_is_infra_utility"]:
            if col in fund_scores_df.columns:
                result[col] = result["Symbol"].map(fund_scores_df[col].to_dict())

        result["Fundamental_Score"] = result["Symbol"].map(fund_lookup).round(2)
        result["Quality_Tier"]      = result["Symbol"].map(tier_lookup)
        result["F_data_fields"]     = result["Symbol"].map(fields_lookup).fillna(0).astype(int)

        # Combined_Score: 60% tech + 40% fundamental
        # If fundamental score is missing, fall back to technical only
        def _combined(row):
            ts = row["Technical_Score"]
            fs = row.get("Fundamental_Score")
            if fs is None or pd.isna(fs):
                return ts
            return round(0.60 * ts + 0.40 * fs, 2)

        result["Combined_Score"] = result.apply(_combined, axis=1)
        result["Fund_data_warning"] = result["F_data_fields"] < 3
    else:
        result["Fundamental_Score"] = np.nan
        result["Quality_Tier"]      = "❓ No data"
        result["Combined_Score"]    = result["Technical_Score"]
        result["Fund_data_warning"] = True
        for col in ["F_roe", "F_pe", "F_eg", "F_de", "F_roa", "F_de_display",
                    "F_div_yield", "F_op_margin", "F_rev_growth",
                    "F_data_fields", "F_fetched_at",
                    "F_is_financial", "F_is_infra_utility"]:
            result[col] = np.nan

    # ── Verdict uses Combined_Score + quality gate ──
    result["Verdict"] = result.apply(
        lambda r: _verdict(
            r["Combined_Score"],
            r["Closest_Pct"],
            r.get("Quality_Tier"),
        ),
        axis=1,
    )

    # ── Sort by Combined_Score then Strategies_Active ──
    result = (result
              .sort_values(["Combined_Score", "Strategies_Active"],
                           ascending=[False, False])
              .reset_index(drop=True))
    result.index += 1
    result.index.name = "Rank"

    if top_n:
        result = result.head(top_n)

    return result
