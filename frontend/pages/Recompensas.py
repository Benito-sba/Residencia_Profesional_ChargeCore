from datetime import datetime, time

import pandas as pd
import plotly.express as px
import streamlit as st

from components.sidebar import _redirect_to_login, render_sidebar
from utils.api_client import api_get, api_post, api_put
from utils.state import init_session_state


def paged_table(dataframe, key):
    page_size = 20
    page_count = max(1, (len(dataframe) + page_size - 1) // page_size)
    page_key = f"rewards_page_{key}"
    current_page = min(max(st.session_state.get(page_key, 1), 1), page_count)
    st.session_state[page_key] = current_page
    first_index = (current_page - 1) * page_size
    last_index = min(first_index + page_size, len(dataframe))

    st.dataframe(
        dataframe.iloc[first_index:last_index],
        use_container_width=True,
        hide_index=True,
    )

    columns = st.columns([2, 1, 1, 1, 2])
    with columns[0]:
        st.caption(f"Mostrando {first_index + 1}–{last_index} de {len(dataframe)} registros")
    with columns[1]:
        if st.button("Anterior", key=f"{key}_previous_page", disabled=current_page == 1):
            st.session_state[page_key] = current_page - 1
            st.rerun()
    with columns[2]:
        st.caption(f"Página {current_page} de {page_count}")
    with columns[3]:
        if st.button("Siguiente", key=f"{key}_next_page", disabled=current_page == page_count):
            st.session_state[page_key] = current_page + 1
            st.rerun()


st.set_page_config(
    page_title="ChargeCore | Recompensas",
    page_icon="🎁",
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
    .block-container {
        max-width: 1440px;
        padding: 1.8rem clamp(1rem, 3vw, 2.5rem) 2.5rem;
    }
    .reward-kicker {
        display: inline-block;
        margin-bottom: 0.45rem;
        padding: 0.28rem 0.65rem;
        border-radius: 999px;
        background: #f1eafb;
        color: #7652a2;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.05em;
        text-transform: uppercase;
    }
    .reward-subtitle {
        margin-top: -0.7rem;
        color: #64748b;
    }
    div[data-testid="stMetric"] {
        min-height: 112px;
        padding: 0.9rem 1rem;
        border: 1px solid #dce8e3;
        border-radius: 14px;
        background: #ffffff;
        box-shadow: 0 2px 10px rgba(15, 23, 42, 0.03);
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.6rem;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.88rem;
    }
    .reward-card-label {
        color: #64748b;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
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

catalogo_resultado = api_get("/recompensas/catalogo")
reglas_resultado = api_get("/recompensas/reglas")
recompensas_resultado = api_get("/recompensas/")
es_admin = st.session_state.get("role") == "admin"
usuarios_resultado = api_get("/usuarios/") if es_admin else []

for label, response in (
    ("catálogo de recompensas", catalogo_resultado),
    ("reglas de recompensas", reglas_resultado),
    ("recompensas emitidas", recompensas_resultado),
    ("usuarios", usuarios_resultado),
):
    if isinstance(response, dict) and "error" in response:
        st.error(f"No se pudieron cargar {label}: {response['error']}")
        st.stop()

catalogo = catalogo_resultado if isinstance(catalogo_resultado, list) else []
reglas = reglas_resultado if isinstance(reglas_resultado, list) else []
recompensas = recompensas_resultado if isinstance(recompensas_resultado, list) else []
usuarios = usuarios_resultado if isinstance(usuarios_resultado, list) else []

ahora = datetime.now()
recompensas_activas = sum(1 for item in catalogo if item.get("activa", True))
reglas_activas = sum(1 for regla in reglas if regla.get("activa", True))
recompensas_mes = 0
for reward in recompensas:
    issued_at = reward.get("fecha_otorgamiento")
    if issued_at:
        try:
            issued_date = datetime.fromisoformat(str(issued_at).replace("Z", "+00:00"))
            if (issued_date.year, issued_date.month) == (ahora.year, ahora.month):
                recompensas_mes += 1
        except ValueError:
            continue
recompensas_usadas = sum(1 for reward in recompensas if reward.get("usado", False))
tasa_canje = round(recompensas_usadas / len(recompensas) * 100) if recompensas else 0

st.title("🎁 Sistema de Recompensas")
st.markdown(
    '<div class="reward-subtitle">Administra beneficios, reglas y reconocimientos para tus usuarios.</div>',
    unsafe_allow_html=True,
)


def reward_date(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return ahora.date()


metric_columns = st.columns(4)
with metric_columns[0]:
    st.metric("Recompensas activas", recompensas_activas)
with metric_columns[1]:
    st.metric("Reglas activas", reglas_activas)
with metric_columns[2]:
    st.metric("Otorgadas este mes", recompensas_mes)
with metric_columns[3]:
    st.metric("Tasa de canje", f"{tasa_canje}%")

catalogo_tab, reglas_tab, otorgadas_tab, ranking_tab = st.tabs(
    ["Catálogo", "Reglas", "Otorgadas", "Ranking"]
)

with catalogo_tab:
    if es_admin:
        with st.expander("➕ Registrar Nueva Recompensa", expanded=False):
            with st.form("form_catalogo_recompensas"):
                form_columns = st.columns(2)
                with form_columns[0]:
                    name = st.text_input(
                        "Nombre de la recompensa",
                        key="create_reward_name",
                    )
                    company = st.text_input(
                        "Empresa o establecimiento",
                        key="create_reward_company",
                    )
                    description = st.text_area(
                        "Descripción",
                        key="create_reward_description",
                    )
                with form_columns[1]:
                    conditions = st.text_area(
                        "Condiciones",
                        key="create_reward_conditions",
                    )
                    start_date = st.date_input(
                        "Fecha de inicio",
                        value=ahora.date(),
                        key="create_reward_start",
                    )
                    end_date = st.date_input(
                        "Fecha de finalización",
                        value=ahora.date(),
                        key="create_reward_end",
                    )
                    active = st.checkbox(
                        "Recompensa activa",
                        value=True,
                        key="create_reward_active",
                    )
                save_catalog = st.form_submit_button(
                    "Crear recompensa",
                    type="primary",
                    use_container_width=True,
                )

            if save_catalog:
                if not name.strip() or not company.strip() or not description.strip():
                    st.error("Nombre, empresa y descripción son obligatorios.")
                elif start_date > end_date:
                    st.error("La fecha de inicio no puede ser posterior a la fecha de finalización.")
                else:
                    result = api_post(
                        "/recompensas/catalogo",
                        json={
                            "nombre": name.strip(),
                            "empresa": company.strip(),
                            "descripcion": description.strip(),
                            "condiciones": conditions.strip(),
                            "fecha_inicio": datetime.combine(start_date, time.min).isoformat(),
                            "fecha_finalizacion": datetime.combine(end_date, time.max).isoformat(),
                            "activa": active,
                        },
                    )
                    if isinstance(result, dict) and result.get("error"):
                        st.error(result["error"])
                    else:
                        st.success("Recompensa creada correctamente.")
                        st.rerun()

    heading_columns = st.columns([3, 1.4, 1])
    with heading_columns[0]:
        st.markdown("### Catálogo de beneficios")
        st.caption("Beneficios disponibles para asociar a reglas de carga.")
    with heading_columns[1]:
        busqueda_catalogo = st.text_input(
            "Buscar beneficio",
            placeholder="Buscar...",
            key="catalogo_busqueda",
            label_visibility="collapsed",
        )
    with heading_columns[2]:
        estado_catalogo_filtro = st.selectbox(
            "Estado del catálogo",
            ["Todas", "Activas", "Inactivas"],
            key="catalogo_estado_filtro",
            label_visibility="collapsed",
        )

    catalogo_filtrado = [
        item for item in catalogo
        if (
            estado_catalogo_filtro == "Todas"
            or (estado_catalogo_filtro == "Activas" and item.get("activa", True))
            or (estado_catalogo_filtro == "Inactivas" and not item.get("activa", True))
        )
        and busqueda_catalogo.casefold() in (
            f"{item.get('nombre', '')} {item.get('empresa', '')} {item.get('descripcion', '')}"
        ).casefold()
    ]

    if not catalogo_filtrado:
        st.info("No hay beneficios que coincidan con la consulta.")
    else:
        for row_start in range(0, len(catalogo_filtrado), 4):
            card_columns = st.columns(4)
            for column, item in zip(card_columns, catalogo_filtrado[row_start:row_start + 4]):
                with column:
                    with st.container(border=True):
                        status = "Activa" if item.get("activa", True) else "Inactiva"
                        st.caption(f"🎁  {status}")
                        st.markdown(f"#### {item.get('nombre', 'Sin nombre')}")
                        st.caption(item.get("empresa", "Sin establecimiento"))
                        st.write(item.get("descripcion", "Sin descripción"))
                        if item.get("condiciones"):
                            st.caption(f"Condiciones: {item['condiciones']}")
                        start_date = str(item.get("fecha_inicio") or "—")[:10]
                        end_date = str(item.get("fecha_finalizacion") or "—")[:10]
                        st.caption(f"Vigencia: {start_date} – {end_date}")

                        if es_admin:
                            with st.popover(
                                "✏️ Editar beneficio",
                                use_container_width=True,
                            ):
                                st.markdown(f"### Editar {item.get('nombre', 'beneficio')}")
                                with st.form(f"edit_reward_catalog_{item['_id']}"):
                                    form_columns = st.columns(2)
                                    with form_columns[0]:
                                        name = st.text_input(
                                            "Nombre de la recompensa",
                                            value=item.get("nombre", ""),
                                            key=f"edit_reward_name_{item['_id']}",
                                        )
                                        company = st.text_input(
                                            "Empresa o establecimiento",
                                            value=item.get("empresa", ""),
                                            key=f"edit_reward_company_{item['_id']}",
                                        )
                                        description = st.text_area(
                                            "Descripción",
                                            value=item.get("descripcion", ""),
                                            key=f"edit_reward_description_{item['_id']}",
                                        )
                                    with form_columns[1]:
                                        conditions = st.text_area(
                                            "Condiciones",
                                            value=item.get("condiciones", ""),
                                            key=f"edit_reward_conditions_{item['_id']}",
                                        )
                                        start_date = st.date_input(
                                            "Fecha de inicio",
                                            value=reward_date(item.get("fecha_inicio")),
                                            key=f"edit_reward_start_{item['_id']}",
                                        )
                                        end_date = st.date_input(
                                            "Fecha de finalización",
                                            value=reward_date(item.get("fecha_finalizacion")),
                                            key=f"edit_reward_end_{item['_id']}",
                                        )
                                        active = st.checkbox(
                                            "Recompensa activa",
                                            value=item.get("activa", True),
                                            key=f"edit_reward_active_{item['_id']}",
                                        )
                                    save_catalog = st.form_submit_button(
                                        "Guardar cambios",
                                        type="primary",
                                        use_container_width=True,
                                    )

                                if save_catalog:
                                    if not name.strip() or not company.strip() or not description.strip():
                                        st.error("Nombre, empresa y descripción son obligatorios.")
                                    elif start_date > end_date:
                                        st.error("La fecha de inicio no puede ser posterior a la fecha de finalización.")
                                    else:
                                        result = api_put(
                                            f"/recompensas/catalogo/{item['_id']}",
                                            json={
                                                "nombre": name.strip(),
                                                "empresa": company.strip(),
                                                "descripcion": description.strip(),
                                                "condiciones": conditions.strip(),
                                                "fecha_inicio": datetime.combine(
                                                    start_date, time.min
                                                ).isoformat(),
                                                "fecha_finalizacion": datetime.combine(
                                                    end_date, time.max
                                                ).isoformat(),
                                                "activa": active,
                                            },
                                        )
                                        if isinstance(result, dict) and result.get("error"):
                                            st.error(result["error"])
                                        else:
                                            st.success("Beneficio actualizado correctamente.")
                                            st.rerun()
                            if st.button(
                                "Desactivar" if item.get("activa", True) else "Activar",
                                key=f"catalog_status_{item['_id']}",
                                use_container_width=True,
                            ):
                                result = api_put(
                                    f"/recompensas/catalogo/{item['_id']}/estado",
                                    json={"activa": not item.get("activa", True)},
                                )
                                if isinstance(result, dict) and result.get("error"):
                                    st.error(result["error"])
                                else:
                                    st.rerun()

with reglas_tab:
    rule_heading = st.columns([4, 1])
    with rule_heading[0]:
        st.markdown("### Reglas de obtención")
        st.caption("Hitos que asignan recompensas automáticamente al alcanzar el número de cargas.")
    with rule_heading[1]:
        if es_admin:
            with st.popover("＋ Nueva regla", use_container_width=True):
                st.markdown("### Crear regla")
                available_catalog = {
                    f"{item.get('nombre', 'Sin nombre')} · {item.get('empresa', 'Sin empresa')}": item
                    for item in catalogo
                    if item.get("activa", True)
                }
                with st.form("new_rule_form"):
                    if not available_catalog:
                        st.info("Crea primero una recompensa activa en el catálogo.")
                    else:
                        required_loads = st.number_input(
                            "Cargas requeridas",
                            min_value=1,
                            value=1,
                            step=1,
                        )
                        reward_label = st.selectbox(
                            "Recompensa asociada",
                            list(available_catalog.keys()),
                        )
                        rule_active = st.checkbox("Regla activa", value=True)
                        save_rule = st.form_submit_button(
                            "Crear regla",
                            type="primary",
                            use_container_width=True,
                        )

                        if save_rule:
                            payload = {
                                "cargas_requeridas": int(required_loads),
                                "recompensa_catalogo_id": available_catalog[reward_label]["_id"],
                                "activa": rule_active,
                            }
                            result = api_post("/recompensas/reglas", json=payload)
                            if isinstance(result, dict) and result.get("error"):
                                st.error(result["error"])
                            else:
                                st.success("Regla creada correctamente.")
                                st.rerun()

    if not reglas:
        st.info("Aún no hay reglas configuradas.")
    else:
        rule_columns = st.columns([3.4, 1.1])
        with rule_columns[0]:
            for rule in sorted(reglas, key=lambda item: item.get("cargas_requeridas", 0)):
                with st.container(border=True):
                    row_columns = st.columns([0.6, 3.4, 0.7, 1.2])
                    with row_columns[0]:
                        st.markdown(f"### {rule.get('cargas_requeridas', 0)}")
                    with row_columns[1]:
                        reward = rule.get("recompensa") or {}
                        status_label = "Activa" if rule.get("activa", True) else "Inactiva"
                        st.markdown(f"**{reward.get('nombre', 'Sin recompensa')}** · {status_label}")
                        st.caption(
                            f"Al completar {rule.get('cargas_requeridas', 0)} cargas"
                            f" · {reward.get('empresa', 'Establecimiento no disponible')}"
                        )
                    with row_columns[2]:
                        grants_for_rule = sum(
                            1 for granted in recompensas
                            if str(granted.get("recompensa_regla_id")) == str(rule.get("_id"))
                        )
                        st.metric("Otorgadas", grants_for_rule)
                    with row_columns[3]:
                        if es_admin:
                            with st.popover("Editar regla", use_container_width=True):
                                st.markdown("### Editar regla")
                                available_catalog = {
                                    f"{item.get('nombre', 'Sin nombre')} · {item.get('empresa', 'Sin empresa')}": item
                                    for item in catalogo
                                    if item.get("activa", True)
                                }
                                with st.form(f"edit_rule_form_{rule['_id']}"):
                                    if not available_catalog:
                                        st.info("Crea primero una recompensa activa en el catálogo.")
                                    else:
                                        selected_catalog_id = rule.get("recompensa_catalogo_id")
                                        catalog_labels = list(available_catalog.keys())
                                        current_label = next(
                                            (
                                                label for label, item in available_catalog.items()
                                                if item.get("_id") == selected_catalog_id
                                            ),
                                            catalog_labels[0],
                                        )
                                        required_loads = st.number_input(
                                            "Cargas requeridas",
                                            min_value=1,
                                            value=int(rule.get("cargas_requeridas", 1)),
                                            step=1,
                                        )
                                        reward_label = st.selectbox(
                                            "Recompensa asociada",
                                            catalog_labels,
                                            index=catalog_labels.index(current_label),
                                        )
                                        rule_active = st.checkbox(
                                            "Regla activa",
                                            value=rule.get("activa", True),
                                        )
                                        save_rule = st.form_submit_button(
                                            "Guardar regla",
                                            type="primary",
                                            use_container_width=True,
                                        )

                                        if save_rule:
                                            payload = {
                                                "cargas_requeridas": int(required_loads),
                                                "recompensa_catalogo_id": available_catalog[reward_label]["_id"],
                                                "activa": rule_active,
                                            }
                                            result = api_put(f"/recompensas/reglas/{rule['_id']}", json=payload)
                                            if isinstance(result, dict) and result.get("error"):
                                                st.error(result["error"])
                                            else:
                                                st.success("Regla actualizada correctamente.")
                                                st.rerun()

        with rule_columns[1]:
            st.markdown("#### Ruta de recompensas")
            st.caption("Los beneficios se activan cuando un usuario alcanza cada hito.")
            for index, rule in enumerate(sorted(reglas, key=lambda item: item.get("cargas_requeridas", 0)), 1):
                reward = rule.get("recompensa") or {}
                st.markdown(f"**{index}. {rule.get('cargas_requeridas', 0)} cargas**")
                st.caption(reward.get("nombre", "Sin recompensa"))
with otorgadas_tab:
    history_heading = st.columns([3, 1.2, 1.2])
    with history_heading[0]:
        st.markdown("### Historial de recompensas")
        st.caption("Seguimiento de beneficios asignados y canjeados.")
    with history_heading[1]:
        reward_status_filter = st.selectbox(
            "Estado de recompensa",
            ["Todas", "Pendiente", "Canjeada"],
            key="granted_status_filter",
        )
    with history_heading[2]:
        reward_search = st.text_input(
            "Buscar recompensa",
            placeholder="Usuario o beneficio...",
            key="granted_search",
        )

    granted_filter_signature = (reward_status_filter, reward_search)
    if st.session_state.get("granted_filter_signature") != granted_filter_signature:
        st.session_state["granted_filter_signature"] = granted_filter_signature
        st.session_state["rewards_page_granted"] = 1

    user_names = {
        str(user.get("_id")): user.get("nombre", "Usuario desconocido")
        for user in usuarios if user.get("_id")
    }
    granted_rows = []
    for reward in recompensas:
        status = "Canjeada" if reward.get("usado") else "Pendiente"
        if reward_status_filter != "Todas" and status != reward_status_filter:
            continue
        catalog_snapshot = reward.get("snapshot_nombre") or reward.get("tipo", "").replace("_", " ").title()
        company = reward.get("snapshot_empresa") or "—"
        user_name = user_names.get(str(reward.get("usuario_id")), "Usuario desconocido")
        if reward_search.casefold() not in f"{user_name} {catalog_snapshot} {company}".casefold():
            continue
        granted_rows.append({
            "Usuario": user_name,
            "Recompensa": catalog_snapshot,
            "Establecimiento": company,
            "Fecha": str(reward.get("fecha_otorgamiento") or "—")[:16].replace("T", " "),
            "Estado": status,
        })

    if granted_rows:
        granted_dataframe = pd.DataFrame(granted_rows)
        paged_table(granted_dataframe, "granted")
        type_counts = granted_dataframe["Recompensa"].value_counts().rename_axis("Recompensa").reset_index(name="Otorgadas")
        reward_chart_columns = st.columns([1, 1])
        with reward_chart_columns[0]:
            st.markdown("#### Recompensas otorgadas")
            figure = px.pie(
                type_counts,
                names="Recompensa",
                values="Otorgadas",
                hole=0.55,
                color_discrete_sequence=px.colors.qualitative.Pastel,
            )
            figure.update_layout(height=300, margin=dict(l=15, r=15, t=20, b=15))
            st.plotly_chart(figure, use_container_width=True)
    else:
        st.info("No hay recompensas que coincidan con los filtros.")

with ranking_tab:
    st.markdown("### Usuarios más activos")
    st.caption("Actividad y progreso de los usuarios del programa.")
    ranking = []
    active_rules = sorted(
        (rule for rule in reglas if rule.get("activa", True)),
        key=lambda rule: rule.get("cargas_requeridas", 0),
    )
    for user in usuarios:
        total_loads = int(user.get("total_cargas", 0) or 0)
        user_rewards = [
            reward for reward in recompensas
            if str(reward.get("usuario_id")) == str(user.get("_id"))
        ]
        reached_rules = [
            rule for rule in active_rules
            if total_loads >= int(rule.get("cargas_requeridas", 0) or 0)
        ]
        next_rule = next(
            (
                rule for rule in active_rules
                if total_loads < int(rule.get("cargas_requeridas", 0) or 0)
            ),
            None,
        )
        level = (
            (reached_rules[-1].get("recompensa") or {}).get("nombre", "Estándar")
            if reached_rules
            else "Estándar"
        )
        next_threshold = int(next_rule.get("cargas_requeridas", 0)) if next_rule else 0
        ranking.append({
            "Usuario": user.get("nombre", "Sin nombre"),
            "Cargas": total_loads,
            "Recompensas": len(user_rewards),
            "Nivel": level,
            "Progreso": (
                min(100, round(total_loads / next_threshold * 100))
                if next_threshold > 0
                else 100 if active_rules else 0
            ),
        })

    ranking.sort(key=lambda item: item["Cargas"], reverse=True)
    if ranking:
        top_columns = st.columns(3)
        for position, column in enumerate(top_columns):
            if position < len(ranking):
                user = ranking[position]
                with column:
                    with st.container(border=True):
                        st.markdown(f"### #{position + 1} · {user['Usuario']}")
                        st.caption(f"{user['Cargas']} cargas · {user['Recompensas']} recompensas")
        ranking_dataframe = pd.DataFrame([
            {
                "Posición": f"#{index}",
                "Usuario": user["Usuario"],
                "Cargas": user["Cargas"],
                "Recompensas": user["Recompensas"],
                "Nivel": user["Nivel"],
                "Progreso": f"{user['Progreso']}%",
            }
            for index, user in enumerate(ranking, 1)
        ])
        paged_table(ranking_dataframe, "ranking")
    else:
        st.info("Todavía no hay usuarios para mostrar en el ranking.")
