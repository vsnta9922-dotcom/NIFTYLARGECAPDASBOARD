"""
dashboard_app.py
-------------------
New entrypoint for the workstation redesign (Phase 1 pilot).

Run with:   streamlit run dashboard_app.py

Uses st.navigation(position="top") — Streamlit's NATIVE multi-page API
(not st.tabs, and no third-party dependency) — so only the currently
selected page's function actually executes on each rerun. Switching pages
does not re-render the other pages' tables/charts, and st.session_state
(so widget selections, filters, and chart period choices) persists across
page switches within the same session, since Streamlit's session state is
shared across all pages in one st.navigation() app.

Pages:
  🏠 Dashboard (Classic)  — the ORIGINAL all-strategies view, unchanged,
                             so every strategy remains available even
                             before the full migration is complete.
  📐 Monthly Pivot S1     — pilot #1 of the new StrategyConfig framework.
  📊 Supertrend           — pilot #2 (the "hard case": per-episode extra
                             chart kwargs, a row-reshaping filter step,
                             and extra table styling beyond the generic
                             framework rules).

st.set_page_config() is called exactly once here, since it can only be
called once per app run — this is why the page-config/CSS block was
removed from legacy_dashboard.py (it used to own that call when it was
still the top of the monolithic script).
"""
import streamlit as st

st.set_page_config(
    page_title="Nifty Large-Cap Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Same metric-value CSS fix as the original script, moved here since this
# is now the single place page_config-adjacent setup belongs.
st.markdown(
    """
    <style>
    [data-testid="stMetricValue"] {
        font-size: 1.35rem;
        white-space: normal;
        overflow: visible;
        text-overflow: unset;
        line-height: 1.2;
    }
    [data-testid="stMetricDelta"] {
        white-space: normal;
        overflow: visible;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

import legacy_dashboard
import page_pivot
import page_supertrend
import page_five_leg
import page_s1_shift
import page_breakout_pullback
import page_ema_pullback
import page_vwap_sr
import page_range_breakout

pages = [
    st.Page(legacy_dashboard.render, title="Dashboard (Classic)", icon="🏠",
            url_path="dashboard", default=True),
    st.Page(page_pivot.render, title="Monthly Pivot S1", icon="📐", url_path="pivot-s1"),
    st.Page(page_supertrend.render, title="Supertrend", icon="📊", url_path="supertrend"),
    st.Page(page_five_leg.render, title="5-Leg EMA Reversal", icon="🔀", url_path="five-leg"),
    st.Page(page_s1_shift.render, title="Monthly S1 Shift", icon="🔺", url_path="s1-shift"),
    st.Page(page_breakout_pullback.render, title="Breakout-Pullback", icon="📈", url_path="breakout-pullback"),
    st.Page(page_ema_pullback.render, title="EMA Pullback", icon="🔁", url_path="ema-pullback"),
    st.Page(page_vwap_sr.render, title="VWAP S/R (Pilot)", icon="🧪", url_path="vwap-sr"),
    st.Page(page_range_breakout.render, title="Range Breakout (5-Leg)", icon="📊", url_path="range-breakout"),
]

nav = st.navigation(pages, position="top")
nav.run()