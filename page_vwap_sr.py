"""page_vwap_sr.py — VWAP Support/Resistance (pilot), rendered via the StrategyPage framework."""
import streamlit as st

from app_globals import load_globals
from strategy_framework import render_strategy_page
from strategy_vwap_sr import CONFIG


def render():
    g = load_globals()
    render_strategy_page(CONFIG, g["merged"])
