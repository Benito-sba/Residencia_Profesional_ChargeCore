from threading import Lock

from nicegui import app, ui

from pages.client_page_utils import (
    client_error,
    display_value,
    format_date,
    page_container,
    render_client_shell,
    render_page_heading,
    show_error,
)
from utils.api_client import (
    OCPP_COMMAND_TIMEOUT_SECONDS,
    api_download,
    api_get,
    api_post,
)


def vehicle_label(vehicle):
    if not isinstance(vehicle, dict):
        return "Vehículo"
    name = " ".join(
        str(value)
        for value in (vehicle.get("marca"), vehicle.get("modelo"))
        if value
    )
    plate = vehicle.get("placa")
    return f"{name} · {plate}" if name and plate else name or plate or "Vehículo"


def show_session_ticket(session, token, api_base_url):
    user = app.storage.user.get("user") or {}
    session_id = str(session.get("id") or "")
    folio = f"CC-{session_id[-8:].upper()}" if session_id else "—"
    benefit = session.get("beneficio_aplicado")
    reward = session.get("recompensa")
    reward = reward if isinstance(reward, dict) else {}
    has_reward = bool(benefit or any(reward.values()))
    energy = session.get("energia_consumida_kwh")
    energy_text = (
        f"{float(energy):.2f}"
        if isinstance(energy, (int, float)) and not isinstance(energy, bool)
        else "—"
    )
    duration = session.get("duracion_minutos")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        hours, minutes = divmod(int(duration), 60)
        duration_text = f"{hours} h {minutes} min" if hours else f"{minutes} min"
    else:
        duration_text = "—"
    average_power = session.get("potencia_promedio_kw")
    power_text = (
        f"{float(average_power):g} kW"
        if isinstance(average_power, (int, float))
        and not isinstance(average_power, bool)
        else "—"
    )
    reward_title = " · ".join(
        str(value)
        for value in (reward.get("nombre"), reward.get("empresa"))
        if value
    )
    reward_description = (
        reward.get("descripcion")
        or benefit
        or "Beneficio aplicado a esta carga"
    )

    with ui.dialog() as dialog:
        with ui.card().classes("chargecore-card w-full max-w-sm gap-4 p-4"):
            with ui.element("div").classes(
                "w-full overflow-hidden rounded-xl bg-white text-[#1c1c1c]"
            ):
                with ui.row().classes(
                    "w-full items-center gap-3 bg-[#f15a24] p-4 text-white"
                ):
                    ui.icon("bolt").classes(
                        "rounded-lg bg-white p-2 text-2xl text-[#f15a24]"
                    )
                    with ui.column().classes("gap-0"):
                        ui.label("ChargeCore").classes(
                            "text-lg font-bold text-white"
                        )
                        ui.label("CRODE Mérida · Ticket de carga").classes(
                            "text-xs text-white"
                        )

                with ui.column().classes("w-full gap-3 p-4"):
                    with ui.column().classes("w-full items-center gap-0"):
                        ui.label("FOLIO").classes(
                            "text-xs tracking-wide text-stone-500"
                        )
                        ui.label(folio).classes(
                            "text-2xl font-semibold tracking-widest text-stone-900"
                        )
                        ui.label(
                            format_date(
                                session.get("fecha_fin")
                                or session.get("fecha_inicio")
                            )
                        ).classes("text-xs text-stone-500")

                    ui.separator().classes("bg-[#e6d7c8]")
                    details = (
                        ("Cliente", user.get("nombre") or "Cliente"),
                        ("Vehículo", vehicle_label(session.get("vehiculo"))),
                        (
                            "Cargador",
                            session.get("cargador")
                            or session.get("charge_point_id"),
                        ),
                        (
                            "Conector",
                            " · ".join(
                                str(value)
                                for value in (
                                    session.get("connector_id"),
                                    (session.get("vehiculo") or {}).get(
                                        "tipo_conector"
                                    ),
                                )
                                if value is not None
                            ),
                        ),
                        ("Inicio", format_date(session.get("fecha_inicio"))),
                        ("Fin", format_date(session.get("fecha_fin"))),
                        ("Duración", duration_text),
                        ("Potencia promedio", power_text),
                        (
                            "Estado",
                            str(session.get("estado") or "Finalizada").capitalize(),
                        ),
                    )
                    if session.get("motivo_fin"):
                        details += (("Motivo de cierre", session["motivo_fin"]),)

                    for label, value in details:
                        with ui.row().classes(
                            "w-full items-start justify-between gap-3"
                        ):
                            ui.label(label).classes(
                                "text-xs text-stone-500"
                            )
                            ui.label(display_value(value)).classes(
                                "max-w-[65%] text-right text-xs font-semibold "
                                "text-stone-900"
                            )

                    with ui.column().classes(
                        "w-full items-center gap-1 rounded-xl bg-[#1c1c1c] p-3"
                    ):
                        ui.label("ENERGÍA ENTREGADA").classes(
                            "text-xs text-stone-300"
                        )
                        ui.label(f"{energy_text} kWh").classes(
                            "text-3xl font-bold text-white"
                        )

                    if has_reward:
                        with ui.column().classes(
                            "w-full items-center gap-1 rounded-xl border "
                            "border-[#f15a24] bg-[#fff0e6] p-3 text-center"
                        ):
                            ui.label("¡GANASTE UNA RECOMPENSA!").classes(
                                "text-sm font-bold text-[#f15a24]"
                            )
                            if reward_title:
                                ui.label(reward_title).classes(
                                    "text-sm font-bold text-stone-900"
                                )
                            ui.label(display_value(reward_description)).classes(
                                "text-xs text-stone-800"
                            )
                            if reward.get("condiciones"):
                                ui.label(reward["condiciones"]).classes(
                                    "text-xs text-stone-600"
                                )

                    ui.separator().classes("bg-[#e6d7c8]")
                    ui.label(
                        "Gracias por realizar su carga con ChargeCore."
                    ).classes("w-full text-center text-sm font-bold text-stone-900")
                    ui.label("ChargeCore · Monitoreo de cargadores eléctricos").classes(
                        "w-full text-center text-xs text-stone-500"
                    )

            ticket_format = ui.select(
                {
                    "recibo": "Recibo · 80 mm",
                    "a6": "A6",
                    "carta": "Carta",
                },
                value="recibo",
                label="Formato del PDF",
            ).props("outlined").classes("w-full")

        def download_ticket():
            result = api_download(
                f"/client/me/sessions/{session_id}/ticket",
                params={"formato": ticket_format.value or "recibo"},
                token=token,
                base_url=api_base_url,
            )
            if isinstance(result, dict) and result.get("error"):
                ui.notify(
                    client_error(result, "No se pudo descargar el ticket."),
                    type="negative",
                )
                return
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("content"), bytes)
                or not result["content"].startswith(b"%PDF")
            ):
                ui.notify(
                    "El servidor devolvió un PDF inválido.",
                    type="negative",
                )
                return
            ui.download(
                result["content"],
                result.get("filename")
                or f"chargecore-ticket-{folio}-{ticket_format.value}.pdf",
                result.get("content_type") or "application/pdf",
            )

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cerrar", on_click=dialog.close).props("flat no-caps")
            ui.button("Descargar PDF", icon="picture_as_pdf", on_click=download_ticket).props(
                "no-caps"
            ).classes("chargecore-primary bg-[#ff500a] text-white")
    dialog.open()


