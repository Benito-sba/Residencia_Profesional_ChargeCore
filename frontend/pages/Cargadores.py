import streamlit as st
from utils.state import init_session_state
from utils.api_client import (
    api_get,
    api_post,
    api_put,
    get_charger_meter_values,
    get_charger_status,
    get_connectors,
)
from components.sidebar import render_sidebar, _redirect_to_login
import pandas as pd
from streamlit_autorefresh import st_autorefresh


STATUS_LABELS = {
    "Available": "Disponible",
    "Charging": "Cargando",
    "Faulted": "Con falla",
    "Unavailable": "Fuera de servicio",
    "Preparing": "Preparando",
    "Finishing": "Finalizando",
    "Reserved": "Reservado",
    "SuspendedEV": "Suspendido por el vehículo",
    "SuspendedEVSE": "Suspendido por el cargador",
    "Accepted": "Aceptado",
    "Rejected": "Rechazado",
    "Scheduled": "Programado",
    "disponible": "Disponible",
    "ocupado": "Ocupado",
    "falla": "Con falla",
    "fuera_de_servicio": "Fuera de servicio",
}


def _command_error_message(result):
    error = str(result.get("error", "")).strip()
    error_lower = error.lower()
    if "no esta conectado" in error_lower:
        return "El cargador no está conectado actualmente y no puede recibir la orden."
    if "cargador no encontrado" in error_lower:
        return "El cargador no fue encontrado."
    if "timeout" in error_lower:
        return "El cargador no respondió dentro del tiempo esperado."
    if "callerror" in error_lower or "respuesta ocpp invalida" in error_lower:
        return "El cargador devolvió un error OCPP o una respuesta inválida."
    if "permisos insuficientes" in error_lower:
        return "El usuario no tiene permisos para realizar esta operación."
    if "422" in error_lower or "datos" in error_lower and "válid" in error_lower:
        return "Los datos enviados para la operación no son válidos."
    if "error ejecutando" in error_lower:
        return "Se produjo un error interno al ejecutar la operación."
    return "No fue posible completar la operación."


def _show_command_result(result, success_message):
    if "error" in result:
        st.error(f"❌ {_command_error_message(result)}")
        return

    status = result.get("status")
    if status == "Accepted":
        st.success(success_message)
    elif status == "Scheduled":
        st.success("✅ Operación OCPP programada correctamente")
    elif status:
        st.warning(f"⚠️ La operación OCPP no fue aceptada. Estado: {STATUS_LABELS.get(status, status)}")
    else:
        st.warning("⚠️ El backend no devolvió un estado OCPP válido")


