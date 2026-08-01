"""
strategy_framework.py
------------------------
Generic StrategyPage shell.

Layout is the personal-research-workstation workflow, NOT a permanent
left-table/right-chart split:

    Filters
      v
    Strategy Table (full width, horizontal scroll, Symbol+Status frozen)
      v
    Charts (placeholder until something is selected; full width once it is)

Every strategy page is rendered by the SAME render_strategy_page()
function, parametrized by a StrategyConfig:

    load_data()          -> raw ledger DataFrame (one of dashboard_core's
                             load_*_ledger() cached functions)
    apply_filters(df)    -> filtered+sorted view DataFrame. Each strategy
                             owns its own filter widget layout (common
                             filters visible, less-used ones behind an
                             st.expander) - the framework doesn't dictate
                             filter layout, only where filters sit
                             relative to the table/charts.
    status_label_fn       -> row -> status label string
    display_columns        -> {source_col: display_col}, in display order
    chart_builder          -> one of dashboard_core's build_*_chart functions
    single_chart_metrics_fn -> ep_row -> list of metric ROWS
    single_chart_caption_fn / multi_chart_caption_fn -> caption text
    extra_kwargs_fn / extra_style_fn -> optional strategy-specific hooks

No strategy math lives here — this module only lays out widgets and wires
selection state to dashboard_core's existing chart builders and
render_strategy_multi_chart_grid(), all of which are imported unmodified.
"""
from dataclasses import dataclass
from typing import Any, Dict, List
from typing import Callable, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import dashboard_core as core


@dataclass
class StrategyConfig:
    key: str
    title: str
    description: str
    status_legend_md: str
    load_data: Callable[[], pd.DataFrame]
    apply_filters: Callable[[pd.DataFrame], pd.DataFrame]
    status_label_fn: Callable[[pd.Series], str]
    status_color_map: dict
    display_columns: dict
    numeric_pct_cols: list
    format_spec: dict
    chart_builder: Callable
    single_chart_metrics_fn: Callable[[pd.Series], list]
    single_chart_caption_fn: Callable[[pd.Series], str]
    multi_chart_caption_fn: Callable[[str, pd.Series], str]
    extra_kwargs_fn: Optional[Callable[[pd.Series], dict]] = None
    extra_style_fn: Optional[Callable] = None
    max_compare: int = 6
    frozen_columns: tuple = ("Symbol", "Status")  # pinned left, per the "freeze Selection/Symbol/Status" requirement
@dataclass         
class StrategyResult:
    config: StrategyConfig
    signal: str
    trend: str
    strength: float
    confidence: float
    metadata: dict
    raw_data: pd.DataFrame


# Selection count -> compare-grid layout, exactly the mapping requested:
# 1 is handled separately (single large chart); everything else is looked
# up here so there's no manual "Layout" picker for the person to fiddle
# with — one fewer click in the screening loop.
_COUNT_TO_LAYOUT = {2: "2", 3: "4", 4: "4", 5: "6", 6: "6"}


def _anchor(name: str) -> None:
    """An invisible, zero-cost scroll target — no widget, no rerun."""
    st.markdown(f'<div id="{name}"></div>', unsafe_allow_html=True)


def _jump_link(target: str, label: str) -> None:
    """
    A plain HTML anchor styled as a small button. Deliberately NOT an
    st.button/st.link_button — either would still be a Streamlit widget
    (extra DOM weight, and st.button triggers a full rerun on click for
    zero reason here). A same-page '#anchor' link does the scroll entirely
    in the browser, so clicking it costs nothing server-side.
    """
    st.markdown(
        f'<a href="#{target}" style="text-decoration:none;">'
        f'<div style="display:inline-block; padding:0.4rem 0.9rem; '
        f'border-radius:0.4rem; background-color:#f0f2f6; color:#31333F; '
        f'font-weight:600; font-size:0.9rem; margin:0.3rem 0;">{label}</div>'
        f'</a>',
        unsafe_allow_html=True,
    )


