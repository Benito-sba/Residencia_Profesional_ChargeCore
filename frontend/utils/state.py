import os

import streamlit as st


def default_api_base_url():
    api_url = os.getenv("CHARGECORE_API_URL", "").strip().rstrip("/")
    if not api_url:
        return "http://localhost:8000/api"
    if not api_url.startswith(("http://", "https://")):
        api_url = f"https://{api_url}"
    if not api_url.endswith("/api"):
        api_url = f"{api_url}/api"
    return api_url


def init_session_state():
    defaults = {
        "authenticated": False,
        "token": None,
        "user": None,
        "current_page": "dashboard",
        "theme": "light",
        "api_base_url": default_api_base_url(),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value