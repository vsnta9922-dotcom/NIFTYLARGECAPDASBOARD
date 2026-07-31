"""
strategy_ema_pullback.py
---------------------------
EMA Pullback Reentry strategy page. Every calculation, status label,
column, and format spec here is copied verbatim from legacy_dashboard.py's
EMA Pullback section.

Note: the original caption embedded the live "min_qualify_days" sidebar
value (f"...for at least {int(min_qualify_days)} trading days..."). The
StrategyConfig.description field is a static string (not re-evaluated per
render), so this refers to "the configured minimum (see sidebar)" instead
of the live number — the number itself is still visible on the sidebar
widget; only the caption text lost the live interpolation. Everything the
number actually FEEDS (the calculation, the ledger) is untouched.
"""
import pandas as pd
import streamlit as st

import dashboard_core as core
from strategy_framework import StrategyConfig

DESCRIPTION = (
    "Daily 20/50 EMA only. After 20 EMA crosses above 50 EMA, price must stay "
    "cleanly above the 50 EMA (no intraday Low touching it) for at least the "
    "configured minimum number of trading days (see 'Min qualify days' in the "
    "sidebar). The highest High made during that clean run is **X**. The first "
    "day price pulls back to touch the 50 EMA locks X in. From there the lowest "
    "Low is tracked as **Y** until the 50 EMA crosses above X — that fires the "
    "signal. Any 20/50 death-cross during Y-tracking resets the setup. Pullback "
    "to **Y** is the buy-on-support entry."
)

STATUS_LEGEND_MD = """
- **🔵 Signal pending** — qualified, touched 50 EMA (X locked), but 50 EMA hasn't
  crossed above X yet. Y is still provisional/updating.
- **⚪ Naked** — signal fired (50 EMA crossed above X, Y locked), but price has never
  moved clearly away from Y and then returned to it — no retest opportunity yet.
- **🟢 Tested** — price rallied clearly above Y (at least 2× the retest band), then
  pulled back to within the retest band. The pullback entry triggered.
- **🔴 Failed** — price dropped ≥8% below Y at any point. Support decisively broken.
- **Invalidation** — if the 20 EMA crosses below the 50 EMA at any point between
  X lock-in and the 50 EMA crossing above X, the whole setup resets from that point.
"""

EP_STATUS_LABELS = {
    "signal_pending": "🔵 Signal pending",
    "naked": "⚪ Naked",
    "tested": "🟢 Tested",
    "failed": "🔴 Failed",
}

STATUS_COLOR_MAP = {
    "🔵 Signal pending": "background-color:#dbeafe; color:#1e3a8a; font-weight:600",
    "⚪ Naked": "background-color:#eeeeee; color:#666; font-weight:600",
    "🟢 Tested": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
    "🔴 Failed": "background-color:#fadbd8; color:#943126; font-weight:600",
}

DISPLAY_COLUMNS = {
    "symbol": "Symbol",
    "crossover_date": "20/50 Crossover",
    "touch_date": "50 EMA Touch (X locked)",
    "y_fix_date": "Signal Date (Y locked)",
    "status_label": "Status",
    "x_price": "X (Highest High)",
    "y_price": "Y (Buy Level)",
    "current_price": "Current Price",
    "%_from_x": "% From X",
    "%_from_y": "% From Y",
    "max_runup_pct": "Max Run-up",
    "days_tracked": "Days Tracked",
    "post_event_drawdown_pct": "Post-Event Drawdown %",
    "post_event_days_to_recover": "Post-Event Recovery Days",
}

FORMAT_SPEC = {
    "20/50 Crossover": lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
    "50 EMA Touch (X locked)": lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
    "Signal Date (Y locked)": lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
    "X (Highest High)": "₹{:.2f}",
    "Y (Buy Level)": "₹{:.2f}",
    "Current Price": "₹{:.2f}",
    "% From X": "{:+.2f}%",
    "% From Y": "{:+.2f}%",
    "Max Run-up": "{:+.1f}%",
    "Days Tracked": "{:.0f}",
    "Post-Event Drawdown %": lambda v: "—" if pd.isna(v) else f"{v:.1f}%",
    "Post-Event Recovery Days": lambda v: "—" if pd.isna(v) else f"{int(v)}",
}


