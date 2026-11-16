from datetime import datetime

from nicegui import app, ui

from components.nicegui_layout import add_nicegui_styles, render_header, render_sidebar
from pages.client_page_utils import page_container, render_page_heading
from utils.api_client import api_get, api_post, api_put


def _error(result, fallback):
    ui.notify(
        result.get("error", fallback) if isinstance(result, dict) else fallback,
        type="negative",
    )


def _new_reward_dialog(token, api_base_url, item=None):
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card cc-reward-dialog w-full max-w-xl gap-4 p-6"
    ):
        ui.label("Editar recompensa" if item else "Nueva recompensa").classes(
            "text-xl font-bold"
        )
        name = ui.input("Nombre", value=(item or {}).get("nombre", "")).props(
            "outlined"
        ).classes("w-full")
        description = ui.textarea(
            "Descripción",
            value=(item or {}).get("descripcion", ""),
        ).props("outlined").classes("w-full")
        conditions = ui.textarea(
            "Requisitos y condiciones",
            value=(item or {}).get("condiciones", ""),
        ).props("outlined").classes("w-full")
        points = ui.number(
            "Puntos necesarios",
            value=(item or {}).get("puntos_requeridos", 100),
            min=1,
            step=1,
        ).props("outlined").classes("w-full")
        expires = ui.input(
            "Vigencia hasta (opcional)",
            value=str((item or {}).get("fecha_finalizacion", ""))[:10],
        ).props("outlined type=date").classes("w-full")

        def submit():
            if not name.value or not description.value or not points.value:
                ui.notify("Completa los campos obligatorios.", type="warning")
                return
            payload = {
                "nombre": name.value.strip(),
                "descripcion": description.value.strip(),
                "condiciones": (conditions.value or "").strip(),
                "puntos_requeridos": int(points.value),
                "fecha_finalizacion": expires.value or None,
            }
            if item:
                result = api_put(
                    f"/company-rewards/catalog/{item['_id']}",
                    json=payload,
                    token=token,
                    base_url=api_base_url,
                )
            else:
                payload["activa"] = True
                result = api_post(
                    "/company-rewards/catalog",
                    json=payload,
                    token=token,
                    base_url=api_base_url,
                )
            if isinstance(result, dict) and result.get("error"):
                _error(result, "No se pudo guardar la recompensa.")
                return
            ui.notify("Recompensa guardada.", type="positive")
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            ui.button("Guardar", on_click=submit).props("no-caps").classes(
                "chargecore-primary bg-[#ff500a] text-white"
            )
    dialog.open()


def _set_state(token, api_base_url, item):
    result = api_put(
        f"/company-rewards/catalog/{item['_id']}/state",
        json={"activa": not item.get("activa", False)},
        token=token,
        base_url=api_base_url,
    )
    if isinstance(result, dict) and result.get("error"):
        _error(result, "No se pudo cambiar el estado.")
        return
    ui.navigate.reload()