def render_strategy_page(cfg: StrategyConfig, merged: pd.DataFrame) -> None:
    """Renders one strategy end-to-end: filters -> table -> charts."""
    st.subheader(cfg.title)
    st.caption(cfg.description)
    with st.expander("ℹ️ What the statuses mean"):
        st.markdown(cfg.status_legend_md)

    raw_df = cfg.load_data()
    if raw_df.empty:
        st.info("No episodes recorded yet — run the dashboard at least once with data loaded.")
        return

    price_lookup = merged.set_index("Symbol")["Price"].to_dict()
    raw_df = raw_df.copy()
    raw_df["current_price"] = raw_df["symbol"].map(price_lookup)
    raw_df["status_label"] = raw_df.apply(cfg.status_label_fn, axis=1)

    # ---- Filters (each strategy owns its own widget layout) ----
    view_df = cfg.apply_filters(raw_df)
    st.caption(f"{len(view_df)} episode(s) match your filters (out of {len(raw_df)} recorded total).")

    # ---- Table: full page width, horizontal scroll, Symbol+Status frozen ----
    _anchor(f"table-{cfg.key}")
    display_df = view_df[list(cfg.display_columns.keys())].rename(columns=cfg.display_columns)
    styled = display_df.style
    for col in cfg.numeric_pct_cols:
        styled = styled.map(
            lambda v: "" if pd.isna(v) else ("color:#1a7f37;font-weight:600" if v >= 0 else "color:#b91c1c;font-weight:600"),
            subset=[col],
        )
    styled = styled.map(lambda v: cfg.status_color_map.get(v, ""), subset=["Status"])
    if cfg.extra_style_fn is not None:
        styled = cfg.extra_style_fn(styled)
    styled = styled.format(cfg.format_spec, na_rep="—")

    column_config = {
        col_name: st.column_config.Column(pinned=True)
        for col_name in cfg.frozen_columns
        if col_name in display_df.columns
    }

    event = st.dataframe(
        styled,
        width="stretch",           # use the full page width — no more forced fullscreen to read columns
        height=520,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        column_config=column_config,
        key=f"{cfg.key}_table",
    )

    selected_rows = []
    if event and event.selection and event.selection.get("rows"):
        selected_rows = event.selection["rows"]
    if len(selected_rows) > cfg.max_compare:
        st.caption(
            f"⚠️ You checked {len(selected_rows)} rows — only the first "
            f"{cfg.max_compare} are shown below. Uncheck a few to compare a different set."
        )
        selected_rows = selected_rows[: cfg.max_compare]

    if selected_rows:
        _jump_link(f"charts-{cfg.key}", f"📊 Jump to Charts ({len(selected_rows)} selected) ↓")

    # ---- Charts: placeholder until something is selected, full width once it is ----
    _anchor(f"charts-{cfg.key}")

    if not selected_rows:
        st.info("Select one or more stocks to begin chart analysis.")
        return

    _jump_link(f"table-{cfg.key}", "↑ Back to Table")

    if len(selected_rows) == 1:
        ep = view_df.iloc[selected_rows[0]]
        sym = ep["symbol"]
        st.markdown(f"#### 📊 {cfg.title.split(' ', 1)[-1]} Chart — {sym}")
        for metrics_row in cfg.single_chart_metrics_fn(ep):
            metric_cols = st.columns(len(metrics_row))
            for mc, (label, value, delta) in zip(metric_cols, metrics_row):
                mc.metric(label, value, delta)
        st.caption(cfg.single_chart_caption_fn(ep))
        period = st.radio(
            "Chart period", ["6mo", "1y", "2y", "5y", "10y", "max"],
            horizontal=True, index=2, key=f"{cfg.key}_period",
        )
        with st.spinner(f"Loading chart for {sym}…"):
            hist = core.fetch_full_history_with_indicators(sym)
        if hist.empty:
            st.warning(f"No price history available for {sym}.")
        else:
            extra_kwargs = cfg.extra_kwargs_fn(ep) if cfg.extra_kwargs_fn else {}
            fig = cfg.chart_builder(hist, sym, ep, period, **extra_kwargs)
            fig.update_layout(height=640)  # a single selected stock gets the largest chart — this IS the main view
            st.plotly_chart(fig, use_container_width=True, key=f"{cfg.key}_chart_{sym}")
            h = core._trim_hist(hist, period)
            vfig = go.Figure(go.Bar(x=h.index, y=h["Volume"], name="Volume", marker_color="#6c757d"))
            vfig.update_layout(height=160, margin=dict(l=70, r=20, t=6, b=10),
                               yaxis=dict(tickformat=",.0f", automargin=True))
            st.plotly_chart(vfig, use_container_width=True, key=f"{cfg.key}_vol_{sym}")
    else:
        st.markdown(f"#### 📊 Comparing {len(selected_rows)} selected episode(s)")
        layout = _COUNT_TO_LAYOUT[len(selected_rows)]  # auto: 2->2col, 3-4->2x2, 5-6->grid — no manual picker
        period = st.radio(
            "Chart period", ["6mo", "1y", "2y", "5y"], horizontal=True, index=1, key=f"{cfg.key}_compare_period"
        )
        selected_eps = [(view_df.iloc[i]["symbol"], view_df.iloc[i]) for i in selected_rows]
        core.render_strategy_multi_chart_grid(
            selected_eps, cfg.chart_builder, layout, period,
            key_prefix=f"{cfg.key}_multi",
            caption_fn=cfg.multi_chart_caption_fn,
            extra_kwargs_fn=cfg.extra_kwargs_fn,
            grid_height=420,  # slightly taller than the old 340 — charts should stay as large as possible
        )
