from html import escape

import streamlit as st

from components.sidebar import render_sidebar, _redirect_to_login
from utils.api_client import api_get
from utils.state import init_session_state


st.set_page_config(
    page_title="ChargeCore | Inicio",
    page_icon="🏠",
    layout="wide",
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
    .client-header {
        border-bottom: 1px solid #e2e8f0;
        margin-bottom: 1.5rem;
        padding-bottom: 1rem;
    }
    .client-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        min-height: 9rem;
        padding: 1.2rem;
    }
    .client-card h3 {
        color: #0f172a;
        margin-bottom: 0.45rem;
    }
    .client-card p {
        color: #64748b;
        margin-bottom: 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

user = st.session_state.get("user") or {}
profile = api_get("/client/me")
vehicles = api_get("/client/me/vehicles")

if isinstance(profile, dict) and "error" in profile:
    st.error(client_error(profile))
    st.stop()
if isinstance(vehicles, dict) and "error" in vehicles:
    st.error(client_error(vehicles))
    st.stop()

name = profile.get("nombre") or user.get("nombre") or "Cliente"
safe_name = escape(str(name))
active_vehicles = vehicles if isinstance(vehicles, list) else []

st.markdown(
    f'<div class="client-header"><h1>Inicio</h1><p>Bienvenido, {safe_name}</p></div>',
    unsafe_allow_html=True,
)

vehicle_column, charges_column = st.columns(2)
with vehicle_column:
    st.markdown(
        f'<div class="client-card"><h3>Mis Vehículos</h3>'
        f'<p>{len(active_vehicles)} {"vehículo activo asociado" if len(active_vehicles) == 1 else "vehículos activos asociados"} a tu cuenta.</p></div>',
        unsafe_allow_html=True,
    )
    if st.button("Ver mis vehículos", type="primary", use_container_width=True):
        st.session_state.current_page = "mis_vehiculos"
        st.switch_page("pages/Mis_Vehiculos.py")

with charges_column:
    st.markdown(
        '<div class="client-card"><h3>Mis Cargas</h3>'
        '<p>Consulta tu historial de sesiones y consumo.</p></div>',
        unsafe_allow_html=True,
    )
    if st.button("Ver mis cargas", use_container_width=True):
        st.switch_page("pages/Mis_Cargas.py")

rewards_column, active_column = st.columns(2)
with rewards_column:
    st.markdown(
        '<div class="client-card"><h3>Mis Recompensas</h3>'
        '<p>Consulta tus recompensas, vigencia y canjes.</p></div>',
        unsafe_allow_html=True,
    )
    if st.button("Ver mis recompensas", use_container_width=True):
        st.switch_page("pages/Mis_Recompensas.py")
with active_column:
    st.markdown(
        '<div class="client-card"><h3>Carga Activa</h3>'
        '<p>Consulta métricas, selecciona recompensa y detén tu carga.</p></div>',
        unsafe_allow_html=True,
    )
    if st.button("Ver carga activa", use_container_width=True):
        st.switch_page("pages/Carga_Activa.py")
