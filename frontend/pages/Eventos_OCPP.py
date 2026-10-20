import pandas as pd
import streamlit as st

from components.sidebar import render_sidebar, _redirect_to_login
from utils.api_client import get_chargers, get_ocpp_errors, get_ocpp_messages
from utils.state import init_session_state

st.set_page_config(
    page_title="ChargeCore | Historial OCPP",
    page_icon="🔌",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_session_state()
render_sidebar()

if not st.session_state.get("authenticated"):
    _redirect_to_login()

st.markdown(
    """
    <style>
    .ocpp-kicker {
        display: inline-block;
        margin-bottom: 0.45rem;
        padding: 0.28rem 0.65rem;
        border-radius: 999px;
        background: #eaf2f8;
        color: #527b99;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.05em;
        text-transform: uppercase;
    }
    .ocpp-subtitle {
        margin-top: -0.7rem;
        color: #64748b;
    }
    .ocpp-table-heading {
        color: #526b7d;
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.045em;
        text-transform: uppercase;
    }
    div[data-testid="stMetric"] {
        min-height: 105px;
        padding: 0.8rem 1rem;
        border: 1px solid #dce8e3;
        border-radius: 14px;
        background: #ffffff;
        box-shadow: 0 2px 10px rgba(15, 23, 42, 0.03);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

heading, refresh_column = st.columns([5, 1])
with heading:
    st.markdown('<div class="ocpp-kicker">✦ Monitor de comunicación</div>', unsafe_allow_html=True)
    st.markdown("# 🔌 Historial OCPP")
    st.markdown(
        '<div class="ocpp-subtitle">Consulta la trazabilidad de comunicación de los cargadores.</div>',
        unsafe_allow_html=True,
    )
with refresh_column:
    st.write("")
    if st.button("🔄 Actualizar", use_container_width=True):
        st.rerun()

chargers = get_chargers()
if isinstance(chargers, dict) and chargers.get("error"):
    st.error("No se pudo conectar con el servidor.")
    st.stop()

charger_options = ["Todos"] + [
    charger.get("charge_point_id")
    for charger in chargers
    if charger.get("charge_point_id")
]
filters = st.columns([2, 1.2])
with filters[0]:
    selected_charger = st.selectbox("ID del cargador", charger_options)
with filters[1]:
    limit = st.selectbox("Mensajes a mostrar", [10, 15, 25, 50, 100, 250, 500], index=4)

charge_point_id = None if selected_charger == "Todos" else selected_charger
if charge_point_id:
    messages = get_ocpp_messages(charge_point_id, params={"limit": int(limit)})
else:
    messages = []
    for charger in chargers:
        current_id = charger.get("charge_point_id")
        if current_id:
            result = get_ocpp_messages(current_id, params={"limit": int(limit)})
            if isinstance(result, list):
                messages.extend(result)
    messages.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    messages = messages[: int(limit)]

messages_error = isinstance(messages, dict) and messages.get("error")
if messages_error:
    st.error("No se pudo cargar el historial OCPP.")
    messages = []

errors = get_ocpp_errors(charge_point_id, limit=int(limit))
errors_error = isinstance(errors, dict) and errors.get("error")
if errors_error:
    st.warning("No se pudieron cargar los errores OCPP.")
    errors = []

incoming_count = sum(1 for message in messages if message.get("direction") == "incoming")
outgoing_count = sum(1 for message in messages if message.get("direction") == "outgoing")

metric_columns = st.columns(4)
with metric_columns[0]:
    st.metric("Mensajes recibidos", f"{len(messages):,}", "En el historial cargado")
with metric_columns[1]:
    st.metric(
        "Entrantes",
        f"{incoming_count:,}",
        f"{round(incoming_count / len(messages) * 100)}% del historial" if messages else "Sin mensajes",
    )
with metric_columns[2]:
    st.metric(
        "Salientes",
        f"{outgoing_count:,}",
        f"{round(outgoing_count / len(messages) * 100)}% del historial" if messages else "Sin mensajes",
    )
with metric_columns[3]:
    st.metric("Errores OCPP", f"{len(errors):,}", "Sin incidencias" if not errors else "Incidencias detectadas")

st.divider()
history_heading, history_status = st.columns([3, 2])
with history_heading:
    st.markdown("### Historial de mensajes")
    st.caption("Trazabilidad técnica de la comunicación entre la plataforma y los cargadores.")
with history_status:
    history_status_text = (
        "🔴 No disponible"
        if messages_error or errors_error
        else "🟢 Historial disponible"
    )
    st.markdown(
        f'<div style="text-align:right; padding-top:0.8rem; color:#64748b;">'
        f'{history_status_text} &nbsp; | &nbsp; {len(messages)} mensajes mostrados</div>',
        unsafe_allow_html=True,
    )

with st.container(border=True):
    st.markdown('<div class="ocpp-table-heading">Filtrar mensajes</div>', unsafe_allow_html=True)
    filter_columns = st.columns([1.6, 1.1, 1.2, 1.4])
    with filter_columns[0]:
        search_message = st.text_input(
            "Buscar mensaje",
            placeholder="ID, acción o descripción...",
            label_visibility="collapsed",
            key="ocpp_search",
        )
    with filter_columns[1]:
        direction_filter = st.selectbox(
            "Dirección",
            ["Todas", "Entrante", "Saliente"],
            label_visibility="collapsed",
            key="ocpp_direction_filter",
        )
    with filter_columns[2]:
        actions = sorted({str(message.get("action")) for message in messages if message.get("action")})
        action_filter = st.selectbox(
            "Acción OCPP",
            ["Todas"] + actions,
            label_visibility="collapsed",
            key="ocpp_action_filter",
        )
    with filter_columns[3]:
        st.selectbox(
            "Mensajes",
            [f"{int(limit)} registros"],
            label_visibility="collapsed",
            key="ocpp_limit_display",
        )

filtered_messages = []
search_text = search_message.casefold().strip()
for message in messages:
    direction = message.get("direction", "")
    if direction_filter == "Entrante" and direction != "incoming":
        continue
    if direction_filter == "Saliente" and direction != "outgoing":
        continue
    if action_filter != "Todas" and message.get("action") != action_filter:
        continue
    searchable_text = " ".join(
        str(message.get(field) or "")
        for field in (
            "charge_point_id",
            "direction",
            "action",
            "unique_id",
            "message_type_name",
            "error_code",
            "error_description",
        )
    ).casefold()
    if search_text and search_text not in searchable_text:
        continue
    filtered_messages.append(message)

if not filtered_messages:
    st.info("No hay mensajes que coincidan con los filtros.")
else:
    filter_signature = (
        selected_charger,
        search_text,
        direction_filter,
        action_filter,
        int(limit),
    )
    if st.session_state.get("ocpp_filter_signature") != filter_signature:
        st.session_state["ocpp_filter_signature"] = filter_signature
        st.session_state["ocpp_page"] = 1

    page_size = 20
    page_count = max(1, (len(filtered_messages) + page_size - 1) // page_size)
    current_page = min(max(st.session_state.get("ocpp_page", 1), 1), page_count)
    st.session_state["ocpp_page"] = current_page
    start_index = (current_page - 1) * page_size
    end_index = min(start_index + page_size, len(filtered_messages))
    page_messages = filtered_messages[start_index:end_index]

    header_columns = st.columns([1, 2.2, 1.35, 1.8, 1.1, 1.15, 1.1])
    headings = ["Hora", "Cargador", "Dirección", "Acción OCPP", "Identificador", "Tipo", "Estado"]
    for column, label in zip(header_columns, headings):
        with column:
            st.markdown(f'<div class="ocpp-table-heading">{label}</div>', unsafe_allow_html=True)

    for message in page_messages:
        timestamp = str(message.get("timestamp") or "Sin fecha")
        time_value = timestamp.split("T")[-1][:12] if "T" in timestamp else timestamp[:12]
        direction = message.get("direction")
        direction_label = "↗ Saliente" if direction == "outgoing" else "↙ Entrante"
        is_error = message.get("message_type_name") == "CALLERROR" or message.get("error_code")
        action = message.get("action") or "Sin acción"

        with st.container(border=True):
            row_columns = st.columns([1, 2.2, 1.35, 1.8, 1.1, 1.15, 1.1])
            with row_columns[0]:
                st.markdown(f"**{time_value}**")
                st.caption(timestamp[:10] if "T" in timestamp else "")
            with row_columns[1]:
                st.markdown(f"🔌 `{message.get('charge_point_id', 'Sin identificador')}`")
            with row_columns[2]:
                st.caption(direction_label)
            with row_columns[3]:
                st.markdown(f"**{action}**")
            with row_columns[4]:
                st.code(str(message.get("unique_id") or "—"), language=None)
            with row_columns[5]:
                st.caption(message.get("message_type_name", "UNKNOWN"))
            with row_columns[6]:
                if is_error:
                    st.error("Error")
                else:
                    st.success("Correcto")

            with st.expander("👁 Inspeccionar detalle"):
                direction_description = (
                    "El cargador envió este mensaje al sistema."
                    if direction == "incoming"
                    else "El sistema envió este mensaje al cargador."
                )
                if is_error:
                    result_description = message.get("error_description") or "El mensaje generó un error."
                elif message.get("message_type_name") == "CALLRESULT":
                    result_description = "El cargador respondió correctamente a una solicitud."
                elif message.get("message_type_name") == "CALL":
                    result_description = "Se recibió una solicitud del cargador."
                else:
                    result_description = "El mensaje se registró en el historial."

                st.markdown("#### Resumen para el operador")
                st.write(direction_description)
                st.write(result_description)

                summary_columns = st.columns(3)
                with summary_columns[0]:
                    st.caption("CARGADOR")
                    st.write(message.get("charge_point_id") or "No identificado")
                with summary_columns[1]:
                    st.caption("ACCIÓN")
                    st.write(action)
                with summary_columns[2]:
                    st.caption("HORA")
                    st.write(timestamp.replace("T", " ")[:19])

                if is_error:
                    st.warning(
                        f"Código: {message.get('error_code') or 'No especificado'}"
                    )
                    st.caption(message.get("error_description") or "Sin descripción adicional.")

                payload = message.get("payload")
                if isinstance(payload, dict) and payload:
                    st.markdown("#### Información del mensaje")
                    readable_payload = {
                        {
                            "currentTime": "Hora reportada por el cargador",
                            "interval": "Intervalo de comunicación (segundos)",
                            "status": "Resultado reportado",
                            "connectorId": "Número de conector",
                            "errorCode": "Código de error del conector",
                            "info": "Información adicional",
                            "vendorId": "Fabricante",
                            "vendorErrorCode": "Código de error del fabricante",
                            "meterValue": "Lectura del medidor",
                            "transactionId": "Identificador de sesión",
                            "idTag": "Identificador de tarjeta",
                        }.get(key, key): value
                        for key, value in payload.items()
                    }
                    st.json(readable_payload)
                elif payload:
                    st.markdown("#### Información del mensaje")
                    st.write(payload)
                else:
                    st.caption("Este mensaje no contiene información adicional.")

                st.markdown("#### Datos técnicos OCPP")
                technical_details = {
                    "Dirección": direction,
                    "Tipo de mensaje": message.get("message_type_name", "UNKNOWN"),
                    "Identificador único": message.get("unique_id"),
                    "Código de error": message.get("error_code"),
                    "Mensaje original": message.get("raw_message"),
                }
                st.json({
                    key: value
                    for key, value in technical_details.items()
                    if value is not None
                })

    pagination_columns = st.columns([2, 1, 2, 1, 2])
    with pagination_columns[0]:
        st.caption(
            f"Mostrando {start_index + 1}–{end_index} de {len(filtered_messages)} mensajes"
        )
    with pagination_columns[1]:
        if st.button(
            "← Anterior",
            key="ocpp_previous_page",
            disabled=current_page == 1,
            use_container_width=True,
        ):
            st.session_state["ocpp_page"] = current_page - 1
            st.rerun()
    with pagination_columns[2]:
        st.caption(f"Página {current_page} de {page_count} · 20 por página")
    with pagination_columns[3]:
        if st.button(
            "Siguiente →",
            key="ocpp_next_page",
            disabled=current_page == page_count,
            use_container_width=True,
        ):
            st.session_state["ocpp_page"] = current_page + 1
            st.rerun()
    with pagination_columns[4]:
        st.caption(f"{len(messages)} mensajes cargados")

st.divider()
st.markdown("### Errores OCPP")
if not errors:
    st.success("No hay errores OCPP registrados.")
else:
    error_rows = [{
        "Fecha": item.get("timestamp", "Sin fecha"),
        "ID del cargador": item.get("charge_point_id", "Sin identificador"),
        "Acción OCPP": item.get("action", "Sin acción"),
        "Código de error": item.get("error_code", "Sin código"),
        "Descripción": item.get("error_description", "Sin descripción"),
        "Identificador único": item.get("unique_id", "Sin identificador único"),
    } for item in errors]
    st.dataframe(pd.DataFrame(error_rows), use_container_width=True, hide_index=True)
