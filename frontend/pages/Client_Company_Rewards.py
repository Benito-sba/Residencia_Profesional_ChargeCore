from nicegui import app, ui

from components.nicegui_layout import add_nicegui_styles, render_header, render_sidebar
from pages.client_page_utils import page_container, render_page_heading
from utils.api_client import api_get, api_post


def render_page(token: str, api_base_url: str):
    user = app.storage.user.get("user") or {}
    add_nicegui_styles()
    drawer = render_sidebar(user, "mis_recompensas_empresa")
    render_header(
        drawer=drawer,
        on_scan=lambda: None,
        notifications=[],
        unread_count=0,
        mark_all_read=lambda: None,
        show_scan=False,
    )
    result = api_get(
        "/client/me/company-rewards",
        token=token,
        base_url=api_base_url,
    )
    with page_container():
        render_page_heading(
            "Mis puntos y recompensas",
            "Consulta tus puntos, el catálogo de tu empresa y tus solicitudes de canje.",
        )
        if isinstance(result, dict) and result.get("error"):
            ui.notify(result["error"], type="negative")
            return
        with ui.element("div").classes("grid w-full grid-cols-1 gap-3 sm:grid-cols-2"):
            for label, amount in (
                ("Puntos disponibles", result.get("puntos_disponibles", 0)),
                ("Puntos reservados", result.get("puntos_reservados", 0)),
            ):
                with ui.card().classes("chargecore-card gap-2 p-5"):
                    ui.label(label).classes("text-sm text-slate-400")
                    ui.label(str(amount)).classes("text-3xl font-bold text-orange-400")

        with ui.card().classes("chargecore-card w-full gap-4 p-5"):
            ui.label("Catálogo de mi empresa").classes("text-lg font-bold")
            catalog = result.get("catalogo", [])
            if not catalog:
                ui.label("No hay recompensas disponibles por ahora.").classes(
                    "text-slate-400"
                )
            for reward in catalog:
                with ui.row().classes(
                    "w-full flex-wrap items-center justify-between border-b border-slate-700 py-3"
                ):
                    with ui.column().classes("gap-1"):
                        ui.label(reward["nombre"]).classes("font-semibold")
                        ui.label(
                            f"{reward['puntos_requeridos']} puntos · "
                            f"{reward.get('descripcion', '')}"
                        ).classes("text-sm text-slate-300")
                        if reward.get("condiciones"):
                            ui.label(reward["condiciones"]).classes(
                                "text-xs text-slate-400"
                            )
                    if result.get("puntos_disponibles", 0) >= reward["puntos_requeridos"]:
                        ui.button(
                            "Solicitar canje",
                            icon="redeem",
                            on_click=lambda item=reward: _request_redemption(
                                token, api_base_url, item
                            ),
                        ).props("no-caps").classes(
                            "chargecore-primary bg-[#ff500a] text-white"
                        )

        with ui.card().classes("chargecore-card w-full gap-4 p-5"):
            ui.label("Mis solicitudes").classes("text-lg font-bold")
            requests = result.get("solicitudes", [])
            if not requests:
                ui.label("Todavía no has solicitado canjes.").classes(
                    "text-slate-400"
                )
            for request in requests:
                snapshot = request.get("snapshot_recompensa", {})
                ui.label(
                    f"{snapshot.get('nombre', 'Recompensa')} · "
                    f"{request.get('puntos_requeridos')} puntos · "
                    f"{request.get('estado')}"
                ).classes("text-sm text-slate-300")

        with ui.card().classes("chargecore-card w-full gap-4 p-5"):
            ui.label("Historial de movimientos").classes("text-lg font-bold")
            movements = result.get("movimientos", [])
            if not movements:
                ui.label("Aún no hay movimientos de puntos.").classes(
                    "text-slate-400"
                )
            for movement in movements:
                ui.label(
                    f"{movement.get('tipo')} · {movement.get('puntos')} puntos · "
                    f"{movement.get('created_at')}"
                ).classes("text-sm text-slate-400")


def _request_redemption(token, api_base_url, reward):
    result = api_post(
        "/client/me/company-rewards/redemptions",
        json={"recompensa_id": reward["_id"]},
        token=token,
        base_url=api_base_url,
    )
    if isinstance(result, dict) and result.get("error"):
        ui.notify(result["error"], type="negative")
        return
    ui.notify("Solicitud de canje enviada.", type="positive")
    ui.navigate.reload()