def render_charge_requests(requests, vehicles, token, api_base_url):
    ui.label("Iniciar una carga").classes("text-xl font-bold text-white")
    ui.label(
        "Conecta el vehículo y elige iniciar con tu tarjeta NFC o escaneando "
        "el QR del cargador. La carga empieza cuando confirmes el vehículo y "
        "el conector."
    ).classes("text-sm leading-6 text-slate-400")
    if not requests:
        ui.label(
            "Al acercar tu tarjeta o escanear un QR, la solicitud aparecerá "
            "aquí durante tres minutos para elegir tu vehículo y conector."
        ).classes("text-sm text-slate-400")
        return

    for charge_request in requests:
        with ui.card().classes("chargecore-card w-full gap-4 p-5"):
            charger = charge_request.get("cargador") or charge_request.get(
                "charge_point_id"
            )
            source_label = (
                "QR escaneado"
                if charge_request.get("source") == "qr"
                else "Tarjeta detectada"
            )
            ui.label(f"{source_label} · {display_value(charger)}").classes(
                "text-lg font-bold text-white"
            )
            if charge_request.get("status") == "start_requested":
                ui.label(
                    "La orden se envió al cargador. Actualiza en unos segundos para confirmar el inicio."
                ).classes("text-amber-300")
                continue
            if not vehicles:
                ui.label(
                    "Agrega un vehículo en Mis Vehículos antes de iniciar una carga."
                ).classes("text-amber-300")
                continue

            available_connectors = [
                connector
                for connector in charge_request.get("conectores", [])
                if str(connector.get("estado", "Available")).lower()
                in {"available", "disponible", "preparing", "preparando"}
            ]
            if not available_connectors:
                ui.label("El cargador no reporta conectores disponibles.").classes(
                    "text-amber-300"
                )
                continue

            vehicle_options = {
                vehicle.get("id") or vehicle.get("_id"): vehicle_label(vehicle)
                for vehicle in vehicles
                if vehicle.get("id") or vehicle.get("_id")
            }
            connector_options = {
                connector.get("id"): (
                    f"Conector {connector.get('id')} · "
                    f"{connector.get('tipo') or 'Tipo no indicado'}"
                )
                for connector in available_connectors
                if connector.get("id") is not None
            }
            command_lock = Lock()
            with ui.row().classes("w-full flex-wrap gap-3"):
                vehicle_select = ui.select(
                    options=vehicle_options,
                    label="Selecciona el vehículo",
                ).props("outlined").classes("min-w-56 flex-1")
                connector_select = ui.select(
                    options=connector_options,
                    label="Selecciona el conector",
                ).props("outlined").classes("min-w-56 flex-1")

            def start_charge(
                request_id=charge_request.get("id"),
                vehicle_choice=vehicle_select,
                connector_choice=connector_select,
            ):
                if not vehicle_choice.value or connector_choice.value is None:
                    ui.notify(
                        "Selecciona el vehículo y el conector.",
                        type="warning",
                    )
                    return
                if not command_lock.acquire(blocking=False):
                    return
                start_button.disable()
                try:
                    result = api_post(
                        f"/client/me/charge-requests/{request_id}/start",
                        json={
                            "vehicle_id": vehicle_choice.value,
                            "connector_id": connector_choice.value,
                        },
                        token=token,
                        base_url=api_base_url,
                        timeout=OCPP_COMMAND_TIMEOUT_SECONDS,
                    )
                    if isinstance(result, dict) and "error" in result:
                        message = (
                            result.get("error")
                            if result.get("_status_code") == 504
                            else client_error(
                                result,
                                "No se pudo iniciar la carga.",
                            )
                        )
                        ui.notify(
                            message,
                            type="negative",
                        )
                        return
                    ui.notify(
                        "Orden enviada al cargador. Esperando confirmación.",
                        type="positive",
                    )
                    ui.navigate.to("/pages/carga_activa")
                finally:
                    start_button.enable()
                    command_lock.release()

            start_button = ui.button(
                "Confirmar e iniciar carga",
                icon="bolt",
                on_click=start_charge,
            ).props("no-caps").classes(
                "chargecore-primary bg-[#ff500a] text-white"
            )


