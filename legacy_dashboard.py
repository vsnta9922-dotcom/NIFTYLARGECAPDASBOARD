"""
legacy_dashboard.py
----------------------
The ORIGINAL all-strategies dashboard, UNCHANGED in substance — this is
everything that used to run after the sidebar+merged block, copied
verbatim (no calculation, filter, styling, or chart logic touched).

Kept as its own page ("Dashboard (Classic — All Strategies)") in the new
navigation so every strategy remains fully available on day one of the
pilot, even the five not yet migrated to the new StrategyConfig framework
(5-Leg, S1 Shift, Breakout-Pullback, EMA Pullback, Confluence/
Fundamentals). Only the sidebar+data-loading preamble was factored out
(into app_globals.load_globals(), shared with the new Pivot S1 and
Supertrend pages so widget keys and the @st.cache_data cache are shared
instead of tripled) — everything below that point, including the two
strategies that ALSO now have dedicated pages, is identical to before.
"""
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard_core import *  # noqa: F401,F403 — verbatim reuse of every
# loader/chart-builder function from the original monolithic script; the
# tail below references them as bare names (build_five_leg_chart, etc.),
# exactly as it always did when they lived in the same module.
#
# `import *` silently excludes underscore-prefixed names, but the tail
# below calls a couple of dashboard_core's "private" helpers as bare names
# too (they were never actually private within the original single-file
# script) - imported explicitly here so they aren't missing at runtime.
from dashboard_core import _trim_hist, _combine_retest
from app_globals import load_globals


