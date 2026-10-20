import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from collections import Counter
from datetime import datetime, timedelta, timezone
import pandas as pd


def get_chart_template():
    """Retorna el template según el tema de la aplicación."""
    return "plotly_white" if st.session_state.get("theme") == "light" else "plotly_dark"


def _style_analysis_chart(fig, height=350, margin=None):
    fig.update_layout(
        template=get_chart_template(),
        height=height,
        margin=margin or dict(l=55, r=25, t=25, b=55),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(size=12),
        hovermode="closest",
    )
    fig.update_xaxes(
        showgrid=False,
        zerolinecolor="#cbd5e1",
        automargin=True,
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(148,163,184,0.2)",
        zerolinecolor="#cbd5e1",
        automargin=True,
    )
    return fig


def render_consumption_chart(data):
    """Gráfica de consumo semanal con barras y línea de cargas."""
    data_by_date = {item["fecha"]: item for item in (data or [])}
    today = datetime.now(timezone.utc).date()
    days = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    fechas = [day.isoformat() for day in days]
    labels = [
        f"{('Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom')[day.weekday()]} {day.day}"
        for day in days
    ]
    energias = [data_by_date.get(fecha, {}).get("energia_kwh", 0) for fecha in fechas]
    cargas = [data_by_date.get(fecha, {}).get("num_cargas", 0) for fecha in fechas]
    missing_dates = [
        fecha for fecha in fechas
        if fecha not in data_by_date
    ]
    axis_max = max(max(energias, default=0) * 1.2, 1)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=energias, name="Energía (kWh)",
        marker_color="#3b82f6", marker_line_width=0,
        text=[f"{energy:.1f}" if fecha in data_by_date else "" for fecha, energy in zip(fechas, energias)],
        textposition="outside",
        customdata=cargas,
        hovertemplate="<b>%{x}</b><br>Energía: %{y:.1f} kWh<br>Cargas: %{customdata}<extra></extra>"
    ))
    if missing_dates:
        missing_labels = [labels[fechas.index(fecha)] for fecha in missing_dates]
        fig.add_trace(go.Bar(
            x=missing_labels,
            y=[axis_max * 0.035] * len(missing_dates),
            name="Sin registros",
            marker_color="#cbd5e1",
            marker_line_width=0,
            text=["—"] * len(missing_dates),
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>Sin sesiones registradas<extra></extra>"
        ))
    fig.add_trace(go.Scatter(
        x=labels, y=cargas, name="Nº Cargas", mode="lines+markers",
        line=dict(color="#f59e0b", width=3), 
        marker=dict(size=8),
        yaxis="y2",
        hovertemplate="<b>%{x}</b><br>Cargas: %{y}<extra></extra>"
    ))

    fig.update_layout(
        template=get_chart_template(),
        barmode="overlay",
        xaxis_title="Día de la semana",
        yaxis_title="Energía (kWh)",
        yaxis=dict(range=[0, axis_max]),
        yaxis2=dict(title="Número de Cargas", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        hovermode="x unified",
        margin=dict(l=20, r=80, t=60, b=20), 
        height=350,
        showlegend=True
    )
    period_caption = "Últimos 7 días"
    if not data_by_date:
        period_caption += " · Sin sesiones registradas"
    st.caption(period_caption)
    st.plotly_chart(fig, use_container_width=True)


def render_charger_status_chart(chargers):
    """Gráfica de cargadores por estado, incluyendo los estados sin cargadores."""
    status_colors = {
        "disponible": "#10b981",
        "ocupado": "#3b82f6",
        "preparando": "#f59e0b",
        "mantenimiento": "#f97316",
        "fuera_de_servicio": "#6b7280",
        "falla": "#ef4444",
        "desconectado": "#94a3b8",
    }
    status_labels = {
        "disponible": "Disponible",
        "ocupado": "Ocupado",
        "preparando": "Preparando",
        "mantenimiento": "Mantenimiento",
        "fuera_de_servicio": "Fuera de servicio",
        "falla": "Falla",
        "desconectado": "Desconectado",
    }
    status_order = list(status_colors)
    counts = Counter(
        charger.get("estado_general") or "desconocido"
        for charger in (chargers or [])
    )
    for status in counts:
        if status not in status_colors:
            status_order.append(status)
            status_labels[status] = status.replace("_", " ").capitalize()
            status_colors[status] = "#8b5cf6"

    labels = [status_labels[status] for status in status_order]
    values = [counts.get(status, 0) for status in status_order]
    colors = [status_colors[status] for status in status_order]
    max_count = max(values, default=0)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=values,
        y=labels,
        orientation="h",
        marker_color=colors,
        hovertemplate="<b>%{y}</b><br>Cargadores: %{x}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=[-0.35] * len(labels),
        y=labels,
        mode="markers",
        marker=dict(color=colors, size=9, symbol="square"),
        hoverinfo="skip",
        showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=[max_count + 0.55] * len(labels),
        y=labels,
        mode="text",
        text=[str(value) for value in values],
        textfont=dict(color="#1f2937" if get_chart_template() == "plotly_white" else "#f8fafc"),
        hoverinfo="skip",
        showlegend=False,
        cliponaxis=False,
    ))
    fig.update_layout(
        template=get_chart_template(),
        showlegend=False,
        xaxis=dict(
            title="Número de cargadores",
            range=[-0.65, max(max_count + 1, 1.25)],
            tickmode="array",
            tickvals=list(range(max_count + 1)),
        ),
        yaxis=dict(autorange="reversed", showgrid=False, title=None),
        margin=dict(l=125, r=25, t=20, b=45),
        height=350,
        bargap=0.38,
    )
    st.caption(f"Distribución por estado · {len(chargers or [])} cargadores")
    st.plotly_chart(fig, use_container_width=True)


