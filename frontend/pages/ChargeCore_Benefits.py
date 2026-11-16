from datetime import date, timedelta
from uuid import uuid4

from nicegui import app, run, ui

from pages.client_page_utils import page_container, render_page_heading
from utils.api_client import api_get, api_post, api_put


CATEGORIES = {
    "promociones_dias_servicio": "Promociones y días de servicio",
    "creditos_descuentos": "Créditos o descuentos",
    "crecimiento_permanencia": "Crecimiento y permanencia",
    "reconocimientos_especiales": "Reconocimientos especiales",
}
BENEFIT_TYPES = {
    "dias_servicio": "Días de servicio",
    "credito": "Crédito",
    "descuento": "Descuento",
    "reconocimiento": "Reconocimiento",
}


def _requirements_summary(requirements):
    requirements = requirements or {}
    labels = (
        ("min_completed_sessions", "cargas finalizadas"),
        ("min_active_clients", "clientes activos"),
        ("min_active_chargers", "cargadores activos"),
    )
    return " · ".join(
        f"{requirements[key]} {label}"
        for key, label in labels
        if requirements.get(key, 0)
    ) or "Sin requisitos adicionales"


def _usage_conditions_summary(conditions):
    conditions = conditions or {}
    minimum = conditions.get("min_days_after_assignment", 0)
    maximum = conditions.get("max_days_after_assignment")
    if maximum is None:
        return f"Disponible desde el día {minimum} después de la asignación"
    return f"Utilizable entre los días {minimum} y {maximum} después de la asignación"


def _show_error(result, fallback):
    ui.notify(
        result.get("error", fallback)
        if isinstance(result, dict)
        else fallback,
        type="negative",
    )


