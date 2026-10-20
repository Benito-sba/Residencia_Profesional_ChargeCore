import streamlit as st
from utils.state import init_session_state
from utils.api_client import api_get, api_post, api_put
from components.sidebar import render_sidebar, _redirect_to_login

init_session_state()
render_sidebar()

if not st.session_state.get("authenticated"):
    _redirect_to_login()

is_admin = st.session_state.get("role") == "admin"
if not is_admin:
    st.error("❌ Acceso denegado. Solo administradores pueden gestionar vehículos.")
    st.stop()

st.markdown("# 🚗 Gestión de Vehículos")
st.markdown("Registra y administra los vehículos eléctricos de los usuarios")

# ============= SECCIÓN: REGISTRAR NUEVO VEHÍCULO =============
if is_admin:
    with st.expander("➕ Registrar Nuevo Vehículo", expanded=False):
        with st.spinner("Cargando usuarios..."):
            users = api_get("/usuarios/")
        
        user_options = {f"{u['nombre']} ({u['email']})": u["_id"] for u in users} if users and "error" not in users else {}
        capacidad_conocida = st.selectbox(
            "Capacidad de batería",
            ["No conozco este dato", "Sí, conozco la capacidad"],
            help="Puedes completar este dato más adelante.",
            key="nuevo_vehiculo_capacidad_conocida",
        )
        
        with st.form("form_vehiculo"):
            col1, col2 = st.columns(2)
            with col1:
                if user_options:
                    usuario = st.selectbox("Propietario", options=list(user_options.keys()))
                else:
                    st.error("⚠️ No hay usuarios disponibles. Registra usuarios primero.")
                    usuario = None
                
                marca = st.text_input("Marca", placeholder="ej. Tesla")
                modelo = st.text_input("Modelo", placeholder="ej. Model 3")
            
            with col2:
                anio = st.number_input("Año de Fabricación", min_value=2000, max_value=2030, value=2024)
                placa = st.text_input("Placa", placeholder="ej. ABC-123")
                tipo_conector = st.selectbox("Tipo de Conector Compatible", 
                    ["Type1", "Type2", "CCS", "CHAdeMO", "Tesla"],
                    format_func={"Type1": "Tipo 1", "Type2": "Tipo 2", "CCS": "CCS", "CHAdeMO": "CHAdeMO", "Tesla": "Tesla"}.get)
            
            capacidad = None
            if capacidad_conocida == "Sí, conozco la capacidad":
                capacidad = st.number_input(
                    "Capacidad de Batería (kWh)",
                    min_value=0.0,
                    step=1.0,
                    value=0.0,
                    help="Ingresa un valor mayor que 0 kWh.",
                )
            
            submitted = st.form_submit_button("💾 Registrar Vehículo", use_container_width=True)
            if submitted:
                if not marca or not modelo or not placa:
                    st.error("⚠️ Marca, Modelo y Placa son obligatorios")
                elif not user_options:
                    st.error("⚠️ No hay usuarios disponibles")
                elif capacidad_conocida == "Sí, conozco la capacidad" and capacidad <= 0:
                    st.error("⚠️ Ingresa una capacidad de batería mayor que 0 kWh")
                else:
                    result = api_post("/vehiculos/", json={
                        "usuario_id": user_options[usuario],
                        "marca": marca,
                        "modelo": modelo,
                        "anio": anio,
                        "placa": placa,
                        "tipo_conector": tipo_conector,
                        "capacidad_bateria_kwh": capacidad,
                        "activo": True
                    })
                    if "error" in result:
                        st.error(f"❌ {result['error']}")
                    else:
                        st.success("✅ Vehículo registrado exitosamente")
                        st.rerun()

st.divider()

# ============= RESUMEN DE VEHÍCULOS =============
with st.spinner("Cargando vehículos..."):
    vehiculos = api_get("/vehiculos/")
    users = api_get("/usuarios/") if is_admin else []

