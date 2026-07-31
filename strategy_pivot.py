"""
strategy_pivot.py
--------------------
Monthly Pivot S1 strategy page — pilot #1 of the StrategyConfig framework.

Every calculation, status-classification, and column here is copied
verbatim from the original Monthly Pivot S1 section of appclaude.py
(_pivot_status_label, the filter widgets, the display-column mapping, the
status color map, the format spec) — only the surrounding scaffolding
(load_data/apply_filters/render wiring) changed shape to fit StrategyConfig.
"""
import pandas as pd
import streamlit as st

import dashboard_core as core
from strategy_framework import StrategyConfig

DESCRIPTION = (
    "Daily 200 EMA vs. the monthly Standard Pivot S1 (computed from the prior "
    "completed month's High/Low/Close, held flat through the current month). "
    "Once S1 stays cleanly above the 200 EMA for the qualifying window (no "
    "touch of either), the running high is tracked - fixed as **X** the "
    "first time price touches S1. From there, the running low is tracked as "
    "**Y** until the 200 EMA finally crosses above X - a touch of the 200 EMA "
    "anywhere before that invalidates the whole setup. X and Y are both "
    "buy-on-pullback levels once complete."
)

STATUS_LEGEND_MD = """
- **🟡 Tracking X** — qualified (S1 stayed clean above the 200 EMA for the
  minimum window), watching the running high. S1 hasn't been touched yet, so
  X isn't fixed - the figure shown is provisional and still climbing.
- **🟠 X fixed, pending cross** — price touched S1, so X is locked in. Now
  tracking the running low (Y, still provisional) while waiting for the 200
  EMA to cross above X. A touch of the 200 EMA anywhere in this phase
  invalidates the whole setup (it simply won't appear here).
- **🟢 Complete** — the 200 EMA crossed above X without ever being touched.
  X and Y are both locked in as buy-on-pullback levels, and are tracked
  afterward the same way as the streak-based ledger: **naked** (never
  retested since) or **tested** (price already pulled away and come back),
  independently for X and for Y.
"""

STATUS_COLOR_MAP = {
    "🟡 Tracking X": "background-color:#fff3cd; color:#8a6d00; font-weight:600",
    "🟠 X fixed, pending cross": "background-color:#fdebd0; color:#8a5a00; font-weight:600",
    "🟢 Complete, pending retest": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
    "🔵 Complete, retested": "background-color:#d6eaf8; color:#1a5276; font-weight:600",
}

DISPLAY_COLUMNS = {
    "symbol": "Symbol",
    "episode_start": "Episode Start",
    "x_price": "X (Streak High)",
    "y_price": "Y (Streak Low)",
    "status_label": "Status",
    "current_price": "Current Price",
    "%_from_x": "% From X",
    "%_from_y": "% From Y",
    "retest_drawdown_pct": "Retest Drawdown %",
    "retest_days_to_recover": "Retest Recovery Days",
    "retest_drawdown_level": "On Level",
}

FORMAT_SPEC = {
    "Episode Start": lambda d: d.strftime("%d-%b-%Y"),
    "X (Streak High)": lambda v: "—" if pd.isna(v) else f"₹{v:.2f}",
    "Y (Streak Low)": lambda v: "—" if pd.isna(v) else f"₹{v:.2f}",
    "Current Price": "₹{:.2f}",
    "% From X": lambda v: "—" if pd.isna(v) else f"{v:+.2f}%",
    "% From Y": lambda v: "—" if pd.isna(v) else f"{v:+.2f}%",
    "Retest Drawdown %": lambda v: "—" if pd.isna(v) else f"{v:.1f}%",
    "Retest Recovery Days": lambda v: "—" if pd.isna(v) else f"{int(v)}",
}

PIVOT_STATUS_OPTIONS = [
    "All", "🟡 Tracking X", "🟠 X fixed, pending cross",
    "🟢 Complete, pending retest", "🔵 Complete, retested",
]


def _pivot_status_label(row):
    if row["status"] == "complete":
        sub = core._combine_retest(row.get("x_retest_status"), row.get("y_retest_status"))
        return "🟢 Complete, pending retest" if sub == "pending retest" else "🔵 Complete, retested"
    return {
        "tracking_x": "🟡 Tracking X",
        "x_fixed_pending_cross": "🟠 X fixed, pending cross",
    }.get(row["status"], row["status"])