def _render_charger_details(charger, status_config, connection_text):
    charger_id = charger.get("_id") or charger.get("charge_point_id", "unknown")
    state = str(charger.get("estado_general", "fuera_de_servicio")).lower()
    config = status_config.get(state, status_config["fuera_de_servicio"])
    connected = charger.get("conectado", False)

    with st.container(border=True):
        title_cols = st.columns([2.2, 1, 1])
        with title_cols[0]:
            st.markdown(f"### {config['icon']} {charger.get('nombre', 'Sin nombre')}")
            st.caption(f"ID: {charger.get('charge_point_id', 'Sin identificador')}")
        with title_cols[1]:
            st.markdown(f"**Estado**  \n{config['label']}")
        with title_cols[2]:
            st.markdown(f"**Conexión OCPP**  \n{connection_text.get(connected, '⚪ Desconocido')}")

        if st.session_state.get("user", {}).get("rol") == "admin":
            with st.expander("✏️ Editar cargador"):
                with st.form(f"edit_charger_{charger_id}"):
                    edit_cols = st.columns(2)
                    with edit_cols[0]:
                        new_name = st.text_input(
                            "Nombre",
                            value=charger.get("nombre") or "",
                            key=f"edit_charger_name_{charger_id}",
                        )
                        new_location = st.text_input(
                            "Ubicación",
                            value=charger.get("ubicacion") or "",
                            key=f"edit_charger_location_{charger_id}",
                        )
                        new_manufacturer = st.text_input(
                            "Fabricante",
                            value=charger.get("fabricante") or "",
                            key=f"edit_charger_manufacturer_{charger_id}",
                        )
                    with edit_cols[1]:
                        new_model = st.text_input(
                            "Modelo",
                            value=charger.get("modelo") or "",
                            key=f"edit_charger_model_{charger_id}",
                        )
                        new_firmware = st.text_input(
                            "Firmware",
                            value=charger.get("firmware_version") or "",
                            key=f"edit_charger_firmware_{charger_id}",
                        )
                    save_edit = st.form_submit_button(
                        "💾 Guardar cambios",
                        use_container_width=True,
                    )
                    if save_edit:
                        if not new_name.strip():
                            st.error("El nombre del cargador es obligatorio.")
                        else:
                            result = api_put(
                                f"/cargadores/{charger.get('_id')}",
                                json={
                                    "nombre": new_name.strip(),
                                    "ubicacion": new_location.strip(),
                                    "fabricante": new_manufacturer.strip(),
                                    "modelo": new_model.strip(),
                                    "firmware_version": new_firmware.strip(),
                                },
                            )
                            if "error" in result:
                                st.error(f"❌ {result['error']}")
                            else:
                                st.success("✅ Cargador actualizado")
                                st.rerun()

                st.caption(
                    "El usuario OCPP del cargador debe ser exactamente su Identificador. "
                    "La contraseña se guarda cifrada y no se puede volver a consultar."
                )
                with st.form(f"ocpp_credentials_{charger_id}"):
                    ocpp_password = st.text_input(
                        "Definir contraseña OCPP",
                        type="password",
                        key=f"ocpp_password_{charger_id}",
                    )
                    update_credentials = st.form_submit_button(
                        "🔐 Guardar credenciales OCPP",
                        use_container_width=True,
                    )
                    if update_credentials:
                        if len(ocpp_password) < 12:
                            st.error("La contraseña OCPP debe tener al menos 12 caracteres.")
                        else:
                            result = api_put(
                                f"/cargadores/{charger.get('_id')}/ocpp-credentials",
                                json={"password": ocpp_password},
                            )
                            if "error" in result:
                                st.error(f"❌ {result['error']}")
                            else:
                                st.success(
                                    "Credenciales guardadas. Configura en el cargador el usuario "
                                    "igual al Identificador y esta contraseña."
                                )
                                st.rerun()
        info_cols = st.columns(3)
        with info_cols[0]:
            st.markdown(f"**📍 Ubicación**  \n{charger.get('ubicacion') or 'Sin ubicación registrada'}")
        with info_cols[1]:
            st.markdown(f"**🏭 Fabricante**  \n{charger.get('fabricante') or 'No especificado'}")
            st.markdown(f"**📦 Modelo**  \n{charger.get('modelo') or 'No especificado'}")
        with info_cols[2]:
            st.markdown(f"**⚙️ Firmware**  \n{charger.get('firmware_version') or 'No disponible'}")

        ocpp_fields = (
            ("Última conexión", "ultima_conexion"),
            ("Última desconexión", "ultima_desconexion"),
            ("Última comunicación", "ultima_comunicacion"),
            ("Último heartbeat", "ultimo_heartbeat"),
            ("Protocolo OCPP", "subprotocolo_ocpp"),
            ("Último estado conocido", "ultimo_estado_conocido"),
            ("Último error", "ultimo_error"),
        )
        date_fields = {"ultima_conexion", "ultima_desconexion", "ultima_comunicacion", "ultimo_heartbeat"}
        metadata = []
        for label, field in ocpp_fields:
            value = charger.get(field)
            if value is not None:
                if field in date_fields:
                    value = str(value)[:19].replace("T", " ")
                elif field == "ultimo_estado_conocido":
                    value = STATUS_LABELS.get(value, value)
                metadata.append((label, value))
        if metadata:
            st.divider()
            metadata_cols = st.columns(min(3, len(metadata)))
            for index, (label, value) in enumerate(metadata):
                with metadata_cols[index % len(metadata_cols)]:
                    st.caption(f"**{label}:** {value}")
        if not charger.get("ocpp_credentials_configured"):
            st.warning(
                "Este cargador no tiene credenciales OCPP. Configúralas antes de "
                "intentar conectarlo."
            )

        connectors = charger.get("conectores") or []
        st.markdown("**Conectores**")
        if connectors:
            connector_cols = st.columns(min(3, len(connectors)))
            for index, connector in enumerate(connectors):
                connector_state = str(connector.get("estado", "desconocido")).lower()
                connector_icon = "🟢" if connector_state in {"disponible", "available"} else (
                    "🔴" if connector_state in {"ocupado", "charging", "faulted"} else "🟡"
                )
                connector_status = STATUS_LABELS.get(
                    connector.get("estado", "Sin estado"),
                    connector.get("estado", "Sin estado"),
                )
                with connector_cols[index % len(connector_cols)]:
                    with st.container(border=True):
                        st.markdown(
                            f"{connector_icon} **Conector {connector.get('id_conector', '—')}**"
                        )
                        st.caption(connector_status)
                        connector_type = {"Type1": "Tipo 1", "Type2": "Tipo 2"}.get(
                            connector.get("tipo", ""),
                            connector.get("tipo", "No especificado"),
                        )
                        st.caption(
                            f"{connector_type} · {connector.get('error_code', 'Sin error')}"
                        )
        else:
            st.caption("No hay conectores registrados.")

        live_key = f"show_details_{charger_id}"
        if st.toggle("Ver monitoreo en tiempo real", key=f"toggle_{live_key}"):
            charger_status = get_charger_status(charger.get("charge_point_id"))
            charger_connectors = get_connectors(charger.get("charge_point_id"))
            active_session = (
                charger_status.get("sesion_actual")
                if isinstance(charger_status, dict) and "error" not in charger_status
                else charger.get("sesion_actual")
            )
            if active_session:
                st.info(
                    f"Sesión activa · ID de usuario: {active_session.get('usuario_id') or 'Sin identificador'} · "
                    f"Inicio: {str(active_session.get('hora_inicio', 'Sin hora de inicio'))[:19]} · "
                    f"Energía: {active_session.get('energia_consumida_kwh', '—')} kWh"
                )
            if isinstance(charger_connectors, list) and charger_connectors:
                st.markdown("**Estado actual de conectores:**")
                st.write([
                    {
                        **connector,
                        "estado": STATUS_LABELS.get(
                            connector.get("estado", "Sin estado"),
                            connector.get("estado", "Sin estado"),
                        ),
                    }
                    for connector in charger_connectors
                ])

            meter_values = get_charger_meter_values(charger.get("charge_point_id"), limit=10)
            if isinstance(meter_values, list) and meter_values:
                rows = []
                for value in meter_values:
                    for sampled in value.get("sampled_values", []):
                        for item in sampled.get("sampled_value", []):
                            rows.append({
                                "Fecha": item.get("timestamp") or sampled.get("timestamp") or value.get("timestamp", "Sin fecha"),
                                "Medición": item.get("measurand", "Sin medición"),
                                "Valor": item.get("value", "Sin dato"),
                                "Unidad": item.get("unit", "Sin unidad"),
                                "ID del conector": value.get("connector_id", "Sin identificador"),
                                "ID de transacción": value.get("transaction_id", "Sin identificador de transacción"),
                            })
                if rows:
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.markdown("**Acciones rápidas**")
        action_cols = st.columns(5)
        with action_cols[0]:
            if st.button("🔄 Reiniciar", key=f"restart_{charger_id}", use_container_width=True):
                result = api_post(
                    f"/cargadores/{charger.get('charge_point_id')}/reset",
                    json={"type": "Soft"},
                )
                _show_command_result(result, "✅ Comando enviado")
        with action_cols[1]:
            if charger.get("activo"):
                if st.button("⛔ Deshabilitar", key=f"disable_{charger_id}", use_container_width=True):
                    result = api_post(
                        f"/cargadores/{charger.get('charge_point_id')}/availability",
                        json={"connectorId": 0, "type": "Inoperative"},
                    )
                    _show_command_result(result, "✅ Cargador deshabilitado")
            elif st.button("✅ Habilitar", key=f"enable_{charger_id}", use_container_width=True):
                result = api_post(
                    f"/cargadores/{charger.get('charge_point_id')}/availability",
                    json={"connectorId": 0, "type": "Operative"},
                )
                if "error" in result:
                    st.error(f"❌ {_command_error_message(result)}")
                else:
                    st.success("✅ Cargador habilitado")
        with action_cols[2]:
            if st.button(
                "🗑️ Eliminar",
                key=f"delete_{charger_id}",
                use_container_width=True,
                disabled=not is_admin,
            ):
                confirm_key = f"confirm_delete_{charger_id}"
                if st.session_state.get(confirm_key, False):
                    result = api_put(f"/cargadores/{charger.get('_id')}", json={"activo": False})
                    if "error" in result:
                        st.error(f"❌ {result['error']}")
                    else:
                        st.success("✅ Cargador eliminado")
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                else:
                    st.session_state[confirm_key] = True
                    st.warning("⚠️ Pulsa Eliminar nuevamente para confirmar.")
        with action_cols[3]:
            if st.button("📊 Historial", key=f"history_{charger_id}", use_container_width=True):
                history_key = f"show_history_{charger_id}"
                st.session_state[history_key] = not st.session_state.get(history_key, False)
        with action_cols[4]:
            if st.button("🔄 Actualizar", key=f"refresh_{charger_id}", use_container_width=True):
                st.rerun()

        if st.session_state.get(f"show_history_{charger_id}", False):
            st.divider()
            st.markdown("**📊 Historial de Sesiones**")
            with st.spinner("Cargando historial..."):
                history = api_get(f"/cargadores/{charger.get('_id')}/historial")
            if "error" not in history and history:
                history_df = pd.DataFrame(history)
                visible_columns = [
                    column
                    for column in (
                        "fecha_inicio",
                        "fecha_fin",
                        "energia_consumida_kwh",
                        "duracion_minutos",
                        "usuario_id",
                    )
                    if column in history_df.columns
                ]
                history_df = history_df[visible_columns].rename(columns={
                    "fecha_inicio": "Inicio",
                    "fecha_fin": "Fin",
                    "energia_consumida_kwh": "Energía (kWh)",
                    "duracion_minutos": "Duración (min)",
                    "usuario_id": "ID de usuario",
                })
                st.dataframe(history_df, use_container_width=True, hide_index=True)
            else:
                st.info("Sin historial de sesiones para este cargador")


