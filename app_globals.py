"""
app_globals.py
----------------
Shared global sidebar and data-loading step, callable from every page  .
"""
from datetime import datetime

import streamlit as st

import dashboard_core as core


def load_globals():
    """
    Renders the GLOBAL sidebar (symbol universe + every strategy's
    calculation parameters) and runs the shared fetch_metrics() pass,
    exactly as the original monolithic script did at module level -
    copied verbatim, just wrapped in a function so every page (legacy
    and the new StrategyConfig pages) can call it identically and share
    the same widget keys (so selections persist across page switches)
    and the same @st.cache_data cache (so this only actually recomputes
    metrics_df when its underlying params change or the TTL expires -
    NOT on every page switch).

    fetch_metrics() computes ALL seven strategies' ledgers together in
    one batch - see the note in dashboard_core.py's module docstring.
    That means every strategy's parameter still has to live in this
    shared sidebar for now, even for strategies that already have their
    own dedicated page (Pivot S1, Supertrend) - splitting fetch_metrics
    itself into independent per-strategy cached calls is Phase 2 scope.
    """
    st.sidebar.title("⚙️ Controls")

    force_symbol_refresh = st.sidebar.button("🔄 Force-refresh symbol list (NSE)")
    symbol_df, symbol_fetched_at, symbol_source = core.load_symbol_universe(force_symbol_refresh)

    st.sidebar.caption(
        f"Universe: **Nifty 100 (Large Cap)** · {len(symbol_df)} stocks\n\n"
        f"Symbol list source: `{symbol_source}`\n\n"
        f"Last synced: {symbol_fetched_at.strftime('%d-%b-%Y %H:%M')}\n\n"
        f"Auto re-checks against NSE every 90 days (quarterly review cycle)."
    )

    if st.sidebar.button("🔃 Force-refresh prices now"):
        core.fetch_metrics.clear()
        core.load_streak_ledger.clear()
        core.load_five_leg_ledger.clear()
        core.load_pivot_ledger.clear()
        core.load_s1_shift_ledger.clear()
        core.load_breakout_pullback_ledger.clear()
        core.load_ema_pullback_ledger.clear()
        core.load_supertrend_ledger.clear()
        core.load_confluence_table_cached.clear()
        core.load_fundamental_cache_cached.clear()
        core.load_range_breakout_ledger.clear()

    st.sidebar.markdown("---")
    st.sidebar.subheader("Filters")

    search = st.sidebar.text_input("Search symbol", "").upper().strip()

    metric_filter = st.sidebar.selectbox(
        "Quick view",
        [
            "All stocks",
            "Near 52W High (within 5%)",
            "Near 52W Low (within 5%)",
            "Above 200 EMA",
            "Below 200 EMA",
            "Volume spike (RelVol > 1.5x)",
            "Retest of prior trend high (X) setups",
        ],
    )

    sort_col = st.sidebar.selectbox(
        "Sort by",
        ["%FromHigh", "%From200EMA", "TrendDays", "%FromX", "RelVolume", "DayChg%", "Price", "Symbol"],
        index=0,
    )
    sort_asc = st.sidebar.checkbox("Ascending", value=True)

    st.sidebar.markdown("---")
    st.sidebar.subheader("Retest-of-X setup")
    st.sidebar.caption(
        "X = the highest price made while a stock was above its 200 EMA for at "
        "least N days in a row, right before it finally closed back below the "
        "200 EMA. After that trend breaks, a rally back up toward X is a "
        "classic 'buy on retest' level to watch."
    )
    min_streak_days = st.sidebar.number_input(
        "Min days above 200 EMA to qualify as a trend (N)", min_value=50, max_value=500, value=200, step=10
    )
    retest_tolerance = st.sidebar.slider(
        "Retest zone: % band around X", min_value=1, max_value=15, value=5, step=1,
        help="Flags stocks whose current price is within this % of X (above or below it).",
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("5-Leg EMA Reversal pattern")
    st.sidebar.caption(
        "Down-up-down-up-down structure on 20 vs 50 EMA, each down leg making a "
        "new low and each up leg a lower high than its counterpart two legs back "
        "(confirmed by EMA or price, whichever shows it), followed eventually by "
        "the 50 EMA converging with the 200 EMA. X/Y = lowest 200 EMA / lowest "
        "price reached over that whole episode - support levels to watch."
    )
    min_leg_days = st.sidebar.number_input(
        "Min days per leg (filters out 20/50 EMA whipsaws)", min_value=2, max_value=30, value=5, step=1
    )
    only_show_retest_zone = st.sidebar.checkbox(
        "Only show stocks currently inside the retest band",
        value=False,
        help="Filters the table down to stocks whose price is within the % band above, regardless of the Quick view setting.",
    )
    retest_risk_filter = st.sidebar.selectbox(
        "Retest / reclaim status filter (latest streak)",
        [
            "All",
            "⚪ Naked — never approached again",
            "🟠 Testing resistance — approached from below, EMA not reclaimed yet",
            "🟢 Reclaimed, pending retest — EMA above X, no pullback to it yet (watchlist)",
            "🔵 Reclaimed & retested — pullback to X already happened",
        ],
        index=0,
        help=(
            "The 200 EMA is slow-moving - it can only climb back ABOVE the old high X if "
            "price already spent a long stretch trading above X, dragging the average up "
            "with it. 'Reclaimed, pending retest' is the live watchlist state: structure "
            "already confirmed bullish, but the actual low-risk pullback entry hasn't fired "
            "yet. 'Testing resistance' means EMA hasn't reclaimed X yet - a genuine, "
            "still-undetermined resistance test (higher risk). 'Reclaimed & retested' means "
            "the pullback-to-support entry already happened."
        ),
    )
    st.sidebar.caption(
        "🟢 Green = reclaimed, pending retest (watchlist) · 🔵 Blue = reclaimed & already retested · "
        "🟠 Amber = testing resistance (unreclaimed) · ⚪ Grey = naked"
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("Monthly Pivot S1 setup")
    st.sidebar.caption(
        "Daily 200 EMA vs. the monthly Standard Pivot S1 (from the prior "
        "completed month's High/Low/Close, held flat through the current "
        "month). S1 stays above the 200 EMA cleanly (no touch of either) for "
        "the qualifying window, then the running high (X) is fixed the first "
        "time price touches S1. From there, the running low (Y) is tracked "
        "until the 200 EMA finally crosses above X - a touch of the 200 EMA "
        "anywhere before that invalidates the whole setup."
    )
    min_qualify_months = st.sidebar.number_input(
        "Min calendar months S1 must stay clean above 200 EMA", min_value=1, max_value=12, value=2, step=1
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("Monthly S1 Shift Up setup")
    st.sidebar.caption(
        "The rare-case flip: price touches/closes at or below the monthly S1 "
        "at some point during a month, but that month's OWN range ends up "
        "pushing the FOLLOWING month's S1 higher than the current one - a sign "
        "of strong responsive buying. X = that month's lowest low, tracked "
        "afterward for a buy-on-revisit."
    )
    fail_pct = st.sidebar.number_input(
        "% below X that counts as 'failed'", min_value=1.0, max_value=30.0, value=8.0, step=0.5
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("🔀 Breakout-Pullback 4-Leg Pattern")
    st.sidebar.caption(
        "Daily 20/50/200 EMA. Leg 1: both EMAs below 200. Leg 2: 20 crosses above 50, "
        "both stay below 200 — track highest close as X. Leg 3: 20 crosses below 50 — "
        "track lowest 50 EMA as Y, lowest price as Z. Invalidated if both EMAs go below "
        "Leg 1 low. Leg 4: 20 crosses above 50 again — signal fires when price closes "
        "above X. Pullbacks to Y or Z are buying entries."
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("📈 EMA Pullback Reentry")
    st.sidebar.caption(
        "Daily 20/50 EMA. After 20 EMA crosses above 50 EMA, price must stay above the "
        "50 EMA (intraday Low must never touch it) for a minimum qualifying window. "
        "The highest High made during this clean run is X. The first day price pulls back "
        "to touch the 50 EMA locks X in. From there, the lowest Low (Y) is tracked until "
        "the 50 EMA crosses above X — that fires the signal. Any 20/50 EMA death-cross "
        "during Y-tracking invalidates the setup. Pullback to Y is the buy entry."
    )
    min_qualify_days = st.sidebar.number_input(
        "Min clean days above 50 EMA (qualification window)",
        min_value=10, max_value=200, value=50, step=5,
        help="How many consecutive trading days the 20 EMA must stay above 50 EMA "
             "(and price Low must not touch the 50 EMA) before the setup qualifies.",
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("📊 Supertrend 3-Phase Pattern")
    st.sidebar.caption(
        "Phase 1: Supertrend BUY, green line ≥ 200 EMA when SELL triggers → highest High = X. "
        "Phase 2: Supertrend SELL, must touch/cross below 200 EMA at least once → lowest Low = Y, lowest 200 EMA = Z. "
        "Not shown until Phase 3's ST line has crossed the 200 EMA (Phase 2, and pre-cross Phase 3, aren't actionable). "
        "Phase 3: ST flips BUY; once its line crosses ≥ 200 EMA the episode is shown, watching for ST line > X; "
        "episode completes once both have happened. "
        "Pullbacks to X / Y / Z are all buy-on-support entries."
    )
    st.sidebar.caption("Multiple Supertrend variants (period, multiplier) run simultaneously.")

    _st_params_options = {
        "(7, 3) — TradingView default": (7, 3.0),
        "(10, 3) — medium sensitivity": (10, 3.0),
        "(14, 3) — swing traders": (14, 3.0),
        "(21, 3) — position traders": (21, 3.0),
        "(7, 2) — tighter bands": (7, 2.0),
        "(10, 2) — medium tight": (10, 2.0),
    }
    _st_selected_labels = st.sidebar.multiselect(
        "Supertrend variants to run",
        options=list(_st_params_options.keys()),
        default=["(7, 3) — TradingView default", "(10, 3) — medium sensitivity"],
        key="st_params_select",
    )
    st_params_list = [_st_params_options[lbl] for lbl in _st_selected_labels] or [(7, 3.0)]

    st.sidebar.markdown("---")
    st.sidebar.subheader("📊 Range Breakout (5-Leg)")
    st.sidebar.caption(
        "Daily timeframe with monthly standard pivot points. 5-leg pattern: "
        "up-down-up-down-up. Signal appears once Leg 4 retests Leg 2 Low. "
        "Breakout confirmed when monthly pivot closes above Leg 1 High."
    )
    range_breakout_retest_pct = st.sidebar.slider(
        "Retest band % (around Leg 2 Low)", min_value=0.0, max_value=20.0,
        value=5.0, step=0.5, key="rb_retest_pct",
        help="How close price must come to Leg 2 Low to count as a retest.",
    )
    range_breakout_fail_pct = st.sidebar.slider(
        "False breakdown threshold % (below Leg 2 Low)", min_value=0.0, max_value=30.0,
        value=8.0, step=0.5, key="rb_fail_pct",
        help="How far below Leg 2 Low counts as a false breakdown.",
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "Data: Yahoo Finance (via `yfinance`), free & no API key.\n\n"
        "Prices cached 15 min per session to avoid rate limits."
    )

    # --------------------------------------------------------------------------
    # MAIN HEADER
    # --------------------------------------------------------------------------
    st.title("📈 Nifty Large-Cap Dashboard")
    st.caption(
        f"Live-ish snapshot · Data refreshes every {core.DATA_CACHE_TTL // 60} min · "
        f"{datetime.now().strftime('%d-%b-%Y %H:%M:%S')}"
    )

    with st.spinner("Fetching latest prices from Yahoo Finance (full history for trend/retest/pattern analysis)..."):
        metrics_df = core.fetch_metrics(
            symbol_df["Symbol"].tolist(),
            min_streak_days=int(min_streak_days),
            retest_pct=float(retest_tolerance),
            min_leg_days=int(min_leg_days),
            min_qualify_months=int(min_qualify_months),
            fail_pct=float(fail_pct),
            min_qualify_days=int(min_qualify_days),
            st_params_list=st_params_list,
            range_breakout_retest_pct=float(range_breakout_retest_pct),
            range_breakout_fail_pct=float(range_breakout_fail_pct),
        )

    if metrics_df.empty:
        st.error(
            "Couldn't fetch data from Yahoo Finance. Check your internet connection, "
            "then click 'Force-refresh prices now' in the sidebar."
        )
        st.stop()

    merged = symbol_df.merge(metrics_df, on="Symbol", how="inner")

    return {
        "symbol_df": symbol_df,
        "symbol_fetched_at": symbol_fetched_at,
        "symbol_source": symbol_source,
        "search": search,
        "metric_filter": metric_filter,
        "sort_col": sort_col,
        "sort_asc": sort_asc,
        "min_streak_days": min_streak_days,
        "retest_tolerance": retest_tolerance,
        "min_leg_days": min_leg_days,
        "only_show_retest_zone": only_show_retest_zone,
        "retest_risk_filter": retest_risk_filter,
        "min_qualify_months": min_qualify_months,
        "fail_pct": fail_pct,
        "min_qualify_days": min_qualify_days,
        "st_params_list": st_params_list,
        "range_breakout_retest_pct": range_breakout_retest_pct,
        "range_breakout_fail_pct": range_breakout_fail_pct,
        "metrics_df": metrics_df,
        "merged": merged,
    }