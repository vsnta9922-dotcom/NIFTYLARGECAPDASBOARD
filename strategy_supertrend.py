"""
strategy_supertrend.py
-------------------------
Supertrend 3-Phase strategy page — pilot #2 of the StrategyConfig
framework, deliberately chosen as the "hard case": it needs per-episode
extra chart kwargs (st_period/st_multiplier vary per row, unlike every
other strategy), a variant-merge/dedup step that RESHAPES rows before
filtering, and an extra styling rule beyond the generic +/- pct coloring.
All three of those needs are met through the framework's existing
extension points (extra_kwargs_fn, apply_filters doing its own row
reshaping, extra_style_fn) - no new business logic, all calculations
copied verbatim from the original Supertrend section of appclaude.py.
"""
import pandas as pd
import streamlit as st

import dashboard_core as core
from strategy_framework import StrategyConfig

DESCRIPTION = (
    "Phase 1: Supertrend BUY, green line ≥ 200 EMA when SELL triggers → highest High = X. "
    "Phase 2: Supertrend SELL, must touch/cross below 200 EMA at least once → lowest Low = Y, lowest 200 EMA = Z. "
    "Not shown until Phase 3's ST line has crossed the 200 EMA (Phase 2, and pre-cross Phase 3, aren't actionable). "
    "Phase 3: ST flips BUY; once its line crosses ≥ 200 EMA the episode is shown, watching for ST line > X; "
    "episode completes once both have happened. "
    "Pullbacks to X / Y / Z are all buy-on-support entries."
)

STATUS_LEGEND_MD = """
- **🟠 ST crossed 200 EMA – awaiting X clear** — Phase 3 is live but the ST
  line hasn't cleared X (Phase 1 high) yet.
- **🟢 Complete** — ST line has cleared X. Episode fully resolved; X, Y, Z
  are all tracked afterward the same way as every other pattern's levels —
  dates came later — the point all three genuinely became live together.
- Each level individually tracked: **⚪ Naked** / **🟢 Tested** / **🔴 Failed**
- Per level: max run-up % before it was tested/failed (or naked run-up so
  far), days tracked, and — once tested or failed — post-event drawdown %
  and days to recover.
- **Supertrend variant** — the (period, multiplier) shown in each row. You
  can enable multiple variants in the sidebar to see all of them
  simultaneously in one table.
- **Z (Lowest 200 EMA)** sits between X (Phase 1 high) and Y (Phase 2 price
  low) — it is the 200 EMA's own floor during the correction, and often the
  most reliable near-term support on the first bounce.
"""

ST_EP_STATUS_LABELS = {
    "phase3_pending": "🟠 ST crossed 200 EMA – awaiting X clear",
    "complete": "🟢 Complete",
}
LEVEL_STATUS_ICONS = {"naked": "⚪", "tested": "🟢", "failed": "🔴"}

STATUS_COLOR_MAP = {
    "🟠 ST crossed 200 EMA – awaiting X clear": "background-color:#fdebd0; color:#8a5a00; font-weight:600",
    "🟢 Complete": "background-color:#d1f2eb; color:#0b6b4f; font-weight:600",
}

DISPLAY_COLUMNS = {
    "symbol": "Symbol",
    "variant": "ST Variant(s)",
    "variant_count": "# Variants",
    "phase1_start": "Phase 1 Start",
    "signal_date": "Signal Date",
    "x_cleared_date": "X Cleared",
    "status_label": "Status",
    "x_price": "X (P1 High)",
    "y_price": "Y (P2 Low)",
    "z_price": "Z (Low 200EMA)",
    "current_price": "Current Price",
    "%_from_x": "% From X",
    "%_from_y": "% From Y",
    "%_from_z": "% From Z",
    "x_icon": "X Status",
    "y_icon": "Y Status",
    "z_icon": "Z Status",
    "x_max_runup_pct": "X Max Run-up %",
    "y_max_runup_pct": "Y Max Run-up %",
    "z_max_runup_pct": "Z Max Run-up %",
    "x_dd_display": "X Post-Test Drawdown",
    "y_dd_display": "Y Post-Test Drawdown",
    "z_dd_display": "Z Post-Test Drawdown",
}

FORMAT_SPEC = {
    "Phase 1 Start": lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
    "Signal Date": lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
    "X Cleared": lambda d: d.strftime("%d-%b-%Y") if pd.notna(d) else "—",
    "X (P1 High)": "₹{:.2f}",
    "Y (P2 Low)": "₹{:.2f}",
    "Z (Low 200EMA)": "₹{:.2f}",
    "Current Price": "₹{:.2f}",
    "% From X": "{:+.2f}%",
    "% From Y": "{:+.2f}%",
    "% From Z": "{:+.2f}%",
    "X Max Run-up %": "{:.1f}%",
    "Y Max Run-up %": "{:.1f}%",
    "Z Max Run-up %": "{:.1f}%",
}