init_session_state()
render_sidebar()

if not st.session_state.get("authenticated"):
    _redirect_to_login()

is_admin = st.session_state.get("user", {}).get("rol") == "admin"

st.markdown("# ⚡ Gestión de Cargadores")
st.markdown("Centro de control y monitoreo de estaciones de carga")
auto_refresh = st.toggle(
    "Actualizar automáticamente esta pantalla",
    value=True,
    key="chargers_auto_refresh",
    help="Actualiza periódicamente el estado OCPP, las sesiones y las mediciones.",
)
if auto_refresh:
    st_autorefresh(interval=15_000, key="chargers_auto_refresh_timer")

with st.spinner("Cargando cargadores..."):
    cargadores = api_get("/cargadores/")

# ============= SECCIÓN: REGISTRAR NUEVO CARGADOR =============
with st.expander("➕ Registrar Nuevo Cargador", expanded=False):
    if not is_admin:
        st.info("Solo un administrador puede registrar cargadores.")
    with st.form("form_cargador"):
        col1, col2 = st.columns(2)
        with col1:
            cp_id = st.text_input("Identificador del Cargador", placeholder="ej. CP-001", help="Identificador único para este cargador")
            nombre = st.text_input("Nombre", placeholder="ej. Estación Centro")
            ubicacion = st.text_input("Ubicación", placeholder="ej. Calle Principal #100")
        with col2:
            modelo = st.text_input("Modelo", placeholder="ej. EV-3000")
            fabricante = st.text_input("Fabricante", placeholder="ej. SunTree")
            num_conectores = st.number_input("Número de conectores", min_value=1, max_value=4, value=1)
            ocpp_password = st.text_input(
                "Contraseña OCPP (mínimo 12 caracteres)",
                type="password",
                help="El usuario configurado en el cargador debe coincidir con su Identificador.",
            )
        
        st.markdown("**Configuración de Conectores**")
        conectores = []
        cols_per_row = 2
        for i in range(int(num_conectores)):
            if i % cols_per_row == 0:
                cols = st.columns(cols_per_row)
            col_idx = i % cols_per_row
            with cols[col_idx]:
                st.markdown(f"**Conector #{i+1}**")
                tipo = st.text_input(
                    "Tipo de conector",
                    value="Type2",
                    placeholder="Ej. Type2, CCS, NACS o GB/T",
                    key=f"tipo_{i}",
                )
                potencia = st.number_input(f"Potencia (kW)", min_value=1.0, value=22.0, key=f"pot_{i}")
                conectores.append({
                    "id_conector": i + 1,
                    "tipo": tipo,
                    "estado": "Available",
                    "potencia_max_kw": potencia
                })
        
        submitted = st.form_submit_button(
            "💾 Registrar Cargador",
            use_container_width=True,
            disabled=not is_admin,
        )
        if submitted:
            if not cp_id or not nombre:
                st.error("⚠️ El Identificador y Nombre son obligatorios")
            elif len(ocpp_password) < 12:
                st.error("⚠️ La contraseña OCPP debe tener al menos 12 caracteres.")
            elif any(not conector["tipo"].strip() for conector in conectores):
                st.error("⚠️ El tipo de conector es obligatorio.")
            else:
                result = api_post("/cargadores/", json={
                    "charge_point_id": cp_id,
                    "nombre": nombre,
                    "ubicacion": ubicacion,
                    "modelo": modelo,
                    "fabricante": fabricante,
                    "conectores": conectores,
                    "estado_general": "disponible",
                    "activo": True,
                    "ocpp_password": ocpp_password,
                })
                if "error" in result:
                    st.error(f"❌ {result['error']}")
                else:
                    st.success("✅ Cargador registrado exitosamente")
                    st.rerun()