def _status_label(row):
    return EP_STATUS_LABELS.get(row["status"], row["status"])


def _load_data():
    df = core.load_ema_pullback_ledger()
    if df.empty:
        return df
    df = df.copy()
    df["crossover_date"] = pd.to_datetime(df["crossover_date"])
    df["touch_date"] = pd.to_datetime(df["touch_date"])
    df["y_fix_date"] = pd.to_datetime(df["y_fix_date"])
    return df


def _apply_filters(raw_df):
    df = raw_df.copy()
    df["%_from_x"] = (df["current_price"] - df["x_price"]) / df["x_price"] * 100
    df["%_from_y"] = (df["current_price"] - df["y_price"]) / df["y_price"] * 100

    c1, c2, c3 = st.columns(3)
    status_filter = c1.selectbox(
        "Status", ["All", "🔵 Signal pending", "⚪ Naked", "🟢 Tested", "🔴 Failed"],
        index=0, key="ep_status",
    )
    symbol_filter = c2.text_input("Filter by symbol", "", key="ep_symbol").upper().strip()
    band = c3.slider("Show only within % of current price (vs Y)", 1, 50, 15, key="ep_band")

    view = df.dropna(subset=["current_price"]).copy()
    if status_filter != "All":
        view = view[view["status_label"] == status_filter]
    if symbol_filter:
        view = view[view["symbol"].str.contains(symbol_filter)]
    view = view[view["%_from_y"].abs() <= band]
    view = view.sort_values("%_from_y", key=lambda s: s.abs()).reset_index(drop=True)
    return view


def _single_chart_metrics(ep):
    return [[
        ("Current Price", f"₹{ep['current_price']:.2f}" if pd.notna(ep.get('current_price')) else "—", None),
        ("X (Highest High)", f"₹{ep['x_price']:.2f}" if pd.notna(ep.get('x_price')) else "—",
         f"{ep['%_from_x']:+.2f}%" if pd.notna(ep.get('%_from_x')) else None),
        ("Y (Buy Level)", f"₹{ep['y_price']:.2f}" if pd.notna(ep.get('y_price')) else "—",
         f"{ep['%_from_y']:+.2f}%" if pd.notna(ep.get('%_from_y')) else None),
        ("Status", ep.get("status_label", "—"), None),
    ]]


def _single_chart_caption(ep):
    parts = [f"20/50 crossover **{pd.to_datetime(ep['crossover_date']).strftime('%d-%b-%Y')}**"]
    if pd.notna(ep.get("touch_date")):
        parts.append(f"50 EMA touched **{pd.to_datetime(ep['touch_date']).strftime('%d-%b-%Y')}** (X locked)")
    if pd.notna(ep.get("y_fix_date")):
        parts.append(f"Signal fired **{pd.to_datetime(ep['y_fix_date']).strftime('%d-%b-%Y')}** (Y locked)")
    return " · ".join(parts)


def _multi_chart_caption(sym, ep):
    return (
        f"**{sym}**\n\n"
        f"Crossover {pd.to_datetime(ep['crossover_date']).strftime('%d-%b-%Y')} · "
        f"{ep.get('status_label', '')}"
    )


CONFIG = StrategyConfig(
    key="ep",
    title="📈 EMA Pullback Reentry Pattern",
    description=DESCRIPTION,
    status_legend_md=STATUS_LEGEND_MD,
    load_data=_load_data,
    apply_filters=_apply_filters,
    status_label_fn=_status_label,
    status_color_map=STATUS_COLOR_MAP,
    display_columns=DISPLAY_COLUMNS,
    numeric_pct_cols=["% From X", "% From Y"],
    format_spec=FORMAT_SPEC,
    chart_builder=core.build_ema_pullback_chart,
    single_chart_metrics_fn=_single_chart_metrics,
    single_chart_caption_fn=_single_chart_caption,
    multi_chart_caption_fn=_multi_chart_caption,
)