def _render_owner_client_rewards(token, api_base_url):
    catalog = api_get("/company-rewards/catalog", token=token, base_url=api_base_url)
    rule = api_get("/company-rewards/rules", token=token, base_url=api_base_url)
    balances = api_get("/company-rewards/balances", token=token, base_url=api_base_url)
    ledger = api_get("/company-rewards/ledger", token=token, base_url=api_base_url)
    redemptions = api_get(
        "/company-rewards/redemptions",
        token=token,
        base_url=api_base_url,
    )
    users = api_get("/usuarios/", token=token, base_url=api_base_url)
    if any(
        isinstance(item, dict) and item.get("error")
        for item in (catalog, rule, balances, ledger, redemptions, users)
    ):
        _error({"error": "No se pudieron cargar todos los datos del programa."}, "")
        return
    if not all(
        isinstance(value, list)
        for value in (catalog, balances, ledger, redemptions, users)
    ) or not isinstance(rule, (dict, type(None))):
        ui.notify("El servidor devolvió datos de recompensas inválidos.", type="negative")
        return

    from pages.nicegui_admin import (
        _render_reward_data_table,
        _render_reward_styles,
        _render_summary_cards,
    )

    _render_reward_styles()
    user_names = {
        str(user.get("_id")): user.get("nombre") or user.get("email") or "Cliente"
        for user in users
    }
    active_catalog = sum(bool(item.get("activa", True)) for item in catalog)
    month_ledger = [
        item
        for item in ledger
        if item.get("tipo") == "acumulacion"
        and _is_current_month(item.get("created_at"))
    ]
    decided_redemptions = [
        item for item in redemptions if item.get("estado") in {"aprobada", "rechazada"}
    ]
    approved_redemptions = sum(
        item.get("estado") == "aprobada" for item in decided_redemptions
    )
    redemption_rate = (
        round(approved_redemptions * 100 / len(decided_redemptions))
        if decided_redemptions
        else 0
    )
    _render_summary_cards([
        ("Recompensas activas", active_catalog, "Disponibles para clientes", "card_giftcard", "#8b5cf6"),
        ("Reglas activas", int(bool(rule and rule.get("activa"))), "Regla de acumulación", "sell", "#3b82f6"),
        ("Cargas con puntos este mes", len(month_ledger), "Puntos otorgados por cargas", "emoji_events", "#f59e0b"),
        ("Canjes aprobados", f"{redemption_rate}%", "Sobre solicitudes resueltas", "currency_exchange", "#22c55e"),
    ])

    with ui.row().classes("w-full items-center justify-between gap-4"):
        render_page_heading(
            "Sistema de recompensas",
            "Crea recompensas y reglas para los clientes que cargan en tu empresa.",
        )
        new_reward_button = ui.button(
            "Nueva recompensa",
            icon="add",
            on_click=lambda: _new_reward_dialog(token, api_base_url),
        ).props("no-caps").classes("chargecore-primary bg-[#ff500a] text-white")

    tabs = ui.tabs().classes("cc-reward-tabs w-full")
    with tabs:
        ui.tab("catalogo", label="Catálogo")
        ui.tab("reglas", label="Reglas")
        ui.tab("otorgadas", label="Otorgadas")
        ui.tab("ranking", label="Ranking")
    panel = ui.column().classes("w-full gap-4")

    def render_tab(tab_name):
        new_reward_button.set_visibility(tab_name == "catalogo")
        panel.clear()
        with panel:
            if tab_name == "catalogo":
                ui.label("Catálogo de recompensas").classes(
                    "text-lg font-bold text-white"
                )
                if not catalog:
                    ui.label("Todavía no hay recompensas.").classes("text-slate-400")
                with ui.element("div").classes("cc-reward-grid w-full"):
                    for item in catalog:
                        with ui.card().classes(
                            "cc-reward-card w-full gap-4 p-5"
                        ):
                            with ui.row().classes(
                                "w-full items-start justify-between gap-3"
                            ):
                                ui.label(item.get("nombre", "Recompensa")).classes(
                                    "cc-reward-card-title"
                                )
                                ui.badge(
                                    "Activa" if item.get("activa") else "Inactiva"
                                )
                            ui.label(
                                f"{item.get('puntos_requeridos', 0)} puntos"
                            ).classes("text-sm font-semibold text-orange-400")
                            ui.label(item.get("descripcion", "")).classes(
                                "text-sm text-slate-300"
                            )
                            ui.separator()
                            ui.label(
                                f"Condiciones: {item.get('condiciones') or 'Sin condiciones'}"
                            ).classes("text-sm text-slate-400")
                            expiry = item.get("fecha_finalizacion")
                            ui.label(
                                f"Vigencia: {str(expiry)[:10] if expiry else 'Sin vencimiento'}"
                            ).classes("text-xs text-slate-400")
                            with ui.row().classes("w-full justify-end gap-2"):
                                ui.button(
                                    "Editar",
                                    icon="edit",
                                    on_click=lambda row=item: _new_reward_dialog(
                                        token, api_base_url, row
                                    ),
                                ).props("flat no-caps").classes("text-orange-400")
                                ui.button(
                                    "Desactivar" if item.get("activa") else "Activar",
                                    icon="toggle_on" if item.get("activa") else "toggle_off",
                                    on_click=lambda row=item: _set_state(
                                        token, api_base_url, row
                                    ),
                                ).props("flat no-caps").classes("text-orange-400")
            elif tab_name == "reglas":
                _render_owner_rule(rule, token, api_base_url)
            elif tab_name == "otorgadas":
                _render_owner_activity(
                    ledger,
                    redemptions,
                    user_names,
                    token,
                    api_base_url,
                    _render_reward_data_table,
                )
            elif tab_name == "ranking":
                _render_owner_ranking(
                    balances,
                    ledger,
                    user_names,
                    _render_reward_data_table,
                )

    tabs.on_value_change(lambda event: render_tab(str(event.value)))
    render_tab("catalogo")


