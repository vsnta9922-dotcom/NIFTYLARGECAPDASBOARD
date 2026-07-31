"""page_five_leg.py — 5-Leg EMA Reversal, rendered via the StrategyPage framework."""
import streamlit as st

from app_globals import load_globals
from strategy_framework import render_strategy_page
from strategy_five_leg import CONFIG


def render():
    g = load_globals()
    # min_leg_days has no explicit widget key, so it can't be looked up via
    # st.session_state — rebind the real value from load_globals() here,
    # each render, instead.
    min_leg_days_value = g["min_leg_days"]
    CONFIG.extra_kwargs_fn = lambda ep, _mld=min_leg_days_value: {"min_leg_days": int(_mld)}
    render_strategy_page(CONFIG, g["merged"])
