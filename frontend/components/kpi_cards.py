import streamlit as st


def render_kpi_cards(kpis):
    """Renderiza tarjetas KPI modernas y profesionales."""
    
    # Mapeo de claves a etiquetas y iconos
    kpi_config = {
        "cargadores_registrados": {"label": "Cargadores Registrados", "icon": "⚡", "color": "#3b82f6"},
        "cargadores_conectados": {"label": "Cargadores Conectados", "icon": "🟢", "color": "#10b981"},
        "cargadores_cargando": {"label": "En Carga Actualmente", "icon": "🔋", "color": "#f59e0b"},
        "usuarios_registrados": {"label": "Usuarios Registrados", "icon": "👥", "color": "#8b5cf6"},
        "energia_hoy_kwh": {"label": "Energía Hoy (kWh)", "icon": "⚡", "color": "#ec4899"},
        "sesiones_hoy": {"label": "Sesiones Hoy", "icon": "📊", "color": "#06b6d4"},
        "total_cargas": {"label": "Total de Cargas", "icon": "🔌", "color": "#14b8a6"},
        "cargas_hoy": {"label": "Cargas Hoy", "icon": "📅", "color": "#f97316"},
        "energia_total_kwh": {"label": "Energía Total (kWh)", "icon": "⚡", "color": "#6366f1"},
        "cargadores_activos": {"label": "Cargadores Activos", "icon": "🟢", "color": "#10b981"},
        "cargadores_ocupados": {"label": "Cargadores Ocupados", "icon": "🔵", "color": "#3b82f6"},
        "cargadores_mantenimiento": {"label": "En Mantenimiento", "icon": "🟡", "color": "#f59e0b"},
        "cargadores_fuera_de_servicio": {"label": "Fuera de Servicio", "icon": "⚫", "color": "#6b7280"},
        "total_usuarios": {"label": "Usuarios Registrados", "icon": "👥", "color": "#8b5cf6"},
        "total_vehiculos": {"label": "Vehículos Registrados", "icon": "🚗", "color": "#059669"},
        "recompensas_pendientes": {"label": "Recompensas Pendientes", "icon": "🎁", "color": "#f59e0b"},
        "sesiones_activas": {"label": "Sesiones Activas", "icon": "🔋", "color": "#06b6d4"},
        "alertas_recientes": {"label": "Alertas Recientes", "icon": "⚠️", "color": "#ef4444"},
        "eventos_recientes": {"label": "Eventos Recientes", "icon": "⚡", "color": "#6366f1"}
    }
    
    # Filtrar KPIs válidos y organizarlos en filas de 3
    valid_kpis = {k: v for k, v in kpis.items() if k in kpi_config}
    
    if not valid_kpis:
        st.warning("No hay datos de KPI disponibles")
        return
    
    # Crear filas de 3 columnas
    items = list(valid_kpis.items())
    for i in range(0, len(items), 3):
        cols = st.columns(3)
        for col_idx, col in enumerate(cols):
            if i + col_idx < len(items):
                key, value = items[i + col_idx]
                config = kpi_config[key]
                
                with col:
                    # CSS personalizado para tarjetas KPI modernas
                    st.markdown(f"""
                    <div style="
                        background: linear-gradient(135deg, {config['color']}15 0%, {config['color']}05 100%);
                        border: 2px solid {config['color']}30;
                        border-radius: 12px;
                        padding: 16px 12px;
                        margin: 5px 0;
                        text-align: center;
                        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
                        transition: all 0.3s ease;
                    ">
                        <div style="font-size: 25px; margin-bottom: 6px;">{config['icon']}</div>
                        <div style="color: #64748b; font-size: 11px; line-height: 1.35; font-weight: 600; text-transform: uppercase; letter-spacing: 0.35px; margin-bottom: 6px;">
                            {config['label']}
                        </div>
                        <div style="color: {config['color']}; font-size: 28px; font-weight: 700; margin-bottom: 2px;">
                            {value if isinstance(value, (int, float)) else value}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)


def render_mini_stats(label: str, value: str | int | float, icon: str = "📊", color: str = "#3b82f6"):
    """Renderiza una estadística mini para uso en diferentes contextos."""
    st.markdown(f"""
    <div style="
        background: linear-gradient(135deg, {color}15 0%, {color}05 100%);
        border-left: 4px solid {color};
        border-radius: 8px;
        padding: 16px;
        margin: 8px 0;
    ">
        <div style="display: flex; align-items: center; justify-content: space-between;">
            <div>
                <div style="color: #64748b; font-size: 12px; font-weight: 600; margin-bottom: 4px;">{label}</div>
                <div style="color: {color}; font-size: 24px; font-weight: 700;">{value}</div>
            </div>
            <div style="font-size: 32px;">{icon}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)