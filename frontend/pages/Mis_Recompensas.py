from datetime import datetime
from html import escape

import streamlit as st

from components.sidebar import render_sidebar, _redirect_to_login
from utils.api_client import api_get
from utils.state import init_session_state


st.set_page_config(
    page_title="ChargeCore | Mis Recompensas",
    page_icon="🎁",
    layout="wide",
    initial_sidebar_state="expanded",
)


def error_message(result):
    status_code = result.get("_status_code") if isinstance(result, dict) else None
    if status_code == 401:
        return "Tu sesión ha expirado. Inicia sesión nuevamente."
    if status_code == 403:
        return "No tienes permisos para acceder a esta sección."
    if status_code == 404:
        return "No se encontró la información solicitada."
    if isinstance(status_code, int) and status_code >= 500:
        return "No fue posible cargar tus recompensas. Intenta nuevamente más tarde."
    return "No fue posible cargar tus recompensas. Intenta nuevamente más tarde."


def display_value(value):
    return "—" if value is None or value == "" else str(value)


def format_date(value):
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.strftime("%d %b %Y, %H:%M")
    except (TypeError, ValueError):
        return display_value(value)


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
    .rewards-header {
        border-bottom: 1px solid #e2e8f0;
        margin-bottom: 1.5rem;
        padding-bottom: 1rem;
    }
    .reward-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        margin-bottom: 1rem;
        padding: 1.2rem 1.3rem;
    }
    .reward-label {
        color: #64748b;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }
    .reward-value {
        color: #334155;
        font-size: 0.92rem;
        margin: 0.15rem 0 0.75rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="rewards-header"><h1>Mis Recompensas</h1>'
    '<p>Consulta las recompensas otorgadas a tu cuenta.</p></div>',
    unsafe_allow_html=True,
)

with st.spinner("Cargando tus recompensas..."):
    result = api_get("/client/me/rewards")

if isinstance(result, dict) and "error" in result:
    st.error(error_message(result))
    st.stop()

rewards = result.get("recompensas", []) if isinstance(result, dict) else []
rewards = rewards if isinstance(rewards, list) else []
used_count = sum(1 for reward in rewards if reward.get("usada") is True)
expired_count = sum(
    1
    for reward in rewards
    if reward.get("usada") is not True and reward.get("estado") == "Vencida"
)
pending_count = sum(1 for reward in rewards if reward.get("estado") == "Pendiente")
available_count = sum(
    1 for reward in rewards if reward.get("estado") == "Disponible"
)

summary = st.columns(5)
with summary[0]:
    st.metric("Total", len(rewards))
with summary[1]:
    st.metric("Disponibles", available_count)
with summary[2]:
    st.metric("Usadas", used_count)
with summary[3]:
    st.metric("Pendientes", pending_count)
with summary[4]:
    st.metric("Vencidas", expired_count)

st.divider()

if not rewards:
    st.info(
        "Aún no tienes recompensas.\n\n"
        "Cuando alcances las condiciones de una recompensa, aparecerá aquí."
    )
    st.stop()

for reward in rewards:
    used = reward.get("usada") is True
    status = reward.get("estado") or ("Usada" if used else "Disponible")
    status_foreground, status_background = (
        ("#475569", "#f1f5f9")
        if used
        else ("#92400e", "#fef3c7")
        if status in {"Vencida", "Pendiente"}
        else ("#166534", "#dcfce7")
    )
    with st.container(border=True):
        st.markdown(
            f'<div class="reward-card"><h3>{escape(display_value(reward.get("nombre")))}</h3>'
            f'<span style="background:{status_background}; color:{status_foreground}; '
            f'border-radius:999px; padding:0.25rem 0.65rem; font-size:0.78rem; '
            f'font-weight:700;">{status}</span></div>',
            unsafe_allow_html=True,
        )
        columns = st.columns(2)
        with columns[0]:
            st.markdown('<div class="reward-label">Empresa / establecimiento</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="reward-value">{escape(display_value(reward.get("empresa")))}</div>', unsafe_allow_html=True)
            st.markdown('<div class="reward-label">Descripción</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="reward-value">{escape(display_value(reward.get("descripcion")))}</div>', unsafe_allow_html=True)
            st.markdown('<div class="reward-label">Condiciones</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="reward-value">{escape(display_value(reward.get("condiciones")))}</div>', unsafe_allow_html=True)
        with columns[1]:
            st.markdown('<div class="reward-label">Cargas</div>', unsafe_allow_html=True)
            required = display_value(reward.get("cargas_requeridas"))
            accumulated = display_value(reward.get("cargas_acumuladas"))
            st.markdown(f'<div class="reward-value">{escape(accumulated)} / {escape(required)}</div>', unsafe_allow_html=True)
            st.markdown('<div class="reward-label">Fecha de otorgamiento</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="reward-value">{escape(format_date(reward.get("fecha_otorgamiento")))}</div>', unsafe_allow_html=True)
            st.markdown('<div class="reward-label">Vigencia</div>', unsafe_allow_html=True)
            validity = f"{format_date(reward.get('fecha_inicio'))} - {format_date(reward.get('fecha_finalizacion'))}"
            st.markdown(f'<div class="reward-value">{escape(validity)}</div>', unsafe_allow_html=True)
            st.markdown('<div class="reward-label">Fecha de uso</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="reward-value">{escape(format_date(reward.get("fecha_uso")))}</div>', unsafe_allow_html=True)
