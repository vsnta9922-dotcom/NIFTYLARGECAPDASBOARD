"""
strategy_vwap_sr.py
-----------------------
VWAP Support/Resistance strategy — now covers BOTH lower-band (support
from below) and upper-band (resistance from above) Day-D conditions.

SCOPED TO A SMALL TEST UNIVERSE for this pilot.
"""
import pandas as pd
import streamlit as st

import price_cache
import hourly_price_cache
from vwap_support_resistance_pattern import (
    compute_session_summary,
    find_vwap_sr_episodes,
    find_vwap_sr_episodes_upper,
)
from vwap_sr_chart import build_vwap_sr_chart
from strategy_framework import StrategyConfig

TEST_UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "ITC",
    "SBIN", "BHARTIARTL", "LT", "KOTAKBANK", "HINDUNILVR", "AXISBANK",
]

MIN_RUNUP_PCT = 2.0

DESCRIPTION = (
    "⚠️ **Pilot strategy — scoped to a 12-symbol test universe, not the full Nifty 100.** "
    "Hourly timeframe, session VWAP (resets daily) with 1-standard-deviation bands. "
    "**Lower-band Day D**: closing lower-band > first-hour high → X = lower-band (support). "
    "**Upper-band Day D**: closing upper-band < first-hour low → X = upper-band (resistance). "
    "From Day D onward, X's role is read dynamically from VWAP-close position, "
    "debounced over 5 sessions. Retest/run-up/drawdown tracked against X. "
    "Episodes with < {min_runup}% favorable move before retest/fail are filtered out."
).format(min_runup=MIN_RUNUP_PCT)

STATUS_LEGEND_MD = """
- **🟢 Support / 🔴 Resistance** — current role based on the last 5+ sessions' VWAP-close
  position relative to X (debounced). A level can flip roles over time.
- **⚪ Naked** — X hasn't been retested since Day D (still open).
- **🟢 Tested** — the level did what it was supposed to do: for support, price ran up
  away from X then pulled back into the retest band; for resistance, price dropped
  away from X then rallied back into the retest band. **The Tested/Failed label reflects
  the level's role at the time of the event**, not necessarily its current classification.
- **🔴 Failed** — the level broke decisively: for support, price dropped 8%+ below X;
  for resistance, price broke 8%+ above X. Status is **sticky** — it stays Failed even
  if classification later flips.
- **Drawdown** — measured from the event date: for support, max drop below X; for
  resistance, max rally above X (adverse move from a position entered at X).
- **Max Run-up** — for support, max rally above X before the retest; for resistance,
  max drop below X before the retest. Episodes with < {min_runup}% run-up are filtered out.
- **Known limitation**: hourly data is only ever a rolling ~2-year window (Yahoo's own limit).
""".format(min_runup=MIN_RUNUP_PCT)

STATUS_COLOR_MAP = {
    "🟢 Support · ⚪ Naked": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
    "🟢 Support · 🟢 Tested": "background-color:#c8f0da; color:#0b6b4f; font-weight:600",
    "🟢 Support · 🔴 Failed": "background-color:#fadbd8; color:#943126; font-weight:600",
    "🔴 Resistance · ⚪ Naked": "background-color:#fdebd0; color:#8a5a00; font-weight:600",
    "🔴 Resistance · 🟢 Tested": "background-color:#fde3cf; color:#a34700; font-weight:600",
    "🔴 Resistance · 🔴 Failed": "background-color:#fadbd8; color:#943126; font-weight:600",
}

DISPLAY_COLUMNS = {
    "symbol": "Symbol",
    "episode_type": "Type",
    "day_d_date": "Day D",
    "first_hour_high": "First-Hour High",
    "first_hour_low": "First-Hour Low",
    "x_price": "X",
    "gap_pct": "Gap %",
    "current_price": "Current Price",
    "%_from_x": "% From X",
    "status_label": "Status",
    "classification_changed_date": "Classification Since",
    "max_runup_pct": "Max Run-up",
    "days_tracked": "Days Tracked",
    "drawdown_pct": "Drawdown After Event",
    "drawdown_days_to_recover": "Days to Recover",
}

