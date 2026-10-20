from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

from components.sidebar import _redirect_to_login, render_sidebar
from utils.api_client import api_get, get_base_url, get_headers
from utils.state import init_session_state


st.set_page_config(
    page_title="ChargeCore | Reportes",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_session_state()
render_sidebar()

if not st.session_state.get("authenticated"):
    _redirect_to_login()

base_url = get_base_url()


def read_list(endpoint, label, params=None):
    result = api_get(endpoint, params=params)
    if isinstance(result, dict) and result.get("error"):
        st.warning(f"No se pudo cargar {label}: {result['error']}")
        return None
    if not isinstance(result, list):
        st.warning(f"La respuesta de {label} no tiene el formato esperado.")
        return None
    return result


def period_dates(period, key_prefix):
    today = date.today()
    if period == "Últimos 3 meses":
        start = today - timedelta(days=89)
    elif period == "Últimos 6 meses":
        start = today - timedelta(days=182)
    elif period == "Este año":
        start = date(today.year, 1, 1)
    elif period == "Personalizado":
        start = today - timedelta(days=29)

    if period == "Personalizado":
        columns = st.columns(2)
        with columns[0]:
            start = st.date_input("Desde", value=start, key=f"{key_prefix}_start")
        with columns[1]:
            today = st.date_input("Hasta", value=today, key=f"{key_prefix}_end")
    elif period == "Últimos 30 días":
        start = today - timedelta(days=29)
    return start, today


def paged_table(dataframe, key):
    if dataframe.empty:
        st.info("No hay registros que coincidan con la consulta.")
        return dataframe.iloc[0:0]

    page_size = 20
    page_count = (len(dataframe) + page_size - 1) // page_size
    page_key = f"reports_page_{key}"
    current_page = min(max(st.session_state.get(page_key, 1), 1), page_count)
    st.session_state[page_key] = current_page
    first_index = (current_page - 1) * page_size
    last_index = min(first_index + page_size, len(dataframe))

    visible_rows = dataframe.iloc[first_index:last_index]
    st.dataframe(
        visible_rows,
        use_container_width=True,
        hide_index=True,
    )

    columns = st.columns([2, 1, 1, 1, 2])
    with columns[0]:
        st.caption(f"Mostrando {first_index + 1}–{last_index} de {len(dataframe)} registros")
    with columns[1]:
        if st.button("Anterior", key=f"{key}_previous", disabled=current_page == 1):
            st.session_state[page_key] = current_page - 1
            st.rerun()
    with columns[2]:
        st.caption(f"Página {current_page} de {page_count}")
    with columns[3]:
        if st.button("Siguiente", key=f"{key}_next", disabled=current_page == page_count):
            st.session_state[page_key] = current_page + 1
            st.rerun()
    return visible_rows


def report_header(title, description):
    st.markdown(f"### {title}")
    st.caption(description)


def report_downloads(dataframe, report_name, endpoint=None, params=None):
    if dataframe.empty:
        return

    download_columns = st.columns([1, 1, 5])
    query = dict(params or {})
    if endpoint:
        query_signature = str(
            abs(hash(tuple(sorted((str(key), str(value)) for key, value in query.items()))))
        )
        pdf_data_key = f"{report_name}_pdf_data_{query_signature}"
        pdf_error_key = f"{report_name}_pdf_error_{query_signature}"
        with download_columns[0]:
            if st.button("Preparar PDF", key=f"{report_name}_prepare_pdf"):
                try:
                    response = requests.get(
                        f"{base_url}{endpoint}",
                        headers=get_headers(),
                        params={**query, "fmt": "pdf"},
                        timeout=30,
                    )
                    if (
                        response.status_code == 200
                        and response.headers.get("Content-Type", "").startswith("application/pdf")
                        and response.content.startswith(b"%PDF")
                    ):
                        st.session_state[pdf_data_key] = response.content
                        st.session_state[pdf_error_key] = None
                    else:
                        try:
                            payload = response.json()
                        except ValueError:
                            payload = {}
                        st.session_state[pdf_data_key] = None
                        st.session_state[pdf_error_key] = payload.get(
                            "error",
                            payload.get("detail", "El servidor no generó un PDF válido."),
                        )
                except requests.RequestException:
                    st.session_state[pdf_data_key] = None
                    st.session_state[pdf_error_key] = (
                        "No fue posible conectar con el servidor para generar el PDF."
                    )
                st.rerun()

        pdf_data = st.session_state.get(pdf_data_key)
        pdf_error = st.session_state.get(pdf_error_key)
        if pdf_data:
            st.download_button(
                "Descargar PDF",
                data=pdf_data,
                file_name=f"{report_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                mime="application/pdf",
                key=f"{report_name}_download_pdf",
            )
        elif pdf_error:
            st.error(f"No se pudo preparar el PDF: {pdf_error}")
    with download_columns[1]:
        csv = dataframe.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            "Descargar CSV",
            data=csv,
            file_name=f"{report_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            key=f"{report_name}_csv",
        )


