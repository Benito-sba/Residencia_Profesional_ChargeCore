import streamlit as st

from components.sidebar import render_sidebar, _redirect_to_login
from utils.api_client import api_delete, api_get, api_post, api_put
from utils.state import init_session_state


CONNECTOR_OPTIONS = ["Type1", "Type2", "CCS", "CHAdeMO", "Tesla"]

st.set_page_config(
    page_title="ChargeCore | Mis Vehículos",
    page_icon="🚗",
    layout="wide",
    initial_sidebar_state="expanded",
)


def operation_error(result):
    status_code = result.get("_status_code") if isinstance(result, dict) else None
    if status_code == 401:
        return "Tu sesión ha expirado. Vuelve a iniciar sesión."
    if status_code == 403:
        return "No tienes permisos para acceder a esta sección."
    if status_code == 404:
        return "El vehículo ya no está disponible."
    if status_code == 422:
        return "Revisa los datos ingresados."
    if isinstance(status_code, int) and status_code >= 500:
        return "No fue posible completar la operación. Intenta nuevamente."
    error = result.get("error", "") if isinstance(result, dict) else ""
    if "permisos" in error.lower():
        return "No tienes permisos para acceder a esta sección."
    if "no existe" in error.lower() or "no encontrado" in error.lower():
        return "El vehículo ya no está disponible."
    if "revisa" in error.lower() or "obligatorios" in error.lower():
        return "Revisa los datos ingresados."
    if "servidor" in error.lower() or "inesperado" in error.lower():
        return "No fue posible completar la operación. Intenta nuevamente."
    return error or "No fue posible completar la operación. Intenta nuevamente."


def render_vehicle_form(form_key, vehicle=None):
    vehicle = vehicle or {}
    current_capacity = vehicle.get("capacidad_bateria_kwh")
    current_capacity_is_known = isinstance(current_capacity, (int, float)) and current_capacity > 0
    capacity_choice = st.selectbox(
        "Capacidad de batería",
        ["No conozco este dato", "Sí, conozco la capacidad"],
        index=1 if current_capacity_is_known else 0,
        help="Puedes completar este dato más adelante.",
        key=f"{form_key}_capacidad_conocida",
    )
    with st.form(form_key, border=False):
        first_column, second_column = st.columns(2)
        with first_column:
            marca = st.text_input("Marca", value=vehicle.get("marca", ""), key=f"{form_key}_marca")
            modelo = st.text_input("Modelo", value=vehicle.get("modelo", ""), key=f"{form_key}_modelo")
            placa = st.text_input("Placa", value=vehicle.get("placa", ""), key=f"{form_key}_placa")
        with second_column:
            anio = st.number_input(
                "Año",
                min_value=2000,
                max_value=2030,
                value=int(vehicle.get("anio", 2024)),
                step=1,
                key=f"{form_key}_anio",
            )
            connector = vehicle.get("tipo_conector", "Type2")
            connector_index = CONNECTOR_OPTIONS.index(connector) if connector in CONNECTOR_OPTIONS else 1
            tipo_conector = st.selectbox(
                "Tipo de conector",
                CONNECTOR_OPTIONS,
                index=connector_index,
                key=f"{form_key}_conector",
                format_func={"Type1": "Tipo 1", "Type2": "Tipo 2", "CCS": "CCS", "CHAdeMO": "CHAdeMO", "Tesla": "Tesla"}.get,
            )
            capacidad = None
            if capacity_choice == "Sí, conozco la capacidad":
                capacidad = st.number_input(
                    "Capacidad de batería (kWh)",
                    min_value=0.0,
                    value=float(current_capacity) if current_capacity_is_known else 0.0,
                    step=1.0,
                    help="Ingresa un valor mayor que 0 kWh.",
                    key=f"{form_key}_capacidad",
                )

        submitted = st.form_submit_button("Guardar", type="primary", use_container_width=True)

    if not submitted:
        return None

    if not marca.strip() or not modelo.strip() or not placa.strip():
        st.error("Marca, modelo y placa son obligatorios.")
        return None
    if capacity_choice == "Sí, conozco la capacidad" and capacidad <= 0:
        st.error("Ingresa una capacidad de batería mayor que 0 kWh.")
        return None

    return {
        "marca": marca.strip(),
        "modelo": modelo.strip(),
        "anio": int(anio),
        "placa": placa.strip(),
        "tipo_conector": tipo_conector,
        "capacidad_bateria_kwh": float(capacidad) if capacidad is not None else None,
    }


def load_vehicles():
    result = api_get("/client/me/vehicles")
    if isinstance(result, dict) and "error" in result:
        return None, operation_error(result)
    return result if isinstance(result, list) else [], None


init_session_state()

if not st.session_state.get("authenticated"):
    _redirect_to_login()

if (st.session_state.get("user") or {}).get("rol") != "cliente":
    st.error("No tienes permisos para acceder a esta sección.")
    st.stop()

render_sidebar()