FORMAT_SPEC = {
    "Day D": lambda d: d.strftime("%d-%b-%Y"),
    "First-Hour High": "₹{:.2f}",
    "First-Hour Low": "₹{:.2f}",
    "X": "₹{:.2f}",
    "Gap %": "{:.2f}%",
    "Current Price": "₹{:.2f}",
    "% From X": "{:+.2f}%",
    "Classification Since": lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
    "Max Run-up": "{:+.1f}%",
    "Days Tracked": "{:.0f}",
    "Drawdown After Event": lambda v: "—" if pd.isna(v) else f"{v:.1f}%",
    "Days to Recover": lambda v: "—" if pd.isna(v) else f"{int(v)}",
}

STATUS_OPTIONS = [
    "All",
    "🟢 Support · ⚪ Naked", "🟢 Support · 🟢 Tested", "🟢 Support · 🔴 Failed",
    "🔴 Resistance · ⚪ Naked", "🔴 Resistance · 🟢 Tested", "🔴 Resistance · 🔴 Failed",
]

EPISODE_TYPE_OPTIONS = ["All", "Lower Band (Support)", "Upper Band (Resistance)"]


def _status_label(row):
    emoji = "🟢" if row.get("classification") == "support" else "🔴"
    sub = {"naked": "⚪ Naked", "tested": "🟢 Tested", "failed": "🔴 Failed"}.get(row.get("status"), row.get("status"))
    return f"{emoji} {row.get('classification', '').title()} · {sub}"


@st.cache_data(ttl=3600, show_spinner="Scanning VWAP S/R test universe (hourly data)…")
def _load_ledger_cached() -> pd.DataFrame:
    all_rows = []
    for sym in TEST_UNIVERSE:
        try:
            hourly_df = hourly_price_cache.get_hourly_history(sym)
            if hourly_df.empty:
                continue
            daily_hist = price_cache.get_full_history(sym)
            if daily_hist.empty:
                continue
            session_summary = compute_session_summary(hourly_df, daily_hist)
            eps_lower = find_vwap_sr_episodes(
                daily_hist, session_summary, min_runup_pct=MIN_RUNUP_PCT,
                min_gap_pct=0.5
            )
            eps_upper = find_vwap_sr_episodes_upper(
                daily_hist, session_summary, min_runup_pct=MIN_RUNUP_PCT,
                min_gap_pct=0.5
            )
            for ep in eps_lower + eps_upper:
                ep["symbol"] = sym
                all_rows.append(ep)
        except Exception:
            continue
    if not all_rows:
        return pd.DataFrame()
    return pd.DataFrame(all_rows)


@st.cache_data(ttl=3600)
def _get_hourly_and_summary_cached(symbol: str):
    hourly_df = hourly_price_cache.get_hourly_history(symbol)
    daily_hist = price_cache.get_full_history(symbol)
    session_summary = compute_session_summary(hourly_df, daily_hist) if not hourly_df.empty else pd.DataFrame()
    return hourly_df, session_summary


def _load_data():
    df = _load_ledger_cached()
    if df.empty:
        return df
    df = df.copy()
    df["day_d_date"] = pd.to_datetime(df["day_d_date"])
    df["classification_changed_date"] = pd.to_datetime(df["classification_changed_date"])
    return df


def _apply_filters(raw_df):
    df = raw_df.copy()
    df["%_from_x"] = (df["current_price"] - df["x_price"]) / df["x_price"] * 100

    c1, c2, c3, c4, c5 = st.columns(5)
    status_filter = c1.selectbox("Status", STATUS_OPTIONS, index=0, key="vwap_sr_status")
    ep_type_filter = c2.selectbox("Episode Type", EPISODE_TYPE_OPTIONS, index=0, key="vwap_sr_type")
    symbol_filter = c3.text_input("Filter by symbol", "", key="vwap_sr_symbol").upper().strip()
    band = c4.slider("Within % of price (vs X)", 1, 50, 20, key="vwap_sr_band")
    min_gap = c5.slider("Min Gap %", 0.0, 5.0, 0.5, 0.1, key="vwap_sr_mingap")

    view = df.dropna(subset=["current_price"]).copy()
    if status_filter != "All":
        view = view[view["status_label"] == status_filter]
    if ep_type_filter == "Lower Band (Support)":
        view = view[view["episode_type"] != "upper_band"]
    elif ep_type_filter == "Upper Band (Resistance)":
        view = view[view["episode_type"] == "upper_band"]
    if symbol_filter:
        view = view[view["symbol"].str.contains(symbol_filter)]
    view = view[view["%_from_x"].abs() <= band]
    view = view[view["gap_pct"] >= min_gap]
    view = view.sort_values("%_from_x", key=lambda s: s.abs()).reset_index(drop=True)
    return view


