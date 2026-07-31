"""
strategy_breakout_pullback.py
--------------------------------
Breakout-Pullback 4-Leg strategy page. Every calculation, status label,
column, and format spec here is copied verbatim from legacy_dashboard.py's
Breakout-Pullback section.
"""
import pandas as pd
import streamlit as st

import dashboard_core as core
from strategy_framework import StrategyConfig

DESCRIPTION = (
    "Daily 20/50/200 EMA. A 4-leg structure where Leg 1 establishes both EMAs "
    "below the 200 EMA, Leg 2 sees 20 EMA cross above 50 EMA (both still below 200), "
    "Leg 3 pulls back with 20 below 50 again, and Leg 4 breaks out with 20 above 50 "
    "and price closing above Leg 2's highest close (X). Y = lowest 50 EMA in Leg 3, "
    "Z = lowest price in Leg 3 — both are buy-on-pullback levels once the signal fires."
)

STATUS_LEGEND_MD = """
- **🟡 Signal fired** — price closed above X during Leg 4. Y and Z are now active
  buy-on-pullback levels, but neither has been retested yet.
- **🟠 Partially tested** — either Y or Z has been retested (price pulled away,
  then came back within the retest band), but not both.
- **🟢 Tested** — both Y and Z have been retested. That opportunity already played out.
- **🔴 Failed** — price dropped more than 8% below the lower of Y and Z at any point —
  support decisively broken, no longer a valid level.
- **Invalidation during Leg 3** — if both 20 and 50 EMA drop below Leg 1's lowest
  price during Leg 3, the whole setup is discarded and the scanner searches for
  a fresh Leg 1 from that point.
- **Leg 4 reset** — if 20 EMA crosses back below 50 EMA during Leg 4 before price
  ever closes above X, the whole setup resets and the scanner looks for a new Leg 1.
"""

BP_STATUS_LABELS = {
    "signal_fired": "🟡 Signal fired",
    "partially_tested": "🟠 Partially tested",
    "tested": "🟢 Tested",
    "failed": "🔴 Failed",
}

STATUS_COLOR_MAP = {
    "🟡 Signal fired": "background-color:#fff3cd; color:#8a6d00; font-weight:600",
    "🟠 Partially tested": "background-color:#fdebd0; color:#8a5a00; font-weight:600",
    "🟢 Tested": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
    "🔴 Failed": "background-color:#fadbd8; color:#943126; font-weight:600",
}

DISPLAY_COLUMNS = {
    "symbol": "Symbol",
    "leg1_start": "Leg 1 Started",
    "signal_date": "Signal Date",
    "status_label": "Status",
    "x_price": "X (Leg 2 High Close)",
    "y_price": "Y (Leg 3 Low 50EMA)",
    "z_price": "Z (Leg 3 Low Price)",
    "current_price": "Current Price",
    "%_from_x": "% From X",
    "%_from_y": "% From Y",
    "%_from_z": "% From Z",
    "max_runup_pct": "Max Run-up",
    "days_tracked": "Days Tracked",
    "post_event_drawdown_pct": "Post-Event Drawdown %",
    "post_event_days_to_recover": "Post-Event Recovery Days",
}

FORMAT_SPEC = {
    "Leg 1 Started": lambda d: d.strftime("%d-%b-%Y"),
    "Signal Date": lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
    "X (Leg 2 High Close)": "₹{:.2f}",
    "Y (Leg 3 Low 50EMA)": "₹{:.2f}",
    "Z (Leg 3 Low Price)": "₹{:.2f}",
    "Current Price": "₹{:.2f}",
    "% From X": "{:+.2f}%",
    "% From Y": "{:+.2f}%",
    "% From Z": "{:+.2f}%",
    "Max Run-up": "{:+.1f}%",
    "Days Tracked": "{:.0f}",
    "Post-Event Drawdown %": lambda v: "—" if pd.isna(v) else f"{v:.1f}%",
    "Post-Event Recovery Days": lambda v: "—" if pd.isna(v) else f"{int(v)}",
}


def _status_label(row):
    return BP_STATUS_LABELS.get(row["status"], row["status"])


