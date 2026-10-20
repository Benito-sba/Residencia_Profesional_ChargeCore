from datetime import datetime, timezone
from html import escape

import streamlit as st

from components.sidebar import render_sidebar, _redirect_to_login
from utils.api_client import api_get, api_post
from utils.state import init_session_state


st.set_page_config(
    page_title="ChargeCore | Carga Activa",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)


def error_message(result):
    status_code = result.get("_status_code") if isinstance(result, dict) else None
    if status_code == 401:
        return "Tu sesión expiró. Inicia sesión nuevamente."
    if status_code == 403:
        return "No tienes permiso para consultar tu carga activa."
    if status_code == 404:
        return "No tienes una carga activa en este momento."
    if isinstance(status_code, int) and status_code >= 500:
        return "No fue posible consultar tu carga activa en este momento."
    return "No fue posible consultar tu carga activa en este momento."


def display_value(value):
    return "—" if value is None or value == "" else str(value)


def format_energy(value):
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f} kWh"
    except (TypeError, ValueError):
        return "—"


def format_power(value):
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f} kW"
    except (TypeError, ValueError):
        return "—"


def parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def format_datetime(value):
    parsed = parse_datetime(value)
    return parsed.strftime("%d %b %Y, %H:%M") if parsed else "—"


def elapsed_time(value):
    started_at = parse_datetime(value)
    if started_at is None:
        return "—"
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    seconds = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds()))
    minutes = seconds // 60
    hours, remaining_minutes = divmod(minutes, 60)
    if hours and remaining_minutes:
        return f"{hours} h {remaining_minutes} min"
    if hours:
        return f"{hours} h"
    return f"{remaining_minutes} min"


def format_vehicle(vehicle):
    if not isinstance(vehicle, dict):
        return "No especificado"
    name = " ".join(value for value in (vehicle.get("marca"), vehicle.get("modelo")) if value)
    plate = vehicle.get("placa")
    if name and plate:
        return f"{name} · {plate}"
    return name or plate or "No especificado"


init_session_state()

if not st.session_state.get("authenticated"):
    _redirect_to_login()

if (st.session_state.get("user") or {}).get("rol") != "cliente":
    st.error("No tienes permiso para consultar tu carga activa.")
    st.stop()

render_sidebar()