def _benefit_editor(token, api_base_url, item=None):
    editing = item is not None
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-2xl gap-4 p-6"
    ):
        ui.label("Editar beneficio" if editing else "Crear beneficio").classes(
            "text-xl font-bold text-white"
        )
        name = ui.input("Nombre", value=(item or {}).get("nombre", "")).props(
            "outlined"
        ).classes("w-full")
        category = ui.select(
            CATEGORIES,
            value=(item or {}).get("categoria", "promociones_dias_servicio"),
            label="Categoría",
        ).props("outlined").classes("w-full")
        benefit_type = ui.select(
            BENEFIT_TYPES,
            value=(item or {}).get("tipo", "dias_servicio"),
            label="Tipo",
        ).props("outlined").classes("w-full")
        description = ui.textarea(
            "Descripción",
            value=(item or {}).get("descripcion", ""),
        ).props("outlined").classes("w-full")
        conditions = ui.textarea(
            "Condiciones",
            value=(item or {}).get("condiciones", ""),
        ).props("outlined").classes("w-full")
        requirements = (item or {}).get("requisitos") or {}
        usage_conditions = (item or {}).get("condiciones_uso") or {}
        min_sessions = ui.number(
            "Cargas finalizadas mínimas",
            value=requirements.get("min_completed_sessions", 0),
            min=0,
            step=1,
        ).props("outlined").classes("w-full")
        min_clients = ui.number(
            "Clientes activos mínimos",
            value=requirements.get("min_active_clients", 0),
            min=0,
            step=1,
        ).props("outlined").classes("w-full")
        min_chargers = ui.number(
            "Cargadores activos mínimos",
            value=requirements.get("min_active_chargers", 0),
            min=0,
            step=1,
        ).props("outlined").classes("w-full")
        min_days = ui.number(
            "Días mínimos antes de utilizar",
            value=usage_conditions.get("min_days_after_assignment", 0),
            min=0,
            step=1,
        ).props("outlined").classes("w-full")
        max_days = ui.number(
            "Días máximos para utilizar (opcional)",
            value=usage_conditions.get("max_days_after_assignment"),
            min=0,
            step=1,
        ).props("outlined").classes("w-full")
        value = ui.number(
            "Valor (días, crédito o porcentaje)",
            value=(item or {}).get("valor"),
            min=0.01,
        ).props("outlined").classes("w-full")
        start = ui.input(
            "Inicio",
            value=(item or {}).get("fecha_inicio", date.today().isoformat())[:16],
        ).props("outlined type=datetime-local").classes("w-full")
        raw_end = (item or {}).get("fecha_finalizacion")
        end_value = (
            raw_end[:16]
            if isinstance(raw_end, str) and raw_end
            else (
                ""
                if editing
                else (date.today() + timedelta(days=30)).isoformat()
            )
        )
        end = ui.input(
            "Fin",
            value=end_value,
        ).props("outlined type=datetime-local").classes("w-full")
        ui.label("Deja vacío para quitar el vencimiento al editar.").classes(
            "text-sm text-slate-400"
        )
        max_assignments = ui.number(
            "Máximo de asignaciones por empresa (opcional)",
            value=(item or {}).get("max_assignments_per_company"),
            min=1,
            step=1,
        ).props("outlined").classes("w-full")

        def submit():
            if (
                not name.value
                or not description.value
                or not start.value
                or (not editing and not end.value)
            ):
                ui.notify("Completa los campos obligatorios.", type="warning")
                return
            payload = {
                "nombre": name.value.strip(),
                "categoria": category.value,
                "tipo": benefit_type.value,
                "descripcion": description.value.strip(),
                "condiciones": (conditions.value or "").strip(),
                "requisitos": {
                    "min_completed_sessions": int(min_sessions.value or 0),
                    "min_active_clients": int(min_clients.value or 0),
                    "min_active_chargers": int(min_chargers.value or 0),
                },
                "condiciones_uso": {
                    "min_days_after_assignment": int(min_days.value or 0),
                    "max_days_after_assignment": (
                        int(max_days.value)
                        if max_days.value not in (None, "")
                        else None
                    ),
                },
                "fecha_inicio": start.value,
                "fecha_finalizacion": end.value or None,
                "valor": value.value if benefit_type.value != "reconocimiento" else None,
                "max_assignments_per_company": (
                    int(max_assignments.value)
                    if max_assignments.value not in (None, "")
                    else None
                ),
            }
            if editing:
                result = api_put(
                    f"/commercial-benefits/catalog/{item['_id']}",
                    json=payload,
                    token=token,
                    base_url=api_base_url,
                )
            else:
                payload["activa"] = True
                result = api_post(
                    "/commercial-benefits/catalog",
                    json=payload,
                    token=token,
                    base_url=api_base_url,
                )
            if isinstance(result, dict) and result.get("error"):
                _show_error(result, "No se pudo guardar el beneficio.")
                return
            ui.notify("Beneficio guardado.", type="positive")
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            ui.button("Guardar", on_click=submit).props(
                "no-caps"
            ).classes("chargecore-primary bg-[#ff500a] text-white")
    dialog.open()


def _assign_dialog(token, api_base_url, benefit):
    companies = api_get("/empresas/", token=token, base_url=api_base_url)
    if isinstance(companies, dict) and companies.get("error"):
        _show_error(companies, "No se pudieron cargar las empresas.")
        return
    options = {
        company["_id"]: company["nombre"]
        for company in companies
        if company.get("activo", True)
    }
    if not options:
        ui.notify("No hay empresas activas para asignar.", type="warning")
        return
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-lg gap-4 p-6"
    ):
        ui.label(f"Asignar: {benefit['nombre']}").classes("text-xl font-bold")
        companies_select = ui.select(
            options,
            multiple=True,
            with_input=True,
            label="Empresas",
        ).props("outlined use-chips").classes("w-full")
        assignment_request_id = str(uuid4())

        async def submit():
            if not companies_select.value:
                ui.notify("Selecciona al menos una empresa.", type="warning")
                return
            assign_button.disable()
            try:
                result = await run.io_bound(
                    api_post,
                    f"/commercial-benefits/catalog/{benefit['_id']}/assign",
                    json={
                        "empresa_ids": list(companies_select.value),
                        "assignment_request_id": assignment_request_id,
                    },
                    token=token,
                    base_url=api_base_url,
                )
            finally:
                assign_button.enable()
            if isinstance(result, dict) and result.get("error"):
                _show_error(result, "No se pudo asignar el beneficio.")
                return
            ui.notify("Beneficio asignado.", type="positive")
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            assign_button = ui.button("Asignar", on_click=submit).props(
                "no-caps"
            ).classes("chargecore-primary bg-[#ff500a] text-white")
    dialog.open()