def _st_status_label(row):
    return ST_EP_STATUS_LABELS.get(row["status"], row["status"])


def _load_data():
    df = core.load_supertrend_ledger()
    if df.empty:
        return df
    df = df.copy()
    for col in ["phase1_start", "signal_date", "x_cleared_date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
    return df


def _apply_filters(raw_df):
    """
    Verbatim port of the original Supertrend section's post-load pipeline:
    %_from_x/y/z, per-level underwater flags + drawdown-display strings,
    variant labeling, the optional variant-merge/dedup step (reshapes
    rows — this is why it lives here rather than in a generic framework
    hook), then the interactive filter widgets.
    """
    df = raw_df.copy()
    df["%_from_x"] = (df["current_price"] - df["x_price"]) / df["x_price"] * 100
    df["%_from_y"] = (df["current_price"] - df["y_price"]) / df["y_price"] * 100
    df["%_from_z"] = (df["current_price"] - df["z_price"]) / df["z_price"] * 100

    for lvl in ("x", "y", "z"):
        status_col = f"{lvl}_status"
        pct_col = f"%_from_{lvl}"
        was_tested = df[status_col].isin(["tested", "failed"])
        underwater_now = was_tested & (df[pct_col] < 0)
        df[f"{lvl}_underwater"] = underwater_now

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

        df[f"{lvl}_dd_display"] = df.apply(_fmt_dd, axis=1)

    df["any_underwater"] = df[["x_underwater", "y_underwater", "z_underwater"]].any(axis=1)
    df["variant"] = df.apply(lambda r: f"({int(r['st_period'])}, {float(r['st_multiplier']):.1f})", axis=1)
    df["x_icon"] = df["x_status"].map(LEVEL_STATUS_ICONS).fillna("—")
    df["y_icon"] = df["y_status"].map(LEVEL_STATUS_ICONS).fillna("—")
    df["z_icon"] = df["z_status"].map(LEVEL_STATUS_ICONS).fillna("—")

    ST_STATUS_OPTIONS = ["All", "🟠 ST crossed 200 EMA – awaiting X clear", "🟢 Complete"]

    # Commonly-used filters stay immediately visible; the variant-merge
    # toggle, specific-variant picker, and underwater-only filter are used
    # less often, so they live behind an expander to keep the common path
    # (Status/Symbol/Band, then straight to the table) shorter to scroll
    # past. The underlying merge/dedup and filtering logic is unchanged —
    # only where each widget is drawn moved.
    c1, c2, c3 = st.columns(3)
    status_filter = c1.selectbox("Status", ST_STATUS_OPTIONS, index=0, key="st_status")
    symbol_filter = c2.text_input("Filter by symbol", "", key="st_symbol").upper().strip()
    band = c3.slider("Within % of current price (any of X/Y/Z)", 1, 50, 20, key="st_band")

    with st.expander("⚙️ Advanced filters (variant merge, specific variant, underwater-only)"):
        st.caption(
            "Different ST variants (e.g. (7,3) and (10,3)) often flip on the exact same day for the "
            "same stock, since X/Y/Z come from price and 200 EMA — not from the ST math itself."
        )
        merge_variants = st.checkbox(
            "Merge duplicate signals across ST variants (recommended)", value=True, key="st_merge_variants",
        )

        if merge_variants:
            g = df

            def _sort_variant_str(v: str) -> tuple:
                return tuple(float(x) for x in v.strip("()").split(", "))

            variant_join = g.groupby(["symbol", "phase1_start"])["variant"].transform(
                lambda s: " · ".join(sorted(s.unique().tolist(), key=_sort_variant_str))
            )
            variant_count = g.groupby(["symbol", "phase1_start"])["variant"].transform("nunique")

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

            df = g.loc[keep_idx].copy()
            df["variant"] = variant_join.loc[keep_idx]
            df["variant_count"] = variant_count.loc[keep_idx]
            df = df.reset_index(drop=True)
        else:
            df["variant_count"] = 1

        variant_filter = st.selectbox("Variant", ["All"] + sorted(df["variant"].unique().tolist()), key="st_variant")
        underwater_only = st.checkbox(
            "⚠️ Only show rows where a tested/failed level is CURRENTLY still below its reference price "
            "(not yet recovered)", value=False, key="st_underwater_only",
        )

    view = df.dropna(subset=["current_price"]).copy()
    if status_filter != "All":
        view = view[view["status_label"] == status_filter]
    if symbol_filter:
        view = view[view["symbol"].str.contains(symbol_filter)]
    if variant_filter != "All":
        if merge_variants:
            view = view[view["variant"].apply(lambda v: variant_filter in v.split(" · "))]
        else:
            view = view[view["variant"] == variant_filter]
    if underwater_only:
        view = view[view["any_underwater"]]
    view = view[
        (view["%_from_x"].abs() <= band) | (view["%_from_y"].abs() <= band) | (view["%_from_z"].abs() <= band)
    ]
    view["_closest"] = view[["%_from_x", "%_from_y", "%_from_z"]].abs().min(axis=1)
    view = view.sort_values("_closest").reset_index(drop=True)
    return view


def _extra_style(styled):
    return styled.map(
        lambda v: "color:#b91c1c;font-weight:600" if isinstance(v, str) and "still below" in v
        else ("color:#1a7f37;font-weight:600" if isinstance(v, str) and ("recovered" in v or "held" in v) else ""),
        subset=["X Post-Test Drawdown", "Y Post-Test Drawdown", "Z Post-Test Drawdown"],
    )


def _single_chart_metrics(ep):
    def _runup_delta(days_val):
        return f"{int(days_val)}d tracked" if pd.notna(days_val) else None

    return [
        [
            ("Current Price", f"₹{ep['current_price']:.2f}" if pd.notna(ep.get('current_price')) else "—", None),
            ("X (Phase 1 High)", f"₹{ep['x_price']:.2f}" if pd.notna(ep.get('x_price')) else "—",
             f"{ep['%_from_x']:+.2f}%" if pd.notna(ep.get('%_from_x')) else None),
            ("Y (Phase 2 Low)", f"₹{ep['y_price']:.2f}" if pd.notna(ep.get('y_price')) else "—",
             f"{ep['%_from_y']:+.2f}%" if pd.notna(ep.get('%_from_y')) else None),
            ("Z (Lowest 200 EMA)", f"₹{ep['z_price']:.2f}" if pd.notna(ep.get('z_price')) else "—",
             f"{ep['%_from_z']:+.2f}%" if pd.notna(ep.get('%_from_z')) else None),
            ("Status", ep.get("status_label", "—"), None),
        ],
        [
            ("X Level", f"{ep.get('x_icon', '—')} {ep.get('x_status', '—')}", None),
            ("Y Level", f"{ep.get('y_icon', '—')} {ep.get('y_status', '—')}", None),
            ("Z Level", f"{ep.get('z_icon', '—')} {ep.get('z_status', '—')}", None),
        ],
        [
            ("X Max Run-up (before retest)",
             f"{ep['x_max_runup_pct']:.1f}%" if pd.notna(ep.get('x_max_runup_pct')) else "—",
             _runup_delta(ep.get('x_days_tracked'))),
            ("Y Max Run-up (before retest)",
             f"{ep['y_max_runup_pct']:.1f}%" if pd.notna(ep.get('y_max_runup_pct')) else "—",
             _runup_delta(ep.get('y_days_tracked'))),
            ("Z Max Run-up (before retest)",
             f"{ep['z_max_runup_pct']:.1f}%" if pd.notna(ep.get('z_max_runup_pct')) else "—",
             _runup_delta(ep.get('z_days_tracked'))),
        ],
    ]


def _single_chart_caption(ep):
    ep_period_val = int(ep.get("st_period", 7))
    ep_mult_val = float(ep.get("st_multiplier", 3.0))
    return f"Supertrend ({ep_period_val}, {ep_mult_val}) · Phase 1 Start **{pd.to_datetime(ep['phase1_start']).strftime('%d-%b-%Y')}**"


def _multi_chart_caption(sym, ep):
    ep_period_val = int(ep.get("st_period", 7))
    ep_mult_val = float(ep.get("st_multiplier", 3.0))
    return (
        f"**{sym}**\n\n"
        f"({ep_period_val}, {ep_mult_val}) · "
        f"P1 {pd.to_datetime(ep['phase1_start']).strftime('%d-%b-%Y')} · "
        f"{ep.get('status_label', '')}"
    )


def _extra_kwargs(ep):
    return {
        "st_period": int(ep.get("st_period", 7)),
        "st_multiplier": float(ep.get("st_multiplier", 3.0)),
    }


CONFIG = StrategyConfig(
    key="supertrend",
    title="📊 Supertrend 3-Phase Pattern",
    description=DESCRIPTION,
    status_legend_md=STATUS_LEGEND_MD,
    load_data=_load_data,
    apply_filters=_apply_filters,
    status_label_fn=_st_status_label,
    status_color_map=STATUS_COLOR_MAP,
    display_columns=DISPLAY_COLUMNS,
    numeric_pct_cols=["% From X", "% From Y", "% From Z"],
    format_spec=FORMAT_SPEC,
    chart_builder=core.build_supertrend_chart,
    single_chart_metrics_fn=_single_chart_metrics,
    single_chart_caption_fn=_single_chart_caption,
    multi_chart_caption_fn=_multi_chart_caption,
    extra_kwargs_fn=_extra_kwargs,
    extra_style_fn=_extra_style,
)
