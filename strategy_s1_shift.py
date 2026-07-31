"""
strategy_s1_shift.py
-----------------------
Monthly S1 Shift Up strategy page. Every calculation, status label, column,
and format spec here is copied verbatim from legacy_dashboard.py's
Monthly S1 Shift Up section.
"""
import pandas as pd
import streamlit as st

import dashboard_core as core
from strategy_framework import StrategyConfig

DESCRIPTION = (
    "Most months where price touches/closes at or below the monthly Pivot "
    "S1, the FOLLOWING month's S1 ends up lower too - the decline drags the "
    "whole range down. In the rare case where the following month's S1 is "
    "actually HIGHER, that's a sign of strong responsive buying. **X** = "
    "that month's lowest low - a buy-on-revisit level once this rare shift "
    "is confirmed."
)

STATUS_LEGEND_MD = """
- **⚪ Naked** — X has never been revisited since tracking started. Still an
  open, unresolved level.
- **🟢 Tested** — price ran up away from X, then came back down within the
  retest band. A genuine revisit.
- **🔴 Failed** — price dropped below X by more than the "% below X that
  counts as failed" threshold (default 8%, adjustable in the sidebar) -
  support decisively broken, no longer a valid level to buy against.
- **Max Run-up** — the largest % price climbed away from X before the
  resolving event (or before "now," if still naked/ongoing).
"""

STATUS_COLOR_MAP = {
    "⚪ Naked": "background-color:#eeeeee; color:#666; font-weight:600",
    "🟡 Naked (below level)": "background-color:#fff3cd; color:#8a6d00; font-weight:600",
    "🟢 Tested": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
    "🔴 Failed": "background-color:#fadbd8; color:#943126; font-weight:600",
}

DISPLAY_COLUMNS = {
    "symbol": "Symbol",
    "month": "Month",
    "x_price": "X (Month Low)",
    "current_price": "Current Price",
    "%_from_x": "% From X",
    "status_label": "Status",
    "max_runup_pct": "Max Run-up",
    "days_tracked": "Days Tracked",
    "post_event_drawdown_pct": "Drawdown After Test/Fail",
    "post_event_days_to_recover": "Days to Recover",
}

FORMAT_SPEC = {
    "X (Month Low)": "₹{:.2f}",
    "Current Price": "₹{:.2f}",
    "% From X": "{:+.2f}%",
    "Max Run-up": "{:+.1f}%",
    "Days Tracked": "{:.0f}",
    "Drawdown After Test/Fail": lambda v: "—" if pd.isna(v) else f"{v:.1f}%",
    "Days to Recover": lambda v: "—" if pd.isna(v) else f"{int(v)}",
}


def _status_label(row):
    """
    Naked episodes split into two display states:
      ⚪ Naked          — price still above X (untouched, watching for pullback).
      🟡 Naked (below)  — price has drifted below X without a confirmed retest —
                          no longer a clean buy-on-pullback, now overhead resistance.
    Depends on %_from_x, which the framework hasn't computed yet when it first
    calls status_label_fn — so _apply_filters() recomputes status_label with
    this same function once %_from_x exists (verbatim order from the original).
    """
    status = row.get("status")
    if status == "naked" and pd.notna(row.get("%_from_x")) and row["%_from_x"] < 0:
        return "🟡 Naked (below level)"
    return {"naked": "⚪ Naked", "tested": "🟢 Tested", "failed": "🔴 Failed"}.get(status, status)


def _load_data():
    df = core.load_s1_shift_ledger()
    if df.empty:
        return df
    df = df.copy()
    df["anchor_date"] = pd.to_datetime(df["anchor_date"])
    return df


def _apply_filters(raw_df):
    df = raw_df.copy()
    df["%_from_x"] = (df["current_price"] - df["x_price"]) / df["x_price"] * 100
    df["status_label"] = df.apply(_status_label, axis=1)

    c1, c2, c3 = st.columns(3)
    status_filter = c1.selectbox(
        "Status", ["All", "⚪ Naked", "🟡 Naked (below level)", "🟢 Tested", "🔴 Failed"],
        index=0, key="s1_shift_status",
    )
    symbol_filter = c2.text_input("Filter by symbol", "", key="s1_shift_symbol").upper().strip()
    band = c3.slider("Show only within % of current price (vs X)", 1, 50, 15, key="s1_shift_band")

    view = df.dropna(subset=["current_price"]).copy()
    if status_filter != "All":
        view = view[view["status_label"] == status_filter]
    if symbol_filter:
        view = view[view["symbol"].str.contains(symbol_filter)]
    view = view[view["%_from_x"].abs() <= band]
    view = view.sort_values("%_from_x", key=lambda s: s.abs()).reset_index(drop=True)
    return view


def _single_chart_metrics(ep):
    return [[
        ("Current Price", f"₹{ep['current_price']:.2f}" if pd.notna(ep.get('current_price')) else "—", None),
        ("X (Month Low)", f"₹{ep['x_price']:.2f}" if pd.notna(ep.get('x_price')) else "—",
         f"{ep['%_from_x']:+.2f}%" if pd.notna(ep.get('%_from_x')) else None),
        ("S1(M)", f"₹{ep['s1_month']:.2f}" if pd.notna(ep.get('s1_month')) else "—", None),
        ("S1(M+1) ↑", f"₹{ep['s1_next_month']:.2f}" if pd.notna(ep.get('s1_next_month')) else "—", None),
        ("Status", ep.get("status_label", "—"), None),
    ]]


def _single_chart_caption(ep):
    parts = [f"Setup month **{ep['month']}** · X (month low) on **{ep['x_date']}**"]
    if pd.notna(ep.get("anchor_date")):
        parts.append(f"Tracking since **{pd.to_datetime(ep['anchor_date']).strftime('%d-%b-%Y')}**")
    if ep.get("status") == "tested" and pd.notna(ep.get("tested_date")):
        parts.append(f"Retested **{pd.to_datetime(ep['tested_date']).strftime('%d-%b-%Y')}**")
    if ep.get("status") == "failed" and pd.notna(ep.get("failed_date")):
        parts.append(f"Failed **{pd.to_datetime(ep['failed_date']).strftime('%d-%b-%Y')}**")
    return " · ".join(parts)


def _multi_chart_caption(sym, ep):
    return f"**{sym}**\n\nMonth {ep['month']} · {ep.get('status_label', '')}"


CONFIG = StrategyConfig(
    key="s1_shift",
    title="🔺 Monthly S1 Shift Up Setup",
    description=DESCRIPTION,
    status_legend_md=STATUS_LEGEND_MD,
    load_data=_load_data,
    apply_filters=_apply_filters,
    status_label_fn=lambda row: row.get("status"),  # placeholder — _apply_filters recomputes once %_from_x exists
    status_color_map=STATUS_COLOR_MAP,
    display_columns=DISPLAY_COLUMNS,
    numeric_pct_cols=["% From X"],
    format_spec=FORMAT_SPEC,
    chart_builder=core.build_s1_shift_chart,
    single_chart_metrics_fn=_single_chart_metrics,
    single_chart_caption_fn=_single_chart_caption,
    multi_chart_caption_fn=_multi_chart_caption,
)
