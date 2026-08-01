"""
strategy_vwap_sr.py
-------------------
VWAP Support/Resistance strategy runner — SCALED TO NIFTY 100.
"""

import logging
from types import SimpleNamespace
from typing import Callable, Optional

import numpy as np
import pandas as pd
import streamlit as st

import hourly_price_cache
import levels_store
import price_cache
import symbols_fetcher
from strategy_framework import StrategyResult
from vwap_support_resistance_pattern import (
    compute_session_summary,
    find_vwap_sr_episodes,
    find_vwap_sr_episodes_upper,
)

_log = logging.getLogger("strategy_vwap_sr")

# ── Strategy Config (SimpleNamespace avoids collision with StrategyConfig in framework) ──
VWAP_SR_CONFIG = SimpleNamespace(
    name="vwap_sr",
    display_name="VWAP Support / Resistance",
    description=(
        "Session VWAP (resets daily) vs first-hour range. "
        "Lower-band Day D = support level; upper-band Day D = resistance level."
    ),
    params={
        "min_confirm_days": 5,
        "retest_pct": 5.0,
        "fail_pct": 8.0,
        "min_runup_pct": None,
        "min_gap_pct": 0.5,
    },
    data_requirements=["hourly_ohlcv", "daily_ohlcv"],
    output_fields=["day_d_date", "x_price", "classification", "status", "episode_type"],
)


# ── NIFTY 100 Universe ────────────────────────────────────────────────────

def get_universe_symbols() -> list[str]:
    """Return the current NIFTY 100 symbol list from NSE (cached)."""
    df, _, source = symbols_fetcher.get_symbols()
    _log.info("[vwap_sr] Universe source=%s, count=%d", source, len(df))
    return df["Symbol"].tolist()


# ── Core Detection ────────────────────────────────────────────────────────

def detect_vwap_sr_for_symbol(
    symbol: str,
    min_confirm_days: int = 5,
    retest_pct: float = 5.0,
    fail_pct: float = 8.0,
    min_runup_pct: Optional[float] = None,
    min_gap_pct: float = 0.5,
) -> dict:
    try:
        daily_hist = price_cache.get_full_history(symbol)
        if daily_hist.empty or len(daily_hist) < 50:
            return {"symbol": symbol, "error": "insufficient_daily_history"}

        hourly_hist = hourly_price_cache.get_hourly_history(symbol)
        if hourly_hist.empty or len(hourly_hist) < 50:
            return {"symbol": symbol, "error": "insufficient_hourly_history"}

        session_summary = compute_session_summary(hourly_hist, daily_hist)

        lower = find_vwap_sr_episodes(
            daily_hist, session_summary,
            min_confirm_days=min_confirm_days,
            retest_pct=retest_pct,
            fail_pct=fail_pct,
            min_runup_pct=min_runup_pct,
            min_gap_pct=min_gap_pct,
        )
        upper = find_vwap_sr_episodes_upper(
            daily_hist, session_summary,
            min_confirm_days=min_confirm_days,
            retest_pct=retest_pct,
            fail_pct=fail_pct,
            min_runup_pct=min_runup_pct,
            min_gap_pct=min_gap_pct,
        )

        return {
            "symbol": symbol,
            "lower_band_episodes": lower,
            "upper_band_episodes": upper,
            "daily_hist": daily_hist,
            "session_summary": session_summary,
            "error": None,
        }
    except Exception as e:
        _log.warning("[vwap_sr] Error processing %s: %s", symbol, e)
        return {"symbol": symbol, "error": str(e)}


# ── Full Universe Runner ──────────────────────────────────────────────────

def run_vwap_sr_strategy(
    symbols: Optional[list[str]] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> StrategyResult:
    if symbols is None:
        symbols = get_universe_symbols()

    hourly_price_cache.bulk_refresh_hourly_histories(symbols)
    price_cache.bulk_refresh_histories(symbols)

    per_symbol_data = []
    processed = 0
    total = len(symbols)

    for sym in symbols:
        result = detect_vwap_sr_for_symbol(sym)
        processed += 1
        if progress_callback:
            progress_callback(processed, total)

        if result.get("error"):
            per_symbol_data.append({"symbol": sym, "vwap_sr_rows": []})
            continue

        rows = []
        for ep in result.get("lower_band_episodes", []):
            rows.append(_normalize_episode_row(ep))
        for ep in result.get("upper_band_episodes", []):
            rows.append(_normalize_episode_row(ep))

        per_symbol_data.append({"symbol": sym, "vwap_sr_rows": rows})

    if per_symbol_data:
        levels_store.batch_upsert_all(per_symbol_data)

    all_rows = []
    for item in per_symbol_data:
        all_rows.extend(item.get("vwap_sr_rows", []))

    df = pd.DataFrame(all_rows)
    if df.empty:
        return StrategyResult(
            config=VWAP_SR_CONFIG,
            signal="neutral",
            trend="neutral",
            strength=0.0,
            confidence=0.0,
            metadata={"symbols_scanned": total, "episodes_found": 0},
            raw_data=df,
        )

    naked_count = int((df["status"] == "naked").sum())
    tested_count = int((df["status"] == "tested").sum())
    failed_count = int((df["status"] == "failed").sum())
    total_episodes = len(df)

    if total_episodes == 0:
        signal = "neutral"
        trend = "neutral"
        strength = 0.0
    else:
        actionable_ratio = (naked_count + tested_count) / total_episodes
        if actionable_ratio > 0.6 and naked_count > tested_count:
            signal = "buy"
            trend = "bullish"
            strength = min(1.0, actionable_ratio)
        elif failed_count / total_episodes > 0.4:
            signal = "sell"
            trend = "bearish"
            strength = min(1.0, failed_count / total_episodes)
        else:
            signal = "neutral"
            trend = "mixed"
            strength = 0.3

    return StrategyResult(
        config=VWAP_SR_CONFIG,
        signal=signal,
        trend=trend,
        strength=round(strength, 2),
        confidence=round(min(1.0, total_episodes / max(len(symbols), 1)), 2),
        metadata={
            "symbols_scanned": total,
            "episodes_found": total_episodes,
            "naked": naked_count,
            "tested": tested_count,
            "failed": failed_count,
        },
        raw_data=df,
    )


def _normalize_episode_row(ep: dict) -> dict:
    return {
        "day_d_date": ep.get("day_d_date"),
        "episode_type": ep.get("episode_type", "lower_band"),
        "x_price": ep.get("x_price"),
        "first_hour_high": ep.get("first_hour_high"),
        "first_hour_low": ep.get("first_hour_low"),
        "gap_pct": ep.get("gap_pct"),
        "classification": ep.get("classification"),
        "classification_changed_date": ep.get("classification_changed_date"),
        "status": ep.get("status"),
        "tested_date": ep.get("tested_date"),
        "tested_price": ep.get("tested_price"),
        "failed_date": ep.get("failed_date"),
        "failed_price": ep.get("failed_price"),
        "max_runup_pct": ep.get("max_runup_pct"),
        "days_tracked": ep.get("days_tracked"),
        "drawdown_pct": ep.get("drawdown_pct"),
        "drawdown_recovered": ep.get("drawdown_recovered"),
        "drawdown_recovery_date": ep.get("drawdown_recovery_date"),
        "drawdown_days_to_recover": ep.get("drawdown_days_to_recover"),
    }


# ── Cached Dashboard Read ─────────────────────────────────────────────────

def load_vwap_sr_ledger() -> pd.DataFrame:
    return levels_store.get_vwap_sr_episodes()