def _render_admin(token, api_base_url):
    result = api_get(
        "/commercial-benefits/catalog",
        token=token,
        base_url=api_base_url,
    )
    assignments = api_get(
        "/commercial-benefits/assignments",
        token=token,
        base_url=api_base_url,
    )
    companies = api_get("/empresas/", token=token, base_url=api_base_url)
    with page_container():
        with ui.row().classes("w-full items-center justify-between gap-4"):
            render_page_heading(
                "Beneficios comerciales ChargeCore",
                "Administra ofertas comerciales separadas del programa de puntos de cada empresa.",
            )
            ui.button(
                "Crear beneficio",
                icon="add",
                on_click=lambda: _benefit_editor(token, api_base_url),
            ).props("no-caps").classes("chargecore-primary bg-[#ff500a] text-white")
        if isinstance(result, dict) and result.get("error"):
            _show_error(result, "No se pudieron cargar los beneficios.")
            return
        if any(
            isinstance(response, dict) and response.get("error")
            for response in (assignments, companies)
        ):
            _show_error({"error": "No se pudo cargar el historial comercial."}, "")
            return
        for benefit in result:
            end_date = benefit.get("fecha_finalizacion")
            displayed_end_date = (
                str(end_date)[:10] if end_date else "Sin vencimiento"
            )
            with ui.card().classes("chargecore-card w-full gap-3 p-5"):
                with ui.row().classes("w-full flex-wrap items-start justify-between"):
                    with ui.column().classes("gap-1"):
                        ui.label(benefit["nombre"]).classes("text-lg font-bold")
                        ui.label(CATEGORIES.get(benefit["categoria"], benefit["categoria"])).classes(
                            "text-sm text-slate-400"
                        )
                    ui.badge("Activo" if benefit.get("activa") else "Inactivo")
                ui.label(benefit.get("descripcion", "")).classes("text-slate-300")
                ui.label(
                    f"Tipo: {BENEFIT_TYPES.get(benefit['tipo'], benefit['tipo'])} · "
                    f"Valor: {benefit.get('valor') or '—'}"
                ).classes("text-sm text-slate-400")
                ui.label(
                    f"Vigencia: {str(benefit.get('fecha_inicio', ''))[:10]} – "
                    f"{displayed_end_date}"
                ).classes("text-sm text-slate-400")
                ui.label(
                    "Requisitos: "
                    + _requirements_summary(benefit.get("requisitos"))
                ).classes("text-sm text-slate-400")
                ui.label(
                    "Uso: "
                    + _usage_conditions_summary(benefit.get("condiciones_uso"))
                ).classes("text-sm text-slate-400")
                with ui.row().classes("gap-2"):
                    ui.button(
                        "Editar",
                        icon="edit",
                        on_click=lambda row=benefit: _benefit_editor(
                            token, api_base_url, row
                        ),
                    ).props("flat no-caps")
                    _render_admin_benefit_actions(
                        token,
                        api_base_url,
                        benefit,
                    )

        company_names = {
            company["_id"]: company.get("nombre", company["_id"])
            for company in companies
        }
        with ui.card().classes("chargecore-card w-full gap-4 p-5"):
            ui.label("Asignaciones e historial").classes("text-lg font-bold")
            if not assignments:
                ui.label("Aún no hay beneficios asignados.").classes(
                    "text-slate-400"
                )
            for assignment in assignments:
                snapshot = assignment.get("snapshot_benefit", {})
                with ui.expansion(
                    f"{snapshot.get('nombre', 'Beneficio')} · "
                    f"{company_names.get(assignment.get('empresa_id'), assignment.get('empresa_id'))} · "
                    f"{assignment.get('estado', 'disponible').capitalize()}"
                ).classes("w-full"):
                    ui.label(
                        f"Asignado: {str(assignment.get('assigned_at', ''))[:10]} · "
                        f"Vence: {str(assignment.get('expires_at', ''))[:10]}"
                    ).classes("text-sm text-slate-400")
                    for movement in assignment.get("movimientos", []):
                        ui.label(
                            f"{movement.get('tipo')} · {movement.get('created_at')}"
                        ).classes("text-sm text-slate-400")