def _load_data():
    df = core.load_breakout_pullback_ledger()
    if df.empty:
        return df
    df = df.copy()
    df["leg1_start"] = pd.to_datetime(df["leg1_start"])
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    return df


def _apply_filters(raw_df):
    df = raw_df.copy()
    df["%_from_x"] = (df["current_price"] - df["x_price"]) / df["x_price"] * 100
    df["%_from_y"] = (df["current_price"] - df["y_price"]) / df["y_price"] * 100
    df["%_from_z"] = (df["current_price"] - df["z_price"]) / df["z_price"] * 100

    c1, c2, c3 = st.columns(3)
    status_filter = c1.selectbox(
        "Status", ["All", "🟡 Signal fired", "🟠 Partially tested", "🟢 Tested", "🔴 Failed"],
        index=0, key="bp_status",
    )
    symbol_filter = c2.text_input("Filter by symbol", "", key="bp_symbol").upper().strip()
    band = c3.slider("Show only within % of current price (vs X, Y, or Z)", 1, 50, 15, key="bp_band")

    view = df.dropna(subset=["current_price"]).copy()
    if status_filter != "All":
        view = view[view["status_label"] == status_filter]
    if symbol_filter:
        view = view[view["symbol"].str.contains(symbol_filter)]
    view = view[
        (view["%_from_x"].abs() <= band) | (view["%_from_y"].abs() <= band) | (view["%_from_z"].abs() <= band)
    ]
    view["_closest"] = view[["%_from_x", "%_from_y", "%_from_z"]].abs().min(axis=1)
    view = view.sort_values("_closest").reset_index(drop=True)
    return view


def _single_chart_metrics(ep):
    return [[
        ("Current Price", f"₹{ep['current_price']:.2f}" if pd.notna(ep.get('current_price')) else "—", None),
        ("X (Leg 2 High)", f"₹{ep['x_price']:.2f}" if pd.notna(ep.get('x_price')) else "—",
         f"{ep['%_from_x']:+.2f}%" if pd.notna(ep.get('%_from_x')) else None),
        ("Y (Leg 3 Low 50EMA)", f"₹{ep['y_price']:.2f}" if pd.notna(ep.get('y_price')) else "—",
         f"{ep['%_from_y']:+.2f}%" if pd.notna(ep.get('%_from_y')) else None),
        ("Z (Leg 3 Low Price)", f"₹{ep['z_price']:.2f}" if pd.notna(ep.get('z_price')) else "—",
         f"{ep['%_from_z']:+.2f}%" if pd.notna(ep.get('%_from_z')) else None),
        ("Status", ep.get("status_label", "—"), None),
    ]]


def _single_chart_caption(ep):
    parts = [f"Leg 1 started **{pd.to_datetime(ep['leg1_start']).strftime('%d-%b-%Y')}**"]
    if pd.notna(ep.get("signal_date")):
        parts.append(f"Signal fired **{pd.to_datetime(ep['signal_date']).strftime('%d-%b-%Y')}**")
    return " · ".join(parts)


def _multi_chart_caption(sym, ep):
    return (
        f"**{sym}**\n\n"
        f"Leg1 {pd.to_datetime(ep['leg1_start']).strftime('%d-%b-%Y')} · "
        f"{ep.get('status_label', '')}"
    )


CONFIG = StrategyConfig(
    key="bp",
    title="🔀 Breakout-Pullback 4-Leg Pattern",
    description=DESCRIPTION,
    status_legend_md=STATUS_LEGEND_MD,
    load_data=_load_data,
    apply_filters=_apply_filters,
    status_label_fn=_status_label,
    status_color_map=STATUS_COLOR_MAP,
    display_columns=DISPLAY_COLUMNS,
    numeric_pct_cols=["% From X", "% From Y", "% From Z"],
    format_spec=FORMAT_SPEC,
    chart_builder=core.build_breakout_pullback_chart,
    single_chart_metrics_fn=_single_chart_metrics,
    single_chart_caption_fn=_single_chart_caption,
    multi_chart_caption_fn=_multi_chart_caption,
)
