"""
strategy_range_breakout.py
---------------------------
Range Breakout (5-Leg) strategy page — matches the actual StrategyConfig
interface used by strategy_framework.py.
"""
import numpy as np
import pandas as pd
import streamlit as st

import dashboard_core as core
from strategy_framework import StrategyConfig

DESCRIPTION = (
    "Monthly Pivot Range Breakout (5-Leg). Daily timeframe with monthly "
    "standard pivot points. Legs defined by month-over-month pivot direction. "
    "Signal appears once Leg 4 has completed its retest of Leg 2 Low."
)

STATUS_LEGEND_MD = """
- **4th Leg Retest Done** — Leg 4 has touched the Leg 2 Low retest zone. Pattern is now actionable.
- **5th Leg In Progress** — Leg 5 (up) is underway but monthly pivot has not yet closed above Leg 1 High.
- **5th Leg Completed** — Monthly pivot closed above Leg 1 High. Breakout confirmed.

**Pattern Variants:**
- *Regular* — clean 5-leg breakout with no false moves.
- *False Breakout (Leg 3)* — Leg 3 went significantly above Leg 1 High then reversed into Leg 4.
- *False Breakdown (Leg 4)* — Leg 4 went below Leg 2 Low but recovered, then Leg 5 confirmed.
- *False Both* — both false breakout and false breakdown occurred.

**Buy Levels:** Retests to Leg 1 High or Leg 2 Low Pivot / Low Price.
"""

STATUS_COLOR_MAP = {
    "4th Leg Retest Done": "background-color:#fff3cd; color:#8a6d00; font-weight:600",
    "5th Leg In Progress": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
    "5th Leg Completed — Breakout Confirmed": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
    "❌ Invalidated — Range Broken": "background-color:#f8d7da; color:#721c24; font-weight:600",
}

DISPLAY_COLUMNS = {
    "symbol": "Symbol",
    "status_label": "Status",
    "pattern_type_label": "Pattern Type",
    "leg1_high": "Leg1 High",
    "leg2_low_pivot": "Leg2 Low Pivot",
    "leg2_low_price": "Leg2 Low Price",
    "leg3_max_pivot": "Leg3 Max Pivot",
    "leg4_min_low": "Leg4 Min Low",
    "leg5_last_pivot": "Leg5 Last Pivot",
    "is_ongoing_label": "Ongoing",
    "leg1_start": "Leg1 Start",
    "leg4_end": "Leg4 End",
}

FORMAT_SPEC = {
    "Leg1 High": "₹{:.2f}",
    "Leg2 Low Pivot": "₹{:.2f}",
    "Leg2 Low Price": "₹{:.2f}",
    "Leg3 Max Pivot": "₹{:.2f}",
    "Leg4 Min Low": "₹{:.2f}",
    "Leg5 Last Pivot": "₹{:.2f}",
    "Leg1 Start": lambda d: pd.to_datetime(d).strftime("%d-%b-%Y") if pd.notna(d) else "—",
    "Leg4 End": lambda d: pd.to_datetime(d).strftime("%d-%b-%Y") if pd.notna(d) else "—",
}


def _status_label(row):
    status = row.get("status")
    if status == "leg4_retest_done":
        return "4th Leg Retest Done"
    if status == "leg5_progress":
        return "5th Leg In Progress"
    if status == "leg5_completed":
        return "5th Leg Completed — Breakout Confirmed"
    if status == "invalidated":
        return "❌ Invalidated — Range Broken"
    return status


def _pattern_type_label(row):
    ptype = row.get("pattern_type", "regular")
    mapping = {
        "regular": "Regular",
        "false_breakout_leg3": "False Breakout (Leg 3)",
        "false_breakdown_leg4": "False Breakdown (Leg 4)",
        "false_both": "False Both",
    }
    return mapping.get(ptype, ptype)