def _is_current_month(value):
    try:
        timestamp = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError):
        return False
    now = datetime.now(timestamp.tzinfo) if timestamp.tzinfo else datetime.now()
    return (timestamp.year, timestamp.month) == (now.year, now.month)


def _render_owner_rule(rule, token, api_base_url):
    rule = rule or {}
    with ui.card().classes("chargecore-card w-full max-w-3xl gap-4 p-5"):
        ui.label("Regla de acumulación").classes("text-lg font-bold text-white")
        ui.label(
            "Los puntos se asignan automáticamente al cliente cuando finaliza una carga "
            "que cumple la energía mínima."
        ).classes("text-sm text-slate-400")
        points = ui.number(
            "Puntos por carga finalizada",
            value=rule.get("puntos_por_carga", 10),
            min=1,
            step=1,
        ).props("outlined").classes("w-full")
        energy = ui.number(
            "Energía mínima (kWh)",
            value=rule.get("energia_minima_kwh", 0),
            min=0,
            step=0.1,
        ).props("outlined").classes("w-full")
        active = ui.checkbox("Regla activa", value=rule.get("activa", True))

        def save_rule():
            result = api_put(
                "/company-rewards/rules",
                json={
                    "puntos_por_carga": int(points.value),
                    "energia_minima_kwh": float(energy.value or 0),
                    "activa": active.value,
                },
                token=token,
                base_url=api_base_url,
            )
            if isinstance(result, dict) and result.get("error"):
                _error(result, "No se pudo guardar la regla.")
                return
            ui.notify("Regla actualizada.", type="positive")
            ui.navigate.reload()

        ui.button("Guardar regla", icon="save", on_click=save_rule).props(
            "no-caps"
        ).classes("chargecore-primary bg-[#ff500a] text-white")


def _render_owner_activity(
    ledger,
    redemptions,
    user_names,
    token,
    api_base_url,
    render_data_table,
):
    accruals = [
        (
            user_names.get(str(item.get("usuario_id")), "Cliente"),
            "Puntos por carga" if item.get("tipo") == "acumulacion" else item.get("tipo"),
            item.get("puntos", 0),
            str(item.get("created_at") or "")[:16],
        )
        for item in ledger
    ]
    with ui.card().classes("chargecore-card w-full gap-3 p-5"):
        ui.label("Puntos otorgados por carga").classes(
            "text-lg font-bold text-white"
        )
        render_data_table(
            ("Cliente", "Movimiento", "Puntos", "Fecha"),
            accruals,
            "1.4fr 1.4fr .7fr 1fr",
            "Aún no se han otorgado puntos.",
        )
    with ui.card().classes("chargecore-card w-full gap-3 p-5"):
        ui.label("Solicitudes de canje").classes("text-lg font-bold text-white")
        if not redemptions:
            ui.label("No hay solicitudes de canje.").classes("text-slate-400")
        for request in redemptions:
            snapshot = request.get("snapshot_recompensa", {})
            with ui.row().classes(
                "w-full flex-wrap items-center justify-between border-b border-slate-700 py-3"
            ):
                with ui.column().classes("gap-1"):
                    ui.label(
                        f"{snapshot.get('nombre', 'Recompensa')} · "
                        f"{request.get('puntos_requeridos', 0)} puntos"
                    ).classes("font-semibold")
                    ui.label(
                        f"{user_names.get(str(request.get('usuario_id')), 'Cliente')} · "
                        f"{request.get('estado', 'pendiente')}"
                    ).classes("text-sm text-slate-400")
                if request.get("estado") == "pendiente":
                    ui.button(
                        "Aprobar",
                        on_click=lambda row=request: _decide(
                            token,
                            api_base_url,
                            row,
                            "aprobar",
                        ),
                    ).props("no-caps").classes("text-green-400")
                    ui.button(
                        "Rechazar",
                        on_click=lambda row=request: _decide(
                            token,
                            api_base_url,
                            row,
                            "rechazar",
                        ),
                    ).props("flat no-caps").classes("text-red-400")