st.markdown(
    """
    <style>
    .vehicle-page-header {
        border-bottom: 1px solid #e2e8f0;
        margin-bottom: 1.5rem;
        padding-bottom: 1rem;
    }
    .vehicle-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1.1rem 1.2rem;
        min-height: 12rem;
        margin-bottom: 1rem;
    }
    .vehicle-label {
        color: #64748b;
        font-size: 0.78rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .vehicle-title {
        color: #0f172a;
        font-size: 1.25rem;
        font-weight: 750;
        margin: 0.2rem 0 0.8rem;
    }
    .vehicle-detail {
        color: #334155;
        font-size: 0.92rem;
        margin: 0.25rem 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="vehicle-page-header"><h1>Mis Vehículos</h1>'
    '<p>Administra los vehículos asociados a tu cuenta.</p></div>',
    unsafe_allow_html=True,
)

if st.button("➕ Agregar vehículo", type="primary"):
    st.session_state.show_add_vehicle = not st.session_state.get("show_add_vehicle", False)
    st.session_state.edit_vehicle_id = None

if st.session_state.get("show_add_vehicle"):
    with st.container(border=True):
        st.markdown("### Registrar vehículo")
        payload = render_vehicle_form("client_add_vehicle")
        if payload is not None:
            result = api_post("/client/me/vehicles", json=payload)
            if "error" in result:
                st.error(operation_error(result))
            else:
                st.success("Vehículo registrado correctamente.")
                st.session_state.show_add_vehicle = False
                st.rerun()

vehicles, load_error = load_vehicles()
if load_error:
    st.error(load_error)
    st.stop()

if not vehicles:
    st.info("No tienes vehículos activos registrados.")
else:
    st.markdown(f"**{len(vehicles)} {'vehículo activo' if len(vehicles) == 1 else 'vehículos activos'}**")
    for vehicle in vehicles:
        vehicle_id = vehicle.get("_id")
        with st.container(border=True):
            details_column, actions_column = st.columns([4, 1])
            with details_column:
                st.markdown('<div class="vehicle-label">Vehículo</div>', unsafe_allow_html=True)
                st.markdown(
                    f'<div class="vehicle-title">{vehicle.get("marca", "")} '
                    f'{vehicle.get("modelo", "")}</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(f'<div class="vehicle-detail"><b>Placa:</b> {vehicle.get("placa") or "No registrada"}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="vehicle-detail"><b>Año:</b> {vehicle.get("anio", "No disponible")}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="vehicle-detail"><b>Conector:</b> { {"Type1": "Tipo 1", "Type2": "Tipo 2"}.get(vehicle.get("tipo_conector", "No disponible"), vehicle.get("tipo_conector", "No disponible")) }</div>', unsafe_allow_html=True)
                capacity = vehicle.get("capacidad_bateria_kwh")
                capacity_label = f"{capacity:g} kWh" if isinstance(capacity, (int, float)) else "No disponible"
                st.markdown(f'<div class="vehicle-detail"><b>Batería:</b> {capacity_label}</div>', unsafe_allow_html=True)
                st.markdown('<div class="vehicle-detail"><b>Estado:</b> Activo</div>', unsafe_allow_html=True)
            with actions_column:
                if st.button("Editar", key=f"edit_button_{vehicle_id}", use_container_width=True):
                    st.session_state.edit_vehicle_id = vehicle_id
                    st.session_state.show_add_vehicle = False
                    st.rerun()
                if st.button("Desactivar", key=f"delete_button_{vehicle_id}", use_container_width=True):
                    st.session_state.confirm_vehicle_id = vehicle_id
                    st.rerun()

            if st.session_state.get("edit_vehicle_id") == vehicle_id:
                st.divider()
                st.markdown("### Editar vehículo")
                update_payload = render_vehicle_form(f"client_edit_vehicle_{vehicle_id}", vehicle)
                if update_payload is not None:
                    result = api_put(f"/client/me/vehicles/{vehicle_id}", json=update_payload)
                    if "error" in result:
                        st.error(operation_error(result))
                    else:
                        st.success("Vehículo actualizado correctamente.")
                        st.session_state.edit_vehicle_id = None
                        st.rerun()

            if st.session_state.get("confirm_vehicle_id") == vehicle_id:
                st.warning("Esta acción desactivará el vehículo. ¿Deseas continuar?")
                confirm_column, cancel_column = st.columns(2)
                with confirm_column:
                    if st.button("Confirmar desactivación", key=f"confirm_delete_{vehicle_id}", type="primary", use_container_width=True):
                        result = api_delete(f"/client/me/vehicles/{vehicle_id}")
                        if "error" in result:
                            st.error(operation_error(result))
                        else:
                            st.success("Vehículo desactivado correctamente.")
                            st.session_state.confirm_vehicle_id = None
                            st.rerun()
                with cancel_column:
                    if st.button("Cancelar", key=f"cancel_delete_{vehicle_id}", use_container_width=True):
                        st.session_state.confirm_vehicle_id = None
                        st.rerun()
