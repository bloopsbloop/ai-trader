"""Standalone local dashboard: reads only the Batch 3 database."""
import streamlit as st
from app.dashboard.ai_pages import ml_shadow_page, news_page, scanner_page

st.set_page_config(page_title="Batch 3 PAPER", layout="wide")
st.title("Batch 3 — Local PAPER Trading")
st.button("Refresh")
pages = {"Scanner & portfolios": scanner_page, "News": news_page, "ML Shadow": ml_shadow_page}
pages[st.sidebar.radio("Page", list(pages))](st)