def _load_data():
    df = core.load_range_breakout_ledger()
    if df.empty:
        return df
    df = df.copy()
    for col in ["leg1_start", "leg1_end", "leg2_start", "leg2_end",
                "leg3_start", "leg3_end", "leg4_start", "leg4_end",
                "leg5_start", "leg5_end"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _apply_filters(raw_df):
    df = raw_df.copy()
    df["status_label"] = df.apply(_status_label, axis=1)
    df["pattern_type_label"] = df.apply(_pattern_type_label, axis=1)
    df["is_ongoing_label"] = df["is_ongoing"].map({True: "●", False: ""})

    c1, c2, c3 = st.columns(3)

    all_statuses = sorted(df["status_label"].dropna().unique().tolist())
    status_filter = c1.multiselect(
        "Status",
        options=all_statuses,
        default=[s for s in all_statuses if "Completed" not in s],
        key="rb_status",
    )

    all_types = sorted(df["pattern_type_label"].dropna().unique().tolist())
    type_filter = c2.multiselect(
        "Pattern Type",
        options=all_types,
        default=all_types,
        key="rb_type",
    )

    symbol_filter = c3.text_input("Filter by symbol", "", key="rb_symbol").upper().strip()

    view = df.copy()
    if status_filter:
        view = view[view["status_label"].isin(status_filter)]
    if type_filter:
        view = view[view["pattern_type_label"].isin(type_filter)]
    if symbol_filter:
        view = view[view["symbol"].str.contains(symbol_filter, na=False)]

    # Optional: range width filter
    if "leg1_high" in view.columns and "leg2_low_pivot" in view.columns:
        denom = view["leg2_low_pivot"].abs().replace(0, np.nan)
        view["range_width_pct"] = (view["leg1_high"] - view["leg2_low_pivot"]) / denom * 100
        min_width = st.sidebar.slider("Min Range Width %", 0.0, 50.0, 5.0, 1.0, key="rb_min_width")
        view = view[view["range_width_pct"] >= min_width]

    view = view.sort_values(["is_ongoing", "leg1_start"], ascending=[False, False]).reset_index(drop=True)
    return view


def _single_chart_metrics(ep):
    rows = []
    row1 = [
        ("Leg1 High", f"₹{ep['leg1_high']:.2f}" if pd.notna(ep.get('leg1_high')) else "—", None),
        ("Leg2 Low Pivot", f"₹{ep['leg2_low_pivot']:.2f}" if pd.notna(ep.get('leg2_low_pivot')) else "—", None),
        ("Leg2 Low Price", f"₹{ep['leg2_low_price']:.2f}" if pd.notna(ep.get('leg2_low_price')) else "—", None),
        ("Status", ep.get("status_label", "—"), None),
    ]
    rows.append(row1)
    if pd.notna(ep.get("leg3_max_pivot")):
        rows.append([
            ("Leg3 Max Pivot", f"₹{ep['leg3_max_pivot']:.2f}", None),
            ("Leg4 Min Low", f"₹{ep['leg4_min_low']:.2f}" if pd.notna(ep.get('leg4_min_low')) else "—", None),
            ("Leg5 Last Pivot", f"₹{ep['leg5_last_pivot']:.2f}" if pd.notna(ep.get('leg5_last_pivot')) else "—", None),
            ("Pattern", ep.get("pattern_type_label", "—"), None),
        ])
    return rows


def _single_chart_caption(ep):
    parts = [f"Setup from **{pd.to_datetime(ep['leg1_start']).strftime('%d-%b-%Y')}** to **{pd.to_datetime(ep['leg4_end']).strftime('%d-%b-%Y')}**"]
    if ep.get("status") == "leg5_completed":
        parts.append("Breakout **confirmed**")
    elif ep.get("status") == "leg5_progress":
        parts.append("Breakout **in progress**")
    else:
        parts.append("Waiting for **Leg 5 breakout**")
    if ep.get("is_ongoing"):
        parts.append("(ongoing)")
    return " · ".join(parts)


def _multi_chart_caption(sym, ep):
    return f"**{sym}**\n\n{ep.get('status_label', '')} · {ep.get('pattern_type_label', '')}"


CONFIG = StrategyConfig(
    key="range_breakout",
    title="📊 Range Breakout (5-Leg)",
    description=DESCRIPTION,
    status_legend_md=STATUS_LEGEND_MD,
    load_data=_load_data,
    apply_filters=_apply_filters,
    status_label_fn=_status_label,
    status_color_map=STATUS_COLOR_MAP,
    display_columns=DISPLAY_COLUMNS,
    numeric_pct_cols=[],
    format_spec=FORMAT_SPEC,
    chart_builder=core.build_range_breakout_chart,
    single_chart_metrics_fn=_single_chart_metrics,
    single_chart_caption_fn=_single_chart_caption,
    multi_chart_caption_fn=_multi_chart_caption,
)
