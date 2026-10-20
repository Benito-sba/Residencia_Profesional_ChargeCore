from html import escape

import streamlit as st

from components.sidebar import render_sidebar, _redirect_to_login
from utils.api_client import api_get
from utils.state import init_session_state


st.set_page_config(
    page_title="ChargeCore | Mi Perfil",
    page_icon="👤",
    layout="centered",
    initial_sidebar_state="expanded",
)


def client_error(result):
    status_code = result.get("_status_code") if isinstance(result, dict) else None
    if status_code == 401:
        return "Tu sesión ha expirado. Vuelve a iniciar sesión."
    if status_code == 403:
        return "No tienes permisos para acceder a esta sección."
    if status_code == 404:
        return "No se encontró la información solicitada."
    if isinstance(status_code, int) and status_code >= 500:
        return "No fue posible cargar la información. Intenta nuevamente."
    return result.get("error", "No fue posible cargar la información. Intenta nuevamente.")


init_session_state()

if not st.session_state.get("authenticated"):
    _redirect_to_login()

if (st.session_state.get("user") or {}).get("rol") != "cliente":
    st.error("No tienes permisos para acceder a esta sección.")
    st.stop()

render_sidebar()

st.markdown(
    """
    <style>
    .profile-header {
        border-bottom: 1px solid #e2e8f0;
        margin-bottom: 1.5rem;
        padding-bottom: 1rem;
    }
    .profile-panel {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1.4rem;
    }
    .profile-label {
        color: #64748b;
        font-size: 0.78rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }
    .profile-value {
        color: #0f172a;
        font-size: 1rem;
        margin: 0.2rem 0 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

profile = api_get("/client/me")
if isinstance(profile, dict) and "error" in profile:
    st.error(client_error(profile))
    st.stop()

st.markdown(
    '<div class="profile-header"><h1>Mi Perfil</h1>'
    '<p>Consulta la información asociada a tu cuenta.</p></div>',
    unsafe_allow_html=True,
)

with st.container(border=True):
    st.markdown('<div class="profile-label">Nombre</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="profile-value">{escape(str(profile.get("nombre") or "No disponible"))}</div>', unsafe_allow_html=True)

    st.markdown('<div class="profile-label">Correo electrónico</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="profile-value">{escape(str(profile.get("email") or "No disponible"))}</div>', unsafe_allow_html=True)

    st.markdown('<div class="profile-label">Teléfono</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="profile-value">{escape(str(profile.get("telefono") or "No registrado"))}</div>', unsafe_allow_html=True)

    st.markdown('<div class="profile-label">Estado</div>', unsafe_allow_html=True)
    status_label = "Activo" if profile.get("activo", False) else "Inactivo"
    st.markdown(f'<div class="profile-value">{status_label}</div>', unsafe_allow_html=True)

    st.markdown('<div class="profile-label">Total de cargas</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="profile-value">{profile.get("total_cargas", 0)}</div>', unsafe_allow_html=True)

    st.markdown('<div class="profile-label">Rol</div>', unsafe_allow_html=True)
    st.markdown('<div class="profile-value">Cliente</div>', unsafe_allow_html=True)