st.divider()

# ============= RESUMEN DE CARGADORES =============
if isinstance(cargadores, list):
    total_cargadores = len(cargadores)
    disponibles = sum(
        1 for charger in cargadores
        if str(charger.get("estado_general", "")).lower() == "disponible"
    )
    conectados = sum(1 for charger in cargadores if charger.get("conectado") is True)
    fuera_servicio = sum(
        1 for charger in cargadores
        if str(charger.get("estado_general", "")).lower() in {"fuera_de_servicio", "falla"}
    )

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
                <div class="summary-card-label">Total de cargadores</div>
                <div class="summary-card-value">{total_cargadores}</div>
                <div class="summary-card-detail">Inventario completo</div>
            </div>
            <div class="summary-card" style="--summary-accent:#16a34a">
                <div class="summary-card-label">Disponibles</div>
                <div class="summary-card-value">{disponibles}</div>
                <div class="summary-card-detail">Listos para operar</div>
            </div>
            <div class="summary-card" style="--summary-accent:#0ea5e9">
                <div class="summary-card-label">Conectados</div>
                <div class="summary-card-value">{conectados}</div>
                <div class="summary-card-detail">Con comunicación activa</div>
            </div>
            <div class="summary-card" style="--summary-accent:#ef4444">
                <div class="summary-card-label">Fuera de servicio / falla</div>
                <div class="summary-card-value">{fuera_servicio}</div>
                <div class="summary-card-detail">Requieren atención</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.divider()