def _render_owner_ranking(balances, ledger, user_names, render_data_table):
    loads_by_user = {}
    for item in ledger:
        if item.get("tipo") == "acumulacion":
            user_id = str(item.get("usuario_id"))
            loads_by_user[user_id] = loads_by_user.get(user_id, 0) + 1
    ranked = sorted(
        balances,
        key=lambda item: int(item.get("puntos_disponibles", 0)),
        reverse=True,
    )
    rows = [
        (
            index,
            user_names.get(str(item.get("usuario_id")), "Cliente"),
            item.get("puntos_disponibles", 0),
            item.get("puntos_reservados", 0),
            loads_by_user.get(str(item.get("usuario_id")), 0),
        )
        for index, item in enumerate(ranked, start=1)
    ]
    with ui.card().classes("chargecore-card w-full gap-3 p-5"):
        ui.label("Clientes con más puntos").classes(
            "text-lg font-bold text-white"
        )
        render_data_table(
            ("#", "Cliente", "Disponibles", "Reservados", "Cargas con puntos"),
            rows,
            "50px 1.5fr 1fr 1fr 1.2fr",
            "Aún no hay clientes con puntos acumulados.",
        )


def _decide(token, api_base_url, redemption, decision):
    result = api_post(
        f"/company-rewards/redemptions/{redemption['_id']}/decision",
        json={"decision": decision},
        token=token,
        base_url=api_base_url,
    )
    if isinstance(result, dict) and result.get("error"):
        _error(result, "No se pudo actualizar la solicitud.")
        return
    ui.notify("Solicitud actualizada.", type="positive")
    ui.navigate.reload()


def render_page(page_key, token, api_base_url):
    user = app.storage.user.get("user") or {}
    role = user.get("rol")
    if role not in {"superadmin", "dueno_empresa"}:
        ui.notify("No tienes permiso para acceder a recompensas.", type="negative")
        return

    add_nicegui_styles()
    drawer = render_sidebar(user, "recompensas")
    render_header(
        drawer=drawer,
        on_scan=lambda: None,
        notifications=[],
        unread_count=0,
        mark_all_read=lambda: None,
        show_scan=False,
    )

    subtitle = (
        "Administra beneficios para empresas y recompensas por cargas."
        if role == "superadmin"
        else "Consulta beneficios asignados a tu empresa y administra recompensas para tus clientes."
    )
    with page_container():
        render_page_heading("Recompensas", subtitle)
        tabs = ui.tabs().classes("cc-reward-tabs w-full")
        if role == "superadmin":
            with tabs:
                ui.tab("company-benefits", label="Beneficios a empresas")
                ui.tab("charge-rewards", label="Recompensas por cargas")
            with ui.tab_panels(tabs, value="company-benefits").classes("w-full"):
                with ui.tab_panel("company-benefits"):
                    from pages.ChargeCore_Benefits import _render_admin

                    _render_admin(token, api_base_url)
                with ui.tab_panel("charge-rewards"):
                    from pages.nicegui_admin import _render_rewards_page

                    _render_rewards_page(token, api_base_url, user)
        else:
            with tabs:
                ui.tab("assigned-benefits", label="Beneficios recibidos")
                ui.tab("client-rewards", label="Recompensas para mis clientes")
            with ui.tab_panels(tabs, value="assigned-benefits").classes("w-full"):
                with ui.tab_panel("assigned-benefits"):
                    from pages.ChargeCore_Benefits import (
                        _render_owner as render_benefits,
                    )

                    render_benefits(token, api_base_url)
                with ui.tab_panel("client-rewards"):
                    _render_owner_client_rewards(token, api_base_url)
