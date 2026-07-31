"""
strategy_five_leg.py
-----------------------
5-Leg EMA Reversal strategy page. Every calculation, status label, column,
and format spec here is copied verbatim from legacy_dashboard.py's 5-Leg
section — only the surrounding scaffolding changed shape to fit
StrategyConfig. Needs extra_kwargs_fn since build_five_leg_chart requires
min_leg_days (a global sidebar param, constant across the page, but still
passed per-episode for a uniform chart_builder call signature).
"""
import pandas as pd
import streamlit as st

import dashboard_core as core
from strategy_framework import StrategyConfig

DESCRIPTION = (
    "Daily 20/50/200 EMA. A down-up-down-up-down structure where each down leg "
    "makes a new low vs. the down leg two positions back, and each up leg makes "
    "a LOWER high vs. the up leg two positions back (either the EMA pair or "
    "price confirms it - whichever shows it). Once 5 legs qualify, the episode "
    "is flagged right away. **X** = lowest 200 EMA reached, **Y** = lowest "
    "price reached, from leg 1's start through the relevant end point below - "
    "support levels to watch for a buy, with your stop defined below them."
)

STATUS_LEGEND_MD = """
- **Leg** = a run of days where the 20 EMA stays continuously above (up leg)
  or below (down leg) the 50 EMA. Runs shorter than "Min days per leg" (in the
  sidebar) are merged into their neighbor so ordinary whipsaws don't count as
  separate legs.
- By the time the 5th leg qualifies, the 50 EMA has usually already been
  dragged below the 200 EMA by the decline - that's the normal case. In that
  case we wait for the 50 EMA to cross back ABOVE the 200 EMA (a golden-cross
  style recovery signal) before locking in X/Y:
  - **🟡 Pattern forming (below 200 EMA)** — 5 legs done, still waiting for
    that golden cross. X/Y are provisional, still updating.
  - **🟢 Complete (golden cross), pending retest** — the golden cross has
    happened, X/Y are locked in, and at least one of the two hasn't been
    revisited yet - the live watchlist state.
  - **🔵 Complete (golden cross), retested** — both X and Y have already
    been revisited since the golden cross. That opportunity already played out.
- **Rare case**: sometimes the whole 5-leg structure plays out without ever
  dragging the 50 EMA below the 200 EMA (a shallower pullback within a
  longer-term uptrend). There's no golden cross to wait for here, so X/Y are
  taken directly from the pattern's own low:
  - **🟠 Forming above 200 EMA (rare)** — 5 legs done, 50 EMA never dropped
    below 200 EMA, and leg 5 (the qualifying down leg) is still in progress.
    X/Y still updating.
  - **🟢 Complete (above 200 EMA), pending retest** — leg 5 has finished and
    X/Y are locked in, with at least one still unrevisited - the watchlist state.
  - **🔵 Complete (above 200 EMA), retested** — both X and Y have already
    been revisited since leg 5 ended.
- Legs 6 and beyond are unconstrained - they don't need to keep making new
  extremes. The minimum requirement is 5 legs.
"""

STATUS_COLOR_MAP = {
    "🟡 Pattern forming (below 200 EMA)": "background-color:#fff3cd; color:#8a6d00; font-weight:600",
    "🟠 Forming above 200 EMA (rare)": "background-color:#fde3cf; color:#a34700; font-weight:600",
    "🟢 Complete (golden cross), pending retest": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
    "🔵 Complete (golden cross), retested": "background-color:#d6eaf8; color:#1a5276; font-weight:600",
    "🟢 Complete (above 200 EMA), pending retest": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
    "🔵 Complete (above 200 EMA), retested": "background-color:#d6eaf8; color:#1a5276; font-weight:600",
}

DISPLAY_COLUMNS = {
    "symbol": "Symbol",
    "leg1_start": "Leg 1 Started",
    "qualified_date": "Qualified (5 legs)",
    "num_legs_observed": "Legs Observed",
    "status_label": "Status",
    "x_price": "X (Lowest 200 EMA)",
    "y_price": "Y (Lowest Price)",
    "current_price": "Current Price",
    "%_from_x": "% From X",
    "%_from_y": "% From Y",
    "retest_drawdown_pct": "Retest Drawdown %",
    "retest_days_to_recover": "Retest Recovery Days",
    "retest_drawdown_level": "On Level",
}

FORMAT_SPEC = {
    "Leg 1 Started": lambda d: d.strftime("%d-%b-%Y"),
    "Qualified (5 legs)": lambda d: d.strftime("%d-%b-%Y"),
    "X (Lowest 200 EMA)": "₹{:.2f}",
    "Y (Lowest Price)": "₹{:.2f}",
    "Current Price": "₹{:.2f}",
    "% From X": "{:+.2f}%",
    "% From Y": "{:+.2f}%",
    "Retest Drawdown %": lambda v: "—" if pd.isna(v) else f"{v:.1f}%",
    "Retest Recovery Days": lambda v: "—" if pd.isna(v) else f"{int(v)}",
}

