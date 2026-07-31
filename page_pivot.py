"""page_pivot.py — Monthly Pivot S1, rendered via the StrategyPage framework."""
import streamlit as st

from app_globals import load_globals
from strategy_framework import render_strategy_page
from strategy_pivot import CONFIG


def render():
    g = load_globals()
    render_strategy_page(CONFIG, g["merged"])
