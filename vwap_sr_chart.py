"""
vwap_sr_chart.py
-------------------
Chart builder for the VWAP Support/Resistance strategy.

Two-panel chart:
  Top    - hourly candles around Day D (+/- 14 sessions), with session
           VWAP and the relevant 1-SD band (lower for lower-band episodes,
           upper for upper-band episodes) overlaid. Day D's first hour
           is shaded.
  Bottom - daily candles for the selected period, with X marked as a
           horizontal line (green if currently support, red if resistance).
"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def build_vwap_sr_chart(daily_hist: pd.DataFrame, symbol: str, ep_row: pd.Series,
                          daily_period: str, hourly_df: pd.DataFrame,
                          session_summary: pd.DataFrame) -> go.Figure:
    day_d = pd.to_datetime(ep_row["day_d_date"])
    x_price = ep_row["x_price"]
    classification = ep_row.get("classification", "support")
    is_upper = ep_row.get("episode_type") == "upper_band"
    band_col = "upper_band_close" if is_upper else "lower_band_close"
    band_color = "#ef5350" if is_upper else "#29b6f6"
    band_name = "Upper Band" if is_upper else "Lower Band"

    fig = make_subplots(
        rows=2, cols=1, row_heights=[0.45, 0.55], vertical_spacing=0.08,
        subplot_titles=(
            f"Hourly - around Day D ({day_d.strftime('%d-%b-%Y')}) · {band_name}",
            "Daily - X tracked forward",
        ),
    )

    # ---- Top: hourly window around Day D ----
    if not hourly_df.empty:
        window_start = day_d - pd.Timedelta(days=14)
        window_end = day_d + pd.Timedelta(days=14)
        h = hourly_df.loc[(hourly_df.index >= window_start) & (hourly_df.index <= window_end)]
        if not h.empty:
            fig.add_trace(
                go.Candlestick(
                    x=h.index, open=h["Open"], high=h["High"], low=h["Low"], close=h["Close"],
                    name="Hourly Price", showlegend=False,
                ),
                row=1, col=1,
            )
            ss_window = session_summary.loc[
                (session_summary.index >= window_start) & (session_summary.index <= window_end)
            ]
            for dt, row in ss_window.iterrows():
                day_start = pd.Timestamp(dt)
                day_end = day_start + pd.Timedelta(hours=10)
                fig.add_trace(
                    go.Scatter(x=[day_start, day_end], y=[row["vwap_close"], row["vwap_close"]],
                               mode="lines", line=dict(color="#c2185b", width=1.5),
                               showlegend=False, hoverinfo="skip"),
                    row=1, col=1,
                )
                fig.add_trace(
                    go.Scatter(x=[day_start, day_end], y=[row[band_col], row[band_col]],
                               mode="lines", line=dict(color=band_color, width=1.5),
                               showlegend=False, hoverinfo="skip"),
                    row=1, col=1,
                )
            fh_start = day_d + pd.Timedelta(hours=9, minutes=15)
            fh_end = day_d + pd.Timedelta(hours=10, minutes=15)
            fig.add_vrect(x0=fh_start, x1=fh_end, fillcolor="orange", opacity=0.15,
                          line_width=0, row=1, col=1)

    # ---- Bottom: daily view with X tracked forward ----
    d = daily_hist
    if daily_period != "max":
        cutoff_days = {"1mo": 30, "3mo": 90, "6mo": 182, "1y": 365, "2y": 730}.get(daily_period, 182)
        d = d.loc[d.index >= (day_d - pd.Timedelta(days=cutoff_days))]
    fig.add_trace(
        go.Candlestick(
            x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
            name="Daily Price", showlegend=False,
        ),
        row=2, col=1,
    )
    line_color = "#1a7f37" if classification == "support" else "#b91c1c"
    fig.add_hline(
        y=x_price, line_dash="dot", line_color=line_color, line_width=2,
        annotation_text=f"X = ₹{x_price:.2f} · {'🟢 support' if classification == 'support' else '🔴 resistance'} ({band_name})",
        annotation_position="top left", row=2, col=1,
    )

    fig.update_layout(
        height=760,
        margin=dict(l=70, r=20, t=50, b=10),
        xaxis_rangeslider_visible=False,
        xaxis2_rangeslider_visible=False,
        showlegend=False,
        hovermode="x unified",
    )
    fig.update_yaxes(tickprefix="₹", tickformat=",.2f")
    return fig
