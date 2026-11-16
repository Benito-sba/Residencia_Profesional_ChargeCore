from nicegui import ui

from pages.client_page_utils import (
    client_error,
    display_value,
    page_container,
    render_client_shell,
    render_page_heading,
    show_error,
)
from utils.api_client import api_delete, api_get, api_post, api_put


CONNECTORS = {
    "Type1": "Tipo 1",
    "Type2": "Tipo 2",
    "CCS": "CCS",
    "CHAdeMO": "CHAdeMO",
    "Tesla": "Tesla",
}


def open_vehicle_dialog(token, api_base_url, vehicle=None):
    editing = vehicle is not None
    current = vehicle or {}
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-2xl gap-4 p-6"
    ):
        ui.label("Editar vehículo" if editing else "Agregar vehículo").classes(
            "text-xl font-bold text-white"
        )
        with ui.row().classes("w-full flex-wrap gap-3"):
            brand = ui.input("Marca", value=current.get("marca") or "").props(
                "outlined"
            ).classes("min-w-48 flex-1")
            model = ui.input("Modelo", value=current.get("modelo") or "").props(
                "outlined"
            ).classes("min-w-48 flex-1")
        with ui.row().classes("w-full flex-wrap gap-3"):
            plate = ui.input("Placa", value=current.get("placa") or "").props(
                "outlined"
            ).classes("min-w-48 flex-1")
            year = ui.number(
                "Año",
                value=current.get("anio", 2024),
                min=2000,
                max=2030,
                step=1,
            ).props("outlined").classes("min-w-32 flex-1")
        connector = ui.select(
            options=CONNECTORS,
            value=current.get("tipo_conector", "Type2"),
            label="Tipo de conector",
        ).props("outlined").classes("w-full")
        capacity = ui.number(
            "Capacidad de batería (kWh)",
            value=current.get("capacidad_bateria_kwh"),
            min=0,
            step=1,
        ).props("outlined clearable").classes("w-full")

        def save():
            brand_value = (brand.value or "").strip()
            model_value = (model.value or "").strip()
            plate_value = (plate.value or "").strip()
            try:
                year_value = int(year.value)
            except (TypeError, ValueError):
                ui.notify("Ingresa un año válido.", type="warning")
                return
            if not brand_value or not model_value:
                ui.notify("Marca y modelo son obligatorios.", type="warning")
                return
            if not 2000 <= year_value <= 2030:
                ui.notify("El año debe estar entre 2000 y 2030.", type="warning")
                return
            try:
                capacity_value = (
                    float(capacity.value)
                    if capacity.value not in (None, "")
                    else None
                )
            except (TypeError, ValueError):
                ui.notify("Ingresa una capacidad de batería válida.", type="warning")
                return
            if capacity_value is not None and capacity_value <= 0:
                ui.notify("La capacidad debe ser mayor que cero.", type="warning")
                return
            payload = {
                "marca": brand_value,
                "modelo": model_value,
                "anio": year_value,
                "placa": plate_value or None,
                "tipo_conector": connector.value or "Type2",
                "capacidad_bateria_kwh": capacity_value,
            }
            endpoint = (
                f"/client/me/vehicles/{current['_id']}"
                if editing
                else "/client/me/vehicles"
            )
            save_vehicle = api_put if editing else api_post
            result = save_vehicle(
                endpoint,
                json=payload,
                token=token,
                base_url=api_base_url,
            )
            if isinstance(result, dict) and "error" in result:
                ui.notify(client_error(result, "No se pudo guardar el vehículo."), type="negative")
                return
            dialog.close()
            ui.notify(
                "Vehículo actualizado." if editing else "Vehículo registrado.",
                type="positive",
            )
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props(
                "flat no-caps"
            ).classes("text-slate-300")
            ui.button("Guardar", icon="save", on_click=save).props(
                "no-caps"
            ).classes("chargecore-primary bg-[#ff500a] text-white")
    dialog.open()