def render():
    g = load_globals()
    symbol_df = g["symbol_df"]
    symbol_fetched_at = g["symbol_fetched_at"]
    symbol_source = g["symbol_source"]
    search = g["search"]
    metric_filter = g["metric_filter"]
    sort_col = g["sort_col"]
    sort_asc = g["sort_asc"]
    min_streak_days = g["min_streak_days"]
    retest_tolerance = g["retest_tolerance"]
    min_leg_days = g["min_leg_days"]
    only_show_retest_zone = g["only_show_retest_zone"]
    retest_risk_filter = g["retest_risk_filter"]
    min_qualify_months = g["min_qualify_months"]
    fail_pct = g["fail_pct"]
    min_qualify_days = g["min_qualify_days"]
    st_params_list = g["st_params_list"]
    metrics_df = g["metrics_df"]
    merged = g["merged"]


    # --------------------------------------------------------------------------
    # TOP-LEVEL SUMMARY METRICS
    # --------------------------------------------------------------------------
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Stocks tracked", len(merged))
    c2.metric("Advancers", int((merged["DayChg%"] > 0).sum()))
    c3.metric("Decliners", int((merged["DayChg%"] < 0).sum()))
    c4.metric("Above 200 EMA", int((merged["%From200EMA"] > 0).sum()))
    c5.metric("Volume spikes (>1.5x)", int((merged["RelVolume"] > 1.5).sum()))
    retest_mask_summary = merged["%FromX"].abs() <= retest_tolerance
    c6.metric(f"Retest-of-X setups (±{retest_tolerance}%)", int(retest_mask_summary.sum()))

    st.markdown("---")

    # --------------------------------------------------------------------------
    # APPLY FILTERS
    # --------------------------------------------------------------------------
    view = merged.copy()

    if search:
        view = view[view["Symbol"].str.contains(search) | view["CompanyName"].str.upper().str.contains(search)]

    if metric_filter == "Near 52W High (within 5%)":
        view = view[view["%FromHigh"] >= -5]
    elif metric_filter == "Near 52W Low (within 5%)":
        view = view[view["%FromLow"] <= 5]
    elif metric_filter == "Above 200 EMA":
        view = view[view["%From200EMA"] > 0]
    elif metric_filter == "Below 200 EMA":
        view = view[view["%From200EMA"] <= 0]
    elif metric_filter == "Volume spike (RelVol > 1.5x)":
        view = view[view["RelVolume"] > 1.5]
    elif metric_filter == "Retest of prior trend high (X) setups":
        view = view[view["StreakHighX"].notna() & (view["%FromX"].abs() <= retest_tolerance)]

    if only_show_retest_zone:
        view = view[view["StreakHighX"].notna() & (view["%FromX"].abs() <= retest_tolerance)]

    STATUS_FILTER_MAP = {
        "⚪ Naked — never approached again": "naked",
        "🟠 Testing resistance — approached from below, EMA not reclaimed yet": "testing_resistance",
        "🟢 Reclaimed, pending retest — EMA above X, no pullback to it yet (watchlist)": "reclaimed_pending_retest",
        "🔵 Reclaimed & retested — pullback to X already happened": "reclaimed_retested",
    }
    if retest_risk_filter in STATUS_FILTER_MAP:
        view = view[view["StreakStatus"] == STATUS_FILTER_MAP[retest_risk_filter]]

    view = view.sort_values(sort_col, ascending=sort_asc).reset_index(drop=True)

    # --------------------------------------------------------------------------
    # ACTIVE FILTER SUMMARY
    # --------------------------------------------------------------------------
    _active_filters = []
    if search:
        _active_filters.append(f"symbol contains '{search}'")
    if metric_filter != "All stocks":
        _active_filters.append(metric_filter)
    if only_show_retest_zone:
        _active_filters.append("inside retest zone")
    if retest_risk_filter != "All":
        _active_filters.append(retest_risk_filter.split(" — ")[0])  # short label only

    # --------------------------------------------------------------------------
    # TABLE
    # --------------------------------------------------------------------------
    st.subheader(f"📋 Stock Table ({len(view)} shown) — click a row to see its chart")
    if _active_filters:
        st.caption(f"🔍 Active filters: {' · '.join(_active_filters)} → {len(view)} of {len(merged)} stocks shown")

    with st.expander("ℹ️ What do 'Days Above EMA Before X', 'Total Streak Days' and the Retest Level colors mean?"):
        st.markdown(
            """
    - **Total Streak Days** *(shown in the Reference Level Ledger below, and in the
      per-stock caption)* — the full length of the uptrend that produced this
      level: from the day price first closed above the 200 EMA, to the day it
      finally closed back below it.
    - **Days Above EMA Before X** — how much of that streak had already played out
      *when the stock printed its high*. E.g. a 235-day streak where the high (X)
      was made on day 152 means: it ran for 152 days, topped out, then drifted for
      83 more days before the trend finally broke. This tells you whether X was an
      early breakout high (trend still had room) or a late, stretched-out top.
    - **Retest Level (X) color** — four possible states, based on what's happened
      to price *since* the streak that created X ended:
      - ⚪ **Naked** — price has never come back near X at all since the breakdown.
      - 🟠 **Testing resistance** — price came back near X from below, but the
        200 EMA hasn't reclaimed X yet. Outcome undetermined — **higher risk**.
      - 🟢 **Reclaimed, pending retest** — the 200 EMA has climbed back above X
        (only possible after price already spent a long stretch trading above X),
        but price hasn't yet pulled back down near X since reclaiming it. This is
        the live **watchlist** state: structure already confirmed bullish, the
        actual low-risk pullback entry just hasn't fired yet.
      - 🔵 **Reclaimed & retested** — EMA has reclaimed X *and* price has already
        pulled back down near X as support at least once. That entry already
        played out.
            """
        )

    display_df = view[
        [
            "Symbol",
            "CompanyName",
            "Price",
            "DayChg%",
            "52W High",
            "52W Low",
            "%FromHigh",
            "200EMA",
            "%From200EMA",
            "TrendDays",
            "StreakHighX",
            "DaysToX",
            "%FromX",
            "Volume",
            "AvgVol20D",
            "RelVolume",
        ]
    ].rename(
        columns={
            "CompanyName": "Company",
            "52W High": "52W High",
            "52W Low": "52W Low",
            "%FromHigh": "% From 52W High",
            "200EMA": "200 EMA",
            "%From200EMA": "% From 200 EMA",
            "TrendDays": "Days Above/Below 200EMA",
            "StreakHighX": "Retest Level (X)",
            "DaysToX": "Days Above EMA Before X",
            "%FromX": "% From X",
            "AvgVol20D": "20D Avg Vol",
            "RelVolume": "Rel Volume",
        }
    )


    def color_pct(val):
        if pd.isna(val):
            return ""
        color = "#1a7f37" if val >= 0 else "#b91c1c"
        return f"color: {color}; font-weight: 600"


    def color_relvol(val):
        if pd.isna(val):
            return ""
        if val > 2:
            return "background-color: #ffe08a; font-weight:600"
        if val > 1.5:
            return "background-color: #fff3cd"
        return ""


    def color_trend_days(val):
        # val is the signed numeric day count (kept numeric so header-click sort works correctly)
        if pd.isna(val):
            return ""
        return "color: #1a7f37; font-weight:600" if val >= 0 else "color: #b91c1c; font-weight:600"


    def color_pctfromx(val):
        if pd.isna(val):
            return ""
        if abs(val) <= retest_tolerance:
            return "background-color: #a7d8ff; font-weight:700"  # in the retest zone
        color = "#1a7f37" if val >= 0 else "#b91c1c"
        return f"color: {color}; font-weight: 600"


    def format_trend_days(val):
        if pd.isna(val):
            return "—"
        v = int(val)
        return f"{abs(v)}d {'Above' if v >= 0 else 'Below'}"


    def format_days_to_x(val):
        if pd.isna(val):
            return "—"
        return f"{int(val)}d"


    STATUS_COLORS = {
        "naked": "background-color:#eeeeee; color:#666; font-weight:700",
        "testing_resistance": "background-color:#fdebd0; color:#8a5a00; font-weight:700",
        "reclaimed_pending_retest": "background-color:#d1f2eb; color:#0b6b4f; font-weight:700",
        "reclaimed_retested": "background-color:#d6eaf8; color:#1a5276; font-weight:700",
    }
    # Precomputed per-row style for the 'Retest Level (X)' column, keyed off the
    # latest streak's 4-state status:
    #   naked                     -> grey   (never approached again)
    #   testing_resistance        -> amber  (approached from below, EMA not
    #                                reclaimed yet - undetermined, higher risk)
    #   reclaimed_pending_retest  -> green  (EMA above X, no pullback to it yet -
    #                                the live watchlist state)
    #   reclaimed_retested        -> blue   (pullback to X already happened -
    #                                setup already played out)
    _retest_status_styles = view["StreakStatus"].map(STATUS_COLORS).fillna("")
    _retest_status_styles.index = display_df.index


    def highlight_retest_col(_col):
        return _retest_status_styles


    styled = (
        display_df.style
        .map(color_pct, subset=["DayChg%", "% From 52W High", "% From 200 EMA"])
        .map(color_relvol, subset=["Rel Volume"])
        .map(color_trend_days, subset=["Days Above/Below 200EMA"])
        .map(color_pctfromx, subset=["% From X"])
        .apply(highlight_retest_col, subset=["Retest Level (X)"])
        .format(
            {
                "Price": "₹{:.2f}",
                "DayChg%": "{:+.2f}%",
                "52W High": "₹{:.2f}",
                "52W Low": "₹{:.2f}",
                "% From 52W High": "{:+.2f}%",
                "200 EMA": "₹{:.2f}",
                "% From 200 EMA": "{:+.2f}%",
                "Days Above/Below 200EMA": format_trend_days,
                "Retest Level (X)": "₹{:.2f}",
                "Days Above EMA Before X": format_days_to_x,
                "% From X": "{:+.2f}%",
                "Volume": "{:,.0f}",
                "20D Avg Vol": "{:,.0f}",
                "Rel Volume": "{:.2f}x",
            },
            na_rep="—",
        )
    )

    event = st.dataframe(
        styled,
        use_container_width=True,
        height=560,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="stock_table",
    )

    # --------------------------------------------------------------------------
    # CHART ON CLICK
    # --------------------------------------------------------------------------
    selected_symbol = None
    if event and event.selection and event.selection.get("rows"):
        sel_idx = event.selection["rows"][0]
        selected_symbol = display_df.iloc[sel_idx]["Symbol"]

    st.markdown("---")

    if selected_symbol:
        row = view[view["Symbol"] == selected_symbol].iloc[0]
        st.subheader(f"📊 {selected_symbol} — {row['CompanyName']}")

        mcol1, mcol2, mcol3, mcol4, mcol5, mcol6 = st.columns(6)
        mcol1.metric("Price", f"₹{row['Price']:.2f}", f"{row['DayChg%']:+.2f}%")
        mcol2.metric("52W High", f"₹{row['52W High']:.2f}", f"{row['%FromHigh']:+.2f}%")
        mcol3.metric("200 EMA", f"₹{row['200EMA']:.2f}", f"{row['%From200EMA']:+.2f}%")
        trend_lbl = f"{abs(int(row['TrendDays']))}d {'Above' if row['CurrentlyAbove200EMA'] else 'Below'}"
        mcol4.metric("Trend duration", trend_lbl)
        STATUS_LABELS = {
            "naked": "⚪ naked — never approached again",
            "testing_resistance": "🟠 testing resistance — EMA not reclaimed yet, higher-risk",
            "reclaimed_pending_retest": "🟢 reclaimed, pending retest — watchlist, lower-risk",
            "reclaimed_retested": "🔵 reclaimed & retested — pullback already happened",
        }
        if pd.notna(row["StreakHighX"]):
            risk_tag = STATUS_LABELS.get(row["StreakStatus"], row["StreakStatus"])
            mcol5.metric("Retest level (X)", f"₹{row['StreakHighX']:.2f}", f"{row['%FromX']:+.2f}% away")
        else:
            mcol5.metric("Retest level (X)", "—", f"No {int(min_streak_days)}d+ streak found yet")
        mcol6.metric("Rel Volume", f"{row['RelVolume']:.2f}x", f"Avg20D {row['AvgVol20D']:,.0f}")

        if pd.notna(row["StreakHighX"]):
            st.caption(
                f"📐 That uptrend ran for a **total of {int(row['StreakTotalDays'])} days above the 200 EMA** "
                f"before finally closing below it on {pd.to_datetime(row['StreakEndDate']).strftime('%d-%b-%Y')}. "
                f"Within that run, it had already been above the 200 EMA for "
                f"**{int(row['DaysToX'])} days** when it printed this high of ₹{row['StreakHighX']:.2f} "
                f"(then drifted for {int(row['DaysFromXToEnd'])} more days before the trend broke). "
                f"Status: **{risk_tag}**. "
                f"This stock has **{int(row['NumTotalStreaks'])} completed streak(s)** total, "
                f"of which **{int(row['NumUnresolvedLevels'])} are still unresolved** "
                f"(naked, testing resistance, or reclaimed-but-pending-retest)."
            )
            if pd.notna(row.get("MaxCorrectionPct")):
                reclaim_txt = (
                    f"took **{int(row['DaysToReclaim'])} days** for the 200 EMA to reclaim it"
                    if pd.notna(row.get("DaysToReclaim"))
                    else "hasn't reclaimed it yet (still ongoing - both figures may still update)"
                )
                st.caption(
                    f"📉 From that high of ₹{row['StreakHighX']:.2f}, price corrected a maximum of "
                    f"**{row['MaxCorrectionPct']:.1f}%** before the 200 EMA {reclaim_txt}. "
                    f"Depth and duration of this correction can help calibrate a stop-loss and a realistic "
                    f"target if a similar move plays out around a future retest."
                )
        if pd.notna(row["NearestNakedX"]) and row["NearestNakedX"] != row["StreakHighX"]:
            nearest_tag = STATUS_LABELS.get(row["NearestNakedStatus"], row["NearestNakedStatus"])
            st.caption(
                f"🎯 Nearest **unresolved** level overall is ₹{row['NearestNakedX']:.2f} "
                f"({row['%FromNearestNakedX']:+.2f}% away, status: {nearest_tag}), from a streak that ended "
                f"{pd.to_datetime(row['NearestNakedEnd']).strftime('%d-%b-%Y')} — this may be an older, "
                f"still-unresolved level worth watching alongside the latest one."
            )

        period_choice = st.radio(
            "Chart period", ["6mo", "1y", "2y", "5y", "10y", "max"], horizontal=True, index=2
        )
        with st.spinner(f"Loading chart for {selected_symbol}…"):
            streak_hist = fetch_full_history_with_indicators(selected_symbol)

        if not streak_hist.empty:
            streak_fig = build_streak_chart(streak_hist, selected_symbol, row, period_choice)
            st.plotly_chart(streak_fig, use_container_width=True, key=f"streak_chart_{selected_symbol}")
            streak_h = _trim_hist(streak_hist, period_choice)
            vol_fig = go.Figure(go.Bar(x=streak_h.index, y=streak_h["Volume"], name="Volume",
                                       marker_color="#6c757d"))
            vol_fig.update_layout(height=180, margin=dict(l=70, r=20, t=10, b=10),
                                  yaxis=dict(tickformat=",.0f", automargin=True))
            st.plotly_chart(vol_fig, use_container_width=True, key=f"streak_vol_{selected_symbol}")
    else:
        st.info("👆 Click any row in the table above to view its detailed candlestick chart.")

    # --------------------------------------------------------------------------
    # MULTI-CHART COMPARISON — view several stocks side by side
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("📊 Multi-Chart Comparison")
    st.caption(
        "Pick up to 6 stocks to view side by side - independent of the single-stock "
        "chart above. Each mini-chart shows daily candles with the 50/200 EMA overlay."
    )

    mc_col1, mc_col2 = st.columns([3, 1])
    multi_chart_symbols = mc_col1.multiselect(
        "Stocks to compare",
        options=sorted(symbol_df["Symbol"].tolist()),
        default=[],
        max_selections=6,
        key="multi_chart_symbols",
    )
    multi_chart_layout = mc_col2.radio(
        "Layout", ["1", "2", "4", "6"], horizontal=True, index=1, key="multi_chart_layout"
    )
    multi_chart_period = st.radio(
        "Chart period", ["6mo", "1y", "2y", "5y"], horizontal=True, index=1, key="multi_chart_period"
    )

    render_multi_chart_grid(multi_chart_symbols, multi_chart_layout, multi_chart_period, key_prefix="multichart")


    # --------------------------------------------------------------------------
    # REFERENCE LEVEL LEDGER (all naked/untested levels, across all of history)
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("📚 Reference Level Ledger — every streak level, any age, any status")
    st.caption(
        "Every completed 200-EMA streak ever detected for this universe is recorded here "
        "(persisted locally in `levels_ledger.db`), not just the most recent one per stock, "
        "and classified into four states based on what's happened since. As the local price "
        "cache grows over time, older levels stay visible instead of quietly falling out of "
        "a rolling fetch window."
    )
    st.caption(
        "⚪ **Naked** — never approached again · 🟠 **Testing resistance** — approached from "
        "below, EMA not reclaimed yet (undetermined, higher risk) · 🟢 **Reclaimed, pending "
        "retest** — EMA above X, no pullback to it yet (the live watchlist state) · 🔵 "
        "**Reclaimed & retested** — pullback to X already happened."
    )

    LEDGER_STATUS_LABELS = {
        "naked": "⚪ Naked",
        "testing_resistance": "🟠 Testing resistance",
        "reclaimed_pending_retest": "🟢 Reclaimed, pending retest",
        "reclaimed_retested": "🔵 Reclaimed & retested",
    }
    LEDGER_STATUS_COLORS = {
        "⚪ Naked": "background-color:#eeeeee; color:#666; font-weight:600",
        "🟠 Testing resistance": "background-color:#fdebd0; color:#8a5a00; font-weight:600",
        "🟢 Reclaimed, pending retest": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
        "🔵 Reclaimed & retested": "background-color:#d6eaf8; color:#1a5276; font-weight:600",
    }

    ledger_df = load_streak_ledger()
    if ledger_df.empty:
        st.info("No streak levels recorded yet — run the dashboard at least once with data loaded.")
    else:
        ledger_df["streak_end"] = pd.to_datetime(ledger_df["streak_end"])
        price_lookup = merged.set_index("Symbol")["Price"].to_dict()
        ledger_df["current_price"] = ledger_df["symbol"].map(price_lookup)
        ledger_df["%_from_x"] = (ledger_df["current_price"] - ledger_df["x_price"]) / ledger_df["x_price"] * 100
        ledger_df["age_years"] = (pd.Timestamp.now() - ledger_df["streak_end"]).dt.days / 365.25
        ledger_df["status_label"] = ledger_df["status"].map(LEDGER_STATUS_LABELS).fillna(ledger_df["status"])

        lcol1, lcol2, lcol3 = st.columns(3)
        ledger_status_filter = lcol1.selectbox(
            "Status",
            [
                "All",
                "⚪ Naked",
                "🟠 Testing resistance",
                "🟢 Reclaimed, pending retest",
                "🔵 Reclaimed & retested",
            ],
            index=0,
        )
        ledger_symbol_filter = lcol2.text_input("Filter by symbol", "").upper().strip()
        ledger_band = lcol3.slider("Show only within % of current price", 1, 30, 10)

        lview = ledger_df.dropna(subset=["current_price"]).copy()
        if ledger_status_filter != "All":
            lview = lview[lview["status_label"] == ledger_status_filter]
        if ledger_symbol_filter:
            lview = lview[lview["symbol"].str.contains(ledger_symbol_filter)]
        lview = lview[lview["%_from_x"].abs() <= ledger_band]
        lview = lview.sort_values("%_from_x", key=lambda s: s.abs()).reset_index(drop=True)

        st.caption(f"{len(lview)} level(s) match your filters (out of {len(ledger_df)} recorded total).")

        ledger_display = lview[
            [
                "symbol", "x_price", "streak_end", "age_years", "days_to_x",
                "total_streak_days", "status_label", "current_price", "%_from_x",
                "max_correction_pct", "days_to_reclaim",
                "retest_drawdown_pct", "retest_days_to_recover",
            ]
        ].rename(
            columns={
                "symbol": "Symbol",
                "x_price": "X (Level)",
                "streak_end": "Streak Ended",
                "age_years": "Age (yrs)",
                "days_to_x": "Days Above EMA Before X",
                "total_streak_days": "Total Streak Days",
                "status_label": "Status",
                "current_price": "Current Price",
                "%_from_x": "% From X",
                "max_correction_pct": "Max Correction From X",
                "days_to_reclaim": "Days for EMA to Reclaim X",
                "retest_drawdown_pct": "Retest Drawdown %",
                "retest_days_to_recover": "Retest Recovery Days",
            }
        )

        ledger_styled = (
            ledger_display.style
            .map(
                lambda v: "" if pd.isna(v) else ("color:#1a7f37;font-weight:600" if v >= 0 else "color:#b91c1c;font-weight:600"),
                subset=["% From X"],
            )
            .map(
                lambda v: LEDGER_STATUS_COLORS.get(v, ""),
                subset=["Status"],
            )
            .format(
                {
                    "X (Level)": "₹{:.2f}",
                    "Streak Ended": lambda d: d.strftime("%d-%b-%Y"),
                    "Age (yrs)": "{:.1f}",
                    "Current Price": "₹{:.2f}",
                    "Max Correction From X": lambda v: "—" if pd.isna(v) else f"{v:.1f}%",
                    "Days for EMA to Reclaim X": lambda v: "—" if pd.isna(v) else f"{int(v)}",
                    "Retest Drawdown %": lambda v: "—" if pd.isna(v) else f"{v:.1f}%",
                    "Retest Recovery Days": lambda v: "—" if pd.isna(v) else f"{int(v)}",
                    "% From X": "{:+.2f}%",
                },
                na_rep="—",
            )
        )
        ledger_event = st.dataframe(
            ledger_styled,
            use_container_width=True,
            height=400,
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            key="ledger_table",
        )

        # ----------------------------------------------------------------------
        # Resolve checked rows (checkboxes in the table itself, not a dropdown).
        # lview row order matches ledger_display row order 1:1, since
        # ledger_display is derived from lview with no further filtering/sorting
        # in between, so positional indices from the selection event map
        # straight onto lview.
        #
        # Streamlit's multi-row selection has no built-in cap, so we enforce
        # max 6 ourselves here (same convention as the main Multi-Chart
        # Comparison section) - a stock chart-comparison grid stops being
        # readable well before 6 panes anyway.
        # ----------------------------------------------------------------------
        MAX_LEDGER_COMPARE = 6
        ledger_selected_rows = []
        if ledger_event and ledger_event.selection and ledger_event.selection.get("rows"):
            ledger_selected_rows = ledger_event.selection["rows"]

        if len(ledger_selected_rows) > MAX_LEDGER_COMPARE:
            st.caption(
                f"⚠️ You checked {len(ledger_selected_rows)} rows - only the first "
                f"{MAX_LEDGER_COMPARE} are shown below. Uncheck a few to compare a different set."
            )
        ledger_selected_rows = ledger_selected_rows[:MAX_LEDGER_COMPARE]
        ledger_selected_levels = [lview.iloc[i] for i in ledger_selected_rows]
        # Symbols in the order checked, de-duplicated (a symbol could in theory
        # have two different levels both checked - only need it once for a chart).
        ledger_selected_symbols = list(dict.fromkeys(lvl["symbol"] for lvl in ledger_selected_levels))

        if len(ledger_selected_levels) == 1:
            # Exactly one row checked -> single-stock chart with THAT specific
            # level marked, same as the click-to-chart pattern on the main table.
            ledger_selected_level = ledger_selected_levels[0]
            ledger_selected_symbol = ledger_selected_level["symbol"]

            st.markdown("#### 📈 Selected level chart")
            company_row = merged[merged["Symbol"] == ledger_selected_symbol]
            company_name = company_row["CompanyName"].iloc[0] if not company_row.empty else ""
            st.caption(f"**{ledger_selected_symbol}** — {company_name}")
            ledger_period_choice = st.radio(
                "Chart period", ["6mo", "1y", "2y", "5y", "10y", "max"],
                horizontal=True, index=2, key="ledger_chart_period",
            )
            with st.spinner(f"Loading chart for {ledger_selected_symbol}…"):
                ledger_hist = fetch_full_history_with_indicators(ledger_selected_symbol)
            if not ledger_hist.empty:
                ledger_fig = build_ledger_level_chart(
                    ledger_hist, ledger_selected_symbol, ledger_selected_level, ledger_period_choice
                )
                st.plotly_chart(ledger_fig, use_container_width=True, key=f"ledger_chart_{ledger_selected_symbol}")
            else:
                st.warning(f"No price history available for {ledger_selected_symbol}.")

        elif len(ledger_selected_levels) >= 2:
            # Multiple rows checked -> multi-chart comparison, reusing the exact
            # same grid renderer as the main Multi-Chart Comparison section.
            st.markdown(f"#### 📊 Comparing {len(ledger_selected_symbols)} selected stock(s)")
            lcc1, lcc2 = st.columns([3, 1])
            ledger_compare_layout = lcc1.radio(
                "Layout", ["1", "2", "4", "6"], horizontal=True, index=1, key="ledger_compare_layout"
            )
            ledger_compare_period = lcc2.radio(
                "Chart period", ["6mo", "1y", "2y", "5y"], horizontal=True, index=1, key="ledger_compare_period"
            )
            render_multi_chart_grid(
                ledger_selected_symbols, ledger_compare_layout, ledger_compare_period,
                key_prefix="ledger_multichart",
            )

        else:
            st.info(
                "👆 Check one row's box in the ledger table above for its single-stock chart, "
                "or check 2-6 rows to compare them side by side."
            )

    # --------------------------------------------------------------------------
    # 5-LEG EMA REVERSAL PATTERN SCANNER
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("🔀 5-Leg EMA Reversal Pattern")
    st.caption(
        "Daily 20/50/200 EMA. A down-up-down-up-down structure where each down leg "
        "makes a new low vs. the down leg two positions back, and each up leg makes "
        "a LOWER high vs. the up leg two positions back (either the EMA pair or "
        "price confirms it - whichever shows it). Once 5 legs qualify, the episode "
        "is flagged right away. **X** = lowest 200 EMA reached, **Y** = lowest "
        "price reached, from leg 1's start through the relevant end point below - "
        "support levels to watch for a buy, with your stop defined below them."
    )
    with st.expander("ℹ️ What the statuses mean, and what counts as a leg"):
        st.markdown(
            """
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
        )

    five_leg_df = load_five_leg_ledger()
    if five_leg_df.empty:
        st.info("No 5-leg pattern episodes recorded yet — run the dashboard at least once with data loaded.")
    else:
        five_leg_df["leg1_start"] = pd.to_datetime(five_leg_df["leg1_start"])
        five_leg_df["qualified_date"] = pd.to_datetime(five_leg_df["qualified_date"])
        price_lookup_5leg = merged.set_index("Symbol")["Price"].to_dict()
        five_leg_df["current_price"] = five_leg_df["symbol"].map(price_lookup_5leg)
        five_leg_df["%_from_x"] = (five_leg_df["current_price"] - five_leg_df["x_price"]) / five_leg_df["x_price"] * 100
        five_leg_df["%_from_y"] = (five_leg_df["current_price"] - five_leg_df["y_price"]) / five_leg_df["y_price"] * 100

        def _five_leg_status_label(row):
            if row["status"] == "probe_complete":
                sub = _combine_retest(row.get("x_retest_status"), row.get("y_retest_status"))
                return "🟢 Complete (golden cross), pending retest" if sub == "pending retest" \
                    else "🔵 Complete (golden cross), retested"
            if row["status"] == "above_200_complete":
                sub = _combine_retest(row.get("x_retest_status"), row.get("y_retest_status"))
                return "🟢 Complete (above 200 EMA), pending retest" if sub == "pending retest" \
                    else "🔵 Complete (above 200 EMA), retested"
            return {
                "pattern_forming": "🟡 Pattern forming (below 200 EMA)",
                "above_200_forming": "🟠 Forming above 200 EMA (rare)",
            }.get(row["status"], row["status"])

        five_leg_df["status_label"] = five_leg_df.apply(_five_leg_status_label, axis=1)

        FIVE_LEG_STATUS_OPTIONS = [
            "All",
            "🟡 Pattern forming (below 200 EMA)",
            "🟠 Forming above 200 EMA (rare)",
            "🟢 Complete (golden cross), pending retest",
            "🔵 Complete (golden cross), retested",
            "🟢 Complete (above 200 EMA), pending retest",
            "🔵 Complete (above 200 EMA), retested",
        ]

        fcol1, fcol2, fcol3 = st.columns(3)
        five_leg_status_filter = fcol1.selectbox("Status", FIVE_LEG_STATUS_OPTIONS, index=0)
        five_leg_symbol_filter = fcol2.text_input("Filter by symbol", "", key="five_leg_symbol").upper().strip()
        five_leg_band = fcol3.slider("Show only within % of current price (vs X or Y)", 1, 50, 15, key="five_leg_band")

        fview = five_leg_df.dropna(subset=["current_price"]).copy()
        if five_leg_status_filter != "All":
            fview = fview[fview["status_label"] == five_leg_status_filter]
        if five_leg_symbol_filter:
            fview = fview[fview["symbol"].str.contains(five_leg_symbol_filter)]
        fview = fview[(fview["%_from_x"].abs() <= five_leg_band) | (fview["%_from_y"].abs() <= five_leg_band)]
        fview["_closest"] = fview[["%_from_x", "%_from_y"]].abs().min(axis=1)
        fview = fview.sort_values("_closest").reset_index(drop=True)

        st.caption(f"{len(fview)} episode(s) match your filters (out of {len(five_leg_df)} recorded total).")

        five_leg_display = fview[
            [
                "symbol", "leg1_start", "qualified_date", "num_legs_observed", "status_label",
                "x_price", "y_price", "current_price", "%_from_x", "%_from_y",
                "retest_drawdown_pct", "retest_days_to_recover", "retest_drawdown_level",
            ]
        ].rename(
            columns={
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
        )

        FIVE_LEG_STATUS_COLORS = {
            "🟡 Pattern forming (below 200 EMA)": "background-color:#fff3cd; color:#8a6d00; font-weight:600",
            "🟠 Forming above 200 EMA (rare)": "background-color:#fde3cf; color:#a34700; font-weight:600",
            "🟢 Complete (golden cross), pending retest": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
            "🔵 Complete (golden cross), retested": "background-color:#d6eaf8; color:#1a5276; font-weight:600",
            "🟢 Complete (above 200 EMA), pending retest": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
            "🔵 Complete (above 200 EMA), retested": "background-color:#d6eaf8; color:#1a5276; font-weight:600",
        }

        five_leg_styled = (
            five_leg_display.style
            .map(
                lambda v: "color:#1a7f37;font-weight:600" if v >= 0 else "color:#b91c1c;font-weight:600",
                subset=["% From X", "% From Y"],
            )
            .map(
                lambda v: FIVE_LEG_STATUS_COLORS.get(v, ""),
                subset=["Status"],
            )
            .format(
                {
                    "Leg 1 Started": lambda d: d.strftime("%d-%b-%Y"),
                    "Qualified (5 legs)": lambda d: d.strftime("%d-%b-%Y"),
                    "X (Lowest 200 EMA)": "₹{:.2f}",
                    "Y (Lowest Price)": "₹{:.2f}",
                    "Current Price": "₹{:.2f}",
                    "% From X": "{:+.2f}%",
                    "% From Y": "{:+.2f}%",
                    "Retest Drawdown %": lambda v: "—" if pd.isna(v) else f"{v:.1f}%",
                    "Retest Recovery Days": lambda v: "—" if pd.isna(v) else f"{int(v)}",
                },
                na_rep="—",
            )
        )
        five_leg_event = st.dataframe(
            five_leg_styled, use_container_width=True, height=400, hide_index=True,
            on_select="rerun", selection_mode="multi-row", key="five_leg_table",
        )

        # ---- 5-Leg selection handling ----
        five_leg_selected_rows = []
        if five_leg_event and five_leg_event.selection and five_leg_event.selection.get("rows"):
            five_leg_selected_rows = five_leg_event.selection["rows"]

        MAX_COMPARE = 6
        if len(five_leg_selected_rows) > MAX_COMPARE:
            st.caption(
                f"⚠️ You selected {len(five_leg_selected_rows)} rows — only the first "
                f"{MAX_COMPARE} are shown below. Uncheck a few to compare a different set."
            )
            five_leg_selected_rows = five_leg_selected_rows[:MAX_COMPARE]

        if len(five_leg_selected_rows) == 1:
            sel_idx = five_leg_selected_rows[0]
            ep = fview.iloc[sel_idx]
            five_leg_selected_sym = ep["symbol"]
            st.markdown(f"#### 📊 5-Leg Chart — {five_leg_selected_sym}")
            # Key metrics strip
            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            cur_price = ep.get("current_price", float("nan"))
            mc1.metric("Current Price", f"₹{cur_price:.2f}" if pd.notna(cur_price) else "—")
            mc2.metric("X (Lowest 200 EMA)", f"₹{ep['x_price']:.2f}" if pd.notna(ep.get('x_price')) else "—",
                       f"{ep['%_from_x']:+.2f}%" if pd.notna(ep.get('%_from_x')) else None)
            mc3.metric("Y (Lowest Price)", f"₹{ep['y_price']:.2f}" if pd.notna(ep.get('y_price')) else "—",
                       f"{ep['%_from_y']:+.2f}%" if pd.notna(ep.get('%_from_y')) else None)
            mc4.metric("Legs Observed", int(ep["num_legs_observed"]) if pd.notna(ep.get("num_legs_observed")) else "—")
            mc5.metric("Status", ep.get("status_label", "—"))
            st.caption(
                f"Leg 1 started **{pd.to_datetime(ep['leg1_start']).strftime('%d-%b-%Y')}** · "
                f"Qualified (5 legs) **{pd.to_datetime(ep['qualified_date']).strftime('%d-%b-%Y')}**"
                + (f" · Golden Cross **{pd.to_datetime(ep['probe_date']).strftime('%d-%b-%Y')}**"
                   if pd.notna(ep.get('probe_date')) else "")
            )
            fl_period = st.radio("Chart period", ["6mo", "1y", "2y", "5y", "10y", "max"],
                                 horizontal=True, index=2, key="fl_period")
            with st.spinner(f"Loading chart for {five_leg_selected_sym}…"):
                fl_hist = fetch_full_history_with_indicators(five_leg_selected_sym)
            if not fl_hist.empty:
                fl_fig = build_five_leg_chart(fl_hist, five_leg_selected_sym, ep, fl_period,
                                              min_leg_days=int(min_leg_days))
                st.plotly_chart(fl_fig, use_container_width=True, key=f"fl_chart_{five_leg_selected_sym}")
                # Volume sub-chart
                fl_h = _trim_hist(fl_hist, fl_period)
                vfig = go.Figure(go.Bar(x=fl_h.index, y=fl_h["Volume"], name="Volume",
                                        marker_color="#6c757d"))
                vfig.update_layout(height=160, margin=dict(l=70, r=20, t=6, b=10),
                                   yaxis=dict(tickformat=",.0f", automargin=True))
                st.plotly_chart(vfig, use_container_width=True, key=f"fl_vol_{five_leg_selected_sym}")
        elif len(five_leg_selected_rows) >= 2:
            st.markdown(f"#### 📊 Comparing {len(five_leg_selected_rows)} selected 5-Leg episode(s)")
            flcc1, flcc2 = st.columns([3, 1])
            fl_compare_layout = flcc1.radio(
                "Layout", ["1", "2", "4", "6"], horizontal=True, index=1, key="fl_compare_layout"
            )
            fl_compare_period = flcc2.radio(
                "Chart period", ["6mo", "1y", "2y", "5y"], horizontal=True, index=1, key="fl_compare_period"
            )
            five_leg_selected_eps = [(fview.iloc[i]["symbol"], fview.iloc[i]) for i in five_leg_selected_rows]
            render_strategy_multi_chart_grid(
                five_leg_selected_eps, build_five_leg_chart, fl_compare_layout, fl_compare_period,
                key_prefix="fl_multi",
                caption_fn=lambda sym, ep: (
                    f"**{sym}**\n\n"
                    f"Leg1 {pd.to_datetime(ep['leg1_start']).strftime('%d-%b-%Y')} · "
                    f"{ep.get('status_label', '')}"
                ),
                extra_kwargs_fn=lambda ep: {"min_leg_days": int(min_leg_days)},
            )
        else:
            st.info(
                "👆 Click any row above to see the 5-Leg pattern chart for that episode, "
                "or check 2–6 rows to compare them side by side."
            )
    # --------------------------------------------------------------------------
    # MONTHLY PIVOT S1 / 200 EMA SETUP SCANNER
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("📐 Monthly Pivot S1 Setup")
    st.caption(
        "Daily 200 EMA vs. the monthly Standard Pivot S1 (computed from the prior "
        "completed month's High/Low/Close, held flat through the current month). "
        "Once S1 stays cleanly above the 200 EMA for the qualifying window (no "
        "touch of either), the running high is tracked - fixed as **X** the "
        "first time price touches S1. From there, the running low is tracked as "
        "**Y** until the 200 EMA finally crosses above X - a touch of the 200 EMA "
        "anywhere before that invalidates the whole setup. X and Y are both "
        "buy-on-pullback levels once complete."
    )
    with st.expander("ℹ️ What the statuses mean"):
        st.markdown(
            """
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
        )

    pivot_df = load_pivot_ledger()
    if pivot_df.empty:
        st.info("No Monthly Pivot S1 episodes recorded yet — run the dashboard at least once with data loaded.")
    else:
        pivot_df["episode_start"] = pd.to_datetime(pivot_df["episode_start"])
        pivot_df["x_fix_date"] = pd.to_datetime(pivot_df["x_fix_date"])
        pivot_df["y_fix_date"] = pd.to_datetime(pivot_df["y_fix_date"])
        price_lookup_pivot = merged.set_index("Symbol")["Price"].to_dict()
        pivot_df["current_price"] = pivot_df["symbol"].map(price_lookup_pivot)
        pivot_df["%_from_x"] = (pivot_df["current_price"] - pivot_df["x_price"]) / pivot_df["x_price"] * 100
        pivot_df["%_from_y"] = (pivot_df["current_price"] - pivot_df["y_price"]) / pivot_df["y_price"] * 100

        def _pivot_status_label(row):
            if row["status"] == "complete":
                sub = _combine_retest(row.get("x_retest_status"), row.get("y_retest_status"))
                return "🟢 Complete, pending retest" if sub == "pending retest" else "🔵 Complete, retested"
            return {
                "tracking_x": "🟡 Tracking X",
                "x_fixed_pending_cross": "🟠 X fixed, pending cross",
            }.get(row["status"], row["status"])

        pivot_df["status_label"] = pivot_df.apply(_pivot_status_label, axis=1)

        PIVOT_STATUS_OPTIONS = [
            "All", "🟡 Tracking X", "🟠 X fixed, pending cross",
            "🟢 Complete, pending retest", "🔵 Complete, retested",
        ]

        pcol1, pcol2, pcol3 = st.columns(3)
        pivot_status_filter = pcol1.selectbox("Status", PIVOT_STATUS_OPTIONS, index=0, key="pivot_status")
        pivot_symbol_filter = pcol2.text_input("Filter by symbol", "", key="pivot_symbol").upper().strip()
        pivot_band = pcol3.slider("Show only within % of current price (vs X or Y)", 1, 50, 15, key="pivot_band")

        pview = pivot_df.dropna(subset=["current_price"]).copy()
        if pivot_status_filter != "All":
            pview = pview[pview["status_label"] == pivot_status_filter]
        if pivot_symbol_filter:
            pview = pview[pview["symbol"].str.contains(pivot_symbol_filter)]
        pview["_dist_x"] = pview["%_from_x"].abs()
        pview["_dist_y"] = pview["%_from_y"].abs()
        pview = pview[(pview["_dist_x"] <= pivot_band) | (pview["_dist_y"].fillna(999) <= pivot_band)]
        pview["_closest"] = pview[["_dist_x", "_dist_y"]].min(axis=1)
        pview = pview.sort_values("_closest").reset_index(drop=True)

        st.caption(f"{len(pview)} episode(s) match your filters (out of {len(pivot_df)} recorded total).")

        pivot_display = pview[
            [
                "symbol", "episode_start", "x_price", "y_price", "status_label",
                "current_price", "%_from_x", "%_from_y",
                "retest_drawdown_pct", "retest_days_to_recover", "retest_drawdown_level",
            ]
        ].rename(
            columns={
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
        )

        PIVOT_STATUS_COLORS = {
            "🟡 Tracking X": "background-color:#fff3cd; color:#8a6d00; font-weight:600",
            "🟠 X fixed, pending cross": "background-color:#fdebd0; color:#8a5a00; font-weight:600",
            "🟢 Complete, pending retest": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
            "🔵 Complete, retested": "background-color:#d6eaf8; color:#1a5276; font-weight:600",
        }

        pivot_styled = (
            pivot_display.style
            .map(
                lambda v: "" if pd.isna(v) else ("color:#1a7f37;font-weight:600" if v >= 0 else "color:#b91c1c;font-weight:600"),
                subset=["% From X", "% From Y"],
            )
            .map(lambda v: PIVOT_STATUS_COLORS.get(v, ""), subset=["Status"])
            .format(
                {
                    "Episode Start": lambda d: d.strftime("%d-%b-%Y"),
                    "X (Streak High)": lambda v: "—" if pd.isna(v) else f"₹{v:.2f}",
                    "Y (Streak Low)": lambda v: "—" if pd.isna(v) else f"₹{v:.2f}",
                    "Current Price": "₹{:.2f}",
                    "% From X": lambda v: "—" if pd.isna(v) else f"{v:+.2f}%",
                    "% From Y": lambda v: "—" if pd.isna(v) else f"{v:+.2f}%",
                    "Retest Drawdown %": lambda v: "—" if pd.isna(v) else f"{v:.1f}%",
                    "Retest Recovery Days": lambda v: "—" if pd.isna(v) else f"{int(v)}",
                },
                na_rep="—",
            )
        )
        pivot_event = st.dataframe(
            pivot_styled, use_container_width=True, height=400, hide_index=True,
            on_select="rerun", selection_mode="multi-row", key="pivot_table",
        )

        # ---- Monthly Pivot S1 selection handling ----
        pivot_selected_rows = []
        if pivot_event and pivot_event.selection and pivot_event.selection.get("rows"):
            pivot_selected_rows = pivot_event.selection["rows"]

        MAX_COMPARE = 6
        if len(pivot_selected_rows) > MAX_COMPARE:
            st.caption(
                f"⚠️ You selected {len(pivot_selected_rows)} rows — only the first "
                f"{MAX_COMPARE} are shown below. Uncheck a few to compare a different set."
            )
            pivot_selected_rows = pivot_selected_rows[:MAX_COMPARE]

        if len(pivot_selected_rows) == 1:
            sel_idx = pivot_selected_rows[0]
            ep = pview.iloc[sel_idx]
            pivot_selected_sym = ep["symbol"]
            st.markdown(f"#### 📊 Monthly Pivot S1 Chart — {pivot_selected_sym}")
            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            cur_price = ep.get("current_price", float("nan"))
            mc1.metric("Current Price", f"₹{cur_price:.2f}" if pd.notna(cur_price) else "—")
            mc2.metric("X (Running High)", f"₹{ep['x_price']:.2f}" if pd.notna(ep.get('x_price')) else "—",
                       f"{ep['%_from_x']:+.2f}%" if pd.notna(ep.get('%_from_x')) else None)
            mc3.metric("Y (Running Low)", f"₹{ep['y_price']:.2f}" if pd.notna(ep.get('y_price')) else "—",
                       f"{ep['%_from_y']:+.2f}%" if pd.notna(ep.get('%_from_y')) else None)
            mc4.metric("Status", ep.get("status_label", "—"))
            mc5.metric("Episode Start", pd.to_datetime(ep["episode_start"]).strftime("%d-%b-%Y"))
            cap_parts = [f"Episode started **{pd.to_datetime(ep['episode_start']).strftime('%d-%b-%Y')}**"]
            if pd.notna(ep.get("x_fix_date")):
                cap_parts.append(f"S1 touched (X fixed) **{pd.to_datetime(ep['x_fix_date']).strftime('%d-%b-%Y')}**")
            if pd.notna(ep.get("y_fix_date")):
                cap_parts.append(f"200 EMA crossed above X (Y fixed) **{pd.to_datetime(ep['y_fix_date']).strftime('%d-%b-%Y')}**")
            st.caption(" · ".join(cap_parts))
            ps1_period = st.radio("Chart period", ["6mo", "1y", "2y", "5y", "10y", "max"],
                                  horizontal=True, index=2, key="ps1_period")
            with st.spinner(f"Loading chart for {pivot_selected_sym}…"):
                ps1_hist = fetch_full_history_with_indicators(pivot_selected_sym)
            if not ps1_hist.empty:
                ps1_fig = build_pivot_s1_chart(ps1_hist, pivot_selected_sym, ep, ps1_period)
                st.plotly_chart(ps1_fig, use_container_width=True, key=f"ps1_chart_{pivot_selected_sym}")
                ps1_h = _trim_hist(ps1_hist, ps1_period)
                vfig = go.Figure(go.Bar(x=ps1_h.index, y=ps1_h["Volume"], name="Volume",
                                        marker_color="#6c757d"))
                vfig.update_layout(height=160, margin=dict(l=70, r=20, t=6, b=10),
                                   yaxis=dict(tickformat=",.0f", automargin=True))
                st.plotly_chart(vfig, use_container_width=True, key=f"ps1_vol_{pivot_selected_sym}")
        elif len(pivot_selected_rows) >= 2:
            st.markdown(f"#### 📊 Comparing {len(pivot_selected_rows)} selected Monthly Pivot S1 episode(s)")
            pcc1, pcc2 = st.columns([3, 1])
            p_compare_layout = pcc1.radio(
                "Layout", ["1", "2", "4", "6"], horizontal=True, index=1, key="pivot_compare_layout"
            )
            p_compare_period = pcc2.radio(
                "Chart period", ["6mo", "1y", "2y", "5y"], horizontal=True, index=1, key="pivot_compare_period"
            )
            pivot_selected_eps = [(pview.iloc[i]["symbol"], pview.iloc[i]) for i in pivot_selected_rows]
            render_strategy_multi_chart_grid(
                pivot_selected_eps, build_pivot_s1_chart, p_compare_layout, p_compare_period,
                key_prefix="ps1_multi",
                caption_fn=lambda sym, ep: (
                    f"**{sym}**\n\n"
                    f"Episode {pd.to_datetime(ep['episode_start']).strftime('%d-%b-%Y')} · "
                    f"{ep.get('status_label', '')}"
                ),
            )
        else:
            st.info(
                "👆 Click any row above to see the Monthly Pivot S1 chart for that episode, "
                "or check 2–6 rows to compare them side by side."
            )
    # --------------------------------------------------------------------------
    # MONTHLY S1 SHIFT UP SETUP SCANNER
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("🔺 Monthly S1 Shift Up Setup")
    st.caption(
        "Most months where price touches/closes at or below the monthly Pivot "
        "S1, the FOLLOWING month's S1 ends up lower too - the decline drags the "
        "whole range down. In the rare case where the following month's S1 is "
        "actually HIGHER, that's a sign of strong responsive buying. **X** = "
        "that month's lowest low - a buy-on-revisit level once this rare shift "
        "is confirmed."
    )
    with st.expander("ℹ️ What the statuses mean"):
        st.markdown(
            """
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
        )

    s1_shift_df = load_s1_shift_ledger()
    if s1_shift_df.empty:
        st.info("No Monthly S1 Shift Up episodes recorded yet — run the dashboard at least once with data loaded.")
    else:
        s1_shift_df["anchor_date"] = pd.to_datetime(s1_shift_df["anchor_date"])
        price_lookup_s1shift = merged.set_index("Symbol")["Price"].to_dict()
        s1_shift_df["current_price"] = s1_shift_df["symbol"].map(price_lookup_s1shift)
        s1_shift_df["%_from_x"] = (s1_shift_df["current_price"] - s1_shift_df["x_price"]) / s1_shift_df["x_price"] * 100

        def _s1_shift_status_label(row):
            """
            Naked episodes are split into two display states:
              ⚪ Naked          — price still above X (level untouched, watching for pullback).
              🟡 Naked (below)  — price has drifted below X without triggering a confirmed
                                  retest (the confirm-away-then-return gate was never met).
                                  The level is no longer a clean buy-on-pullback — it is now
                                  overhead resistance. Flagged amber so it stands out from a
                                  genuinely untouched level.
            """
            status = row.get("status")
            if status == "naked" and pd.notna(row.get("%_from_x")) and row["%_from_x"] < 0:
                return "🟡 Naked (below level)"
            return {"naked": "⚪ Naked", "tested": "🟢 Tested", "failed": "🔴 Failed"}.get(status, status)

        s1_shift_df["status_label"] = s1_shift_df.apply(_s1_shift_status_label, axis=1)

        scol1, scol2, scol3 = st.columns(3)
        s1_shift_status_filter = scol1.selectbox(
            "Status", ["All", "⚪ Naked", "🟡 Naked (below level)", "🟢 Tested", "🔴 Failed"],
            index=0, key="s1_shift_status"
        )
        s1_shift_symbol_filter = scol2.text_input("Filter by symbol", "", key="s1_shift_symbol").upper().strip()
        s1_shift_band = scol3.slider("Show only within % of current price (vs X)", 1, 50, 15, key="s1_shift_band")

        sview = s1_shift_df.dropna(subset=["current_price"]).copy()
        if s1_shift_status_filter != "All":
            sview = sview[sview["status_label"] == s1_shift_status_filter]
        if s1_shift_symbol_filter:
            sview = sview[sview["symbol"].str.contains(s1_shift_symbol_filter)]
        sview = sview[sview["%_from_x"].abs() <= s1_shift_band]
        sview = sview.sort_values("%_from_x", key=lambda s: s.abs()).reset_index(drop=True)

        st.caption(f"{len(sview)} episode(s) match your filters (out of {len(s1_shift_df)} recorded total).")

        s1_shift_display = sview[
            [
                "symbol", "month", "x_price", "current_price", "%_from_x", "status_label",
                "max_runup_pct", "days_tracked", "post_event_drawdown_pct", "post_event_days_to_recover",
            ]
        ].rename(
            columns={
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
        )

        S1_SHIFT_STATUS_COLORS = {
            "⚪ Naked":             "background-color:#eeeeee; color:#666; font-weight:600",
            "🟡 Naked (below level)": "background-color:#fff3cd; color:#8a6d00; font-weight:600",
            "🟢 Tested":            "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
            "🔴 Failed":            "background-color:#fadbd8; color:#943126; font-weight:600",
        }

        s1_shift_styled = (
            s1_shift_display.style
            .map(
                lambda v: "" if pd.isna(v) else ("color:#1a7f37;font-weight:600" if v >= 0 else "color:#b91c1c;font-weight:600"),
                subset=["% From X"],
            )
            .map(lambda v: S1_SHIFT_STATUS_COLORS.get(v, ""), subset=["Status"])
            .format(
                {
                    "X (Month Low)": "₹{:.2f}",
                    "Current Price": "₹{:.2f}",
                    "% From X": "{:+.2f}%",
                    "Max Run-up": "{:+.1f}%",
                    "Days Tracked": "{:.0f}",
                    "Drawdown After Test/Fail": lambda v: "—" if pd.isna(v) else f"{v:.1f}%",
                    "Days to Recover": lambda v: "—" if pd.isna(v) else f"{int(v)}",
                },
                na_rep="—",
            )
        )
        s1_shift_event = st.dataframe(
            s1_shift_styled, use_container_width=True, height=400, hide_index=True,
            on_select="rerun", selection_mode="multi-row", key="s1_shift_table",
        )

        # ---- Monthly S1 Shift Up selection handling ----
        s1_shift_selected_rows = []
        if s1_shift_event and s1_shift_event.selection and s1_shift_event.selection.get("rows"):
            s1_shift_selected_rows = s1_shift_event.selection["rows"]

        MAX_COMPARE = 6
        if len(s1_shift_selected_rows) > MAX_COMPARE:
            st.caption(
                f"⚠️ You selected {len(s1_shift_selected_rows)} rows — only the first "
                f"{MAX_COMPARE} are shown below. Uncheck a few to compare a different set."
            )
            s1_shift_selected_rows = s1_shift_selected_rows[:MAX_COMPARE]

        if len(s1_shift_selected_rows) == 1:
            sel_idx = s1_shift_selected_rows[0]
            ep = sview.iloc[sel_idx]
            s1_shift_selected_sym = ep["symbol"]
            st.markdown(f"#### 📊 Monthly S1 Shift Up Chart — {s1_shift_selected_sym}")
            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            cur_price = ep.get("current_price", float("nan"))
            mc1.metric("Current Price", f"₹{cur_price:.2f}" if pd.notna(cur_price) else "—")
            mc2.metric("X (Month Low)", f"₹{ep['x_price']:.2f}" if pd.notna(ep.get('x_price')) else "—",
                       f"{ep['%_from_x']:+.2f}%" if pd.notna(ep.get('%_from_x')) else None)
            mc3.metric("S1(M)", f"₹{ep['s1_month']:.2f}" if pd.notna(ep.get('s1_month')) else "—")
            mc4.metric("S1(M+1) ↑", f"₹{ep['s1_next_month']:.2f}" if pd.notna(ep.get('s1_next_month')) else "—")
            mc5.metric("Status", ep.get("status_label", "—"))
            cap_parts = [f"Setup month **{ep['month']}** · X (month low) on **{ep['x_date']}**"]
            if pd.notna(ep.get("anchor_date")):
                cap_parts.append(f"Tracking since **{pd.to_datetime(ep['anchor_date']).strftime('%d-%b-%Y')}**")
            if ep.get("status") == "tested" and pd.notna(ep.get("tested_date")):
                cap_parts.append(f"Retested **{pd.to_datetime(ep['tested_date']).strftime('%d-%b-%Y')}**")
            if ep.get("status") == "failed" and pd.notna(ep.get("failed_date")):
                cap_parts.append(f"Failed **{pd.to_datetime(ep['failed_date']).strftime('%d-%b-%Y')}**")
            st.caption(" · ".join(cap_parts))
            ss_period = st.radio("Chart period", ["6mo", "1y", "2y", "5y", "10y", "max"],
                                 horizontal=True, index=2, key="ss_period")
            with st.spinner(f"Loading chart for {s1_shift_selected_sym}…"):
                ss_hist = fetch_full_history_with_indicators(s1_shift_selected_sym)
            if not ss_hist.empty:
                ss_fig = build_s1_shift_chart(ss_hist, s1_shift_selected_sym, ep, ss_period)
                st.plotly_chart(ss_fig, use_container_width=True, key=f"ss_chart_{s1_shift_selected_sym}")
                ss_h = _trim_hist(ss_hist, ss_period)
                vfig = go.Figure(go.Bar(x=ss_h.index, y=ss_h["Volume"], name="Volume",
                                        marker_color="#6c757d"))
                vfig.update_layout(height=160, margin=dict(l=70, r=20, t=6, b=10),
                                   yaxis=dict(tickformat=",.0f", automargin=True))
                st.plotly_chart(vfig, use_container_width=True, key=f"ss_vol_{s1_shift_selected_sym}")
        elif len(s1_shift_selected_rows) >= 2:
            st.markdown(f"#### 📊 Comparing {len(s1_shift_selected_rows)} selected S1 Shift Up episode(s)")
            scc1, scc2 = st.columns([3, 1])
            ss_compare_layout = scc1.radio(
                "Layout", ["1", "2", "4", "6"], horizontal=True, index=1, key="ss_compare_layout"
            )
            ss_compare_period = scc2.radio(
                "Chart period", ["6mo", "1y", "2y", "5y"], horizontal=True, index=1, key="ss_compare_period"
            )
            s1_shift_selected_eps = [(sview.iloc[i]["symbol"], sview.iloc[i]) for i in s1_shift_selected_rows]
            render_strategy_multi_chart_grid(
                s1_shift_selected_eps, build_s1_shift_chart, ss_compare_layout, ss_compare_period,
                key_prefix="ss_multi",
                caption_fn=lambda sym, ep: f"**{sym}**\n\nMonth {ep['month']} · {ep.get('status_label', '')}",
            )
        else:
            st.info(
                "👆 Click any row above to see the Monthly S1 Shift Up chart for that episode, "
                "or check 2–6 rows to compare them side by side."
            )
    # --------------------------------------------------------------------------
    # BREAKOUT-PULLBACK 4-LEG PATTERN SCANNER
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("🔀 Breakout-Pullback 4-Leg Pattern")
    st.caption(
        "Daily 20/50/200 EMA. A 4-leg structure where Leg 1 establishes both EMAs "
        "below the 200 EMA, Leg 2 sees 20 EMA cross above 50 EMA (both still below 200), "
        "Leg 3 pulls back with 20 below 50 again, and Leg 4 breaks out with 20 above 50 "
        "and price closing above Leg 2's highest close (X). Y = lowest 50 EMA in Leg 3, "
        "Z = lowest price in Leg 3 — both are buy-on-pullback levels once the signal fires."
    )
    with st.expander("ℹ️ What the statuses mean"):
        st.markdown(
            """
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
        )

    bp_df = load_breakout_pullback_ledger()
    if bp_df.empty:
        st.info("No Breakout-Pullback episodes recorded yet — run the dashboard at least once with data loaded.")
    else:
        bp_df["leg1_start"] = pd.to_datetime(bp_df["leg1_start"])
        bp_df["signal_date"] = pd.to_datetime(bp_df["signal_date"])
        price_lookup_bp = merged.set_index("Symbol")["Price"].to_dict()
        bp_df["current_price"] = bp_df["symbol"].map(price_lookup_bp)
        bp_df["%_from_x"] = (bp_df["current_price"] - bp_df["x_price"]) / bp_df["x_price"] * 100
        bp_df["%_from_y"] = (bp_df["current_price"] - bp_df["y_price"]) / bp_df["y_price"] * 100
        bp_df["%_from_z"] = (bp_df["current_price"] - bp_df["z_price"]) / bp_df["z_price"] * 100

        BP_STATUS_LABELS = {
            "signal_fired": "🟡 Signal fired",
            "partially_tested": "🟠 Partially tested",
            "tested": "🟢 Tested",
            "failed": "🔴 Failed",
        }
        bp_df["status_label"] = bp_df["status"].map(BP_STATUS_LABELS).fillna(bp_df["status"])

        BP_STATUS_OPTIONS = [
            "All", "🟡 Signal fired", "🟠 Partially tested", "🟢 Tested", "🔴 Failed",
        ]

        bpcol1, bpcol2, bpcol3 = st.columns(3)
        bp_status_filter = bpcol1.selectbox("Status", BP_STATUS_OPTIONS, index=0, key="bp_status")
        bp_symbol_filter = bpcol2.text_input("Filter by symbol", "", key="bp_symbol").upper().strip()
        bp_band = bpcol3.slider("Show only within % of current price (vs X, Y, or Z)", 1, 50, 15, key="bp_band")

        bpview = bp_df.dropna(subset=["current_price"]).copy()
        if bp_status_filter != "All":
            bpview = bpview[bpview["status_label"] == bp_status_filter]
        if bp_symbol_filter:
            bpview = bpview[bpview["symbol"].str.contains(bp_symbol_filter)]
        bpview = bpview[
            (bpview["%_from_x"].abs() <= bp_band) |
            (bpview["%_from_y"].abs() <= bp_band) |
            (bpview["%_from_z"].abs() <= bp_band)
        ]
        bpview["_closest"] = bpview[["%_from_x", "%_from_y", "%_from_z"]].abs().min(axis=1)
        bpview = bpview.sort_values("_closest").reset_index(drop=True)

        st.caption(f"{len(bpview)} episode(s) match your filters (out of {len(bp_df)} recorded total).")

        bp_display = bpview[
            [
                "symbol", "leg1_start", "signal_date", "status_label",
                "x_price", "y_price", "z_price", "current_price",
                "%_from_x", "%_from_y", "%_from_z",
                "max_runup_pct", "days_tracked",
                "post_event_drawdown_pct", "post_event_days_to_recover",
            ]
        ].rename(
            columns={
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
        )

        BP_STATUS_COLORS = {
            "🟡 Signal fired": "background-color:#fff3cd; color:#8a6d00; font-weight:600",
            "🟠 Partially tested": "background-color:#fdebd0; color:#8a5a00; font-weight:600",
            "🟢 Tested": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
            "🔴 Failed": "background-color:#fadbd8; color:#943126; font-weight:600",
        }

        bp_styled = (
            bp_display.style
            .map(
                lambda v: "" if pd.isna(v) else ("color:#1a7f37;font-weight:600" if v >= 0 else "color:#b91c1c;font-weight:600"),
                subset=["% From X", "% From Y", "% From Z"],
            )
            .map(lambda v: BP_STATUS_COLORS.get(v, ""), subset=["Status"])
            .format(
                {
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
                },
                na_rep="—",
            )
        )
        bp_event = st.dataframe(
            bp_styled, use_container_width=True, height=400, hide_index=True,
            on_select="rerun", selection_mode="multi-row", key="bp_table",
        )

        # ---- Breakout-Pullback selection handling ----
        bp_selected_rows = []
        if bp_event and bp_event.selection and bp_event.selection.get("rows"):
            bp_selected_rows = bp_event.selection["rows"]

        MAX_COMPARE = 6
        if len(bp_selected_rows) > MAX_COMPARE:
            st.caption(
                f"⚠️ You selected {len(bp_selected_rows)} rows — only the first "
                f"{MAX_COMPARE} are shown below. Uncheck a few to compare a different set."
            )
            bp_selected_rows = bp_selected_rows[:MAX_COMPARE]

        if len(bp_selected_rows) == 1:
            sel_idx = bp_selected_rows[0]
            ep = bpview.iloc[sel_idx]
            bp_selected_sym = ep["symbol"]
            st.markdown(f"#### 📊 Breakout-Pullback Chart — {bp_selected_sym}")
            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            cur_price = ep.get("current_price", float("nan"))
            mc1.metric("Current Price", f"₹{cur_price:.2f}" if pd.notna(cur_price) else "—")
            mc2.metric("X (Leg 2 High)", f"₹{ep['x_price']:.2f}" if pd.notna(ep.get('x_price')) else "—",
                       f"{ep['%_from_x']:+.2f}%" if pd.notna(ep.get('%_from_x')) else None)
            mc3.metric("Y (Leg 3 Low 50EMA)", f"₹{ep['y_price']:.2f}" if pd.notna(ep.get('y_price')) else "—",
                       f"{ep['%_from_y']:+.2f}%" if pd.notna(ep.get('%_from_y')) else None)
            mc4.metric("Z (Leg 3 Low Price)", f"₹{ep['z_price']:.2f}" if pd.notna(ep.get('z_price')) else "—",
                       f"{ep['%_from_z']:+.2f}%" if pd.notna(ep.get('%_from_z')) else None)
            mc5.metric("Status", ep.get("status_label", "—"))
            cap_parts = [f"Leg 1 started **{pd.to_datetime(ep['leg1_start']).strftime('%d-%b-%Y')}**"]
            if pd.notna(ep.get("signal_date")):
                cap_parts.append(f"Signal fired **{pd.to_datetime(ep['signal_date']).strftime('%d-%b-%Y')}**")
            st.caption(" · ".join(cap_parts))
            bp_period = st.radio("Chart period", ["6mo", "1y", "2y", "5y", "10y", "max"],
                                 horizontal=True, index=2, key="bp_period")
            with st.spinner(f"Loading chart for {bp_selected_sym}…"):
                bp_hist = fetch_full_history_with_indicators(bp_selected_sym)
            if not bp_hist.empty:
                bp_fig = build_breakout_pullback_chart(bp_hist, bp_selected_sym, ep, bp_period)
                st.plotly_chart(bp_fig, use_container_width=True, key=f"bp_chart_{bp_selected_sym}")
                bp_h = _trim_hist(bp_hist, bp_period)
                vfig = go.Figure(go.Bar(x=bp_h.index, y=bp_h["Volume"], name="Volume",
                                        marker_color="#6c757d"))
                vfig.update_layout(height=160, margin=dict(l=70, r=20, t=6, b=10),
                                   yaxis=dict(tickformat=",.0f", automargin=True))
                st.plotly_chart(vfig, use_container_width=True, key=f"bp_vol_{bp_selected_sym}")
        elif len(bp_selected_rows) >= 2:
            st.markdown(f"#### 📊 Comparing {len(bp_selected_rows)} selected Breakout-Pullback episode(s)")
            bcc1, bcc2 = st.columns([3, 1])
            bp_compare_layout = bcc1.radio(
                "Layout", ["1", "2", "4", "6"], horizontal=True, index=1, key="bp_compare_layout"
            )
            bp_compare_period = bcc2.radio(
                "Chart period", ["6mo", "1y", "2y", "5y"], horizontal=True, index=1, key="bp_compare_period"
            )
            bp_selected_eps = [(bpview.iloc[i]["symbol"], bpview.iloc[i]) for i in bp_selected_rows]
            render_strategy_multi_chart_grid(
                bp_selected_eps, build_breakout_pullback_chart, bp_compare_layout, bp_compare_period,
                key_prefix="bp_multi",
                caption_fn=lambda sym, ep: (
                    f"**{sym}**\n\n"
                    f"Leg1 {pd.to_datetime(ep['leg1_start']).strftime('%d-%b-%Y')} · "
                    f"{ep.get('status_label', '')}"
                ),
            )
        else:
            st.info(
                "👆 Click any row above to see the Breakout-Pullback chart for that episode, "
                "or check 2–6 rows to compare them side by side."
            )
    # --------------------------------------------------------------------------
    # EMA PULLBACK REENTRY PATTERN SCANNER
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("📈 EMA Pullback Reentry Pattern")
    st.caption(
        "Daily 20/50 EMA only. After 20 EMA crosses above 50 EMA, price must stay "
        "cleanly above the 50 EMA (no intraday Low touching it) for at least "
        f"{int(min_qualify_days)} trading days. The highest High made during that clean "
        "run is **X**. The first day price pulls back to touch the 50 EMA locks X in. "
        "From there the lowest Low is tracked as **Y** until the 50 EMA crosses above X "
        "— that fires the signal. Any 20/50 death-cross during Y-tracking resets the setup. "
        "Pullback to **Y** is the buy-on-support entry."
    )
    with st.expander("ℹ️ What the statuses mean"):
        st.markdown(
            """
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
        )

    ep_df = load_ema_pullback_ledger()
    if ep_df.empty:
        st.info("No EMA Pullback Reentry episodes recorded yet — run the dashboard at least once with data loaded.")
    else:
        ep_df["crossover_date"] = pd.to_datetime(ep_df["crossover_date"])
        ep_df["touch_date"]     = pd.to_datetime(ep_df["touch_date"])
        ep_df["y_fix_date"]     = pd.to_datetime(ep_df["y_fix_date"])
        price_lookup_ep = merged.set_index("Symbol")["Price"].to_dict()
        ep_df["current_price"] = ep_df["symbol"].map(price_lookup_ep)
        ep_df["%_from_x"] = (ep_df["current_price"] - ep_df["x_price"]) / ep_df["x_price"] * 100
        ep_df["%_from_y"] = (ep_df["current_price"] - ep_df["y_price"]) / ep_df["y_price"] * 100

        EP_STATUS_LABELS = {
            "signal_pending": "🔵 Signal pending",
            "naked":   "⚪ Naked",
            "tested":  "🟢 Tested",
            "failed":  "🔴 Failed",
        }
        ep_df["status_label"] = ep_df["status"].map(EP_STATUS_LABELS).fillna(ep_df["status"])

        EP_STATUS_OPTIONS = ["All", "🔵 Signal pending", "⚪ Naked", "🟢 Tested", "🔴 Failed"]

        epcol1, epcol2, epcol3 = st.columns(3)
        ep_status_filter = epcol1.selectbox("Status", EP_STATUS_OPTIONS, index=0, key="ep_status")
        ep_symbol_filter = epcol2.text_input("Filter by symbol", "", key="ep_symbol").upper().strip()
        ep_band = epcol3.slider("Show only within % of current price (vs Y)", 1, 50, 15, key="ep_band")

        epview = ep_df.dropna(subset=["current_price"]).copy()
        if ep_status_filter != "All":
            epview = epview[epview["status_label"] == ep_status_filter]
        if ep_symbol_filter:
            epview = epview[epview["symbol"].str.contains(ep_symbol_filter)]
        epview = epview[epview["%_from_y"].abs() <= ep_band]
        epview = epview.sort_values("%_from_y", key=lambda s: s.abs()).reset_index(drop=True)

        st.caption(f"{len(epview)} episode(s) match your filters (out of {len(ep_df)} recorded total).")

        ep_display = epview[
            [
                "symbol", "crossover_date", "touch_date", "y_fix_date", "status_label",
                "x_price", "y_price", "current_price",
                "%_from_x", "%_from_y",
                "max_runup_pct", "days_tracked",
                "post_event_drawdown_pct", "post_event_days_to_recover",
            ]
        ].rename(columns={
            "symbol":         "Symbol",
            "crossover_date": "20/50 Crossover",
            "touch_date":     "50 EMA Touch (X locked)",
            "y_fix_date":     "Signal Date (Y locked)",
            "status_label":   "Status",
            "x_price":        "X (Highest High)",
            "y_price":        "Y (Buy Level)",
            "current_price":  "Current Price",
            "%_from_x":       "% From X",
            "%_from_y":       "% From Y",
            "max_runup_pct":  "Max Run-up",
            "days_tracked":   "Days Tracked",
            "post_event_drawdown_pct":    "Post-Event Drawdown %",
            "post_event_days_to_recover": "Post-Event Recovery Days",
        })

        EP_STATUS_COLORS = {
            "🔵 Signal pending": "background-color:#dbeafe; color:#1e3a8a; font-weight:600",
            "⚪ Naked":          "background-color:#eeeeee; color:#666; font-weight:600",
            "🟢 Tested":         "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
            "🔴 Failed":         "background-color:#fadbd8; color:#943126; font-weight:600",
        }

        ep_styled = (
            ep_display.style
            .map(
                lambda v: "" if pd.isna(v) else ("color:#1a7f37;font-weight:600" if v >= 0 else "color:#b91c1c;font-weight:600"),
                subset=["% From X", "% From Y"],
            )
            .map(lambda v: EP_STATUS_COLORS.get(v, ""), subset=["Status"])
            .format(
                {
                    "20/50 Crossover":        lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
                    "50 EMA Touch (X locked)":lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
                    "Signal Date (Y locked)": lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
                    "X (Highest High)":  "₹{:.2f}",
                    "Y (Buy Level)":     "₹{:.2f}",
                    "Current Price":     "₹{:.2f}",
                    "% From X":          "{:+.2f}%",
                    "% From Y":          "{:+.2f}%",
                    "Max Run-up":        "{:+.1f}%",
                    "Days Tracked":      "{:.0f}",
                    "Post-Event Drawdown %":    lambda v: "—" if pd.isna(v) else f"{v:.1f}%",
                    "Post-Event Recovery Days": lambda v: "—" if pd.isna(v) else f"{int(v)}",
                },
                na_rep="—",
            )
        )
        ep_event = st.dataframe(
            ep_styled, use_container_width=True, height=400, hide_index=True,
            on_select="rerun", selection_mode="multi-row", key="ep_table",
        )

        # ---- EMA Pullback selection handling ----
        ep_selected_rows = []
        if ep_event and ep_event.selection and ep_event.selection.get("rows"):
            ep_selected_rows = ep_event.selection["rows"]

        MAX_COMPARE = 6
        if len(ep_selected_rows) > MAX_COMPARE:
            st.caption(
                f"⚠️ You selected {len(ep_selected_rows)} rows — only the first "
                f"{MAX_COMPARE} are shown below. Uncheck a few to compare a different set."
            )
            ep_selected_rows = ep_selected_rows[:MAX_COMPARE]

        if len(ep_selected_rows) == 1:
            sel_idx = ep_selected_rows[0]
            ep = epview.iloc[sel_idx]
            ep_selected_sym = ep["symbol"]
            st.markdown(f"#### 📊 EMA Pullback Reentry Chart — {ep_selected_sym}")
            mc1, mc2, mc3, mc4 = st.columns(4)
            cur_price = ep.get("current_price", float("nan"))
            mc1.metric("Current Price", f"₹{cur_price:.2f}" if pd.notna(cur_price) else "—")
            mc2.metric("X (Highest High)", f"₹{ep['x_price']:.2f}" if pd.notna(ep.get('x_price')) else "—",
                       f"{ep['%_from_x']:+.2f}%" if pd.notna(ep.get('%_from_x')) else None)
            mc3.metric("Y (Buy Level)", f"₹{ep['y_price']:.2f}" if pd.notna(ep.get('y_price')) else "—",
                       f"{ep['%_from_y']:+.2f}%" if pd.notna(ep.get('%_from_y')) else None)
            mc4.metric("Status", ep.get("status_label", "—"))
            cap_parts = [f"20/50 crossover **{pd.to_datetime(ep['crossover_date']).strftime('%d-%b-%Y')}**"]
            if pd.notna(ep.get("touch_date")):
                cap_parts.append(f"50 EMA touched **{pd.to_datetime(ep['touch_date']).strftime('%d-%b-%Y')}** (X locked)")
            if pd.notna(ep.get("y_fix_date")):
                cap_parts.append(f"Signal fired **{pd.to_datetime(ep['y_fix_date']).strftime('%d-%b-%Y')}** (Y locked)")
            st.caption(" · ".join(cap_parts))
            ep_period = st.radio("Chart period", ["6mo", "1y", "2y", "5y", "10y", "max"],
                                 horizontal=True, index=2, key="ep_period")
            with st.spinner(f"Loading chart for {ep_selected_sym}…"):
                ep_hist = fetch_full_history_with_indicators(ep_selected_sym)
            if not ep_hist.empty:
                ep_fig = build_ema_pullback_chart(ep_hist, ep_selected_sym, ep, ep_period)
                st.plotly_chart(ep_fig, use_container_width=True, key=f"ep_chart_{ep_selected_sym}")
                ep_h = _trim_hist(ep_hist, ep_period)
                vfig = go.Figure(go.Bar(x=ep_h.index, y=ep_h["Volume"], name="Volume",
                                        marker_color="#6c757d"))
                vfig.update_layout(height=160, margin=dict(l=70, r=20, t=6, b=10),
                                   yaxis=dict(tickformat=",.0f", automargin=True))
                st.plotly_chart(vfig, use_container_width=True, key=f"ep_vol_{ep_selected_sym}")
        elif len(ep_selected_rows) >= 2:
            st.markdown(f"#### 📊 Comparing {len(ep_selected_rows)} selected EMA Pullback episode(s)")
            ecc1, ecc2 = st.columns([3, 1])
            ep_compare_layout = ecc1.radio(
                "Layout", ["1", "2", "4", "6"], horizontal=True, index=1, key="ep_compare_layout"
            )
            ep_compare_period = ecc2.radio(
                "Chart period", ["6mo", "1y", "2y", "5y"], horizontal=True, index=1, key="ep_compare_period"
            )
            ep_selected_eps = [(epview.iloc[i]["symbol"], epview.iloc[i]) for i in ep_selected_rows]
            render_strategy_multi_chart_grid(
                ep_selected_eps, build_ema_pullback_chart, ep_compare_layout, ep_compare_period,
                key_prefix="ep_multi",
                caption_fn=lambda sym, ep: (
                    f"**{sym}**\n\n"
                    f"Crossover {pd.to_datetime(ep['crossover_date']).strftime('%d-%b-%Y')} · "
                    f"{ep.get('status_label', '')}"
                ),
            )
        else:
            st.info(
                "👆 Click any row above to see the EMA Pullback Reentry chart for that episode, "
                "or check 2–6 rows to compare them side by side."
            )
    # --------------------------------------------------------------------------
    # SUPERTREND 3-PHASE PATTERN SCANNER
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("📊 Supertrend 3-Phase Pattern")
    st.caption(
        "Supertrend (ATR-based) + 200 EMA three-phase structure. "
        "**Phase 1** (ST buy; green line ≥ 200 EMA when SELL triggers): highest High = **X**. "
        "**Phase 2** (ST sell; must touch/cross below 200 EMA at least once): lowest Low = **Y**, lowest 200 EMA value = **Z**. "
        "**Phase 3** (ST flips buy → shown once its line crosses ≥ 200 EMA, watching for it to also clear X): all three levels activate. "
        "Pullback to any of the three is a buy entry. Multiple ST variants run in parallel."
    )
    with st.expander("ℹ️ Status meanings and three-level tracking"):
        st.markdown("""
    - **Phase 2, and the start of Phase 3, are not shown** — a bear phase that hasn't yet flipped back
      to BUY isn't actionable, and neither is a fresh BUY flip whose ST line hasn't crossed the 200 EMA
      yet: that first crossing can take an unpredictable amount of time to happen (or may never happen
      before the next SELL flip), so a row for it would mostly just sit there "watching and waiting"
      with no bounded outcome. Both are silently skipped rather than shown with provisional data.
    - **🟠 ST crossed 200 EMA – awaiting X clear** — Phase 2 already passed its mandatory condition
      (red line touched/crossed the 200 EMA), ST flipped buy, AND its line has now also crossed **≥ 200
      EMA** — the open-ended part of the wait is over. X, Y, Z are already confirmed and live; the only
      thing left is a bounded, watchable wait for the ST line to also clear **X**.
    - **🟢 Complete** — ST line has been both ≥ 200 EMA and > X (not necessarily on the same day — the
      "Signal Date" column shows when the EMA was crossed, "X Cleared" shows when X separately cleared).
      All three levels (X, Y, Z) are active buy targets, tracked for retest from whichever of those two
      dates came later — the point all three genuinely became live together.
      - Each level individually tracked: **⚪ Naked** / **🟢 Tested** / **🔴 Failed**
      - Per level: **max run-up %** before it was tested/failed (or "naked" run-up so far), **days
        tracked**, and — once tested or failed — **post-event drawdown %** and **days to recover**,
        the same stat set used across every other pattern in this dashboard.
    - **Supertrend variant** — the (period, multiplier) shown in each row. You can enable multiple
      variants in the sidebar to see all of them simultaneously in one table.
    - **Z (Lowest 200 EMA)** sits between X (Phase 1 high) and Y (Phase 2 price low) — it is the
      200 EMA's own floor during the correction, and often the most reliable near-term support on the
      first bounce, since the EMA acts as a dynamic magnet for pullbacks after reclaiming it.
        """)

    st_df_ledger = load_supertrend_ledger()
    if st_df_ledger.empty:
        st.info("No Supertrend episodes recorded yet — run the dashboard at least once with the Supertrend variants enabled in the sidebar.")
    else:
        for col in ["phase1_start", "signal_date", "x_cleared_date"]:
            if col in st_df_ledger.columns:
                st_df_ledger[col] = pd.to_datetime(st_df_ledger[col])

        price_lookup_st = merged.set_index("Symbol")["Price"].to_dict()
        st_df_ledger["current_price"] = st_df_ledger["symbol"].map(price_lookup_st)
        st_df_ledger["%_from_x"] = (st_df_ledger["current_price"] - st_df_ledger["x_price"]) / st_df_ledger["x_price"] * 100
        st_df_ledger["%_from_y"] = (st_df_ledger["current_price"] - st_df_ledger["y_price"]) / st_df_ledger["y_price"] * 100
        st_df_ledger["%_from_z"] = (st_df_ledger["current_price"] - st_df_ledger["z_price"]) / st_df_ledger["z_price"] * 100

        # --- Drawdown / recovery display, per level -----------------------
        # x_drawdown_pct / x_recovery_days etc. are already computed by the
        # detector (_post_event_drawdown) for every level that's been tested or
        # failed -- how far price kept falling AFTER the retest/failure event,
        # and whether/when it closed back at or above the level. That data just
        # wasn't surfaced in the UI before. Two things matter for a decision:
        #   1. How deep did it go (max_drawdown_pct) -- a shallow undershoot
        #      reads very differently from a level that got blown through.
        #   2. Is it CURRENTLY still below the level, or has it snapped back?
        #      recovery_days tells you the FIRST time it closed back above, but
        #      price could have relapsed below again since then -- so "currently
        #      underwater" is checked live against %_from_<level> (< 0 = below
        #      right now), not just inferred from whether it recovered once.
        for lvl in ("x", "y", "z"):
            status_col   = f"{lvl}_status"
            pct_col      = f"%_from_{lvl}"
            was_tested   = st_df_ledger[status_col].isin(["tested", "failed"])
            underwater_now = was_tested & (st_df_ledger[pct_col] < 0)
            st_df_ledger[f"{lvl}_underwater"] = underwater_now

            def _fmt_dd(row, lvl=lvl):
                status_v = row[f"{lvl}_status"]
                dd_v = row[f"{lvl}_drawdown_pct"]
                rec_v = row[f"{lvl}_recovery_days"]
                below_now = row[f"{lvl}_underwater"]
                if status_v not in ("tested", "failed") or pd.isna(dd_v):
                    return "—"
                if dd_v == 0.0:
                    return "held (no breach)"
                state = "⚠️ still below" if below_now else (f"recovered {int(rec_v)}d" if pd.notna(rec_v) else "recovered")
                return f"-{dd_v:.1f}% · {state}"

            st_df_ledger[f"{lvl}_dd_display"] = st_df_ledger.apply(_fmt_dd, axis=1)

        st_df_ledger["any_underwater"] = st_df_ledger[["x_underwater", "y_underwater", "z_underwater"]].any(axis=1)

        ST_EP_STATUS_LABELS = {
            "phase3_pending": "🟠 ST crossed 200 EMA – awaiting X clear",
            "complete":       "🟢 Complete",
        }
        LEVEL_STATUS_ICONS = {"naked": "⚪", "tested": "🟢", "failed": "🔴"}
        st_df_ledger["status_label"] = st_df_ledger["status"].map(ST_EP_STATUS_LABELS).fillna(st_df_ledger["status"])
        st_df_ledger["variant"] = st_df_ledger.apply(
            lambda r: f"({int(r['st_period'])}, {float(r['st_multiplier']):.1f})", axis=1
        )
        st_df_ledger["x_icon"] = st_df_ledger["x_status"].map(LEVEL_STATUS_ICONS).fillna("—")
        st_df_ledger["y_icon"] = st_df_ledger["y_status"].map(LEVEL_STATUS_ICONS).fillna("—")
        st_df_ledger["z_icon"] = st_df_ledger["z_status"].map(LEVEL_STATUS_ICONS).fillna("—")

        ST_STATUS_OPTIONS = [
            "All", "🟠 ST crossed 200 EMA – awaiting X clear", "🟢 Complete",
        ]

        st.caption(
            "Different ST variants (e.g. (7,3) and (10,3)) often flip on the exact same day for the "
            "same stock, since X/Y/Z come from price and 200 EMA — not from the ST math itself. "
            "When that happens they're the same underlying signal wearing two labels."
        )
        st_merge_variants = st.checkbox(
            "Merge duplicate signals across ST variants (recommended)", value=True, key="st_merge_variants",
            help="Groups rows by (symbol, Phase 1 Start) — the anchor day the flip happened. Since X, Y, Z "
                 "are derived purely from price High/Low and the 200 EMA (not from the ST period/multiplier), "
                 "two variants sharing the same Phase 1 Start day always produce the same X/Y/Z. Uncheck to "
                 "see every variant's row separately, e.g. to compare whether a faster variant (lower period) "
                 "reached 'Complete' sooner than a slower one."
        )

        if st_merge_variants:
            # Rebuilt without groupby(...).apply(...): different pandas versions
            # disagree on whether the group-key columns end up inside the
            # returned Series or get pulled out into the index, which made
            # reset_index() either restore them cleanly or collide with columns
            # that were already there ("cannot insert phase1_start, already
            # exists"). transform() sidesteps that entirely -- it always returns
            # a same-shaped Series aligned back to the original rows, so there's
            # no group-key ambiguity to resolve afterward.
            g = st_df_ledger

            def _sort_variant_str(v: str) -> tuple:
                return tuple(float(x) for x in v.strip("()").split(", "))

            # Merged "variant" label + count, broadcast to every row in the group.
            variant_join = g.groupby(["symbol", "phase1_start"])["variant"].transform(
                lambda s: " · ".join(sorted(s.unique().tolist(), key=_sort_variant_str))
            )
            variant_count = g.groupby(["symbol", "phase1_start"])["variant"].transform("nunique")

            # Representative row per group = furthest status (complete > pending),
            # then earliest completion/signal trigger among ties.
            status_rank = (g["status"] == "complete").astype(int)
            sort_date = g["x_cleared_date"].fillna(g["signal_date"])
            rank_df = pd.DataFrame({
                "symbol": g["symbol"], "phase1_start": g["phase1_start"],
                "_status_rank": status_rank, "_sort_date": sort_date,
            })
            rank_df = rank_df.sort_values(
                ["symbol", "phase1_start", "_status_rank", "_sort_date"],
                ascending=[True, True, False, True],
            )
            keep_idx = rank_df.drop_duplicates(subset=["symbol", "phase1_start"], keep="first").index

            st_df_ledger = g.loc[keep_idx].copy()
            st_df_ledger["variant"] = variant_join.loc[keep_idx]
            st_df_ledger["variant_count"] = variant_count.loc[keep_idx]
            st_df_ledger = st_df_ledger.reset_index(drop=True)
        else:
            st_df_ledger["variant_count"] = 1

        stcol1, stcol2, stcol3, stcol4 = st.columns(4)
        st_status_filter  = stcol1.selectbox("Status", ST_STATUS_OPTIONS, index=0, key="st_status")
        st_symbol_filter  = stcol2.text_input("Filter by symbol", "", key="st_symbol").upper().strip()
        st_variant_filter = stcol3.selectbox("Variant", ["All"] + sorted(st_df_ledger["variant"].unique().tolist()), key="st_variant")
        st_band           = stcol4.slider("Within % of current price (any of X/Y/Z)", 1, 50, 20, key="st_band")
        st_underwater_only = st.checkbox(
            "⚠️ Only show rows where a tested/failed level is CURRENTLY still below its reference price "
            "(not yet recovered)", value=False, key="st_underwater_only",
            help="A level being 'tested' just means price came back near it — this filters down to cases "
                 "where the level broke and price is still trading below it right now, i.e. the risk hasn't "
                 "resolved either way yet."
        )

        stview = st_df_ledger.dropna(subset=["current_price"]).copy()
        if st_status_filter != "All":
            stview = stview[stview["status_label"] == st_status_filter]
        if st_symbol_filter:
            stview = stview[stview["symbol"].str.contains(st_symbol_filter)]
        if st_variant_filter != "All":
            if st_merge_variants:
                # Merged rows show combined variant strings like "(7,3) · (10,3)" — match
                # any merged row that INCLUDES the selected single variant, not an exact string match.
                stview = stview[stview["variant"].apply(lambda v: st_variant_filter in v.split(" · "))]
            else:
                stview = stview[stview["variant"] == st_variant_filter]
        if st_underwater_only:
            stview = stview[stview["any_underwater"]]
        stview = stview[
            (stview["%_from_x"].abs() <= st_band) |
            (stview["%_from_y"].abs() <= st_band) |
            (stview["%_from_z"].abs() <= st_band)
        ]
        stview["_closest"] = stview[["%_from_x", "%_from_y", "%_from_z"]].abs().min(axis=1)
        stview = stview.sort_values("_closest").reset_index(drop=True)

        st.caption(f"{len(stview)} episode(s) match your filters (out of {len(st_df_ledger)} recorded total"
                   f"{' after merging duplicate variants' if st_merge_variants else ''}).")

        st_display = stview[[
            "symbol", "variant", "variant_count", "phase1_start", "signal_date", "x_cleared_date", "status_label",
            "x_price", "y_price", "z_price", "current_price",
            "%_from_x", "%_from_y", "%_from_z",
            "x_icon", "y_icon", "z_icon",
            "x_max_runup_pct", "y_max_runup_pct", "z_max_runup_pct",
            "x_dd_display", "y_dd_display", "z_dd_display",
        ]].rename(columns={
            "symbol":         "Symbol",
            "variant":        "ST Variant(s)",
            "variant_count":  "# Variants",
            "phase1_start":   "Phase 1 Start",
            "signal_date":    "Signal Date",
            "x_cleared_date": "X Cleared",
            "status_label":   "Status",
            "x_price":        "X (P1 High)",
            "y_price":        "Y (P2 Low)",
            "z_price":        "Z (Low 200EMA)",
            "current_price":  "Current Price",
            "%_from_x":       "% From X",
            "%_from_y":       "% From Y",
            "%_from_z":       "% From Z",
            "x_icon":         "X Status",
            "y_icon":         "Y Status",
            "z_icon":         "Z Status",
            "x_max_runup_pct": "X Max Run-up %",
            "y_max_runup_pct": "Y Max Run-up %",
            "z_max_runup_pct": "Z Max Run-up %",
            "x_dd_display":    "X Post-Test Drawdown",
            "y_dd_display":    "Y Post-Test Drawdown",
            "z_dd_display":    "Z Post-Test Drawdown",
        })

        ST_STATUS_COLORS = {
            "🟠 ST crossed 200 EMA – awaiting X clear": "background-color:#fdebd0; color:#8a5a00; font-weight:600",
            "🟢 Complete":                           "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
        }

        st_styled = (
            st_display.style
            .map(
                lambda v: "" if pd.isna(v) else ("color:#1a7f37;font-weight:600" if v >= 0 else "color:#b91c1c;font-weight:600"),
                subset=["% From X", "% From Y", "% From Z"],
            )
            .map(lambda v: ST_STATUS_COLORS.get(v, ""), subset=["Status"])
            .map(
                lambda v: "color:#b91c1c;font-weight:600" if isinstance(v, str) and "still below" in v
                else ("color:#1a7f37;font-weight:600" if isinstance(v, str) and ("recovered" in v or "held" in v) else ""),
                subset=["X Post-Test Drawdown", "Y Post-Test Drawdown", "Z Post-Test Drawdown"],
            )
            .format({
                "Phase 1 Start":  lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
                "Signal Date":    lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
                "X Cleared":      lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
                "X (P1 High)":    "₹{:.2f}",
                "Y (P2 Low)":     "₹{:.2f}",
                "Z (Low 200EMA)": "₹{:.2f}",
                "Current Price":  "₹{:.2f}",
                "% From X":       "{:+.2f}%",
                "% From Y":       "{:+.2f}%",
                "% From Z":       "{:+.2f}%",
                "X Max Run-up %": "{:.1f}%",
                "Y Max Run-up %": "{:.1f}%",
                "Z Max Run-up %": "{:.1f}%",
            }, na_rep="—")
        )

        st_event = st.dataframe(
            st_styled, use_container_width=True, height=420, hide_index=True,
            on_select="rerun", selection_mode="multi-row", key="st_table",
        )

        # ---- Supertrend selection handling ----
        st_selected_rows = []
        if st_event and st_event.selection and st_event.selection.get("rows"):
            st_selected_rows = st_event.selection["rows"]

        MAX_COMPARE = 6
        if len(st_selected_rows) > MAX_COMPARE:
            st.caption(
                f"⚠️ You selected {len(st_selected_rows)} rows — only the first "
                f"{MAX_COMPARE} are shown below. Uncheck a few to compare a different set."
            )
            st_selected_rows = st_selected_rows[:MAX_COMPARE]

        if len(st_selected_rows) == 1:
            sel_idx = st_selected_rows[0]
            ep = stview.iloc[sel_idx]
            st_selected_sym = ep["symbol"]
            ep_period_val = int(ep.get("st_period", 7))
            ep_mult_val = float(ep.get("st_multiplier", 3.0))
            st.markdown(f"#### 📊 Supertrend ({ep_period_val}, {ep_mult_val}) Chart — {st_selected_sym}")
            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            cur_price = ep.get("current_price", float("nan"))
            mc1.metric("Current Price", f"₹{cur_price:.2f}" if pd.notna(cur_price) else "—")
            mc2.metric("X (Phase 1 High)", f"₹{ep['x_price']:.2f}" if pd.notna(ep.get('x_price')) else "—",
                       f"{ep['%_from_x']:+.2f}%" if pd.notna(ep.get('%_from_x')) else None)
            mc3.metric("Y (Phase 2 Low)", f"₹{ep['y_price']:.2f}" if pd.notna(ep.get('y_price')) else "—",
                       f"{ep['%_from_y']:+.2f}%" if pd.notna(ep.get('%_from_y')) else None)
            mc4.metric("Z (Lowest 200 EMA)", f"₹{ep['z_price']:.2f}" if pd.notna(ep.get('z_price')) else "—",
                       f"{ep['%_from_z']:+.2f}%" if pd.notna(ep.get('%_from_z')) else None)
            mc5.metric("Status", ep.get("status_label", "—"))
            mc6, mc7, mc8 = st.columns(3)
            mc6.metric("X Level", f"{ep.get('x_icon','—')} {ep.get('x_status','—')}")
            mc7.metric("Y Level", f"{ep.get('y_icon','—')} {ep.get('y_status','—')}")
            mc8.metric("Z Level", f"{ep.get('z_icon','—')} {ep.get('z_status','—')}")

            def _runup_delta(days_val):
                return f"{int(days_val)}d tracked" if pd.notna(days_val) else None

            mc9, mc10, mc11 = st.columns(3)
            mc9.metric("X Max Run-up (before retest)",
                        f"{ep['x_max_runup_pct']:.1f}%" if pd.notna(ep.get('x_max_runup_pct')) else "—",
                        _runup_delta(ep.get('x_days_tracked')))
            mc10.metric("Y Max Run-up (before retest)",
                         f"{ep['y_max_runup_pct']:.1f}%" if pd.notna(ep.get('y_max_runup_pct')) else "—",
                         _runup_delta(ep.get('y_days_tracked')))
            mc11.metric("Z Max Run-up (before retest)",
                         f"{ep['z_max_runup_pct']:.1f}%" if pd.notna(ep.get('z_max_runup_pct')) else "—",
                         _runup_delta(ep.get('z_days_tracked')))

            dd_parts = []
            for lvl, label in (("x", "X"), ("y", "Y"), ("z", "Z")):
                status_v = ep.get(f"{lvl}_status")
                dd_v = ep.get(f"{lvl}_drawdown_pct")
                rec_v = ep.get(f"{lvl}_recovery_days")
                if status_v in ("tested", "failed") and pd.notna(dd_v):
                    rec_txt = f"{int(rec_v)}d to recover" if pd.notna(rec_v) else "not yet recovered"
                    dd_parts.append(f"**{label}** post-event drawdown **{dd_v:.1f}%**, {rec_txt}")
            if dd_parts:
                st.caption("Post-event drawdown/recovery: " + " · ".join(dd_parts))

            cap_parts = [f"Phase 1 started **{pd.to_datetime(ep['phase1_start']).strftime('%d-%b-%Y')}**"]
            if pd.notna(ep.get("signal_date")):
                cap_parts.append(f"Signal **{pd.to_datetime(ep['signal_date']).strftime('%d-%b-%Y')}**")
            if pd.notna(ep.get("x_cleared_date")):
                cap_parts.append(f"X cleared **{pd.to_datetime(ep['x_cleared_date']).strftime('%d-%b-%Y')}**")
            st.caption(" · ".join(cap_parts))
            st_period_sel = st.radio("Chart period", ["6mo", "1y", "2y", "5y", "10y", "max"],
                                      horizontal=True, index=2, key="st_period_radio")
            with st.spinner(f"Loading chart for {st_selected_sym}…"):
                st_chart_hist = fetch_full_history_with_indicators(st_selected_sym)
            if not st_chart_hist.empty:
                st_fig = build_supertrend_chart(
                    st_chart_hist, st_selected_sym, ep, st_period_sel,
                    st_period=ep_period_val, st_multiplier=ep_mult_val,
                )
                st.plotly_chart(st_fig, use_container_width=True, key=f"st_chart_{st_selected_sym}")
                st_h = _trim_hist(st_chart_hist, st_period_sel)
                vfig = go.Figure(go.Bar(x=st_h.index, y=st_h["Volume"], name="Volume", marker_color="#6c757d"))
                vfig.update_layout(height=160, margin=dict(l=70, r=20, t=6, b=10),
                                   yaxis=dict(tickformat=",.0f", automargin=True))
                st.plotly_chart(vfig, use_container_width=True, key=f"st_vol_{st_selected_sym}")
        elif len(st_selected_rows) >= 2:
            st.markdown(f"#### 📊 Comparing {len(st_selected_rows)} selected Supertrend episode(s)")
            stcc1, stcc2 = st.columns([3, 1])
            st_compare_layout = stcc1.radio(
                "Layout", ["1", "2", "4", "6"], horizontal=True, index=1, key="st_compare_layout"
            )
            st_compare_period = stcc2.radio(
                "Chart period", ["6mo", "1y", "2y", "5y"], horizontal=True, index=1, key="st_compare_period"
            )
            st_selected_eps = [(stview.iloc[i]["symbol"], stview.iloc[i]) for i in st_selected_rows]

            def _st_caption(sym, ep):
                ep_period_val = int(ep.get("st_period", 7))
                ep_mult_val = float(ep.get("st_multiplier", 3.0))
                return (
                    f"**{sym}**\n\n"
                    f"({ep_period_val}, {ep_mult_val}) · "
                    f"P1 {pd.to_datetime(ep['phase1_start']).strftime('%d-%b-%Y')} · "
                    f"{ep.get('status_label', '')}"
                )

            render_strategy_multi_chart_grid(
                st_selected_eps, build_supertrend_chart, st_compare_layout, st_compare_period,
                key_prefix="st_multi",
                caption_fn=_st_caption,
                extra_kwargs_fn=lambda ep: {
                    "st_period": int(ep.get("st_period", 7)),
                    "st_multiplier": float(ep.get("st_multiplier", 3.0)),
                },
            )
        else:
            st.info(
                "👆 Click any row above to see the Supertrend 3-Phase chart for that episode, "
                "or check 2–6 rows to compare them side by side."
            )
    # --------------------------------------------------------------------------
    # CONFLUENCE MASTER SCORE — SIP / Lumpsum Ranking
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("🏆 Confluence Master Score — SIP / Lumpsum Ranking")
    st.caption(
        "Auto-computed every run. Aggregates signals from ALL seven strategies **plus** "
        "live fundamental data (ROE, P/E, earnings growth, D/E, operating margin) into a "
        "single ranked table. Combined Score = 60% Technical + 40% Fundamental. "
        "**Lumpsum NOW** = Combined ≥7.5 AND price within 5% of a buy level AND quality ≥ Average. "
        "Tax note: dividend yield is penalised (dividends taxed at 30% slab; LTCG only 12.5%)."
    )

    with st.expander("ℹ️ Scoring methodology (click to expand)"):
        st.markdown("""
    **Technical Score (60% weight) — max 100 pts normalised to 0–10**

    | Section | Max pts | What it measures |
    |---|---|---|
    | A — Strategy Confluence | 56 | Signals from all 7 strategies (streak/5-leg/pivot/S1 shift/BP/EMA pullback/Supertrend) |
    | B — Trend Health | 24 | Above 200 EMA, streak duration, proximity to EMA, momentum, pullback depth |
    | C — Proximity Urgency | 20 | How close is price to the nearest active buy level RIGHT NOW |

    **Fundamental Score (40% weight) — max 100 pts normalised to 0–10**

    | Factor | Max pts | What it measures |
    |---|---|---|
    | F1 — ROE | 25 | Return on Equity — single most important compounder metric |
    | F2 — P/E Ratio | 20 | Valuation discipline — P/E > 100 scores 0 |
    | F3 — Earnings Growth | 15 | YoY EPS growth |
    | F4 — Debt/Equity | 15 | Balance sheet safety |
    | F5 — Dividend Yield | −10 max | PENALTY: dividends taxed at 30% slab, not 12.5% LTCG |
    | F6 — Operating Margin | 10 | Pricing power / business quality |
    | F7 — Revenue Growth | 10 | Business momentum |
    | F8 — Current Ratio | 5 | Short-term liquidity |

    **Quality Gate**: even a top-scoring stock is downgraded from "Lumpsum NOW" to
    "Lumpsum on dip" if its fundamental Quality Tier is ❌ Weak or 🔶 Below Average.
    Fundamental data is cached weekly (re-fetched every 7 days from Yahoo Finance).
        """)

    with st.expander("ℹ️ How to act on the verdicts"):
        st.markdown("""
    - **🟢 Lumpsum NOW** — deploy 1–2 tranches immediately. Price is at/near a proven buy level,
      technicals are strong across multiple strategies, AND the business is fundamentally sound.
      Suggested stop-loss: 8% below the Closest Level.
    - **🟡 Lumpsum on dip** — strong setup but either price hasn't reached the level yet, or
      fundamentals are merely average. Set a limit buy order at the Closest Level shown.
    - **🔵 SIP monthly** — good structural quality but no immediate entry trigger. Add a
      fixed amount every month regardless of price. Best for core long-term holdings.
    - **⚪ Watchlist** — some signals but insufficient confluence. Monitor; don't deploy yet.
    - **⛔ Avoid / skip** — failed signals dominate or fundamentals are weak. Skip for now.
    - **Flags**: ST=Supertrend ✅/◷/⚠ | 200EMA=streak | 5-Leg | EP=EMA Pullback |
      BP=Breakout-Pullback | Piv=Monthly Pivot | S1Sh=S1 Shift Up
    - **⚠ Fund data warning**: shown when Yahoo Finance returned fewer than 3 key fundamental
      fields for that stock. The fundamental score is less reliable in those rows.
        """)

    # ── Sidebar controls ──────────────────────────────────────────────────────
    _conf_top_n = st.sidebar.number_input(
        "Max stocks in Master Score table", min_value=5, max_value=100, value=30, step=5,
        key="conf_top_n",
        help="Show only the top N ranked stocks in the Confluence Master Score table."
    )
    _fund_refresh_force = st.sidebar.checkbox(
        "Force-refresh fundamental data now",
        value=False, key="fund_force_refresh",
        help="Re-fetch ROE/P/E/growth data from Yahoo Finance for all symbols. "
             "Takes ~60–90 seconds. Normally refreshes automatically every 7 days."
    )

    # ── Fetch / refresh fundamentals ──────────────────────────────────────────
    _all_symbols = symbol_df["Symbol"].tolist()
    _symbols_key = ",".join(sorted(_all_symbols))

    if _fund_refresh_force:
        with st.spinner(f"Refreshing fundamental data for {len(_all_symbols)} symbols from Yahoo Finance… (this takes ~60–90 seconds)"):
            _fund_df_raw = fundamental_score.refresh_fundamentals(
                _all_symbols, force=True
            )
        load_fundamental_cache_cached.clear()
        st.sidebar.success("Fundamental data refreshed.")
    else:
        # Check if ANY symbol is missing from cache; if so, fetch missing ones silently
        _cached_fund = fundamental_score.get_cached_fundamentals(_all_symbols)
        _missing = [s for s in _all_symbols if s not in (_cached_fund.index.tolist() if not _cached_fund.empty else [])]
        if _missing:
            with st.spinner(f"Fetching fundamental data for {len(_missing)} new symbols…"):
                _fund_df_raw = fundamental_score.refresh_fundamentals(_all_symbols, force=False)
            load_fundamental_cache_cached.clear()
        else:
            _fund_df_raw = load_fundamental_cache_cached(_symbols_key)

    # Build fundamental scores for the universe
    _fund_scores = fundamental_score.build_fundamental_scores(_all_symbols, _fund_df_raw)

    # Show when fundamental data was last fetched
    if not _fund_df_raw.empty and "fetched_at" in _fund_df_raw.columns:
        _oldest = _fund_df_raw["fetched_at"].dropna().min()
        _newest = _fund_df_raw["fetched_at"].dropna().max()
        if _oldest:
            try:
                _oldest_dt = pd.to_datetime(_oldest)
                _newest_dt = pd.to_datetime(_newest)
                st.caption(
                    f"📊 Fundamental data: fetched {_oldest_dt.strftime('%d-%b-%Y')} – "
                    f"{_newest_dt.strftime('%d-%b-%Y')} · "
                    f"{len(_fund_df_raw)} symbols cached · refreshes automatically every 7 days"
                )
            except Exception:
                pass

    # ── Build the combined confluence table ───────────────────────────────────
    with st.spinner("Building Confluence Master Score…"):
        _streak_df_raw   = load_streak_ledger()
        _five_df_raw     = load_five_leg_ledger()
        _pivot_df_raw    = load_pivot_ledger()
        _s1_shift_df_raw = load_s1_shift_ledger()
        _bp_df_raw       = load_breakout_pullback_ledger()
        _ep_df_raw       = load_ema_pullback_ledger()
        _st_df_raw       = load_supertrend_ledger()
        conf_df = confluence_score.build_confluence_table(
            metrics_df=merged,
            streak_df=_streak_df_raw,
            five_df=_five_df_raw,
            pivot_df=_pivot_df_raw,
            s1_shift_df=_s1_shift_df_raw,
            bp_df=_bp_df_raw,
            ep_df=_ep_df_raw,
            st_df=_st_df_raw,
            top_n=int(_conf_top_n),
            fund_scores_df=_fund_scores if not _fund_scores.empty else None,
        )

    if conf_df.empty:
        st.info("Run the dashboard once (click Refresh Data) to populate the strategy ledgers — the Confluence Score needs at least one full scan to have data to aggregate.")
    else:
        # ── Filter controls ──────────────────────────────────────────────────
        conf_c1, conf_c2, conf_c3, conf_c4 = st.columns(4)
        conf_verdict_filter = conf_c1.selectbox(
            "Verdict", ["All", "🟢 Lumpsum NOW", "🟡 Lumpsum on dip", "🔵 SIP monthly", "⚪ Watchlist", "⛔ Avoid / skip"],
            key="conf_verdict",
        )
        conf_sym_filter = conf_c2.text_input("Symbol search", "", key="conf_sym").upper().strip()
        conf_min_strategies = conf_c3.slider(
            "Min strategies active", 0, 7, 2, key="conf_min_st",
            help="Show only stocks where at least N strategies have an active (non-failed) signal."
        )
        conf_quality_filter = conf_c4.selectbox(
            "Min quality tier",
            ["All", "⚠️  Average", "✅ Solid Business", "🏆 Quality Compounder"],
            key="conf_quality",
        )

        conf_view = conf_df.copy()
        if conf_verdict_filter != "All":
            conf_view = conf_view[conf_view["Verdict"] == conf_verdict_filter]
        if conf_sym_filter:
            conf_view = conf_view[conf_view["Symbol"].str.contains(conf_sym_filter)]
        conf_view = conf_view[conf_view["Strategies_Active"] >= conf_min_strategies]
        if conf_quality_filter != "All" and "Quality_Tier" in conf_view.columns:
            quality_order = ["❌ Weak", "🔶 Below Average", "⚠️  Average", "✅ Solid Business", "🏆 Quality Compounder"]
            min_qi = quality_order.index(conf_quality_filter) if conf_quality_filter in quality_order else 0
            conf_view = conf_view[
                conf_view["Quality_Tier"].apply(
                    lambda t: quality_order.index(t) >= min_qi if t in quality_order else False
                )
            ]

        st.caption(
            f"**{len(conf_view)} stocks** shown (ranked by Combined Score = 60% Technical + 40% Fundamental). "
            f"Computed: {datetime.now().strftime('%d-%b-%Y %H:%M')} · Universe: {len(conf_df)} stocks."
        )

        VERDICT_COLORS = {
            "🟢 Lumpsum NOW":    "background-color:#d1f2eb; color:#0b6b4f; font-weight:700",
            "🟡 Lumpsum on dip": "background-color:#fef9e7; color:#8a6d00; font-weight:700",
            "🔵 SIP monthly":    "background-color:#dbeafe; color:#1e3a8a; font-weight:700",
            "⚪ Watchlist":       "background-color:#f5f5f5; color:#555; font-weight:600",
            "⛔ Avoid / skip":    "background-color:#fadbd8; color:#943126; font-weight:600",
        }
        QUALITY_COLORS = {
            "🏆 Quality Compounder": "background-color:#d1f2eb; color:#0b6b4f; font-weight:700",
            "✅ Solid Business":     "background-color:#eafaf1; color:#1a7f37; font-weight:600",
            "⚠️  Average":            "background-color:#fef9e7; color:#8a6d00",
            "🔶 Below Average":      "background-color:#fde8d0; color:#7b4a00",
            "❌ Weak":               "background-color:#fadbd8; color:#943126; font-weight:600",
            "❓ No data":            "background-color:#f0f0f0; color:#888",
        }

        # ── Tab 1: Combined view  /  Tab 2: Fundamental detail ───────────────
        tab_combined, tab_fund, tab_technical = st.tabs([
            "📊 Combined Ranking", "🔬 Fundamental Detail", "📈 Technical Breakdown"
        ])

        with tab_combined:
            combined_cols = [
                "Symbol", "Price", "Combined_Score", "Technical_Score",
                "Fundamental_Score", "Quality_Tier", "Verdict",
                "Strategies_Active", "Closest_Level", "Closest_Pct",
                "Above200EMA", "%From200EMA", "Flags",
            ]
            cview = conf_view[[c for c in combined_cols if c in conf_view.columns]].copy()
            cview.index = range(1, len(cview) + 1)
            cview.index.name = "Rank"

            num_cols_combined = [c for c in ["Combined_Score", "Technical_Score", "Fundamental_Score",
                                              "Closest_Pct", "%From200EMA"] if c in cview.columns]
            # Pre-format scores as strings to avoid Streamlit's .000000 precision bug
            cview["Combined_Score"]    = cview["Combined_Score"].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
            cview["Technical_Score"]   = cview["Technical_Score"].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
            cview["Fundamental_Score"] = cview["Fundamental_Score"].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "⚠ No data")
            def _fmt_closest_pct(v):
                if pd.isna(v):
                    return "—"
                if v < 0:
                    # Price is BELOW the nearest level — that level is now overhead
                    # resistance, not support. Flag it so users don't mistake a
                    # negative distance for an imminent buy opportunity.
                    return f"{v:+.2f}% ⚠ above"
                return f"{v:+.2f}%"
            cview["Closest_Pct"] = cview["Closest_Pct"].apply(_fmt_closest_pct)
            cview["%From200EMA"]       = cview["%From200EMA"].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "—")
            cview["Closest_Level"]     = cview["Closest_Level"].apply(lambda v: f"₹{v:.2f}" if pd.notna(v) else "—")
            cview["Strategies_Active"] = cview["Strategies_Active"].apply(lambda v: f"{int(v)}/7" if pd.notna(v) else "—")
            fmt_combined = {
                "Price": "₹{:.2f}",
            }
            try:
                cview_styled = (
                    cview.style
                    .map(lambda v: VERDICT_COLORS.get(v, ""), subset=["Verdict"])
                    .map(lambda v: QUALITY_COLORS.get(v, ""), subset=["Quality_Tier"])
                    .map(
                        lambda v: "" if (v is None or not isinstance(v, str) or v in ("—", "⚠ No data"))
                        else ("color:#1a7f37;font-weight:600" if v.startswith("+") else
                              ("color:#b91c1c;font-weight:600" if v.startswith("-") else "")),
                        subset=[c for c in ["Closest_Pct", "%From200EMA"] if c in cview.columns],
                    )
                    .map(
                        lambda v: "color:#1a7f37;font-weight:700" if v is True else "color:#b91c1c",
                        subset=["Above200EMA"],
                    )
                    .format(fmt_combined, na_rep="—")
                )
                st.dataframe(cview_styled, use_container_width=True,
                             height=min(600, 45 + len(cview) * 36), hide_index=False)
            except Exception:
                st.dataframe(cview, use_container_width=True, hide_index=False)

        with tab_fund:
            st.caption(
                "Fundamental data sourced from Yahoo Finance · refreshed every 7 days. "
                "⚠ = fewer than 3 fields available (score less reliable). "
                "🏦 = Financial company — D/E column shows ROA instead (D/E is structurally "
                "high for banks/NBFCs and not a meaningful distress signal for them)."
            )
            # Build display-ready copy with pre-formatted strings to avoid Streamlit
            # float precision issues (e.g. 5.700000 instead of 5.7×)
            fund_cols_raw = [
                "Symbol", "Price", "Fundamental_Score", "Quality_Tier",
                "F_pe", "F_roe", "F_eg", "F_de_display",
                "F_div_yield", "F_op_margin", "F_rev_growth",
                "F_data_fields", "F_is_financial", "F_is_infra_utility",
            ]
            fview = conf_view[[c for c in fund_cols_raw if c in conf_view.columns]].copy()

            # Pre-format numeric columns as strings so Streamlit renders them cleanly
            def _fmt(val, fmt_str, suffix=""):
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    return "—"
                try:
                    return fmt_str.format(float(val)) + suffix
                except Exception:
                    return str(val)

            fview["P/E"]           = fview["F_pe"].apply(lambda v: _fmt(v, "{:.1f}") + "×" if (v is not None and pd.notna(v)) else "—")
            fview["ROE %"]         = fview["F_roe"].apply(lambda v: _fmt(v, "{:.1f}") + "%" if (v is not None and pd.notna(v)) else "—")
            fview["EPS Growth %"]  = fview["F_eg"].apply(lambda v: (("+" if float(v) >= 0 else "") + f"{float(v):.1f}%") if (v is not None and pd.notna(v)) else "—")
            fview["Rev Growth %"]  = fview["F_rev_growth"].apply(lambda v: (("+" if float(v) >= 0 else "") + f"{float(v):.1f}%") if (v is not None and pd.notna(v)) else "—")
            fview["Div Yield %"]   = fview["F_div_yield"].apply(lambda v: _fmt(v, "{:.2f}") + "%" if (v is not None and pd.notna(v)) else "—")
            fview["Op Margin %"]   = fview["F_op_margin"].apply(lambda v: _fmt(v, "{:.1f}") + "%" if (v is not None and pd.notna(v)) else "—")
            def _fmt_de_roa(r):
                disp = r.get("F_de_display")
                is_fin   = bool(r.get("F_is_financial"))
                is_infra = bool(r.get("F_is_infra_utility"))
                if is_fin:
                    # F_de_display holds ROA as a plain float (e.g. 2.78 means 2.78%),
                    # or None when ROA data is unavailable for this financial company.
                    if disp is None or (isinstance(disp, float) and pd.isna(disp)):
                        return "🏦 —"          # financial but no ROA data
                    try:
                        return f"🏦 ROA {float(disp):.2f}%"
                    except (TypeError, ValueError):
                        return "🏦 —"
                if is_infra:
                    return "⚡ Infra (neutral)"   # structured project debt — not penalised
                # Regular company: F_de_display holds the D/E ratio float, or None.
                if disp is None or (isinstance(disp, float) and pd.isna(disp)):
                    return "—"
                try:
                    return f"{float(disp):.2f}"
                except (TypeError, ValueError):
                    return "—"

            fview["D/E or ROA"] = fview.apply(_fmt_de_roa, axis=1)
            fview["Data Fields"]   = fview["F_data_fields"].apply(lambda v: f"{int(v)}/8" if pd.notna(v) else "—")
            fview["Fund Score"]    = fview["Fundamental_Score"].apply(lambda v: f"{float(v):.2f}" if pd.notna(v) else "⚠ No data")

            display_fview = fview[[
                "Symbol", "Price", "Fund Score", "Quality_Tier",
                "P/E", "ROE %", "EPS Growth %", "D/E or ROA",
                "Div Yield %", "Op Margin %", "Rev Growth %", "Data Fields",
            ]].copy()
            display_fview.index = range(1, len(display_fview) + 1)
            display_fview.index.name = "Rank"
            display_fview = display_fview.rename(columns={"Quality_Tier": "Quality"})

            try:
                fview_styled = (
                    display_fview.style
                    .map(lambda v: QUALITY_COLORS.get(v, ""), subset=["Quality"])
                    .format({"Price": "₹{:.2f}"}, na_rep="—")
                )
                st.dataframe(fview_styled, use_container_width=True,
                             height=min(600, 45 + len(display_fview) * 36), hide_index=False)
            except Exception:
                st.dataframe(display_fview, use_container_width=True, hide_index=False)

        with tab_technical:
            tech_cols = [
                "Symbol", "Price", "Technical_Score", "Strategies_Active",
                "Strategy_Score", "Trend_Score", "Proximity_Score",
                "Closest_Level", "Closest_Pct", "Above200EMA", "TrendDays",
                "%From200EMA", "%FromHigh", "Flags",
            ]
            tview = conf_view[[c for c in tech_cols if c in conf_view.columns]].copy()
            tview.index = range(1, len(tview) + 1)
            tview.index.name = "Rank"
            # Pre-format to avoid .000000 display issues
            tview["Technical_Score"]   = tview["Technical_Score"].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
            tview["Strategy_Score"]    = tview["Strategy_Score"].apply(lambda v: f"{int(v)}" if pd.notna(v) else "—")
            tview["Trend_Score"]       = tview["Trend_Score"].apply(lambda v: f"{int(v)}" if pd.notna(v) else "—")
            tview["Proximity_Score"]   = tview["Proximity_Score"].apply(lambda v: f"{int(v)}" if pd.notna(v) else "—")
            tview["Closest_Level"]     = tview["Closest_Level"].apply(lambda v: f"₹{v:.2f}" if pd.notna(v) else "—")
            tview["Closest_Pct"]       = tview["Closest_Pct"].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "—")
            tview["%From200EMA"]       = tview["%From200EMA"].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "—")
            tview["%FromHigh"]         = tview["%FromHigh"].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "—")
            tview["TrendDays"]         = tview["TrendDays"].apply(lambda v: f"{int(v)}d" if pd.notna(v) else "—")
            tview["Strategies_Active"] = tview["Strategies_Active"].apply(lambda v: f"{int(v)}/7" if pd.notna(v) else "—")
            fmt_tech = {"Price": "₹{:.2f}"}
            try:
                tview_styled = (
                    tview.style
                    .map(
                        lambda v: "" if (v is None or not isinstance(v, str) or v in ("—",))
                        else ("color:#1a7f37;font-weight:600" if v.startswith("+") else
                              ("color:#b91c1c;font-weight:600" if v.startswith("-") else "")),
                        subset=[c for c in ["Closest_Pct", "%From200EMA", "%FromHigh"] if c in tview.columns],
                    )
                    .map(
                        lambda v: "color:#1a7f37;font-weight:700" if v is True else "color:#b91c1c",
                        subset=["Above200EMA"],
                    )
                    .format(fmt_tech, na_rep="—")
                )
                st.dataframe(tview_styled, use_container_width=True,
                             height=min(600, 45 + len(tview) * 36), hide_index=False)
            except Exception:
                st.dataframe(tview, use_container_width=True, hide_index=False)

        # ── Export ───────────────────────────────────────────────────────────
        st.download_button(
            label="⬇️ Export Full Confluence Table (CSV)",
            data=conf_view.to_csv(index=True),
            file_name=f"confluence_{datetime.now().strftime('%Y-%m-%dT%H-%M')}.csv",
            mime="text/csv",
        )

        # ── Action callouts ──────────────────────────────────────────────────
        lumpsum_now = conf_view[conf_view["Verdict"] == "🟢 Lumpsum NOW"]
        if not lumpsum_now.empty:
            st.success(
                f"🟢 **{len(lumpsum_now)} stock(s) rated Lumpsum NOW:** "
                + " | ".join(
                    f"**{r['Symbol']}** "
                    f"({r['Closest_Pct']:+.1f}% from ₹{r['Closest_Level']:.0f}"
                    f" · {r.get('Quality_Tier','')}"
                    f")"
                    for _, r in lumpsum_now.iterrows()
                    if pd.notna(r.get("Closest_Pct")) and pd.notna(r.get("Closest_Level"))
                )
            )

        lumpsum_dip = conf_view[conf_view["Verdict"] == "🟡 Lumpsum on dip"]
        if not lumpsum_dip.empty:
            st.warning(
                f"🟡 **{len(lumpsum_dip)} stock(s) — Lumpsum on dip:** "
                + ", ".join(
                    f"**{r['Symbol']}** (₹{r['Closest_Level']:.0f})"
                    if pd.notna(r.get("Closest_Level")) else f"**{r['Symbol']}**"
                    for _, r in lumpsum_dip.iterrows()
                )
            )

        sip_candidates = conf_view[conf_view["Verdict"] == "🔵 SIP monthly"]
        if not sip_candidates.empty:
            st.info(
                f"🔵 **{len(sip_candidates)} stock(s) for SIP:** "
                + ", ".join(sip_candidates["Symbol"].tolist())
            )

    # --------------------------------------------------------------------------
    # CHART EXPLORER — pick any symbol + setup to inspect it independently
    # --------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("🔭 Chart Explorer")
    st.caption(
        "Already know the symbol? Pick it here alongside any of the four setups to "
        "see its dedicated chart with all setup-specific overlays — independent of "
        "which scanner table you're browsing above. Useful for cross-referencing "
        "multiple setups on the same stock side-by-side."
    )

    ex_col1, ex_col2, ex_col3 = st.columns([2, 2, 1])
    explorer_symbol = ex_col1.selectbox(
        "Symbol", options=sorted(symbol_df["Symbol"].tolist()), index=0, key="explorer_symbol"
    )
    explorer_setup = ex_col2.selectbox(
        "Setup to visualise",
        [
            "📈 EMA Streak (200 EMA retest level)",
            "🔀 5-Leg EMA Reversal",
            "📐 Monthly Pivot S1",
            "🔺 Monthly S1 Shift Up",
            "🔀 Breakout-Pullback 4-Leg",
            "📈 EMA Pullback Reentry",
            "📊 Supertrend 3-Phase",
        ],
        index=0,
        key="explorer_setup",
    )
    explorer_period = ex_col3.radio(
        "Period", ["6mo", "1y", "2y", "5y", "10y", "max"], horizontal=False, index=2, key="explorer_period"
    )

    with st.spinner(f"Loading {explorer_symbol}…"):
        ex_hist = fetch_full_history_with_indicators(explorer_symbol)

    ex_row = merged[merged["Symbol"] == explorer_symbol]
    ex_row_data = ex_row.iloc[0] if not ex_row.empty else None

    if not ex_hist.empty and ex_row_data is not None:
        if explorer_setup == "📈 EMA Streak (200 EMA retest level)":
            ex_fig = build_streak_chart(ex_hist, explorer_symbol, ex_row_data, explorer_period)
            st.plotly_chart(ex_fig, use_container_width=True, key="explorer_streak_fig")

        elif explorer_setup == "🔀 5-Leg EMA Reversal":
            # Pull the most-recent episode for this symbol from the ledger
            fl_all = load_five_leg_ledger()
            sym_eps = fl_all[fl_all["symbol"] == explorer_symbol] if not fl_all.empty else pd.DataFrame()
            if sym_eps.empty:
                st.info(f"No 5-Leg episodes detected for **{explorer_symbol}** yet.")
            else:
                # Show most recent episode by leg1_start; let user pick if there are multiple
                sym_eps = sym_eps.copy()
                sym_eps["leg1_start"] = pd.to_datetime(sym_eps["leg1_start"])
                sym_eps = sym_eps.sort_values("leg1_start", ascending=False).reset_index(drop=True)
                if len(sym_eps) > 1:
                    ep_labels = [
                        f"Leg1 {row['leg1_start'].strftime('%d-%b-%Y')} — {row['status']}"
                        for _, row in sym_eps.iterrows()
                    ]
                    ep_choice = st.selectbox("Episode", ep_labels, index=0, key="explorer_fl_ep")
                    ep_idx = ep_labels.index(ep_choice)
                else:
                    ep_idx = 0
                ex_ep = sym_eps.iloc[ep_idx]
                # Enrich with current price / pct_from for the metrics strip
                if ex_row_data is not None:
                    ex_ep = ex_ep.copy()
                    ex_ep["current_price"] = ex_row_data["Price"]
                    ex_ep["%_from_x"] = (ex_row_data["Price"] - ex_ep["x_price"]) / ex_ep["x_price"] * 100
                    ex_ep["%_from_y"] = (ex_row_data["Price"] - ex_ep["y_price"]) / ex_ep["y_price"] * 100

                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric("Current Price", f"₹{ex_row_data['Price']:.2f}")
                mc2.metric("X (Lowest 200 EMA)", f"₹{ex_ep['x_price']:.2f}", f"{ex_ep['%_from_x']:+.2f}%")
                mc3.metric("Y (Lowest Price)", f"₹{ex_ep['y_price']:.2f}", f"{ex_ep['%_from_y']:+.2f}%")
                mc4.metric("Legs", int(ex_ep["num_legs_observed"]))
                ex_fig = build_five_leg_chart(ex_hist, explorer_symbol, ex_ep, explorer_period,
                                              min_leg_days=int(min_leg_days))
                st.plotly_chart(ex_fig, use_container_width=True, key="explorer_fl_fig")

        elif explorer_setup == "📐 Monthly Pivot S1":
            pv_all = load_pivot_ledger()
            sym_eps = pv_all[pv_all["symbol"] == explorer_symbol] if not pv_all.empty else pd.DataFrame()
            if sym_eps.empty:
                st.info(f"No Monthly Pivot S1 episodes detected for **{explorer_symbol}** yet.")
            else:
                sym_eps = sym_eps.copy()
                sym_eps["episode_start"] = pd.to_datetime(sym_eps["episode_start"])
                sym_eps = sym_eps.sort_values("episode_start", ascending=False).reset_index(drop=True)
                if len(sym_eps) > 1:
                    ep_labels = [
                        f"Episode {row['episode_start'].strftime('%d-%b-%Y')} — {row['status']}"
                        for _, row in sym_eps.iterrows()
                    ]
                    ep_choice = st.selectbox("Episode", ep_labels, index=0, key="explorer_ps1_ep")
                    ep_idx = ep_labels.index(ep_choice)
                else:
                    ep_idx = 0
                ex_ep = sym_eps.iloc[ep_idx]
                if ex_row_data is not None:
                    ex_ep = ex_ep.copy()
                    ex_ep["current_price"] = ex_row_data["Price"]
                    ex_ep["%_from_x"] = (ex_row_data["Price"] - ex_ep["x_price"]) / ex_ep["x_price"] * 100 if pd.notna(ex_ep.get("x_price")) else float("nan")
                    ex_ep["%_from_y"] = (ex_row_data["Price"] - ex_ep["y_price"]) / ex_ep["y_price"] * 100 if pd.notna(ex_ep.get("y_price")) else float("nan")
                mc1, mc2, mc3 = st.columns(3)
                mc1.metric("X (Running High)", f"₹{ex_ep['x_price']:.2f}" if pd.notna(ex_ep.get("x_price")) else "—",
                           f"{ex_ep['%_from_x']:+.2f}%" if pd.notna(ex_ep.get("%_from_x")) else None)
                mc2.metric("Y (Running Low)", f"₹{ex_ep['y_price']:.2f}" if pd.notna(ex_ep.get("y_price")) else "—",
                           f"{ex_ep['%_from_y']:+.2f}%" if pd.notna(ex_ep.get("%_from_y")) else None)
                mc3.metric("Status", ex_ep.get("status", "—"))
                ex_fig = build_pivot_s1_chart(ex_hist, explorer_symbol, ex_ep, explorer_period)
                st.plotly_chart(ex_fig, use_container_width=True, key="explorer_ps1_fig")

        elif explorer_setup == "🔺 Monthly S1 Shift Up":
            ss_all = load_s1_shift_ledger()
            sym_eps = ss_all[ss_all["symbol"] == explorer_symbol] if not ss_all.empty else pd.DataFrame()
            if sym_eps.empty:
                st.info(f"No Monthly S1 Shift Up episodes detected for **{explorer_symbol}** yet.")
            else:
                sym_eps = sym_eps.copy()
                sym_eps = sym_eps.sort_values("month", ascending=False).reset_index(drop=True)
                if len(sym_eps) > 1:
                    ep_labels = [
                        f"Month {row['month']} — {row['status']}"
                        for _, row in sym_eps.iterrows()
                    ]
                    ep_choice = st.selectbox("Episode", ep_labels, index=0, key="explorer_ss_ep")
                    ep_idx = ep_labels.index(ep_choice)
                else:
                    ep_idx = 0
                ex_ep = sym_eps.iloc[ep_idx]
                if ex_row_data is not None:
                    ex_ep = ex_ep.copy()
                    ex_ep["current_price"] = ex_row_data["Price"]
                    ex_ep["%_from_x"] = (ex_row_data["Price"] - ex_ep["x_price"]) / ex_ep["x_price"] * 100 if pd.notna(ex_ep.get("x_price")) else float("nan")
                    S1_SHIFT_STATUS_LABELS = {"naked": "⚪ Naked", "tested": "🟢 Tested", "failed": "🔴 Failed"}
                    ex_ep["status_label"] = S1_SHIFT_STATUS_LABELS.get(ex_ep.get("status"), ex_ep.get("status", "—"))
                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric("Current Price", f"₹{ex_row_data['Price']:.2f}")
                mc2.metric("X (Month Low)", f"₹{ex_ep['x_price']:.2f}" if pd.notna(ex_ep.get("x_price")) else "—",
                           f"{ex_ep['%_from_x']:+.2f}%" if pd.notna(ex_ep.get("%_from_x")) else None)
                mc3.metric("S1(M)", f"₹{ex_ep['s1_month']:.2f}" if pd.notna(ep.get("s1_month")) else "—")
                mc4.metric("S1(M+1) ↑", f"₹{ex_ep['s1_next_month']:.2f}" if pd.notna(ep.get("s1_next_month")) else "—")
                ex_fig = build_s1_shift_chart(ex_hist, explorer_symbol, ex_ep, explorer_period)
                st.plotly_chart(ex_fig, use_container_width=True, key="explorer_ss_fig")

        elif explorer_setup == "🔀 Breakout-Pullback 4-Leg":
            bp_all = load_breakout_pullback_ledger()
            sym_eps = bp_all[bp_all["symbol"] == explorer_symbol] if not bp_all.empty else pd.DataFrame()
            if sym_eps.empty:
                st.info(f"No Breakout-Pullback episodes detected for **{explorer_symbol}** yet.")
            else:
                sym_eps = sym_eps.copy()
                sym_eps["leg1_start"] = pd.to_datetime(sym_eps["leg1_start"])
                sym_eps = sym_eps.sort_values("leg1_start", ascending=False).reset_index(drop=True)
                if len(sym_eps) > 1:
                    ep_labels = [
                        f"Leg1 {row['leg1_start'].strftime('%d-%b-%Y')} — {row['status']}"
                        for _, row in sym_eps.iterrows()
                    ]
                    ep_choice = st.selectbox("Episode", ep_labels, index=0, key="explorer_bp_ep")
                    ep_idx = ep_labels.index(ep_choice)
                else:
                    ep_idx = 0
                ex_ep = sym_eps.iloc[ep_idx]
                if ex_row_data is not None:
                    ex_ep = ex_ep.copy()
                    ex_ep["current_price"] = ex_row_data["Price"]
                    ex_ep["%_from_x"] = (ex_row_data["Price"] - ex_ep["x_price"]) / ex_ep["x_price"] * 100 if pd.notna(ex_ep.get("x_price")) else float("nan")
                    ex_ep["%_from_y"] = (ex_row_data["Price"] - ex_ep["y_price"]) / ex_ep["y_price"] * 100 if pd.notna(ex_ep.get("y_price")) else float("nan")
                    ex_ep["%_from_z"] = (ex_row_data["Price"] - ex_ep["z_price"]) / ex_ep["z_price"] * 100 if pd.notna(ex_ep.get("z_price")) else float("nan")
                    BP_STATUS_LABELS = {"signal_fired": "🟡 Signal fired", "partially_tested": "🟠 Partially tested",
                                        "tested": "🟢 Tested", "failed": "🔴 Failed"}
                    ex_ep["status_label"] = BP_STATUS_LABELS.get(ex_ep.get("status"), ex_ep.get("status", "—"))
                mc1, mc2, mc3, mc4, mc5 = st.columns(5)
                mc1.metric("Current Price", f"₹{ex_row_data['Price']:.2f}")
                mc2.metric("X (Leg 2 High)", f"₹{ex_ep['x_price']:.2f}" if pd.notna(ex_ep.get("x_price")) else "—",
                           f"{ex_ep['%_from_x']:+.2f}%" if pd.notna(ex_ep.get("%_from_x")) else None)
                mc3.metric("Y (Leg 3 Low 50EMA)", f"₹{ex_ep['y_price']:.2f}" if pd.notna(ex_ep.get("y_price")) else "—",
                           f"{ex_ep['%_from_y']:+.2f}%" if pd.notna(ex_ep.get("%_from_y")) else None)
                mc4.metric("Z (Leg 3 Low Price)", f"₹{ex_ep['z_price']:.2f}" if pd.notna(ex_ep.get("z_price")) else "—",
                           f"{ex_ep['%_from_z']:+.2f}%" if pd.notna(ex_ep.get("%_from_z")) else None)
                mc5.metric("Status", ex_ep.get("status_label", "—"))
                ex_fig = build_breakout_pullback_chart(ex_hist, explorer_symbol, ex_ep, explorer_period)
                st.plotly_chart(ex_fig, use_container_width=True, key="explorer_bp_fig")

        elif explorer_setup == "📈 EMA Pullback Reentry":
            ep_all = load_ema_pullback_ledger()
            sym_eps = ep_all[ep_all["symbol"] == explorer_symbol] if not ep_all.empty else pd.DataFrame()
            if sym_eps.empty:
                st.info(f"No EMA Pullback Reentry episodes detected for **{explorer_symbol}** yet.")
            else:
                sym_eps = sym_eps.copy()
                sym_eps["crossover_date"] = pd.to_datetime(sym_eps["crossover_date"])
                sym_eps = sym_eps.sort_values("crossover_date", ascending=False).reset_index(drop=True)
                EP_STATUS_LABELS_EX = {
                    "signal_pending": "🔵 Signal pending",
                    "naked": "⚪ Naked", "tested": "🟢 Tested", "failed": "🔴 Failed",
                }
                if len(sym_eps) > 1:
                    ep_labels = [
                        f"Crossover {row['crossover_date'].strftime('%d-%b-%Y')} — {EP_STATUS_LABELS_EX.get(row['status'], row['status'])}"
                        for _, row in sym_eps.iterrows()
                    ]
                    ep_choice = st.selectbox("Episode", ep_labels, index=0, key="explorer_ep_ep")
                    ep_idx = ep_labels.index(ep_choice)
                else:
                    ep_idx = 0
                ex_ep = sym_eps.iloc[ep_idx]
                if ex_row_data is not None:
                    ex_ep = ex_ep.copy()
                    ex_ep["current_price"] = ex_row_data["Price"]
                    ex_ep["%_from_x"] = (ex_row_data["Price"] - ex_ep["x_price"]) / ex_ep["x_price"] * 100 if pd.notna(ex_ep.get("x_price")) else float("nan")
                    ex_ep["%_from_y"] = (ex_row_data["Price"] - ex_ep["y_price"]) / ex_ep["y_price"] * 100 if pd.notna(ex_ep.get("y_price")) else float("nan")
                    ex_ep["status_label"] = EP_STATUS_LABELS_EX.get(ex_ep.get("status"), ex_ep.get("status", "—"))
                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric("Current Price", f"₹{ex_row_data['Price']:.2f}")
                mc2.metric("X (Highest High)", f"₹{ex_ep['x_price']:.2f}" if pd.notna(ex_ep.get("x_price")) else "—",
                           f"{ex_ep['%_from_x']:+.2f}%" if pd.notna(ex_ep.get("%_from_x")) else None)
                mc3.metric("Y (Buy Level)", f"₹{ex_ep['y_price']:.2f}" if pd.notna(ex_ep.get("y_price")) else "—",
                           f"{ex_ep['%_from_y']:+.2f}%" if pd.notna(ex_ep.get("%_from_y")) else None)
                mc4.metric("Status", ex_ep.get("status_label", "—"))
                ex_fig = build_ema_pullback_chart(ex_hist, explorer_symbol, ex_ep, explorer_period)
                st.plotly_chart(ex_fig, use_container_width=True, key="explorer_ep_fig")


        elif explorer_setup == "📊 Supertrend 3-Phase":
            st_all = load_supertrend_ledger()
            sym_eps = st_all[st_all["symbol"] == explorer_symbol] if not st_all.empty else pd.DataFrame()
            if sym_eps.empty:
                st.info(f"No Supertrend episodes detected for **{explorer_symbol}** yet.")
            else:
                sym_eps = sym_eps.copy()
                sym_eps["phase1_start"] = pd.to_datetime(sym_eps["phase1_start"])
                sym_eps["variant"] = sym_eps.apply(
                    lambda r: f"({int(r['st_period'])}, {float(r['st_multiplier']):.1f})", axis=1
                )
                sym_eps = sym_eps.sort_values(["phase1_start", "st_period"], ascending=False).reset_index(drop=True)
                ST_EP_SL = {"phase3_pending": "🟠 ST crossed 200 EMA – awaiting X",
                            "complete": "🟢 Complete"}
                if len(sym_eps) > 1:
                    ep_labels = [
                        f"{row['variant']} · P1 {row['phase1_start'].strftime('%d-%b-%Y')} — {ST_EP_SL.get(row['status'], row['status'])}"
                        for _, row in sym_eps.iterrows()
                    ]
                    ep_choice = st.selectbox("Episode", ep_labels, index=0, key="explorer_st_ep")
                    ep_idx = ep_labels.index(ep_choice)
                else:
                    ep_idx = 0
                ex_ep = sym_eps.iloc[ep_idx]
                if ex_row_data is not None:
                    ex_ep = ex_ep.copy()
                    ex_ep["current_price"] = ex_row_data["Price"]
                    ex_ep["%_from_x"] = (ex_row_data["Price"] - ex_ep["x_price"]) / ex_ep["x_price"] * 100 if pd.notna(ex_ep.get("x_price")) else float("nan")
                    ex_ep["%_from_y"] = (ex_row_data["Price"] - ex_ep["y_price"]) / ex_ep["y_price"] * 100 if pd.notna(ex_ep.get("y_price")) else float("nan")
                    ex_ep["%_from_z"] = (ex_row_data["Price"] - ex_ep["z_price"]) / ex_ep["z_price"] * 100 if pd.notna(ex_ep.get("z_price")) else float("nan")
                    ex_ep["status_label"] = ST_EP_SL.get(ex_ep.get("status"), ex_ep.get("status", "—"))
                    LEVEL_ICONS = {"naked": "⚪", "tested": "🟢", "failed": "🔴"}
                    ex_ep["x_icon"] = LEVEL_ICONS.get(ex_ep.get("x_status"), "—")
                    ex_ep["y_icon"] = LEVEL_ICONS.get(ex_ep.get("y_status"), "—")
                    ex_ep["z_icon"] = LEVEL_ICONS.get(ex_ep.get("z_status"), "—")
                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric("X (P1 High)", f"₹{ex_ep['x_price']:.2f}" if pd.notna(ex_ep.get("x_price")) else "—",
                           f"{ex_ep['%_from_x']:+.2f}%" if pd.notna(ex_ep.get("%_from_x")) else None)
                mc2.metric("Y (P2 Low)", f"₹{ex_ep['y_price']:.2f}" if pd.notna(ex_ep.get("y_price")) else "—",
                           f"{ex_ep['%_from_y']:+.2f}%" if pd.notna(ex_ep.get("%_from_y")) else None)
                mc3.metric("Z (Low 200EMA)", f"₹{ex_ep['z_price']:.2f}" if pd.notna(ex_ep.get("z_price")) else "—",
                           f"{ex_ep['%_from_z']:+.2f}%" if pd.notna(ex_ep.get("%_from_z")) else None)
                mc4.metric("Status", ex_ep.get("status_label", "—"))
                ex_fig = build_supertrend_chart(
                    ex_hist, explorer_symbol, ex_ep, explorer_period,
                    st_period=int(ex_ep.get("st_period", 7)),
                    st_multiplier=float(ex_ep.get("st_multiplier", 3.0)),
                )
                st.plotly_chart(ex_fig, use_container_width=True, key="explorer_st_fig")

        # Shared volume bar below every explorer chart
        ex_h = _trim_hist(ex_hist, explorer_period)
        ex_vfig = go.Figure(go.Bar(x=ex_h.index, y=ex_h["Volume"], name="Volume", marker_color="#6c757d"))
        ex_vfig.update_layout(height=160, margin=dict(l=70, r=20, t=6, b=10),
                              yaxis=dict(tickformat=",.0f", automargin=True))
        st.plotly_chart(ex_vfig, use_container_width=True, key="explorer_vol")