def render_daily_consumption_chart(data):
    """Gráfica de barras con el consumo de cada uno de los últimos siete días."""
    rows = data.to_dict("records") if isinstance(data, pd.DataFrame) else (data or [])
    energy_by_date = {
        str(item["fecha"])[:10]: float(item.get("energia_kwh", 0) or 0)
        for item in rows
    }
    today = datetime.now(timezone.utc).date()
    days = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    labels = [
        f"{('Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom')[day.weekday()]} {day.day}"
        for day in days
    ]
    energies = [energy_by_date.get(day.isoformat(), 0) for day in days]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels,
        y=energies,
        marker_color="#3b82f6",
        marker_line_width=0,
        text=[f"{value:.1f}" if value else "" for value in energies],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>Consumo: %{y:.2f} kWh<extra></extra>",
    ))
    _style_analysis_chart(fig, margin=dict(l=55, r=25, t=20, b=55))
    fig.update_layout(showlegend=False, bargap=0.35)
    fig.update_xaxes(title="Día de la semana")
    fig.update_yaxes(title="Energía (kWh)", rangemode="tozero")
    st.caption("Últimos 7 días · Sin sesiones registradas" if not rows else "Consumo diario de los últimos 7 días")
    st.plotly_chart(fig, use_container_width=True)


def render_monthly_consumption_chart(data):
    """Gráfica de consumo mensual en barras."""
    if not data:
        st.info("Sin datos disponibles")
        return
    
    df = pd.DataFrame(data) if isinstance(data, list) else data
    
    fig = px.bar(
        df,
        x='mes' if 'mes' in df.columns else df.columns[0],
        y='energia_kwh' if 'energia_kwh' in df.columns else df.columns[1],
        title="Consumo Mensual",
        labels={'energia_kwh': 'Energía (kWh)', 'mes': 'Mes'},
        color_discrete_sequence=['#3b82f6'],
        text_auto='.1f'
    )
    
    fig.update_traces(
        textposition='outside',
        hovertemplate="<b>%{x}</b><br>Energía: %{y:.2f} kWh<extra></extra>"
    )
    fig.update_layout(
        template=get_chart_template(),
        margin=dict(l=20, r=20, t=60, b=20),
        height=300,
        showlegend=False
    )
    st.plotly_chart(fig, use_container_width=True)