def confirm_vehicle_deactivation(token, api_base_url, vehicle):
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-md gap-4 p-6"
    ):
        ui.label("Desactivar vehículo").classes("text-xl font-bold text-white")
        ui.label(
            f"¿Deseas desactivar {vehicle.get('marca', '')} "
            f"{vehicle.get('modelo', '')}? Se conservará el historial de cargas."
        ).classes("text-slate-300")

        def deactivate():
            result = api_delete(
                f"/client/me/vehicles/{vehicle['_id']}",
                token=token,
                base_url=api_base_url,
            )
            if isinstance(result, dict) and "error" in result:
                ui.notify(client_error(result, "No se pudo desactivar el vehículo."), type="negative")
                return
            dialog.close()
            ui.notify("Vehículo desactivado.", type="positive")
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props(
                "flat no-caps"
            ).classes("text-slate-300")
            ui.button("Desactivar", icon="delete", on_click=deactivate).props(
                "no-caps"
            ).classes("bg-red-700 text-white")
    dialog.open()


def render_page(token: str, api_base_url: str):
    if not render_client_shell("mis_vehiculos", token, api_base_url):
        return
    vehicles = api_get(
        "/client/me/vehicles",
        token=token,
        base_url=api_base_url,
    )
    with page_container():
        with ui.row().classes("w-full flex-wrap items-center justify-between gap-4"):
            render_page_heading(
                "Mis vehículos",
                "Administra los vehículos asociados a tu cuenta.",
            )
            ui.button(
                "Agregar vehículo",
                icon="add",
                on_click=lambda: open_vehicle_dialog(token, api_base_url),
            ).props("no-caps").classes(
                "chargecore-primary bg-[#ff500a] text-white"
            )

        if isinstance(vehicles, dict) and "error" in vehicles:
            show_error(client_error(vehicles, "No fue posible cargar tus vehículos."))
            return
        if not isinstance(vehicles, list):
            show_error("La respuesta de vehículos no tiene el formato esperado.")
            return
        if not vehicles:
            with ui.card().classes("chargecore-card w-full gap-3 p-6"):
                ui.icon("directions_car").classes("text-4xl text-orange-400")
                ui.label("Aún no tienes vehículos").classes("text-xl font-bold")
                ui.label(
                    "Registra tu vehículo para poder seleccionarlo al iniciar una carga."
                ).classes("text-slate-400")
            return

        ui.label(
            f"{len(vehicles)} vehículo{'s' if len(vehicles) != 1 else ''} activo"
            f"{'s' if len(vehicles) != 1 else ''}"
        ).classes("text-sm text-slate-400")
        with ui.element("div").classes(
            "grid w-full grid-cols-1 gap-4 lg:grid-cols-2"
        ):
            for vehicle in vehicles:
                with ui.card().classes("chargecore-card w-full gap-4 p-5"):
                    with ui.row().classes("w-full items-start justify-between"):
                        with ui.row().classes("items-center gap-3"):
                            ui.icon("directions_car").classes(
                                "rounded-xl bg-orange-950 p-3 text-2xl text-orange-400"
                            )
                            with ui.column().classes("gap-1"):
                                ui.label(
                                    f"{vehicle.get('marca', '')} {vehicle.get('modelo', '')}"
                                ).classes("text-lg font-bold text-white")
                                ui.label(display_value(vehicle.get("placa"), "Sin placa")).classes(
                                    "text-sm text-slate-400"
                                )
                        ui.badge("Activo").classes("bg-green-950 text-green-300")
                    with ui.element("div").classes("grid w-full grid-cols-2 gap-3"):
                        capacity = vehicle.get("capacidad_bateria_kwh")
                        details = (
                            ("Año", vehicle.get("anio")),
                            ("Conector", CONNECTORS.get(
                                vehicle.get("tipo_conector"),
                                vehicle.get("tipo_conector"),
                            )),
                            ("Batería", f"{capacity:g} kWh" if isinstance(
                                capacity, (int, float)
                            ) else "No indicada"),
                            ("Placa", vehicle.get("placa") or "No registrada"),
                        )
                        for label, value in details:
                            with ui.column().classes("gap-1"):
                                ui.label(label).classes(
                                    "text-xs uppercase tracking-wide text-slate-500"
                                )
                                ui.label(display_value(value)).classes(
                                    "text-sm font-medium text-slate-200"
                                )
                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button(
                            "Editar",
                            icon="edit",
                            on_click=lambda item=vehicle: open_vehicle_dialog(
                                token, api_base_url, item
                            ),
                        ).props("flat no-caps").classes("text-orange-400")
                        ui.button(
                            "Desactivar",
                            icon="delete",
                            on_click=lambda item=vehicle: confirm_vehicle_deactivation(
                                token, api_base_url, item
                            ),
                        ).props("flat no-caps").classes("text-red-300")
