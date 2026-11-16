import math

from nicegui import run, ui

from pages.client_page_utils import (
    client_error,
    format_date,
    page_container,
    render_client_shell,
    render_page_heading,
    show_error,
)
from utils.api_client import api_get, api_post


def render_page(token: str, api_base_url: str):
    if not render_client_shell("mis_recompensas", token, api_base_url):
        return
    result = api_get(
        "/client/me/company-rewards",
        token=token,
        base_url=api_base_url,
    )
    with page_container():
        with ui.row().classes("w-full flex-wrap items-center justify-between gap-4"):
            render_page_heading(
                "Mis recompensas",
                "Consulta y canjea las recompensas que obtienes al cargar en tu empresa.",
            )
            ui.button(
                "Actualizar",
                icon="refresh",
                on_click=ui.navigate.reload,
            ).props("flat no-caps").classes("text-orange-400")

        if isinstance(result, dict) and "error" in result:
            show_error(client_error(result, "No fue posible cargar tus recompensas."))
            return
        if not isinstance(result, dict):
            show_error("La respuesta de recompensas no tiene el formato esperado.")
            return

        catalog = result.get("catalogo")
        requests = result.get("solicitudes")
        points = result.get("puntos_disponibles")
        points_per_charge = result.get("puntos_por_carga")
        if (
            not isinstance(catalog, list)
            or not isinstance(requests, list)
            or not isinstance(points, int)
            or points < 0
            or (
                points_per_charge is not None
                and (
                    not isinstance(points_per_charge, int)
                    or points_per_charge <= 0
                )
            )
        ):
            show_error("La respuesta de recompensas no tiene el formato esperado.")
            return

        if any(
            not isinstance(reward, dict)
            or not isinstance(reward.get("puntos_requeridos"), int)
            or reward["puntos_requeridos"] < 1
            for reward in catalog
        ):
            show_error("El catálogo de recompensas contiene datos no válidos.")
            return

        approved = [
            item for item in requests if item.get("estado") == "aprobada"
        ]
        pending = [
            item for item in requests if item.get("estado") == "pendiente"
        ]
        pending_ids = {
            str(item.get("recompensa_id"))
            for item in pending
            if item.get("recompensa_id")
        }
        available = [
            reward
            for reward in catalog
            if reward["puntos_requeridos"] <= points
            and str(reward.get("_id")) not in pending_ids
        ]

        _render_summary(len(catalog), len(available), len(approved))
        _render_next_reward(catalog, points, points_per_charge)
        _render_catalog(catalog, pending_ids, points, token, api_base_url)
        _render_received_rewards(approved)
        _render_requests(pending)


def _render_summary(total, available, used):
    cards = (
        ("TOTAL", total, "Opciones de recompensa", "🎁", True),
        ("DISPONIBLES", available, "Listas para canjear", "✅", False),
        ("RECIBIDAS", used, "Canjes aprobados", "🏷️", False),
    )
    with ui.element("div").classes(
        "grid w-full grid-cols-1 gap-3 md:grid-cols-3"
    ):
        for label, amount, description, icon, highlighted in cards:
            card_classes = (
                "chargecore-card w-full gap-3 border-orange-400 bg-[#f4511e] p-5"
                if highlighted
                else "chargecore-card w-full gap-3 p-5"
            )
            with ui.card().classes(card_classes):
                with ui.element("div").classes(
                    "flex h-12 w-12 items-center justify-center rounded-xl bg-black/10 text-2xl"
                ):
                    ui.label(icon)
                ui.label(label).classes("text-sm font-bold text-white/80")
                ui.label(str(amount)).classes("text-4xl font-bold text-white")
                ui.label(description).classes("text-sm text-white/80")


def _render_next_reward(catalog, points, points_per_charge):
    with ui.card().classes("chargecore-card w-full gap-3 p-5"):
        if not catalog:
            ui.label("Tu próxima recompensa").classes(
                "text-lg font-bold text-white"
            )
            ui.label(
                "Tu empresa aún no tiene recompensas disponibles."
            ).classes("text-slate-400")
            return

        reward = min(catalog, key=lambda item: item["puntos_requeridos"])
        target = reward["puntos_requeridos"]
        missing_points = max(target - points, 0)
        progress = min(points / target, 1)
        with ui.row().classes("w-full flex-wrap items-center justify-between gap-3"):
            ui.label("Tu próxima recompensa").classes(
                "text-lg font-bold text-white"
            )
            ui.label(f"{min(points, target)} de {target} puntos").classes(
                "text-slate-300"
            )
        ui.linear_progress(progress, color="orange").props("rounded").classes(
            "w-full"
        )
        if missing_points == 0:
            progress_text = f"Ya puedes solicitar: {reward['nombre']}"
        elif points_per_charge:
            charges = math.ceil(missing_points / points_per_charge)
            progress_text = (
                f"Te faltan {charges} cargas para canjear: {reward['nombre']}"
            )
        else:
            progress_text = (
                f"Te faltan {missing_points} puntos para canjear: {reward['nombre']}"
            )
        ui.label(progress_text).classes("text-slate-300")