if isinstance(vehiculos, list):
    capacidades_conocidas = [
        v["capacidad_bateria_kwh"]
        for v in vehiculos
        if isinstance(v.get("capacidad_bateria_kwh"), (int, float))
        and v["capacidad_bateria_kwh"] > 0
    ]
    capacidad_promedio = (
        f"{sum(capacidades_conocidas) / len(capacidades_conocidas):.1f}"
        if capacidades_conocidas
        else "—"
    )
    marcas_unicas = len({
        str(v.get("marca", "")).strip().casefold()
        for v in vehiculos
        if str(v.get("marca", "")).strip()
    })
    vehiculos_activos = sum(1 for v in vehiculos if v.get("activo", True))
    capacidad_detalle = (
        f"Con dato en {len(capacidades_conocidas)} vehículos"
        if capacidades_conocidas
        else "Sin capacidades registradas"
    )

    st.markdown(
        """
        <style>
        .vehicle-summary-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.75rem;
            margin: 0.75rem 0 1.25rem;
        }
        .vehicle-summary-card {
            background: #ffffff;
            border: 1px solid #dbe5ef;
            border-left: 3px solid var(--summary-accent);
            border-radius: 12px;
            box-shadow: 0 5px 16px rgba(15, 23, 42, 0.06);
            min-height: 6rem;
            padding: 0.85rem 1rem;
        }
        .vehicle-summary-label {
            color: #475569;
            font-size: 0.88rem;
            font-weight: 600;
        }
        .vehicle-summary-value {
            color: #0f172a;
            font-size: 1.75rem;
            font-weight: 700;
            line-height: 1.25;
            margin: 0.25rem 0;
        }
        .vehicle-summary-detail {
            color: var(--summary-accent);
            font-size: 0.76rem;
        }
        @media (max-width: 800px) {
            .vehicle-summary-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        @media (max-width: 480px) {
            .vehicle-summary-grid {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div class="vehicle-summary-grid">
            <div class="vehicle-summary-card" style="--summary-accent:#2563eb">
                <div class="vehicle-summary-label">Total de vehículos</div>
                <div class="vehicle-summary-value">{len(vehiculos)}</div>
                <div class="vehicle-summary-detail">Inventario completo</div>
            </div>
            <div class="vehicle-summary-card" style="--summary-accent:#16a34a">
                <div class="vehicle-summary-label">Marcas diferentes</div>
                <div class="vehicle-summary-value">{marcas_unicas}</div>
                <div class="vehicle-summary-detail">Marcas registradas</div>
            </div>
            <div class="vehicle-summary-card" style="--summary-accent:#f59e0b">
                <div class="vehicle-summary-label">Capacidad promedio</div>
                <div class="vehicle-summary-value">{capacidad_promedio}</div>
                <div class="vehicle-summary-detail">{capacidad_detalle}</div>
            </div>
            <div class="vehicle-summary-card" style="--summary-accent:#16a34a">
                <div class="vehicle-summary-label">Vehículos activos</div>
                <div class="vehicle-summary-value">{vehiculos_activos}</div>
                <div class="vehicle-summary-detail">Disponibles en el sistema</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
elif isinstance(vehiculos, dict) and "error" in vehiculos:
    st.error(f"❌ No se pudieron cargar los vehículos: {vehiculos['error']}")
else:
    st.info("ℹ️ No hay vehículos registrados todavía.")

st.divider()

# ============= SECCIÓN: FILTROS =============
st.markdown("**Filtrar Vehículos**")
col1, col2, col3 = st.columns(3)

with col1:
    filtro_marca = st.text_input("Buscar por Marca", placeholder="ej. Tesla")
with col2:
    filtro_usuario = (
        st.text_input("Buscar por Usuario", placeholder="ej. Juan")
        if is_admin
        else ""
    )
with col3:
    if st.button("🔄 Actualizar", use_container_width=True):
        st.rerun()

# ============= SECCIÓN: LISTA DE VEHÍCULOS =============
st.markdown("**🚗 Vehículos Registrados**")

if not isinstance(vehiculos, list) or not vehiculos:
    st.info("ℹ️ No hay vehículos registrados. Registra un vehículo para comenzar.")
else:
    # Preparar datos
    user_dict = {str(u["_id"]): u["nombre"] for u in users} if users and "error" not in users else {}
    
    vehiculos_filtrados = vehiculos
    
    if filtro_marca:
        vehiculos_filtrados = [v for v in vehiculos_filtrados if filtro_marca.lower() in v.get('marca', '').lower()]
    
    if filtro_usuario:
        vehiculos_filtrados = [v for v in vehiculos_filtrados if filtro_usuario.lower() in user_dict.get(str(v.get("usuario_id", "")), '').lower()]

    filter_signature = (filtro_marca.strip().casefold(), filtro_usuario.strip().casefold())
    if st.session_state.get("vehiculos_filter_signature") != filter_signature:
        st.session_state.vehiculos_filter_signature = filter_signature
        st.session_state.vehiculos_page = 1
    
    if not vehiculos_filtrados:
        st.info("ℹ️ No hay vehículos que coincidan con los filtros")
    else:
        st.write(f"**Total: {len(vehiculos_filtrados)} {'vehículo' if len(vehiculos_filtrados) == 1 else 'vehículos'}**")

        page_size = 20
        total_pages = max(1, (len(vehiculos_filtrados) + page_size - 1) // page_size)
        current_page = min(max(st.session_state.get("vehiculos_page", 1), 1), total_pages)
        st.session_state.vehiculos_page = current_page
        start_index = (current_page - 1) * page_size
        vehiculos_visibles = vehiculos_filtrados[start_index:start_index + page_size]

        column_widths = [1.6, 1.05, 1.45, 1.0, 1.25, 0.65, 0.8]
        headers = [
            "🚗 Vehículo",
            "📋 Placa",
            "👤 Propietario",
            "🔌 Conector",
            "🔋 Batería (kWh)",
            "📅 Año",
            "✅ Estado",
        ]
        header_columns = st.columns(column_widths)
        for column, header in zip(header_columns, headers):
            column.markdown(f"**{header}**")

        for vehiculo in vehiculos_visibles:
            row_columns = st.columns(column_widths)
            capacidad = vehiculo.get("capacidad_bateria_kwh")
            values = [
                f"{vehiculo.get('marca', '')} {vehiculo.get('modelo', '')}".strip(),
                vehiculo.get("placa", "-"),
                user_dict.get(str(vehiculo.get("usuario_id", "")), "Desconocido"),
                {"Type1": "Tipo 1", "Type2": "Tipo 2"}.get(
                    vehiculo.get("tipo_conector", "-"),
                    vehiculo.get("tipo_conector", "-"),
                ),
                f"{capacidad:g} kWh" if isinstance(capacidad, (int, float)) and capacidad > 0 else "No especificada",
                vehiculo.get("anio", "-"),
                "✅ Activo" if vehiculo.get("activo", True) else "❌ Inactivo",
            ]
            for column, value in zip(row_columns, values):
                column.write(value)

            vehicle_id = vehiculo["_id"]
            with st.expander(
                f"Editar o desactivar {vehiculo.get('marca', 'vehículo')} "
                f"{vehiculo.get('modelo', '')} · {vehiculo.get('placa', '')}",
                expanded=False,
            ):
                with st.form(f"edit_vehicle_{vehicle_id}"):
                    edit_col1, edit_col2 = st.columns(2)
                    with edit_col1:
                        updated_make = st.text_input(
                            "Marca",
                            value=vehiculo.get("marca", ""),
                            key=f"vehicle_make_{vehicle_id}",
                        )
                        updated_model = st.text_input(
                            "Modelo",
                            value=vehiculo.get("modelo", ""),
                            key=f"vehicle_model_{vehicle_id}",
                        )
                        updated_plate = st.text_input(
                            "Placa",
                            value=vehiculo.get("placa") or "",
                            key=f"vehicle_plate_{vehicle_id}",
                        )
                        capacity_value = vehiculo.get("capacidad_bateria_kwh")
                        updated_capacity = st.text_input(
                            "Capacidad de batería (kWh; vacío para no cambiar)",
                            value=(
                                str(capacity_value)
                                if isinstance(capacity_value, (int, float))
                                else ""
                            ),
                            key=f"vehicle_capacity_{vehicle_id}",
                        )
                    with edit_col2:
                        updated_year = st.number_input(
                            "Año",
                            min_value=2000,
                            max_value=2030,
                            value=int(vehiculo.get("anio", 2024)),
                            key=f"vehicle_year_{vehicle_id}",
                        )
                        connector_options = [
                            "Type1", "Type2", "CCS", "CHAdeMO", "Tesla"
                        ]
                        current_connector = vehiculo.get("tipo_conector", "Type2")
                        updated_connector = st.selectbox(
                            "Tipo de conector",
                            connector_options,
                            index=(
                                connector_options.index(current_connector)
                                if current_connector in connector_options
                                else 1
                            ),
                            key=f"vehicle_connector_{vehicle_id}",
                        )

                    if st.form_submit_button(
                        "Guardar cambios", use_container_width=True
                    ):
                        try:
                            capacity = (
                                float(updated_capacity)
                                if updated_capacity.strip()
                                else None
                            )
                        except ValueError:
                            capacity = -1

                        if not updated_make.strip() or not updated_model.strip():
                            st.error("La marca y el modelo son obligatorios.")
                        elif capacity is not None and capacity <= 0:
                            st.error("La capacidad debe ser un número mayor que cero.")
                        else:
                            update_data = {
                                "marca": updated_make.strip(),
                                "modelo": updated_model.strip(),
                                "anio": int(updated_year),
                                "placa": updated_plate.strip() or None,
                                "tipo_conector": updated_connector,
                            }
                            if capacity is not None:
                                update_data["capacidad_bateria_kwh"] = capacity
                            result = api_put(
                                f"/vehiculos/{vehicle_id}", json=update_data
                            )
                            if "error" in result:
                                st.error(f"❌ {result['error']}")
                            else:
                                st.success("Vehículo actualizado.")
                                st.rerun()

                confirm_key = f"confirm_vehicle_deactivation_{vehicle_id}"
                if st.session_state.get(confirm_key):
                    st.warning("Confirma la desactivación de este vehículo.")
                    confirm_col, cancel_col = st.columns(2)
                    with confirm_col:
                        if st.button(
                            "Confirmar desactivación",
                            key=f"confirm_vehicle_{vehicle_id}",
                            type="primary",
                            use_container_width=True,
                        ):
                            result = api_put(
                                f"/vehiculos/{vehicle_id}", json={"activo": False}
                            )
                            if "error" in result:
                                st.error(f"❌ {result['error']}")
                            else:
                                st.session_state.pop(confirm_key, None)
                                st.success("Vehículo desactivado.")
                                st.rerun()
                    with cancel_col:
                        if st.button(
                            "Cancelar",
                            key=f"cancel_vehicle_{vehicle_id}",
                            use_container_width=True,
                        ):
                            st.session_state.pop(confirm_key, None)
                            st.rerun()
                elif st.button(
                    "Desactivar vehículo",
                    key=f"deactivate_vehicle_{vehicle_id}",
                ):
                    st.session_state[confirm_key] = True
                    st.rerun()
            st.divider()

        pagination_columns = st.columns([1, 2, 1])
        with pagination_columns[0]:
            if st.button(
                "← Anterior",
                disabled=current_page == 1,
                use_container_width=True,
                key="vehiculos_previous_page",
            ):
                st.session_state.vehiculos_page = current_page - 1
                st.rerun()
        with pagination_columns[1]:
            first_visible = start_index + 1
            last_visible = start_index + len(vehiculos_visibles)
            st.caption(
                f"Mostrando {first_visible}–{last_visible} de {len(vehiculos_filtrados)} "
                f"vehículos · Página {current_page} de {total_pages}"
            )
        with pagination_columns[2]:
            if st.button(
                "Siguiente →",
                disabled=current_page == total_pages,
                use_container_width=True,
                key="vehiculos_next_page",
            ):
                st.session_state.vehiculos_page = current_page + 1
                st.rerun()