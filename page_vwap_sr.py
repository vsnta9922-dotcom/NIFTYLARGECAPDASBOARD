"""
page_vwap_sr.py
----------------
VWAP Support/Resistance strategy page — SCALED TO NIFTY 100.
"""

from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

import hourly_price_cache
from vwap_support_resistance_pattern import compute_session_summary

from app_globals import load_globals
from dashboard_core import DATA_CACHE_TTL, fetch_history_for_chart
from strategy_vwap_sr import (
    VWAP_SR_CONFIG,
    load_vwap_sr_ledger,
    run_vwap_sr_strategy,
)
from vwap_sr_chart import build_vwap_sr_chart


_FILTER_KEYS = [
    "vf_symbol", "vf_type", "vf_status", "vf_class",
    "vf_pct_lo", "vf_pct_hi", "vf_gap_lo", "vf_gap_hi",
    "vf_days_lo", "vf_days_hi", "vf_runup_lo", "vf_runup_hi",
    "vf_xp_lo", "vf_xp_hi", "vf_pr_lo", "vf_pr_hi",
    "vf_dd_lo", "vf_dd_hi", "vf_sort_by",
]


def _reset_filters():
    for k in _FILTER_KEYS:
        if k in st.session_state:
            del st.session_state[k]


def render():
    st.header(f"📊 {VWAP_SR_CONFIG.display_name}")
    st.caption(VWAP_SR_CONFIG.description)

    # ── Sidebar: scan controls ────────────────────────────────────────────
    with st.sidebar:
        st.markdown("---")
        st.subheader("🔧 VWAP S/R Controls")

        min_confirm_days = st.slider(
            "Quiet window (trading days)", 1, 20,
            int(VWAP_SR_CONFIG.params.get("min_confirm_days", 5)),
            step=1, key="vwap_min_confirm_days",
            help="Price must NOT touch X for this many trading days after Day D. A touch inside this window invalidates the episode.",
        )
        fail_pct = st.slider(
            "Fail threshold %", 2.0, 20.0,
            float(VWAP_SR_CONFIG.params.get("fail_pct", 8.0)),
            step=0.5, key="vwap_fail_pct",
            help="If price breaches X by this % at any point from Day D onward, the episode is marked failed.",
        )
        min_gap_pct = st.slider(
            "Min gap % (Day D)", 0.1, 2.0,
            float(VWAP_SR_CONFIG.params.get("min_gap_pct", 0.5)),
            step=0.1, key="vwap_min_gap",
            help="The VWAP band must clear the first-hour boundary by at least this % to avoid rounding-noise signals.",
        )
        retest_pct = st.slider(
            "Retest band %", 0.0, 15.0,
            float(VWAP_SR_CONFIG.params.get("retest_pct", 5.0)),
            step=0.5, key="vwap_retest_pct",
            help="Kept for compatibility with Monthly S1 Shift strategy. Not used in the quiet-window retest logic.",
        )

        st.markdown("---")
        rescan = st.button("🔄 Rescan Universe", type="primary", key="vwap_rescan")
        if rescan:
            with st.spinner("Scanning NIFTY 100 for VWAP S/R episodes..."):
                progress_bar = st.progress(0.0, text="Initializing...")

                def _progress(done, total):
                    progress_bar.progress(
                        done / max(total, 1),
                        text=f"Processed {done}/{total} symbols...",
                    )

                result = run_vwap_sr_strategy(
                    progress_callback=_progress,
                    min_confirm_days=min_confirm_days,
                    retest_pct=retest_pct,
                    fail_pct=fail_pct,
                    min_gap_pct=min_gap_pct,
                )
                progress_bar.empty()
                st.success(
                    f"✅ Scanned {result.metadata['symbols_scanned']} symbols, "
                    f"found {result.metadata['episodes_found']} episodes."
                )
                st.balloons()
                load_vwap_sr_ledger.clear()
                st.rerun()

    # ── Load cached ledger ────────────────────────────────────────────────
    vwap_df = load_vwap_sr_ledger()

    if vwap_df.empty:
        st.info(
            "No VWAP S/R episodes recorded yet. "
            "Click **🔄 Rescan Universe** in the sidebar to run the first scan."
        )
        return

    # Enrich with current price from globals
    g = load_globals()
    merged = g.get("merged")
    if merged is not None and not merged.empty:
        price_lookup = merged.set_index("Symbol")["Price"].to_dict()
        vwap_df["current_price"] = vwap_df["symbol"].map(price_lookup)
    else:
        vwap_df["current_price"] = np.nan

    # ── Excel-style Column Filters ────────────────────────────────────────
    # Numeric columns: min/max range inputs with >, < semantics.
    # Categorical columns: multi-select checkboxes (like Excel's dropdown).
    # All filters are additive (AND logic).  Collapsed by default so the
    # table is visible immediately; expand only when you want to filter.

    view = vwap_df.dropna(subset=["current_price"]).copy()
    view["day_d_date"] = pd.to_datetime(view["day_d_date"], errors="coerce")

    # Compute % From X now so it's available for range filtering.
    view["%_from_x"] = np.where(
        view["x_price"].fillna(0) != 0,
        (view["current_price"] - view["x_price"]) / view["x_price"] * 100,
        np.nan,
    )

    def _rng(col):
        """Safe finite min/max for a column, ignoring NaN/inf."""
        s = view[col].replace([np.inf, -np.inf], np.nan).dropna()
        if s.empty:
            return 0.0, 0.0
        return float(s.min()), float(s.max())

    with st.expander("🔽 Column Filters", expanded=False):
        # ── Row 1: categorical ────────────────────────────────────────────
        cc1, cc2, cc3, cc4 = st.columns(4)

        with cc1:
            sym_search = st.text_input("Symbol contains", "", key="vf_symbol").upper().strip()

        with cc2:
            all_types = sorted(view["episode_type"].dropna().unique().tolist())
            sel_types = st.multiselect("Type", all_types, default=all_types, key="vf_type")

        with cc3:
            all_statuses = sorted(view["status"].dropna().unique().tolist())
            sel_statuses = st.multiselect("Status", all_statuses, default=all_statuses, key="vf_status")

        with cc4:
            all_class = sorted(view["classification"].dropna().unique().tolist())
            sel_class = st.multiselect("Classification", all_class, default=all_class, key="vf_class")

        st.markdown("---")

        # ── Row 2: % From X, Gap %, Days Tracked ─────────────────────────
        nc1, nc2, nc3, nc4 = st.columns(4)

        pct_min, pct_max = _rng("%_from_x")
        gap_min, gap_max = _rng("gap_pct")
        days_min, days_max = _rng("days_tracked")
        runup_min, runup_max = _rng("max_runup_pct")

        with nc1:
            st.caption("% From X")
            fc1a, fc1b = st.columns(2)
            pct_lo = fc1a.number_input("≥", value=float(round(pct_min, 2)), step=0.5, format="%.2f", key="vf_pct_lo")
            pct_hi = fc1b.number_input("≤", value=float(round(pct_max, 2)), step=0.5, format="%.2f", key="vf_pct_hi")

        with nc2:
            st.caption("Gap %")
            fc2a, fc2b = st.columns(2)
            gap_lo = fc2a.number_input("≥", value=float(round(gap_min, 2)), step=0.1, format="%.2f", key="vf_gap_lo")
            gap_hi = fc2b.number_input("≤", value=float(round(gap_max, 2)), step=0.1, format="%.2f", key="vf_gap_hi")

        with nc3:
            st.caption("Days Tracked")
            fc3a, fc3b = st.columns(2)
            days_lo = fc3a.number_input("≥", value=int(days_min), step=1, format="%d", key="vf_days_lo")
            days_hi = fc3b.number_input("≤", value=int(days_max), step=1, format="%d", key="vf_days_hi")

        with nc4:
            st.caption("Max Runup %")
            fc4a, fc4b = st.columns(2)
            runup_lo = fc4a.number_input("≥", value=float(round(runup_min, 2)), step=0.5, format="%.2f", key="vf_runup_lo")
            runup_hi = fc4b.number_input("≤", value=float(round(runup_max, 2)), step=0.5, format="%.2f", key="vf_runup_hi")

        # ── Row 3: X (Level), Price, Drawdown ─────────────────────────────
        nc5, nc6, nc7, nc8 = st.columns(4)

        xprice_min, xprice_max = _rng("x_price")
        price_min, price_max   = _rng("current_price")
        dd_min, dd_max         = _rng("drawdown_pct")

        with nc5:
            st.caption("X (Level) ₹")
            fc5a, fc5b = st.columns(2)
            xp_lo = fc5a.number_input("≥", value=float(round(xprice_min, 2)), step=10.0, format="%.2f", key="vf_xp_lo")
            xp_hi = fc5b.number_input("≤", value=float(round(xprice_max, 2)), step=10.0, format="%.2f", key="vf_xp_hi")

        with nc6:
            st.caption("Current Price ₹")
            fc6a, fc6b = st.columns(2)
            pr_lo = fc6a.number_input("≥", value=float(round(price_min, 2)), step=10.0, format="%.2f", key="vf_pr_lo")
            pr_hi = fc6b.number_input("≤", value=float(round(price_max, 2)), step=10.0, format="%.2f", key="vf_pr_hi")

        with nc7:
            st.caption("Max Drawdown %")
            fc7a, fc7b = st.columns(2)
            dd_lo = fc7a.number_input("≥", value=float(round(dd_min, 2)) if not np.isnan(dd_min) else 0.0,
                                       step=0.5, format="%.2f", key="vf_dd_lo")
            dd_hi = fc7b.number_input("≤", value=float(round(dd_max, 2)) if not np.isnan(dd_max) else 0.0,
                                       step=0.5, format="%.2f", key="vf_dd_hi")

        with nc8:
            st.caption("Sort by")
            sort_by = st.selectbox(
                "Sort by",
                ["Day D Date ↓", "Day D Date ↑", "Days Tracked ↓", "Days Tracked ↑",
                 "% From X ↑", "% From X ↓", "Max Runup % ↓", "Max Drawdown % ↑"],
                key="vf_sort_by", label_visibility="collapsed",
            )
            st.button("🔁 Reset all filters", key="vf_reset", on_click=_reset_filters)

    # ── Apply all filters ─────────────────────────────────────────────────
    if sym_search:
        view = view[view["symbol"].str.contains(sym_search, na=False)]
    if sel_types:
        view = view[view["episode_type"].isin(sel_types)]
    if sel_statuses:
        view = view[view["status"].isin(sel_statuses)]
    if sel_class:
        view = view[view["classification"].isin(sel_class)]

    view = view[view["%_from_x"].between(pct_lo, pct_hi, inclusive="both") | view["%_from_x"].isna()]
    view = view[view["gap_pct"].between(gap_lo, gap_hi, inclusive="both") | view["gap_pct"].isna()]
    view = view[view["days_tracked"].between(days_lo, days_hi, inclusive="both") | view["days_tracked"].isna()]
    view = view[view["max_runup_pct"].between(runup_lo, runup_hi, inclusive="both") | view["max_runup_pct"].isna()]
    view = view[view["x_price"].between(xp_lo, xp_hi, inclusive="both") | view["x_price"].isna()]
    view = view[view["current_price"].between(pr_lo, pr_hi, inclusive="both") | view["current_price"].isna()]

    # Drawdown filter: only apply if the range was actually narrowed by the user
    # (dd_min/dd_max come from the data, so a user narrowing it means lo > dd_min or hi < dd_max).
    _dd_col = view["drawdown_pct"].replace([np.inf, -np.inf], np.nan)
    view = view[_dd_col.between(dd_lo, dd_hi, inclusive="both") | _dd_col.isna()]

    _sort_map = {
        "Day D Date ↓":       ("day_d_date",    False),
        "Day D Date ↑":       ("day_d_date",    True),
        "Days Tracked ↓":     ("days_tracked",  False),
        "Days Tracked ↑":     ("days_tracked",  True),
        "% From X ↑":         ("%_from_x",      True),
        "% From X ↓":         ("%_from_x",      False),
        "Max Runup % ↓":      ("max_runup_pct", False),
        "Max Drawdown % ↑":   ("drawdown_pct",  True),
    }
    _sort_col, _sort_asc = _sort_map.get(sort_by, ("day_d_date", False))
    view = view.sort_values(_sort_col, ascending=_sort_asc).reset_index(drop=True)

    st.caption(f"Showing {len(view)} of {len(vwap_df)} recorded episodes")

    # ── Styled Table ──────────────────────────────────────────────────────
    display_cols = [
        "symbol", "day_d_date", "episode_type", "x_price", "classification",
        "status", "current_price", "%_from_x", "gap_pct", "days_tracked",
        "max_runup_pct", "drawdown_pct", "drawdown_recovered",
        "drawdown_recovery_date", "drawdown_days_to_recover",
    ]
    display_df = view[display_cols].rename(columns={
        "symbol": "Symbol",
        "day_d_date": "Day D Date",
        "episode_type": "Type",
        "x_price": "X (Level)",
        "classification": "Classification",
        "status": "Status",
        "current_price": "Price",
        "%_from_x": "% From X",
        "gap_pct": "Gap %",
        "days_tracked": "Days Tracked",
        "max_runup_pct": "Max Runup %",
        "drawdown_pct": "Max Drawdown %",
        "drawdown_recovered": "DD Recovered",
        "drawdown_recovery_date": "DD Recovery Date",
        "drawdown_days_to_recover": "DD Days to Recover",
    })

    def _color_status(val):
        if val == "naked":
            return "background-color:#eeeeee; color:#666; font-weight:600"
        if val == "tested":
            return "background-color:#d1f2eb; color:#0b6b4f; font-weight:600"
        if val == "failed":
            return "background-color:#fadbd8; color:#c0392b; font-weight:600"
        return ""

    def _color_type(val):
        if "lower" in str(val):
            return "color:#27ae60; font-weight:600"
        if "upper" in str(val):
            return "color:#e74c3c; font-weight:600"
        return ""

    def _color_pct(val):
        if pd.isna(val):
            return ""
        return "color:#1a7f37;font-weight:600" if val >= 0 else "color:#b91c1c;font-weight:600"

    def _color_dd(val):
        if pd.isna(val):
            return ""
        return "color:#b91c1c;font-weight:600" if val < 0 else "color:#1a7f37;font-weight:600"

    styled = (
        display_df.style
        .map(_color_status, subset=["Status"])
        .map(_color_type, subset=["Type"])
        .map(_color_pct, subset=["% From X", "Max Runup %"])
        .map(_color_dd, subset=["Max Drawdown %"])
        .map(
            lambda v: "color:#27ae60;font-weight:600" if v is True else ("color:#b91c1c" if v is False else ""),
            subset=["DD Recovered"],
        )
        .format({
            "Day D Date": lambda d: pd.to_datetime(d).strftime("%d-%b-%Y"),
            "X (Level)": "₹{:.2f}",
            "Price": "₹{:.2f}",
            "% From X": "{:+.2f}%",
            "Gap %": "{:.2f}%",
            "Max Runup %": "{:.2f}%",
            "Max Drawdown %": "{:.2f}%",
            "DD Recovery Date": lambda d: pd.to_datetime(d).strftime("%d-%b-%Y") if pd.notna(d) else "—",
            "DD Days to Recover": "{:.0f}",
        }, na_rep="—")
    )

    event = st.dataframe(
        styled,
        use_container_width=True,
        height=560,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="vwap_table",
    )

    # ── Selected Row Charts ───────────────────────────────────────────────
    selected_rows = []
    if event and event.selection and event.selection.get("rows"):
        selected_rows = event.selection["rows"]

    MAX_COMPARE = 6
    if len(selected_rows) > MAX_COMPARE:
        st.caption(
            f"⚠️ You selected {len(selected_rows)} rows — only the first "
            f"{MAX_COMPARE} are shown below."
        )
        selected_rows = selected_rows[:MAX_COMPARE]

    if not selected_rows:
        st.info("👆 Select one or more rows above to view their VWAP S/R charts.")
        return

    st.markdown("---")
    st.subheader("📈 VWAP S/R Charts")

    cols_per_row = 2
    for row_start in range(0, len(selected_rows), cols_per_row):
        row_indices = selected_rows[row_start:row_start + cols_per_row]
        row_cols = st.columns(len(row_indices))
        for col, idx in zip(row_cols, row_indices):
            with col:
                ep = view.iloc[idx]
                sym = ep["symbol"]
                ep_type = ep.get("episode_type", "unknown")
                ep_status = ep.get("status", "unknown")
                st.markdown(f"**{sym}** — {ep_type} · {ep_status}")

                daily_hist = fetch_history_for_chart(sym, period="1y")
                if daily_hist.empty:
                    st.warning(f"No data for {sym}")
                    continue

                # VWAP S/R chart requires hourly data + session summary for the top panel
                hourly_hist = hourly_price_cache.get_hourly_history(sym)
                session_summary = (
                    compute_session_summary(hourly_hist, daily_hist)
                    if not hourly_hist.empty
                    else pd.DataFrame()
                )

                fig = build_vwap_sr_chart(
                    daily_hist=daily_hist,
                    symbol=sym,
                    ep_row=ep,
                    daily_period="1y",
                    hourly_df=hourly_hist,
                    session_summary=session_summary,
                )
                st.plotly_chart(fig, use_container_width=True, key=f"vwap_chart_{sym}_{idx}")