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


def render():
    st.header(f"📊 {VWAP_SR_CONFIG.display_name}")
    st.caption(VWAP_SR_CONFIG.description)

    # ── Sidebar: scan controls ────────────────────────────────────────────
    with st.sidebar:
        st.markdown("---")
        st.subheader("🔧 VWAP S/R Controls")

        min_confirm_days = st.slider(
            "Min confirm days", 2, 10,
            int(VWAP_SR_CONFIG.params.get("min_confirm_days", 5)),
            key="vwap_min_confirm",
        )
        retest_pct = st.slider(
            "Retest tolerance %", 1.0, 15.0,
            float(VWAP_SR_CONFIG.params.get("retest_pct", 5.0)),
            step=0.5, key="vwap_retest_pct",
        )
        fail_pct = st.slider(
            "Fail threshold %", 2.0, 20.0,
            float(VWAP_SR_CONFIG.params.get("fail_pct", 8.0)),
            step=0.5, key="vwap_fail_pct",
        )
        min_gap_pct = st.slider(
            "Min gap % (Day D)", 0.1, 2.0,
            float(VWAP_SR_CONFIG.params.get("min_gap_pct", 0.5)),
            step=0.1, key="vwap_min_gap",
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

                result = run_vwap_sr_strategy(progress_callback=_progress)
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

    # Normalize day_d_date immediately so all downstream code works with
    # Timestamps (sorting is chronological, not lexicographic on strings).
    vwap_df["day_d_date"] = pd.to_datetime(vwap_df["day_d_date"], errors="coerce")

    # Enrich with current price from globals
    g = load_globals()
    merged = g.get("merged")
    if merged is not None and not merged.empty:
        price_lookup = merged.set_index("Symbol")["Price"].to_dict()
        vwap_df["current_price"] = vwap_df["symbol"].map(price_lookup)
    else:
        vwap_df["current_price"] = np.nan

    # Guard against zero / NaN / inf x_price before computing % from X
    _x = pd.to_numeric(vwap_df["x_price"], errors="coerce")
    _x = _x.replace([0, np.inf, -np.inf], np.nan)
    vwap_df["%_from_x"] = np.where(
        _x.notna(),
        (vwap_df["current_price"] - _x) / _x * 100,
        np.nan,
    )

    # ── Filters ───────────────────────────────────────────────────────────
    fcol1, fcol2, fcol3, fcol4 = st.columns(4)

    with fcol1:
        search = st.text_input("🔍 Symbol", "", key="vwap_search").upper().strip()
    with fcol2:
        type_filter = st.selectbox(
            "Episode type",
            ["All", "lower_band (support)", "upper_band (resistance)"],
            key="vwap_type_filter",
        )
    with fcol3:
        status_filter = st.selectbox(
            "Status",
            ["All", "naked", "tested", "failed"],
            key="vwap_status_filter",
        )
    with fcol4:
        prox_band = st.slider("Within % of X", 1, 30, 10, key="vwap_prox_band")

    view = vwap_df.dropna(subset=["current_price"]).copy()

    if search:
        view = view[view["symbol"].str.contains(search)]
    if type_filter != "All":
        etype = "lower_band" if "lower" in type_filter else "upper_band"
        view = view[view["episode_type"] == etype]
    if status_filter != "All":
        view = view[view["status"] == status_filter]
    view = view[view["%_from_x"].abs() <= prox_band]

    view = view.copy()
    view = view.sort_values("day_d_date", ascending=False).reset_index(drop=True)

    st.caption(f"Showing {len(view)} of {len(vwap_df)} recorded episodes")

    # ── Styled Table ──────────────────────────────────────────────────────
    display_cols = [
        "symbol", "day_d_date", "episode_type", "x_price", "classification",
        "status", "current_price", "%_from_x", "gap_pct", "days_tracked",
        "max_runup_pct", "max_drawdown_pct",
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
        "max_drawdown_pct": "Max Drawdown %",
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

    styled = (
        display_df.style
        .map(_color_status, subset=["Status"])
        .map(_color_type, subset=["Type"])
        .map(
            lambda v: "" if pd.isna(v) else ("color:#1a7f37;font-weight:600" if v >= 0 else "color:#b91c1c;font-weight:600"),
            subset=["% From X"],
        )
        .format({
            "Day D Date": lambda d: pd.to_datetime(d).strftime("%d-%b-%Y"),
            "X (Level)": "₹{:.2f}",
            "Price": "₹{:.2f}",
            "% From X": "{:+.2f}%",
            "Gap %": "{:.2f}%",
            "Max Runup %": "{:.2f}%",
            "Max Drawdown %": "{:.2f}%",
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