def _set_benefit_state(token, api_base_url, benefit):
    result = api_put(
        f"/commercial-benefits/catalog/{benefit['_id']}/state",
        json={"activa": not benefit.get("activa", False)},
        token=token,
        base_url=api_base_url,
    )
    if isinstance(result, dict) and result.get("error"):
        _show_error(result, "No se pudo actualizar el estado.")
        return
    ui.navigate.reload()


def _render_admin_benefit_actions(token, api_base_url, benefit):
    ui.button(
        "Asignar",
        icon="business",
        on_click=lambda row=benefit: _assign_dialog(token, api_base_url, row),
    ).props("flat no-caps")
    ui.button(
        "Desactivar" if benefit.get("activa") else "Activar",
        icon="toggle_off" if benefit.get("activa") else "toggle_on",
        on_click=lambda row=benefit: _set_benefit_state(
            token, api_base_url, row
        ),
    ).props("flat no-caps")


def _render_owner(token, api_base_url):
    result = api_get("/commercial-benefits/mine", token=token, base_url=api_base_url)
    with page_container():
        render_page_heading(
            "Mis beneficios ChargeCore",
            "Consulta los beneficios comerciales asignados a tu empresa, sus condiciones y vigencia.",
        )
        if isinstance(result, dict) and result.get("error"):
            _show_error(result, "No se pudieron cargar tus beneficios.")
            return
        if not result:
            ui.label("Aún no hay beneficios asignados a tu empresa.").classes(
                "text-slate-400"
            )
            return
        for assignment in result:
            benefit = assignment.get("snapshot_benefit", {})
            state = assignment.get("estado", "disponible")
            expires_at = assignment.get("expires_at")
            displayed_expiration = (
                str(expires_at)[:10] if expires_at else "Sin vencimiento"
            )
            with ui.card().classes("chargecore-card w-full gap-3 p-5"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(benefit.get("nombre", "Beneficio ChargeCore")).classes(
                        "text-lg font-bold"
                    )
                    ui.badge(state.capitalize())
                ui.label(benefit.get("descripcion", "")).classes("text-slate-300")
                ui.label(
                    f"Condiciones: {benefit.get('condiciones') or 'Sin condiciones adicionales'}"
                ).classes("text-sm text-slate-400")
                ui.label(
                    "Requisitos: "
                    + _requirements_summary(
                        assignment.get("requisitos_snapshot")
                        or benefit.get("requisitos")
                    )
                ).classes("text-sm text-slate-400")
                ui.label(
                    "Uso: "
                    + _usage_conditions_summary(
                        assignment.get("condiciones_uso_snapshot")
                        or benefit.get("condiciones_uso")
                    )
                ).classes("text-sm text-slate-400")
                ui.label(
                    f"Vence: {displayed_expiration}"
                ).classes("text-sm text-slate-400")
                if state == "disponible":
                    ui.button(
                        "Registrar uso",
                        icon="redeem",
                        on_click=lambda row=assignment: _use_benefit(
                            token, api_base_url, row
                        ),
                    ).props("no-caps").classes(
                        "chargecore-primary bg-[#ff500a] text-white"
                    )
                with ui.expansion("Historial").classes("w-full"):
                    for movement in assignment.get("movimientos", []):
                        ui.label(
                            f"{movement.get('tipo')} · {movement.get('created_at')}"
                        ).classes("text-sm text-slate-400")


def _use_benefit(token, api_base_url, assignment):
    result = api_post(
        f"/commercial-benefits/mine/{assignment['_id']}/use",
        json={},
        token=token,
        base_url=api_base_url,
    )
    if isinstance(result, dict) and result.get("error"):
        _show_error(result, "No se pudo registrar el uso.")
        return
    ui.notify("Uso registrado.", type="positive")
    ui.navigate.reload()


def render_page(page_key, token, api_base_url):
    from components.nicegui_layout import add_nicegui_styles, render_header, render_sidebar

    user = app.storage.user.get("user") or {}
    add_nicegui_styles()
    drawer = render_sidebar(user, page_key)
    render_header(
        drawer=drawer,
        on_scan=lambda: None,
        notifications=[],
        unread_count=0,
        mark_all_read=lambda: None,
        show_scan=False,
    )
    if page_key == "beneficios_chargecore":
        _render_admin(token, api_base_url)
    else:
        _render_owner(token, api_base_url)