def _render_catalog(catalog, pending_ids, points, token, api_base_url):
    with ui.card().classes("chargecore-card w-full gap-4 p-5"):
        ui.label("Recompensas disponibles").classes(
            "text-lg font-bold text-white"
        )
        if not catalog:
            ui.label("Tu empresa aún no tiene recompensas disponibles.").classes(
                "text-slate-400"
            )
            return

        for reward in sorted(
            catalog, key=lambda item: item["puntos_requeridos"]
        ):
            reward_id = str(reward.get("_id"))
            required_points = reward["puntos_requeridos"]
            missing_points = max(required_points - points, 0)
            with ui.row().classes(
                "w-full flex-wrap items-center justify-between gap-4 border-b border-slate-700 py-3"
            ):
                with ui.column().classes("min-w-0 gap-1"):
                    ui.label(reward.get("nombre") or "Recompensa").classes(
                        "font-semibold text-white"
                    )
                    ui.label(
                        f"{required_points} puntos · "
                        f"{reward.get('descripcion', '')}"
                    ).classes("text-sm text-slate-300")
                    if reward.get("condiciones"):
                        ui.label(reward["condiciones"]).classes(
                            "text-xs text-slate-400"
                        )
                if reward_id in pending_ids:
                    ui.badge("Solicitud pendiente").classes(
                        "bg-amber-950 text-amber-200"
                    )
                elif missing_points == 0:
                    button = ui.button("Solicitar canje", icon="redeem").props(
                        "no-caps"
                    ).classes("chargecore-primary bg-[#ff500a] text-white")

                    async def request(item=reward, control=button):
                        await _request_redemption(
                            token, api_base_url, item, control
                        )

                    button.on_click(request)
                else:
                    ui.label(f"Te faltan {missing_points} puntos").classes(
                        "text-sm text-slate-400"
                    )


def _render_received_rewards(rewards):
    with ui.card().classes("chargecore-card w-full gap-4 p-5"):
        ui.label("Mis recompensas recibidas").classes(
            "text-lg font-bold text-white"
        )
        if not rewards:
            ui.label(
                "Aún no has recibido recompensas. Cuando aprueben un canje, aparecerá aquí."
            ).classes("text-slate-400")
            return

        for request in rewards:
            snapshot = request.get("snapshot_recompensa") or {}
            with ui.card().classes(
                "w-full gap-3 border border-slate-700 bg-[#242124] p-4"
            ):
                with ui.row().classes(
                    "w-full flex-wrap items-start justify-between gap-3"
                ):
                    with ui.column().classes("gap-1"):
                        ui.label(
                            snapshot.get("nombre") or "Recompensa"
                        ).classes("text-lg font-bold text-white")
                        ui.label(
                            f"{request.get('puntos_requeridos', 0)} puntos"
                        ).classes("text-sm text-slate-400")
                    ui.badge("Recibida").classes(
                        "bg-green-950 text-green-200"
                    )
                if snapshot.get("descripcion"):
                    ui.label(snapshot["descripcion"]).classes("text-slate-300")
                if snapshot.get("condiciones"):
                    ui.label(snapshot["condiciones"]).classes("text-sm text-slate-400")
                ui.label(
                    f"Recibida el {format_date(request.get('decided_at') or request.get('created_at'))}"
                ).classes("text-xs text-slate-500")


def _render_requests(requests):
    if not requests:
        return
    with ui.card().classes("chargecore-card w-full gap-3 p-5"):
        ui.label("Solicitudes pendientes").classes(
            "text-lg font-bold text-white"
        )
        for request in requests:
            snapshot = request.get("snapshot_recompensa") or {}
            ui.label(
                f"{snapshot.get('nombre', 'Recompensa')} · "
                f"{request.get('puntos_requeridos', 0)} puntos · En revisión"
            ).classes("text-sm text-slate-300")


async def _request_redemption(token, api_base_url, reward, button):
    button.disable()
    try:
        result = await run.io_bound(
            api_post,
            "/client/me/company-rewards/redemptions",
            json={"recompensa_id": reward["_id"]},
            token=token,
            base_url=api_base_url,
        )
    finally:
        button.enable()
    if isinstance(result, dict) and result.get("error"):
        show_error(client_error(result, "No se pudo solicitar el canje."))
        return
    ui.notify("Solicitud de canje enviada.", type="positive")
    ui.navigate.reload()
