import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from utils.api_client import api_get
from components.kpi_cards import render_kpi_cards, render_mini_stats
from components.charts import (
    render_consumption_chart, 
    render_charger_status_chart,
    render_daily_consumption_chart,
    render_monthly_consumption_chart,
    render_sessions_by_charger_chart,
    render_energy_by_charger_chart,
    render_usage_trend_chart,
    render_user_activity_chart
)
from components.sidebar import render_sidebar, _redirect_to_login

# Configuración de página
st.set_page_config(
    page_title="ChargeCore | Dashboard Principal", 
    page_icon="⚡", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

# Estilos CSS personalizados
def apply_dashboard_style():
    st.markdown("""
    <style>
    div[data-testid="stSkeleton"] {
        display: none !important;
    }
    .main {
        background-color: #f8fafc;
    }
    .block-container {
        width: 100%;
        padding: 1.75rem clamp(1rem, 3vw, 2.5rem) 2.5rem;
        max-width: 1240px;
        margin: 0 auto;
    }
    .dashboard-header {
        margin-bottom: 2rem;
        padding-bottom: 1rem;
        border-bottom: 2px solid #e2e8f0;
    }
    .dashboard-subtitle {
        color: #64748b;
        margin-top: -0.8rem;
        margin-bottom: 1.5rem;
        font-size: 0.95rem;
    }
    .section-heading {
        color: #1e293b;
        margin-top: 1.5rem;
        margin-bottom: 1rem;
        font-weight: 700;
        font-size: 1.2rem;
        border-left: 4px solid #3b82f6;
        padding-left: 12px;
    }
    .empty-state {
        color: #64748b;
        font-size: 0.9rem;
        padding: 1rem;
        text-align: center;
    }
    @media (max-width: 768px) {
        .block-container {
            padding: 1.25rem 1rem 2rem;
        }

        .dashboard-header {
            margin-bottom: 1.25rem;
        }
    }
    </style>
    """, unsafe_allow_html=True)

apply_dashboard_style()

# Verificar autenticación
if not st.session_state.get("authenticated"):
    _redirect_to_login()

# Renderizar sidebar
render_sidebar()

# ============= ENCABEZADO DEL DASHBOARD =============
st.markdown('<div class="dashboard-header">', unsafe_allow_html=True)
col1, col2 = st.columns([3, 1])
with col1:
    st.markdown("# ⚡ Dashboard Principal")
    st.markdown('<div class="dashboard-subtitle">Centro de monitoreo en tiempo real del sistema ChargeCore</div>', unsafe_allow_html=True)
with col2:
    # Información de actualización
    if st.button("🔄 Actualizar"):
        st.rerun()
st.markdown('</div>', unsafe_allow_html=True)

# ============= KPIs PRINCIPALES =============
st.markdown('<div class="section-heading">📊 Indicadores Clave de Desempeño</div>', unsafe_allow_html=True)

with st.spinner("Cargando métricas..."):
    kpis = api_get("/dashboard/kpi")

if "error" in kpis:
    st.error(f"❌ Error al cargar KPIs: {kpis['error']}")
else:
    render_kpi_cards(kpis)

st.divider()

# ============= GRÁFICAS PRINCIPALES =============
st.markdown('<div class="section-heading">📈 Análisis de Consumo y Sesiones</div>', unsafe_allow_html=True)

col1, col2 = st.columns(2)

# Consumo Semanal
with col1:
    st.subheader("📊 Consumo Semanal")
    with st.spinner("Cargando..."):
        consumo_data = api_get("/dashboard/grafica-consumo-semanal")
    if "error" not in consumo_data:
        render_consumption_chart(consumo_data)
    else:
        st.info("No hay datos de consumo recientes")

# Estado de Cargadores
with col2:
    st.subheader("🔋 Estado de Cargadores")
    with st.spinner("Cargando..."):
        chargers = api_get("/cargadores/")
    if "error" not in chargers:
        render_charger_status_chart(chargers)
    else:
        st.info("No hay cargadores registrados")

st.divider()

# ============= ANÁLISIS AVANZADO =============
st.markdown('<div class="section-heading">🎯 Análisis Avanzado</div>', unsafe_allow_html=True)

tab1, tab2, tab3, tab4 = st.tabs(["Consumo Diario", "Sesiones", "Energía por Estación", "Tendencias"])

with tab1:
    st.subheader("Consumo Diario (kWh)")
    with st.spinner("Cargando..."):
        daily_data = api_get("/dashboard/grafica-consumo-diario")
    if isinstance(daily_data, dict) and "error" in daily_data:
        st.error(f"No se pudo cargar el consumo diario: {daily_data['error']}")
    else:
        render_daily_consumption_chart(daily_data)

with tab2:
    st.subheader("Sesiones por Cargador")
    with st.spinner("Cargando..."):
        sessions_data = api_get("/dashboard/sesiones-por-cargador")
    if isinstance(sessions_data, dict) and "error" in sessions_data:
        st.error(f"No se pudieron cargar las sesiones: {sessions_data['error']}")
    else:
        render_sessions_by_charger_chart(sessions_data)

with tab3:
    st.subheader("Energía Suministrada por Estación")
    with st.spinner("Cargando..."):
        energy_data = api_get("/dashboard/energia-por-cargador")
    if isinstance(energy_data, dict) and "error" in energy_data:
        st.error(f"No se pudo cargar la energía: {energy_data['error']}")
    else:
        render_energy_by_charger_chart(energy_data)

with tab4:
    st.subheader("Tendencia de Uso")
    with st.spinner("Cargando..."):
        trend_data = api_get("/dashboard/tendencia-uso")
    if isinstance(trend_data, dict) and "error" in trend_data:
        st.error(f"No se pudo cargar la tendencia: {trend_data['error']}")
    else:
        render_usage_trend_chart(trend_data)

st.divider()

# ============= SESIONES ACTIVAS =============
st.markdown('<div class="section-heading">⚙️ Sesiones Activas</div>', unsafe_allow_html=True)

with st.spinner("Consultando sesiones activas..."):
    activas = api_get("/sesiones/activas")

if "error" in activas or not activas:
    st.success("✅ No hay sesiones activas en este momento")
else:
    st.info(f"🔋 {len(activas)} {'sesión activa' if len(activas) == 1 else 'sesiones activas'}")
    sesiones_activas_data = []
    for sesion in activas:
        inicio = sesion.get("fecha_inicio", "")
        estado = sesion.get("estado", "activa").lower()
        estado_icon = "🟢" if estado == "activa" else "🟡"
        sesiones_activas_data.append({
            "🔌 ID del cargador": sesion.get("charge_point_id", "Sin identificador"),
            "ID del conector": sesion.get("connector_id", "Sin identificador"),
            "⏱️ Inicio": inicio[:19].replace("T", " ") if inicio else "--",
            "⚡ Energía": f"{sesion.get('energia_consumida_kwh', 0):.2f} kWh",
            "Estado": f"{estado_icon} {estado.capitalize()}",
        })
    st.dataframe(
        pd.DataFrame(sesiones_activas_data),
        use_container_width=True,
        hide_index=True,
        height=145,
    )

st.divider()

# ============= ÚLTIMAS SESIONES FINALIZADAS =============
st.markdown('<div class="section-heading">📋 Últimas Sesiones Finalizadas</div>', unsafe_allow_html=True)

with st.spinner("Cargando historial..."):
    ultimas = api_get("/sesiones/", params={"estado": "finalizada", "limit": 15})

if "error" not in ultimas and ultimas:
    df_data = []
    for s in ultimas:
        df_data.append({
            "🔌 ID del cargador": s.get("charge_point_id", ""),
            "ID del conector": s.get("connector_id", ""),
            "📅 Inicio": s.get("fecha_inicio", "")[:19].replace("T", " ") if s.get("fecha_inicio") else "",
            "⚡ Energía (kWh)": round(s.get("energia_consumida_kwh", 0), 2),
            "⏱️ Duración (min)": s.get("duracion_minutos", 0),
            "👤 ID de usuario": s.get("usuario_id", "Desconocido"),
            "🎁 Beneficio": s.get("beneficio_aplicado", "Ninguno") or "Ninguno"
        })
    
    df = pd.DataFrame(df_data)
    st.dataframe(df, use_container_width=True, hide_index=True, height=145)
else:
    st.info("No hay sesiones finalizadas registradas")

st.divider()

# ============= ALERTAS Y EVENTOS RECIENTES =============
col1, col2 = st.columns(2)

with col1:
    st.markdown('<div class="section-heading">🔔 Alertas Recientes</div>', unsafe_allow_html=True)

    with st.spinner("Cargando alertas recientes..."):
        alertas_recientes = api_get("/dashboard/alertas-recientes")

    if isinstance(alertas_recientes, dict) and "error" in alertas_recientes:
        st.info("No fue posible cargar las alertas recientes.")
    elif not alertas_recientes:
        st.info("No hay alertas recientes.")
    else:
        alertas_data = []
        for alerta in alertas_recientes:
            datos = alerta.get("datos")
            alertas_data.append({
                "Nivel": alerta.get("nivel", "Sin nivel").capitalize(),
                "Módulo": alerta.get("modulo", "Sin módulo"),
                "Mensaje": alerta.get("mensaje", "Sin mensaje"),
                "Datos": str(datos) if datos else "",
                "Fecha": str(alerta.get("timestamp", "Sin fecha"))[:19].replace("T", " ")
            })
        st.dataframe(pd.DataFrame(alertas_data), use_container_width=True, hide_index=True, height=145)

with col2:
    st.markdown('<div class="section-heading">⚡ Eventos OCPP Recientes</div>', unsafe_allow_html=True)

    with st.spinner("Cargando eventos OCPP recientes..."):
        eventos_recientes = api_get("/dashboard/eventos-ocpp-recientes")

    if isinstance(eventos_recientes, dict) and "error" in eventos_recientes:
        st.info("No fue posible cargar los eventos OCPP recientes.")
    elif not eventos_recientes:
        st.info("No hay eventos OCPP recientes.")
    else:
        eventos_data = []
        for evento in eventos_recientes:
            payload = evento.get("payload")
            payload_resumido = str(payload) if payload else ""
            if len(payload_resumido) > 120:
                payload_resumido = f"{payload_resumido[:117]}..."
            eventos_data.append({
                "ID del cargador": evento.get("charge_point_id", "Sin identificador"),
                "Tipo": evento.get("tipo_mensaje", "Sin tipo de mensaje"),
                "Acción": evento.get("accion", "Sin acción"),
                "Datos OCPP": payload_resumido,
                "Fecha": str(evento.get("timestamp", "Sin fecha"))[:19].replace("T", " ")
            })
        st.dataframe(pd.DataFrame(eventos_data), use_container_width=True, hide_index=True, height=145)