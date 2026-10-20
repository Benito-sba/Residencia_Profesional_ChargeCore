import streamlit as st
from utils.state import init_session_state
from utils.api_client import api_get, get_base_url, get_headers
from components.sidebar import render_sidebar, _redirect_to_login
from datetime import datetime, timedelta
import pandas as pd
import requests


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


init_session_state()
render_sidebar()

if not st.session_state.get("authenticated"):
    _redirect_to_login()

st.markdown("# 📊 Historial de Sesiones de Carga")
st.markdown("Monitorea y analiza todas las sesiones de carga realizadas en el sistema")

st.markdown(
    """
    <style>
    .block-container {
        max-width: 1440px;
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    .sessions-subtitle {
        color: #64748b;
        margin-top: -0.8rem;
        margin-bottom: 1.2rem;
    }
    .sessions-section-label {
        color: #334155;
        font-size: 0.8rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="sessions-subtitle">Consulta las sesiones, su consumo y sus detalles.</div>',
    unsafe_allow_html=True,
)

# Los filtros existentes se mantienen en un solo bloque; la búsqueda complementa
# al cargador buscando también por vehículo o folio en los resultados cargados.
with st.container():
    st.markdown('<div class="sessions-section-label">Filtros de consulta</div>', unsafe_allow_html=True)
    filter_columns = st.columns([1.15, 1, 1, 1.25, 1.6])

    with filter_columns[0]:
        estado_filtro = st.selectbox("Estado", ["Todas", "Activa", "Finalizada", "Error"])

    with filter_columns[1]:
        fecha_inicio = st.date_input(
            "Desde",
            value=datetime.now() - timedelta(days=30),
            key="date_start",
        )

    with filter_columns[2]:
        fecha_fin = st.date_input("Hasta", value=datetime.now(), key="date_end")

    with filter_columns[3]:
        filtro_cargador = st.text_input("Cargador", placeholder="Ej. CP-001")

    with filter_columns[4]:
        usuarios = api_get("/usuarios/", params={"limit": 200})
        usuarios = usuarios if isinstance(usuarios, list) else []
        usuario_options = {"Todos": None}
        for usuario in usuarios:
            usuario_id = usuario.get("_id")
            if usuario_id:
                usuario_label = (
                    f"{usuario.get('nombre') or 'Sin nombre'} "
                    f"({usuario.get('email') or 'sin correo'})"
                )
                usuario_options[usuario_label] = usuario_id
        usuario_seleccionado = st.selectbox("Usuario", list(usuario_options))

    busqueda = st.text_input(
        "Buscar en resultados",
        placeholder="Buscar por cargador, vehículo o folio...",
        help="La búsqueda se aplica a las sesiones devueltas por los filtros seleccionados.",
    )

# ============= SECCIÓN: CONSTRUCCIÓN DE PARÁMETROS =============
params = {"limit": 200}

estado_map = {"Activa": "activa", "Finalizada": "finalizada", "Error": "error"}
if estado_filtro != "Todas":
    params["estado"] = estado_map.get(estado_filtro, "todas")

if fecha_inicio <= fecha_fin:
    params["fecha_inicio"] = fecha_inicio.isoformat()
    params["fecha_fin"] = fecha_fin.isoformat()

if filtro_cargador:
    params["charge_point_id"] = filtro_cargador

if usuario_options[usuario_seleccionado]:
    params["usuario_id"] = usuario_options[usuario_seleccionado]

# ============= SECCIÓN: CARGAR SESIONES =============
with st.spinner("Cargando sesiones..."):
    sesiones = api_get("/sesiones/", params=params)

if "error" in sesiones:
    st.error("❌ No se pudieron cargar las sesiones")
elif not sesiones:
    st.info("ℹ️ No hay sesiones que coincidan con los filtros")
else:
    df = pd.DataFrame(sesiones)
    
    # Formatear fechas
    if "fecha_inicio" in df.columns:
        df["📅 Inicio"] = (
            pd.to_datetime(df["fecha_inicio"], errors="coerce")
            .dt.strftime("%d %b %Y, %H:%M")
            .fillna("No disponible")
        )
    if "fecha_fin" in df.columns:
        df["📅 Fin"] = (
            pd.to_datetime(df["fecha_fin"], errors="coerce")
            .dt.strftime("%d %b %Y, %H:%M")
            .fillna("No disponible")
        )
    
    df["🔌 Cargador"] = df.apply(
        lambda row: row.get("cargador_nombre") or row.get("charge_point_id") or "No disponible",
        axis=1,
    )
    df["👤 Usuario"] = df.apply(
        lambda row: row.get("usuario_nombre") or row.get("usuario_email") or "No disponible",
        axis=1,
    )
    df["🚗 Vehículo"] = df.apply(
        lambda row: " - ".join(
            value for value in (
                row.get("vehiculo_placa"),
                " ".join(value for value in (row.get("vehiculo_marca"), row.get("vehiculo_modelo")) if value),
            ) if value
        ) or "No disponible",
        axis=1,
    )
    df["💳 NFC"] = df["nfc_uid"].fillna("No disponible") if "nfc_uid" in df.columns else "No disponible"
    df["Folio"] = (
        df["transaction_id"].fillna("No disponible").astype(str)
        if "transaction_id" in df.columns
        else "No disponible"
    )

    # Renombrar columnas
    df = df.rename(columns={
        "connector_id": "Conector",
        "energia_consumida_kwh": "⚡ Energía (kWh)",
        "duracion_minutos": "⏱️ Duración (min)",
        "es_carga_gratuita": "🎁 Gratuita",
        "beneficio_aplicado": "🎯 Beneficio",
        "estado": "📋 Estado",
        "meter_start": "🔋 Energía inicial",
        "meter_stop": "🔋 Energía final",
        "motivo_fin": "🛑 Motivo de finalización",
        "monto_pagado": "💵 Monto",
        "tarifa_aplicada": "💰 Tarifa"
    })

    if busqueda.strip():
        searchable_columns = [
            column for column in ("Folio", "🔌 Cargador", "🚗 Vehículo")
            if column in df.columns
        ]
        search_mask = df[searchable_columns].fillna("").astype(str).apply(
            lambda column: column.str.contains(busqueda.strip(), case=False, regex=False)
        ).any(axis=1)
        df = df[search_mask].copy()

    # Columnas compactas para comparar sesiones sin perder campos en las exportaciones.
    compact_columns = ["📅 Inicio", "Folio", "🔌 Cargador", "📋 Estado", "⚡ Energía (kWh)", "⏱️ Duración (min)"]
    cols_display = []
    for col in ["Folio", "🔌 Cargador", "Conector", "👤 Usuario", "🚗 Vehículo", "💳 NFC", "📅 Inicio", "📅 Fin",
                "🔋 Energía inicial", "🔋 Energía final", "⚡ Energía (kWh)", "⏱️ Duración (min)", "📋 Estado",
                "🛑 Motivo de finalización", "🎯 Beneficio"]:
        if col in df.columns:
            cols_display.append(col)

    if df.empty:
        st.info("ℹ️ No hay sesiones que coincidan con la búsqueda.")
        st.stop()

    total_energia = (
        pd.to_numeric(df["⚡ Energía (kWh)"], errors="coerce").fillna(0).sum()
        if "⚡ Energía (kWh)" in df.columns
        else 0
    )
    active_count = (
        df["📋 Estado"].astype(str).str.lower().isin(["activa", "iniciada"]).sum()
        if "📋 Estado" in df.columns
        else 0
    )
    average_energy = total_energia / len(df)

    # Resumen de los resultados filtrados.
    metric_columns = st.columns(3)
    with metric_columns[0]:
        st.metric("Sesiones encontradas", len(df))
    with metric_columns[1]:
        st.metric("Energía consumida", f"{total_energia:.2f} kWh", f"Promedio de {average_energy:.2f} kWh por sesión")
    with metric_columns[2]:
        st.metric("Sesiones activas", int(active_count))

    st.divider()

    st.markdown("### Sesiones")
    page_size = 10
    page_count = (len(df) + page_size - 1) // page_size
    page_key = "sessions_history_page"
    current_page = min(st.session_state.get(page_key, 1), page_count)
    st.session_state[page_key] = current_page
    start_index = (current_page - 1) * page_size
    end_index = min(start_index + page_size, len(df))
    visible_columns = [column for column in compact_columns if column in df.columns]
    table_df = df.iloc[start_index:end_index][visible_columns].copy()
    if "⚡ Energía (kWh)" in table_df.columns:
        table_df["⚡ Energía (kWh)"] = pd.to_numeric(
            table_df["⚡ Energía (kWh)"], errors="coerce"
        ).round(2)
    if "⏱️ Duración (min)" in table_df.columns:
        table_df["⏱️ Duración (min)"] = table_df["⏱️ Duración (min)"].apply(format_duration)
    if "📋 Estado" in table_df.columns:
        status_labels = {
            "finalizada": "🟢 Finalizada",
            "activa": "🔵 Activa",
            "iniciada": "🔵 Iniciada",
            "interrumpida": "🟠 Interrumpida",
            "error": "🔴 Error",
        }
        table_df["📋 Estado"] = table_df["📋 Estado"].apply(
            lambda status: status_labels.get(str(status).lower(), str(status).title())
        )
    st.dataframe(
        table_df,
        use_container_width=True,
        hide_index=True,
    )

    pagination_columns = st.columns([2, 1, 2, 1])
    with pagination_columns[0]:
        st.caption(f"Mostrando {start_index + 1}–{end_index} de {len(df)} sesiones")
    with pagination_columns[1]:
        if st.button("Anterior", disabled=current_page <= 1, use_container_width=True):
            st.session_state[page_key] = current_page - 1
            st.rerun()
    with pagination_columns[2]:
        st.caption(f"Página {current_page} de {page_count}")
    with pagination_columns[3]:
        if st.button("Siguiente", disabled=current_page >= page_count, use_container_width=True):
            st.session_state[page_key] = current_page + 1
            st.rerun()

    detail_options = {"Selecciona una sesión": None}
    for row_index, row in df.iterrows():
        detail_label = (
            f"{row_index + 1}. Folio {row.get('Folio', 'No disponible')} · "
            f"{row.get('🔌 Cargador', 'No disponible')} · "
            f"{row.get('📅 Inicio', 'No disponible')}"
        )
        detail_options[detail_label] = row_index

    selected_detail = st.selectbox("Consultar detalle de sesión", list(detail_options))
    selected_index = detail_options[selected_detail]
    if selected_index is not None:
        selected_session = df.loc[selected_index]
        with st.container(border=True):
            st.markdown(f"#### {selected_session.get('Folio', 'Sesión')}")
            detail_metrics = st.columns(3)
            with detail_metrics[0]:
                st.markdown(f"**Cargador**  \n{selected_session.get('🔌 Cargador', 'No disponible')}")
            with detail_metrics[1]:
                st.markdown(f"**Estado**  \n{selected_session.get('📋 Estado', 'No disponible')}")
            with detail_metrics[2]:
                st.markdown(
                    f"**Energía total**  \n{selected_session.get('⚡ Energía (kWh)', 'No disponible')} kWh"
                )

            detail_columns = [
                ("Inicio", "📅 Inicio"),
                ("Finalización", "📅 Fin"),
                ("Duración", "⏱️ Duración (min)"),
                ("Usuario", "👤 Usuario"),
                ("Vehículo", "🚗 Vehículo"),
                ("Conector", "Conector"),
                ("Beneficio", "🎯 Beneficio"),
                ("Motivo de finalización", "🛑 Motivo de finalización"),
            ]
            for label, column in detail_columns:
                if column in selected_session.index:
                    st.write(f"**{label}:** {selected_session.get(column) or 'No disponible'}")

            transaction_id = selected_session.get("transaction_id")
            if pd.notna(transaction_id):
                st.markdown("##### Mediciones de la sesión")
                from utils.api_client import get_meter_values

                meter_values = get_meter_values(int(transaction_id))
                if isinstance(meter_values, list) and meter_values:
                    rows = []
                    for value in meter_values:
                        for sampled in value.get("sampled_values", []):
                            for item in sampled.get("sampled_value", []):
                                rows.append({
                                    "Fecha": sampled.get("timestamp") or value.get("timestamp", "Sin fecha"),
                                    "Medición": item.get("measurand", "Sin medición"),
                                    "Valor": item.get("value", "Sin dato"),
                                    "Unidad": item.get("unit", "Sin unidad"),
                                    "Conector": value.get("connector_id", "Sin identificador"),
                                    "Transacción": value.get("transaction_id", "Sin identificador de transacción"),
                                })
                    if rows:
                        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                    else:
                        st.info("No hay mediciones disponibles para esta sesión.")
                elif isinstance(meter_values, dict) and meter_values.get("error"):
                    st.warning("No se pudieron cargar las mediciones de la sesión.")
                else:
                    st.info("No hay mediciones disponibles para esta sesión.")
    
    # Opciones de exportación
    st.markdown("### Exportar datos")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        csv = df[cols_display].to_csv(index=False, encoding='utf-8-sig')
        st.download_button(
            label="📥 Descargar como CSV",
            data=csv,
            file_name=f"sesiones_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True
        )
    
    with col2:
        st.caption(
            "CSV respeta estado y búsqueda. Excel aplica los filtros de fecha, cargador y usuario."
        )
        export_params = {"fmt": "xlsx"}
        for field in ("fecha_inicio", "fecha_fin", "charge_point_id"):
            if field in params:
                export_params[field] = params[field]
        if params.get("usuario_id"):
            export_params["usuario_id"] = params["usuario_id"]

        excel_response = requests.get(
            f"{get_base_url()}/reportes/sesiones/export",
            headers=get_headers(),
            params=export_params,
            timeout=20,
        )
        excel_data = excel_response.content
        excel_content_type = excel_response.headers.get("Content-Type", "")
        excel_is_valid = (
            excel_response.status_code == 200
            and excel_content_type.startswith(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            and excel_data[:2] == b"PK"
        )

        if excel_is_valid:
            st.download_button(
                label="📊 Descargar como Excel",
                data=excel_data,
                file_name=f"sesiones_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        else:
            st.info("ℹ️ No se pudo generar la exportación Excel.")
    
    with col3:
        if st.button("🔄 Actualizar", use_container_width=True):
            st.rerun()
    
    st.divider()
    
    # Análisis detallado por cargador
    if "🔌 Cargador" in df.columns:
        st.markdown("**Análisis por Cargador**")
        resumen_cargadores = df.groupby("🔌 Cargador").agg({
            "⚡ Energía (kWh)": "sum",
            "⏱️ Duración (min)": "sum",
            "📋 Estado": "count"
        }).rename(columns={"📋 Estado": "Sesiones"})
        st.dataframe(resumen_cargadores, use_container_width=True)
    
    # Análisis por usuario
    if "👤 Usuario" in df.columns:
        st.markdown("**Análisis por Usuario**")
        resumen_usuarios = df.groupby("👤 Usuario").agg({
            "⚡ Energía (kWh)": "sum",
            "⏱️ Duración (min)": "sum",
            "📋 Estado": "count"
        }).rename(columns={"📋 Estado": "Sesiones"})
        st.dataframe(resumen_usuarios, use_container_width=True)