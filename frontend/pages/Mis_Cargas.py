from datetime import datetime
from html import escape

import streamlit as st

from components.sidebar import render_sidebar, _redirect_to_login
from utils.api_client import api_get, api_post
from utils.state import init_session_state


st.set_page_config(
    page_title="ChargeCore | Mis Cargas",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)


def api_error_message(result):
    status_code = result.get("_status_code") if isinstance(result, dict) else None
    if status_code == 401:
        return "Tu sesión expiró. Inicia sesión nuevamente."
    if status_code == 403:
        return "No tienes permiso para consultar tus cargas."
    if status_code == 404:
        return "No se encontró la información solicitada."
    if isinstance(status_code, int) and status_code >= 500:
        return "No fue posible cargar tu historial en este momento."
    return "No fue posible cargar tu historial en este momento."


def display_value(value, fallback="—"):
    if value is None or value == "":
        return fallback
    return str(value)


def format_date(value):
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.strftime("%d %b %Y, %H:%M")
    except (TypeError, ValueError):
        return display_value(value)


def format_energy(value):
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f} kWh"
    except (TypeError, ValueError):
        return "—"


def format_duration(value):
    if value is None:
        return "—"
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return "—"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours and remaining_minutes:
        return f"{hours} h {remaining_minutes} min"
    if hours:
        return f"{hours} h"
    return f"{remaining_minutes} min"


def format_vehicle(vehicle):
    if not isinstance(vehicle, dict):
        return "No especificado"
    name = " ".join(
        value for value in (vehicle.get("marca"), vehicle.get("modelo")) if value
    )
    plate = vehicle.get("placa")
    if name and plate:
        return f"{name} · {plate}"
    return name or plate or "No especificado"


def status_style(status):
    colors = {
        "finalizada": ("#166534", "#dcfce7"),
        "activa": ("#1d4ed8", "#dbeafe"),
        "iniciada": ("#92400e", "#fef3c7"),
    }
    return colors.get(str(status).lower(), ("#475569", "#f1f5f9"))


init_session_state()

if not st.session_state.get("authenticated"):
    _redirect_to_login()

if (st.session_state.get("user") or {}).get("rol") != "cliente":
    st.error("No tienes permiso para consultar tus cargas.")
    st.stop()

render_sidebar()

