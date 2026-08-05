"""page_range_breakout.py — Range Breakout (5-Leg), rendered via StrategyPage framework."""
import streamlit as st

from app_globals import load_globals
from strategy_framework import render_strategy_page
from strategy_range_breakout import CONFIG


def render():
    g = load_globals()
    render_strategy_page(CONFIG, g["merged"])