def render_sessions_by_charger_chart(data):
    """Gráfica horizontal para comparar sesiones por cargador."""
    rows = data.to_dict("records") if isinstance(data, pd.DataFrame) else (data or [])
    rows = sorted(rows, key=lambda item: item.get("sesiones") or 0, reverse=True)
    labels = [str(item.get("charge_point_id") or "Sin identificador") for item in rows]
    values = [int(item.get("sesiones", 0) or 0) for item in rows]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=values,
        y=labels,
        orientation="h",
        marker_color="#10b981",
        marker_line_width=0,
        text=[str(value) for value in values],
        textposition="outside",
        cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>Sesiones: %{x}<extra></extra>",
    ))
    height = max(350, min(600, 220 + len(rows) * 32))
    _style_analysis_chart(fig, height=height, margin=dict(l=35, r=35, t=20, b=55))
    fig.update_layout(showlegend=False, bargap=0.4)
    fig.update_xaxes(title="Número de sesiones", rangemode="tozero")
    fig.update_yaxes(title=None, autorange="reversed", showgrid=False)
    if not rows:
        fig.add_annotation(
            text="Sin sesiones registradas",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(size=14, color="#64748b"),
        )
        fig.update_xaxes(range=[0, 1], tickvals=[0, 1])
    st.caption(f"Sesiones por estación · {len(rows)} cargadores")
    st.plotly_chart(fig, use_container_width=True)


def render_energy_by_charger_chart(data):
    """Gráfica horizontal para comparar energía entre estaciones."""
    rows = data.to_dict("records") if isinstance(data, pd.DataFrame) else (data or [])
    rows = sorted(rows, key=lambda item: item.get("energia_kwh") or 0, reverse=True)
    labels = [str(item.get("charge_point_id") or "Sin identificador") for item in rows]
    values = [float(item.get("energia_kwh", 0) or 0) for item in rows]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=values,
        y=labels,
        orientation="h",
        marker_color="#8b5cf6",
        marker_line_width=0,
        text=[f"{value:.1f}" for value in values],
        textposition="outside",
        cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>Energía: %{x:.2f} kWh<extra></extra>",
    ))
    height = max(350, min(600, 220 + len(rows) * 32))
    _style_analysis_chart(fig, height=height, margin=dict(l=35, r=45, t=20, b=55))
    fig.update_layout(showlegend=False, bargap=0.4)
    fig.update_xaxes(title="Energía suministrada (kWh)", rangemode="tozero")
    fig.update_yaxes(title=None, autorange="reversed", showgrid=False)
    if not rows:
        fig.add_annotation(
            text="Sin energía registrada",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(size=14, color="#64748b"),
        )
        fig.update_xaxes(range=[0, 1], tickvals=[0, 1])
    st.caption(f"Energía acumulada · {len(rows)} estaciones")
    st.plotly_chart(fig, use_container_width=True)