def filter_text(dataframe, columns, query):
    if not query.strip():
        return dataframe
    searchable = [column for column in columns if column in dataframe.columns]
    if not searchable:
        return dataframe
    mask = dataframe[searchable].fillna("").astype(str).apply(
        lambda column: column.str.contains(query.strip(), case=False, regex=False)
    ).any(axis=1)
    return dataframe[mask].copy()


def format_duration(value):
    if pd.isna(value):
        return "No disponible"
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return "No disponible"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours and remaining_minutes:
        return f"{hours} h {remaining_minutes} min"
    if hours:
        return f"{hours} h"
    return f"{remaining_minutes} min"


st.markdown(
    """
    <style>
    .block-container {
        max-width: 1440px;
        padding: 2rem clamp(1rem, 3vw, 2.5rem) 2.5rem;
    }
    h1 {
        font-size: 2rem !important;
    }
    h2 {
        font-size: 1.55rem !important;
    }
    h3 {
        font-size: 1.25rem !important;
    }
    h4, h5, h6 {
        font-size: 1.05rem !important;
    }
    p, label, [data-testid="stCaptionContainer"] {
        font-size: 0.95rem;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.55rem;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.9rem;
    }
    .report-subtitle {
        color: #64748b;
        margin-top: -0.75rem;
        margin-bottom: 1.25rem;
    }
    div[data-testid="stMetric"] {
        padding: 1rem 1.1rem;
        border: 1px solid #dce8e3;
        border-radius: 14px;
        background: #ffffff;
        box-shadow: 0 2px 10px rgba(15, 23, 42, 0.03);
    }
    div[data-testid="stPlotlyChart"],
    div[data-testid="stDataFrame"] {
        border-radius: 14px;
    }
    @media (max-width: 768px) {
        .block-container {
            padding: 1.25rem 1rem 2rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Reportes y análisis")
st.markdown(
    '<div class="report-subtitle">Consulta la actividad, el consumo y el uso de los cargadores.</div>',
    unsafe_allow_html=True,
)
st.caption(f"Indicadores acumulados del sistema · Actualizado: {datetime.now().strftime('%d %b %Y, %H:%M')}")

summary_kpis = api_get("/dashboard/kpi")
if isinstance(summary_kpis, dict) and "error" not in summary_kpis:
    total_sessions = int(summary_kpis.get("total_cargas") or 0)
    total_energy = float(summary_kpis.get("energia_total_kwh") or 0)
    active_sessions = int(summary_kpis.get("sesiones_activas") or 0)
    average_energy = total_energy / total_sessions if total_sessions else 0
    metric_columns = st.columns(4)
    with metric_columns[0]:
        st.metric("Sesiones finalizadas", total_sessions)
    with metric_columns[1]:
        st.metric("Energía consumida", f"{total_energy:.2f} kWh")
    with metric_columns[2]:
        st.metric("Promedio por sesión", f"{average_energy:.2f} kWh")
    with metric_columns[3]:
        st.metric("Sesiones activas", active_sessions)
else:
    st.warning("No fue posible cargar los indicadores generales.")

consumption_chart = api_get("/dashboard/grafica-consumo-diario")
overview_report = api_get("/reportes/dashboard")
chart_columns = st.columns(2)
with chart_columns[0]:
    st.markdown("#### Consumo diario")
    st.caption("Energía de los últimos 7 días (kWh)")
    if isinstance(consumption_chart, list) and consumption_chart:
        consumption_frame = pd.DataFrame(consumption_chart)
    else:
        consumption_frame = pd.DataFrame(columns=["fecha", "energia_kwh"])
    if {"fecha", "energia_kwh"}.issubset(consumption_frame.columns):
        chart_dates = pd.date_range(end=date.today(), periods=7, freq="D").strftime("%Y-%m-%d")
        chart_values = consumption_frame.set_index("fecha")["energia_kwh"]
        chart_values = pd.to_numeric(chart_values, errors="coerce").groupby(level=0).sum()
        chart_frame = pd.DataFrame(
            {"Fecha": chart_dates, "Energía (kWh)": chart_values.reindex(chart_dates).fillna(0).values}
        )
        consumption_figure = px.bar(
            chart_frame,
            x="Fecha",
            y="Energía (kWh)",
            labels={"Energía (kWh)": "Energía (kWh)"},
            template="plotly_white",
            color_discrete_sequence=["#70b9a3"],
        )
        consumption_figure.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(consumption_figure, use_container_width=True)
        if chart_frame["Energía (kWh)"].sum() == 0:
            st.caption("No hay datos de consumo para mostrar en este periodo.")
        if isinstance(consumption_chart, dict) and consumption_chart.get("error"):
            st.warning(f"No se pudo cargar el consumo diario: {consumption_chart['error']}")
    else:
        st.info("El servicio de consumo no devolvió datos.")

with chart_columns[1]:
    st.markdown("#### Sesiones por día")
    st.caption("Sesiones finalizadas de los últimos 30 días")
    sessions_by_day = []
    if isinstance(overview_report, dict) and "error" not in overview_report:
        sessions_by_day = overview_report.get("graficas", {}).get("sesiones_por_dia", [])
    else:
        st.warning(
            f"No se pudo cargar el resumen de sesiones: "
            f"{overview_report.get('error', 'respuesta no válida') if isinstance(overview_report, dict) else 'respuesta no válida'}"
        )
    sessions_frame = pd.DataFrame(sessions_by_day, columns=["fecha", "sesiones"])
    chart_dates = pd.date_range(end=date.today(), periods=30, freq="D").strftime("%Y-%m-%d")
    if not sessions_frame.empty and {"fecha", "sesiones"}.issubset(sessions_frame.columns):
        sessions_values = pd.to_numeric(
            sessions_frame.set_index("fecha")["sesiones"], errors="coerce"
        ).groupby(level=0).sum()
        daily_values = sessions_values.reindex(chart_dates).fillna(0).values
    else:
        daily_values = [0] * len(chart_dates)
    sessions_chart_frame = pd.DataFrame({"Fecha": chart_dates, "Sesiones": daily_values})
    sessions_figure = px.line(
        sessions_chart_frame,
        x="Fecha",
        y="Sesiones",
        markers=True,
        labels={"Sesiones": "Sesiones"},
        template="plotly_white",
        color_discrete_sequence=["#3885b2"],
    )
    sessions_figure.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(sessions_figure, use_container_width=True)
    if sessions_chart_frame["Sesiones"].sum() == 0:
        st.caption("No hay sesiones registradas para mostrar en este periodo.")

report_tabs = st.tabs(["Resumen", "Sesiones", "Consumo", "Usuarios", "Cargadores", "Recompensas"])

with report_tabs[0]:
    report_header("Resumen general", "Indicadores acumulados y actividad reciente del sistema.")
    if isinstance(overview_report, dict) and "error" not in overview_report:
        charts = overview_report.get("graficas", {})
        overview_columns = st.columns(3)

        charger_usage = charts.get("uso_de_cargadores", [])
        if charger_usage:
            top_charger = max(charger_usage, key=lambda item: item.get("sesiones", 0))
            with overview_columns[0]:
                st.markdown("##### Cargador con más sesiones")
                st.metric(
                    "Cargador",
                    top_charger.get("cargador") or "No disponible",
                    f"{top_charger.get('sesiones', 0)} sesiones",
                )

        top_users = charts.get("top_usuarios", [])
        if top_users:
            top_user = max(top_users, key=lambda item: item.get("kwh", 0))
            with overview_columns[1]:
                st.markdown("##### Usuario con mayor consumo")
                st.metric(
                    "Usuario",
                    top_user.get("usuario") or "No disponible",
                    f"{top_user.get('kwh', 0):.2f} kWh",
                )

        rewards = read_list("/reportes/recompensas", "el resumen de recompensas")
        if rewards is not None:
            used_rewards = sum(1 for reward in rewards if reward.get("estado") == "Usada")
            pending_rewards = len(rewards) - used_rewards
            with overview_columns[2]:
                st.markdown("##### Recompensas")
                st.metric("Entregadas", len(rewards), f"{used_rewards} usadas · {pending_rewards} pendientes")
    else:
        st.warning("No fue posible cargar el resumen general.")

with report_tabs[1]:
    report_header("Reporte de sesiones", "Consulta sesiones y sus datos de consumo.")
    session_filters = st.columns([1.3, 1.2, 2])
    with session_filters[0]:
        session_period = st.selectbox(
            "Periodo",
            ["Últimos 30 días", "Últimos 3 meses", "Últimos 6 meses", "Este año", "Personalizado"],
            key="sessions_period",
        )
    with session_filters[1]:
        session_status = st.selectbox(
            "Estado",
            ["Todos", "Finalizada", "Activa", "Iniciada", "Interrumpida", "Error"],
            key="sessions_status",
        )
    session_start, session_end = period_dates(session_period, "sessions")
    session_end_inclusive = datetime.combine(session_end, datetime.max.time()).isoformat()
    filter_columns = st.columns([1.2, 1.2, 2])
    with filter_columns[0]:
        session_charger = st.text_input("Cargador", placeholder="Todos", key="sessions_charger")
    with filter_columns[1]:
        session_user = st.text_input("Usuario", placeholder="Todos", key="sessions_user")
    with filter_columns[2]:
        session_search = st.text_input("Buscar", placeholder="Buscar por folio, cargador o vehículo...", key="sessions_search")

    session_params = {
        "limit": 2000,
        "fecha_inicio": session_start.isoformat(),
        "fecha_fin": session_end_inclusive,
    }
    if session_status != "Todos":
        session_params["estado"] = session_status.lower()
    if session_charger.strip():
        session_params["charge_point_id"] = session_charger.strip()

    sessions = read_list("/sesiones/", "las sesiones", session_params)
    if sessions is not None:
        session_frame = pd.DataFrame(sessions)
        if not session_frame.empty:
            session_frame["Fecha y hora"] = pd.to_datetime(
                session_frame.get(
                    "fecha_inicio", pd.Series(index=session_frame.index, dtype="object")
                ),
                errors="coerce",
            ).dt.strftime("%d %b %Y, %H:%M").fillna("No disponible")
            session_frame["Folio"] = session_frame.apply(
                lambda row: row.get("transaction_id")
                if pd.notna(row.get("transaction_id"))
                else row.get("_id", "No disponible"),
                axis=1,
            )
            session_frame["Cargador"] = session_frame.apply(
                lambda row: row.get("cargador_nombre") or row.get("charge_point_id") or "No disponible",
                axis=1,
            )
            session_frame["Usuario"] = session_frame.apply(
                lambda row: row.get("usuario_nombre") or row.get("usuario_email") or "No disponible",
                axis=1,
            )
            session_frame["Vehículo"] = session_frame.apply(
                lambda row: " ".join(
                    str(value) for value in (
                        row.get("vehiculo_marca"),
                        row.get("vehiculo_modelo"),
                        row.get("vehiculo_placa"),
                    ) if pd.notna(value) and value
                ) or "No disponible",
                axis=1,
            )
            session_frame["Estado"] = session_frame.get(
                "estado", pd.Series(index=session_frame.index, dtype=str)
            ).fillna(
                "Desconocido"
            ).astype(str).str.title()
            session_frame["Energía (kWh)"] = pd.to_numeric(
                session_frame.get(
                    "energia_consumida_kwh",
                    pd.Series(index=session_frame.index, dtype=float),
                ),
                errors="coerce",
            ).round(2)
            session_frame["Duración"] = session_frame.get(
                "duracion_minutos", pd.Series(index=session_frame.index, dtype=float)
            ).apply(format_duration)
            session_frame = filter_text(session_frame, ["Usuario"], session_user)
            session_frame = filter_text(
                session_frame,
                ["Folio", "Cargador", "Usuario", "Vehículo"],
                session_search,
            )
            display_sessions = session_frame[
                ["Fecha y hora", "Folio", "Cargador", "Usuario", "Estado", "Energía (kWh)", "Duración"]
            ]
            visible_sessions = paged_table(display_sessions, "sessions")
            report_downloads(
                display_sessions,
                "sesiones",
                "/reportes/sesiones/export",
                {
                    "fecha_inicio": session_start.isoformat(),
                    "fecha_fin": session_end_inclusive,
                    "charge_point_id": session_charger.strip(),
                },
            )

            detail_rows = session_frame.loc[visible_sessions.index]
            if not detail_rows.empty:
                detail_options = {
                    f"{row['Folio']} · {row['Cargador']} · {row['Fecha y hora']}": index
                    for index, row in detail_rows.iterrows()
                }
                selected_detail = st.selectbox(
                    "Consultar detalle (solo lectura)",
                    ["Selecciona una sesión"] + list(detail_options),
                    key="session_detail",
                )
                if selected_detail != "Selecciona una sesión":
                    selected = detail_rows.loc[detail_options[selected_detail]]
                    with st.container(border=True):
                        detail_columns = st.columns(3)
                        for column, label, value in zip(
                            detail_columns,
                            ("Cargador", "Usuario", "Estado"),
                            (selected["Cargador"], selected["Usuario"], selected["Estado"]),
                        ):
                            with column:
                                st.markdown(f"**{label}**")
                                st.write(value)
                        st.write(f"**Vehículo:** {selected['Vehículo']}")
                        st.write(f"**Inicio:** {selected['Fecha y hora']}")
                        st.write(f"**Finalización:** {selected.get('fecha_fin') or 'No disponible'}")
                        st.write(f"**Conector:** {selected.get('connector_id') or 'No disponible'}")
                        st.write(f"**Beneficio:** {selected.get('beneficio_aplicado') or 'No disponible'}")
        else:
            st.info("No hay sesiones en el periodo seleccionado.")

with report_tabs[2]:
    report_header("Análisis de consumo", "Compara sesiones realizadas y energía consumida por usuario.")
    consumption_period = st.selectbox(
        "Periodo",
        ["Últimos 30 días", "Últimos 3 meses", "Últimos 6 meses", "Este año", "Personalizado"],
        key="consumption_period",
    )
    consumption_start, consumption_end = period_dates(consumption_period, "consumption")
    consumption_end_inclusive = datetime.combine(
        consumption_end, datetime.max.time()
    ).isoformat()
    consumption_params = {
        "fecha_inicio": consumption_start.isoformat(),
        "fecha_fin": consumption_end_inclusive,
        "limit": 1000,
    }
    consumption_rows = read_list("/reportes/consumo", "el consumo", consumption_params)
    if consumption_rows is not None:
        consumption_frame = pd.DataFrame(consumption_rows)
        if not consumption_frame.empty:
            consumption_frame = consumption_frame.rename(columns={
                "usuario": "Usuario",
                "total_cargas": "Cargas",
                "total_kwh": "Energía total (kWh)",
                "promedio_kwh": "Promedio por sesión (kWh)",
            })
            consumption_frame["Energía total (kWh)"] = pd.to_numeric(
                consumption_frame["Energía total (kWh)"], errors="coerce"
            ).round(2)
            consumption_frame["Promedio por sesión (kWh)"] = pd.to_numeric(
                consumption_frame["Promedio por sesión (kWh)"], errors="coerce"
            ).round(2)
            consumption_frame = filter_text(consumption_frame, ["Usuario"], st.text_input(
                "Buscar usuario", placeholder="Nombre del usuario...", key="consumption_search"
            ))
            paged_table(consumption_frame, "consumption")
            report_downloads(
                consumption_frame,
                "consumo",
                "/reportes/consumo/export",
                consumption_params,
            )
        else:
            st.info("No hay información de consumo para el periodo seleccionado.")

with report_tabs[3]:
    report_header("Reporte de usuarios", "Consulta cuentas, vehículos registrados y actividad de usuarios.")
    users = read_list("/reportes/usuarios", "los usuarios")
    if users is not None:
        users_frame = pd.DataFrame(users)
        if not users_frame.empty:
            users_frame = users_frame.rename(columns={
                "nombre": "Nombre",
                "correo": "Correo",
                "nfc_asociado": "NFC asociado",
                "vehiculos_registrados": "Vehículos",
                "numero_cargas": "Cargas",
                "estado": "Estado",
            })
            users_frame = filter_text(
                users_frame,
                ["Nombre", "Correo", "NFC asociado", "Estado"],
                st.text_input("Buscar usuario", placeholder="Nombre o correo...", key="users_search"),
            )
            paged_table(users_frame, "users")
            report_downloads(users_frame, "usuarios", "/reportes/usuarios/export")
        else:
            st.info("No hay usuarios para mostrar.")

with report_tabs[4]:
    report_header("Reporte de cargadores", "Consulta estado, conexión, sesiones y energía suministrada.")
    chargers = read_list("/reportes/cargadores", "los cargadores")
    if chargers is not None:
        chargers_frame = pd.DataFrame(chargers)
        if not chargers_frame.empty:
            chargers_frame = chargers_frame.rename(columns={
                "charger_id": "Cargador",
                "estado": "Estado",
                "ultima_conexion": "Última conexión",
                "total_sesiones": "Sesiones",
                "energia_suministrada_kwh": "Energía suministrada (kWh)",
            })
            chargers_frame["Energía suministrada (kWh)"] = pd.to_numeric(
                chargers_frame["Energía suministrada (kWh)"], errors="coerce"
            ).round(2)
            chargers_frame = filter_text(
                chargers_frame,
                ["Cargador", "Estado"],
                st.text_input("Buscar cargador", placeholder="Identificador o estado...", key="chargers_search"),
            )
            paged_table(chargers_frame, "chargers")
            report_downloads(chargers_frame, "cargadores", "/reportes/cargadores/export")
        else:
            st.info("No hay cargadores para mostrar.")

with report_tabs[5]:
    report_header("Reporte de recompensas", "Consulta recompensas otorgadas y su estado.")
    rewards = read_list("/reportes/recompensas", "las recompensas")
    if rewards is not None:
        rewards_frame = pd.DataFrame(rewards)
        if not rewards_frame.empty:
            rewards_frame = rewards_frame.rename(columns={
                "usuario": "Usuario",
                "tipo_de_recompensa": "Tipo de recompensa",
                "fecha_generacion": "Fecha",
                "estado": "Estado",
            })
            rewards_frame = filter_text(
                rewards_frame,
                ["Usuario", "Tipo de recompensa", "Estado"],
                st.text_input(
                    "Buscar recompensa",
                    placeholder="Usuario, tipo o estado...",
                    key="rewards_search",
                ),
            )
            paged_table(rewards_frame, "rewards")
            report_downloads(rewards_frame, "recompensas", "/reportes/recompensas/export")
        else:
            st.info("No hay recompensas para mostrar.")
