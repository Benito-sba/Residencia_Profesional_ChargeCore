import pandas as pd
import streamlit as st
from datetime import datetime, timedelta

from components.sidebar import render_sidebar, _redirect_to_login
from utils.api_client import api_get
from utils.state import init_session_state


LEVELS = {
    "error": {"label": "Crítica", "icon": "🔴", "color": "#ef4444"},
    "warning": {"label": "Advertencia", "icon": "🟠", "color": "#f59e0b"},
    "info": {"label": "Informativa", "icon": "🔵", "color": "#3b82f6"},
    "debug": {"label": "Depuración", "icon": "⚪", "color": "#94a3b8"},
}


def format_timestamp(value):
    if not value:
        return "Fecha no disponible"
    try:
        return pd.to_datetime(value).strftime("%d %b %Y, %H:%M")
    except (TypeError, ValueError):
        return str(value)


def alert_identifier(alert, index):
    return str(
        alert.get("_id")
        or f"{alert.get('timestamp', '')}-{alert.get('modulo', '')}-{index}"
    )


init_session_state()
render_sidebar()

if not st.session_state.get("authenticated"):
    _redirect_to_login()

st.markdown("# 🔔 Centro de Alertas y Notificaciones")
st.markdown("Monitorea incidencias y eventos que requieren atención en ChargeCore.")

# ============= FILTROS =============
filter_columns = st.columns([1.1, 1.1, 1.3, 2.2, 0.7])

with filter_columns[0]:
    days = st.selectbox("Periodo", [7, 15, 30, 60, 90], index=0, format_func=lambda value: f"Últimos {value} días")
with filter_columns[1]:
    selected_levels = st.multiselect(
        "Nivel",
        options=["error", "warning", "info", "debug"],
        default=[],
        placeholder="Selecciona uno o varios niveles",
        format_func=lambda level: LEVELS[level]["label"],
    )
with filter_columns[2]:
    module_filter = st.text_input("Módulo", placeholder="Todos los módulos")
with filter_columns[3]:
    search_filter = st.text_input("Buscar", placeholder="Buscar en título, mensaje o recurso")
with filter_columns[4]:
    st.write("")
    st.write("")
    if st.button("🔄 Actualizar", use_container_width=True):
        st.rerun()

with st.spinner("Cargando alertas y eventos..."):
    alert_result = api_get("/monitoreo/alertas", params={"dias": days, "limit": 500})
    event_result = api_get("/monitoreo/eventos-ocpp", params={"dias": days, "limit": 500})

alert_error = alert_result.get("error") if isinstance(alert_result, dict) else None
event_error = event_result.get("error") if isinstance(event_result, dict) else None

if alert_error:
    st.error(f"No se pudieron cargar las alertas: {alert_error}")
    alerts = []
elif isinstance(alert_result, list):
    alerts = alert_result
else:
    st.error("La respuesta del servicio de alertas no tiene el formato esperado.")
    alerts = []

if event_error:
    st.warning(f"No se pudieron cargar los eventos OCPP: {event_error}")
    events = []
elif isinstance(event_result, list):
    events = event_result
else:
    st.warning("La respuesta de eventos OCPP no tiene el formato esperado.")
    events = []

filtered_alerts = []
for alert in alerts:
    level = str(alert.get("nivel", "info")).lower()
    module = str(alert.get("modulo") or "Sistema")
    searchable = " ".join(
        str(alert.get(field) or "")
        for field in ("titulo", "mensaje", "modulo", "recurso_id", "charge_point_id")
    ).casefold()
    if selected_levels and level not in selected_levels:
        continue
    if module_filter and module_filter.casefold() not in module.casefold():
        continue
    if search_filter and search_filter.casefold() not in searchable:
        continue
    filtered_alerts.append(alert)

filtered_alerts.sort(
    key=lambda alert: str(alert.get("timestamp") or ""),
    reverse=True,
)

# ============= RESUMEN =============
error_count = sum(str(alert.get("nivel", "")).lower() == "error" for alert in alerts)
warning_count = sum(str(alert.get("nivel", "")).lower() == "warning" for alert in alerts)
summary = st.columns(4)
with summary[0]:
    st.metric("Alertas encontradas", len(filtered_alerts))
with summary[1]:
    st.metric("Críticas", error_count)
with summary[2]:
    st.metric("Advertencias", warning_count)
with summary[3]:
    st.metric("Eventos OCPP", len(events))
st.caption(f"Resumen del periodo seleccionado · Se consultan hasta 500 alertas y 500 eventos.")

st.divider()

# ============= BANDEJA Y DETALLE =============
st.markdown("**Bandeja de alertas y detalle**")
inbox_column, detail_column = st.columns([1, 1.6], gap="medium")

