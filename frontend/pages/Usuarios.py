import streamlit as st
from utils.state import init_session_state
from utils.api_client import api_get, api_post, api_put, api_delete
from components.sidebar import render_sidebar, _redirect_to_login
import pandas as pd

ROLE_OPTIONS = ["admin", "operador", "consulta", "cliente"]
ROLE_LABELS = {
    "admin": "Administrador",
    "operador": "Operador",
    "consulta": "Consulta",
    "cliente": "Cliente",
}

init_session_state()
render_sidebar()

if not st.session_state.get("authenticated"):
    _redirect_to_login()

st.markdown("# 👤 Gestión de Usuarios")
st.markdown("Administra y monitorea los usuarios de la plataforma ChargeCore")

# ============= SECCIÓN: CREAR NUEVO USUARIO =============
with st.expander("➕ Registrar Nuevo Usuario", expanded=False):
    with st.form("form_usuario"):
        col1, col2 = st.columns(2)
        with col1:
            nombre = st.text_input("Nombre Completo", placeholder="ej. Juan Pérez")
            email = st.text_input("Correo Electrónico", placeholder="ej. juan@example.com")
            telefono = st.text_input("Teléfono", placeholder="ej. 999-123-4567")
        with col2:
            password = st.text_input("Contraseña", type="password")
            rol = st.selectbox("Rol", ROLE_OPTIONS, format_func=ROLE_LABELS.get, help="Define permisos de acceso")
            activo = st.checkbox("Activar usuario", value=True)
        
        submitted = st.form_submit_button("💾 Registrar Usuario", use_container_width=True)
        if submitted:
            if not nombre or not email or not password:
                st.error("⚠️ Nombre, Correo electrónico y Contraseña son obligatorios")
            else:
                result = api_post("/usuarios/", json={
                    "nombre": nombre,
                    "email": email,
                    "telefono": telefono,
                    "password": password,
                    "rol": rol,
                    "activo": activo
                })
                if "error" in result:
                    st.error(f"❌ {result['error']}")
                else:
                    st.success("✅ Usuario registrado exitosamente")
                    st.rerun()

st.divider()

# ============= DATOS Y RESUMEN DE USUARIOS =============
with st.spinner("Cargando usuarios..."):
    users = []
    users_error = None
    users_skip = 0
    users_page_size = 100
    while True:
        users_batch = api_get(
            "/usuarios/",
            params={"skip": users_skip, "limit": users_page_size},
        )
        if isinstance(users_batch, dict) and "error" in users_batch:
            users_error = users_batch["error"]
            break
        if not isinstance(users_batch, list):
            users_error = "La respuesta de usuarios no tiene el formato esperado."
            break
        users.extend(users_batch)
        if len(users_batch) < users_page_size:
            break
        users_skip += users_page_size

if users_error:
    st.error(f"❌ No se pudieron cargar los usuarios: {users_error}")
else:
    active_users = [user for user in users if user.get("activo", True)]
    administrators = [user for user in users if user.get("rol") == "admin"]
    inactive_users = [user for user in users if not user.get("activo", True)]

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
                <div class="summary-card-label">Usuarios registrados</div>
                <div class="summary-card-value">{len(users)}</div>
                <div class="summary-card-detail">Inventario completo</div>
            </div>
            <div class="summary-card" style="--summary-accent:#16a34a">
                <div class="summary-card-label">Usuarios activos</div>
                <div class="summary-card-value">{len(active_users)}</div>
                <div class="summary-card-detail">Cuentas habilitadas</div>
            </div>
            <div class="summary-card" style="--summary-accent:#f59e0b">
                <div class="summary-card-label">Administradores</div>
                <div class="summary-card-value">{len(administrators)}</div>
                <div class="summary-card-detail">Con acceso administrativo</div>
            </div>
            <div class="summary-card" style="--summary-accent:#ef4444">
                <div class="summary-card-label">Cuentas inactivas</div>
                <div class="summary-card-value">{len(inactive_users)}</div>
                <div class="summary-card-detail">Sin acceso activo</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ============= SECCIÓN: FILTROS Y BÚSQUEDA =============
st.markdown("**Filtrar Usuarios**")
col1, col2, col3, col4 = st.columns([2.2, 1.35, 1.15, 0.7])

with col1:
    filtro_busqueda = st.text_input("🔍 Buscar por nombre o correo electrónico", placeholder="ej. Juan o juan@...")
with col2:
    filtro_rol = st.multiselect("Por Rol", ROLE_OPTIONS, format_func=ROLE_LABELS.get, default=[])
with col3:
    filtro_estado = st.selectbox("Por Estado", ["Todos", "Activos", "Inactivos"])
with col4:
    if st.button("🔄 Actualizar", use_container_width=True):
        st.rerun()