def _load_data():
    df = core.load_pivot_ledger()
    if df.empty:
        return df
    df = df.copy()
    df["episode_start"] = pd.to_datetime(df["episode_start"])
    df["x_fix_date"] = pd.to_datetime(df["x_fix_date"])
    df["y_fix_date"] = pd.to_datetime(df["y_fix_date"])
    return df


def _apply_filters(raw_df):
    df = raw_df.copy()
    df["%_from_x"] = (df["current_price"] - df["x_price"]) / df["x_price"] * 100
    df["%_from_y"] = (df["current_price"] - df["y_price"]) / df["y_price"] * 100

    pcol1, pcol2, pcol3 = st.columns(3)
    status_filter = pcol1.selectbox("Status", PIVOT_STATUS_OPTIONS, index=0, key="pivot_status")
    symbol_filter = pcol2.text_input("Filter by symbol", "", key="pivot_symbol").upper().strip()
    band = pcol3.slider("Show only within % of current price (vs X or Y)", 1, 50, 15, key="pivot_band")

    view = df.dropna(subset=["current_price"]).copy()
    if status_filter != "All":
        view = view[view["status_label"] == status_filter]
    if symbol_filter:
        view = view[view["symbol"].str.contains(symbol_filter)]
    view["_dist_x"] = view["%_from_x"].abs()
    view["_dist_y"] = view["%_from_y"].abs()
    view = view[(view["_dist_x"] <= band) | (view["_dist_y"].fillna(999) <= band)]
    view["_closest"] = view[["_dist_x", "_dist_y"]].min(axis=1)
    view = view.sort_values("_closest").reset_index(drop=True)
    return view


def _single_chart_metrics(ep):
    return [[
        ("Current Price", f"₹{ep['current_price']:.2f}" if pd.notna(ep.get('current_price')) else "—", None),
        ("X (Running High)", f"₹{ep['x_price']:.2f}" if pd.notna(ep.get('x_price')) else "—",
         f"{ep['%_from_x']:+.2f}%" if pd.notna(ep.get('%_from_x')) else None),
        ("Y (Running Low)", f"₹{ep['y_price']:.2f}" if pd.notna(ep.get('y_price')) else "—",
         f"{ep['%_from_y']:+.2f}%" if pd.notna(ep.get('%_from_y')) else None),
        ("Status", ep.get("status_label", "—"), None),
        ("Episode Start", pd.to_datetime(ep["episode_start"]).strftime("%d-%b-%Y"), None),
    ]]


def _single_chart_caption(ep):
    parts = [f"Episode started **{pd.to_datetime(ep['episode_start']).strftime('%d-%b-%Y')}**"]
    if pd.notna(ep.get("x_fix_date")):
        parts.append(f"S1 touched (X fixed) **{pd.to_datetime(ep['x_fix_date']).strftime('%d-%b-%Y')}**")
    if pd.notna(ep.get("y_fix_date")):
        parts.append(f"200 EMA crossed above X (Y fixed) **{pd.to_datetime(ep['y_fix_date']).strftime('%d-%b-%Y')}**")
    return " · ".join(parts)


def _multi_chart_caption(sym, ep):
    return (
        f"**{sym}**\n\n"
        f"Episode {pd.to_datetime(ep['episode_start']).strftime('%d-%b-%Y')} · "
        f"{ep.get('status_label', '')}"
    )


CONFIG = StrategyConfig(
    key="pivot_s1",
    title="📐 Monthly Pivot S1 Setup",
    description=DESCRIPTION,
    status_legend_md=STATUS_LEGEND_MD,
    load_data=_load_data,
    apply_filters=_apply_filters,
    status_label_fn=_pivot_status_label,
    status_color_map=STATUS_COLOR_MAP,
    display_columns=DISPLAY_COLUMNS,
    numeric_pct_cols=["% From X", "% From Y"],
    format_spec=FORMAT_SPEC,
    chart_builder=core.build_pivot_s1_chart,
    single_chart_metrics_fn=_single_chart_metrics,
    single_chart_caption_fn=_single_chart_caption,
    multi_chart_caption_fn=_multi_chart_caption,
)
