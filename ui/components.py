"""Reusable interface primitives for the Streamlit workspace."""

from __future__ import annotations

from html import escape

import streamlit as st


def page_header(section: str, title: str) -> None:
    """Render the shared eyebrow and title hierarchy for a routed page."""
    st.markdown(
        '<header class="brl-page-heading">'
        f'<div class="brl-page-heading__eyebrow">{escape(section)}</div>'
        f'<h1 class="brl-page-heading__title">{escape(title)}</h1>'
        '</header>',
        unsafe_allow_html=True,
    )


def section_heading(title: str) -> None:
    """Render a compact, consistent section heading."""
    st.markdown(
        f'<h2 class="brl-section-heading">{escape(title)}</h2>',
        unsafe_allow_html=True,
    )
