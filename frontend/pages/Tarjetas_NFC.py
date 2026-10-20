from datetime import datetime

import streamlit as st
from utils.state import init_session_state
from utils.api_client import api_get, api_post, api_put, api_delete
from components.sidebar import render_sidebar, _redirect_to_login

CARD_STATES = ["activa", "bloqueada", "perdida"]
CARD_STATE_LABELS = {
    "activa": "Activa",
    "bloqueada": "Bloqueada",
    "perdida": "Desactivada",
}


def clear_card_filters():
    st.session_state.nfc_search = ""
    st.session_state.nfc_state_filter = "Todos los estados"
    st.session_state.nfc_user_filter = "Todos los usuarios"

init_session_state()
render_sidebar()

if not st.session_state.get("authenticated"):
    _redirect_to_login()

is_admin = st.session_state.get("role") == "admin"
if not is_admin:
    st.error("Solo un administrador puede consultar los identificadores completos de tarjetas NFC.")
    st.stop()

st.markdown("# 🪪 Gestión de Tarjetas NFC")
st.markdown("Administra las tarjetas NFC asignadas a usuarios para autorizar cargas")

# Obtener lista de usuarios
with st.spinner("Cargando usuarios..."):
    users = api_get("/usuarios/") if is_admin else []

if "error" in users:
    users = []

client_users = [
    user for user in users
    if user.get("rol") == "cliente" and user.get("activo", True)
] if users else []
user_options = {
    f"{user['nombre']} ({user['email']})": user["_id"]
    for user in client_users
}
user_dict = {user["_id"]: user["nombre"] for user in users} if users else {}

with st.spinner("Cargando tarjetas..."):
    tarjetas_response = api_get("/nfc-cards/")

tarjetas_error = not isinstance(tarjetas_response, list)
tarjetas = [] if tarjetas_error or not isinstance(tarjetas_response, list) else tarjetas_response

# ============= SECCIÓN: REGISTRAR NUEVA TARJETA =============
with st.expander("➕ Registrar Nueva Tarjeta NFC", expanded=False):
    if not is_admin:
        st.info("Solo un administrador puede registrar o modificar tarjetas NFC.")
    with st.form("form_nfc"):
        st.markdown("**Información de la Tarjeta**")
        uid = st.text_input(
            "UID de la Tarjeta",
            placeholder="ej. AB1234CD",
            help="UID de la tarjeta, entre 4 y 20 caracteres, tal como lo envía el cargador."
        )
        
        if not user_options:
            st.error("⚠️ No hay clientes activos disponibles para asignar una tarjeta.")
        else:
            usuario = st.selectbox("Asignar a Usuario", options=list(user_options.keys()))
            estado = st.selectbox(
                "Estado Inicial",
                CARD_STATES,
                format_func=CARD_STATE_LABELS.get,
            )
        
        submitted = st.form_submit_button(
            "💾 Registrar Tarjeta",
            use_container_width=True,
            disabled=not is_admin,
        )
        if submitted:
            if not uid:
                st.error("⚠️ El UID de la tarjeta es obligatorio")
            elif not user_options:
                st.error("⚠️ No hay usuarios disponibles")
            else:
                # Validar que el UID no exista
                result = api_post("/nfc-cards/", json={
                    "uid": uid,
                    "usuario_id": user_options[usuario],
                    "estado": estado
                })
                if "error" in result:
                    st.error(f"❌ {result['error']}")
                else:
                    st.success("✅ Tarjeta registrada exitosamente")
                    st.rerun()

st.divider()

