"""page_s1_shift.py — Monthly S1 Shift Up, rendered via the StrategyPage framework."""
import streamlit as st

from app_globals import load_globals
from strategy_framework import render_strategy_page
from strategy_s1_shift import CONFIG


def render():
    g = load_globals()
    render_strategy_page(CONFIG, g["merged"])