def render_usage_trend_chart(data):
    """Compara las sesiones, energía y horas de uso por cargador en el tiempo."""
    dates = data.get("dates", []) if isinstance(data, dict) else []
    stations = data.get("stations", []) if isinstance(data, dict) else []
    stations = sorted(
        stations,
        key=lambda station: (
            -int(station.get("total_sesiones", 0) or 0),
            str(station.get("charge_point_id", "")),
        ),
    )
    colors = px.colors.qualitative.Dark24
    metrics = (
        ("sesiones", "Sesiones por día", "Sesiones"),
        ("energia_kwh", "Energía consumida", "Consumo (kWh)"),
        ("horas_uso", "Tiempo de uso", "Horas"),
    )
    fig = make_subplots(
        rows=len(metrics),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.1,
        subplot_titles=[metric[1] for metric in metrics],
    )

    for index, station in enumerate(stations):
        station_id = str(station.get("charge_point_id", "Sin identificador"))
        color = colors[index % len(colors)]
        for row, (key, _, _) in enumerate(metrics, start=1):
            values = station.get(key, [])
            if len(values) != len(dates):
                values = (list(values) + [0] * len(dates))[:len(dates)]
            fig.add_trace(
                go.Scatter(
                    x=dates,
                    y=values,
                    mode="lines+markers",
                    name=station_id,
                    legendgroup=station_id,
                    showlegend=row == 1,
                    line=dict(color=color, width=2.5),
                    marker=dict(size=5),
                    hovertemplate=(
                        f"<b>{station_id}</b><br>"
                        "%{x|%d %b %Y}<br>"
                        f"{metrics[row - 1][2]}: %{{y:.2f}}<extra></extra>"
                    ),
                ),
                row=row,
                col=1,
            )

    _style_analysis_chart(
        fig,
        height=590,
        margin=dict(l=75, r=30, t=95, b=60),
    )
    fig.update_layout(
        showlegend=bool(stations),
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            groupclick="togglegroup",
        ),
    )
    for row, (_, _, axis_title) in enumerate(metrics, start=1):
        fig.update_yaxes(title_text=axis_title, rangemode="tozero", row=row, col=1)
        if not stations:
            fig.update_yaxes(range=[0, 1], tickvals=[0, 1], row=row, col=1)
    fig.update_xaxes(
        title_text="Fecha",
        tickformat="%d %b",
        tickangle=-30,
        dtick="D3",
        row=len(metrics),
        col=1,
    )

    if not stations:
        fig.add_annotation(
            text="No hay cargadores registrados",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(size=15, color="#64748b"),
        )
        st.caption("Comparación de los últimos 30 días")
    else:
        most_used = stations[0]
        total_sessions = int(most_used.get("total_sesiones", 0) or 0)
        if total_sessions:
            st.caption(
                f"Últimos 30 días · Más usado por número de sesiones: "
                f"{most_used.get('charge_point_id')} ({total_sessions} sesiones)"
            )
        else:
            st.caption("Últimos 30 días · Sin sesiones registradas en el periodo")
    st.plotly_chart(fig, use_container_width=True)


def render_user_activity_chart(data):
    """Gráfica de actividad de usuarios."""
    if not data:
        st.info("Sin datos disponibles")
        return
    
    df = pd.DataFrame(data) if isinstance(data, list) else data
    
    fig = px.bar(
        df,
        x='usuario' if 'usuario' in df.columns else df.columns[0],
        y='cargas' if 'cargas' in df.columns else df.columns[1],
        title="Actividad de Usuarios (Top 10)",
        labels={'cargas': 'Número de Cargas', 'usuario': 'Usuario'},
        color_discrete_sequence=['#8b5cf6']
    )
    
    fig.update_layout(
        template=get_chart_template(),
        margin=dict(l=20, r=20, t=60, b=80),
        height=300,
        showlegend=False,
        xaxis_tickangle=-45
    )
    fig.update_traces(
        hovertemplate="<b>%{x}</b><br>Cargas: %{y}<extra></extra>"
    )
    st.plotly_chart(fig, use_container_width=True)


def render_reward_distribution_chart(data):
    """Gráfica de distribución de recompensas."""
    if not data:
        st.info("Sin datos disponibles")
        return
    
    df = pd.DataFrame(data) if isinstance(data, list) else data
    
    fig = px.pie(
        df,
        names='tipo_recompensa' if 'tipo_recompensa' in df.columns else df.columns[0],
        values='cantidad' if 'cantidad' in df.columns else df.columns[1],
        title="Distribución de Recompensas",
        color_discrete_sequence=px.colors.qualitative.Set3
    )
    
    fig.update_layout(
        template=get_chart_template(),
        margin=dict(l=20, r=20, t=60, b=20),
        height=300
    )
    fig.update_traces(
        hovertemplate="<b>%{label}</b><br>Cantidad: %{value}<br>Porcentaje: %{percent}<extra></extra>"
    )
    st.plotly_chart(fig, use_container_width=True)