selected_id = st.session_state.get("selected_alert_id")
selected_alert = next(
    (
        alert for index, alert in enumerate(filtered_alerts)
        if alert_identifier(alert, index) == selected_id
    ),
    None,
)
if selected_alert is None and filtered_alerts:
    selected_alert = filtered_alerts[0]
    st.session_state.selected_alert_id = alert_identifier(selected_alert, 0)

with inbox_column:
    with st.container(border=True):
        st.markdown("**📥 Bandeja**")
        st.caption(f"{len(filtered_alerts)} incidencias en el filtro actual")
        if not filtered_alerts:
            st.info("No hay alertas que coincidan con los filtros.")
        else:
            with st.container(height=420):
                for index, alert in enumerate(filtered_alerts):
                    alert_id = alert_identifier(alert, index)
                    level = str(alert.get("nivel", "info")).lower()
                    config = LEVELS.get(level, LEVELS["debug"])
                    title = alert.get("titulo") or alert.get("mensaje") or "Alerta sin descripción"
                    module = alert.get("modulo") or "Sistema"
                    selected = alert_id == str(st.session_state.get("selected_alert_id", ""))
                    button_label = f"{config['icon']} {title}"
                    if st.button(
                        button_label,
                        key=f"select_alert_{alert_id}",
                        use_container_width=True,
                        type="primary" if selected else "secondary",
                    ):
                        st.session_state.selected_alert_id = alert_id
                        st.rerun()
                    st.caption(
                        f"{config['label']} · {module} · "
                        f"{format_timestamp(alert.get('timestamp'))}"
                    )
                    if index < len(filtered_alerts) - 1:
                        st.divider()

with detail_column:
    with st.container(border=True):
        if selected_alert is None:
            st.markdown("**🔎 Detalle de la alerta**")
            st.info("Selecciona una alerta de la bandeja para consultar sus detalles.")
        else:
            level = str(selected_alert.get("nivel", "info")).lower()
            config = LEVELS.get(level, LEVELS["debug"])
            title = selected_alert.get("titulo") or "Alerta del sistema"
            message = selected_alert.get("mensaje") or "No hay descripción adicional."
            module = selected_alert.get("modulo") or "Sistema"
            resource = (
                selected_alert.get("recurso_id")
                or selected_alert.get("charge_point_id")
                or "No especificado"
            )

            st.markdown(f"### {config['icon']} {title}")
            st.caption(f"{config['label']} · {module}")
            with st.container(border=True):
                st.markdown("**Descripción del evento**")
                st.write(message)

            metadata = st.columns(2)
            with metadata[0]:
                st.markdown("**Origen**")
                st.write(resource)
                st.markdown("**Módulo**")
                st.write(module)
            with metadata[1]:
                st.markdown("**Detectada**")
                st.write(format_timestamp(selected_alert.get("timestamp")))
                st.markdown("**ID de registro**")
                st.write(str(selected_alert.get("_id") or "No disponible"))

            details = selected_alert.get("datos")
            if details:
                with st.expander("Ver datos asociados"):
                    st.json(details)

            st.caption(
                "Las alertas provienen de registros del sistema. "
                "Actualmente no existe una acción persistente para archivarlas o resolverlas."
            )

st.divider()

# ============= TENDENCIAS =============
st.markdown("**Actividad de alertas**")
daily_column, origin_column = st.columns(2, gap="medium")

now = datetime.now()
seven_days_ago = now.date() - timedelta(days=6)
daily_counts = {seven_days_ago + timedelta(days=offset): 0 for offset in range(7)}
module_counts = {}
for alert in alerts:
    timestamp = alert.get("timestamp")
    if timestamp:
        try:
            alert_date = pd.to_datetime(timestamp).date()
        except (TypeError, ValueError):
            alert_date = None
        if alert_date in daily_counts:
            daily_counts[alert_date] += 1
    module = str(alert.get("modulo") or "Sistema")
    module_counts[module] = module_counts.get(module, 0) + 1

with daily_column:
    with st.container(border=True):
        st.markdown("**Alertas de los últimos 7 días**")
        daily_frame = pd.DataFrame(
            {
                "Día": [day.strftime("%a %d %b") for day in daily_counts],
                "Alertas": list(daily_counts.values()),
            }
        ).set_index("Día")
        st.bar_chart(daily_frame, y="Alertas", height=240, color="#3b82f6")

with origin_column:
    with st.container(border=True):
        st.markdown("**Origen de incidencias**")
        if module_counts:
            origin_frame = pd.DataFrame(
                sorted(module_counts.items(), key=lambda item: item[1], reverse=True),
                columns=["Módulo", "Alertas"],
            ).set_index("Módulo")
            st.bar_chart(origin_frame, y="Alertas", height=240, color="#f59e0b")
        else:
            st.info("No hay datos de origen para el periodo seleccionado.")
