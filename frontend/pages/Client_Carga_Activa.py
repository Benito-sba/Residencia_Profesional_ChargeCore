from datetime import datetime, timezone
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
    api_get,
    api_post,
)


ACTIVE_SESSION_REFRESH_INTERVAL_SECONDS = 15
PENDING_REWARD_SELECTIONS_KEY = "pending_charge_reward_selections"


def pending_reward_selection(storage, session_id, default):
    selections = storage.get(PENDING_REWARD_SELECTIONS_KEY, {})
    pending = selections.get(str(session_id), {})
    if isinstance(pending, dict) and "reward_id" in pending:
        return pending["reward_id"]
    return default


def remember_pending_reward_selection(storage, session_id, reward_id):
    selections = dict(storage.get(PENDING_REWARD_SELECTIONS_KEY, {}))
    selections[str(session_id)] = {"reward_id": reward_id}
    storage[PENDING_REWARD_SELECTIONS_KEY] = selections


def clear_pending_reward_selection(storage, session_id):
    selections = dict(storage.get(PENDING_REWARD_SELECTIONS_KEY, {}))
    selections.pop(str(session_id), None)
    storage[PENDING_REWARD_SELECTIONS_KEY] = selections


def _schedule_active_session_auto_refresh():
    ui.timer(
        ACTIVE_SESSION_REFRESH_INTERVAL_SECONDS,
        ui.navigate.reload,
        immediate=False,
    )


def parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def elapsed_time(value):
    started_at = parse_datetime(value)
    if started_at is None:
        return "—"
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    seconds = max(
        0,
        int((datetime.now(timezone.utc) - started_at).total_seconds()),
    )
    hours, remainder = divmod(seconds // 60, 60)
    if hours:
        return f"{hours} h {remainder} min"
    return f"{remainder} min"


def vehicle_label(vehicle):
    if not isinstance(vehicle, dict):
        return "No especificado"
    name = " ".join(
        str(value)
        for value in (vehicle.get("marca"), vehicle.get("modelo"))
        if value
    )
    plate = vehicle.get("placa")
    return f"{name} · {plate}" if name and plate else name or plate or "No especificado"


def render_page(token: str, api_base_url: str):
    if not render_client_shell("carga_activa", token, api_base_url):
        return
    _schedule_active_session_auto_refresh()
    result = api_get(
        "/client/me/active-session",
        token=token,
        base_url=api_base_url,
    )
    rewards_result = api_get(
        "/client/me/rewards",
        token=token,
        base_url=api_base_url,
    )
    with page_container():
        with ui.row().classes("w-full flex-wrap items-center justify-between gap-4"):
            render_page_heading(
                "Carga activa",
                "Consulta el estado de tu carga y controla la sesión.",
            )
            ui.button(
                "Actualizar",
                icon="refresh",
                on_click=ui.navigate.reload,
            ).props("flat no-caps").classes("text-orange-400")

        if isinstance(result, dict) and "error" in result:
            show_error(client_error(result, "No fue posible consultar tu carga activa."))
            return
        if not isinstance(result, dict):
            show_error("La respuesta de carga activa no tiene el formato esperado.")
            return
        session = result.get("session")
        if not result.get("active") or not isinstance(session, dict):
            with ui.card().classes("chargecore-card w-full gap-3 p-6"):
                ui.icon("battery_charging_full").classes("text-4xl text-orange-400")
                ui.label("No tienes una carga activa").classes(
                    "text-xl font-bold text-white"
                )
                ui.label(
                    "Cuando confirmes una carga desde Mis Cargas, podrás consultar aquí su avance."
                ).classes("text-slate-400")
                ui.button(
                    "Ir a Mis Cargas",
                    icon="bolt",
                    on_click=lambda: ui.navigate.to("/pages/mis_cargas"),
                ).props("no-caps").classes(
                    "chargecore-primary bg-[#ff500a] text-white"
                )
            return

        with ui.card().classes("chargecore-card w-full gap-5 p-5 sm:p-7"):
            with ui.row().classes("w-full flex-wrap items-center justify-between gap-3"):
                ui.label("Información de la carga").classes(
                    "text-xl font-bold text-white"
                )
                state = str(session.get("estado") or "Sin estado")
                state_key = state.lower()
                state_class = (
                    "chargecore-status-progress"
                    if state_key in {"activa", "iniciada"}
                    else "chargecore-status-error"
                    if state_key in {"error", "interrumpida", "fuera de servicio"}
                    else "chargecore-status-success"
                    if state_key in {"finalizada", "finalizado", "completed"}
                    else "chargecore-status-warning"
                    if state_key in {"preparando", "mantenimiento"}
                    else "chargecore-status-neutral"
                )
                ui.badge(state.capitalize()).classes(state_class)

            details = (
                ("Cargador", session.get("cargador") or session.get("charge_point_id")),
                ("Conector", session.get("connector_id")),
                ("Vehículo", vehicle_label(session.get("vehiculo"))),
                ("Inicio", format_date(session.get("fecha_inicio"))),
            )
            with ui.element("div").classes(
                "grid w-full grid-cols-1 gap-4 sm:grid-cols-2"
            ):
                for label, value in details:
                    with ui.column().classes("gap-1"):
                        ui.label(label).classes(
                            "text-xs uppercase tracking-wide text-slate-500"
                        )
                        ui.label(display_value(value)).classes(
                            "text-base font-semibold text-slate-100"
                        )

            with ui.element("div").classes(
                "grid w-full grid-cols-1 gap-4 sm:grid-cols-3"
            ):
                metrics = (
                    (
                        "Energía consumida",
                        (
                            f"{float(session['energia_consumida_kwh']):.2f} kWh"
                            if isinstance(session.get("energia_consumida_kwh"), (int, float))
                            else "—"
                        ),
                        "bolt",
                    ),
                    (
                        "Potencia actual",
                        (
                            f"{float(session['potencia_actual_kw']):.2f} kW"
                            if isinstance(session.get("potencia_actual_kw"), (int, float))
                            else "—"
                        ),
                        "electric_bolt",
                    ),
                    (
                        "Tiempo transcurrido",
                        elapsed_time(session.get("fecha_inicio")),
                        "schedule",
                    ),
                )
                for label, value, icon in metrics:
                    with ui.card().classes("chargecore-card gap-2 p-4"):
                        ui.icon(icon).classes("text-xl text-orange-400")
                        ui.label(label).classes("text-sm text-slate-400")
                        ui.label(value).classes("text-xl font-bold text-white")

            ui.label(
                f"Última actualización: {format_date(session.get('ultima_actualizacion'))}"
            ).classes("text-xs text-slate-500")

            if isinstance(rewards_result, dict) and "error" in rewards_result:
                ui.label(
                    "No se pudieron consultar las recompensas. Aún puedes detener la carga."
                ).classes("text-amber-300")
            elif not isinstance(rewards_result, dict) or not isinstance(
                rewards_result.get("recompensas"), list
            ):
                ui.label(
                    "La respuesta de recompensas no tiene el formato esperado."
                ).classes("text-amber-300")
            elif not session.get("stop_requested"):
                rewards = [
                    reward
                    for reward in rewards_result["recompensas"]
                    if reward.get("estado") == "Disponible"
                    or reward.get("id") == session.get("selected_reward_id")
                ]
                ui.separator().classes("bg-[#393633]")
                ui.label("Recompensa para esta sesión").classes(
                    "text-lg font-bold text-white"
                )
                ui.label(
                    "La selección se registra en el historial de la sesión y no modifica importes de cobro."
                ).classes("text-sm text-slate-400")
                reward_options = {
                    reward["id"]: (
                        f"{reward.get('nombre') or 'Recompensa'}"
                        + (
                            f" — {reward['empresa']}"
                            if reward.get("empresa")
                            else ""
                        )
                    )
                    for reward in rewards
                    if reward.get("id")
                }
                reward_options = {None: "No aplicar recompensa", **reward_options}
                selected_id = pending_reward_selection(
                    app.storage.user,
                    session["id"],
                    session.get("selected_reward_id"),
                )
                if selected_id not in reward_options:
                    selected_id = session.get("selected_reward_id")
                reward_select = ui.select(
                    options=reward_options,
                    value=selected_id,
                    label="Selecciona una recompensa disponible",
                    clearable=True,
                ).props("outlined").classes("w-full")
                reward_select.on_value_change(
                    lambda _: remember_pending_reward_selection(
                        app.storage.user,
                        session["id"],
                        reward_select.value,
                    )
                )

                def save_reward():
                    selection = api_post(
                        f"/client/me/sessions/{session['id']}/reward",
                        json={"reward_id": reward_select.value},
                        token=token,
                        base_url=api_base_url,
                    )
                    if isinstance(selection, dict) and "error" in selection:
                        ui.notify(
                            client_error(selection, "No se pudo guardar la selección."),
                            type="negative",
                        )
                        return
                    ui.notify(
                        selection.get("mensaje", "Selección guardada."),
                        type="positive",
                    )
                    clear_pending_reward_selection(
                        app.storage.user,
                        session["id"],
                    )
                    ui.navigate.reload()

                ui.button(
                    "Guardar selección",
                    icon="save",
                    on_click=save_reward,
                ).props("no-caps").classes(
                    "chargecore-primary bg-[#ff500a] text-white"
                )

            ui.separator().classes("bg-[#393633]")
            if session.get("stop_requested"):
                ui.label(
                    "La orden de detención fue enviada. Espera a que el cargador cierre la sesión."
                ).classes("text-amber-300")
            else:
                command_lock = Lock()

                def stop_charge():
                    if not command_lock.acquire(blocking=False):
                        return
                    stop_button.disable()
                    try:
                        stop_result = api_post(
                            f"/client/me/sessions/{session['id']}/stop",
                            token=token,
                            base_url=api_base_url,
                            timeout=OCPP_COMMAND_TIMEOUT_SECONDS,
                        )
                        if isinstance(stop_result, dict) and "error" in stop_result:
                            message = (
                                stop_result.get("error")
                                if stop_result.get("_status_code") == 504
                                else client_error(
                                    stop_result,
                                    "No se pudo detener la carga.",
                                )
                            )
                            ui.notify(message, type="negative")
                            return
                        ui.notify(
                            "Orden de detención enviada al cargador.",
                            type="positive",
                        )
                        ui.navigate.reload()
                    finally:
                        stop_button.enable()
                        command_lock.release()

                stop_button = ui.button(
                    "Detener carga",
                    icon="stop_circle",
                    on_click=stop_charge,
                ).props("no-caps").classes("bg-red-700 text-white")

        ui.label(
            "El estado, la potencia y la energía se actualizan automáticamente cada 15 segundos."
        ).classes("text-xs text-slate-500")