def _single_chart_metrics(ep):
    is_upper = ep.get("episode_type") == "upper_band"
    classification_str = f"{'🟢' if ep.get('classification') == 'support' else '🔴'} {ep['classification'].title()}"
    x_label = "X (Upper Band Close)" if is_upper else "X (Lower Band Close)"
    fh_label = "First-Hour Low (Day D)" if is_upper else "First-Hour High (Day D)"
    fh_value = ep.get("first_hour_low" if is_upper else "first_hour_high")

    return [
        [
            ("Current Price", f"₹{ep['current_price']:.2f}" if pd.notna(ep.get('current_price')) else "—", None),
            (x_label, f"₹{ep['x_price']:.2f}" if pd.notna(ep.get('x_price')) else "—",
             f"{ep['%_from_x']:+.2f}%" if pd.notna(ep.get('%_from_x')) else None),
            (fh_label, f"₹{fh_value:.2f}" if pd.notna(fh_value) else "—", None),
            ("Classification", classification_str, None),
            ("Status", ep.get("status_label", "—"), None),
        ],
        [
            ("Max Run-up (before retest)", f"{ep['max_runup_pct']:.1f}%" if pd.notna(ep.get('max_runup_pct')) else "—",
             f"{int(ep['days_tracked'])}d tracked" if pd.notna(ep.get('days_tracked')) else None),
            ("Drawdown After Event", f"{ep['drawdown_pct']:.1f}%" if pd.notna(ep.get('drawdown_pct')) else "—",
             (f"{int(ep['drawdown_days_to_recover'])}d to recover" if pd.notna(ep.get('drawdown_days_to_recover'))
              else ("still underwater" if ep.get('drawdown_recovered') is False else None))),
        ],
    ]


def _single_chart_caption(ep):
    is_upper = ep.get("episode_type") == "upper_band"
    band_name = "Upper Band" if is_upper else "Lower Band"
    parts = [f"Day D **{pd.to_datetime(ep['day_d_date']).strftime('%d-%b-%Y')}** · {band_name}"]
    if pd.notna(ep.get("classification_changed_date")):
        parts.append(f"Current role since **{pd.to_datetime(ep['classification_changed_date']).strftime('%d-%b-%Y')}**")
    if ep.get("status") == "tested" and pd.notna(ep.get("tested_date")):
        parts.append(f"Retested **{pd.to_datetime(ep['tested_date']).strftime('%d-%b-%Y')}**")
    if ep.get("status") == "failed" and pd.notna(ep.get("failed_date")):
        parts.append(f"Failed **{pd.to_datetime(ep['failed_date']).strftime('%d-%b-%Y')}**")
    return " · ".join(parts)


def _multi_chart_caption(sym, ep):
    emoji = "🟢" if ep.get("classification") == "support" else "🔴"
    band = "UB" if ep.get("episode_type") == "upper_band" else "LB"
    return f"**{sym}**\n\nDay D {pd.to_datetime(ep['day_d_date']).strftime('%d-%b-%Y')} · {emoji} {ep.get('status_label', '')} · {band}"


def _extra_kwargs(ep):
    hourly_df, session_summary = _get_hourly_and_summary_cached(ep["symbol"])
    return {"hourly_df": hourly_df, "session_summary": session_summary}


CONFIG = StrategyConfig(
    key="vwap_sr",
    title="📐 VWAP Support/Resistance (Pilot)",
    description=DESCRIPTION,
    status_legend_md=STATUS_LEGEND_MD,
    load_data=_load_data,
    apply_filters=_apply_filters,
    status_label_fn=_status_label,
    status_color_map=STATUS_COLOR_MAP,
    display_columns=DISPLAY_COLUMNS,
    numeric_pct_cols=["% From X"],
    format_spec=FORMAT_SPEC,
    chart_builder=build_vwap_sr_chart,
    single_chart_metrics_fn=_single_chart_metrics,
    single_chart_caption_fn=_single_chart_caption,
    multi_chart_caption_fn=_multi_chart_caption,
    extra_kwargs_fn=_extra_kwargs,
)