def render_page(token: str, api_base_url: str):
    if not render_client_shell("mis_cargas", token, api_base_url):
        return
    sessions = api_get(
        "/client/me/sessions",
        token=token,
        base_url=api_base_url,
    )
    charge_requests = api_get(
        "/client/me/charge-requests",
        token=token,
        base_url=api_base_url,
    )
    vehicles = api_get(
        "/client/me/vehicles",
        token=token,
        base_url=api_base_url,
    )
    with page_container():
        with ui.row().classes("w-full flex-wrap items-center justify-between gap-4"):
            render_page_heading(
                "Mis cargas",
                "Inicia una carga y consulta el historial de tus sesiones.",
            )
            ui.button(
                "Actualizar",
                icon="refresh",
                on_click=ui.navigate.reload,
            ).props("flat no-caps").classes("text-orange-400")

        if isinstance(sessions, dict) and "error" in sessions:
            show_error(client_error(sessions, "No fue posible cargar tu historial."))
            return
        if not isinstance(sessions, list):
            show_error("La respuesta del historial no tiene el formato esperado.")
            return

        if isinstance(charge_requests, dict) and "error" in charge_requests:
            show_error(client_error(
                charge_requests,
                "No fue posible consultar las solicitudes de carga.",
            ))
        elif not isinstance(charge_requests, list):
            show_error("La respuesta de solicitudes de carga no tiene el formato esperado.")
        elif isinstance(vehicles, dict) and "error" in vehicles:
            show_error(client_error(vehicles, "No fue posible cargar tus vehículos."))
        elif not isinstance(vehicles, list):
            show_error("La respuesta de vehículos no tiene el formato esperado.")
        else:
            with ui.card().classes("chargecore-card w-full gap-4 p-5"):
                render_charge_requests(
                    charge_requests,
                    vehicles,
                    token,
                    api_base_url,
                )

        total_energy = sum(
            float(item["energia_consumida_kwh"])
            for item in sessions
            if isinstance(item.get("energia_consumida_kwh"), (int, float))
        )
        active_count = sum(
            str(item.get("estado") or "").lower() in {"activa", "iniciada"}
            for item in sessions
        )
        with ui.element("div").classes(
            "grid w-full grid-cols-1 gap-4 sm:grid-cols-3"
        ):
            for label, value, icon in (
                ("Total de cargas", str(len(sessions)), "receipt_long"),
                ("Energía consumida", f"{total_energy:.2f} kWh", "bolt"),
                ("Cargas activas", str(active_count), "battery_charging_full"),
            ):
                with ui.card().classes("chargecore-card w-full gap-2 p-5"):
                    ui.icon(icon).classes("text-2xl text-orange-400")
                    ui.label(label).classes("text-sm text-slate-400")
                    ui.label(value).classes("text-2xl font-bold text-white")

        ui.label("Historial").classes("text-xl font-bold text-white")
        if not sessions:
            ui.label(
                "Aún no tienes cargas registradas. Tus sesiones aparecerán aquí."
            ).classes("text-slate-400")
            return

        for session in sessions:
            state = str(session.get("estado") or "Sin estado")
            state_key = state.lower()
            active = state_key in {"activa", "iniciada"}
            status_class = (
                "chargecore-status-progress"
                if active
                else "chargecore-status-error"
                if state_key in {"error", "interrumpida", "fuera de servicio"}
                else "chargecore-status-success"
                if state_key in {"finalizada", "finalizado", "completed"}
                else "chargecore-status-warning"
                if state_key in {"preparando", "mantenimiento"}
                else "chargecore-status-neutral"
            )
            with ui.card().classes("chargecore-card w-full gap-4 p-5"):
                with ui.row().classes(
                    "w-full flex-wrap items-start justify-between gap-3"
                ):
                    with ui.column().classes("gap-1"):
                        ui.label(
                            session.get("cargador")
                            or session.get("charge_point_id")
                            or "Cargador"
                        ).classes("text-lg font-bold text-white")
                        ui.label(
                            f"{vehicle_label(session.get('vehiculo'))} · "
                            f"Conector {display_value(session.get('connector_id'))}"
                        ).classes("text-sm text-slate-400")
                    ui.badge(state.capitalize()).classes(status_class)
                with ui.element("div").classes(
                    "grid w-full grid-cols-2 gap-4 md:grid-cols-4"
                ):
                    values = (
                        ("Inicio", format_date(session.get("fecha_inicio"))),
                        ("Finalización", format_date(session.get("fecha_fin"))),
                        (
                            "Energía",
                            (
                                f"{float(session['energia_consumida_kwh']):.2f} kWh"
                                if isinstance(session.get("energia_consumida_kwh"), (int, float))
                                else "—"
                            ),
                        ),
                        (
                            "Duración",
                            (
                                f"{int(session['duracion_minutos'])} min"
                                if isinstance(session.get("duracion_minutos"), (int, float))
                                else "—"
                            ),
                        ),
                    )
                    for label, value in values:
                        with ui.column().classes("gap-1"):
                            ui.label(label).classes(
                                "text-xs uppercase tracking-wide text-slate-500"
                            )
                            ui.label(value).classes("text-sm text-slate-200")
                if session.get("beneficio_aplicado"):
                    ui.label(
                        f"Beneficio aplicado: {session['beneficio_aplicado']}"
                    ).classes("text-sm text-orange-300")
                if state_key in {"finalizada", "finalizado", "completed"}:
                    ui.button(
                        "Ver ticket",
                        icon="receipt_long",
                        on_click=lambda item=session: show_session_ticket(
                            item,
                            token,
                            api_base_url,
                        ),
                    ).props("flat no-caps").classes("text-orange-400")
                if active:
                    ui.button(
                        "Ver carga activa",
                        icon="arrow_forward",
                        on_click=lambda: ui.navigate.to("/pages/carga_activa"),
                    ).props("flat no-caps").classes("text-orange-400")