st.markdown(
    """
    <style>
    .active-header {
        border-bottom: 1px solid #e2e8f0;
        margin-bottom: 1.5rem;
        padding-bottom: 1rem;
    }
    .active-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1.35rem;
    }
    .active-label {
        color: #64748b;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }
    .active-value {
        color: #334155;
        font-size: 1rem;
        margin: 0.15rem 0 0.85rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="active-header"><h1>Carga Activa</h1>'
    '<p>Consulta el estado de tu carga en tiempo real.</p></div>',
    unsafe_allow_html=True,
)

with st.spinner("Consultando tu carga activa..."):
    result = api_get("/client/me/active-session")
rewards_result = api_get("/client/me/rewards")

if isinstance(result, dict) and "error" in result:
    st.error(error_message(result))
    st.stop()

if not isinstance(result, dict) or not result.get("active") or not result.get("session"):
    with st.container(border=True):
        st.info(
            "No tienes una carga activa en este momento.\n\n"
            "Cuando inicies una carga, podrás consultar aquí su estado, energía y tiempo transcurrido."
        )
    st.stop()

session = result["session"]
if isinstance(rewards_result, dict) and "error" in rewards_result:
    st.warning(
        "No se pudieron consultar las recompensas. Puedes detener la carga, "
        "pero no seleccionar una recompensa en este momento."
    )
    reward_options = []
else:
    reward_options = [
        reward
        for reward in rewards_result.get("recompensas", [])
        if reward.get("estado") == "Disponible"
        or reward.get("id") == session.get("selected_reward_id")
    ]

status = display_value(session.get("estado"))
status_colors = {
    "activa": ("#1d4ed8", "#dbeafe"),
    "iniciada": ("#92400e", "#fef3c7"),
}
foreground, background = status_colors.get(status.lower(), ("#475569", "#f1f5f9"))

with st.container(border=True):
    st.markdown(
        f'<span style="background:{background}; color:{foreground}; border-radius:999px; '
        f'padding:0.3rem 0.75rem; font-size:0.8rem; font-weight:700;">'
        f'{escape(status)}</span>',
        unsafe_allow_html=True,
    )
    st.markdown("### Información de la carga")
    info_columns = st.columns(3)
    with info_columns[0]:
        st.markdown('<div class="active-label">Cargador</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="active-value">{escape(display_value(session.get("cargador") or session.get("charge_point_id")))}</div>', unsafe_allow_html=True)
    with info_columns[1]:
        st.markdown('<div class="active-label">Conector</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="active-value">{escape(display_value(session.get("connector_id")))}</div>', unsafe_allow_html=True)
    with info_columns[2]:
        st.markdown('<div class="active-label">Vehículo</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="active-value">{escape(format_vehicle(session.get("vehiculo")))}</div>', unsafe_allow_html=True)

    st.divider()
    st.markdown("### Métricas")
    metric_columns = st.columns(3)
    with metric_columns[0]:
        st.metric("Energía consumida", format_energy(session.get("energia_consumida_kwh")))
    with metric_columns[1]:
        st.metric("Potencia actual", format_power(session.get("potencia_actual_kw")))
    with metric_columns[2]:
        st.metric("Tiempo transcurrido", elapsed_time(session.get("fecha_inicio")))

    st.divider()
    st.markdown("### Información adicional")
    additional_columns = st.columns(2)
    with additional_columns[0]:
        st.markdown('<div class="active-label">Inicio de carga</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="active-value">{escape(format_datetime(session.get("fecha_inicio")))}</div>', unsafe_allow_html=True)
    with additional_columns[1]:
        st.markdown('<div class="active-label">Última actualización</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="active-value">{escape(format_datetime(session.get("ultima_actualizacion")))}</div>', unsafe_allow_html=True)

    st.divider()
    if not session.get("stop_requested"):
        st.markdown("### Recompensa para esta sesión")
        st.caption(
            "El canje se registra en el historial de la sesión. "
            "No cambia importes de cobro."
        )
        selected_reward_id = session.get("selected_reward_id")
        reward_by_id = {
            reward["id"]: reward
            for reward in reward_options
            if reward.get("id")
        }
        reward_ids = [None, *reward_by_id]
        selected_index = (
            reward_ids.index(selected_reward_id)
            if selected_reward_id in reward_ids
            else 0
        )

        def reward_label(reward_id):
            if reward_id is None:
                return "No aplicar recompensa"
            reward = reward_by_id[reward_id]
            name = reward.get("nombre") or "Recompensa"
            company = reward.get("empresa")
            return f"{name} — {company}" if company else name

        chosen_reward_id = st.selectbox(
            "Selecciona una recompensa disponible",
            options=reward_ids,
            index=selected_index,
            format_func=reward_label,
            key=f"reward_selection_{session['id']}",
            disabled=not reward_options,
        )
        if st.button(
            "Guardar selección",
            key=f"save_reward_{session['id']}",
            disabled=not reward_options,
        ):
            selection_result = api_post(
                f"/client/me/sessions/{session['id']}/reward",
                json={"reward_id": chosen_reward_id},
            )
            if isinstance(selection_result, dict) and "error" in selection_result:
                st.error(selection_result["error"])
            else:
                st.success(selection_result.get("mensaje", "Selección guardada."))
                st.rerun()
        if selected_reward_id:
            st.info("Hay una recompensa seleccionada para esta carga.")
    st.divider()
    if session.get("stop_requested"):
        st.info("La orden de detención fue enviada. Espera a que el cargador cierre la sesión.")
        if st.button("🔄 Actualizar estado", key=f"refresh_stop_{session['id']}"):
            st.rerun()
    elif st.button(
        "⏹️ Detener carga",
        key=f"stop_charge_{session['id']}",
        type="primary",
    ):
        result = api_post(f"/client/me/sessions/{session['id']}/stop")
        if isinstance(result, dict) and "error" in result:
            st.error(result["error"])
        else:
            st.success("Orden de detención enviada al cargador.")
            st.rerun()
