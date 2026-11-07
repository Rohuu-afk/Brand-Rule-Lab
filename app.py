"""
app.py
------
Brand Rule Lab — Streamlit entrypoint.

Run locally:
    streamlit run app.py

The app:
  - applies the database schema and seeds demo data on first launch
  - renders the sidebar navigation
  - routes to the selected page module
"""

import sys
import logging
from pathlib import Path

logger = logging.getLogger("brand_rule_lab.app")

# Ensure project root is on sys.path (needed when run from subdirs)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

# Apply schema + seed on initial server startup (seed applies migrations once).
from db.seed import seed


@st.cache_resource
def _initialize_database() -> bool:
    seed(force=False, verbose=False)
    return True


_initialize_database()

# Navigation
from ui.navigation import render_sidebar, PAGE_MODULE
from ui.styles import inject_styles

st.set_page_config(
    page_title="Brand Rule Lab",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded",
)

role, page_label = render_sidebar()

# Dynamic page routing
import importlib
import inspect

module_path = PAGE_MODULE.get(page_label)
if module_path:
    try:
        module = importlib.import_module(module_path)
        # Pass active role to pages that accept it (facts, review_results, etc.)
        sig = inspect.signature(module.render)
        if "role" in sig.parameters:
            module.render(role=role)
        else:
            module.render()
    except Exception as exc:
        logger.exception("Page rendering failed for %s", module_path)
        st.error("This page could not be loaded. Please try again or contact the workspace administrator.")
else:
    st.error(f"Page not found: {page_label!r}")