# ============= SECCIÓN: FILTROS Y BÚSQUEDA =============
st.markdown("**Filtrar Cargadores**")
filter_cols = st.columns([2.2, 1.4, 1.2, 0.8])
with filter_cols[0]:
    filtro_nombre = st.text_input(
        "Buscar por nombre o ubicación",
        placeholder="ej. Estación Centro",
        key="filtro_nombre",
    )
with filter_cols[1]:
    filtro_estado = st.multiselect(
        "Por estado",
        [
            "Disponible",
            "Ocupado",
            "Mantenimiento",
            "Fuera de servicio",
            "Falla",
            "Cargando",
            "Preparando",
        ],
        key="filtro_estado",
        placeholder="Todos los estados",
    )
with filter_cols[2]:
    filtro_conexion = st.selectbox(
        "Conexión",
        ["Todos", "Conectados", "Desconectados"],
        key="filtro_conexion",
    )
with filter_cols[3]:
    if st.button("🔄 Actualizar", use_container_width=True, key="refresh_chargers_list"):
        st.rerun()

if isinstance(cargadores, dict) and "error" in cargadores:
    st.error("❌ No se pudieron cargar los cargadores")
elif not cargadores:
    st.info("ℹ️ No hay cargadores registrados. Registra uno nuevo para comenzar.")
