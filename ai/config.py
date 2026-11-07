"""Provider configuration from environment variables and Streamlit secrets."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv


_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)


def _read_streamlit_secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
    except FileNotFoundError:
        value = default
    return default if value is None else str(value).strip()


def get_config_value(name: str, default: str = "") -> str:
    """Return a non-empty environment value, falling back to a root-level secret."""
    value = os.getenv(name)
    if value and value.strip():
        return value.strip()
    return _read_streamlit_secret(name, default)