st.markdown(
    """
    <style>
    .sessions-header {
        border-bottom: 1px solid #e2e8f0;
        margin-bottom: 1.5rem;
        padding-bottom: 1rem;
    }
    .session-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        margin-bottom: 1rem;
        padding: 1.15rem 1.25rem;
    }
    .session-card-title {
        color: #0f172a;
        font-size: 1.08rem;
        font-weight: 750;
        margin-bottom: 0.7rem;
    }
    .session-detail-label {
        color: #64748b;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }
    .session-detail-value {
        color: #334155;
        font-size: 0.92rem;
        margin: 0.15rem 0 0.7rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="sessions-header"><h1>Mis Cargas</h1>'
    '<p>Identifica tu tarjeta, elige el vehículo y controla tus sesiones de carga.</p></div>',
    unsafe_allow_html=True,
)

st.markdown("### Iniciar una carga")
st.info(
    "Conecta el vehículo y acerca tu tarjeta NFC al cargador. "
    "La tarjeta solo identifica tu cuenta; la carga no empieza hasta que la confirmes aquí."
)

if st.button("🔄 Buscar tarjeta acercada", key="refresh_charge_requests"):
    st.rerun()

charge_requests = api_get("/client/me/charge-requests")
if isinstance(charge_requests, dict) and "error" in charge_requests:
    st.error(api_error_message(charge_requests))
    charge_requests = []

if not charge_requests:
    st.caption("Cuando acerques una tarjeta asignada a tu cuenta, aparecerá aquí por tres minutos.")
else:
    vehicles = api_get("/client/me/vehicles")
    if isinstance(vehicles, dict) and "error" in vehicles:
        st.error("No fue posible cargar tus vehículos. Intenta nuevamente.")
        vehicles = []
    vehicles = vehicles if isinstance(vehicles, list) else []

    for charge_request in charge_requests:
        with st.container(border=True):
            charger_name = display_value(
                charge_request.get("cargador")
                or charge_request.get("charge_point_id")
            )
            st.markdown(f"**Tarjeta detectada · {escape(charger_name)}**")
            if charge_request.get("status") == "start_requested":
                st.info(
                    "La orden fue enviada. Esperando que el cargador confirme el inicio; "
                    "actualiza esta pantalla en unos segundos."
                )
                continue
            if not vehicles:
                st.warning("Agrega un vehículo en **Mis Vehículos** antes de iniciar una carga.")
                continue

            connectors = [
                connector for connector in charge_request.get("conectores", [])
                if str(connector.get("estado", "Available")).lower()
                in {"available", "disponible", "preparing", "preparando"}
            ]
            if not connectors:
                st.warning("El cargador no reporta conectores disponibles.")
                continue

            vehicle = st.selectbox(
                "Selecciona el vehículo",
                vehicles,
                format_func=lambda item: format_vehicle(item),
                key=f"charge_vehicle_{charge_request['id']}",
            )
            connector = st.selectbox(
                "Selecciona el conector",
                connectors,
                format_func=lambda item: (
                    f"Conector {item['id']} · {item.get('tipo') or 'Tipo no indicado'}"
                ),
                key=f"charge_connector_{charge_request['id']}",
            )
            if st.button(
                "⚡ Confirmar e iniciar carga",
                key=f"start_charge_{charge_request['id']}",
                type="primary",
            ):
                vehicle_id = vehicle.get("id") or vehicle.get("_id")
                result = api_post(
                    f"/client/me/charge-requests/{charge_request['id']}/start",
                    json={
                        "vehicle_id": vehicle_id,
                        "connector_id": connector["id"],
                    },
                )
                if "error" in result:
                    st.error(result["error"])
                else:
                    st.success("Orden enviada al cargador. Se actualizará al comenzar la sesión.")
                    st.rerun()

with st.spinner("Cargando tus cargas..."):
    sessions = api_get("/client/me/sessions")

if isinstance(sessions, dict) and "error" in sessions:
    st.error(api_error_message(sessions))
    st.stop()

sessions = sessions if isinstance(sessions, list) else []

if not sessions:
    st.info("Aún no tienes cargas registradas.\n\nCuando realices tu primera carga, aparecerá aquí tu historial.")
    st.stop()

energy_total = sum(
    float(session["energia_consumida_kwh"])
    for session in sessions
    if session.get("energia_consumida_kwh") is not None
)
active_count = sum(
    1 for session in sessions
    if str(session.get("estado", "")).lower() in {"activa", "iniciada"}
)
latest_date = format_date(sessions[0].get("fecha_inicio"))

summary_columns = st.columns(4)
with summary_columns[0]:
    st.metric("Total de cargas", len(sessions))
with summary_columns[1]:
    st.metric("Energía consumida", f"{energy_total:.2f} kWh")
with summary_columns[2]:
    st.metric("Última carga", latest_date)
with summary_columns[3]:
    st.metric("Cargas activas", active_count)

st.divider()
st.markdown("### Historial")

for session in sessions:
    status = display_value(session.get("estado"))
    status_foreground, status_background = status_style(status)
    charger = display_value(session.get("cargador") or session.get("charge_point_id"))
    benefit = display_value(session.get("beneficio_aplicado"))
    vehicle = format_vehicle(session.get("vehiculo"))
    connector = display_value(session.get("connector_id"))

    with st.container(border=True):
        st.markdown(
            f'<div class="session-card-title">Cargador: {escape(charger)}</div>'
            f'<span style="background:{status_background}; color:{status_foreground}; '
            f'border-radius:999px; padding:0.25rem 0.65rem; font-size:0.78rem; '
            f'font-weight:700;">{escape(status)}</span>',
            unsafe_allow_html=True,
        )
        detail_columns = st.columns(2)
        with detail_columns[0]:
            st.markdown('<div class="session-detail-label">Conector</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="session-detail-value">{escape(connector)}</div>', unsafe_allow_html=True)
            st.markdown('<div class="session-detail-label">Vehículo</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="session-detail-value">{escape(vehicle)}</div>', unsafe_allow_html=True)
            st.markdown('<div class="session-detail-label">Fecha de inicio</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="session-detail-value">{escape(format_date(session.get("fecha_inicio")))}</div>', unsafe_allow_html=True)
        with detail_columns[1]:
            st.markdown('<div class="session-detail-label">Fecha de finalización</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="session-detail-value">{escape(format_date(session.get("fecha_fin")))}</div>', unsafe_allow_html=True)
            st.markdown('<div class="session-detail-label">Energía</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="session-detail-value">{escape(format_energy(session.get("energia_consumida_kwh")))}</div>', unsafe_allow_html=True)
            st.markdown('<div class="session-detail-label">Duración</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="session-detail-value">{escape(format_duration(session.get("duracion_minutos")))}</div>', unsafe_allow_html=True)
        st.markdown('<div class="session-detail-label">Beneficio</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="session-detail-value">{escape(benefit)}</div>', unsafe_allow_html=True)