else:
    estado_config = {
        "disponible": {"icon": "🟢", "label": "Disponible"},
        "ocupado": {"icon": "🔵", "label": "Ocupado"},
        "mantenimiento": {"icon": "🟡", "label": "Mantenimiento"},
        "fuera_de_servicio": {"icon": "⚫", "label": "Fuera de servicio"},
        "falla": {"icon": "🔴", "label": "Falla"},
        "cargando": {"icon": "🔵", "label": "Cargando"},
        "preparando": {"icon": "🟡", "label": "Preparando"},
    }
    conexion_text = {True: "🟢 Conectado", False: "⚪ Desconectado"}
    estado_filtro = {
        "Disponible": "disponible",
        "Ocupado": "ocupado",
        "Mantenimiento": "mantenimiento",
        "Fuera de servicio": "fuera_de_servicio",
        "Falla": "falla",
        "Cargando": "cargando",
        "Preparando": "preparando",
    }

    cargadores_filtrados = cargadores
    search_term = filtro_nombre.strip().casefold()
    if search_term:
        cargadores_filtrados = [
            charger for charger in cargadores_filtrados
            if search_term in str(charger.get("nombre", "")).casefold()
            or search_term in str(charger.get("ubicacion", "")).casefold()
            or search_term in str(charger.get("charge_point_id", "")).casefold()
        ]
    if filtro_estado:
        selected_states = {estado_filtro[state] for state in filtro_estado}
        cargadores_filtrados = [
            charger for charger in cargadores_filtrados
            if str(charger.get("estado_general", "")).lower() in selected_states
        ]
    if filtro_conexion == "Conectados":
        cargadores_filtrados = [charger for charger in cargadores_filtrados if charger.get("conectado") is True]
    elif filtro_conexion == "Desconectados":
        cargadores_filtrados = [charger for charger in cargadores_filtrados if charger.get("conectado") is not True]

    filter_signature = (search_term, tuple(sorted(filtro_estado)), filtro_conexion)
    if st.session_state.get("cargadores_filter_signature") != filter_signature:
        st.session_state.cargadores_filter_signature = filter_signature
        st.session_state.cargadores_page = 1
        st.session_state.pop("cargadores_selected", None)

    if not cargadores_filtrados:
        st.info("ℹ️ No hay cargadores que coincidan con los filtros")
    else:
        page_size = 20
        total_pages = max(1, (len(cargadores_filtrados) + page_size - 1) // page_size)
        current_page = min(max(st.session_state.get("cargadores_page", 1), 1), total_pages)
        st.session_state.cargadores_page = current_page
        start_index = (current_page - 1) * page_size
        cargadores_visibles = cargadores_filtrados[start_index:start_index + page_size]

        def charger_key(charger):
            return str(charger.get("_id") or charger.get("charge_point_id", ""))

        visible_keys = {charger_key(charger) for charger in cargadores_visibles}
        selected_key = st.session_state.get("cargadores_selected")
        if selected_key not in visible_keys:
            selected_key = charger_key(cargadores_visibles[0])
            st.session_state.cargadores_selected = selected_key
        selected_charger = next(
            charger for charger in cargadores_visibles
            if charger_key(charger) == selected_key
        )

        list_col, detail_col = st.columns([1.05, 3.2], gap="medium")
        with list_col:
            with st.container(border=True):
                st.markdown(f"**Cargadores Registrados** · {len(cargadores_filtrados)}")
                for charger in cargadores_visibles:
                    key = charger_key(charger)
                    charger_state = str(charger.get("estado_general", "fuera_de_servicio")).lower()
                    state_config = estado_config.get(charger_state, estado_config["fuera_de_servicio"])
                    label = str(charger.get("nombre") or "Sin nombre")
                    charger_id_label = str(charger.get("charge_point_id") or "Sin identificador")
                    if st.button(
                        f"{state_config['icon']} {label}\nID: {charger_id_label}",
                        key=f"select_charger_{key}",
                        type="primary" if key == selected_key else "secondary",
                        use_container_width=True,
                    ):
                        st.session_state.cargadores_selected = key
                        st.rerun()

                pagination_cols = st.columns([1, 1])
                with pagination_cols[0]:
                    if st.button(
                        "←",
                        help="Página anterior",
                        disabled=current_page == 1,
                        use_container_width=True,
                        key="cargadores_previous_page",
                    ):
                        st.session_state.cargadores_page = current_page - 1
                        st.session_state.pop("cargadores_selected", None)
                        st.rerun()
                with pagination_cols[1]:
                    if st.button(
                        "→",
                        help="Página siguiente",
                        disabled=current_page == total_pages,
                        use_container_width=True,
                        key="cargadores_next_page",
                    ):
                        st.session_state.cargadores_page = current_page + 1
                        st.session_state.pop("cargadores_selected", None)
                        st.rerun()
                st.caption(
                    f"Página {current_page} de {total_pages} · {len(cargadores_visibles)} de 20"
                )

        with detail_col:
            _render_charger_details(selected_charger, estado_config, conexion_text)