FIVE_LEG_STATUS_OPTIONS = [
    "All",
    "🟡 Pattern forming (below 200 EMA)",
    "🟠 Forming above 200 EMA (rare)",
    "🟢 Complete (golden cross), pending retest",
    "🔵 Complete (golden cross), retested",
    "🟢 Complete (above 200 EMA), pending retest",
    "🔵 Complete (above 200 EMA), retested",
]


def _status_label(row):
    if row["status"] == "probe_complete":
        sub = core._combine_retest(row.get("x_retest_status"), row.get("y_retest_status"))
        return "🟢 Complete (golden cross), pending retest" if sub == "pending retest" \
            else "🔵 Complete (golden cross), retested"
    if row["status"] == "above_200_complete":
        sub = core._combine_retest(row.get("x_retest_status"), row.get("y_retest_status"))
        return "🟢 Complete (above 200 EMA), pending retest" if sub == "pending retest" \
            else "🔵 Complete (above 200 EMA), retested"
    return {
        "pattern_forming": "🟡 Pattern forming (below 200 EMA)",
        "above_200_forming": "🟠 Forming above 200 EMA (rare)",
    }.get(row["status"], row["status"])


def _load_data():
    df = core.load_five_leg_ledger()
    if df.empty:
        return df
    df = df.copy()
    df["leg1_start"] = pd.to_datetime(df["leg1_start"])
    df["qualified_date"] = pd.to_datetime(df["qualified_date"])
    return df


def _apply_filters(raw_df):
    df = raw_df.copy()
    df["%_from_x"] = (df["current_price"] - df["x_price"]) / df["x_price"] * 100
    df["%_from_y"] = (df["current_price"] - df["y_price"]) / df["y_price"] * 100

    c1, c2, c3 = st.columns(3)
    status_filter = c1.selectbox("Status", FIVE_LEG_STATUS_OPTIONS, index=0, key="five_leg_status")
    symbol_filter = c2.text_input("Filter by symbol", "", key="five_leg_symbol").upper().strip()
    band = c3.slider("Show only within % of current price (vs X or Y)", 1, 50, 15, key="five_leg_band")

    view = df.dropna(subset=["current_price"]).copy()
    if status_filter != "All":
        view = view[view["status_label"] == status_filter]
    if symbol_filter:
        view = view[view["symbol"].str.contains(symbol_filter)]
    view = view[(view["%_from_x"].abs() <= band) | (view["%_from_y"].abs() <= band)]
    view["_closest"] = view[["%_from_x", "%_from_y"]].abs().min(axis=1)
    view = view.sort_values("_closest").reset_index(drop=True)
    return view


def _single_chart_metrics(ep):
    return [[
        ("Current Price", f"₹{ep['current_price']:.2f}" if pd.notna(ep.get('current_price')) else "—", None),
        ("X (Lowest 200 EMA)", f"₹{ep['x_price']:.2f}" if pd.notna(ep.get('x_price')) else "—",
         f"{ep['%_from_x']:+.2f}%" if pd.notna(ep.get('%_from_x')) else None),
        ("Y (Lowest Price)", f"₹{ep['y_price']:.2f}" if pd.notna(ep.get('y_price')) else "—",
         f"{ep['%_from_y']:+.2f}%" if pd.notna(ep.get('%_from_y')) else None),
        ("Legs Observed", int(ep["num_legs_observed"]) if pd.notna(ep.get("num_legs_observed")) else "—", None),
        ("Status", ep.get("status_label", "—"), None),
    ]]


def _single_chart_caption(ep):
    return (
        f"Leg 1 started **{pd.to_datetime(ep['leg1_start']).strftime('%d-%b-%Y')}** · "
        f"Qualified (5 legs) **{pd.to_datetime(ep['qualified_date']).strftime('%d-%b-%Y')}**"
        + (f" · Golden Cross **{pd.to_datetime(ep['probe_date']).strftime('%d-%b-%Y')}**"
           if pd.notna(ep.get('probe_date')) else "")
    )


def _multi_chart_caption(sym, ep):
    return (
        f"**{sym}**\n\n"
        f"Leg1 {pd.to_datetime(ep['leg1_start']).strftime('%d-%b-%Y')} · "
        f"{ep.get('status_label', '')}"
    )


def _extra_kwargs(ep):
    """
    Placeholder — page_five_leg.py rebinds this at render time to close over
    the actual min_leg_days value from app_globals.load_globals(), since that
    sidebar widget has no explicit `key=` to look up via st.session_state.
    """
    return {"min_leg_days": 5}


CONFIG = StrategyConfig(
    key="five_leg",
    title="🔀 5-Leg EMA Reversal Pattern",
    description=DESCRIPTION,
    status_legend_md=STATUS_LEGEND_MD,
    load_data=_load_data,
    apply_filters=_apply_filters,
    status_label_fn=_status_label,
    status_color_map=STATUS_COLOR_MAP,
    display_columns=DISPLAY_COLUMNS,
    numeric_pct_cols=["% From X", "% From Y"],
    format_spec=FORMAT_SPEC,
    chart_builder=core.build_five_leg_chart,
    single_chart_metrics_fn=_single_chart_metrics,
    single_chart_caption_fn=_single_chart_caption,
    multi_chart_caption_fn=_multi_chart_caption,
    extra_kwargs_fn=_extra_kwargs,
)