# ============= SECCIÓN: LISTA DE USUARIOS =============
st.markdown("**📋 Lista de Usuarios**")

if users_error:
    pass
elif not users:
    st.info("ℹ️ No hay usuarios registrados. Registra el primer usuario para comenzar.")
else:
    # Filtrar usuarios
    users_filtrados = users
    
    if filtro_busqueda:
        search_term = filtro_busqueda.casefold()
        users_filtrados = [
            user for user in users_filtrados
            if search_term in str(user.get("nombre") or "").casefold()
            or search_term in str(user.get("email") or "").casefold()
        ]
    
    if filtro_rol:
        users_filtrados = [u for u in users_filtrados if u.get('rol', '') in filtro_rol]
    
    if filtro_estado == "Activos":
        users_filtrados = [u for u in users_filtrados if u.get('activo', True)]
    elif filtro_estado == "Inactivos":
        users_filtrados = [u for u in users_filtrados if not u.get('activo', True)]

    filter_signature = (
        filtro_busqueda.strip().casefold(),
        tuple(filtro_rol),
        filtro_estado,
    )
    if st.session_state.get("users_filter_signature") != filter_signature:
        st.session_state.users_filter_signature = filter_signature
        st.session_state.users_page = 1

    page_size = 20
    total_pages = max(1, (len(users_filtrados) + page_size - 1) // page_size)
    current_page = min(max(st.session_state.get("users_page", 1), 1), total_pages)
    st.session_state.users_page = current_page
    start_index = (current_page - 1) * page_size
    users_visibles = users_filtrados[start_index:start_index + page_size]
    
    if not users_filtrados:
        st.info("ℹ️ No hay usuarios que coincidan con los filtros")
    else:
        st.write(f"**Total: {len(users_filtrados)} {'usuario' if len(users_filtrados) == 1 else 'usuarios'}**")

        row_widths = [1.7, 2.4, 1.0, 0.8, 1.0, 0.45]
        header_cols = st.columns(row_widths)
        header_cols[0].markdown("**Nombre**")
        header_cols[1].markdown("**Correo electrónico**")
        header_cols[2].markdown("**Rol**")
        header_cols[3].markdown("**Cargas**")
        header_cols[4].markdown("**Estado**")
        header_cols[5].markdown("**Acciones**")

        for user in users_visibles:
            user_id = user.get("_id", "")
            row_cols = st.columns(row_widths)
            row_cols[0].write(user.get("nombre", "Usuario"))
            row_cols[1].write(user.get("email", "-"))
            role = user.get("rol", "cliente")
            row_cols[2].write(ROLE_LABELS.get(role, role.capitalize()))
            row_cols[3].write(user.get("total_cargas", 0))
            row_cols[4].write("✅ Activo" if user.get("activo", True) else "❌ Inactivo")

            with row_cols[5]:
                with st.popover("⋮"):
                    if st.button("✏️ Editar", key=f"table_edit_{user_id}", use_container_width=True):
                        st.session_state[f"edit_{user_id}"] = not st.session_state.get(f"edit_{user_id}", False)
                        st.rerun()
                    if st.button("📋 Ver historial", key=f"table_hist_{user_id}", use_container_width=True):
                        st.session_state[f"history_{user_id}"] = not st.session_state.get(f"history_{user_id}", False)
                        st.rerun()

                    confirm_key = f"confirm_del_{user_id}"
                    if st.session_state.get(confirm_key, False):
                        st.warning(f"⚠️ Confirma la eliminación de {user.get('nombre', 'usuario')}")
                    if st.button(
                        "⚠️ Confirmar eliminación" if st.session_state.get(confirm_key, False) else "🗑️ Eliminar",
                        key=f"table_del_{user_id}",
                        use_container_width=True,
                    ):
                        if st.session_state.get(confirm_key, False):
                            result = api_delete(f"/usuarios/{user_id}")
                            if "error" not in result:
                                st.session_state.pop(confirm_key, None)
                                st.success("✅ Usuario eliminado de la base de datos")
                                st.rerun()
                            else:
                                st.error(f"❌ {result['error']}")
                        else:
                            st.session_state[confirm_key] = True
                            st.rerun()
                    if st.session_state.get(confirm_key, False) and st.button(
                        "Cancelar", key=f"table_cancel_del_{user_id}", use_container_width=True
                    ):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()

            if st.session_state.get(f"edit_{user_id}", False):
                st.markdown("**Editar Información**")
                with st.form(f"table_edit_form_{user_id}"):
                    edit_col1, edit_col2 = st.columns(2)
                    with edit_col1:
                        nuevo_nombre = st.text_input(
                            "Nombre Completo",
                            value=user.get("nombre", ""),
                            key=f"table_name_{user_id}",
                        )
                        nuevo_email = st.text_input(
                            "Correo electrónico",
                            value=user.get("email", ""),
                            key=f"table_email_{user_id}",
                        )
                        nueva_password = st.text_input(
                            "Nueva contraseña (opcional)",
                            type="password",
                            help="La contraseña actual no se puede consultar. Déjalo vacío para conservarla.",
                            key=f"table_password_{user_id}",
                        )
                        confirmar_password = st.text_input(
                            "Confirmar nueva contraseña",
                            type="password",
                            key=f"table_confirm_password_{user_id}",
                        )
                    with edit_col2:
                        nuevo_tel = st.text_input(
                            "Teléfono",
                            value=user.get("telefono", ""),
                            key=f"table_tel_{user_id}",
                        )
                        role_index = ROLE_OPTIONS.index(role) if role in ROLE_OPTIONS else 0
                        nuevo_rol = st.selectbox(
                            "Rol",
                            ROLE_OPTIONS,
                            format_func=ROLE_LABELS.get,
                            index=role_index,
                            key=f"table_role_{user_id}",
                        )
                        nuevo_estado = st.checkbox(
                            "Activo",
                            value=user.get("activo", True),
                            key=f"table_state_{user_id}",
                        )

                    edit_actions = st.columns(2)
                    with edit_actions[0]:
                        guardar_usuario = st.form_submit_button(
                            "💾 Guardar Cambios",
                            use_container_width=True,
                        )
                    with edit_actions[1]:
                        cancelar_edicion = st.form_submit_button(
                            "❌ Cancelar",
                            use_container_width=True,
                        )

                    if guardar_usuario:
                        if confirmar_password and not nueva_password:
                            st.error("Ingresa la nueva contraseña para confirmar el cambio.")
                        elif nueva_password and len(nueva_password) < 6:
                            st.error("La nueva contraseña debe tener al menos 6 caracteres.")
                        elif nueva_password != confirmar_password:
                            st.error("Las nuevas contraseñas no coinciden.")
                        else:
                            update_data = {
                                "nombre": nuevo_nombre,
                                "email": nuevo_email,
                                "telefono": nuevo_tel,
                                "rol": nuevo_rol,
                                "activo": nuevo_estado,
                            }
                            if nueva_password:
                                update_data["password"] = nueva_password
                            result = api_put(f"/usuarios/{user_id}", json=update_data)
                            if "error" not in result:
                                st.success("✅ Usuario actualizado")
                                st.session_state[f"edit_{user_id}"] = False
                                st.rerun()
                            else:
                                st.error(f"❌ {result['error']}")
                    if cancelar_edicion:
                        st.session_state[f"edit_{user_id}"] = False
                        st.rerun()

            if st.session_state.get(f"history_{user_id}", False):
                st.markdown("**📋 Historial de Sesiones**")
                with st.spinner("Cargando historial..."):
                    historial_result = api_get(f"/sesiones/usuario/{user_id}")

                if isinstance(historial_result, dict) and "error" in historial_result:
                    st.error(f"❌ No se pudo cargar el historial: {historial_result['error']}")
                elif isinstance(historial_result, dict):
                    historial = historial_result.get("sesiones", [])
                    df_hist = pd.DataFrame(historial)
                    cols_display = [
                        col for col in [
                            "fecha_inicio",
                            "charge_point_id",
                            "energia_consumida_kwh",
                            "duracion_minutos",
                        ]
                        if col in df_hist.columns
                    ]
                    df_hist = df_hist[cols_display].rename(columns={
                        "fecha_inicio": "Fecha",
                        "charge_point_id": "Cargador",
                        "energia_consumida_kwh": "Energía (kWh)",
                        "duracion_minutos": "Duración (min)"
                    })
                    if df_hist.empty:
                        st.info("Sin historial de sesiones")
                    else:
                        st.dataframe(df_hist, use_container_width=True, hide_index=True)
                else:
                    st.error("❌ El servidor devolvió un historial con formato inesperado.")

            st.divider()

        pagination_columns = st.columns([1, 3, 1])
        with pagination_columns[0]:
            if st.button(
                "← Anterior",
                disabled=current_page <= 1,
                use_container_width=True,
                key="users_previous_page",
            ):
                st.session_state.users_page = current_page - 1
                st.rerun()
        with pagination_columns[1]:
            first_record = start_index + 1
            last_record = min(start_index + page_size, len(users_filtrados))
            st.caption(
                f"Mostrando {first_record}–{last_record} de {len(users_filtrados)} registros"
                f" · Página {current_page} de {total_pages}"
            )
        with pagination_columns[2]:
            if st.button(
                "Siguiente →",
                disabled=current_page >= total_pages,
                use_container_width=True,
                key="users_next_page",
            ):
                st.session_state.users_page = current_page + 1
                st.rerun()