# ============= RESUMEN DE TARJETAS =============
if not tarjetas_error:
    active_count = sum(1 for card in tarjetas if card.get("estado", "activa") == "activa")
    blocked_count = sum(1 for card in tarjetas if card.get("estado") == "bloqueada")
    deactivated_count = sum(1 for card in tarjetas if card.get("estado") == "perdida")

    st.markdown(
        """
        <style>
        .summary-card-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.75rem;
            margin: 0.75rem 0 1.25rem;
        }
        .summary-card {
            background: #ffffff;
            border: 1px solid #dbe5ef;
            border-left: 3px solid var(--summary-accent);
            border-radius: 12px;
            box-shadow: 0 5px 16px rgba(15, 23, 42, 0.06);
            min-height: 6rem;
            padding: 0.85rem 1rem;
        }
        .summary-card-label {
            color: #475569;
            font-size: 0.88rem;
            font-weight: 600;
        }
        .summary-card-value {
            color: #0f172a;
            font-size: 1.75rem;
            font-weight: 700;
            line-height: 1.25;
            margin: 0.25rem 0;
        }
        .summary-card-detail {
            color: var(--summary-accent);
            font-size: 0.76rem;
        }
        @media (max-width: 800px) {
            .summary-card-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        @media (max-width: 480px) {
            .summary-card-grid {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div class="summary-card-grid">
            <div class="summary-card" style="--summary-accent:#2563eb">
                <div class="summary-card-label">Total de tarjetas</div>
                <div class="summary-card-value">{len(tarjetas)}</div>
                <div class="summary-card-detail">Inventario completo</div>
            </div>
            <div class="summary-card" style="--summary-accent:#16a34a">
                <div class="summary-card-label">Activas</div>
                <div class="summary-card-value">{active_count}</div>
                <div class="summary-card-detail">Listas para usar</div>
            </div>
            <div class="summary-card" style="--summary-accent:#f59e0b">
                <div class="summary-card-label">Bloqueadas</div>
                <div class="summary-card-value">{blocked_count}</div>
                <div class="summary-card-detail">Acceso temporalmente bloqueado</div>
            </div>
            <div class="summary-card" style="--summary-accent:#ef4444">
                <div class="summary-card-label">Desactivadas</div>
                <div class="summary-card-value">{deactivated_count}</div>
                <div class="summary-card-detail">Sin acceso activo</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.divider()

# ============= SECCIÓN: FILTROS =============
st.markdown("**Filtrar Tarjetas**")
filter_columns = st.columns([2.2, 1.1, 1.4, 0.8])

with filter_columns[0]:
    filtro_busqueda = st.text_input(
        "Buscar tarjeta",
        placeholder="UID o nombre del usuario",
        key="nfc_search",
    )
with filter_columns[1]:
    filtro_estado = st.selectbox(
        "Por Estado",
        ["Todos los estados", *CARD_STATES],
        format_func=lambda state: (
            state if state == "Todos los estados" else CARD_STATE_LABELS[state]
        ),
        key="nfc_state_filter",
    )
user_filter_options = ["Todos los usuarios", *user_options.keys()]
with filter_columns[2]:
    filtro_usuario = st.selectbox(
        "Por Usuario",
        user_filter_options,
        key="nfc_user_filter",
    )
with filter_columns[3]:
    st.button(
        "Limpiar filtros",
        use_container_width=True,
        on_click=clear_card_filters,
    )

refresh_columns = st.columns([1, 4])
with refresh_columns[0]:
    if st.button("🔄 Actualizar", use_container_width=True):
        st.rerun()

# ============= SECCIÓN: LISTA DE TARJETAS =============
st.markdown("**🪪 Tarjetas registradas**")

if tarjetas_error:
    st.error("❌ No se pudieron cargar las tarjetas NFC")
elif not tarjetas:
    st.info("ℹ️ No hay tarjetas NFC registradas. Registra la primera tarjeta para comenzar.")
else:
    # Preparar datos
    tarjetas_data = []
    for t in tarjetas:
        usuario_nombre = user_dict.get(str(t.get("usuario_id", "")), "Desconocido")
        tarjetas_data.append({
            "uid": t.get("uid", ""),
            "usuario_id": t.get("usuario_id", ""),
            "usuario": usuario_nombre,
            "estado": t.get("estado", "activa"),
            "fecha_registro": t.get("created_at"),
            "_id": t.get("_id", "")
        })
    
    # Filtrar
    tarjetas_filtradas = tarjetas_data
    
    if filtro_busqueda:
        search_term = filtro_busqueda.strip().casefold()
        tarjetas_filtradas = [
            t for t in tarjetas_filtradas
            if search_term in t["uid"].casefold()
            or search_term in t["usuario"].casefold()
        ]
    
    if filtro_usuario != "Todos los usuarios":
        selected_user_id = user_options[filtro_usuario]
        tarjetas_filtradas = [
            t for t in tarjetas_filtradas
            if str(t["usuario_id"]) == str(selected_user_id)
        ]
    
    if filtro_estado != "Todos los estados":
        tarjetas_filtradas = [
            t for t in tarjetas_filtradas if t["estado"] == filtro_estado
        ]

    filter_signature = (
        filtro_busqueda.strip().casefold(),
        filtro_usuario,
        filtro_estado,
    )
    if st.session_state.get("tarjetas_filter_signature") != filter_signature:
        st.session_state.tarjetas_filter_signature = filter_signature
        st.session_state.tarjetas_page = 1
    
    if not tarjetas_filtradas:
        st.info("ℹ️ No hay tarjetas que coincidan con los filtros")
    else:
        st.write(f"**Total: {len(tarjetas_filtradas)} {'tarjeta' if len(tarjetas_filtradas) == 1 else 'tarjetas'}**")

        page_size = 20
        total_pages = max(1, (len(tarjetas_filtradas) + page_size - 1) // page_size)
        current_page = min(max(st.session_state.get("tarjetas_page", 1), 1), total_pages)
        st.session_state.tarjetas_page = current_page
        start_index = (current_page - 1) * page_size
        tarjetas_visibles = tarjetas_filtradas[start_index:start_index + page_size]

        column_widths = [2.1, 1.8, 1.2, 1.5, 0.5]
        header_cols = st.columns(column_widths)
        header_cols[0].markdown("**Tarjeta / identificador**")
        header_cols[1].markdown("**Usuario asociado**")
        header_cols[2].markdown("**Estado**")
        header_cols[3].markdown("**Fecha de registro**")
        header_cols[4].markdown("**Acciones**")

        estado_config = {
            "activa": {"icon": "🟢", "label": "Activa"},
            "bloqueada": {"icon": "🔴", "label": "Bloqueada"},
            "perdida": {"icon": "⚪", "label": "Desactivada"}
        }

        for t in tarjetas_visibles:
            config = estado_config.get(t["estado"], {"icon": "⚪", "label": t["estado"].capitalize()})
            row_cols = st.columns(column_widths)
            row_cols[0].write(t["uid"])
            row_cols[1].write(t["usuario"])
            row_cols[2].write(f"{config['icon']} {config['label']}")
            created_at = t["fecha_registro"]
            if created_at:
                try:
                    registered_at = datetime.fromisoformat(
                        str(created_at).replace("Z", "+00:00")
                    ).strftime("%d/%m/%Y")
                except ValueError:
                    registered_at = str(created_at)[:10]
            else:
                registered_at = "-"
            row_cols[3].write(registered_at)

            with row_cols[4]:
                with st.popover("⋮"):
                    if st.button(
                        "✏️ Editar",
                        key=f"toggle_edit_{t['_id']}",
                        use_container_width=True,
                        disabled=not is_admin,
                    ):
                        st.session_state[f"edit_{t['_id']}"] = not st.session_state.get(f"edit_{t['_id']}", False)
                        st.rerun()

                    if t["estado"] == "activa":
                        if st.button(
                            "🔒 Bloquear",
                            key=f"block_{t['_id']}",
                            use_container_width=True,
                            disabled=not is_admin,
                        ):
                            result = api_put(f"/nfc-cards/{t['_id']}", json={"estado": "bloqueada"})
                            if "error" not in result:
                                st.success("✅ Tarjeta bloqueada")
                                st.rerun()
                            else:
                                st.error(f"❌ {result['error']}")
                    elif t["estado"] == "bloqueada":
                        if st.button(
                            "🔓 Desbloquear",
                            key=f"unblock_{t['_id']}",
                            use_container_width=True,
                            disabled=not is_admin,
                        ):
                            result = api_put(f"/nfc-cards/{t['_id']}", json={"estado": "activa"})
                            if "error" not in result:
                                st.success("✅ Tarjeta desbloqueada")
                                st.rerun()
                            else:
                                st.error(f"❌ {result['error']}")
                    else:
                        if st.button(
                            "✅ Activar",
                            key=f"activate_{t['_id']}",
                            use_container_width=True,
                            disabled=not is_admin,
                        ):
                            result = api_put(f"/nfc-cards/{t['_id']}", json={"estado": "activa"})
                            if "error" not in result:
                                st.success("✅ Tarjeta activada")
                                st.rerun()
                            else:
                                st.error(f"❌ {result['error']}")

                    if st.button(
                        "🗑️ Eliminar",
                        key=f"del_{t['_id']}",
                        use_container_width=True,
                        disabled=not is_admin,
                    ):
                        if st.session_state.get(f"confirm_del_{t['_id']}", False):
                            result = api_delete(f"/nfc-cards/{t['_id']}")
                            if "error" not in result:
                                st.session_state[f"confirm_del_{t['_id']}"] = False
                                st.success("✅ Tarjeta eliminada")
                                st.rerun()
                            else:
                                st.error(f"❌ {result['error']}")
                        else:
                            st.session_state[f"confirm_del_{t['_id']}"] = True
                            st.warning(f"⚠️ Confirmar eliminación de tarjeta {t['uid']}")

            if st.session_state.get(f"edit_{t['_id']}", False):
                st.markdown("**Editar Tarjeta**")
                with st.form(f"edit_form_{t['_id']}"):
                    current_state = t.get("estado", "activa")
                    nuevo_estado = st.selectbox(
                        "Nuevo Estado",
                        CARD_STATES,
                        index=(
                            CARD_STATES.index(current_state)
                            if current_state in CARD_STATES
                            else 0
                        ),
                        format_func=CARD_STATE_LABELS.get,
                        key=f"state_{t['_id']}"
                    )

                    if user_options:
                        current_user_id = str(t.get("usuario_id", ""))
                        user_ids = list(user_options.values())
                        nuevo_usuario = st.selectbox(
                            "Reasignar a Usuario",
                            options=list(user_options.keys()),
                            index=(
                                user_ids.index(current_user_id)
                                if current_user_id in user_ids
                                else 0
                            ),
                            key=f"user_{t['_id']}"
                        )

                    edit_cols = st.columns(2)
                    with edit_cols[0]:
                        if st.form_submit_button(
                            "💾 Guardar Cambios",
                            use_container_width=True,
                            disabled=not is_admin,
                        ):
                            update_data = {"estado": nuevo_estado}
                            if user_options:
                                update_data["usuario_id"] = user_options[nuevo_usuario]

                            result = api_put(f"/nfc-cards/{t['_id']}", json=update_data)
                            if "error" not in result:
                                st.success("✅ Tarjeta actualizada")
                                st.session_state[f"edit_{t['_id']}"] = False
                                st.rerun()
                            else:
                                st.error(f"❌ {result['error']}")

                    with edit_cols[1]:
                        if st.form_submit_button("❌ Cancelar", use_container_width=True):
                            st.session_state[f"edit_{t['_id']}"] = False
                            st.rerun()

            st.divider()

        pagination_columns = st.columns([1, 2, 1])
        with pagination_columns[0]:
            if st.button(
                "← Anterior",
                disabled=current_page == 1,
                use_container_width=True,
                key="tarjetas_previous_page",
            ):
                st.session_state.tarjetas_page = current_page - 1
                st.rerun()
        with pagination_columns[1]:
            first_visible = start_index + 1
            last_visible = start_index + len(tarjetas_visibles)
            st.caption(
                f"Mostrando {first_visible}–{last_visible} de {len(tarjetas_filtradas)} "
                f"tarjetas · Página {current_page} de {total_pages}"
            )
        with pagination_columns[2]:
            if st.button(
                "Siguiente →",
                disabled=current_page == total_pages,
                use_container_width=True,
                key="tarjetas_next_page",
            ):
                st.session_state.tarjetas_page = current_page + 1
                st.rerun()
        