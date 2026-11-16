import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from urllib.parse import quote, urlsplit

from nicegui import app, ui

from components.nicegui_layout import add_nicegui_styles, render_header, render_sidebar
from pages.client_page_utils import page_container, render_page_heading, show_error
from utils.api_client import (
    OCPP_COMMAND_TIMEOUT_SECONDS,
    api_delete,
    api_download,
    api_get,
    api_post,
    api_put,
)


PAGE_CONFIG = {
    "dashboard": ("Panel de control", "/dashboard/kpi", "Indicadores operativos y actividad reciente."),
    "empresas": ("Empresas", "/empresas/", "Gestión de las empresas registradas."),
    "empresa": ("Mi empresa", "/empresas/me", "Información de tu empresa."),
    "operaciones_qr": (
        "Operaciones QR",
        "/empresas/qr-operations",
        "Supervisa las solicitudes QR de carga.",
    ),
    "usuarios": ("Usuarios", "/usuarios/", "Cuentas registradas en ChargeCore."),
    "vehiculos": ("Vehículos", "/vehiculos/", "Vehículos registrados."),
    "tarjetas": ("Tarjetas NFC", "/nfc-cards/", "Tarjetas y su estado de activación."),
    "cargadores": ("Cargadores", "/cargadores/", "Estaciones de carga registradas."),
    "sesiones": (
        "Historial de sesiones de carga",
        "/sesiones/",
        "Monitorea y analiza todas las sesiones de carga realizadas en el sistema",
    ),
    "eventos": (
        "Historial OCPP",
        "/monitoreo/eventos-ocpp",
        "Consulta la trazabilidad de comunicación de los cargadores",
    ),
    "recompensas": ("Recompensas", "/recompensas/catalogo", "Catálogo y reglas de recompensas."),
    "reportes": ("Reportes", "/reportes/dashboard", "Resumen del sistema para análisis."),
    "alertas": ("Alertas", "/monitoreo/alertas", "Alertas recientes del sistema."),
    "configuracion": ("Configuración", "/dashboard/kpi", "Estado y configuración general."),
}

CHARGER_REFRESH_INTERVAL_SECONDS = 15
CHARGER_HEARTBEAT_STABLE_SECONDS = 120
CHARGER_HEARTBEAT_OFFLINE_SECONDS = 300
OCPP_LIVE_REFRESH_INTERVAL_SECONDS = 15
RECORDS_PER_PAGE = 20
DASHBOARD_COLLAPSED_ROWS = 5
DASHBOARD_EXPANDED_ROWS = 20
SESSION_INITIAL_ROWS = 7
OCPP_MESSAGE_TABLE_FIELDS = (
    "charge_point_id",
    "direction",
    "message_type",
    "message_type_name",
    "unique_id",
    "action",
    "payload",
)
OCPP_MESSAGE_TABLE_LABELS = {
    "charge_point_id": "ID de cargador",
    "direction": "Dirección",
    "message_type": "Tipo de mensaje",
    "message_type_name": "Nombre del tipo",
    "unique_id": "ID único",
    "action": "Acción OCPP",
    "payload": "Datos (payload)",
    "subprotocolo_ocpp": "Protocolo OCPP",
    "error_code": "Código de error",
    "error_description": "Descripción del error",
    "raw_message": "Mensaje original",
    "timestamp": "Fecha y hora",
}
OCPP_MESSAGE_TABLE_WIDTHS = {
    "charge_point_id": "12%",
    "direction": "8%",
    "message_type": "8%",
    "message_type_name": "12%",
    "unique_id": "12%",
    "action": "14%",
    "payload": "34%",
}
OCPP_PROTOCOL_OPTIONS = {
    "ocpp1.6": "OCPP 1.6J (ocpp1.6 / ocpp1.6j)",
    "ocpp2.0.1": "OCPP 2.0.1",
    "ocpp2.1": "OCPP 2.1",
}

DASHBOARD_KPI_KEYS = (
    "total_cargas",
    "cargas_hoy",
    "energia_total_kwh",
    "cargadores_activos",
    "cargadores_conectados",
    "cargadores_ocupados",
    "cargadores_mantenimiento",
    "cargadores_fuera_de_servicio",
    "total_usuarios",
    "total_vehiculos",
    "recompensas_pendientes",
    "sesiones_activas",
    "alertas_recientes",
    "eventos_recientes",
)


def _rows_from_response(result):
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return None
    for key in ("items", "data", "usuarios", "vehiculos", "cargadores", "sesiones", "tarjetas", "catalogo", "alertas", "eventos"):
        value = result.get(key)
        if isinstance(value, list):
            return value
    return [result] if result else []


def _build_list_params(page_key, filters):
    if page_key in {"eventos", "alertas"}:
        params = {"dias": filters.get("dias", 7), "limit": 100}
        if page_key == "alertas" and filters.get("nivel"):
            params["nivel"] = filters["nivel"]
        if page_key == "alertas" and filters.get("modulo"):
            params["modulo"] = filters["modulo"].strip()
        if page_key == "eventos" and filters.get("charge_point_id"):
            params["charge_point_id"] = filters["charge_point_id"].strip()
        return params
    if page_key == "sesiones":
        params = {"limit": 200}
        for key in (
            "estado",
            "charge_point_id",
            "fecha_inicio",
            "fecha_fin",
            "usuario_id",
        ):
            value = filters.get(key)
            if value:
                params[key] = value.strip() if isinstance(value, str) else value
        return params
    return None


def _build_user_update_payload(payload, password, password_confirmation):
    updated = dict(payload)
    if not password:
        return updated
    if len(password) < 6:
        raise ValueError("La nueva contraseña debe tener al menos 6 caracteres.")
    if password != password_confirmation:
        raise ValueError("Las nuevas contraseñas no coinciden.")
    updated["password"] = password
    return updated


def _load_all_users(token, api_base_url):
    users = []
    skip = 0
    page_size = 100
    while True:
        result = api_get(
            "/usuarios/",
            params={"skip": skip, "limit": page_size},
            token=token,
            base_url=api_base_url,
        )
        if isinstance(result, dict) and result.get("error"):
            return result
        if not isinstance(result, list):
            return {"error": "La respuesta de usuarios no tiene el formato esperado."}
        users.extend(result)
        if len(result) < page_size:
            return users
        skip += len(result)


def _load_all_companies(token, api_base_url):
    result = api_get(
        "/empresas/",
        token=token,
        base_url=api_base_url,
    )
    if isinstance(result, dict) and result.get("error"):
        return result
    if not isinstance(result, list):
        return {"error": "La respuesta de empresas no tiene el formato esperado."}
    return result


def _filter_companies(companies, query):
    normalized_query = (query or "").strip().casefold()
    if not normalized_query:
        return [company for company in companies if isinstance(company, dict)]
    return [
        company
        for company in companies
        if isinstance(company, dict)
        and normalized_query
        in " ".join(
            str(company.get(field) or "")
            for field in (
                "nombre",
                "contacto_nombre",
                "contacto_email",
                "contacto_telefono",
            )
        ).casefold()
    ]


async def _reload_chargers_when_idle():
    dialog_open = await ui.run_javascript(
        "Boolean(document.querySelector('.q-dialog--modal'))"
    )
    if not dialog_open:
        ui.navigate.reload()


async def _reload_ocpp_events_when_idle():
    if not app.storage.user.get("ocpp_events_live", False):
        return
    dialog_open = await ui.run_javascript(
        "Boolean(document.querySelector('.q-dialog--modal'))"
    )
    if not dialog_open:
        ui.navigate.reload()


def _schedule_charger_auto_refresh(page_key):
    if page_key == "cargadores":
        ui.timer(
            CHARGER_REFRESH_INTERVAL_SECONDS,
            _reload_chargers_when_idle,
            immediate=False,
        )


def _display_table(
    rows,
    *,
    row_key=None,
    fields=None,
    labels=None,
    column_widths=None,
    truncate_cell_values=True,
    pagination=None,
    responsive=False,
):
    if not rows and fields is None:
        ui.label("No hay registros para mostrar.").classes("text-slate-400")
        return
    if fields is None:
        fields = []
        seen = set()
        for row in rows:
            if isinstance(row, dict):
                for key in row:
                    if key not in seen and not key.startswith("_"):
                        seen.add(key)
                        fields.append(key)
        fields = fields[:8]
    else:
        fields = list(fields)
    if not fields:
        ui.label("No hay campos compatibles para mostrar.")
        return
    labels = labels or {}
    column_widths = column_widths or {}
    columns = [
        {
            "name": field,
            "label": labels.get(field, field.replace("_", " ").capitalize()),
            "field": field,
            "align": "left",
            "sortable": True,
            **(
                {
                    "style": f"width: {column_widths[field]};",
                    "headerStyle": f"width: {column_widths[field]};",
                }
                if field in column_widths
                else {}
            ),
        }
        for field in fields
    ]
    table_rows = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        table_row = {
            field: (
                str(row.get(field))
                if not isinstance(row.get(field), (dict, list))
                else (
                    str(row.get(field))
                    if not truncate_cell_values
                    else str(row.get(field))[:180]
                )
            )
            for field in fields
        }
        if row_key:
            table_row[row_key] = str(index)
        table_rows.append(table_row)
    table_options = {
        "columns": columns,
        "rows": table_rows,
        "row_key": row_key or fields[0],
    }
    if pagination is not None:
        table_options["pagination"] = pagination
    table = ui.table(**table_options)
    if responsive:
        table.props(
            'dense flat wrap-cells no-data-label="No hay registros para mostrar."'
        ).classes("w-full cc-responsive-table")
    else:
        table.classes("w-full")


def _dashboard_page_bounds(total_rows, page_size, page_index):
    total_pages = max(1, math.ceil(total_rows / page_size))
    page_index = min(max(page_index, 0), total_pages - 1)
    start = page_index * page_size
    return start, min(start + page_size, total_rows), total_pages, page_index


def _dashboard_history_chart_data(grouped, start_day, end_day):
    chart_days = sorted(grouped)
    if not chart_days:
        day_count = (end_day - start_day).days + 1
        if day_count <= 0:
            return [], []
        interval = max(1, math.ceil(day_count / 90))
        chart_days = [
            (start_day + timedelta(days=offset)).isoformat()
            for offset in range(0, day_count, interval)
        ]
        if chart_days[-1] != end_day.isoformat():
            chart_days.append(end_day.isoformat())

    chart_values = [
        round(grouped[day]["energy"], 2)
        if day in grouped and grouped[day]["energy_sessions"]
        else None
        for day in chart_days
    ]
    return chart_days, chart_values


def _dashboard_station_energy_rows(energy_rows, chargers):
    stations_by_id = {}
    for charger in chargers:
        charge_point_id = charger.get("charge_point_id")
        if isinstance(charge_point_id, str) and charge_point_id.strip():
            stations_by_id[charge_point_id] = (charger, None)

    unidentified_stations = []
    for item in energy_rows:
        value = item.get("energia_kwh")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            continue
        charge_point_id = item.get("charge_point_id")
        if isinstance(charge_point_id, str) and charge_point_id.strip():
            charger = stations_by_id.get(charge_point_id, ({}, None))[0]
            stations_by_id[charge_point_id] = ({**charger, **item}, value)
        else:
            unidentified_stations.append((item, value))

    stations = list(stations_by_id.values()) + unidentified_stations
    stations.sort(
        key=lambda entry: (entry[1] is not None, entry[1] or 0),
        reverse=True,
    )
    return stations


def _format_dashboard_timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    else:
        return "—" if value is None else str(value)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _dashboard_alert_table_rows(alerts):
    rows = []
    for index, item in enumerate(alerts):
        level = str(item.get("nivel") or "").strip().casefold()
        rows.append(
            {
                "fila": str(item.get("_id") or index),
                "nivel": {
                    "warning": "Warning",
                    "error": "Error",
                    "info": "Info",
                }.get(level, level.capitalize() or "—"),
                "nivel_class": level
                if level in {"warning", "error", "info"}
                else "info",
                "mensaje": str(item.get("mensaje") or "—"),
                "fecha": _format_dashboard_timestamp(item.get("timestamp")),
            }
        )
    return rows


def _dashboard_event_table_rows(events):
    return [
        {
            "fila": str(item.get("_id") or index),
            "cargador": str(
                item.get("charge_point_id")
                or item.get("chargePointId")
                or "—"
            ),
            "evento": str(
                item.get("tipo_mensaje") or item.get("accion") or "—"
            ),
            "fecha": _format_dashboard_timestamp(item.get("timestamp")),
        }
        for index, item in enumerate(events)
    ]


def _render_expandable_dashboard_rows(rows, render_rows):
    state = {"expanded": False, "page": 0}
    with ui.column().classes("w-full gap-0"):
        content = ui.column().classes("w-full gap-0")
        footer = ui.row().classes("cc-dashboard-list-footer w-full")

    def render():
        page_size = (
            DASHBOARD_EXPANDED_ROWS
            if state["expanded"]
            else DASHBOARD_COLLAPSED_ROWS
        )
        start, end, total_pages, page = _dashboard_page_bounds(
            len(rows), page_size, state["page"]
        )
        state["page"] = page
        content.clear()
        with content:
            render_rows(rows[start:end])

        footer.clear()
        if len(rows) <= DASHBOARD_COLLAPSED_ROWS:
            return
        with footer:
            with ui.row().classes("cc-dashboard-list-nav"):
                if state["expanded"] and page > 0:
                    ui.button(
                        "← Anterior",
                        on_click=lambda: change_page(-1),
                    ).props("outline no-caps dense").classes(
                        "cc-dashboard-page-button"
                    )
            with ui.column().classes("cc-dashboard-list-summary"):
                if state["expanded"]:
                    shown_range = (
                        f"Mostrando {start + 1}–{end} de {len(rows)}"
                    )
                else:
                    shown_range = (
                        f"Mostrando {end} de {len(rows)}"
                    )
                ui.label(shown_range).classes("cc-dashboard-muted")
                if state["expanded"] and total_pages > 1:
                    ui.label(
                        f"Página {page + 1} de {total_pages}"
                    ).classes("cc-dashboard-muted")
            with ui.row().classes("cc-dashboard-list-nav justify-end"):
                if state["expanded"] and page + 1 < total_pages:
                    ui.button(
                        "Siguiente →",
                        on_click=lambda: change_page(1),
                    ).props("outline no-caps dense").classes(
                        "cc-dashboard-page-button"
                    )
                if state["expanded"]:
                    ui.button(
                        "Ver menos ↑",
                        on_click=collapse,
                    ).props("outline no-caps dense").classes(
                        "cc-dashboard-page-button"
                    )
                else:
                    ui.button(
                        "Ver más ↓",
                        on_click=expand,
                    ).props("outline no-caps dense").classes(
                        "cc-dashboard-page-button"
                    )

    def expand():
        state["expanded"] = True
        state["page"] = 0
        render()

    def collapse():
        state["expanded"] = False
        state["page"] = 0
        render()

    def change_page(offset):
        state["page"] += offset
        render()

    render()


def _notify_result(result, success_message):
    if isinstance(result, dict) and result.get("error"):
        if result.get("_status_code") == 401:
            app.storage.user.clear()
            ui.navigate.to("/")
            return False
        ui.notify(result["error"], type="negative")
        return False
    ui.notify(success_message, type="positive")
    ui.navigate.reload()
    return True


def _show_api_error(result, fallback="No fue posible cargar la información."):
    if isinstance(result, dict) and result.get("_status_code") == 401:
        app.storage.user.clear()
        ui.navigate.to("/")
        return
    message = (
        result.get("error")
        if isinstance(result, dict)
        else fallback
    )
    show_error(message or fallback)


def _dashboard_chart_values(rows, value_key):
    values = []
    has_missing_values = False
    for row in rows:
        value = row.get(value_key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            values.append(None)
            has_missing_values = True
        else:
            values.append(value)
    return values, has_missing_values


def _render_dashboard_table(result, fallback, *, empty_columns=None):
    if isinstance(result, dict) and "error" in result:
        _show_api_error(result, fallback)
    elif not isinstance(result, list):
        show_error(fallback)
    elif not result:
        if empty_columns:
            with ui.element("div").classes("cc-dashboard-table-wrap"):
                ui.table(
                    columns=empty_columns,
                    rows=[],
                    row_key=empty_columns[0]["name"],
                ).props(
                    'dense flat no-data-label="No hay registros para mostrar."'
                ).classes("w-full")
        else:
            ui.label("No hay registros para mostrar.").classes(
                "cc-dashboard-muted"
            )
    else:
        _render_expandable_dashboard_rows(
            result,
            lambda visible_rows: _display_table(
                visible_rows,
                row_key="_dashboard_row_key",
            ),
        )


EDITABLE_FIELDS = {
    "usuarios": ("nombre", "email", "telefono", "rol", "activo"),
    "vehiculos": (
        "marca",
        "modelo",
        "anio",
        "placa",
        "tipo_conector",
        "capacidad_bateria_kwh",
    ),
    "tarjetas": ("usuario_id", "estado"),
    "cargadores": (
        "nombre",
        "ubicacion",
        "fabricante",
        "modelo",
        "firmware_version",
        "estado_general",
        "activo",
        "conectores",
    ),
}


CONNECTOR_STATES = [
    "Available",
    "Preparing",
    "Charging",
    "Occupied",
    "SuspendedEV",
    "SuspendedEVSE",
    "Finishing",
    "Reserved",
    "Unavailable",
    "Faulted",
]


def _build_connector_editor(existing=None):
    rows = []
    with ui.column().classes("w-full gap-3"):
        ui.label("Conectores").classes("text-sm font-semibold text-slate-200")

        def add_connector(values=None):
            values = values or {}
            with ui.row().classes("w-full flex-wrap items-center gap-2") as row:
                connector_id = ui.number(
                    "ID",
                    value=values.get("id_conector", len(rows) + 1),
                    min=1,
                    step=1,
                ).props("outlined").classes("min-w-20 flex-1")
                connector_type = ui.input(
                    "Tipo",
                    value=values.get("tipo", "Type2"),
                ).props("outlined").classes("min-w-28 flex-1")
                power = ui.number(
                    "Potencia kW",
                    value=values.get("potencia_max_kw", 7.4),
                    min=0.1,
                    step=0.1,
                ).props("outlined").classes("min-w-28 flex-1")
                state = ui.select(
                    CONNECTOR_STATES,
                    value=values.get("estado", "Available"),
                    label="Estado",
                ).props("outlined").classes("min-w-32 flex-1")
                fields = {
                    "id_conector": connector_id,
                    "tipo": connector_type,
                    "potencia_max_kw": power,
                    "estado": state,
                }
                rows.append(fields)

                def remove_connector():
                    rows.remove(fields)
                    row.delete()

                ui.button(
                    icon="remove_circle_outline",
                    on_click=remove_connector,
                ).props("flat round dense").classes("text-red-300")

        for connector in existing or []:
            add_connector(connector)
        if not rows:
            add_connector()

        ui.button(
            "Agregar conector",
            icon="add",
            on_click=lambda: add_connector(),
        ).props("flat no-caps").classes("text-orange-400")
    return rows


def _serialize_connectors(rows):
    if not rows:
        raise ValueError("Configura al menos un conector.")
    connectors = []
    seen_ids = set()
    for fields in rows:
        connector_id = int(fields["id_conector"].value)
        connector_type = (fields["tipo"].value or "").strip()
        power = float(fields["potencia_max_kw"].value)
        state = fields["estado"].value
        if connector_id < 1:
            raise ValueError("El ID del conector debe ser mayor que cero.")
        if connector_id in seen_ids:
            raise ValueError("No puede haber IDs de conector repetidos.")
        if not connector_type:
            raise ValueError("Indica el tipo de cada conector.")
        if power <= 0:
            raise ValueError("La potencia debe ser mayor que cero.")
        seen_ids.add(connector_id)
        connectors.append({
            "id_conector": connector_id,
            "tipo": connector_type,
            "potencia_max_kw": power,
            "estado": state,
        })
    return connectors


def _edit_record(page_key, record, token, api_base_url):
    record_id = record.get("_id") or record.get("id")
    if not record_id:
        ui.notify("El registro no contiene identificador para editar.", type="negative")
        return
    current_role = (app.storage.user.get("user") or {}).get("rol")
    editable_fields = list(EDITABLE_FIELDS[page_key])
    company_options = None
    if page_key == "usuarios":
        if current_role == "superadmin":
            company_result = _load_all_companies(token, api_base_url)
            if isinstance(company_result, dict) and company_result.get("error"):
                _show_api_error(company_result, "No se pudieron cargar las empresas.")
                return
            company_options = {
                company["_id"]: company["nombre"]
                for company in company_result
                if (
                    isinstance(company, dict)
                    and company.get("_id")
                    and company.get("activo", True)
                )
            }
            editable_fields.append("empresa_id")
        else:
            editable_fields.remove("rol")
        if (
            "rol" in editable_fields
            and record.get("rol") not in {"superadmin", "dueno_empresa", "cliente"}
        ):
            editable_fields.remove("rol")
    fields = {}
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-xl gap-4 p-6"
    ):
        ui.label("Editar registro").classes("text-xl font-bold text-white")
        for field in editable_fields:
            value = record.get(field)
            if field == "conectores":
                fields[field] = _build_connector_editor(value)
            elif field == "rol":
                fields[field] = ui.select(
                    {
                        "superadmin": "Superadministrador",
                        "dueno_empresa": "Dueño de empresa",
                        "cliente": "Cliente",
                    },
                    value=value or "cliente",
                    label="Rol",
                ).props("outlined").classes("w-full")
            elif field == "empresa_id":
                company_id = str(value) if value is not None else None
                fields[field] = ui.select(
                    company_options,
                    value=company_id,
                    label="Empresa",
                    clearable=True,
                ).props("outlined").classes("w-full")
            elif field == "estado":
                fields[field] = ui.select(
                    ["activa", "bloqueada", "perdida"],
                    value=value or "activa",
                    label="Estado",
                ).props("outlined").classes("w-full")
            elif field == "estado_general":
                fields[field] = ui.select(
                    [
                        "disponible",
                        "ocupado",
                        "mantenimiento",
                        "fuera_de_servicio",
                    ],
                    value=value or "disponible",
                    label="Estado general",
                ).props("outlined").classes("w-full")
            elif field == "tipo_conector":
                fields[field] = ui.select(
                    ["Type1", "Type2", "CCS", "CHAdeMO", "Tesla"],
                    value=value or "Type2",
                    label="Tipo de conector",
                ).props("outlined").classes("w-full")
            elif field == "activo":
                fields[field] = ui.select(
                    {True: "Activo", False: "Inactivo"},
                    value=bool(value),
                    label="Estado de cuenta",
                ).props("outlined").classes("w-full")
            elif field in {"anio", "capacidad_bateria_kwh"}:
                fields[field] = ui.number(
                    field.replace("_", " ").capitalize(),
                    value=value,
                    step=1 if field == "anio" else 0.1,
                ).props("outlined clearable").classes("w-full")
            else:
                fields[field] = ui.input(
                    field.replace("_", " ").capitalize(),
                    value=str(value or ""),
                ).props("outlined").classes("w-full")
        password = None
        password_confirmation = None
        if page_key == "usuarios":
            password = ui.input(
                "Nueva contraseña (opcional)",
                password=True,
                password_toggle_button=True,
            ).props("outlined autocomplete=new-password").classes("w-full")
            password_confirmation = ui.input(
                "Confirmar nueva contraseña",
                password=True,
                password_toggle_button=True,
            ).props("outlined autocomplete=new-password").classes("w-full")

        def save():
            payload = {}
            try:
                for name, element in fields.items():
                    if name == "conectores":
                        value = _serialize_connectors(element)
                    else:
                        value = element.value
                        if name == "anio":
                            value = int(value)
                        elif name == "capacidad_bateria_kwh":
                            value = (
                                float(value)
                                if value not in (None, "")
                                else None
                            )
                        elif name in {
                            "nombre",
                            "email",
                            "telefono",
                            "marca",
                            "modelo",
                            "placa",
                            "uid",
                            "ubicacion",
                            "fabricante",
                            "firmware_version",
                        }:
                            value = (value or "").strip()
                    payload[name] = value
                if page_key == "usuarios":
                    if current_role == "superadmin":
                        next_role = payload.get("rol", record.get("rol"))
                        if next_role == "superadmin":
                            payload["empresa_id"] = None
                        elif not payload.get("empresa_id"):
                            ui.notify(
                                "Selecciona la empresa para este usuario.",
                                type="warning",
                            )
                            return
                    payload = _build_user_update_payload(
                        payload,
                        password.value or "",
                        password_confirmation.value or "",
                    )
            except (TypeError, ValueError) as error:
                ui.notify(str(error), type="warning")
                return

            endpoints = {
                "usuarios": f"/usuarios/{record_id}",
                "vehiculos": f"/vehiculos/{record_id}",
                "tarjetas": f"/nfc-cards/{record_id}",
                "cargadores": f"/cargadores/{record_id}",
            }
            result = api_put(
                endpoints[page_key],
                json=payload,
                token=token,
                base_url=api_base_url,
            )
            if _notify_result(result, "Registro actualizado."):
                dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            ui.button("Guardar cambios", icon="save", on_click=save).props(
                "no-caps"
            ).classes("chargecore-primary bg-[#ff500a] text-white")
    dialog.open()


def _deactivate_record(page_key, record, token, api_base_url):
    record_id = record.get("_id") or record.get("id")
    if page_key == "cargadores":
        result = api_put(
            f"/cargadores/{record_id}",
            json={"activo": False},
            token=token,
            base_url=api_base_url,
        )
    else:
        endpoint = {
            "usuarios": "/usuarios/",
            "vehiculos": "/vehiculos/",
            "tarjetas": "/nfc-cards/",
        }[page_key]
        result = api_delete(
            f"{endpoint}{record_id}",
            token=token,
            base_url=api_base_url,
        )
    _notify_result(result, "Registro desactivado o bloqueado.")


def _delete_charger(record, token, api_base_url):
    charger_id = record.get("_id") or record.get("id")
    if not charger_id:
        ui.notify("El cargador no tiene identificador de registro.", type="negative")
        return

    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-md gap-4 p-6"
    ):
        ui.label("Eliminar cargador").classes("text-xl font-bold text-white")
        ui.label(
            f"Se eliminará permanentemente {record.get('nombre') or record.get('charge_point_id')}. "
            "La eliminación quedará registrada en Reportes."
        ).classes("text-sm text-slate-300")
        ui.label("Haz doble clic en el botón para confirmar.").classes(
            "text-sm text-red-300"
        )
        command_lock = Lock()

        def confirm_delete():
            if not command_lock.acquire(blocking=False):
                return
            delete_button.disable()
            try:
                result = api_delete(
                    f"/cargadores/{charger_id}",
                    token=token,
                    base_url=api_base_url,
                )
                if _notify_result(
                    result,
                    "Cargador eliminado y registrado en Reportes.",
                ):
                    dialog.close()
                    ui.navigate.reload()
            finally:
                delete_button.enable()
                command_lock.release()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            delete_button = ui.button(
                "Eliminar permanentemente",
                icon="delete_forever",
            ).props("no-caps").classes("bg-red-700 text-white")
            delete_button.on("dblclick", confirm_delete)
    dialog.open()


def _show_record_history(record, token, api_base_url):
    user_id = record.get("_id") or record.get("id")
    result = api_get(
        f"/sesiones/usuario/{user_id}",
        token=token,
        base_url=api_base_url,
    )
    result_rows = (
        result.get("sesiones", [])
        if isinstance(result, dict)
        else _rows_from_response(result) or []
    )
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-5xl gap-4 p-5"
    ):
        ui.label(f"Historial de {record.get('nombre') or 'usuario'}").classes(
            "text-xl font-bold text-white"
        )
        if isinstance(result, dict) and result.get("error"):
            _show_api_error(result)
        else:
            _display_table(result_rows)
        ui.button("Cerrar", on_click=dialog.close).props("flat no-caps")
    dialog.open()


def _show_charger_history(record, token, api_base_url):
    charger_id = record.get("_id") or record.get("id")
    result = api_get(
        f"/cargadores/{charger_id}/historial",
        token=token,
        base_url=api_base_url,
    )
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-5xl gap-4 p-5"
    ):
        ui.label(
            f"Historial de {record.get('charge_point_id') or 'cargador'}"
        ).classes("text-xl font-bold text-white")
        if isinstance(result, dict) and result.get("error"):
            _show_api_error(result)
        else:
            _display_table(_rows_from_response(result) or [])
        ui.button("Cerrar", on_click=dialog.close).props("flat no-caps")
    dialog.open()


def _show_charger_meter_values(record, token, api_base_url):
    charge_point_id = record.get("charge_point_id")
    result = api_get(
        f"/cargadores/{charge_point_id}/meter-values",
        params={"limit": 50},
        token=token,
        base_url=api_base_url,
    )
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-5xl gap-4 p-5"
    ):
        ui.label(f"Lecturas · {charge_point_id}").classes(
            "text-xl font-bold text-white"
        )
        if isinstance(result, dict) and result.get("error"):
            _show_api_error(result)
        else:
            _display_table(_rows_from_response(result) or [])
        ui.button("Cerrar", on_click=dialog.close).props("flat no-caps")
    dialog.open()


def _charger_connection_urls(api_base_url, charge_point_id):
    try:
        parsed = urlsplit(api_base_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("La dirección de la API no es válida.") from error
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ValueError("La dirección de la API no permite generar la ruta OCPP.")

    host = hostname
    if ":" in host:
        host = f"[{host}]"
    if port is not None:
        host = f"{host}:{port}"
    path = f"/ocpp/{quote(charge_point_id, safe='')}"
    return {
        "wss": f"wss://{host}{path}",
        "ws": f"ws://{host}{path}",
    }


def _show_charger_connection(
    record,
    api_base_url,
    *,
    ocpp_password=None,
    reload_on_close=False,
):
    charge_point_id = record.get("charge_point_id")
    if not charge_point_id:
        ui.notify("El cargador no tiene un identificador OCPP.", type="negative")
        return
    try:
        urls = _charger_connection_urls(api_base_url, charge_point_id)
    except ValueError as error:
        ui.notify(str(error), type="negative")
        return

    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-3xl gap-4 p-6"
    ):
        ui.label("Conexión del cargador").classes(
            "text-xl font-bold text-white"
        )
        ui.label(
            "Configura estos datos en el cargador. La ruta WSS cifra la conexión "
            "y es la recomendada para producción."
        ).classes("text-sm text-slate-300")
        ui.input("Identificador OCPP / usuario", value=charge_point_id).props(
            "outlined readonly"
        ).classes("w-full")
        ui.input("URL segura (WSS, recomendada)", value=urls["wss"]).props(
            "outlined readonly"
        ).classes("w-full")
        ui.input("URL sin cifrar (WS, solo red local de confianza)", value=urls["ws"]).props(
            "outlined readonly"
        ).classes("w-full")
        ui.input(
            "Versión OCPP configurada",
            value=OCPP_PROTOCOL_OPTIONS.get(
                record.get("ocpp_version"),
                "Sin versión configurada",
            ),
        ).props("outlined readonly").classes("w-full")
        ui.input(
            "Subprotocolo OCPP",
            value=record.get("subprotocolo_ocpp") or "Se detectará al conectar",
        ).props("outlined readonly").classes("w-full")
        ui.label(
            "Versiones aceptadas por el servidor: OCPP 1.6J (ocpp1.6 u "
            "ocpp1.6j), OCPP 2.0.1 y OCPP 2.1. Configura en el cargador el subprotocolo "
            "de la versión que ejecuta; ChargeCore mostrará el negociado al conectar. "
            "Las funciones no incluidas en el adaptador de la versión no están soportadas."
        ).classes("text-xs text-slate-400")

        with ui.card().classes("w-full gap-2 bg-slate-900 p-4"):
            ui.label("Seguridad").classes("font-semibold text-white")
            ui.label(
                "Autenticación OCPP HTTP Basic: usa el identificador OCPP como "
                "usuario y la contraseña OCPP configurada para este cargador."
            ).classes("text-sm text-slate-300")
            if ocpp_password:
                password = ui.input(
                    "Contraseña OCPP (mostrar ahora y guardar en el cargador)",
                    value=ocpp_password,
                    password=True,
                    password_toggle_button=True,
                ).props("outlined readonly").classes("w-full")
                password.tooltip(
                    "La contraseña no se puede recuperar después de cerrar esta pantalla."
                )
                ui.label(
                    "Guárdala ahora en el equipo. ChargeCore conserva únicamente "
                    "una verificación protegida y no podrá volver a mostrarla."
                ).classes("text-xs text-amber-300")
            elif record.get("ocpp_credentials_configured"):
                ui.label(
                    "La contraseña está configurada, pero no se puede recuperar "
                    "desde el sistema. Un administrador puede establecer una nueva "
                    "desde «Credenciales OCPP»."
                ).classes("text-sm text-amber-300")
            else:
                ui.label(
                    "Aún no se ha configurado una contraseña OCPP para este cargador."
                ).classes("text-sm text-amber-300")

        with ui.card().classes("w-full gap-2 bg-slate-900 p-4"):
            ui.label("Pasos de conexión").classes("font-semibold text-white")
            ui.label(
                "1. Red Wi-Fi: selecciona la red e ingresa su contraseña directamente "
                "en el cargador. No se guarda en ChargeCore."
            ).classes("text-sm text-slate-300")
            ui.label(
                "2. Cable Ethernet: conecta el cable al cargador y a la red; "
                "comprueba que obtenga acceso a la red (por DHCP si está habilitado)."
            ).classes("text-sm text-slate-300")
            ui.label(
                "3. En los ajustes OCPP del equipo, captura la URL WSS, el "
                "identificador, el subprotocolo y la credencial OCPP."
            ).classes("text-sm text-slate-300")
            ui.label(
                "4. Conserva habilitada la validación del certificado TLS. WS no "
                "cifra las credenciales y no debe usarse en Internet."
            ).classes("text-sm text-slate-300")
            ui.label(
                "Los nombres de menús pueden variar según fabricante y modelo."
            ).classes("text-xs text-slate-400")

        def close():
            dialog.close()
            if reload_on_close:
                ui.navigate.reload()

        with ui.row().classes("w-full justify-end"):
            ui.button("Cerrar", on_click=close).props("flat no-caps")
    dialog.open()


def _view_charger_connection(record, token, api_base_url):
    charger_id = record.get("_id") or record.get("id")
    if not charger_id:
        ui.notify("El cargador no tiene un identificador de registro.", type="negative")
        return
    result = api_get(
        f"/cargadores/{charger_id}",
        token=token,
        base_url=api_base_url,
    )
    if isinstance(result, dict) and result.get("error"):
        _show_api_error(result, "No se pudo consultar la conexión del cargador.")
        return
    if not isinstance(result, dict):
        show_error("La respuesta del cargador no tiene el formato esperado.")
        return
    _show_charger_connection(result, api_base_url)


def _show_session_meter_values(record, token, api_base_url):
    transaction_id = record.get("transaction_id")
    if transaction_id is None:
        ui.notify(
            "La sesión no contiene el identificador de transacción OCPP.",
            type="warning",
        )
        return
    result = api_get(
        f"/sesiones/transaction/{transaction_id}/meter-values",
        token=token,
        base_url=api_base_url,
    )
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-5xl gap-4 p-5"
    ):
        ui.label(f"Lecturas de sesión · {transaction_id}").classes(
            "text-xl font-bold text-white"
        )
        if isinstance(result, dict) and result.get("error"):
            _show_api_error(result)
        else:
            _display_table(_rows_from_response(result) or [])
        ui.button("Cerrar", on_click=dialog.close).props("flat no-caps")
    dialog.open()


def _edit_reward_catalog(record, token, api_base_url):
    catalog_id = record.get("_id") or record.get("id")
    if not catalog_id:
        ui.notify("La recompensa no contiene identificador.", type="negative")
        return
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-xl gap-4 p-6"
    ):
        ui.label("Editar recompensa").classes("text-xl font-bold text-white")
        name = ui.input(
            "Nombre",
            value=record.get("nombre") or "",
        ).props("outlined").classes("w-full")
        company = ui.input(
            "Empresa",
            value=record.get("empresa") or "",
        ).props("outlined").classes("w-full")
        description = ui.textarea(
            "Descripción",
            value=record.get("descripcion") or "",
        ).props("outlined").classes("w-full")
        conditions = ui.textarea(
            "Condiciones",
            value=record.get("condiciones") or "",
        ).props("outlined").classes("w-full")
        with ui.row().classes("w-full gap-3"):
            start = ui.input(
                "Fecha de inicio",
                value=str(record.get("fecha_inicio") or "")[:10],
            ).props("outlined type=date").classes("w-full")
            end = ui.input(
                "Fecha de finalización",
                value=str(record.get("fecha_finalizacion") or "")[:10],
            ).props("outlined type=date").classes("w-full")
        active = ui.checkbox(
            "Recompensa activa",
            value=bool(record.get("activa", True)),
        )

        def save():
            values = {
                "nombre": (name.value or "").strip(),
                "empresa": (company.value or "").strip(),
                "descripcion": (description.value or "").strip(),
                "condiciones": (conditions.value or "").strip(),
                "fecha_inicio": start.value,
                "fecha_finalizacion": end.value,
                "activa": bool(active.value),
            }
            if any(
                not values[field]
                for field in (
                    "nombre",
                    "empresa",
                    "descripcion",
                    "fecha_inicio",
                    "fecha_finalizacion",
                )
            ):
                ui.notify("Completa todos los campos.", type="warning")
                return
            if values["fecha_inicio"] > values["fecha_finalizacion"]:
                ui.notify(
                    "La fecha de inicio no puede ser posterior a la fecha final.",
                    type="warning",
                )
                return
            values["fecha_inicio"] += "T00:00:00"
            values["fecha_finalizacion"] += "T00:00:00"
            result = api_put(
                f"/recompensas/catalogo/{catalog_id}",
                json=values,
                token=token,
                base_url=api_base_url,
            )
            if _notify_result(result, "Recompensa actualizada."):
                dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            ui.button("Guardar cambios", icon="save", on_click=save).props(
                "no-caps"
            ).classes("chargecore-primary bg-[#ff500a] text-white")
    dialog.open()


def _set_charger_availability(record, token, api_base_url):
    charge_point_id = record.get("charge_point_id")
    if not charge_point_id:
        ui.notify("El cargador no tiene identificador OCPP.", type="negative")
        return
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-md gap-4 p-5"
    ):
        ui.label("Cambiar disponibilidad").classes(
            "text-lg font-bold text-white"
        )
        connector = ui.number(
            "Conector (0 para todos)",
            value=0,
            min=0,
            step=1,
        ).props("outlined").classes("w-full")
        state = ui.select(
            {"Operative": "Disponible", "Inoperative": "No disponible"},
            value="Operative",
            label="Disponibilidad",
        ).props("outlined").classes("w-full")
        command_lock = Lock()

        def submit():
            try:
                connector_id = int(connector.value)
            except (TypeError, ValueError):
                ui.notify("Ingresa un conector válido.", type="warning")
                return
            if not command_lock.acquire(blocking=False):
                return
            submit_button.disable()
            try:
                result = api_post(
                    f"/cargadores/{charge_point_id}/availability",
                    json={
                        "connectorId": connector_id,
                        "type": state.value,
                    },
                    token=token,
                    base_url=api_base_url,
                    timeout=OCPP_COMMAND_TIMEOUT_SECONDS,
                )
                if _notify_result(result, "Se envió el cambio de disponibilidad."):
                    dialog.close()
            finally:
                submit_button.enable()
                command_lock.release()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            submit_button = ui.button("Enviar", icon="send", on_click=submit).props(
                "no-caps"
            ).classes("chargecore-primary bg-[#ff500a] text-white")
    dialog.open()


def _charger_command(record, token, api_base_url, command):
    charge_point_id = record.get("charge_point_id")
    if not charge_point_id:
        ui.notify("El cargador no tiene identificador OCPP.", type="negative")
        return
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-md gap-4 p-5"
    ):
        ui.label("Confirmar operación OCPP").classes(
            "text-lg font-bold text-white"
        )
        ui.label(command["confirm"]).classes("text-slate-300")
        command_lock = Lock()

        def execute():
            if not command_lock.acquire(blocking=False):
                return
            execute_button.disable()
            try:
                result = api_post(
                    command["endpoint"](charge_point_id),
                    json=command["payload"](),
                    token=token,
                    base_url=api_base_url,
                    timeout=OCPP_COMMAND_TIMEOUT_SECONDS,
                )
                if _notify_result(result, command["success"]):
                    dialog.close()
            finally:
                execute_button.enable()
                command_lock.release()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            execute_button = ui.button(
                command["label"],
                icon=command["icon"],
                on_click=execute,
            ).props("no-caps").classes("bg-red-700 text-white")
    dialog.open()


def _reconcile_charger_commands(record, token, api_base_url):
    charge_point_id = record.get("charge_point_id")
    if not charge_point_id:
        ui.notify("El cargador no tiene identificador OCPP.", type="negative")
        return
    result = api_post(
        f"/cargadores/{charge_point_id}/commands/reconcile",
        json={},
        token=token,
        base_url=api_base_url,
        timeout=OCPP_COMMAND_TIMEOUT_SECONDS,
    )
    if not isinstance(result, dict):
        ui.notify("La respuesta de reconciliación no es válida.", type="negative")
        return
    if result.get("error"):
        _notify_result(result, "Se revisaron los comandos pendientes.")
        return

    commands = result.get("commands")
    if not isinstance(commands, list):
        ui.notify("La respuesta de reconciliación no es válida.", type="negative")
        return
    if not commands:
        ui.notify("No hay comandos pendientes de reconciliación.", type="positive")
        return
    details = "; ".join(
        f"{item.get('action')}: {item.get('status')}"
        for item in commands
    )
    notification_type = (
        "warning"
        if any(item.get("status") != "confirmed" for item in commands)
        else "positive"
    )
    ui.notify(f"Resultado de reconciliación: {details}", type=notification_type)


def _render_record_actions(
    page_key,
    record,
    token,
    api_base_url,
    *,
    can_manage=False,
    can_operate=False,
):
    if page_key in EDITABLE_FIELDS and can_manage:
        ui.button(
            "Editar",
            icon="edit",
            on_click=lambda: _edit_record(
                page_key, record, token, api_base_url
            ),
        ).props("flat no-caps").classes("text-orange-400")
        ui.button(
            "Desactivar",
            icon="block",
            on_click=lambda: _deactivate_record(
                page_key, record, token, api_base_url
            ),
        ).props("flat no-caps").classes("text-red-300")
        if page_key == "usuarios":
            ui.button(
                "Historial",
                icon="history",
                on_click=lambda: _show_record_history(
                    record, token, api_base_url
                ),
            ).props("flat no-caps").classes("text-slate-300")
    if page_key == "cargadores":
        if can_manage:
            ui.button(
                "Credenciales OCPP",
                icon="key",
                on_click=lambda: _edit_ocpp_credentials(
                    record,
                    token,
                    api_base_url,
                ),
            ).props("flat no-caps").classes("text-orange-400")
        ui.button(
            "Ver conexión",
            icon="router",
            on_click=lambda: _view_charger_connection(
                record,
                token,
                api_base_url,
            ),
        ).props("flat no-caps").classes("text-cyan-300")
        ui.button(
            "Historial",
            icon="history",
            on_click=lambda: _show_charger_history(
                record, token, api_base_url
            ),
        ).props("flat no-caps").classes("text-slate-300")
        if can_manage:
            ui.button(
                "Eliminar",
                icon="delete_forever",
                on_click=lambda: _delete_charger(
                    record,
                    token,
                    api_base_url,
                ),
            ).props("flat no-caps").classes("text-red-300")
        if can_operate:
            ui.button(
                "Cambiar disponibilidad",
                icon="settings",
                on_click=lambda: _set_charger_availability(
                    record, token, api_base_url
                ),
            ).props("flat no-caps").classes("text-orange-400")
            ui.button(
                "Revisar comandos",
                icon="sync",
                on_click=lambda: _reconcile_charger_commands(
                    record, token, api_base_url
                ),
            ).props("flat no-caps").classes("text-cyan-300")
    elif page_key == "sesiones" and can_operate:
        state = str(record.get("estado") or "").lower()
        if state in {"activa", "iniciada"}:
            charge_point_id = record.get("charge_point_id")
            transaction_id = record.get("transaction_id")
            if charge_point_id and transaction_id:
                command_lock = Lock()

                def stop_session():
                    if not command_lock.acquire(blocking=False):
                        return
                    stop_button.disable()
                    try:
                        result = api_post(
                            f"/cargadores/{charge_point_id}/remote-stop",
                            json={"transactionId": transaction_id},
                            token=token,
                            base_url=api_base_url,
                            timeout=OCPP_COMMAND_TIMEOUT_SECONDS,
                        )
                        _notify_result(
                            result, "Se envió la orden de detener la sesión."
                        )
                    finally:
                        stop_button.enable()
                        command_lock.release()

                stop_button = ui.button(
                    "Detener sesión",
                    icon="stop",
                    on_click=stop_session,
                ).props("flat no-caps").classes("text-red-300")
        ui.button(
            "Lecturas",
            icon="monitoring",
            on_click=lambda: _show_session_meter_values(
                record, token, api_base_url
            ),
        ).props("flat no-caps").classes("text-orange-400")
    elif page_key == "sesiones":
        ui.button(
            "Lecturas",
            icon="monitoring",
            on_click=lambda: _show_session_meter_values(
                record, token, api_base_url
            ),
        ).props("flat no-caps").classes("text-orange-400")
    elif (
        page_key == "recompensas"
        and can_manage
        and record.get("_id")
    ):
        ui.button(
            "Editar",
            icon="edit",
            on_click=lambda: _edit_reward_catalog(
                record, token, api_base_url
            ),
        ).props("flat no-caps").classes("text-orange-400")

        def toggle_reward():
            result = api_put(
                f"/recompensas/catalogo/{record['_id']}/estado",
                json={"activa": not bool(record.get("activa"))},
                token=token,
                base_url=api_base_url,
            )
            _notify_result(result, "Estado de recompensa actualizado.")

        ui.button(
            "Desactivar" if record.get("activa") else "Activar",
            icon="toggle_on" if record.get("activa") else "toggle_off",
            on_click=toggle_reward,
        ).props("flat no-caps").classes("text-orange-400")
    if page_key == "cargadores":
        if not can_operate:
            return
        for command in (
            {
                "label": "Reinicio suave",
                "icon": "restart_alt",
                "endpoint": lambda charger_id: f"/cargadores/{charger_id}/reset",
                "payload": lambda: {"type": "Soft"},
                "confirm": "El cargador se reiniciará de forma suave.",
                "success": "Se envió el reinicio suave.",
            },
            {
                "label": "Reinicio forzado",
                "icon": "power_settings_new",
                "endpoint": lambda charger_id: f"/cargadores/{charger_id}/reset",
                "payload": lambda: {"type": "Hard"},
                "confirm": "El reinicio forzado puede interrumpir una carga activa.",
                "success": "Se envió el reinicio forzado.",
            },
        ):
            ui.button(
                command["label"],
                icon=command["icon"],
                on_click=lambda item=command: _charger_command(
                    record, token, api_base_url, item
                ),
            ).props("flat no-caps").classes("text-amber-300")


def _edit_ocpp_credentials(record, token, api_base_url):
    charger_id = record.get("_id") or record.get("id")
    if not charger_id:
        ui.notify("El cargador no contiene identificador para editar.", type="negative")
        return
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-md gap-4 p-6"
    ):
        ui.label("Actualizar credenciales OCPP").classes(
            "text-xl font-bold text-white"
        )
        ui.label(
            "Define una nueva contraseña para el usuario OCPP de este cargador."
        ).classes("text-sm text-slate-400")
        password = ui.input(
            "Nueva contraseña OCPP",
            password=True,
            password_toggle_button=True,
        ).props("outlined autocomplete=new-password").classes("w-full")
        confirmation = ui.input(
            "Confirmar contraseña OCPP",
            password=True,
            password_toggle_button=True,
        ).props("outlined autocomplete=new-password").classes("w-full")

        def save():
            if len(password.value or "") < 12:
                ui.notify(
                    "La contraseña OCPP debe tener al menos 12 caracteres.",
                    type="warning",
                )
                return
            if password.value != confirmation.value:
                ui.notify("Las contraseñas no coinciden.", type="warning")
                return
            result = api_put(
                f"/cargadores/{charger_id}/ocpp-credentials",
                json={"password": password.value},
                token=token,
                base_url=api_base_url,
            )
            if _notify_result(result, "Credenciales OCPP actualizadas."):
                dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            ui.button("Guardar credenciales", icon="save", on_click=save).props(
                "no-caps"
            ).classes("chargecore-primary bg-[#ff500a] text-white")
    dialog.open()


def _user_summary_counts(rows):
    active_count = sum(bool(row.get("activo", True)) for row in rows)
    return {
        "total": len(rows),
        "active": active_count,
        "admins": sum(
            str(row.get("rol") or "").lower() == "superadmin"
            for row in rows
        ),
        "inactive": len(rows) - active_count,
    }


def _user_summary_cards(rows):
    summary = _user_summary_counts(rows)
    return (
        (
            "Usuarios registrados",
            summary["total"],
            "Inventario completo",
            "groups",
            "#8b5cf6",
        ),
        (
            "Usuarios activos",
            summary["active"],
            "Cuentas habilitadas",
            "check_box",
            "#22c55e",
        ),
        (
            "Superadministradores",
            summary["admins"],
            "Con acceso administrativo",
            "shield",
            "#9ca3af",
        ),
        (
            "Cuentas inactivas",
            summary["inactive"],
            "Sin acceso activo",
            "do_not_disturb_on",
            "#ef4444",
        ),
    )


def _render_summary_cards(summary_cards):
    ui.add_head_html(
        """
        <style>
            .cc-summary-grid {
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 16px;
                width: 100%;
            }
            .q-page .cc-summary-card {
                box-sizing: border-box;
                min-width: 0;
                min-height: 170px;
                justify-content: space-between;
                gap: 8px;
                padding: 18px;
                border: 1px solid var(--cc-border) !important;
                border-radius: 14px !important;
                background: var(--cc-bg-card) !important;
                color: var(--cc-text-primary) !important;
                box-shadow: none !important;
            }
            .cc-summary-icon-box {
                display: grid;
                width: 38px;
                height: 38px;
                place-items: center;
                border-radius: 11px;
                background: var(--cc-field);
            }
            .cc-summary-icon {
                font-size: 20px;
            }
            .cc-summary-title {
                color: var(--cc-text-muted);
                font-size: 11px;
                font-weight: 700;
                text-transform: uppercase;
            }
            .cc-summary-value {
                color: var(--cc-text-primary);
                font-size: 32px;
                font-weight: 700;
                line-height: 1;
            }
            .cc-summary-caption {
                color: var(--cc-text-muted);
                font-size: 12px;
            }
            .q-page .cc-summary-card-featured {
                border-color: #fff !important;
                background: var(--cc-primary) !important;
            }
            .cc-summary-card-featured .cc-summary-icon-box {
                background: rgba(255, 255, 255, .2);
            }
            .cc-summary-card-featured .cc-summary-title,
            .cc-summary-card-featured .cc-summary-value,
            .cc-summary-card-featured .cc-summary-caption {
                color: #fff !important;
            }
            @media (max-width: 900px) {
                .cc-summary-grid {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }
            }
            @media (max-width: 600px) {
                .cc-summary-grid {
                    grid-template-columns: minmax(0, 1fr);
                }
                .q-page .cc-summary-card {
                    min-height: 145px;
                    padding: 15px;
                }
            }
        </style>
        """
    )
    with ui.element("div").classes("cc-summary-grid"):
        for index, (title, value, caption, icon, icon_color) in enumerate(
            summary_cards
        ):
            card_class = (
                "chargecore-card cc-summary-card cc-summary-card-featured"
                if index == 0
                else "chargecore-card cc-summary-card"
            )
            with ui.card().classes(card_class):
                with ui.element("div").classes("cc-summary-icon-box"):
                    ui.icon(icon).classes("cc-summary-icon").style(
                        f"color: {'#fff' if index == 0 else icon_color};"
                    )
                ui.label(title).classes("cc-summary-title")
                ui.label(str(value)).classes("cc-summary-value")
                ui.label(caption).classes("cc-summary-caption")


def _render_users_records(rows, token, api_base_url):
    ui.add_head_html(
        """
        <style>
            .cc-users-page {
                display: flex;
                flex-direction: column;
                gap: 20px;
                width: 100%;
                padding-top: 20px;
            }
            .cc-users-page .cc-users-section-title {
                margin-bottom: 10px;
                color: var(--cc-text-primary);
                font-size: 18px;
                font-weight: 700;
            }
            .q-page .cc-users-page .cc-users-panel {
                min-width: 0;
                gap: 0;
                padding: 20px;
                border-radius: 12px;
            }
            .cc-users-page .cc-users-filter-fields {
                display: grid;
                grid-template-columns: minmax(220px, 1.7fr) repeat(2, minmax(170px, 1fr));
                gap: 14px;
                width: 100%;
            }
            .cc-users-page .cc-users-filter-fields > * {
                min-width: 0;
                width: 100%;
            }
            .cc-users-page .cc-users-table-scroll {
                width: 100%;
                overflow-x: auto;
            }
            .cc-users-page .cc-users-table {
                min-width: 700px;
                width: 100%;
            }
            .cc-users-page .cc-users-table-head,
            .cc-users-page .cc-users-table-row {
                display: grid;
                grid-template-columns:
                    minmax(120px, 1.15fr)
                    minmax(160px, 1.5fr)
                    minmax(110px, .95fr)
                    minmax(55px, .45fr)
                    minmax(90px, .8fr)
                    44px;
                align-items: center;
                column-gap: 14px;
                min-width: 700px;
                padding: 0 12px;
            }
            .cc-users-page .cc-users-table-head {
                min-height: 42px;
                border-bottom: 1px solid var(--cc-border);
                color: var(--cc-text-muted);
                font-size: 11px;
                font-weight: 700;
                letter-spacing: .06em;
                text-transform: uppercase;
            }
            .cc-users-page .cc-users-table-row {
                min-height: 56px;
                border-bottom: 1px solid var(--cc-border);
                color: var(--cc-text-secondary);
                font-size: 13px;
                transition: background-color .15s ease;
            }
            .cc-users-page .cc-users-table-row:hover {
                background: var(--cc-field);
            }
            .cc-users-page .cc-users-cell {
                min-width: 0;
                overflow-wrap: anywhere;
            }
            .cc-users-page .cc-users-empty {
                grid-column: 1 / -1;
            }
            .cc-users-page .cc-users-name {
                color: var(--cc-text-primary);
                font-weight: 600;
            }
            .cc-users-page .cc-users-badge {
                display: inline-flex;
                align-items: center;
                justify-self: start;
                min-height: 25px;
                padding: 3px 10px;
                border-radius: 999px;
                font-size: 11px;
                font-weight: 600;
                white-space: nowrap;
            }
            .cc-users-page .cc-users-role-superadmin {
                background: rgba(238, 90, 36, .14);
                color: #ee5a24;
            }
            .cc-users-page .cc-users-role-dueno_empresa {
                background: rgba(47, 111, 224, .14);
                color: #5d91ee;
            }
            .cc-users-page .cc-users-role-cliente,
            .cc-users-page .cc-users-role-default {
                background: rgba(139, 143, 152, .15);
                color: var(--cc-text-secondary);
            }
            .cc-users-page .cc-users-status-active {
                background: rgba(31, 166, 90, .14);
                color: #24ad62;
            }
            .cc-users-page .cc-users-status-inactive {
                background: rgba(214, 56, 47, .14);
                color: #e35b52;
            }
            .cc-users-page .cc-users-actions {
                display: flex;
                justify-content: center;
            }
            .q-page .cc-users-page .cc-users-action-button {
                color: var(--cc-text-muted);
            }
            .q-page .cc-users-page .cc-users-action-button:hover {
                background: var(--cc-field);
                color: var(--cc-text-primary);
            }
            .cc-users-page .cc-users-list-footer {
                padding-top: 14px;
                color: var(--cc-text-muted);
                font-size: 12px;
                text-align: center;
            }
            .q-menu.cc-users-action-menu {
                border: 1px solid var(--cc-border);
                background: var(--cc-bg-card);
                color: var(--cc-text-primary);
            }
            .q-menu.cc-users-action-menu .q-item:last-child {
                color: var(--cc-danger);
            }
            @media (max-width: 600px) {
                .cc-users-page {
                    gap: 16px;
                    padding-top: 14px;
                }
                .cc-users-page .cc-users-filter-fields {
                    grid-template-columns: minmax(0, 1fr);
                    gap: 10px;
                }
                .q-page .cc-users-page .cc-users-panel {
                    padding: 14px;
                }
            }
        </style>
        """
    )
    role = (app.storage.user.get("user") or {}).get("rol")
    can_manage = role in {"superadmin", "dueno_empresa"}
    page_state = {"number": 1}
    summary_cards = _user_summary_cards(rows)

    with ui.element("div").classes("cc-users-page"):
        _render_summary_cards(summary_cards)

        ui.label("Filtrar usuarios").classes("cc-users-section-title")
        with ui.card().classes("chargecore-card cc-users-panel"):
            with ui.element("div").classes("cc-users-filter-fields"):
                search = ui.input(
                    "Buscar por nombre o correo",
                    placeholder="Escribe un nombre o correo electrónico",
                ).props("outlined clearable")
                role_filter = ui.select(
                    {
                        "": "Todos",
                        "superadmin": "Superadministrador",
                        "dueno_empresa": "Dueño de empresa",
                        "cliente": "Cliente",
                    },
                    value="",
                    label="Por rol",
                ).props("outlined")
                status_filter = ui.select(
                    {"": "Todos", "true": "Activos", "false": "Inactivos"},
                    value="",
                    label="Por estado",
                ).props("outlined")

        ui.label("Lista de usuarios").classes("cc-users-section-title")
        with ui.card().classes("chargecore-card cc-users-panel"):
            list_container = ui.column().classes("w-full gap-0")

    def render_filtered():
        query = (search.value or "").strip().lower()
        filtered_rows = [
            row
            for row in rows
            if (
                not query
                or query in str(row.get("nombre") or "").lower()
                or query in str(row.get("email") or "").lower()
            )
            and (
                not role_filter.value
                or str(row.get("rol") or "").lower() == role_filter.value
            )
            and (
                not status_filter.value
                or bool(row.get("activo", True))
                == (status_filter.value == "true")
            )
        ]
        page_count = max(
            1,
            (len(filtered_rows) + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE,
        )
        page_state["number"] = min(max(page_state["number"], 1), page_count)
        start = (page_state["number"] - 1) * RECORDS_PER_PAGE
        visible_rows = filtered_rows[start : start + RECORDS_PER_PAGE]
        list_container.clear()
        with list_container:
            with ui.element("div").classes("cc-users-table-scroll"):
                with ui.element("div").classes("cc-users-table"):
                    with ui.element("div").classes("cc-users-table-head"):
                        for label in (
                            "Nombre",
                            "Correo electrónico",
                            "Rol",
                            "Cargas",
                            "Estado",
                            "Acciones",
                        ):
                            ui.label(label)
                    if not visible_rows:
                        with ui.element("div").classes("cc-users-table-row"):
                            ui.label("No hay usuarios que coincidan con los filtros.").classes(
                                "cc-users-cell cc-users-empty"
                            )
                    for record in visible_rows:
                        user_role = str(record.get("rol") or "").lower()
                        role_labels = {
                            "superadmin": "Superadministrador",
                            "dueno_empresa": "Dueño de empresa",
                            "cliente": "Cliente",
                        }
                        role_class = (
                            f"cc-users-role-{user_role}"
                            if user_role in role_labels
                            else "cc-users-role-default"
                        )
                        active = bool(record.get("activo", True))
                        with ui.element("div").classes("cc-users-table-row"):
                            ui.label(record.get("nombre") or "—").classes(
                                "cc-users-cell cc-users-name"
                            )
                            ui.label(record.get("email") or "—").classes(
                                "cc-users-cell"
                            )
                            ui.label(
                                role_labels.get(
                                    user_role,
                                    str(record.get("rol") or "Sin rol"),
                                )
                            ).classes(f"cc-users-badge {role_class}")
                            ui.label(
                                str(record.get("total_cargas", record.get("cargas", 0)))
                            ).classes("cc-users-cell")
                            status_class = (
                                "cc-users-status-active"
                                if active
                                else "cc-users-status-inactive"
                            )
                            ui.label(
                                "Activo" if active else "Inactivo"
                            ).classes(f"cc-users-badge {status_class}")
                            with ui.element("div").classes("cc-users-actions"):
                                if can_manage:
                                    with ui.button(icon="more_vert").props(
                                        "flat round dense"
                                    ).classes("cc-users-action-button"):
                                        with ui.menu().props(
                                            'anchor="bottom right" self="top right"'
                                        ).classes("cc-users-action-menu"):
                                            ui.menu_item(
                                                "Editar",
                                                on_click=lambda selected=record: _edit_record(
                                                    "usuarios",
                                                    selected,
                                                    token,
                                                    api_base_url,
                                                ),
                                            )
                                            ui.menu_item(
                                                "Ver historial",
                                                on_click=lambda selected=record: _show_record_history(
                                                    selected,
                                                    token,
                                                    api_base_url,
                                                ),
                                            )
                                            ui.separator()
                                            ui.menu_item(
                                                "Eliminar",
                                                on_click=lambda selected=record: _deactivate_record(
                                                    "usuarios",
                                                    selected,
                                                    token,
                                                    api_base_url,
                                                ),
                                            )
                                else:
                                    ui.label("—")
            ui.label(
                f"Mostrando {len(visible_rows)} de {len(filtered_rows)} usuarios"
            ).classes("cc-users-list-footer w-full")
            if page_count > 1:
                with ui.row().classes("w-full items-center justify-center gap-2"):
                    ui.button(
                        "Anterior",
                        icon="chevron_left",
                        on_click=lambda: change_page(-1),
                    ).props("flat no-caps").set_enabled(page_state["number"] > 1)
                    ui.label(
                        f"{page_state['number']} / {page_count}"
                    ).classes("text-sm text-slate-400")
                    ui.button(
                        "Siguiente",
                        icon="chevron_right",
                        on_click=lambda: change_page(1),
                    ).props("flat no-caps").set_enabled(
                        page_state["number"] < page_count
                    )

    def change_page(offset):
        page_state["number"] += offset
        render_filtered()

    def reset_page_and_render(_):
        page_state["number"] = 1
        render_filtered()

    search.on_value_change(reset_page_and_render)
    role_filter.on_value_change(reset_page_and_render)
    status_filter.on_value_change(reset_page_and_render)
    render_filtered()


def _registry_summary_cards(page_key, rows):
    if page_key == "vehiculos":
        brands = {
            str(row.get("marca")).strip().casefold()
            for row in rows
            if row.get("marca")
        }
        capacities = []
        for row in rows:
            try:
                capacity = float(row.get("capacidad_bateria_kwh"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(capacity):
                capacities.append(capacity)
        average_capacity = (
            f"{math.fsum(capacities) / len(capacities):.1f}"
            if capacities
            else "—"
        )
        return (
            (
                "Total de vehículos",
                len(rows),
                "Inventario completo",
                "directions_car",
                "#60a5fa",
            ),
            (
                "Marcas diferentes",
                len(brands),
                "Marcas registradas",
                "sell",
                "#9ca3af",
            ),
            (
                "Capacidad promedio",
                average_capacity,
                f"Con dato en {len(capacities)} vehículos",
                "battery_charging_full",
                "#22c55e",
            ),
            (
                "Vehículos activos",
                sum(bool(row.get("activo", True)) for row in rows),
                "Disponibles en el sistema",
                "check_box",
                "#22c55e",
            ),
        )

    states = [str(row.get("estado") or "").lower() for row in rows]
    return (
        (
            "Total de tarjetas",
            len(rows),
            "Inventario completo",
            "credit_card",
            "#60a5fa",
        ),
        ("Activas", states.count("activa"), "Listas para usar", "check_box", "#22c55e"),
        (
            "Bloqueadas",
            states.count("bloqueada"),
            "Acceso temporalmente bloqueado",
            "do_not_disturb_on",
            "#ef4444",
        ),
        (
            "Desactivadas",
            states.count("perdida"),
            "Sin acceso activo",
            "pause",
            "#e5e7eb",
        ),
    )


def _charger_summary_cards(rows):
    states = [str(row.get("estado_general") or "").casefold() for row in rows]
    faults = sum(
        state in {"fuera_de_servicio", "falla", "faulted"}
        or any(
            str(connector.get("estado") or "").casefold() == "faulted"
            for connector in (row.get("conectores") or [])
            if isinstance(connector, dict)
        )
        for row, state in zip(rows, states)
    )
    return (
        (
            "Total de cargadores",
            len(rows),
            "Inventario completo",
            "ev_station",
            "#f97316",
        ),
        (
            "Disponibles",
            sum(state == "disponible" for state in states),
            "Listos para operar",
            "check_box",
            "#22c55e",
        ),
        (
            "Conectados",
            sum(row.get("conectado") is True for row in rows),
            "Con comunicación activa",
            "bar_chart",
            "#60a5fa",
        ),
        (
            "Fuera de servicio / falla",
            faults,
            "Requieren atención",
            "warning",
            "#fbbf24",
        ),
    )


def _charger_communication_status(charger, now=None):
    if charger.get("conectado") is not True:
        return "Sin comunicación", "red"

    heartbeat = charger.get("ultimo_heartbeat")
    if isinstance(heartbeat, str):
        try:
            heartbeat = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
        except ValueError:
            heartbeat = None
    if not isinstance(heartbeat, datetime):
        return "Comunicación inestable", "yellow"

    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    heartbeat_age = (current_time - heartbeat).total_seconds()

    if heartbeat_age <= CHARGER_HEARTBEAT_STABLE_SECONDS:
        return "Comunicación estable", "green"
    if heartbeat_age <= CHARGER_HEARTBEAT_OFFLINE_SECONDS:
        return "Comunicación inestable", "yellow"
    return "Sin comunicación", "red"


def _render_registry_records(page_key, rows, token, api_base_url):
    is_vehicle_page = page_key == "vehiculos"
    page_class = "cc-registry-vehicles" if is_vehicle_page else "cc-registry-nfc-cards"
    summary_cards = _registry_summary_cards(page_key, rows)
    status_filter = None
    page_state = {"number": 1}
    role = (app.storage.user.get("user") or {}).get("rol")
    can_manage = role in {"superadmin", "dueno_empresa"}

    ui.add_head_html(
        """
        <style>
            .cc-registry-page {
                display: flex;
                flex-direction: column;
                gap: 20px;
                width: 100%;
                padding-top: 20px;
            }
            .cc-registry-page .cc-registry-section-title {
                margin-bottom: 10px;
                color: var(--cc-text-primary);
                font-size: 18px;
                font-weight: 700;
            }
            .q-page .cc-registry-page .cc-registry-panel {
                min-width: 0;
                gap: 0;
                padding: 20px;
                border-radius: 12px;
            }
            .cc-registry-page .cc-registry-filter-fields {
                display: grid;
                grid-template-columns: minmax(220px, 1.7fr) minmax(180px, 1fr);
                gap: 14px;
                width: 100%;
            }
            .cc-registry-page .cc-registry-search-only {
                grid-template-columns: minmax(220px, 1fr);
            }
            .cc-registry-page .cc-registry-filter-fields > * {
                min-width: 0;
                width: 100%;
            }
            .cc-registry-page .cc-registry-table-scroll {
                width: 100%;
                overflow-x: auto;
            }
            .cc-registry-page .cc-registry-table-head,
            .cc-registry-page .cc-registry-table-row {
                display: grid;
                align-items: center;
                column-gap: 8px;
                padding: 0 12px;
            }
            .cc-registry-page .cc-registry-table-head {
                min-height: 42px;
                border-bottom: 1px solid var(--cc-border);
                color: var(--cc-text-muted);
                font-size: 11px;
                font-weight: 700;
                letter-spacing: .06em;
                text-transform: uppercase;
            }
            .cc-registry-page .cc-registry-table-row {
                min-height: 56px;
                border-bottom: 1px solid var(--cc-border);
                color: var(--cc-text-secondary);
                font-size: 13px;
            }
            .cc-registry-page .cc-registry-table-row:hover {
                background: var(--cc-field);
            }
            .cc-registry-page.cc-registry-vehicles .cc-registry-table {
                min-width: 680px;
                width: 100%;
            }
            .cc-registry-page.cc-registry-vehicles .cc-registry-table-head,
            .cc-registry-page.cc-registry-vehicles .cc-registry-table-row {
                grid-template-columns:
                    minmax(115px, 1.2fr)
                    minmax(70px, .8fr)
                    minmax(90px, .9fr)
                    minmax(75px, .8fr)
                    minmax(120px, 1.1fr)
                    minmax(70px, .7fr)
                    64px;
                min-width: 680px;
            }
            .cc-registry-page.cc-registry-nfc-cards .cc-registry-table {
                min-width: 650px;
                width: 100%;
            }
            .cc-registry-page.cc-registry-nfc-cards .cc-registry-table-head,
            .cc-registry-page.cc-registry-nfc-cards .cc-registry-table-row {
                grid-template-columns:
                    minmax(120px, 1.1fr)
                    minmax(150px, 1.5fr)
                    minmax(100px, .9fr)
                    minmax(130px, 1.2fr)
                    64px;
                min-width: 650px;
            }
            .cc-registry-page .cc-registry-cell {
                min-width: 0;
                overflow-wrap: anywhere;
            }
            .cc-registry-page .cc-registry-primary {
                color: var(--cc-text-primary);
                font-weight: 600;
            }
            .cc-registry-page .cc-registry-secondary {
                color: var(--cc-text-muted);
                font-size: 11px;
            }
            .cc-registry-page .cc-registry-badge {
                display: inline-flex;
                align-items: center;
                justify-self: start;
                min-height: 25px;
                padding: 3px 10px;
                border-radius: 999px;
                font-size: 11px;
                font-weight: 600;
                white-space: nowrap;
            }
            .cc-registry-page .cc-registry-status-active {
                background: rgba(31, 166, 90, .14);
                color: #24ad62;
            }
            .cc-registry-page .cc-registry-status-blocked,
            .cc-registry-page .cc-registry-status-lost {
                background: rgba(214, 56, 47, .14);
                color: #e35b52;
            }
            .cc-registry-page .cc-registry-status-default {
                background: rgba(139, 143, 152, .15);
                color: var(--cc-text-secondary);
            }
            .cc-registry-page .cc-registry-actions {
                display: flex;
                justify-content: center;
            }
            .q-page .cc-registry-page .cc-registry-action-button {
                color: var(--cc-text-muted);
            }
            .q-menu.cc-registry-action-menu {
                border: 1px solid var(--cc-border);
                background: var(--cc-bg-card);
                color: var(--cc-text-primary);
            }
            .cc-registry-page .cc-registry-empty {
                grid-column: 1 / -1;
                color: var(--cc-text-muted);
            }
            .cc-registry-page .cc-registry-list-footer {
                padding-top: 14px;
                color: var(--cc-text-muted);
                font-size: 12px;
                text-align: center;
            }
            @media (max-width: 600px) {
                .cc-registry-page {
                    gap: 16px;
                    padding-top: 14px;
                }
                .cc-registry-page .cc-registry-filter-fields {
                    grid-template-columns: minmax(0, 1fr);
                    gap: 10px;
                }
                .q-page .cc-registry-page .cc-registry-panel {
                    padding: 14px;
                }
            }
        </style>
        """
    )

    with ui.element("div").classes(f"cc-registry-page {page_class}"):
        _render_summary_cards(summary_cards)

        ui.label(
            "Filtrar vehículos" if is_vehicle_page else "Filtrar tarjetas NFC"
        ).classes("cc-registry-section-title")
        with ui.card().classes("chargecore-card cc-registry-panel"):
            filter_class = (
                "cc-registry-filter-fields"
                if not is_vehicle_page
                else "cc-registry-filter-fields cc-registry-search-only"
            )
            with ui.element("div").classes(filter_class):
                search = ui.input(
                    "Buscar en esta lista",
                    placeholder="Escribe para filtrar los registros",
                ).props("outlined clearable")
                if not is_vehicle_page:
                    status_filter = ui.select(
                        {
                            "": "Todos los estados",
                            "activa": "Activa",
                            "bloqueada": "Bloqueada",
                            "perdida": "Perdida",
                        },
                        value="",
                        label="Estado",
                    ).props("outlined")

        ui.label(
            "Lista de vehículos" if is_vehicle_page else "Lista de tarjetas NFC"
        ).classes("cc-registry-section-title")
        with ui.card().classes("chargecore-card cc-registry-panel"):
            list_container = ui.column().classes("w-full gap-0")

    def render_filtered():
        query = (search.value or "").strip().lower()
        filtered_rows = [
            row
            for row in rows
            if (not query or query in str(row).lower())
            and (
                status_filter is None
                or not status_filter.value
                or str(row.get("estado") or "").lower() == status_filter.value
            )
        ]
        page_count = max(
            1,
            (len(filtered_rows) + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE,
        )
        page_state["number"] = min(max(page_state["number"], 1), page_count)
        start = (page_state["number"] - 1) * RECORDS_PER_PAGE
        visible_rows = filtered_rows[start : start + RECORDS_PER_PAGE]
        list_container.clear()
        with list_container:
            with ui.element("div").classes("cc-registry-table-scroll"):
                with ui.element("div").classes("cc-registry-table"):
                    headings = (
                        (
                            "Vehículo",
                            "Placa",
                            "Conector",
                            "Batería",
                            "Usuario asignado",
                            "Estado",
                            "Acciones",
                        )
                        if is_vehicle_page
                        else (
                            "UID",
                            "Usuario asignado",
                            "Estado",
                            "Fecha de registro",
                            "Acciones",
                        )
                    )
                    with ui.element("div").classes("cc-registry-table-head"):
                        for label in headings:
                            ui.label(label)
                    if not visible_rows:
                        with ui.element("div").classes("cc-registry-table-row"):
                            ui.label(
                                "No hay registros que coincidan con los filtros."
                            ).classes("cc-registry-empty")
                    for record in visible_rows:
                        with ui.element("div").classes("cc-registry-table-row"):
                            if is_vehicle_page:
                                with ui.column().classes("min-w-0 gap-0"):
                                    ui.label(
                                        " ".join(
                                            str(record.get(field) or "").strip()
                                            for field in ("marca", "modelo")
                                            if record.get(field)
                                        )
                                        or "—"
                                    ).classes("cc-registry-cell cc-registry-primary")
                                    ui.label(
                                        f"Año {record['anio']}"
                                        if record.get("anio") not in (None, "")
                                        else "Año no especificado"
                                    ).classes("cc-registry-secondary")
                                ui.label(record.get("placa") or "—").classes(
                                    "cc-registry-cell"
                                )
                                ui.label(record.get("tipo_conector") or "—").classes(
                                    "cc-registry-cell"
                                )
                                capacity = record.get("capacidad_bateria_kwh")
                                ui.label(
                                    "—"
                                    if capacity in (None, "")
                                    else f"{capacity} kWh"
                                ).classes("cc-registry-cell")
                                ui.label(
                                    record.get("usuario_nombre")
                                    or record.get("usuario_id")
                                    or "—"
                                ).classes("cc-registry-cell")
                                active = bool(record.get("activo", True))
                                status_class = (
                                    "cc-registry-status-active"
                                    if active
                                    else "cc-registry-status-blocked"
                                )
                                ui.label(
                                    "Activo" if active else "Inactivo"
                                ).classes(
                                    f"cc-registry-badge {status_class}"
                                )
                            else:
                                ui.label(record.get("uid") or "—").classes(
                                    "cc-registry-cell cc-registry-primary"
                                )
                                ui.label(
                                    record.get("usuario_nombre")
                                    or record.get("usuario_id")
                                    or "—"
                                ).classes("cc-registry-cell")
                                card_state = str(record.get("estado") or "").lower()
                                state_class = {
                                    "activa": "cc-registry-status-active",
                                    "bloqueada": "cc-registry-status-blocked",
                                    "perdida": "cc-registry-status-lost",
                                }.get(card_state, "cc-registry-status-default")
                                ui.label(
                                    {
                                        "activa": "Activa",
                                        "bloqueada": "Bloqueada",
                                        "perdida": "Perdida",
                                    }.get(card_state, str(record.get("estado") or "—"))
                                ).classes(f"cc-registry-badge {state_class}")
                                created_at = record.get("created_at")
                                ui.label(
                                    str(created_at)[:10] if created_at else "—"
                                ).classes("cc-registry-cell")

                            with ui.element("div").classes("cc-registry-actions"):
                                if can_manage:
                                    with ui.button(icon="more_vert").props(
                                        "flat round dense"
                                    ).classes("cc-registry-action-button"):
                                        with ui.menu().props(
                                            'anchor="bottom right" self="top right"'
                                        ).classes("cc-registry-action-menu"):
                                            ui.menu_item(
                                                "Editar",
                                                on_click=lambda selected=record: _edit_record(
                                                    page_key,
                                                    selected,
                                                    token,
                                                    api_base_url,
                                                ),
                                            )
                                            ui.menu_item(
                                                "Desactivar",
                                                on_click=lambda selected=record: _deactivate_record(
                                                    page_key,
                                                    selected,
                                                    token,
                                                    api_base_url,
                                                ),
                                            )
                                else:
                                    ui.label("—")
            ui.label(
                f"Mostrando {len(visible_rows)} de {len(filtered_rows)} registros"
            ).classes("cc-registry-list-footer w-full")
            if page_count > 1:
                with ui.row().classes("w-full items-center justify-center gap-2"):
                    ui.button(
                        "Anterior",
                        icon="chevron_left",
                        on_click=lambda: change_page(-1),
                    ).props("flat no-caps").set_enabled(page_state["number"] > 1)
                    ui.label(
                        f"{page_state['number']} / {page_count}"
                    ).classes("text-sm text-slate-400")
                    ui.button(
                        "Siguiente",
                        icon="chevron_right",
                        on_click=lambda: change_page(1),
                    ).props("flat no-caps").set_enabled(
                        page_state["number"] < page_count
                    )

    def change_page(offset):
        page_state["number"] += offset
        render_filtered()

    def reset_page_and_render(_):
        page_state["number"] = 1
        render_filtered()

    search.on_value_change(reset_page_and_render)
    if status_filter is not None:
        status_filter.on_value_change(reset_page_and_render)
    render_filtered()


def _render_charger_management(rows, token, api_base_url):
    _render_summary_cards(_charger_summary_cards(rows))

    with ui.card().classes("chargecore-card w-full gap-4 p-5"):
        ui.label("Filtrar cargadores").classes("text-lg font-bold text-white")
        with ui.element("div").classes(
            "grid w-full grid-cols-1 gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]"
        ):
            search = ui.input(
                "Buscar por nombre, ubicación o identificador",
                value=app.storage.user.get("charger_search", ""),
                placeholder="Ej. Estación Centro",
            ).props("outlined clearable").classes("w-full")
            state_filter = ui.select(
                {
                    "": "Todos los estados",
                    "disponible": "Disponible",
                    "ocupado": "Ocupado",
                    "mantenimiento": "Mantenimiento",
                    "fuera_de_servicio": "Fuera de servicio",
                },
                value="",
                label="Por estado",
            ).props("outlined").classes("w-full")
            connection_filter = ui.select(
                {
                    "": "Todas las conexiones",
                    "conectado": "Conectado",
                    "desconectado": "Desconectado",
                },
                value="",
                label="Conexión",
            ).props("outlined").classes("w-full")

    with ui.element("div").classes(
        "grid w-full grid-cols-1 items-start gap-4 xl:grid-cols-[320px_minmax(0,1fr)]"
    ):
        with ui.element("div"):
            list_container = ui.column().classes("w-full min-w-0")
        with ui.element("div"):
            detail_container = ui.column().classes("w-full min-w-0")
    page_state = {"number": 1}
    selected_state = {
        "id": app.storage.user.get("charger_selected_id"),
    }

    def render_filtered():
        query = (search.value or "").strip().casefold()
        selected_status = state_filter.value or ""
        selected_connection = connection_filter.value or ""
        filtered_rows = [
            row
            for row in rows
            if (
                not query
                or query
                in " ".join(
                    str(row.get(field) or "")
                    for field in ("nombre", "ubicacion", "charge_point_id")
                ).casefold()
            )
            and (
                not selected_status
                or str(row.get("estado_general") or "").casefold()
                == selected_status
            )
            and (
                not selected_connection
                or bool(row.get("conectado"))
                == (selected_connection == "conectado")
            )
        ]
        page_count = max(
            1,
            (len(filtered_rows) + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE,
        )
        page_state["number"] = min(max(page_state["number"], 1), page_count)
        start = (page_state["number"] - 1) * RECORDS_PER_PAGE
        visible_rows = filtered_rows[start : start + RECORDS_PER_PAGE]
        selected = next(
            (
                row
                for row in visible_rows
                if str(row.get("_id") or row.get("id")) == selected_state["id"]
            ),
            visible_rows[0] if visible_rows else None,
        )
        selected_state["id"] = (
            str(selected.get("_id") or selected.get("id"))
            if selected
            else None
        )
        app.storage.user["charger_selected_id"] = selected_state["id"]

        list_container.clear()
        with list_container:
            with ui.card().classes("chargecore-card w-full gap-3 p-4"):
                ui.label(f"Cargadores registrados · {len(filtered_rows)}").classes(
                    "text-base font-bold text-white"
                )
                with ui.column().classes("max-h-[280px] w-full gap-2 overflow-y-auto"):
                    for row in visible_rows:
                        row_id = str(row.get("_id") or row.get("id"))
                        active = row_id == selected_state["id"]
                        state_text = str(
                            row.get("estado_general") or "Sin estado"
                        ).replace("_", " ").capitalize()
                        with ui.button(
                            on_click=lambda item=row: select_charger(item),
                        ).props("flat no-caps").classes(
                            "w-full justify-start text-left "
                            + (
                                "border border-orange-500 bg-slate-800"
                                if active
                                else "border border-slate-700"
                            )
                        ):
                            with ui.row().classes("w-full items-center gap-2"):
                                status_label, status_color = (
                                    _charger_communication_status(row)
                                )
                                ui.icon(
                                    "bolt",
                                    color=status_color,
                                ).props("size=18").tooltip(status_label)
                                with ui.column().classes("min-w-0 flex-1 gap-0"):
                                    ui.label(
                                        row.get("nombre")
                                        or row.get("charge_point_id")
                                        or "Cargador"
                                    ).classes("truncate text-sm font-medium")
                                    ui.label(
                                        f"{row.get('charge_point_id') or 'Sin ID'} · {state_text}"
                                    ).classes("truncate text-xs text-slate-400")
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(
                        f"Página {page_state['number']} de {page_count}"
                    ).classes("text-xs text-slate-400")
                    with ui.row().classes("gap-1"):
                        ui.button(
                            icon="chevron_left",
                            on_click=lambda: change_page(-1),
                        ).props("flat round dense").set_enabled(
                            page_state["number"] > 1
                        )
                        ui.button(
                            icon="chevron_right",
                            on_click=lambda: change_page(1),
                        ).props("flat round dense").set_enabled(
                            page_state["number"] < page_count
                        )

        detail_container.clear()
        with detail_container:
            if selected is None:
                with ui.card().classes("chargecore-card w-full p-5"):
                    ui.label("No hay cargadores que coincidan con los filtros.")
                return

            role = (app.storage.user.get("user") or {}).get("rol")
            can_manage = role in {"superadmin", "dueno_empresa"}
            can_operate = role in {"superadmin", "dueno_empresa"}
            with ui.card().classes("chargecore-card w-full gap-4 p-5"):
                with ui.row().classes(
                    "w-full flex-wrap items-start justify-between gap-4"
                ):
                    with ui.column().classes("min-w-0 gap-1"):
                        ui.label(
                            selected.get("nombre")
                            or selected.get("charge_point_id")
                            or "Cargador"
                        ).classes("text-xl font-bold text-white")
                        ui.label(
                            f"Charge Point ID: {selected.get('charge_point_id') or '—'}"
                        ).classes("text-sm text-slate-400")
                    with ui.column().classes("gap-1"):
                        ui.label(
                            "Estado: "
                            + str(
                                selected.get("estado_general") or "Sin estado"
                            ).replace("_", " ")
                        ).classes("text-sm text-slate-200")
                        status_label, status_color = (
                            _charger_communication_status(selected)
                        )
                        with ui.row().classes("items-center gap-2"):
                            ui.icon(
                                "traffic",
                                color=status_color,
                            ).props("size=20").tooltip(status_label)
                            ui.label(
                                f"Comunicación OCPP: {status_label}"
                            ).classes("text-sm text-slate-200")

                with ui.element("div").classes(
                    "grid w-full grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3"
                ):
                    for label, value in (
                        ("Ubicación", selected.get("ubicacion")),
                        ("Fabricante", selected.get("fabricante")),
                        ("Firmware version", selected.get("firmware_version")),
                        ("Modelo", selected.get("modelo")),
                        (
                            "Estado general",
                            str(selected.get("estado_general") or "").replace(
                                "_", " "
                            ),
                        ),
                        ("Activo", "Sí" if selected.get("activo", True) else "No"),
                        ("Último heartbeat", selected.get("ultimo_heartbeat")),
                        ("Fecha de registro", selected.get("created_at")),
                    ):
                        with ui.column().classes("min-w-0 gap-1"):
                            ui.label(label).classes(
                                "text-xs uppercase tracking-wide text-slate-500"
                            )
                            ui.label(
                                "No disponible"
                                if value in (None, "")
                                else str(value)
                            ).classes("break-all text-sm text-slate-200")

                ui.label("Conectores").classes("text-base font-semibold text-white")
                connectors = selected.get("conectores") or []
                if connectors:
                    with ui.element("div").classes(
                        "grid w-full grid-cols-1 gap-3 sm:grid-cols-2"
                    ):
                        for connector in connectors:
                            if not isinstance(connector, dict):
                                continue
                            with ui.card().classes(
                                "chargecore-card w-full gap-2 p-4"
                            ):
                                ui.label(
                                    f"Conector {connector.get('id_conector', '—')}"
                                ).classes("font-semibold text-white")
                                ui.label(
                                    f"Estado: {connector.get('estado') or '—'}"
                                ).classes("text-sm text-slate-300")
                                ui.label(
                                    f"Tipo: {connector.get('tipo') or '—'} · "
                                    f"{connector.get('potencia_max_kw', '—')} kW"
                                ).classes("text-sm text-slate-400")
                else:
                    ui.label("No hay conectores registrados.").classes(
                        "text-sm text-slate-400"
                    )

                ui.separator()
                with ui.row().classes("w-full flex-wrap gap-2"):
                    _render_record_actions(
                        "cargadores",
                        selected,
                        token,
                        api_base_url,
                        can_manage=can_manage,
                        can_operate=can_operate,
                    )

    def select_charger(record):
        selected_state["id"] = str(record.get("_id") or record.get("id"))
        app.storage.user["charger_selected_id"] = selected_state["id"]
        render_filtered()

    def change_page(offset):
        page_state["number"] += offset
        render_filtered()

    def reset_page_and_render(_):
        page_state["number"] = 1
        app.storage.user["charger_search"] = search.value or ""
        render_filtered()

    search.on_value_change(reset_page_and_render)
    state_filter.on_value_change(reset_page_and_render)
    connection_filter.on_value_change(reset_page_and_render)
    render_filtered()


def _render_records(page_key, rows, token, api_base_url):
    if not rows:
        ui.label("No hay registros para mostrar.").classes("text-slate-400")
        return
    search = ui.input(
        "Buscar en esta lista",
        value=(
            app.storage.user.get("charger_search", "")
            if page_key == "cargadores"
            else ""
        ),
        placeholder="Escribe para filtrar los registros",
    ).props("outlined clearable").classes("w-full max-w-xl")
    filters = {}
    with ui.row().classes("w-full flex-wrap gap-3"):
        if page_key == "usuarios":
            filters["rol"] = ui.select(
                {
                    "": "Todos los roles",
                    "superadmin": "Superadministrador",
                    "dueno_empresa": "Dueño de empresa",
                    "cliente": "Cliente",
                },
                value="",
                label="Rol",
            ).props("outlined").classes("w-full max-w-xs")
            filters["activo"] = ui.select(
                {"": "Todos los estados", "true": "Activos", "false": "Inactivos"},
                value="",
                label="Estado",
            ).props("outlined").classes("w-full max-w-xs")
        elif page_key == "tarjetas":
            filters["estado"] = ui.select(
                {
                    "": "Todos los estados",
                    "activa": "Activa",
                    "bloqueada": "Bloqueada",
                    "perdida": "Perdida",
                },
                value="",
                label="Estado",
            ).props("outlined").classes("w-full max-w-xs")
    container = ui.column().classes("w-full gap-3")
    role = (app.storage.user.get("user") or {}).get("rol")
    can_manage = role in {"superadmin", "dueno_empresa"}
    can_operate = role in {"superadmin", "dueno_empresa"}
    page_state = {"number": 1}

    def render_filtered():
        if page_key == "cargadores":
            app.storage.user["charger_search"] = search.value or ""
        query = (search.value or "").strip().lower()
        filtered_rows = [
            row
            for row in rows
            if (not query or query in str(row).lower())
            and (
                not filters.get("rol")
                or not filters["rol"].value
                or row.get("rol") == filters["rol"].value
            )
            and (
                not filters.get("activo")
                or not filters["activo"].value
                or bool(row.get("activo", True))
                == (filters["activo"].value == "true")
            )
            and (
                not filters.get("estado")
                or not filters["estado"].value
                or str(row.get("estado", "")).lower()
                == filters["estado"].value
            )
        ]
        page_count = max(
            1,
            (len(filtered_rows) + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE,
        )
        page_state["number"] = min(max(page_state["number"], 1), page_count)
        start = (page_state["number"] - 1) * RECORDS_PER_PAGE
        visible_rows = filtered_rows[start : start + RECORDS_PER_PAGE]
        container.clear()
        with container:
            ui.label(
                f"{len(filtered_rows)} registros · página "
                f"{page_state['number']} de {page_count}"
            ).classes(
                "text-sm text-slate-400"
            )
            with ui.element("div").classes(
                "grid w-full grid-cols-1 gap-4 xl:grid-cols-2"
            ):
                for record in visible_rows:
                    with ui.card().classes("chargecore-card w-full gap-4 p-5"):
                        primary = next(
                            (
                                record[key]
                                for key in (
                                    "nombre",
                                    "charge_point_id",
                                    "uid",
                                    "nombre",
                                    "titulo",
                                )
                                if record.get(key)
                            ),
                            record.get("_id", "Registro"),
                        )
                        ui.label(str(primary)).classes(
                            "text-lg font-bold text-white"
                        )
                        values = [
                            (key, value)
                            for key, value in record.items()
                            if key not in {"_id", "password", "ocpp_password_hash"}
                        ][:12]
                        with ui.element("div").classes(
                            "grid w-full grid-cols-1 gap-2 sm:grid-cols-2"
                        ):
                            for key, value in values:
                                text = (
                                    str(value)
                                    if not isinstance(value, (dict, list))
                                    else str(value)
                                )
                                with ui.column().classes("min-w-0 gap-1"):
                                    ui.label(key.replace("_", " ").capitalize()).classes(
                                        "text-xs uppercase tracking-wide text-slate-500"
                                    )
                                    ui.label(text[:200]).classes(
                                        "break-all text-sm text-slate-200"
                                    )
                        with ui.row().classes("w-full flex-wrap justify-end gap-2"):
                            _render_record_actions(
                                page_key,
                                record,
                                token,
                                api_base_url,
                                can_manage=can_manage,
                                can_operate=can_operate,
                            )
            with ui.row().classes("w-full items-center justify-between"):
                ui.button(
                    "Anterior",
                    icon="chevron_left",
                    on_click=lambda: change_page(-1),
                ).props("flat no-caps").set_enabled(page_state["number"] > 1)
                ui.button(
                    "Siguiente",
                    icon="chevron_right",
                    on_click=lambda: change_page(1),
                ).props("flat no-caps").set_enabled(
                    page_state["number"] < page_count
                )

    def change_page(offset):
        page_state["number"] += offset
        render_filtered()

    def reset_page_and_render(_):
        page_state["number"] = 1
        render_filtered()

    search.on_value_change(reset_page_and_render)
    for control in filters.values():
        control.on_value_change(reset_page_and_render)
    render_filtered()


def _render_dashboard(token, api_base_url):
    is_dark_theme = app.storage.user.get("color_theme") == "dark"
    chart_text_color = "#f3f1ee" if is_dark_theme else "#1c1c1c"
    chart_muted_color = "#a8a49e" if is_dark_theme else "#7a746b"
    chart_border_color = "#3a3430" if is_dark_theme else "#eadfce"
    chart_grid_color = "#3a3430" if is_dark_theme else "#f7f3ec"
    chart_tooltip_background = "#1f1f1f" if is_dark_theme else "#ffffff"
    chart_colors = ["#ee5a24", "#2f6fe0", "#1fa65a", "#e8a010"]
    ui.add_head_html(
        """
        <style>
            .q-page:has(.cc-dashboard) {
                background: var(--cc-bg-main) !important;
                color: var(--cc-text-primary) !important;
            }
            body:has(.cc-dashboard) .chargecore-header {
                background: var(--cc-bg-main) !important;
                color: var(--cc-text-primary) !important;
                box-shadow: none;
            }
            .cc-dashboard {
                box-sizing: border-box;
                max-width: none !important;
                min-height: calc(100vh - 64px);
                gap: 14px !important;
                padding: 28px clamp(16px, 3vw, 40px) !important;
                color: var(--cc-text-primary);
                font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
            }
            .cc-dashboard .text-white { color: var(--cc-text-primary) !important; }
            .cc-dashboard .text-slate-400,
            .cc-dashboard .text-slate-500 { color: var(--cc-text-muted) !important; }
            .cc-dashboard .text-slate-200,
            .cc-dashboard .text-slate-300 { color: var(--cc-text-secondary) !important; }
            .cc-dashboard .text-orange-400 { color: var(--cc-primary) !important; }
            .cc-dashboard .q-card.cc-dashboard-card {
                box-sizing: border-box;
                border: 1px solid var(--cc-border) !important;
                border-radius: 14px !important;
                background: var(--cc-bg-card) !important;
                color: var(--cc-text-primary) !important;
                box-shadow: 0 6px 20px rgba(29, 41, 57, .06) !important;
            }
            .cc-dashboard .q-card.cc-dashboard-kpi {
                position: relative;
                min-height: 132px;
                justify-content: flex-end;
                gap: 4px !important;
                padding: 16px !important;
                transition: transform 180ms ease, box-shadow 180ms ease;
            }
            .cc-dashboard .q-card.cc-dashboard-kpi:hover,
            .cc-dashboard .q-card.cc-dashboard-card:hover {
                transform: translateY(-2px);
                box-shadow: 0 10px 24px rgba(29, 41, 57, .12) !important;
            }
            .cc-dashboard .cc-dashboard-kpi-icon {
                display: grid;
                width: 38px;
                height: 38px;
                margin-bottom: 5px;
                place-items: center;
                border-radius: 11px;
                background: color-mix(in srgb, var(--cc-primary) 10%, var(--cc-bg-card));
                color: var(--cc-primary);
                font-size: 19px;
            }
            .cc-dashboard .cc-dashboard-kpi-label {
                color: var(--cc-text-muted) !important;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: .06em;
                text-transform: uppercase;
            }
            .cc-dashboard .cc-dashboard-kpi-value {
                color: var(--cc-text-primary) !important;
                font-size: 28px;
                font-weight: 800;
                letter-spacing: -.02em;
                line-height: 1.15;
                overflow-wrap: anywhere;
            }
            .cc-dashboard .q-card.cc-dashboard-kpi-hero {
                border-color: var(--cc-primary) !important;
                background: var(--cc-primary) !important;
                color: #fff !important;
            }
            .cc-dashboard .cc-dashboard-kpi-hero .cc-dashboard-kpi-icon {
                background: rgba(255, 255, 255, .22);
                color: #fff;
            }
            .cc-dashboard .cc-dashboard-kpi-hero .cc-dashboard-kpi-label,
            .cc-dashboard .cc-dashboard-kpi-hero .cc-dashboard-kpi-value {
                color: #fff !important;
            }
            .cc-dashboard .cc-dashboard-section-title {
                position: relative;
                margin: 12px 0 -2px;
                padding-left: 12px;
                color: var(--cc-text-primary) !important;
                font-size: 16px;
                font-weight: 750;
            }
            .cc-dashboard .cc-dashboard-section-title:before {
                position: absolute;
                top: 2px;
                bottom: 2px;
                left: 0;
                width: 4px;
                border-radius: 2px;
                background: var(--cc-primary);
                content: "";
            }
            .cc-dashboard .cc-dashboard-charts .q-card,
            .cc-dashboard .cc-dashboard-tables .q-card {
                min-width: 0;
            }
            .cc-dashboard .q-table,
            .cc-dashboard .q-table__container,
            .cc-dashboard .q-table thead,
            .cc-dashboard .q-table tbody tr {
                background: var(--cc-bg-card) !important;
                color: var(--cc-text-primary) !important;
            }
            .cc-dashboard .q-table th {
                border-color: var(--cc-border) !important;
                background: var(--cc-bg-card) !important;
                color: var(--cc-text-muted) !important;
            }
            .cc-dashboard .q-table td { border-color: var(--cc-border) !important; }
            .cc-dashboard .q-table tbody tr:hover {
                background: color-mix(in srgb, var(--cc-primary) 6%, var(--cc-bg-card)) !important;
            }
            .cc-dashboard .q-table__bottom { color: var(--cc-text-muted); }
            .cc-dashboard .q-btn--flat:not(.chargecore-nav-item) { color: var(--cc-primary) !important; }
            .cc-dashboard .echarts {
                border-radius: 10px;
            }
            @media (max-width: 700px) {
                .cc-dashboard { padding: 18px 14px !important; }
                .cc-dashboard .q-card.cc-dashboard-kpi { min-height: 116px; }
            }
            @media (prefers-reduced-motion: reduce) {
                .cc-dashboard .q-card.cc-dashboard-kpi,
                .cc-dashboard .q-card.cc-dashboard-card { transition: none; }
                .cc-dashboard .q-card.cc-dashboard-kpi:hover,
                .cc-dashboard .q-card.cc-dashboard-card:hover { transform: none; }
            }
        </style>
        """
    )
    ui.add_head_html(
        """
        <style>
            .cc-dashboard {
                box-sizing: border-box;
                width: 100%;
                max-width: 1280px !important;
                gap: 0 !important;
                padding: 28px clamp(16px, 3vw, 40px) !important;
                color: var(--cc-text-primary);
                font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
            }
            .cc-dashboard-top {
                display: flex;
                align-items: flex-start;
                justify-content: space-between;
                gap: 16px;
                flex-wrap: wrap;
                margin-bottom: 22px;
            }
            .cc-dashboard-title {
                margin: 0;
                color: var(--cc-text-primary);
                font-size: clamp(26px, 3vw, 34px);
                font-weight: 700;
                letter-spacing: -.02em;
                line-height: 1.2;
            }
            .cc-dashboard-subtitle {
                margin-top: 4px;
                color: var(--cc-text-muted);
                font-size: 14px;
            }
            .cc-dashboard-actions { display: flex; align-items: center; gap: 10px; }
            .cc-dashboard-live {
                display: inline-flex;
                align-items: center;
                gap: 8px;
                border: 1px solid var(--cc-border);
                border-radius: 99px;
                background: var(--cc-bg-card);
                padding: 8px 12px;
                color: var(--cc-text-primary);
                font-size: 12px;
                font-weight: 600;
            }
            .cc-dashboard-live-dot {
                width: 8px;
                height: 8px;
                border-radius: 50%;
                background: var(--cc-success);
                animation: cc-dashboard-pulse 1.6s infinite;
            }
            @keyframes cc-dashboard-pulse {
                50% { opacity: .3; transform: scale(1.5); }
            }
            .cc-dashboard .cc-dashboard-refresh {
                min-height: 38px;
                border-radius: 8px;
                background: var(--cc-text-primary) !important;
                color: var(--cc-bg-card) !important;
                font-size: 13px;
                font-weight: 700;
            }
            .cc-dashboard .cc-dashboard-section-title {
                display: flex;
                align-items: center;
                gap: 8px;
                margin: 28px 0 12px;
                padding: 0;
                color: var(--cc-text-primary) !important;
                font-size: 16px;
                font-weight: 700;
            }
            .cc-dashboard .cc-dashboard-section-title:before {
                position: static;
                width: 4px;
                height: 18px;
                flex: 0 0 4px;
                border-radius: 2px;
                background: var(--cc-primary);
                content: "";
            }
            .cc-dashboard .cc-dashboard-grid-four {
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 14px;
            }
            .cc-dashboard .cc-dashboard-grid-two {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 14px;
            }
            .cc-dashboard .q-card.cc-dashboard-card {
                box-sizing: border-box;
                min-width: 0;
                border: 1px solid var(--cc-border) !important;
                border-radius: 14px !important;
                background: var(--cc-bg-card) !important;
                padding: 18px !important;
                color: var(--cc-text-primary) !important;
                box-shadow: 0 6px 20px rgba(120, 80, 30, .06) !important;
            }
            .cc-dashboard .q-card.cc-dashboard-kpi-hero {
                border-color: var(--cc-primary) !important;
                background: var(--cc-primary) !important;
                color: #fff !important;
            }
            .cc-dashboard .q-card.cc-dashboard-kpi {
                min-height: 150px;
                gap: 0 !important;
                transition: transform 180ms ease, box-shadow 180ms ease;
            }
            .cc-dashboard .q-card.cc-dashboard-kpi:hover,
            .cc-dashboard .q-card.cc-dashboard-card:hover {
                transform: translateY(-2px);
                box-shadow: 0 10px 24px rgba(120, 80, 30, .11) !important;
            }
            .cc-dashboard .cc-dashboard-kpi-icon {
                display: grid;
                width: 38px;
                height: 38px;
                margin-bottom: 12px;
                place-items: center;
                border-radius: 11px;
                background: var(--cc-field);
                color: var(--cc-primary);
                font-size: 18px;
            }
            .cc-dashboard .cc-dashboard-kpi-label {
                color: var(--cc-text-muted) !important;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: .06em;
                text-transform: uppercase;
            }
            .cc-dashboard .cc-dashboard-kpi-value {
                color: var(--cc-text-primary) !important;
                font-size: 32px;
                font-weight: 700;
                letter-spacing: -.02em;
                line-height: 1.2;
                overflow-wrap: anywhere;
            }
            .cc-dashboard .cc-dashboard-kpi-hero .cc-dashboard-kpi-icon {
                background: rgba(255, 255, 255, .22);
                color: #fff;
            }
            .cc-dashboard .cc-dashboard-kpi-hero .cc-dashboard-kpi-label,
            .cc-dashboard .cc-dashboard-kpi-hero .cc-dashboard-kpi-value,
            .cc-dashboard .cc-dashboard-kpi-hero .cc-dashboard-kpi-note,
            .cc-dashboard .cc-dashboard-kpi-hero .cc-dashboard-kpi-unit { color: #fff !important; }
            .cc-dashboard .cc-dashboard-summary-grid .q-card {
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                gap: 6px;
                min-height: 94px;
                padding: 14px !important;
                text-align: center;
            }
            .cc-dashboard-summary-grid .q-card > .nicegui-column {
                align-items: center;
                width: 100%;
            }
            .cc-dashboard-summary-icon { color: var(--cc-primary); font-size: 18px; }
            .cc-dashboard-summary-grid .cc-dashboard-summary-value {
                font-size: 20px;
                font-weight: 700;
                line-height: 1;
                text-align: center;
            }
            .cc-dashboard-summary-label,
            .cc-dashboard-muted { color: var(--cc-text-muted); font-size: 12px; }
            .cc-dashboard-summary-grid .cc-dashboard-summary-label {
                text-align: center;
            }
            .cc-dashboard-status-head {
                display: flex;
                align-items: baseline;
                justify-content: space-between;
                gap: 12px;
            }
            .cc-dashboard-status-strip {
                display: flex;
                height: 14px;
                overflow: hidden;
                margin: 6px 0 14px;
                border-radius: 99px;
                background: var(--cc-field);
            }
            .cc-dashboard-status-strip > div {
                display: block;
                height: 100%;
                min-width: 1px;
            }
            .cc-dashboard-status-legend { display: flex; flex-wrap: wrap; gap: 8px; }
            .cc-dashboard-status-item {
                display: inline-flex;
                align-items: center;
                gap: 6px;
                border: 1px solid var(--cc-border);
                border-radius: 99px;
                padding: 5px 10px;
                color: var(--cc-text-primary);
                font-size: 12px;
            }
            .cc-dashboard-status-dot { width: 9px; height: 9px; border-radius: 50%; }
            .cc-dashboard-panel-title { color: var(--cc-text-primary); font-size: 15px; font-weight: 700; }
            .cc-dashboard-panel-subtitle { margin-top: 2px; color: var(--cc-text-muted); font-size: 12px; }
            .cc-dashboard-history-filters {
                display: flex;
                align-items: flex-end;
                flex-wrap: wrap;
                gap: 10px;
                margin: 12px 0 6px;
            }
            .cc-dashboard-history-filters .q-field { width: 170px; }
            .cc-dashboard-range-buttons { display: flex; flex-wrap: wrap; gap: 6px; }
            .cc-dashboard-range-buttons .q-btn {
                min-height: 36px;
                border: 1px solid var(--cc-border);
                background: var(--cc-bg-card);
                color: var(--cc-text-primary);
                font-size: 13px;
                font-weight: 600;
            }
            .cc-dashboard-history-summary {
                display: flex;
                flex-wrap: wrap;
                gap: 22px;
                margin: 6px 0;
                color: var(--cc-text-muted);
                font-size: 12px;
            }
            .cc-dashboard-history-summary b { display: block; color: var(--cc-text-primary); font-size: 20px; }
            .cc-dashboard-chart { width: 100%; min-width: 0; height: 200px; }
            .cc-dashboard-history-chart { height: 360px; }
            .cc-dashboard-chart-secondary { height: 220px; }
            .cc-dashboard-energy-list { display: flex; flex-direction: column; align-items: stretch; gap: 13px; margin-top: 14px; }
            .cc-dashboard-energy-row { display: flex; flex-direction: column; align-self: stretch; width: 100%; gap: 6px; }
            .cc-dashboard-energy-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; }
            .cc-dashboard-energy-name { overflow: hidden; color: var(--cc-text-primary); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
            .cc-dashboard-energy-track { width: 100%; height: 13px; overflow: hidden; border-radius: 99px; background: var(--cc-field); }
            .cc-dashboard-energy-fill { height: 100%; border-radius: inherit; background: var(--cc-primary); }
            .cc-dashboard-energy-value { color: var(--cc-text-primary); font-size: 12px; font-weight: 700; white-space: nowrap; }
            .cc-dashboard-energy-empty { color: var(--cc-text-muted); font-weight: 400; }
            .cc-dashboard-table-wrap { width: 100%; overflow-x: auto; }
            .cc-dashboard-list-footer {
                display: grid;
                grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
                align-items: center;
                gap: 12px;
                margin-top: 10px;
                border-top: 1px solid var(--cc-border);
                padding-top: 10px;
            }
            .cc-dashboard-list-nav { min-width: 0; align-items: center; }
            .cc-dashboard-list-summary { align-items: center; gap: 4px; text-align: center; }
            .cc-dashboard-page-button {
                min-height: 36px !important;
                border-color: var(--cc-border) !important;
                border-radius: 9px !important;
                color: var(--cc-text-primary) !important;
            }
            .cc-dashboard .q-table,
            .cc-dashboard .q-table__container,
            .cc-dashboard .q-table thead,
            .cc-dashboard .q-table tbody tr { background: var(--cc-bg-card) !important; color: var(--cc-text-primary) !important; }
            .cc-dashboard .q-table th { background: var(--cc-bg-card) !important; color: var(--cc-text-muted) !important; font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; }
            .cc-dashboard .q-table th,
            .cc-dashboard .q-table td { border-color: var(--cc-border) !important; white-space: nowrap; }
            .cc-dashboard .q-table td { padding: 11px 10px; }
            .cc-dashboard .q-table tbody tr:hover { background: var(--cc-field) !important; }
            .cc-dashboard-alert-badge {
                display: inline-flex;
                align-items: center;
                gap: 7px;
                border-radius: 9999px;
                background: var(--cc-field);
                padding: 4px 10px;
                color: var(--cc-text-primary);
                font-size: 12px;
                font-weight: 600;
            }
            .cc-dashboard-alert-dot { width: 8px; height: 8px; border-radius: 50%; }
            .cc-dashboard-alert-warning .cc-dashboard-alert-dot { background: #f59e0b; }
            .cc-dashboard-alert-error .cc-dashboard-alert-dot { background: #ef4444; }
            .cc-dashboard-alert-info .cc-dashboard-alert-dot { background: #3b82f6; }
            @media (max-width: 1100px) {
                .cc-dashboard .cc-dashboard-grid-four { grid-template-columns: repeat(2, minmax(0, 1fr)); }
                .cc-dashboard .cc-dashboard-grid-two { grid-template-columns: 1fr; }
            }
            @media (max-width: 480px) {
                .cc-dashboard .cc-dashboard-grid-four { grid-template-columns: 1fr; }
                .cc-dashboard .cc-dashboard-history-filters .q-field { width: 100%; }
                .cc-dashboard .cc-dashboard-list-footer { gap: 6px; }
                .cc-dashboard .cc-dashboard-page-button { padding: 0 7px; font-size: 11px; }
                .cc-dashboard .cc-dashboard-history-chart { height: 280px; }
            }
            @media (prefers-reduced-motion: reduce) {
                .cc-dashboard .cc-dashboard-kpi,
                .cc-dashboard .cc-dashboard-card { transition: none; }
                .cc-dashboard .cc-dashboard-live-dot { animation: none; }
                .cc-dashboard .cc-dashboard-kpi:hover,
                .cc-dashboard .cc-dashboard-card:hover { transform: none; }
            }
        </style>
        """
    )
    with ui.row().classes("cc-dashboard-top w-full"):
        with ui.column().classes("gap-1"):
            ui.label("Panel de control").classes("cc-dashboard-title")
            ui.label(
                "Centro de monitoreo en tiempo real del sistema ChargeCore"
            ).classes("cc-dashboard-subtitle")
        with ui.row().classes("cc-dashboard-actions"):
            with ui.row().classes("cc-dashboard-live"):
                ui.element("span").classes("cc-dashboard-live-dot")
                ui.label("En vivo ·")
                clock = ui.label(datetime.now().strftime("%H:%M:%S"))
            ui.button(
                "Actualizar",
                icon="refresh",
                on_click=ui.navigate.reload,
            ).props("no-caps").classes("cc-dashboard-refresh")
    ui.timer(
        1,
        lambda: clock.set_text(datetime.now().strftime("%H:%M:%S")),
        immediate=False,
    )
    initial_end_date = date.today()
    initial_start_date = initial_end_date - timedelta(days=29)
    history_params = {
        "estado": "finalizada",
        "limit": 500,
        "fecha_inicio": initial_start_date.isoformat(),
        "fecha_fin": initial_end_date.isoformat(),
    }
    requests_to_make = (
        ("kpis", "/dashboard/kpi"),
        ("weekly_consumption", "/dashboard/grafica-consumo-semanal"),
        ("daily_consumption", "/dashboard/grafica-consumo-diario"),
        ("sessions_by_charger", "/dashboard/sesiones-por-cargador"),
        ("energy_by_charger", "/dashboard/energia-por-cargador"),
        ("usage_trend", "/dashboard/tendencia-uso"),
        ("active_sessions", "/sesiones/activas"),
        ("pending_rewards", "/dashboard/recompensas-pendientes-resumen"),
        ("recent_alerts", "/dashboard/alertas-recientes"),
        ("recent_events", "/dashboard/eventos-ocpp-recientes"),
        ("chargers", "/cargadores/"),
        ("history_sessions", "/sesiones/"),
    )
    dashboard_list_params = {
        "pending_rewards": {"limit": DASHBOARD_EXPANDED_ROWS},
        "recent_alerts": {"limit": DASHBOARD_EXPANDED_ROWS},
        "recent_events": {"limit": DASHBOARD_EXPANDED_ROWS},
    }
    with ThreadPoolExecutor(max_workers=len(requests_to_make)) as executor:
        futures = {
            key: executor.submit(
                api_get,
                endpoint,
                params=(
                    history_params
                    if key == "history_sessions"
                    else dashboard_list_params.get(key)
                ),
                token=token,
                base_url=api_base_url,
            )
            for key, endpoint in requests_to_make
        }
        results = {key: future.result() for key, future in futures.items()}

    expired_session = next(
        (
            result
            for result in results.values()
            if isinstance(result, dict) and result.get("_status_code") == 401
        ),
        None,
    )
    if expired_session is not None:
        _show_api_error(expired_session)
        return

    kpis = results["kpis"]
    if isinstance(kpis, dict) and "error" in kpis:
        _show_api_error(kpis, "No se pudieron cargar los indicadores.")
    elif not isinstance(kpis, dict):
        show_error("La respuesta de indicadores no tiene el formato esperado.")
        kpis = {}
    elif not kpis:
        show_error("No se recibieron indicadores del sistema.")

    def format_kpi(key):
        value = kpis.get(key)
        if value is None or isinstance(value, bool):
            return "—"
        if isinstance(value, (str, int, float)):
            return str(value)
        return "—"

    def render_kpi(key, label, description, icon, hero=False, unit=None):
        card_classes = "chargecore-card cc-dashboard-card cc-dashboard-kpi"
        if hero:
            card_classes += " cc-dashboard-kpi-hero"
        with ui.card().classes(card_classes):
            with ui.element("span").classes("cc-dashboard-kpi-icon"):
                ui.icon(icon)
            ui.label(label).classes("cc-dashboard-kpi-label")
            with ui.row().classes("items-baseline gap-1"):
                ui.label(format_kpi(key)).classes("cc-dashboard-kpi-value")
                if unit:
                    ui.label(unit).classes("cc-dashboard-kpi-unit")
            ui.label(description).classes("cc-dashboard-kpi-note")

    with ui.element("div").classes("cc-dashboard-grid-four w-full"):
        render_kpi(
            "energia_total_kwh",
            "Energía total",
            "Acumulada en todas las estaciones",
            "bolt",
            hero=True,
            unit="kWh",
        )
        render_kpi("sesiones_activas", "Sesiones activas", "Cargando ahora", "timer")
        render_kpi("cargas_hoy", "Cargas hoy", "Sesiones iniciadas hoy", "today")
        render_kpi(
            "alertas_recientes",
            "Alertas recientes",
            "Cargadores desconectados",
            "warning",
        )

    ui.label("Estado de la red de cargadores").classes(
        "cc-dashboard-section-title"
    )
    with ui.card().classes("chargecore-card cc-dashboard-card w-full"):
        chargers = results["chargers"]
        if isinstance(chargers, dict) and chargers.get("error"):
            _show_api_error(
                chargers, "No se pudo cargar el estado de los cargadores."
            )
        elif not isinstance(chargers, list):
            show_error("La respuesta de cargadores no tiene el formato esperado.")
        else:
            state_specs = (
                ("ocupado", "Ocupado", "#2f6fe0"),
                ("disponible", "Disponible", "#1fa65a"),
                ("preparando", "Preparando", "#e8a010"),
                ("mantenimiento", "Mantenimiento", "#e07b1a"),
                ("fuera_de_servicio", "Fuera de servicio", "#8b8f98"),
                ("falla", "Falla", "#d6382f"),
                ("desconectado", "Desconectado", "#b8bcc6"),
                ("sin_estado", "Sin estado", "#8b8f98"),
            )
            status_counts = {key: 0 for key, _, _ in state_specs}
            for charger in chargers:
                if not isinstance(charger, dict):
                    continue
                general_state = str(charger.get("estado_general") or "").casefold()
                connectors = charger.get("conectores")
                connector_states = (
                    [
                        str(connector.get("estado") or "").casefold()
                        for connector in connectors
                        if isinstance(connector, dict)
                    ]
                    if isinstance(connectors, list)
                    else []
                )
                if charger.get("conectado") is False:
                    state_key = "desconectado"
                elif general_state in {"mantenimiento", "maintenance"}:
                    state_key = "mantenimiento"
                elif general_state in {"fuera_de_servicio", "unavailable"}:
                    state_key = "fuera_de_servicio"
                elif general_state in {"ocupado", "charging"} or any(
                    state in {"charging", "ocupado"} for state in connector_states
                ):
                    state_key = "ocupado"
                elif any(
                    state in {"preparing", "preparando"}
                    for state in connector_states
                ):
                    state_key = "preparando"
                elif any(
                    state in {"faulted", "falla", "error"}
                    for state in connector_states
                ):
                    state_key = "falla"
                elif general_state in {"disponible", "available"} or charger.get(
                    "conectado"
                ) is True:
                    state_key = "disponible"
                else:
                    state_key = "sin_estado"
                status_counts[state_key] += 1
            with ui.row().classes("cc-dashboard-status-head w-full"):
                ui.label(
                    f"{format_kpi('cargadores_activos')} cargadores"
                ).classes("font-bold")
                ui.label("Distribución por estado").classes("cc-dashboard-muted")
            total_chargers = sum(status_counts.values())
            if total_chargers:
                with ui.element("div").classes("cc-dashboard-status-strip"):
                    for key, _, color in state_specs:
                        if status_counts[key]:
                            ui.element("div").style(
                                f"flex:{status_counts[key]} 1 0%;"
                                f"background-color:{color}"
                            )
                with ui.element("div").classes("cc-dashboard-status-legend"):
                    for key, label, color in state_specs:
                        if status_counts[key]:
                            with ui.element("span").classes(
                                "cc-dashboard-status-item"
                            ):
                                ui.element("i").classes(
                                    "cc-dashboard-status-dot"
                                ).style(f"background:{color}")
                                ui.label(f"{label} · {status_counts[key]}")
            else:
                ui.label("No hay cargadores activos para mostrar.").classes(
                    "cc-dashboard-muted"
                )

    ui.label("Resumen del sistema").classes("cc-dashboard-section-title")
    with ui.element("div").classes(
        "cc-dashboard-grid-four cc-dashboard-summary-grid w-full"
    ):
        for key, label, icon in (
            ("total_usuarios", "Usuarios registrados", "group"),
            ("total_vehiculos", "Vehículos registrados", "directions_car"),
            ("recompensas_pendientes", "Recompensas pendientes", "redeem"),
            ("cargadores_conectados", "Cargadores conectados", "power"),
        ):
            with ui.card().classes("chargecore-card cc-dashboard-card"):
                ui.icon(icon).classes("cc-dashboard-summary-icon")
                with ui.column().classes("gap-0"):
                    ui.label(format_kpi(key)).classes(
                        "cc-dashboard-summary-value"
                    )
                    ui.label(label).classes("cc-dashboard-summary-label")

    ui.label("Análisis de consumo").classes("cc-dashboard-section-title")
    consumption = results["weekly_consumption"]
    with ui.card().classes(
        "chargecore-card cc-dashboard-card cc-dashboard-weekly w-full gap-4 p-5"
    ):
        ui.label("Historial de consumo").classes("cc-dashboard-panel-title")
        ui.label("Consumo por día · filtra por rango de fechas").classes(
            "cc-dashboard-panel-subtitle"
        )
        with ui.row().classes("cc-dashboard-history-filters w-full"):
            history_start = ui.input(
                "Desde", value=initial_start_date.isoformat()
            ).props("type=date outlined")
            history_end = ui.input(
                "Hasta", value=initial_end_date.isoformat()
            ).props("type=date outlined")
            with ui.row().classes("cc-dashboard-range-buttons"):
                ui.button(
                    "Buscar",
                    icon="search",
                    on_click=lambda: load_history(
                        history_start.value, history_end.value
                    ),
                ).props("no-caps").classes("chargecore-primary")
                for days in (7, 30, 90):
                    ui.button(
                        f"{days} días",
                        on_click=lambda days=days: set_history_range(days),
                    ).props("no-caps")
        history_results = ui.column().classes("w-full gap-2")

    def render_history_rows(session_result, start_day, end_day):
        history_results.clear()
        with history_results:
            if isinstance(session_result, dict) and session_result.get("error"):
                _show_api_error(session_result)
                return
            if not isinstance(session_result, list):
                show_error("La respuesta del historial no tiene el formato esperado.")
                return
            if not all(isinstance(item, dict) for item in session_result):
                show_error("El historial contiene registros inválidos.")
                return
            grouped = {}
            missing_energy = False
            for item in session_result:
                started = item.get("fecha_inicio")
                if not started:
                    continue
                try:
                    started_at = datetime.fromisoformat(
                        str(started).replace("Z", "+00:00")
                    )
                except ValueError:
                    continue
                day = started_at.date().isoformat()
                group = grouped.setdefault(
                    day, {"sessions": 0, "energy": 0.0, "energy_sessions": 0}
                )
                group["sessions"] += 1
                energy = item.get("energia_consumida_kwh")
                if (
                    isinstance(energy, (int, float))
                    and not isinstance(energy, bool)
                    and math.isfinite(energy)
                ):
                    group["energy"] += energy
                    group["energy_sessions"] += 1
                else:
                    missing_energy = True
            if session_result and not grouped:
                show_error("No se pudieron interpretar las fechas del historial.")
                return
            ordered_days = sorted(grouped)
            chart_days, chart_values = _dashboard_history_chart_data(
                grouped, start_day, end_day
            )
            session_count = sum(grouped[day]["sessions"] for day in ordered_days)
            measured_sessions = sum(
                grouped[day]["energy_sessions"] for day in ordered_days
            )
            total_energy = sum(grouped[day]["energy"] for day in ordered_days)
            with ui.row().classes("cc-dashboard-history-summary"):
                for label, value in (
                    ("Sesiones", str(session_count)),
                    ("Energía", f"{total_energy:.2f} kWh"),
                    (
                        "Prom. por sesión",
                        f"{total_energy / measured_sessions:.2f} kWh"
                        if measured_sessions
                        else "—",
                    ),
                    ("Cargas finalizadas total", format_kpi("total_cargas")),
                ):
                    with ui.column().classes("gap-0"):
                        ui.label(value).classes("font-bold")
                        ui.label(label)
            if missing_energy:
                ui.label(
                    "Las sesiones sin energía disponible no se representan como cero."
                ).classes("cc-dashboard-muted")
            if not grouped:
                ui.label("No hay registros de consumo en este rango.").classes(
                    "cc-dashboard-muted"
                )
            ui.echart(
                {
                    "tooltip": {
                        "trigger": "axis",
                        "backgroundColor": chart_tooltip_background,
                        "borderColor": chart_border_color,
                        "textStyle": {"color": chart_text_color},
                    },
                    "xAxis": {
                        "type": "category",
                        "data": [f"{day[8:10]}/{day[5:7]}" for day in chart_days],
                        "axisLabel": {
                            "color": chart_muted_color,
                            "interval": max(0, math.ceil(len(chart_days) / 15) - 1),
                        },
                        "axisLine": {"lineStyle": {"color": chart_border_color}},
                    },
                    "yAxis": {
                        "type": "value",
                        "name": "kWh",
                        **(
                            {"min": 0, "max": 1, "interval": 0.2}
                            if not grouped
                            else {}
                        ),
                        "axisLabel": {"color": chart_muted_color},
                        "splitNumber": 4,
                        "splitLine": {
                            "lineStyle": {"color": chart_grid_color}
                        },
                    },
                    "series": [
                        {
                            "name": "Energía consumida",
                            "type": "bar",
                            "data": chart_values,
                            "itemStyle": {
                                "color": chart_colors[0],
                                "borderRadius": [5, 5, 0, 0],
                            },
                        }
                    ],
                    "textStyle": {"color": chart_text_color},
                    "axisPointer": {
                        "lineStyle": {"color": chart_colors[0]}
                    },
                    "backgroundColor": "transparent",
                }
            ).classes("cc-dashboard-chart cc-dashboard-history-chart")
            if grouped:
                table_rows = [
                    {
                        "fecha": day,
                        "sesiones": grouped[day]["sessions"],
                        "energia": (
                            f'{grouped[day]["energy"]:.2f} kWh'
                            if grouped[day]["energy_sessions"]
                            else "—"
                        ),
                        "promedio": (
                            f'{grouped[day]["energy"] / grouped[day]["energy_sessions"]:.2f} kWh'
                            if grouped[day]["energy_sessions"]
                            else "—"
                        ),
                    }
                    for day in reversed(ordered_days)
                ]
                def render_consumption_table(visible_rows):
                    with ui.element("div").classes("cc-dashboard-table-wrap"):
                        ui.table(
                            columns=[
                                {"name": "fecha", "label": "Fecha", "field": "fecha"},
                                {
                                    "name": "sesiones",
                                    "label": "Sesiones",
                                    "field": "sesiones",
                                },
                                {
                                    "name": "energia",
                                    "label": "Energía",
                                    "field": "energia",
                                },
                                {
                                    "name": "promedio",
                                    "label": "Prom. por sesión",
                                    "field": "promedio",
                                },
                            ],
                            rows=visible_rows,
                            row_key="fecha",
                        ).props("dense flat").classes("w-full")

                _render_expandable_dashboard_rows(
                    table_rows,
                    render_consumption_table,
                )
                ui.label("Últimas sesiones finalizadas").classes(
                    "cc-dashboard-panel-title"
                )
                recent_sessions = session_result
                session_rows = []
                for index, item in enumerate(recent_sessions):
                    energy_value = item.get("energia_consumida_kwh")
                    duration_value = item.get("duracion_minutos")
                    session_rows.append(
                        {
                            "fila": str(item.get("_id") or index),
                            "cargador": item.get("cargador_nombre")
                            or item.get("charge_point_id")
                            or "—",
                            "conector": item.get("connector_id")
                            if item.get("connector_id") is not None
                            else "—",
                            "inicio": item.get("fecha_inicio") or "—",
                            "energia": (
                                f"{energy_value:.2f} kWh"
                                if isinstance(energy_value, (int, float))
                                and not isinstance(energy_value, bool)
                                and math.isfinite(energy_value)
                                else "—"
                            ),
                            "duracion": (
                                f"{duration_value} min"
                                if isinstance(duration_value, (int, float))
                                and not isinstance(duration_value, bool)
                                else "—"
                            ),
                            "usuario": item.get("usuario_nombre") or "Sin usuario",
                            "beneficio": item.get("beneficio_aplicado") or "Ninguno",
                        }
                    )
                if session_rows:
                    def render_recent_sessions_table(visible_rows):
                        with ui.element("div").classes(
                            "cc-dashboard-table-wrap"
                        ):
                            ui.table(
                                columns=[
                                    {"name": "cargador", "label": "Cargador", "field": "cargador"},
                                    {"name": "conector", "label": "Conector", "field": "conector"},
                                    {"name": "inicio", "label": "Inicio", "field": "inicio"},
                                    {"name": "energia", "label": "Energía", "field": "energia"},
                                    {"name": "duracion", "label": "Duración", "field": "duracion"},
                                    {"name": "usuario", "label": "Usuario", "field": "usuario"},
                                    {"name": "beneficio", "label": "Beneficio", "field": "beneficio"},
                                ],
                                rows=visible_rows,
                                row_key="fila",
                            ).props("dense flat").classes("w-full")

                    _render_expandable_dashboard_rows(
                        session_rows,
                        render_recent_sessions_table,
                    )
            if len(session_result) >= 500:
                ui.label(
                    "El endpoint devolvió el máximo de 500 sesiones para este rango."
                ).classes("cc-dashboard-muted")

    def load_history(start_value, end_value):
        if not start_value or not end_value:
            ui.notify("Selecciona las dos fechas del historial.", type="warning")
            return
        try:
            start_day = date.fromisoformat(str(start_value))
            end_day = date.fromisoformat(str(end_value))
        except ValueError:
            ui.notify("El rango de fechas no es válido.", type="negative")
            return
        if start_day > end_day:
            ui.notify(
                "La fecha inicial no puede ser posterior a la fecha final.",
                type="warning",
            )
            return
        session_result = api_get(
            "/sesiones/",
            params={
                "estado": "finalizada",
                "limit": 500,
                "fecha_inicio": start_day.isoformat(),
                "fecha_fin": end_day.isoformat(),
            },
            token=token,
            base_url=api_base_url,
        )
        render_history_rows(session_result, start_day, end_day)

    def set_history_range(days):
        end_day = date.today()
        start_day = end_day - timedelta(days=days - 1)
        history_start.set_value(start_day.isoformat())
        history_end.set_value(end_day.isoformat())
        load_history(start_day.isoformat(), end_day.isoformat())

    render_history_rows(
        results["history_sessions"],
        initial_start_date,
        initial_end_date,
    )

    ui.label("Energía por estación").classes("cc-dashboard-section-title")
    with ui.card().classes("chargecore-card cc-dashboard-card w-full"):
        ui.label("Energía acumulada · kWh").classes(
            "cc-dashboard-panel-subtitle"
        )
        energy_rows = results["energy_by_charger"]
        if isinstance(energy_rows, dict) and energy_rows.get("error"):
            _show_api_error(energy_rows)
        elif not isinstance(energy_rows, list):
            show_error("La respuesta de energía por estación no tiene el formato esperado.")
        elif not all(isinstance(item, dict) for item in energy_rows):
            show_error("La respuesta de energía por estación contiene registros inválidos.")
        else:
            registered_chargers = results["chargers"]
            if not isinstance(registered_chargers, list):
                registered_chargers = []
            station_energy_rows = _dashboard_station_energy_rows(
                energy_rows,
                [
                    charger
                    for charger in registered_chargers
                    if isinstance(charger, dict)
                ],
            )
            max_energy = max(
                (
                    value
                    for _, value in station_energy_rows
                    if value is not None
                ),
                default=0,
            )
            if not station_energy_rows:
                ui.label("No hay datos de energía por estación para mostrar.").classes(
                    "cc-dashboard-muted"
                )
            else:
                def render_energy_stations(visible_energy):
                    with ui.column().classes("cc-dashboard-energy-list w-full"):
                        for item, value in visible_energy:
                            station_name = (
                                item.get("nombre")
                                or item.get("charge_point_id")
                                or "Estación sin identificador"
                            )
                            width = (
                                value / max_energy * 100
                                if value is not None and max_energy > 0
                                else 0
                            )
                            with ui.element("div").classes(
                                "cc-dashboard-energy-row"
                            ):
                                with ui.element("div").classes(
                                    "cc-dashboard-energy-heading"
                                ):
                                    ui.label(str(station_name)).classes(
                                        "cc-dashboard-energy-name"
                                    )
                                    energy_label = (
                                        f"{value:.2f} kWh"
                                        if value is not None
                                        else "Sin datos"
                                    )
                                    ui.label(energy_label).classes(
                                        "cc-dashboard-energy-value"
                                        + (
                                            ""
                                            if value is not None
                                            else " cc-dashboard-energy-empty"
                                        )
                                    )
                                with ui.element("div").classes(
                                    "cc-dashboard-energy-track"
                                ):
                                    ui.element("div").classes(
                                        "cc-dashboard-energy-fill"
                                    ).style(f"width:{width:.2f}%")

                _render_expandable_dashboard_rows(
                    station_energy_rows,
                    render_energy_stations,
                )

    ui.label("Sesiones activas").classes("cc-dashboard-section-title")
    with ui.card().classes(
        "chargecore-card cc-dashboard-card cc-dashboard-tables w-full"
    ):
        active_sessions = results["active_sessions"]
        if isinstance(active_sessions, dict) and active_sessions.get("error"):
            _show_api_error(
                active_sessions, "No se pudieron cargar las sesiones activas."
            )
        elif not isinstance(active_sessions, list):
            show_error("La respuesta de sesiones activas no tiene el formato esperado.")
        elif not all(isinstance(item, dict) for item in active_sessions):
            show_error("La respuesta de sesiones activas contiene registros inválidos.")
        else:
            active_rows = []
            for index, item in enumerate(active_sessions):
                energy_value = item.get("energia_consumida_kwh")
                active_rows.append(
                    {
                        "fila": str(item.get("_id") or index),
                        "cargador": item.get("cargador_nombre")
                        or item.get("charge_point_id")
                        or "—",
                        "conector": item.get("connector_id")
                        if item.get("connector_id") is not None
                        else "—",
                        "inicio": item.get("fecha_inicio") or "—",
                        "energia": (
                            f"{energy_value:.2f} kWh"
                            if isinstance(energy_value, (int, float))
                            and not isinstance(energy_value, bool)
                            and math.isfinite(energy_value)
                            else "—"
                        ),
                        "estado": item.get("estado") or "—",
                    }
                )
            def render_active_sessions_table(visible_rows):
                with ui.element("div").classes("cc-dashboard-table-wrap"):
                    ui.table(
                        columns=[
                            {"name": "cargador", "label": "Cargador", "field": "cargador"},
                            {"name": "conector", "label": "Conector", "field": "conector"},
                            {"name": "inicio", "label": "Inicio", "field": "inicio"},
                            {"name": "energia", "label": "Energía", "field": "energia"},
                            {"name": "estado", "label": "Estado", "field": "estado"},
                        ],
                        rows=visible_rows,
                        row_key="fila",
                    ).props(
                        'dense flat no-data-label="No hay registros para mostrar."'
                    ).classes("w-full")

            _render_expandable_dashboard_rows(
                active_rows,
                render_active_sessions_table,
            )

    ui.label("Recompensas pendientes").classes("cc-dashboard-section-title")
    with ui.card().classes(
        "chargecore-card cc-dashboard-card cc-dashboard-tables w-full"
    ):
        _render_dashboard_table(
            results["pending_rewards"],
            "No se pudieron cargar las recompensas pendientes.",
            empty_columns=[
                {
                    "name": "usuario_nombre",
                    "label": "Usuario",
                    "field": "usuario_nombre",
                },
                {"name": "tipo", "label": "Tipo", "field": "tipo"},
                {
                    "name": "descripcion",
                    "label": "Descripción",
                    "field": "descripcion",
                },
                {
                    "name": "fecha_otorgamiento",
                    "label": "Fecha de otorgamiento",
                    "field": "fecha_otorgamiento",
                },
                {
                    "name": "cargas_acumuladas",
                    "label": "Cargas acumuladas",
                    "field": "cargas_acumuladas",
                },
            ],
        )

    ui.label("Alertas y eventos recientes").classes(
        "cc-dashboard-section-title"
    )
    with ui.element("div").classes(
        "cc-dashboard-tables cc-dashboard-grid-two w-full"
    ):
        for result_key, title in (
            ("recent_alerts", "Alertas recientes"),
            ("recent_events", "Eventos OCPP recientes"),
        ):
            result = results[result_key]
            with ui.card().classes(
                "chargecore-card cc-dashboard-card w-full"
            ):
                ui.label(title).classes("cc-dashboard-panel-title")
                if isinstance(result, dict) and result.get("error"):
                    _show_api_error(
                        result,
                        f"No se pudieron cargar los datos de {title.lower()}.",
                    )
                elif not isinstance(result, list):
                    show_error(
                        f"La respuesta de {title.lower()} no tiene el formato esperado."
                    )
                elif not all(isinstance(item, dict) for item in result):
                    show_error(
                        f"La respuesta de {title.lower()} contiene registros inválidos."
                    )
                else:
                    if result_key == "recent_alerts":
                        table_rows = _dashboard_alert_table_rows(result)

                        def render_recent_rows(visible_rows):
                            with ui.element("div").classes(
                                "cc-dashboard-table-wrap"
                            ):
                                table = ui.table(
                                    columns=[
                                        {
                                            "name": "nivel",
                                            "label": "Nivel",
                                            "field": "nivel",
                                        },
                                        {
                                            "name": "mensaje",
                                            "label": "Mensaje",
                                            "field": "mensaje",
                                        },
                                        {
                                            "name": "fecha",
                                            "label": "Fecha",
                                            "field": "fecha",
                                            "align": "right",
                                        },
                                    ],
                                    rows=visible_rows,
                                    row_key="fila",
                                ).props(
                                    'dense flat no-data-label="No hay registros para mostrar."'
                                ).classes("w-full")
                                table.add_slot(
                                    "body-cell-nivel",
                                    """
                                    <q-td :props="props">
                                      <span
                                        class="cc-dashboard-alert-badge"
                                        :class="'cc-dashboard-alert-' + props.row.nivel_class"
                                      >
                                        <span class="cc-dashboard-alert-dot"></span>
                                        {{ props.value }}
                                      </span>
                                    </q-td>
                                    """,
                                )
                    else:
                        table_rows = _dashboard_event_table_rows(result)

                        def render_recent_rows(visible_rows):
                            with ui.element("div").classes(
                                "cc-dashboard-table-wrap"
                            ):
                                ui.table(
                                    columns=[
                                        {
                                            "name": "cargador",
                                            "label": "Cargador",
                                            "field": "cargador",
                                        },
                                        {
                                            "name": "evento",
                                            "label": "Evento",
                                            "field": "evento",
                                        },
                                        {
                                            "name": "fecha",
                                            "label": "Fecha",
                                            "field": "fecha",
                                            "align": "right",
                                        },
                                    ],
                                    rows=visible_rows,
                                    row_key="fila",
                                ).props(
                                    'dense flat no-data-label="No hay registros para mostrar."'
                                ).classes("w-full")

                    _render_expandable_dashboard_rows(
                        table_rows,
                        render_recent_rows,
                    )


def _report_period_range(period, today=None):
    if period in (None, "", 0, "0", "todo"):
        return None, None
    try:
        days = int(period)
    except (TypeError, ValueError):
        return None, None
    if days < 1:
        return None, None
    end_date = today or date.today()
    start_date = end_date - timedelta(days=days - 1)
    return start_date.isoformat(), end_date.isoformat()


def _report_chart_points(rows, date_field, value_field, days, today=None):
    today = today or date.today()
    values_by_day = {}
    for row in rows:
        parsed_date = _reward_date_value(row.get(date_field))
        if parsed_date is None:
            continue
        try:
            value = float(row.get(value_field) or 0)
        except (TypeError, ValueError):
            continue
        day_key = parsed_date.date().isoformat()
        values_by_day[day_key] = values_by_day.get(day_key, 0) + value

    start_date = today - timedelta(days=days - 1)
    weekday_labels = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
    points = []
    for offset in range(days):
        current_date = start_date + timedelta(days=offset)
        label = (
            f"{weekday_labels[current_date.weekday()]} {current_date.day}"
            if days <= 7
            else str(current_date.day)
        )
        points.append(
            {
                "label": label,
                "value": round(values_by_day.get(current_date.isoformat(), 0), 2),
            }
        )
    return points


def _report_request_rows(endpoint, token, api_base_url, params=None):
    result = api_get(
        endpoint,
        params=params,
        token=token,
        base_url=api_base_url,
    )
    if isinstance(result, dict) and result.get("error"):
        _show_api_error(result)
        return None
    if not isinstance(result, list) or not all(
        isinstance(row, dict) for row in result
    ):
        show_error("El reporte devolvió información con formato inesperado.")
        return None
    return result


def _render_report_export_actions(
    endpoint,
    token,
    api_base_url,
    params=None,
):
    with ui.row().classes("cc-report-export-row"):
        def download_report(fmt):
            query = {"fmt": fmt}
            current_params = params() if callable(params) else params
            query.update(current_params or {})
            result = api_download(
                endpoint,
                params=query,
                token=token,
                base_url=api_base_url,
            )
            if isinstance(result, dict) and result.get("error"):
                ui.notify(
                    f"No se pudo descargar el reporte: {result['error']}",
                    type="negative",
                )
                return
            if not isinstance(result, dict) or not isinstance(
                result.get("content"), bytes
            ):
                ui.notify(
                    "El servidor devolvió un archivo inválido.",
                    type="negative",
                )
                return
            ui.download(
                result["content"],
                result.get("filename") or f"reporte.{fmt}",
                result.get("content_type") or "application/octet-stream",
            )

        ui.button(
            "Descargar PDF",
            icon="picture_as_pdf",
            on_click=lambda: download_report("pdf"),
        ).props("outline no-caps").classes("chargecore-secondary")
        ui.button(
            "Descargar CSV",
            icon="download",
            on_click=lambda: download_report("csv"),
        ).props("outline no-caps").classes("chargecore-secondary")


def _report_request_all_sessions(token, api_base_url):
    rows = []
    skip = 0
    page_size = 500
    while True:
        result = api_get(
            "/sesiones/",
            params={"skip": skip, "limit": page_size},
            token=token,
            base_url=api_base_url,
        )
        if isinstance(result, dict) and result.get("error"):
            _show_api_error(result)
            return None
        if not isinstance(result, list) or not all(
            isinstance(row, dict) for row in result
        ):
            show_error("El reporte de sesiones devolvió información inválida.")
            return None
        rows.extend(result)
        if len(result) < page_size:
            return rows
        skip += page_size


def _report_table_columns(fields):
    return [
        {
            "name": field,
            "label": label,
            "field": field,
            "align": "left",
            "sortable": True,
        }
        for field, label in fields
    ]


def _render_report_table(
    rows,
    fields,
    page_state,
    item_label,
    on_page_change,
    *,
    empty_label="Sin resultados",
    slots=None,
):
    start, end, total_pages, page_index = _dashboard_page_bounds(
        len(rows),
        RECORDS_PER_PAGE,
        page_state["index"],
    )
    page_state["index"] = page_index
    visible_rows = rows[start:end]
    with ui.card().classes("chargecore-card w-full gap-0 p-0"):
        table = ui.table(
            columns=_report_table_columns(fields),
            rows=visible_rows,
            row_key="fila",
        ).props(
            f'dense flat wrap-cells no-data-label="{empty_label}"'
        ).classes("w-full cc-report-table")
        for name, template in (slots or {}).items():
            table.add_slot(name, template)
        if not rows:
            table.add_slot(
                "no-data",
                """
                <div class="full-width row flex-center q-pa-md text-grey-5">
                  Sin resultados
                </div>
                """,
            )
        with ui.row().classes(
            "cc-report-pagination w-full items-center justify-between gap-3"
        ):
            visible_count = end - start
            range_text = (
                f"Mostrando {start + 1}–{end} de {len(rows)} {item_label}"
                if visible_count
                else f"Mostrando 0 de 0 {item_label}"
            )
            ui.label(
                f"{range_text} · Página {page_index + 1} de {total_pages}"
            ).classes("text-sm text-slate-400")
            with ui.row().classes("gap-2"):
                ui.button(
                    "Anterior",
                    icon="chevron_left",
                    on_click=lambda: change_page(-1),
                ).props("flat no-caps").set_enabled(page_index > 0)
                ui.button(
                    "Siguiente",
                    icon="chevron_right",
                    on_click=lambda: change_page(1),
                ).props("flat no-caps").set_enabled(
                    page_index + 1 < total_pages
                )

    def change_page(offset):
        page_state["index"] += offset
        on_page_change()


def _report_period_filter():
    return ui.select(
        {
            7: "Últimos 7 días",
            30: "Últimos 30 días",
            90: "Últimos 90 días",
            0: "Todo el periodo",
        },
        value=30,
        label="Periodo",
    ).props("outlined dense").classes("w-full")


def _report_styles():
    ui.add_head_html(
        """
        <style>
            .cc-reports-page {
                gap: 16px !important;
                padding-top: 20px !important;
            }
            .cc-report-header {
                flex-wrap: wrap;
                align-items: flex-start;
                justify-content: space-between;
                gap: 16px;
                width: 100%;
            }
            .cc-report-update-badge {
                display: inline-flex;
                align-items: center;
                gap: 7px;
                border: 1px solid var(--cc-border);
                border-radius: 999px;
                background: var(--cc-bg-card);
                padding: 8px 12px;
                color: var(--cc-text-secondary);
                font-size: 11px;
                font-weight: 600;
            }
            .cc-report-update-dot {
                width: 8px;
                height: 8px;
                border-radius: 50%;
                background: #22c55e;
            }
            .cc-report-kpi-grid {
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 12px;
                width: 100%;
            }
            .cc-report-kpi {
                box-sizing: border-box;
                min-width: 0;
                min-height: 145px;
                justify-content: space-between;
                gap: 8px !important;
                border: 1px solid var(--cc-border) !important;
                border-radius: 14px !important;
                background: var(--cc-bg-card) !important;
                padding: 16px !important;
                color: var(--cc-text-primary) !important;
                box-shadow: none !important;
            }
            .cc-report-kpi-primary {
                border-color: #ff8a4c !important;
                background: #f45b24 !important;
            }
            .cc-reports-page .q-card.cc-report-kpi-primary {
                border-color: #fff !important;
                background: #f45b24 !important;
            }
            .cc-report-kpi-icon {
                display: grid;
                width: 34px;
                height: 34px;
                place-items: center;
                border-radius: 10px;
                background: rgba(255, 255, 255, .08);
            }
            .cc-report-kpi-primary .cc-report-kpi-icon {
                background: rgba(255, 255, 255, .2);
            }
            .cc-report-kpi-title {
                color: var(--cc-text-muted);
                font-size: 10px;
                font-weight: 700;
                letter-spacing: .03em;
                text-transform: uppercase;
            }
            .cc-report-kpi-primary .cc-report-kpi-title,
            .cc-report-kpi-primary .cc-report-kpi-value,
            .cc-report-kpi-primary .cc-report-kpi-caption {
                color: #fff !important;
            }
            .cc-report-kpi-value {
                color: var(--cc-text-primary);
                font-size: 28px;
                font-weight: 750;
                line-height: 1.15;
            }
            .cc-report-kpi-caption {
                color: var(--cc-text-muted);
                font-size: 11px;
            }
            .cc-report-chart-grid {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 12px;
                width: 100%;
            }
            .cc-report-chart-card {
                min-width: 0;
                min-height: 230px;
                gap: 4px !important;
                border: 1px solid var(--cc-border) !important;
                border-radius: 14px !important;
                background: var(--cc-bg-card) !important;
                padding: 16px !important;
                box-shadow: none !important;
            }
            .cc-report-section-title {
                position: relative;
                padding-left: 12px;
                color: var(--cc-text-primary);
                font-size: 14px;
                font-weight: 700;
            }
            .cc-report-section-title::before {
                position: absolute;
                top: 2px;
                bottom: 2px;
                left: 0;
                width: 4px;
                border-radius: 4px;
                background: var(--cc-primary);
                content: "";
            }
            .cc-report-tabs {
                border-bottom: 1px solid var(--cc-border);
            }
            .cc-report-tabs .q-tab {
                min-height: 46px;
                color: var(--cc-text-muted);
                font-size: 12px;
                font-weight: 650;
            }
            .cc-report-tabs .q-tab--active {
                color: var(--cc-text-primary);
            }
            .cc-report-filters {
                display: grid;
                grid-template-columns: repeat(5, minmax(0, 1fr));
                align-items: end;
                gap: 10px;
                width: 100%;
            }
            .cc-report-filters .q-field {
                min-width: 0;
            }
            .cc-report-search-card {
                border: 1px solid var(--cc-border) !important;
                border-radius: 14px !important;
                background: var(--cc-bg-card) !important;
                padding: 14px !important;
                box-shadow: none !important;
            }
            .cc-report-table .q-table thead th {
                color: var(--cc-text-muted);
                font-size: 10px;
                font-weight: 700;
                text-transform: uppercase;
            }
            .cc-report-table .q-table tbody td {
                color: var(--cc-text-secondary);
                font-size: 12px;
            }
            .cc-report-pagination {
                min-height: 48px;
                border-top: 1px solid var(--cc-border);
                padding: 8px 14px;
            }
            .cc-report-status {
                display: inline-flex;
                align-items: center;
                gap: 6px;
                border-radius: 999px;
                background: var(--cc-field);
                padding: 4px 9px;
                color: var(--cc-text-primary);
                font-size: 10px;
                font-weight: 650;
                white-space: nowrap;
            }
            .cc-report-status::before {
                width: 6px;
                height: 6px;
                border-radius: 50%;
                background: #22c55e;
                content: "";
            }
            .cc-report-status-inactive::before,
            .cc-report-status-error::before {
                background: #ef4444;
            }
            .cc-report-status-pending::before {
                background: #f59e0b;
            }
            .cc-report-participation {
                display: grid;
                grid-template-columns: minmax(55px, 1fr) 38px;
                align-items: center;
                gap: 8px;
                min-width: 120px;
            }
            .cc-report-overview-grid {
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 12px;
                width: 100%;
            }
            .cc-report-overview-card {
                min-width: 0;
                min-height: 90px;
                justify-content: center;
                gap: 4px !important;
                border: 1px solid var(--cc-border) !important;
                border-radius: 14px !important;
                background: var(--cc-bg-card) !important;
                padding: 14px !important;
                box-shadow: none !important;
            }
            .cc-report-export-row {
                flex-wrap: wrap;
                gap: 10px;
                padding-top: 4px;
            }
            @media (max-width: 900px) {
                .cc-report-kpi-grid {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }
                .cc-report-chart-grid {
                    grid-template-columns: minmax(0, 1fr);
                }
                .cc-report-filters {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }
            }
            @media (max-width: 600px) {
                .cc-report-kpi-grid,
                .cc-report-overview-grid,
                .cc-report-filters {
                    grid-template-columns: minmax(0, 1fr);
                }
                .cc-report-kpi {
                    min-height: 125px;
                }
            }
        </style>
        """
    )


def _render_report_kpis(values):
    with ui.element("div").classes("cc-report-kpi-grid"):
        for index, (title, value, caption, icon, color) in enumerate(values):
            card_class = (
                "chargecore-card cc-report-kpi cc-report-kpi-primary"
                if index == 0
                else "chargecore-card cc-report-kpi"
            )
            with ui.card().classes(card_class):
                with ui.element("div").classes("cc-report-kpi-icon"):
                    ui.icon(icon).style(f"color: {color}; font-size: 19px;")
                ui.label(title).classes("cc-report-kpi-title")
                ui.label(str(value)).classes("cc-report-kpi-value")
                ui.label(caption).classes("cc-report-kpi-caption")


def _render_report_chart(title, subtitle, points, *, color, show_values=False):
    with ui.card().classes("chargecore-card cc-report-chart-card"):
        ui.label(title).classes("text-sm font-bold text-white")
        ui.label(subtitle).classes("text-xs text-slate-400")
        ui.echart(
            {
                "tooltip": {"trigger": "axis"},
                "grid": {"left": 24, "right": 12, "top": 18, "bottom": 24},
                "xAxis": {
                    "type": "category",
                    "data": [point["label"] for point in points],
                    "axisLabel": {"color": "#a8a49e", "fontSize": 9},
                    "axisLine": {"lineStyle": {"color": "#3a3430"}},
                },
                "yAxis": {
                    "type": "value",
                    "axisLabel": {"color": "#a8a49e", "fontSize": 9},
                    "splitLine": {"lineStyle": {"color": "#34302d"}},
                },
                "series": [
                    {
                        "type": "bar",
                        "data": [point["value"] for point in points],
                        "itemStyle": {
                            "color": color,
                            "borderRadius": [3, 3, 0, 0],
                        },
                        "barMaxWidth": 28,
                        "label": {
                            "show": show_values,
                            "position": "top",
                            "color": "#f4f4f5",
                            "fontSize": 9,
                        },
                    }
                ],
                "textStyle": {"color": "#d4d4d8"},
                "backgroundColor": "transparent",
            }
        ).classes("h-48 w-full")


def _report_overview_cards(charts, reward_rows):
    charger_rows = charts.get("uso_de_cargadores", [])
    charger_rows = charger_rows if isinstance(charger_rows, list) else []
    busiest_charger = max(
        charger_rows,
        key=lambda row: row.get("sesiones", 0) or 0,
        default={},
    )
    user_rows = charts.get("top_usuarios", [])
    top_user = user_rows[0] if isinstance(user_rows, list) and user_rows else {}
    used = sum(
        str(row.get("estado", "")).casefold() == "usada"
        for row in reward_rows
    )
    pending = sum(
        str(row.get("estado", "")).casefold() in {"pendiente", "disponible"}
        for row in reward_rows
    )
    return [
        (
            "Cargador con más sesiones",
            busiest_charger.get("cargador") or "—",
            f"{busiest_charger.get('sesiones', 0)} sesiones",
        ),
        (
            "Usuario con mayor consumo",
            top_user.get("usuario") or "—",
            f"{top_user.get('kwh', 0)} kWh",
        ),
        (
            "Recompensas entregadas",
            len(reward_rows),
            f"{used} usadas · {pending} pendientes",
        ),
    ]


def _render_report_overview(charts, reward_rows, token, api_base_url):
    ui.label("Resumen general").classes("cc-report-section-title")
    with ui.element("div").classes("cc-report-overview-grid"):
        for title, value, caption in _report_overview_cards(charts, reward_rows):
            with ui.card().classes(
                "chargecore-card cc-report-overview-card"
            ):
                ui.label(title).classes("text-xs text-slate-400")
                ui.label(str(value)).classes(
                    "break-words text-base font-bold text-white"
                )
                ui.label(caption).classes("text-xs text-slate-400")
    _render_report_export_actions(
        "/reportes/general/export",
        token,
        api_base_url,
    )


def _render_report_sessions_panel(rows, token, api_base_url):
    charger_options = {"": "Todos"}
    user_options = {"": "Todos"}
    for row in rows:
        charger_id = row.get("charge_point_id")
        if charger_id:
            charger_options[str(charger_id)] = (
                row.get("cargador_nombre") or str(charger_id)
            )
        user_id = row.get("usuario_id")
        if user_id:
            user_options[str(user_id)] = (
                row.get("usuario_nombre") or str(user_id)
            )
    with ui.card().classes("cc-report-search-card w-full"):
        with ui.element("div").classes("cc-report-filters"):
            period = _report_period_filter()
            state_filter = ui.select(
                {
                    "": "Todos",
                    "activa": "Activa",
                    "finalizada": "Finalizada",
                    "error": "Error",
                },
                value="",
                label="Estado",
            ).props("outlined dense").classes("w-full")
            charger_filter = ui.select(
                charger_options,
                value="",
                label="Cargador",
            ).props("outlined dense").classes("w-full")
            user_filter = ui.select(
                user_options,
                value="",
                label="Usuario",
            ).props("outlined dense").classes("w-full")
            search = ui.input(
                "Buscar",
                placeholder="Folio, cargador o vehículo...",
            ).props("outlined dense clearable").classes("w-full")

    normalized_rows = []
    for index, row in enumerate(rows):
        state_text, state_class = _session_status(row)
        vehicle = row.get("vehiculo_placa") or " ".join(
            str(value)
            for value in (row.get("vehiculo_marca"), row.get("vehiculo_modelo"))
            if value
        )
        normalized_rows.append(
            {
                "fila": str(row.get("_id") or index),
                "folio": _session_folio(row),
                "cargador": row.get("cargador_nombre")
                or row.get("charge_point_id")
                or "—",
                "usuario": row.get("usuario_nombre") or "Sin usuario",
                "vehiculo": vehicle or "—",
                "inicio": _format_session_datetime(row.get("fecha_inicio")),
                "energia": _format_session_energy(
                    row.get("energia_consumida_kwh")
                ),
                "estado": state_text,
                "estado_clase": state_class,
                "estado_original": str(row.get("estado") or "").casefold(),
                "charge_point_id": str(row.get("charge_point_id") or ""),
                "usuario_id": str(row.get("usuario_id") or ""),
                "fecha_inicio_raw": row.get("fecha_inicio"),
            }
        )
    page_state = {"index": 0}
    table_container = ui.column().classes("w-full")
    fields = (
        ("folio", "Folio"),
        ("cargador", "Cargador"),
        ("usuario", "Usuario"),
        ("vehiculo", "Vehículo"),
        ("inicio", "Inicio"),
        ("energia", "Energía"),
        ("estado", "Estado"),
    )
    status_slot = """
        <q-td :props="props">
          <span class="cc-report-status" :class="'cc-report-status-' + props.row.estado_clase">
            {{ props.value }}
          </span>
        </q-td>
    """

    def filtered_rows():
        query = (search.value or "").strip().casefold()
        start_value, _ = _report_period_range(period.value)
        start_date = date.fromisoformat(start_value) if start_value else None
        selected_state = state_filter.value or ""
        selected_charger = charger_filter.value or ""
        selected_user = user_filter.value or ""
        result = []
        for row in normalized_rows:
            if selected_state == "activa" and row["estado_original"] not in {
                "activa",
                "iniciada",
            }:
                continue
            if selected_state and selected_state not in {"activa"}:
                if row["estado_original"] != selected_state:
                    continue
            if selected_charger and row["charge_point_id"] != str(selected_charger):
                continue
            if selected_user and row["usuario_id"] != str(selected_user):
                continue
            row_date = _reward_date_value(row["fecha_inicio_raw"])
            if start_date and (
                row_date is None or row_date.date() < start_date
            ):
                continue
            search_text = " ".join(
                (
                    row["folio"],
                    row["cargador"],
                    row["usuario"],
                    row["vehiculo"],
                )
            ).casefold()
            if query and query not in search_text:
                continue
            result.append(row)
        return result

    def render_table():
        table_container.clear()
        with table_container:
            _render_report_table(
                filtered_rows(),
                fields,
                page_state,
                "sesiones",
                on_page_change=render_table,
                slots={"body-cell-estado": status_slot},
            )

    def refresh_table(_=None):
        page_state["index"] = 0
        render_table()

    period.on_value_change(refresh_table)
    state_filter.on_value_change(refresh_table)
    charger_filter.on_value_change(refresh_table)
    user_filter.on_value_change(refresh_table)
    search.on_value_change(refresh_table)
    render_table()

    def export_params():
        start_value, end_value = _report_period_range(period.value)
        params = {}
        if start_value:
            params["fecha_inicio"] = start_value
        if end_value:
            params["fecha_fin"] = end_value
        if charger_filter.value:
            params["charge_point_id"] = charger_filter.value
        if user_filter.value:
            params["usuario_id"] = user_filter.value
        return params

    _render_report_export_actions(
        "/reportes/sesiones/export",
        token,
        api_base_url,
        params=export_params,
    )


def _render_report_consumption_panel(token, api_base_url):
    page_state = {"index": 0}
    data_state = {"rows": []}
    fields = (
        ("usuario", "Usuario"),
        ("total_cargas", "Sesiones"),
        ("total_kwh", "Energía (kWh)"),
        ("promedio_kwh", "Promedio por sesión"),
        ("participacion", "Participación"),
    )
    participation_slot = """
        <q-td :props="props">
          <div class="cc-report-participation">
            <q-linear-progress
              :value="props.row.participacion / 100"
              rounded
              color="orange"
            />
            <span>{{ props.row.participacion }}%</span>
          </div>
        </q-td>
    """

    with ui.card().classes("cc-report-search-card w-full"):
        period = _report_period_filter()
    table_container = ui.column().classes("w-full")

    def draw_table():
        table_container.clear()
        with table_container:
            _render_report_table(
                data_state["rows"],
                fields,
                page_state,
                "usuarios",
                on_page_change=draw_table,
                slots={"body-cell-participacion": participation_slot},
            )

    def render():
        table_container.clear()
        page_state["index"] = 0
        start_value, end_value = _report_period_range(period.value)
        params = {"limit": 1000}
        if start_value:
            params["fecha_inicio"] = start_value
        if end_value:
            params["fecha_fin"] = end_value
        rows = _report_request_rows(
            "/reportes/consumo",
            token,
            api_base_url,
            params=params,
        )
        if rows is None:
            return
        energy_total = 0.0
        normalized = []
        for index, row in enumerate(rows):
            try:
                energy = float(row.get("total_kwh") or 0)
            except (TypeError, ValueError):
                energy = 0.0
            energy_total += energy
            normalized.append((index, row, energy))
        display_rows = []
        for index, row, energy in normalized:
            participation = (
                round(energy * 100 / energy_total) if energy_total else 0
            )
            display_rows.append(
                {
                    "fila": str(index),
                    "usuario": row.get("usuario") or "Sin usuario",
                    "total_cargas": row.get("total_cargas", 0),
                    "total_kwh": f"{energy:g}",
                    "promedio_kwh": f"{float(row.get('promedio_kwh') or 0):g}",
                    "participacion": participation,
                }
            )
        data_state["rows"] = display_rows
        draw_table()

    period.on_value_change(lambda _: render())
    render()
    _render_report_export_actions(
        "/reportes/consumo/export",
        token,
        api_base_url,
        params=lambda: {
            key: value
            for key, value in zip(
                ("fecha_inicio", "fecha_fin"),
                _report_period_range(period.value),
            )
            if value
        },
    )


def _render_report_search_table(
    rows,
    fields,
    search_label,
    placeholder,
    item_label,
    slots=None,
):
    with ui.card().classes("cc-report-search-card w-full"):
        search = ui.input(
            search_label,
            placeholder=placeholder,
        ).props("outlined dense clearable").classes("w-full")
    page_state = {"index": 0}
    container = ui.column().classes("w-full")

    def render():
        query = (search.value or "").strip().casefold()
        filtered = [
            row
            for row in rows
            if not query
            or query
            in " ".join(
                str(value or "")
                for key, value in row.items()
                if key != "fila" and not key.endswith("_clase")
            ).casefold()
        ]
        container.clear()
        with container:
            _render_report_table(
            filtered,
            fields,
            page_state,
            item_label,
            on_page_change=render,
            slots=slots,
            )

    def reset_page(_):
        page_state["index"] = 0
        render()

    search.on_value_change(reset_page)
    render()


def _render_report_users_panel(rows, token, api_base_url):
    display_rows = []
    for index, row in enumerate(rows):
        status = row.get("estado") or "Activo"
        display_rows.append(
            {
                "fila": str(index),
                "nombre": row.get("nombre") or "—",
                "correo": row.get("correo") or "—",
                "nfc": row.get("nfc_asociado") or "N/A",
                "vehiculos": row.get("vehiculos_registrados", 0),
                "cargas": row.get("numero_cargas", 0),
                "estado": status,
                "estado_clase": (
                    "inactive" if str(status).casefold() == "inactivo" else "active"
                ),
            }
        )
    _render_report_search_table(
        display_rows,
        (
            ("nombre", "Nombre"),
            ("correo", "Correo"),
            ("nfc", "NFC asociado"),
            ("vehiculos", "Vehículos"),
            ("cargas", "Cargas"),
            ("estado", "Estado"),
        ),
        "Buscar usuario",
        "Nombre o correo...",
        "usuarios",
        slots={
            "body-cell-estado": """
                <q-td :props="props">
                  <span class="cc-report-status" :class="'cc-report-status-' + props.row.estado_clase">
                    {{ props.value }}
                  </span>
                </q-td>
            """
        },
    )
    _render_report_export_actions(
        "/reportes/usuarios/export",
        token,
        api_base_url,
    )


def _render_report_chargers_panel(rows, token, api_base_url):
    status_labels = {
        "disponible": ("Disponible", "active"),
        "ocupado": ("Ocupado", "active"),
        "mantenimiento": ("Mantenimiento", "pending"),
        "fuera_de_servicio": ("Fuera de servicio", "inactive"),
    }
    display_rows = []
    for index, row in enumerate(rows):
        raw_status = str(row.get("estado") or "").casefold()
        status, status_class = status_labels.get(
            raw_status,
            (raw_status.replace("_", " ").capitalize() or "Sin estado", "inactive"),
        )
        try:
            energy = f"{float(row.get('energia_suministrada_kwh') or 0):g}"
        except (TypeError, ValueError):
            energy = "—"
        display_rows.append(
            {
                "fila": str(index),
                "cargador": row.get("charger_id") or "—",
                "estado": status,
                "estado_clase": status_class,
                "ultima_conexion": row.get("ultima_conexion") or "—",
                "sesiones": row.get("total_sesiones", 0),
                "energia": energy,
            }
        )
    _render_report_search_table(
        display_rows,
        (
            ("cargador", "Cargador"),
            ("estado", "Estado"),
            ("ultima_conexion", "Última conexión"),
            ("sesiones", "Sesiones"),
            ("energia", "Energía suministrada (kWh)"),
        ),
        "Buscar cargador",
        "Identificador o estado...",
        "cargadores",
        slots={
            "body-cell-estado": """
                <q-td :props="props">
                  <span class="cc-report-status" :class="'cc-report-status-' + props.row.estado_clase">
                    {{ props.value }}
                  </span>
                </q-td>
            """
        },
    )
    _render_report_export_actions(
        "/reportes/cargadores/export",
        token,
        api_base_url,
    )


def _render_report_rewards_panel(rows, token, api_base_url):
    reward_labels = {
        "carga_gratuita": "Carga gratuita",
        "beneficio_especial": "Beneficio especial",
    }
    display_rows = []
    for index, row in enumerate(rows):
        status = row.get("estado") or "Pendiente"
        display_rows.append(
            {
                "fila": str(index),
                "usuario": row.get("usuario") or "Sin usuario",
                "recompensa": reward_labels.get(
                    row.get("tipo_de_recompensa"),
                    row.get("tipo_de_recompensa") or "Recompensa",
                ),
                "fecha": row.get("fecha_generacion") or "—",
                "estado": status,
                "estado_clase": (
                    "pending"
                    if str(status).casefold() == "pendiente"
                    else "active"
                ),
            }
        )
    page_state = {"index": 0}
    container = ui.column().classes("w-full")
    fields = (
        ("usuario", "Usuario"),
        ("recompensa", "Recompensa"),
        ("fecha", "Fecha"),
        ("estado", "Estado"),
    )
    slots = {
        "body-cell-estado": """
            <q-td :props="props">
              <span class="cc-report-status" :class="'cc-report-status-' + props.row.estado_clase">
                {{ props.value }}
              </span>
            </q-td>
        """
    }

    def render():
        container.clear()
        with container:
            _render_report_table(
                display_rows,
                fields,
                page_state,
                "recompensas",
                on_page_change=render,
                empty_label="Sin resultados",
                slots=slots,
            )

    render()
    _render_report_export_actions(
        "/reportes/recompensas/export",
        token,
        api_base_url,
    )


def _render_reports(token, api_base_url):
    _report_styles()
    summary_result = api_get(
        "/reportes/dashboard",
        token=token,
        base_url=api_base_url,
    )
    if isinstance(summary_result, dict) and summary_result.get("error"):
        _show_api_error(summary_result)
        return
    if not isinstance(summary_result, dict):
        show_error("El resumen de reportes no tiene el formato esperado.")
        return
    kpi_result = api_get("/dashboard/kpi", token=token, base_url=api_base_url)
    if isinstance(kpi_result, dict) and kpi_result.get("error"):
        _show_api_error(kpi_result)
        return
    if not isinstance(kpi_result, dict):
        show_error("Los indicadores del reporte tienen un formato inesperado.")
        return
    weekly_result = api_get(
        "/dashboard/grafica-consumo-semanal",
        token=token,
        base_url=api_base_url,
    )
    if isinstance(weekly_result, dict) and weekly_result.get("error"):
        _show_api_error(weekly_result)
        return
    if not isinstance(weekly_result, list) or not all(
        isinstance(row, dict) for row in weekly_result
    ):
        show_error("La gráfica de consumo diario devolvió datos inválidos.")
        return
    charts = summary_result.get("graficas") or {}
    if not isinstance(charts, dict):
        show_error("Las gráficas del reporte tienen un formato inesperado.")
        return
    rewards_rows = _report_request_rows(
        "/reportes/recompensas",
        token,
        api_base_url,
    )
    if rewards_rows is None:
        return

    month_names = (
        "ene",
        "feb",
        "mar",
        "abr",
        "may",
        "jun",
        "jul",
        "ago",
        "sep",
        "oct",
        "nov",
        "dic",
    )
    now = datetime.now().astimezone()
    updated_label = (
        f"Actualizado: {now.day} {month_names[now.month - 1]} "
        f"{now.year}, {now:%H:%M}"
    )
    with ui.row().classes("cc-report-header"):
        render_page_heading(
            "Reportes y análisis",
            "Consulta la actividad, el consumo y el uso de los cargadores.",
        )
        with ui.row().classes("items-center gap-2"):
            with ui.element("div").classes("cc-report-update-badge"):
                ui.element("span").classes("cc-report-update-dot")
                ui.label(updated_label)
            ui.button(
                "Actualizar",
                icon="refresh",
                on_click=ui.navigate.reload,
            ).props("outline no-caps").classes("text-white")

    kpis = {
        "sessions": kpi_result.get("total_cargas", 0),
        "energy": kpi_result.get("energia_total_kwh", 0),
        "active": kpi_result.get("sesiones_activas", 0),
    }
    try:
        total_energy = float(kpis["energy"] or 0)
        completed_sessions = int(kpis["sessions"] or 0)
        average_energy = (
            total_energy / completed_sessions if completed_sessions else 0
        )
        average_text = f"{average_energy:.0f} kWh"
        energy_text = f"{total_energy:g} kWh"
    except (TypeError, ValueError):
        show_error("Los indicadores de energía devueltos por el servidor son inválidos.")
        return
    _render_report_kpis(
        [
            (
                "Sesiones finalizadas",
                completed_sessions,
                "Acumuladas en el sistema",
                "task_alt",
                "#a7f3d0",
            ),
            (
                "Energía consumida",
                energy_text,
                "Total entregado",
                "bolt",
                "#fb923c",
            ),
            (
                "Promedio por sesión",
                average_text,
                "Sesiones finalizadas",
                "show_chart",
                "#60a5fa",
            ),
            (
                "Sesiones activas",
                kpis["active"],
                "Cargando ahora",
                "battery_charging_full",
                "#bef264",
            ),
        ]
    )
    today = date.today()
    daily_consumption = _report_chart_points(
        weekly_result,
        "fecha",
        "energia_kwh",
        7,
        today=today,
    )
    daily_sessions = _report_chart_points(
        charts.get("sesiones_por_dia", []),
        "fecha",
        "sesiones",
        30,
        today=today,
    )
    with ui.element("div").classes("cc-report-chart-grid"):
        _render_report_chart(
            "Consumo diario",
            "Energía de los últimos 7 días (kWh)",
            daily_consumption,
            color="#f45b24",
            show_values=True,
        )
        _render_report_chart(
            "Sesiones por día",
            "Sesiones finalizadas de los últimos 30 días",
            daily_sessions,
            color="#f45b24",
        )

    tabs = ui.tabs().classes("cc-report-tabs w-full")
    with tabs:
        ui.tab("resumen", label="Resumen")
        ui.tab("sesiones", label="Sesiones")
        ui.tab("consumo", label="Consumo")
        ui.tab("usuarios", label="Usuarios")
        ui.tab("cargadores", label="Cargadores")
        ui.tab("recompensas", label="Recompensas")
    panel = ui.column().classes("w-full gap-4")
    cache = {"recompensas": rewards_rows}

    def load_tab_rows(name, endpoint, params=None):
        if name not in cache:
            rows = _report_request_rows(
                endpoint,
                token,
                api_base_url,
                params=params,
            )
            if rows is not None:
                cache[name] = rows
        return cache.get(name)

    def render_tab(tab_name):
        panel.clear()
        with panel:
            if tab_name == "resumen":
                _render_report_overview(
                    charts,
                    rewards_rows,
                    token,
                    api_base_url,
                )
            elif tab_name == "sesiones":
                if cache.get("sesiones") is None:
                    cache["sesiones"] = _report_request_all_sessions(
                        token,
                        api_base_url,
                    )
                if cache["sesiones"] is not None:
                    _render_report_sessions_panel(
                        cache["sesiones"],
                        token,
                        api_base_url,
                    )
            elif tab_name == "consumo":
                _render_report_consumption_panel(token, api_base_url)
            elif tab_name == "usuarios":
                rows = load_tab_rows("usuarios", "/reportes/usuarios")
                if rows is not None:
                    _render_report_users_panel(rows, token, api_base_url)
            elif tab_name == "cargadores":
                rows = load_tab_rows("cargadores", "/reportes/cargadores")
                if rows is not None:
                    _render_report_chargers_panel(rows, token, api_base_url)
            elif tab_name == "recompensas":
                _render_report_rewards_panel(
                    rewards_rows,
                    token,
                    api_base_url,
                )

    tabs.on_value_change(lambda event: render_tab(str(event.value)))
    render_tab("resumen")


ALERT_LEVEL_PRESENTATION = (
    ("error", "Crítica", "critical", "emergency", "#ef4444"),
    ("warning", "Advertencia", "warning", "warning", "#f59e0b"),
    ("info", "Informativa", "info", "info", "#3b82f6"),
    ("debug", "Depuración", "debug", "bug_report", "#8b5cf6"),
)


def _alert_level_key(value):
    level = str(value or "").strip().casefold()
    return {
        "critical": "error",
        "critica": "error",
        "crítica": "error",
        "informativa": "info",
        "information": "info",
        "depuracion": "debug",
        "depuración": "debug",
    }.get(level, level)


def _alert_table_rows(alerts):
    presentation = {
        key: (label, class_name)
        for key, label, class_name, _, _ in ALERT_LEVEL_PRESENTATION
    }
    rows = []
    for index, alert in enumerate(alerts):
        level = _alert_level_key(alert.get("nivel"))
        level_label, level_class = presentation.get(
            level,
            (str(alert.get("nivel") or "—").capitalize(), "info"),
        )
        details = alert.get("datos")
        details = details if isinstance(details, dict) else {}
        resource = (
            alert.get("recurso")
            or alert.get("resource")
            or alert.get("charge_point_id")
            or alert.get("transaction_id")
            or details.get("charge_point_id")
            or details.get("transaction_id")
            or details.get("connector_id")
            or "—"
        )
        raw_status = str(
            alert.get("estado") or alert.get("status") or ""
        ).strip().casefold()
        attended = bool(alert.get("atendida")) or raw_status in {
            "atendida",
            "atendido",
            "attended",
            "resolved",
            "handled",
        }
        rows.append(
            {
                "fila": str(alert.get("_id") or alert.get("id") or index),
                "nivel": level_label,
                "nivel_clase": level_class,
                "nivel_original": level,
                "titulo": str(
                    alert.get("titulo") or alert.get("mensaje") or "Alerta"
                ),
                "modulo": str(alert.get("modulo") or "sistema"),
                "recurso": str(resource),
                "fecha": _format_dashboard_timestamp(alert.get("timestamp")),
                "estado": "Atendida" if attended else "Nueva",
                "estado_clase": "attended" if attended else "new",
            }
        )
    return rows


def _alert_summary_values(alerts):
    levels = [_alert_level_key(alert.get("nivel")) for alert in alerts]
    return (
        len(alerts),
        sum(level == "error" for level in levels),
        sum(level == "warning" for level in levels),
        sum(
            str(alert.get("modulo") or "").strip().casefold() == "ocpp"
            for alert in alerts
        ),
    )


def _alert_chart_points(alerts, today=None):
    today = today or date.today()
    counts = {}
    for alert in alerts:
        timestamp = _reward_date_value(alert.get("timestamp"))
        if timestamp is None:
            continue
        key = timestamp.date().isoformat()
        counts[key] = counts.get(key, 0) + 1
    start_date = today - timedelta(days=6)
    weekdays = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
    return [
        {
            "label": (
                f"{weekdays[(start_date + timedelta(days=offset)).weekday()]} "
                f"{(start_date + timedelta(days=offset)).day}"
            ),
            "value": counts.get(
                (start_date + timedelta(days=offset)).isoformat(),
                0,
            ),
        }
        for offset in range(7)
    ]


def _alerts_styles():
    ui.add_head_html(
        """
        <style>
            .cc-alerts-page {
                gap: 16px !important;
                padding-top: 20px !important;
            }
            .cc-alerts-header {
                flex-wrap: nowrap;
                width: 100%;
                align-items: flex-start;
                justify-content: space-between;
                gap: 16px;
            }
            .cc-alerts-title-block {
                min-width: 0;
                flex: 1 1 auto;
            }
            .cc-alerts-refresh.q-btn {
                flex: 0 0 auto;
            }
            .cc-alerts-title {
                color: var(--cc-text-primary);
                font-size: clamp(22px, 2.2vw, 28px);
                font-weight: 750;
                line-height: 1.15;
            }
            .cc-alerts-subtitle {
                color: var(--cc-text-muted);
                font-size: 12px;
            }
            .cc-alert-kpi-grid {
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 12px;
                width: 100%;
            }
            .cc-alert-kpi-card {
                box-sizing: border-box;
                min-width: 0;
                min-height: 138px;
                justify-content: space-between;
                gap: 7px !important;
                border: 1px solid var(--cc-border) !important;
                border-radius: 13px !important;
                background: var(--cc-bg-card) !important;
                padding: 14px !important;
                box-shadow: none !important;
            }
            .cc-alert-kpi-primary {
                border-color: #ff8a4c !important;
                background: #f45b24 !important;
            }
            .cc-alert-kpi-icon {
                display: grid;
                width: 32px;
                height: 32px;
                place-items: center;
                border-radius: 9px;
                background: rgba(255, 255, 255, .08);
            }
            .cc-alert-kpi-primary .cc-alert-kpi-icon {
                background: rgba(255, 255, 255, .2);
            }
            .cc-alert-kpi-label {
                color: var(--cc-text-muted);
                font-size: 10px;
                font-weight: 700;
                text-transform: uppercase;
            }
            .cc-alert-kpi-value {
                color: var(--cc-text-primary);
                font-size: 27px;
                font-weight: 750;
                line-height: 1.1;
            }
            .cc-alert-kpi-caption {
                color: var(--cc-text-muted);
                font-size: 10px;
            }
            .cc-alert-kpi-primary .cc-alert-kpi-label,
            .cc-alert-kpi-primary .cc-alert-kpi-value,
            .cc-alert-kpi-primary .cc-alert-kpi-caption {
                color: #fff !important;
            }
            .cc-alert-section-title {
                position: relative;
                padding-left: 11px;
                color: var(--cc-text-primary);
                font-size: 14px;
                font-weight: 700;
            }
            .cc-alert-section-title::before {
                position: absolute;
                inset: 1px auto 1px 0;
                width: 4px;
                border-radius: 4px;
                background: #f45b24;
                content: "";
            }
            .cc-alert-filter-card,
            .cc-alert-table-card,
            .cc-alert-chart-card {
                width: 100%;
                border: 1px solid var(--cc-border) !important;
                border-radius: 13px !important;
                background: var(--cc-bg-card) !important;
                box-shadow: none !important;
            }
            .cc-alert-filter-card { padding: 13px !important; }
            .cc-alert-filters {
                display: grid;
                grid-template-columns: 1.05fr 1fr 1.05fr 1fr;
                align-items: start;
                gap: 12px;
                width: 100%;
            }
            .cc-alert-filter-label {
                color: var(--cc-text-primary);
                font-size: 11px;
                font-weight: 650;
            }
            .cc-alert-level-list {
                align-items: center;
                gap: 6px;
            }
            .cc-alert-filter-chip.q-btn {
                min-height: 31px;
                border: 1px solid var(--cc-border);
                border-radius: 999px;
                background: transparent !important;
                font-size: 10px;
            }
            .cc-alert-filter-chip.q-btn:hover,
            .cc-alert-filter-chip-active.q-btn {
                border-color: #f45b24 !important;
            }
            .cc-alert-filter-card .q-field__control {
                min-height: 36px;
                background: var(--cc-field);
            }
            .cc-alert-table-card { padding: 10px 14px !important; }
            .cc-alert-table .q-table thead th {
                color: var(--cc-text-muted);
                font-size: 9px;
                font-weight: 700;
                text-transform: uppercase;
            }
            .cc-alert-table .q-table tbody td {
                height: 43px;
                border-color: var(--cc-border);
                color: var(--cc-text-secondary);
                font-size: 10px;
            }
            .cc-alert-pill {
                display: inline-flex;
                align-items: center;
                gap: 5px;
                border-radius: 999px;
                background: var(--cc-field);
                padding: 4px 8px;
                color: var(--cc-text-primary);
                font-size: 9px;
                font-weight: 650;
                white-space: nowrap;
            }
            .cc-alert-pill::before {
                width: 6px;
                height: 6px;
                border-radius: 50%;
                background: #3b82f6;
                content: "";
            }
            .cc-alert-level-critical::before { background: #ef4444; }
            .cc-alert-level-warning::before { background: #f59e0b; }
            .cc-alert-level-debug::before { background: #3b82f6; }
            .cc-alert-status-new::before { background: #f45b24; }
            .cc-alert-status-attended::before { background: #22c55e; }
            .cc-alert-row-action.q-btn {
                width: 27px;
                height: 27px;
                min-height: 27px;
                border: 1px solid var(--cc-border);
                border-radius: 7px;
            }
            .cc-alert-pagination {
                min-height: 40px;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
                border-top: 1px solid var(--cc-border);
                padding: 7px 4px 0;
            }
            .cc-alert-chart-grid {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 12px;
                width: 100%;
            }
            .cc-alert-chart-card {
                min-width: 0;
                min-height: 250px;
                gap: 4px !important;
                padding: 14px !important;
            }
            .cc-alert-module-row { gap: 5px !important; }
            .cc-alert-module-progress .q-linear-progress__track {
                background: var(--cc-field) !important;
                opacity: 1;
            }
            .cc-alert-module-progress .q-linear-progress__model {
                background: #f45b24 !important;
            }
            @media (max-width: 1200px) {
                .cc-alert-filters {
                    grid-template-columns: repeat(3, minmax(0, 1fr));
                }
                .cc-alert-filter-level { grid-column: span 2; }
            }
            @media (max-width: 900px) {
                .cc-alert-kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
                .cc-alert-filters { grid-template-columns: repeat(2, minmax(0, 1fr)); }
                .cc-alert-filter-level { grid-column: span 2; }
                .cc-alert-chart-grid { grid-template-columns: minmax(0, 1fr); }
            }
            @media (max-width: 600px) {
                .cc-alert-kpi-grid,
                .cc-alert-filters { grid-template-columns: minmax(0, 1fr); }
                .cc-alert-filter-level { grid-column: auto; }
                .cc-alert-kpi-card { min-height: 120px; }
                .cc-alert-pagination { flex-wrap: wrap; }
                .cc-alerts-header { flex-wrap: wrap; }
            }
        </style>
        """
    )


def _render_alerts_page(token, api_base_url):
    _alerts_styles()
    saved_filters = (
        app.storage.user.get("nicegui_filters", {}).get("alertas", {})
    )
    initial_days = saved_filters.get("dias", 7)
    try:
        initial_days = int(initial_days)
    except (TypeError, ValueError):
        initial_days = 7
    if initial_days not in {7, 30, 90}:
        initial_days = 7

    with ui.row().classes("cc-alerts-header"):
        with ui.column().classes("gap-1 cc-alerts-title-block"):
            ui.label("Centro de alertas y notificaciones").classes(
                "cc-alerts-title"
            )
            ui.label(
                "Monitorea incidencias y eventos que requieren atención en ChargeCore"
            ).classes("cc-alerts-subtitle")
        refresh_button = ui.button(
            "Actualizar",
            icon="refresh",
        ).props("outline no-caps dense color=deep-orange-6").classes(
            "cc-alerts-refresh"
        )

    def request_alerts(days):
        result = api_get(
            "/monitoreo/alertas",
            params=_build_list_params(
                "alertas",
                {"dias": days, "nivel": "", "modulo": ""},
            ),
            token=token,
            base_url=api_base_url,
        )
        if isinstance(result, dict) and result.get("error"):
            _show_api_error(result)
            return None
        alerts = _rows_from_response(result)
        if alerts is None or not all(isinstance(row, dict) for row in alerts):
            show_error("La respuesta de alertas tiene un formato inesperado.")
            return None
        return alerts

    initial_alerts = request_alerts(initial_days)
    if initial_alerts is None:
        return
    state = {"alerts": initial_alerts, "page": 0}
    initial_levels = saved_filters.get("niveles")
    if not isinstance(initial_levels, list):
        old_level = _alert_level_key(saved_filters.get("nivel"))
        initial_levels = [old_level] if old_level else []
    selected_levels = {
        _alert_level_key(level)
        for level in initial_levels
        if _alert_level_key(level)
        in {item[0] for item in ALERT_LEVEL_PRESENTATION}
    }
    initial_module = str(saved_filters.get("modulo") or "")
    modules = sorted(
        {
            str(row.get("modulo") or "sistema").strip()
            for row in initial_alerts
            if str(row.get("modulo") or "").strip()
        },
        key=str.casefold,
    )
    module_options = {"": "Todos los módulos"}
    module_options.update({module: module for module in modules})
    if initial_module not in module_options:
        initial_module = ""

    with ui.element("div").classes("cc-alert-kpi-grid") as kpi_container:
        pass

    ui.label("Bandeja de alertas").classes("cc-alert-section-title")
    with ui.card().classes("cc-alert-filter-card"):
        with ui.element("div").classes("cc-alert-filters"):
            with ui.column().classes("min-w-0 gap-1"):
                period_filter = ui.select(
                    {7: "Últimos 7 días", 30: "Últimos 30 días", 90: "Últimos 90 días"},
                    value=initial_days,
                    label="Periodo",
                ).props("outlined dense").classes("w-full")
            with ui.column().classes(
                "min-w-0 gap-1 cc-alert-filter-level"
            ):
                ui.label("Nivel").classes("cc-alert-filter-label")
                with ui.row().classes("cc-alert-level-list"):
                    level_buttons = {}
                    for level, label, _, _, _ in ALERT_LEVEL_PRESENTATION:
                        level_button = ui.button(
                            label,
                        ).props("outline no-caps dense color=grey-5").classes(
                            "cc-alert-filter-chip"
                        )
                        if level in selected_levels:
                            level_button.props("color=deep-orange-6")
                            level_button.classes(
                                add="cc-alert-filter-chip-active"
                            )
                        level_buttons[level] = level_button
            with ui.column().classes("min-w-0 gap-1"):
                module_filter = ui.select(
                    module_options,
                    value=initial_module,
                    label="Módulo",
                ).props("outlined dense").classes("w-full")
            with ui.column().classes("min-w-0 gap-1"):
                search = ui.input(
                    "Buscar",
                    placeholder="Título, mensaje o recurso...",
                ).props("outlined dense clearable").classes("w-full")

    table_container = ui.column().classes("w-full")
    pagination_container = ui.row().classes("cc-alert-pagination w-full")
    ui.label("Actividad de alertas").classes("cc-alert-section-title")
    activity_container = ui.element("div").classes("cc-alert-chart-grid")

    def persist_filters():
        all_filters = app.storage.user.get("nicegui_filters", {})
        all_filters["alertas"] = {
            "dias": period_filter.value or 7,
            "nivel": (
                next(iter(selected_levels))
                if len(selected_levels) == 1
                else ""
            ),
            "niveles": sorted(selected_levels),
            "modulo": module_filter.value or "",
        }
        app.storage.user["nicegui_filters"] = all_filters

    def filtered_rows():
        rows = _alert_table_rows(state["alerts"])
        query = (search.value or "").strip().casefold()
        selected_module = str(module_filter.value or "").casefold()
        return [
            row
            for row in rows
            if (not selected_levels or row["nivel_original"] in selected_levels)
            and (
                not selected_module
                or row["modulo"].casefold() == selected_module
            )
            and (
                not query
                or query
                in " ".join(
                    row[key]
                    for key in ("titulo", "modulo", "recurso", "nivel", "estado")
                ).casefold()
            )
        ]

    def render_kpis():
        kpi_container.clear()
        totals = _alert_summary_values(state["alerts"])
        cards = (
            ("Alertas encontradas", totals[0], "En el periodo seleccionado", "notifications", "#fbbf24"),
            ("Críticas", totals[1], "Requieren acción inmediata", "emergency", "#ef4444"),
            ("Advertencias", totals[2], "Revisar pronto", "warning", "#f59e0b"),
            ("Eventos OCPP", totals[3], "Originados en comunicación OCPP", "electrical_services", "#60a5fa"),
        )
        with kpi_container:
            for index, (label, value, caption, icon, color) in enumerate(cards):
                card_class = (
                    "chargecore-card cc-alert-kpi-card cc-alert-kpi-primary"
                    if index == 0
                    else "chargecore-card cc-alert-kpi-card"
                )
                with ui.card().classes(card_class):
                    with ui.element("div").classes("cc-alert-kpi-icon"):
                        ui.icon(icon).style(
                            f"color: {color}; font-size: 18px;"
                        )
                    ui.label(label).classes("cc-alert-kpi-label")
                    ui.label(str(value)).classes("cc-alert-kpi-value")
                    ui.label(caption).classes("cc-alert-kpi-caption")

    def render_table():
        rows = filtered_rows()
        page_count = max(
            1,
            (len(rows) + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE,
        )
        state["page"] = min(max(state["page"], 0), page_count - 1)
        start = state["page"] * RECORDS_PER_PAGE
        visible_rows = rows[start : start + RECORDS_PER_PAGE]
        table_container.clear()
        pagination_container.clear()
        columns = [
            ("nivel", "Nivel", "12%"),
            ("titulo", "Título", "25%"),
            ("modulo", "Módulo", "11%"),
            ("recurso", "Recurso", "21%"),
            ("fecha", "Fecha", "14%"),
            ("estado", "Estado", "11%"),
            ("acciones", "Acciones", "6%"),
        ]
        with table_container:
            with ui.card().classes("cc-alert-table-card"):
                table = ui.table(
                    columns=[
                        {
                            "name": name,
                            "label": label,
                            "field": name,
                            "align": "left",
                            "sortable": name != "acciones",
                            "style": f"width: {width};",
                            "headerStyle": f"width: {width};",
                        }
                        for name, label, width in columns
                    ],
                    rows=visible_rows,
                    row_key="fila",
                ).props(
                    'dense flat wrap-cells no-data-label="Sin alertas para mostrar."'
                ).classes("w-full cc-alert-table")
                table.add_slot(
                    "body-cell-nivel",
                    """
                    <q-td :props="props">
                      <span class="cc-alert-pill" :class="'cc-alert-level-' + props.row.nivel_clase">
                        {{ props.value }}
                      </span>
                    </q-td>
                    """,
                )
                table.add_slot(
                    "body-cell-titulo",
                    """
                    <q-td :props="props">
                      <span class="cc-alert-title-cell" :title="props.value">
                        {{ props.value }}
                      </span>
                    </q-td>
                    """,
                )
                table.add_slot(
                    "body-cell-estado",
                    """
                    <q-td :props="props">
                      <span class="cc-alert-pill" :class="'cc-alert-status-' + props.row.estado_clase">
                        {{ props.value }}
                      </span>
                    </q-td>
                    """,
                )
                table.add_slot(
                    "body-cell-acciones",
                    """
                    <q-td :props="props">
                      <q-btn flat round dense icon="more_vert"
                        color="grey-5" class="cc-alert-row-action"
                        aria-label="Acciones de alerta" />
                    </q-td>
                    """,
                )
        with pagination_container:
            shown = len(visible_rows)
            range_text = (
                f"Mostrando {start + 1}–{start + shown} de {len(rows)} alertas"
                if shown
                else "Mostrando 0 de 0 alertas"
            )
            ui.label(
                f"{range_text} · Página {state['page'] + 1} de {page_count}"
            ).classes("text-xs text-slate-400")
            with ui.row().classes("gap-2"):
                ui.button(
                    "Anterior",
                    icon="chevron_left",
                    on_click=lambda: change_page(-1),
                ).props("flat no-caps dense color=deep-orange-6").set_enabled(
                    state["page"] > 0
                )
                ui.button(
                    "Siguiente",
                    icon="chevron_right",
                    on_click=lambda: change_page(1),
                ).props(
                    "flat no-caps dense color=deep-orange-6"
                ).set_enabled(state["page"] + 1 < page_count)

    def change_page(offset):
        state["page"] += offset
        render_table()

    def render_activity():
        activity_container.clear()
        with activity_container:
            with ui.card().classes("chargecore-card cc-alert-chart-card"):
                ui.label("Alertas de los últimos 7 días").classes(
                    "text-sm font-bold text-white"
                )
                points = _alert_chart_points(state["alerts"])
                ui.echart(
                    {
                        "tooltip": {"trigger": "axis"},
                        "grid": {"left": 24, "right": 10, "top": 18, "bottom": 24},
                        "xAxis": {
                            "type": "category",
                            "data": [point["label"] for point in points],
                            "axisLabel": {"color": "#a8a49e", "fontSize": 9},
                            "axisLine": {"lineStyle": {"color": "#3a3430"}},
                        },
                        "yAxis": {
                            "type": "value",
                            "minInterval": 1,
                            "axisLabel": {"color": "#a8a49e", "fontSize": 9},
                            "splitLine": {"lineStyle": {"color": "#34302d"}},
                        },
                        "series": [
                            {
                                "type": "bar",
                                "data": [point["value"] for point in points],
                                "itemStyle": {
                                    "color": "#f45b24",
                                    "borderRadius": [3, 3, 0, 0],
                                },
                                "barMaxWidth": 28,
                                "label": {
                                    "show": True,
                                    "position": "top",
                                    "color": "#f4f4f5",
                                    "fontSize": 9,
                                },
                            }
                        ],
                        "textStyle": {"color": "#d4d4d8"},
                        "backgroundColor": "transparent",
                    }
                ).classes("h-48 w-full")

            with ui.card().classes("chargecore-card cc-alert-chart-card"):
                ui.label("Origen de incidencias").classes(
                    "text-sm font-bold text-white"
                )
                ui.label("Por módulo, según los filtros").classes(
                    "text-xs text-slate-400"
                )
                module_counts = {}
                for alert in filtered_rows():
                    module_name = alert["modulo"]
                    module_counts[module_name] = (
                        module_counts.get(module_name, 0) + 1
                    )
                max_count = max(module_counts.values(), default=1)
                if not module_counts:
                    ui.label("Sin datos para mostrar.").classes(
                        "text-sm text-slate-400"
                    )
                for module_name, count in sorted(
                    module_counts.items(),
                    key=lambda item: (-item[1], item[0].casefold()),
                ):
                    with ui.column().classes("cc-alert-module-row"):
                        with ui.row().classes(
                            "w-full items-center justify-between"
                        ):
                            ui.label(module_name).classes(
                                "text-xs text-slate-300"
                            )
                            ui.label(str(count)).classes(
                                "text-xs font-semibold text-white"
                            )
                        ui.linear_progress(
                            count / max_count,
                            color="orange",
                        ).props("rounded").classes(
                            "w-full cc-alert-module-progress"
                        )

    def render_all():
        render_kpis()
        render_table()
        render_activity()

    def save_current_filters():
        persist_filters()
        state["page"] = 0
        render_table()
        render_activity()

    def toggle_level(level):
        if level in selected_levels:
            selected_levels.remove(level)
            level_buttons[level].props("color=grey-5")
            level_buttons[level].classes(
                remove="cc-alert-filter-chip-active"
            )
        else:
            selected_levels.add(level)
            level_buttons[level].props("color=deep-orange-6")
            level_buttons[level].classes(
                add="cc-alert-filter-chip-active"
            )
        save_current_filters()

    def on_period_change(_):
        persist_filters()
        alerts = request_alerts(period_filter.value)
        if alerts is None:
            return
        state["alerts"] = alerts
        state["page"] = 0
        available_modules = sorted(
            {
                str(row.get("modulo") or "sistema").strip()
                for row in alerts
                if str(row.get("modulo") or "").strip()
            },
            key=str.casefold,
        )
        options = {"": "Todos los módulos"}
        options.update({module: module for module in available_modules})
        current_module = module_filter.value or ""
        if current_module not in options:
            current_module = ""
        module_filter.set_options(options, value=current_module)
        persist_filters()
        render_all()

    def refresh_alerts():
        alerts = request_alerts(period_filter.value)
        if alerts is None:
            return
        state["alerts"] = alerts
        state["page"] = 0
        render_all()

    def make_level_handler(selected_level):
        def handle():
            toggle_level(selected_level)

        return handle

    for level, _, _, _, _ in ALERT_LEVEL_PRESENTATION:
        level_buttons[level].on_click(make_level_handler(level))
    period_filter.on_value_change(on_period_change)
    module_filter.on_value_change(lambda _: save_current_filters())
    search.on_value_change(lambda _: save_current_filters())
    refresh_button.on_click(refresh_alerts)
    render_all()


def _session_folio(record):
    transaction_id = record.get("transaction_id")
    try:
        return f"S-{int(transaction_id):04d}"
    except (TypeError, ValueError):
        fallback = str(record.get("_id") or "----")
        return f"S-{fallback[-4:].upper()}"


def _format_session_datetime(value):
    if not value:
        return "—"
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError):
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


def _format_session_duration(minutes):
    try:
        total_minutes = int(float(minutes))
    except (OverflowError, TypeError, ValueError):
        return "—"
    if total_minutes < 0:
        return "—"
    hours, remaining_minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours} h {remaining_minutes} min"
    return f"{remaining_minutes} min"


def _format_session_energy(energy):
    try:
        value = float(energy)
    except (TypeError, ValueError):
        return "—"
    return f"{value:g} kWh" if math.isfinite(value) else "—"


def _session_status(record):
    state = str(record.get("estado") or "").casefold()
    labels = {
        "activa": ("Activa", "active"),
        "iniciada": ("Activa", "active"),
        "finalizada": ("Finalizada", "finished"),
        "error": ("Error", "error"),
    }
    return labels.get(state, (state.capitalize() or "Sin estado", "default"))


def _session_summary_cards(rows):
    states = [str(row.get("estado") or "").casefold() for row in rows]
    energy_values = [
        float(value)
        for row in rows
        if isinstance((value := row.get("energia_consumida_kwh")), (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ]
    durations = [
        float(value)
        for row in rows
        if str(row.get("estado") or "").casefold() == "finalizada"
        and isinstance((value := row.get("duracion_minutos")), (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value > 0
    ]
    total_energy = math.fsum(energy_values)
    average_duration = (
        f"{round(math.fsum(durations) / len(durations))} min"
        if durations
        else "—"
    )
    return (
        (
            "Sesiones en el rango",
            len(rows),
            "Según los filtros aplicados",
            "history",
            "#f97316",
        ),
        (
            "Activas ahora",
            sum(state in {"activa", "iniciada"} for state in states),
            "Cargando en este momento",
            "battery_charging_full",
            "#22c55e",
        ),
        (
            "Energía entregada",
            f"{total_energy:g} kWh",
            "En el rango seleccionado",
            "bolt",
            "#f97316",
        ),
        (
            "Duración promedio",
            average_duration,
            "Sesiones finalizadas",
            "timer",
            "#a78bfa",
        ),
    )


def _show_session_details(record):
    state_label, state_class = _session_status(record)
    vehicle = " ".join(
        str(record.get(field) or "").strip()
        for field in ("vehiculo_marca", "vehiculo_modelo")
        if record.get(field)
    )
    if not vehicle:
        vehicle = record.get("vehiculo_placa") or "—"
    charger = record.get("cargador_nombre") or record.get("charge_point_id") or "—"
    if record.get("connector_id") is not None:
        charger = f"{charger} · conector {record['connector_id']}"
    energy = _format_session_energy(record.get("energia_consumida_kwh"))
    duration = _format_session_duration(record.get("duracion_minutos"))
    average_power = record.get("potencia_promedio_kw")
    if isinstance(average_power, (int, float)) and math.isfinite(
        float(average_power)
    ):
        average_power = f"{float(average_power):g} kW"
    else:
        average_power = "—"

    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-3xl gap-4 p-6"
    ):
        with ui.row().classes("w-full items-start justify-between gap-3"):
            with ui.column().classes("gap-2"):
                ui.label(f"Sesión {_session_folio(record)}").classes(
                    "text-xl font-bold text-white"
                )
                ui.label(state_label).classes(
                    f"cc-session-status cc-session-status-{state_class}"
                )
            ui.button(icon="close", on_click=dialog.close).props(
                "flat round dense"
            )
        ui.separator()
        with ui.element("div").classes(
            "grid w-full grid-cols-1 gap-4 sm:grid-cols-3"
        ):
            for icon, label, value in (
                ("ev_station", "Cargador", charger),
                ("person", "Usuario", record.get("usuario_nombre") or "Sin usuario"),
                ("directions_car", "Vehículo", vehicle),
                ("schedule", "Inicio", _format_session_datetime(record.get("fecha_inicio"))),
                ("timer", "Duración", duration),
                ("bolt", "Energía", energy),
            ):
                with ui.column().classes("min-w-0 gap-1"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon(icon).classes("text-orange-400")
                        ui.label(label).classes("text-sm font-semibold text-white")
                    ui.label(str(value)).classes(
                        "break-words text-sm text-slate-300"
                    )
        ui.separator()
        with ui.element("div").classes(
            "grid w-full grid-cols-1 gap-4 sm:grid-cols-2"
        ):
            for label, value in (
                ("Potencia promedio", average_power),
                ("Motivo de cierre", record.get("motivo_fin") or "—"),
            ):
                with ui.column().classes("min-w-0 gap-1"):
                    ui.label(label).classes("text-sm font-semibold text-white")
                    ui.label(str(value)).classes("break-words text-sm text-slate-400")
    dialog.open()


def _render_sessions_page(token, api_base_url, user):
    saved_filters = app.storage.user.get("nicegui_filters", {}).get("sesiones", {})
    role = user.get("rol")

    charger_result = api_get(
        "/cargadores/",
        token=token,
        base_url=api_base_url,
    )
    if isinstance(charger_result, dict) and charger_result.get("error"):
        _show_api_error(charger_result, "No se pudieron cargar los cargadores.")
        return
    if not isinstance(charger_result, list):
        show_error("La respuesta de cargadores no tiene el formato esperado.")
        return
    charger_options = {
        "": "Todos",
        **{
            item["charge_point_id"]: item.get("nombre")
            or item["charge_point_id"]
            for item in charger_result
            if isinstance(item, dict) and item.get("charge_point_id")
        },
    }

    user_options = None
    if role in {"superadmin", "dueno_empresa"}:
        users = _load_all_users(token, api_base_url)
        if isinstance(users, dict) and users.get("error"):
            _show_api_error(users, "No se pudieron cargar los usuarios.")
            return
        user_options = {
            "": "Todos",
            **{
                item["_id"]: item.get("nombre", item.get("email", "Usuario"))
                for item in (_rows_from_response(users) or [])
                if isinstance(item, dict) and item.get("_id")
            },
        }

    filter_values = {
        "estado": saved_filters.get("estado", ""),
        "charge_point_id": saved_filters.get("charge_point_id", ""),
        "fecha_inicio": saved_filters.get("fecha_inicio", ""),
        "fecha_fin": saved_filters.get("fecha_fin", ""),
        "usuario_id": saved_filters.get("usuario_id", ""),
    }
    params = _build_list_params("sesiones", filter_values)
    result = api_get(
        "/sesiones/",
        params=params,
        token=token,
        base_url=api_base_url,
    )
    if isinstance(result, dict) and result.get("error"):
        _show_api_error(result, "No se pudieron cargar las sesiones.")
        return
    rows = _rows_from_response(result)
    if rows is None or not all(isinstance(row, dict) for row in rows):
        show_error("La respuesta de sesiones no tiene el formato esperado.")
        return

    ui.add_head_html(
        """
        <style>
            .cc-sessions-page {
                display: flex;
                flex-direction: column;
                gap: 18px;
                width: 100%;
            }
            .cc-session-section-title {
                display: flex;
                align-items: center;
                gap: 9px;
                color: var(--cc-text-primary);
                font-size: 14px;
                font-weight: 700;
            }
            .cc-session-section-title::before {
                width: 3px;
                height: 16px;
                border-radius: 2px;
                background: var(--cc-primary);
                content: "";
            }
            .cc-session-filter-grid {
                display: grid;
                grid-template-columns: repeat(6, minmax(0, 1fr));
                gap: 10px;
                width: 100%;
                align-items: end;
            }
            .cc-session-filter-grid > * {
                min-width: 0;
                width: 100%;
            }
            .cc-session-table-wrap {
                width: 100%;
                overflow-x: hidden;
            }
            .cc-session-table-head,
            .cc-session-table-row {
                display: grid;
                grid-template-columns:
                    minmax(55px, .75fr)
                    minmax(90px, 1.3fr)
                    minmax(70px, 1fr)
                    minmax(90px, 1.3fr)
                    minmax(95px, 1.15fr)
                    minmax(56px, .7fr)
                    minmax(56px, .7fr)
                    minmax(72px, .8fr)
                    40px;
                align-items: center;
                column-gap: 8px;
                width: 100%;
                padding: 0 12px;
            }
            .cc-session-table-head {
                min-height: 42px;
                border-bottom: 1px solid var(--cc-border);
                color: var(--cc-text-muted);
                font-size: 10px;
                font-weight: 700;
                letter-spacing: .04em;
                text-transform: uppercase;
            }
            .cc-session-table-row {
                min-height: 43px;
                border-bottom: 1px solid var(--cc-border);
                color: var(--cc-text-secondary);
                font-size: 11px;
            }
            .cc-session-table-row:hover {
                background: var(--cc-field);
            }
            .cc-session-cell {
                min-width: 0;
                overflow-wrap: anywhere;
            }
            .cc-session-folio {
                color: var(--cc-text-primary);
                font-weight: 700;
            }
            .cc-session-status {
                display: inline-flex;
                align-items: center;
                gap: 6px;
                width: fit-content;
                max-width: 100%;
                padding: 4px 9px;
                border-radius: 999px;
                font-size: 10px;
                font-weight: 600;
                white-space: nowrap;
            }
            .cc-session-status::before {
                width: 6px;
                height: 6px;
                border-radius: 50%;
                background: currentColor;
                content: "";
            }
            .cc-session-status-active {
                background: rgba(47, 111, 224, .14);
                color: #5d91ee;
            }
            .cc-session-status-finished {
                background: rgba(31, 166, 90, .14);
                color: #24ad62;
            }
            .cc-session-status-error {
                background: rgba(214, 56, 47, .14);
                color: #e35b52;
            }
            .cc-session-status-default {
                background: rgba(139, 143, 152, .15);
                color: var(--cc-text-secondary);
            }
            .cc-session-empty {
                grid-column: 1 / -1;
                padding: 18px 0;
                color: var(--cc-text-muted);
                text-align: center;
            }
            .cc-session-footer {
                min-height: 48px;
                padding: 8px 12px 0;
                color: var(--cc-text-muted);
                font-size: 11px;
            }
            @media (max-width: 1000px) {
                .cc-session-filter-grid {
                    grid-template-columns: repeat(3, minmax(0, 1fr));
                }
            }
            @media (max-width: 600px) {
                .cc-session-filter-grid {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }
                .cc-session-table-head,
                .cc-session-table-row {
                    column-gap: 4px;
                    padding: 0 6px;
                    font-size: 9px;
                }
                .cc-session-status {
                    gap: 3px;
                    padding: 3px 5px;
                    font-size: 9px;
                }
            }
        </style>
        """
    )

    with ui.element("div").classes("cc-sessions-page"):
        _render_summary_cards(_session_summary_cards(rows))

        ui.label("Filtrar").classes("cc-session-section-title")
        with ui.card().classes("chargecore-card w-full p-4"):
            with ui.element("div").classes("cc-session-filter-grid"):
                session_state = ui.select(
                    {
                        "": "Todas",
                        "activa": "Activa",
                        "iniciada": "Iniciada",
                        "finalizada": "Finalizada",
                        "error": "Error",
                    },
                    value=filter_values["estado"],
                    label="Estado",
                ).props("outlined")
                start_date = ui.input(
                    "Desde",
                    value=filter_values["fecha_inicio"],
                ).props("outlined type=date")
                end_date = ui.input(
                    "Hasta",
                    value=filter_values["fecha_fin"],
                ).props("outlined type=date")
                charger_filter = ui.select(
                    charger_options,
                    value=filter_values["charge_point_id"],
                    label="Cargador",
                ).props("outlined")
                filter_controls = {
                    "estado": session_state,
                    "charge_point_id": charger_filter,
                    "fecha_inicio": start_date,
                    "fecha_fin": end_date,
                }
                if user_options is not None:
                    filter_controls["usuario_id"] = ui.select(
                        user_options,
                        value=filter_values["usuario_id"],
                        label="Usuario",
                    ).props("outlined")
                search = ui.input(
                    "Buscar",
                    value=saved_filters.get("buscar", ""),
                    placeholder="Cargador, vehículo o folio",
                ).props("outlined clearable")

        ui.label("Sesiones").classes("cc-session-section-title")
        with ui.card().classes("chargecore-card cc-session-table-card w-full p-3"):
            table_container = ui.column().classes("cc-session-table-wrap w-full")
        page_state = {"number": 1, "expanded": False}

        def filter_rows():
            query = (search.value or "").strip().casefold()
            return [
                row
                for row in rows
                if not query
                or query
                in " ".join(
                    str(row.get(field) or "")
                    for field in (
                        "transaction_id",
                        "charge_point_id",
                        "cargador_nombre",
                        "usuario_nombre",
                        "vehiculo_placa",
                        "vehiculo_marca",
                        "vehiculo_modelo",
                    )
                ).casefold()
                or query in _session_folio(row).casefold()
            ]

        def render_table():
            filtered_rows = filter_rows()
            page_count = max(
                1,
                (len(filtered_rows) + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE,
            )
            page_state["number"] = min(
                max(page_state["number"], 1),
                page_count,
            )
            if page_state["expanded"]:
                start = (page_state["number"] - 1) * RECORDS_PER_PAGE
                visible_rows = filtered_rows[start : start + RECORDS_PER_PAGE]
                end = start + len(visible_rows)
                footer = (
                    f"Mostrando {start + 1}–{end} de {len(filtered_rows)} sesiones"
                    f" · Página {page_state['number']} de {page_count}"
                    if filtered_rows
                    else "Mostrando 0 de 0 sesiones"
                )
            else:
                visible_rows = filtered_rows[:SESSION_INITIAL_ROWS]
                footer = (
                    f"Mostrando {len(visible_rows)} de {len(filtered_rows)} sesiones"
                )

            table_container.clear()
            with table_container:
                with ui.element("div").classes("cc-session-table-head"):
                    for label in (
                        "Folio",
                        "Cargador",
                        "Usuario",
                        "Vehículo",
                        "Inicio",
                        "Duración",
                        "Energía",
                        "Estado",
                        "Acciones",
                    ):
                        ui.label(label)
                if visible_rows:
                    for record in visible_rows:
                        with ui.element("div").classes("cc-session-table-row"):
                            ui.label(_session_folio(record)).classes(
                                "cc-session-cell cc-session-folio"
                            )
                            ui.label(
                                record.get("cargador_nombre")
                                or record.get("charge_point_id")
                                or "—"
                            ).classes("cc-session-cell")
                            ui.label(
                                record.get("usuario_nombre") or "Sin usuario"
                            ).classes("cc-session-cell")
                            vehicle = " ".join(
                                str(record.get(field) or "").strip()
                                for field in ("vehiculo_marca", "vehiculo_modelo")
                                if record.get(field)
                            )
                            ui.label(
                                vehicle or record.get("vehiculo_placa") or "—"
                            ).classes("cc-session-cell")
                            ui.label(
                                _format_session_datetime(record.get("fecha_inicio"))
                            ).classes("cc-session-cell")
                            ui.label(
                                _format_session_duration(
                                    record.get("duracion_minutos")
                                )
                            ).classes("cc-session-cell")
                            ui.label(
                                _format_session_energy(
                                    record.get("energia_consumida_kwh")
                                )
                            ).classes("cc-session-cell")
                            status_label, status_class = _session_status(record)
                            ui.label(status_label).classes(
                                f"cc-session-status "
                                f"cc-session-status-{status_class}"
                            )
                            with ui.element("div").classes("cc-session-cell"):
                                with ui.button(icon="more_vert").props(
                                    "flat round dense"
                                ):
                                    with ui.menu().props(
                                        'anchor="bottom right" self="top right"'
                                    ):
                                        ui.menu_item(
                                            "Ver detalle",
                                            on_click=lambda item=record: _show_session_details(
                                                item
                                            ),
                                        )
                else:
                    with ui.element("div").classes("cc-session-table-row"):
                        ui.label(
                            "No hay sesiones que coincidan con los filtros."
                        ).classes("cc-session-empty")

                with ui.row().classes(
                    "cc-session-footer w-full items-center justify-between gap-2"
                ):
                    if page_state["expanded"] and page_count > 1:
                        ui.button(
                            "Anterior",
                            icon="chevron_left",
                            on_click=lambda: change_page(-1),
                        ).props("flat no-caps").set_enabled(
                            page_state["number"] > 1
                        )
                    else:
                        ui.element("div")
                    ui.label(footer).classes("text-center")
                    with ui.row().classes("items-center gap-2"):
                        if page_state["expanded"]:
                            ui.button(
                                "Ver menos ↑",
                                on_click=collapse_rows,
                            ).props("flat no-caps")
                        elif len(filtered_rows) > SESSION_INITIAL_ROWS:
                            ui.button(
                                "Ver más ↓",
                                on_click=expand_rows,
                            ).props("flat no-caps")
                        if page_state["expanded"] and page_count > 1:
                            ui.button(
                                "Siguiente",
                                icon="chevron_right",
                                on_click=lambda: change_page(1),
                            ).props("flat no-caps").set_enabled(
                                page_state["number"] < page_count
                            )

        def change_page(offset):
            page_state["number"] += offset
            render_table()

        def expand_rows():
            page_state["expanded"] = True
            page_state["number"] = 1
            render_table()

        def collapse_rows():
            page_state["expanded"] = False
            page_state["number"] = 1
            render_table()

        def save_server_filters(_):
            all_filters = app.storage.user.get("nicegui_filters", {})
            all_filters["sesiones"] = {
                **saved_filters,
                **{
                    name: control.value or ""
                    for name, control in filter_controls.items()
                },
                "buscar": search.value or "",
            }
            app.storage.user["nicegui_filters"] = all_filters
            page_state["number"] = 1
            page_state["expanded"] = False
            ui.navigate.reload()

        def filter_search(_):
            all_filters = app.storage.user.get("nicegui_filters", {})
            all_filters["sesiones"] = {
                **saved_filters,
                **{
                    name: control.value or ""
                    for name, control in filter_controls.items()
                },
                "buscar": search.value or "",
            }
            app.storage.user["nicegui_filters"] = all_filters
            page_state["number"] = 1
            page_state["expanded"] = False
            render_table()

        for control in filter_controls.values():
            control.on_value_change(save_server_filters)
        search.on_value_change(filter_search)
        render_table()


def _ocpp_message_status(message):
    message_type = str(message.get("message_type_name") or "").upper()
    error_code = str(message.get("error_code") or "").casefold()
    if message_type == "CALLERROR" or error_code not in {
        "",
        "0",
        "noerror",
        "none",
    }:
        return "Error", "error"
    return "Correcto", "success"


def _ocpp_timestamp_parts(value):
    if not value:
        return "—", "—"
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError):
        return str(value), "—"
    time_label = parsed.strftime("%H:%M:%S")
    if parsed.microsecond:
        time_label += f".{parsed.microsecond // 1000:03d}"
    return time_label, parsed.strftime("%Y-%m-%d")


def _ocpp_operator_summary(message):
    status, _ = _ocpp_message_status(message)
    if status == "Error":
        return (
            "La comunicación OCPP reportó un error.",
            "El mensaje quedó registrado en el historial.",
        )
    direction = str(message.get("direction") or "").casefold()
    if direction == "outgoing":
        return (
            "El sistema envió este mensaje al cargador.",
            "El mensaje se registró en el historial.",
        )
    if direction == "incoming":
        return (
            "El cargador envió este mensaje al sistema.",
            "El mensaje se registró en el historial.",
        )
    return (
        "El sistema registró este mensaje OCPP.",
        "La dirección del mensaje no está disponible.",
    )


def _ocpp_summary_cards(messages, errors):
    incoming = sum(
        str(message.get("direction") or "").casefold() == "incoming"
        for message in messages
    )
    outgoing = sum(
        str(message.get("direction") or "").casefold() == "outgoing"
        for message in messages
    )
    total = len(messages)
    incoming_percent = round(incoming * 100 / total) if total else 0
    outgoing_percent = round(outgoing * 100 / total) if total else 0
    return (
        (
            "Mensajes recibidos",
            total,
            "En el historial cargado",
            "chat_bubble",
            "#f97316",
        ),
        (
            "Entrantes",
            incoming,
            f"{incoming_percent}% del historial",
            "call_received",
            "#22c55e",
        ),
        (
            "Salientes",
            outgoing,
            f"{outgoing_percent}% del historial",
            "call_made",
            "#60a5fa",
        ),
        (
            "Errores OCPP",
            len(errors),
            "Sin incidencias" if not errors else "Incidencias registradas",
            "emergency",
            "#ef4444",
        ),
    )


def _show_ocpp_event_details(message, dialog, details_container):
    status, status_class = _ocpp_message_status(message)
    time_label, date_label = _ocpp_timestamp_parts(message.get("timestamp"))
    operator_summary, history_summary = _ocpp_operator_summary(message)
    original_message = message.get("raw_message")
    if original_message is None:
        original_message = message.get("payload")
    if original_message is None:
        original_message = "Este mensaje no contiene información adicional."
    technical_data = {
        "Dirección": message.get("direction"),
        "Tipo de mensaje": message.get("message_type_name")
        or message.get("message_type"),
        "Identificador": message.get("unique_id"),
        "Acción OCPP": message.get("action"),
        "Protocolo OCPP": message.get("subprotocolo_ocpp"),
        "Código de error": message.get("error_code"),
        "Descripción del error": message.get("error_description"),
        "Mensaje original": original_message,
    }
    details_container.clear()
    with details_container:
        with ui.row().classes("w-full items-start justify-between gap-3"):
            with ui.column().classes("gap-2"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("visibility").classes("text-orange-400")
                    ui.label("Inspeccionar detalle").classes(
                        "text-xl font-bold text-white"
                    )
                ui.label(f"Estado: {status}").classes(
                    f"cc-ocpp-status cc-ocpp-status-{status_class}"
                )
            ui.button(icon="close", on_click=dialog.close).props(
                "flat round dense"
            )
        ui.separator()
        ui.label("Resumen para el operador").classes(
            "text-lg font-semibold text-white"
        )
        ui.label(operator_summary).classes("text-sm text-slate-300")
        ui.label(history_summary).classes("text-sm text-slate-300")
        with ui.element("div").classes(
            "grid w-full grid-cols-1 gap-4 sm:grid-cols-3"
        ):
            for label, value in (
                (
                    "Cargador",
                    message.get("charge_point_id") or "—",
                ),
                ("Acción", message.get("action") or "Sin acción"),
                ("Hora", f"{date_label} {time_label}"),
            ):
                with ui.column().classes("min-w-0 gap-1"):
                    ui.label(label).classes(
                        "text-xs font-semibold uppercase text-slate-400"
                    )
                    ui.label(str(value)).classes(
                        "break-words text-sm text-white"
                    )
        ui.separator()
        ui.label("Datos técnicos OCPP").classes(
            "text-lg font-semibold text-white"
        )
        with ui.element("pre").classes(
            "cc-ocpp-technical-data w-full overflow-auto rounded-lg p-4 "
            "text-xs text-slate-300"
        ):
            ui.label(
                json.dumps(
                    technical_data,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            ).classes("whitespace-pre-wrap break-all font-mono")
    dialog.open()


def _ocpp_inspection_handler(message, dialog, details_container):
    def inspect_message():
        _show_ocpp_event_details(message, dialog, details_container)

    return inspect_message


def _render_ocpp_events(token, api_base_url):
    chargers = api_get(
        "/cargadores/",
        token=token,
        base_url=api_base_url,
    )
    if isinstance(chargers, dict) and chargers.get("error"):
        _show_api_error(chargers, "No se pudieron cargar los cargadores.")
        return
    if not isinstance(chargers, list):
        show_error("La respuesta de cargadores no tiene el formato esperado.")
        return
    charger_ids = [
        item.get("charge_point_id")
        for item in chargers
        if isinstance(item, dict) and item.get("charge_point_id")
    ]
    saved = app.storage.user.get("nicegui_filters", {}).get("eventos", {})
    options = {"": "Todos los cargadores", **{item: item for item in charger_ids}}
    message_limit = int(saved.get("limit", 100) or 100)
    selected_charger_value = saved.get("charge_point_id", "")
    selected_ids = (
        [selected_charger_value]
        if selected_charger_value
        else charger_ids
    )
    messages = []
    for charge_point_id in selected_ids:
        result = api_get(
            f"/monitoreo/ocpp-messages/{charge_point_id}",
            params={"limit": message_limit},
            token=token,
            base_url=api_base_url,
        )
        if isinstance(result, dict) and result.get("error"):
            _show_api_error(result, "No se pudo cargar el historial OCPP.")
            return
        if not isinstance(result, list):
            show_error("El historial OCPP no tiene el formato esperado.")
            return
        messages.extend(result)
    messages.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    messages = messages[:message_limit]

    error_params = {"limit": message_limit}
    if selected_charger_value:
        error_params["charge_point_id"] = selected_charger_value
    errors = api_get(
        "/monitoreo/ocpp-errors",
        params=error_params,
        token=token,
        base_url=api_base_url,
    )
    if isinstance(errors, dict) and errors.get("error"):
        _show_api_error(errors, "No se pudieron cargar los errores OCPP.")
        return
    if not isinstance(errors, list):
        show_error("La respuesta de errores OCPP no tiene el formato esperado.")
        return

    actions = sorted(
        {
            str(message["action"])
            for message in messages
            if message.get("action")
        },
        key=str.casefold,
    )
    action_options = {"": "Todas"}
    action_options.update({item: item for item in actions})
    saved_action = saved.get("action", "")
    if saved_action and saved_action not in action_options:
        action_options[saved_action] = saved_action

    ui.add_head_html(
        """
        <style>
            .cc-ocpp-page {
                display: flex;
                flex-direction: column;
                gap: 18px;
                width: 100%;
            }
            .cc-ocpp-section-title {
                display: flex;
                align-items: center;
                gap: 9px;
                color: var(--cc-text-primary);
                font-size: 14px;
                font-weight: 700;
            }
            .cc-ocpp-section-title::before {
                width: 3px;
                height: 16px;
                border-radius: 2px;
                background: var(--cc-primary);
                content: "";
            }
            .cc-ocpp-filter-grid {
                display: grid;
                grid-template-columns: repeat(5, minmax(0, 1fr));
                gap: 10px;
                width: 100%;
                align-items: end;
            }
            .cc-ocpp-filter-grid > * {
                min-width: 0;
                width: 100%;
            }
            .cc-ocpp-table-wrap {
                width: 100%;
                overflow-x: auto;
            }
            .cc-ocpp-table-head,
            .cc-ocpp-table-row {
                display: grid;
                grid-template-columns:
                    minmax(100px, .9fr)
                    minmax(150px, 1.4fr)
                    minmax(95px, .9fr)
                    minmax(125px, 1.2fr)
                    minmax(100px, .9fr)
                    minmax(90px, .8fr)
                    minmax(95px, .9fr)
                    40px;
                align-items: center;
                column-gap: 10px;
                width: 100%;
                min-width: 840px;
                padding: 0 12px;
            }
            .cc-ocpp-table-head {
                min-height: 42px;
                border-bottom: 1px solid var(--cc-border);
                color: var(--cc-text-muted);
                font-size: 10px;
                font-weight: 700;
                letter-spacing: .04em;
                text-transform: uppercase;
            }
            .cc-ocpp-table-row {
                min-height: 46px;
                border-bottom: 1px solid var(--cc-border);
                color: var(--cc-text-secondary);
                font-size: 11px;
            }
            .cc-ocpp-table-row:hover {
                background: var(--cc-field);
            }
            .cc-ocpp-cell {
                min-width: 0;
                overflow-wrap: anywhere;
            }
            .cc-ocpp-status {
                display: inline-flex;
                align-items: center;
                gap: 6px;
                width: fit-content;
                max-width: 100%;
                padding: 4px 9px;
                border-radius: 999px;
                font-size: 10px;
                font-weight: 600;
                white-space: nowrap;
            }
            .cc-ocpp-status::before {
                width: 6px;
                height: 6px;
                border-radius: 50%;
                background: currentColor;
                content: "";
            }
            .cc-ocpp-status-success {
                background: rgba(31, 166, 90, .14);
                color: #24ad62;
            }
            .cc-ocpp-status-error {
                background: rgba(214, 56, 47, .14);
                color: #e35b52;
            }
            .cc-ocpp-empty {
                grid-column: 1 / -1;
                padding: 20px 0;
                color: var(--cc-text-muted);
                text-align: center;
            }
            .cc-ocpp-footer {
                min-height: 48px;
                padding: 8px 12px 0;
                color: var(--cc-text-muted);
                font-size: 11px;
            }
            .cc-ocpp-technical-data {
                border: 1px solid var(--cc-border);
                background: var(--cc-field);
                white-space: pre-wrap;
            }
            @media (max-width: 900px) {
                .cc-ocpp-filter-grid {
                    grid-template-columns: repeat(3, minmax(0, 1fr));
                }
            }
            @media (max-width: 600px) {
                .cc-ocpp-filter-grid {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }
            }
        </style>
        """
    )

    with ui.element("div").classes("cc-ocpp-page"):
        _render_summary_cards(_ocpp_summary_cards(messages, errors))
        ui.label("Filtrar").classes("cc-ocpp-section-title")
        with ui.card().classes("chargecore-card w-full p-4"):
            with ui.element("div").classes("cc-ocpp-filter-grid"):
                search = ui.input(
                    "Buscar",
                    value=saved.get("search", ""),
                    placeholder="ID, acción o descripción...",
                ).props("outlined clearable")
                selected_charger = ui.select(
                    options,
                    value=selected_charger_value,
                    label="Cargador",
                ).props("outlined")
                direction = ui.select(
                    {
                        "": "Todas",
                        "incoming": "Entrante",
                        "outgoing": "Saliente",
                    },
                    value=saved.get("direction", ""),
                    label="Dirección",
                ).props("outlined")
                action = ui.select(
                    action_options,
                    value=saved_action,
                    label="Acción OCPP",
                ).props("outlined")
                status_filter = ui.select(
                    {
                        "": "Todos",
                        "success": "Correcto",
                        "error": "Error",
                    },
                    value=saved.get("status", ""),
                    label="Estado",
                ).props("outlined")

        ui.label("Historial de mensajes").classes("cc-ocpp-section-title")
        with ui.card().classes("chargecore-card cc-ocpp-table-card w-full p-3"):
            table_container = ui.column().classes("cc-ocpp-table-wrap w-full")
        with ui.dialog() as details_dialog, ui.card().classes(
            "chargecore-card w-full max-w-3xl gap-4 p-6"
        ):
            details_container = ui.column().classes("w-full gap-4")
        page_state = {"number": 1}

        def filter_messages():
            query = (search.value or "").strip().casefold()
            return [
                message
                for message in messages
                if (
                    not query
                    or query
                    in " ".join(
                        str(message.get(field) or "")
                        for field in (
                            "charge_point_id",
                            "direction",
                            "action",
                            "unique_id",
                            "message_type_name",
                            "message_type",
                            "error_code",
                            "error_description",
                            "payload",
                            "raw_message",
                        )
                    ).casefold()
                )
                and (
                    not direction.value
                    or str(message.get("direction") or "").casefold()
                    == direction.value
                )
                and (
                    not action.value
                    or message.get("action") == action.value
                )
                and (
                    not status_filter.value
                    or _ocpp_message_status(message)[1] == status_filter.value
                )
            ]

        def render_table():
            filtered_messages = filter_messages()
            page_count = max(
                1,
                (len(filtered_messages) + RECORDS_PER_PAGE - 1)
                // RECORDS_PER_PAGE,
            )
            page_state["number"] = min(
                max(page_state["number"], 1),
                page_count,
            )
            start = (page_state["number"] - 1) * RECORDS_PER_PAGE
            visible_messages = filtered_messages[
                start : start + RECORDS_PER_PAGE
            ]
            end = start + len(visible_messages)
            footer = (
                f"Mostrando {start + 1}–{end} de {len(filtered_messages)} mensajes"
                if filtered_messages
                else "Mostrando 0 de 0 mensajes"
            )

            table_container.clear()
            with table_container:
                with ui.element("div").classes("cc-ocpp-table-head"):
                    for label in (
                        "Hora",
                        "Cargador",
                        "Dirección",
                        "Acción OCPP",
                        "Identificador",
                        "Tipo",
                        "Estado",
                        "Acciones",
                    ):
                        ui.label(label)
                if visible_messages:
                    for message in visible_messages:
                        with ui.element("div").classes("cc-ocpp-table-row"):
                            time_label, date_label = _ocpp_timestamp_parts(
                                message.get("timestamp")
                            )
                            with ui.column().classes(
                                "cc-ocpp-cell min-w-0 gap-0"
                            ):
                                ui.label(time_label).classes(
                                    "font-semibold text-white"
                                )
                                ui.label(date_label).classes(
                                    "text-xs text-slate-400"
                                )
                            ui.label(
                                message.get("charge_point_id") or "—"
                            ).classes("cc-ocpp-cell")
                            with ui.row().classes(
                                "cc-ocpp-cell items-center gap-1"
                            ):
                                direction_value = str(
                                    message.get("direction") or ""
                                ).casefold()
                                direction_icon = {
                                    "incoming": "call_received",
                                    "outgoing": "call_made",
                                }.get(direction_value, "help_outline")
                                direction_label = {
                                    "incoming": "Entrante",
                                    "outgoing": "Saliente",
                                }.get(direction_value, "Desconocida")
                                ui.icon(direction_icon).classes(
                                    "text-slate-400"
                                )
                                ui.label(direction_label)
                            ui.label(
                                message.get("action") or "Sin acción"
                            ).classes("cc-ocpp-cell font-semibold text-white")
                            ui.label(message.get("unique_id") or "—").classes(
                                "cc-ocpp-cell"
                            )
                            ui.label(
                                message.get("message_type_name")
                                or message.get("message_type")
                                or "—"
                            ).classes("cc-ocpp-cell")
                            status_label, status_class = _ocpp_message_status(
                                message
                            )
                            ui.label(status_label).classes(
                                f"cc-ocpp-status cc-ocpp-status-{status_class}"
                            )
                            with ui.element("div").classes("cc-ocpp-cell"):
                                with ui.button(icon="more_vert").props(
                                    "flat round dense"
                                ):
                                    with ui.menu().props(
                                        'anchor="bottom right" self="top right"'
                                    ):
                                        ui.menu_item(
                                            "Inspeccionar detalle",
                                            on_click=_ocpp_inspection_handler(
                                                message,
                                                details_dialog,
                                                details_container,
                                            ),
                                        )
                else:
                    with ui.element("div").classes("cc-ocpp-table-row"):
                        ui.label(
                            "No hay mensajes que coincidan con los filtros."
                        ).classes("cc-ocpp-empty")

                with ui.row().classes(
                    "cc-ocpp-footer w-full items-center justify-between gap-2"
                ):
                    if page_count > 1:
                        ui.button(
                            "Anterior",
                            icon="chevron_left",
                            on_click=lambda: change_page(-1),
                        ).props("flat no-caps").set_enabled(
                            page_state["number"] > 1
                        )
                    else:
                        ui.element("div")
                    ui.label(
                        f"{footer} · Página {page_state['number']} de {page_count}"
                    ).classes("text-center")
                    if page_count > 1:
                        ui.button(
                            "Siguiente",
                            icon="chevron_right",
                            on_click=lambda: change_page(1),
                        ).props("flat no-caps").set_enabled(
                            page_state["number"] < page_count
                        )
                    else:
                        ui.element("div")

        def change_page(offset):
            page_state["number"] += offset
            render_table()

        def save_filter_values():
            all_filters = app.storage.user.get("nicegui_filters", {})
            all_filters["eventos"] = {
                **saved,
                "charge_point_id": selected_charger.value or "",
                "search": search.value or "",
                "direction": direction.value or "",
                "action": action.value or "",
                "status": status_filter.value or "",
                "limit": message_limit,
            }
            app.storage.user["nicegui_filters"] = all_filters

        def apply_local_filters(_):
            save_filter_values()
            page_state["number"] = 1
            render_table()

        def apply_charger_filter(_):
            save_filter_values()
            ui.navigate.reload()

        selected_charger.on_value_change(apply_charger_filter)
        search.on_value_change(apply_local_filters)
        direction.on_value_change(apply_local_filters)
        action.on_value_change(apply_local_filters)
        status_filter.on_value_change(apply_local_filters)
        render_table()


def _render_config(token, api_base_url):
    _render_ocpp_protocol_settings(token, api_base_url)

    kpis = api_get(
        "/dashboard/kpi",
        token=token,
        base_url=api_base_url,
    )
    if isinstance(kpis, dict) and kpis.get("error"):
        _show_api_error(kpis)
    else:
        with ui.card().classes("chargecore-card w-full gap-4 p-5"):
            ui.label("Información del sistema").classes(
                "text-lg font-bold text-white"
            )
            with ui.element("div").classes(
                "grid w-full grid-cols-1 gap-3 sm:grid-cols-3"
            ):
                for label, value in (
                    ("Versión", "1.0.0"),
                    ("Estado", "API conectada" if isinstance(kpis, dict) else "Sin datos"),
                    ("Actualización de interfaz", datetime.now().strftime("%Y-%m-%d")),
                ):
                    with ui.card().classes("chargecore-card gap-2 p-4"):
                        ui.label(label).classes("text-sm text-slate-400")
                        ui.label(value).classes("text-lg font-semibold text-white")
                if isinstance(kpis, dict):
                    for key in (
                        "total_usuarios",
                        "total_vehiculos",
                        "cargadores_activos",
                        "sesiones_activas",
                    ):
                        with ui.card().classes("chargecore-card gap-2 p-4"):
                            ui.label(key.replace("_", " ").capitalize()).classes(
                                "text-sm text-slate-400"
                            )
                            ui.label(str(kpis.get(key, 0))).classes(
                                "text-lg font-semibold text-white"
                            )

    with ui.card().classes("chargecore-card w-full gap-4 p-5"):
        ui.label("Parámetros de despliegue").classes(
            "text-lg font-bold text-white"
        )
        ui.label(
            "La URL de la API, la base de datos y las claves de seguridad "
            "se administran mediante variables de entorno del servidor."
        ).classes("text-slate-300")
        ui.label(
            "El catálogo de recompensas y las tarjetas NFC se gestionan desde "
            "sus páginas correspondientes. No se muestran controles de "
            "mantenimiento que no estén disponibles en la API."
        ).classes("text-slate-400")


def _render_ocpp_protocol_settings(token, api_base_url):
    result = api_get(
        "/ocpp-settings/protocols",
        token=token,
        base_url=api_base_url,
    )
    with ui.card().classes("chargecore-card w-full gap-4 p-5"):
        ui.label("Protocolos OCPP").classes("text-lg font-bold text-white")
        if isinstance(result, dict) and result.get("error"):
            _show_api_error(result, "No se pudo cargar la configuración OCPP.")
            return
        if not isinstance(result, dict):
            show_error("La configuración OCPP tiene un formato inesperado.")
            return

        enabled_protocols = result.get("enabled_protocols")
        if not isinstance(enabled_protocols, list):
            show_error("La lista de protocolos OCPP está incompleta.")
            return
        connections = result.get("connections_by_version")
        if not isinstance(connections, dict):
            show_error("No se pudo cargar el estado de conexiones OCPP.")
            return

        detection = ui.checkbox(
            "Detectar automáticamente entre las versiones anunciadas",
            value=bool(result.get("automatic_detection")),
        )
        protocol_options = OCPP_PROTOCOL_OPTIONS
        protocol_controls = {
            protocol: ui.checkbox(
                label,
                value=protocol in enabled_protocols,
            )
            for protocol, label in protocol_options.items()
        }
        preferred = ui.select(
            protocol_options,
            value=result.get("preferred_protocol") or "ocpp1.6",
            label="Versión preferida si la detección automática está desactivada",
        ).props("outlined").classes("w-full max-w-xl")
        preferred.set_enabled(not detection.value)
        detection.on_value_change(
            lambda event: preferred.set_enabled(not event.value)
        )

        ui.label("Conexiones actuales por versión").classes(
            "text-sm font-semibold text-slate-200"
        )
        with ui.row().classes("w-full flex-wrap gap-3"):
            for protocol, label in protocol_options.items():
                ui.label(
                    f"{label}: {connections.get(protocol, 0)} conectados"
                ).classes("text-sm text-slate-300")
        ui.label(
            "Los cambios se aplican a conexiones nuevas y reconexiones; "
            "no interrumpen sesiones ya conectadas."
        ).classes("text-xs text-slate-400")

        def save():
            active_protocols = [
                protocol
                for protocol, control in protocol_controls.items()
                if control.value
            ]
            selected_preference = preferred.value
            if not active_protocols:
                ui.notify(
                    "Debes habilitar al menos una versión OCPP.",
                    type="warning",
                )
                return
            if selected_preference not in active_protocols:
                if not detection.value:
                    ui.notify(
                        "La versión preferida debe estar habilitada.",
                        type="warning",
                    )
                    return
                selected_preference = active_protocols[0]
            payload = {
                "automatic_detection": detection.value,
                "enabled_protocols": active_protocols,
                "preferred_protocol": selected_preference,
                "revision": result.get("revision", 0),
            }
            response = api_put(
                "/ocpp-settings/protocols",
                json=payload,
                token=token,
                base_url=api_base_url,
            )
            if (
                isinstance(response, dict)
                and response.get("_status_code") == 409
            ):
                ui.notify(
                    "La configuración cambió en otra sesión. "
                    "Se volverá a cargar la versión actual.",
                    type="warning",
                )
                ui.navigate.reload()
                return
            _notify_result(response, "Configuración de protocolos OCPP guardada.")

        ui.button(
            "Guardar configuración OCPP",
            icon="save",
            on_click=save,
        ).props("no-caps").classes("chargecore-primary bg-[#ff500a] text-white")


def _render_company_management(token, api_base_url):
    companies = _load_all_companies(token, api_base_url)
    if isinstance(companies, dict) and companies.get("error"):
        _show_api_error(companies, "No se pudieron cargar las empresas.")
        return

    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-lg gap-4 p-6"
    ):
        ui.label("Registrar empresa").classes("text-xl font-bold text-white")
        name = ui.input("Nombre de la empresa").props("outlined").classes("w-full")
        contact_name = ui.input("Persona de contacto").props("outlined").classes(
            "w-full"
        )
        contact_email = ui.input("Correo de contacto").props(
            "outlined type=email"
        ).classes("w-full")
        contact_phone = ui.input("Teléfono de contacto").props("outlined").classes(
            "w-full"
        )

        def create_company():
            company_name = (name.value or "").strip()
            if not company_name:
                ui.notify("Indica el nombre de la empresa.", type="warning")
                return
            result = api_post(
                "/empresas/",
                json={
                    "nombre": company_name,
                    "contacto_nombre": (contact_name.value or "").strip() or None,
                    "contacto_email": (contact_email.value or "").strip() or None,
                    "contacto_telefono": (contact_phone.value or "").strip() or None,
                },
                token=token,
                base_url=api_base_url,
            )
            if isinstance(result, dict) and result.get("error"):
                ui.notify(result["error"], type="negative")
                return
            ui.notify("Empresa registrada correctamente.", type="positive")
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            ui.button("Guardar", icon="save", on_click=create_company).props(
                "no-caps"
            ).classes("chargecore-primary bg-[#ff500a] text-white")

    ui.button(
        "Registrar empresa",
        icon="add",
        on_click=dialog.open,
    ).props("no-caps").classes("chargecore-primary bg-[#ff500a] text-white")

    with ui.card().classes("chargecore-card w-full p-4"):
        search = ui.input(
            "Buscar empresa",
            placeholder="Nombre, persona o correo de contacto",
        ).props("outlined dense clearable").classes("w-full")
    company_container = ui.column().classes("w-full gap-3")
    overview_cache = {}

    def render_company_activity(company_id, company_name, overview):
        metrics = overview["metricas"]
        rows_by_page = {0: overview.get("actividad") or []}
        state = {"expanded": False, "page": 0}
        activity_container = ui.column().classes("w-full gap-0")

        def load_page(page):
            if page in rows_by_page:
                return rows_by_page[page]
            result = api_get(
                f"/empresas/{company_id}/overview",
                params={
                    "skip": page * DASHBOARD_EXPANDED_ROWS,
                    "limit": DASHBOARD_EXPANDED_ROWS,
                },
                token=token,
                base_url=api_base_url,
            )
            if isinstance(result, dict) and result.get("error"):
                _show_api_error(
                    result,
                    f"No se pudo cargar la actividad de {company_name}.",
                )
                return None
            activity = result.get("actividad") if isinstance(result, dict) else None
            if not isinstance(activity, list):
                show_error("La respuesta de actividad de la empresa no es válida.")
                return None
            rows_by_page[page] = activity
            return activity

        def render_activity():
            total = metrics.get("sesiones", 0)
            page = state["page"]
            rows = load_page(page) if state["expanded"] else rows_by_page[0]
            if rows is None:
                return
            page_size = (
                DASHBOARD_EXPANDED_ROWS
                if state["expanded"]
                else DASHBOARD_COLLAPSED_ROWS
            )
            start, page_end, total_pages, page = _dashboard_page_bounds(
                total,
                page_size,
                page if state["expanded"] else 0,
            )
            state["page"] = page
            visible_rows = rows[: page_end - start]
            end = start + len(visible_rows)

            activity_container.clear()
            with activity_container:
                _display_table(
                    visible_rows,
                    fields=(
                        "transaction_id",
                        "charge_point_id",
                        "cliente",
                        "estado",
                        "fecha_inicio",
                        "energia_consumida_kwh",
                    ),
                    labels={
                        "transaction_id": "Transacción",
                        "charge_point_id": "Cargador",
                        "cliente": "Cliente",
                        "estado": "Estado",
                        "fecha_inicio": "Inicio",
                        "energia_consumida_kwh": "Energía (kWh)",
                    },
                    responsive=True,
                )
                if total > DASHBOARD_COLLAPSED_ROWS:
                    with ui.row().classes(
                        "cc-dashboard-list-footer w-full items-center justify-between"
                    ):
                        if state["expanded"] and page > 0:
                            ui.button(
                                "← Anterior",
                                on_click=lambda: change_page(-1),
                            ).props("outline no-caps dense").classes(
                                "cc-dashboard-page-button"
                            )
                        if state["expanded"]:
                            ui.label(
                                f"Mostrando {start + 1}–{end} de {total} "
                                f"· Página {page + 1} de {total_pages}"
                            ).classes("text-sm text-slate-400")
                            if end < total:
                                ui.button(
                                    "Siguiente →",
                                    on_click=lambda: change_page(1),
                                ).props("outline no-caps dense").classes(
                                    "cc-dashboard-page-button"
                                )
                            ui.button(
                                "Ver menos ↑",
                                on_click=collapse,
                            ).props("outline no-caps dense").classes(
                                "cc-dashboard-page-button"
                            )
                        else:
                            ui.label(
                                f"Mostrando {len(visible_rows)} de {total}"
                            ).classes("text-sm text-slate-400")
                            ui.button(
                                "Ver más ↓",
                                on_click=expand,
                            ).props("outline no-caps dense").classes(
                                "cc-dashboard-page-button"
                            )

        def expand():
            state["expanded"] = True
            state["page"] = 0
            render_activity()

        def collapse():
            state["expanded"] = False
            state["page"] = 0
            render_activity()

        def change_page(offset):
            next_page = state["page"] + offset
            if next_page < 0:
                return
            if load_page(next_page) is None:
                return
            state["page"] = next_page
            render_activity()

        ui.label("Actividad reciente · sesiones de carga").classes(
            "mt-2 text-sm font-semibold text-white"
        )
        render_activity()

    def render_company(company):
        company_id = company.get("_id")
        if not company_id:
            return
        with ui.card().classes("chargecore-card w-full gap-3 p-5"):
            with ui.row().classes("w-full flex-wrap items-center gap-3"):
                name_input = ui.input(
                    "Nombre",
                    value=company.get("nombre") or "",
                ).props("outlined").classes("min-w-60 flex-1")
                contact_name = ui.input(
                    "Persona de contacto",
                    value=company.get("contacto_nombre") or "",
                ).props("outlined").classes("min-w-52 flex-1")
                contact_email = ui.input(
                    "Correo de contacto",
                    value=company.get("contacto_email") or "",
                ).props("outlined type=email").classes("min-w-52 flex-1")
                contact_phone = ui.input(
                    "Teléfono de contacto",
                    value=company.get("contacto_telefono") or "",
                ).props("outlined").classes("min-w-40")
                current_state = company.get("estado") or (
                    "activa" if company.get("activo", True) else "inactiva"
                )
                state = ui.select(
                    {
                        "activa": "Activa",
                        "suspendida": "Suspendida",
                        "inactiva": "Inactiva",
                    },
                    value=current_state,
                    label="Estado",
                ).props("outlined").classes("min-w-36")
                controls = (
                    name_input,
                    contact_name,
                    contact_email,
                    contact_phone,
                    state,
                )
                for control in controls:
                    control.set_enabled(False)

                edit_button = ui.button(
                    "Editar",
                    icon="edit",
                ).props("no-caps").classes(
                    "chargecore-primary bg-[#ff500a] text-white"
                )
                save_button = ui.button(
                    "Guardar",
                    icon="save",
                ).props("no-caps").classes(
                    "chargecore-primary bg-[#ff500a] text-white"
                )
                save_button.set_visibility(False)

                def start_editing():
                    for control in controls:
                        control.set_enabled(True)
                    edit_button.set_visibility(False)
                    save_button.set_visibility(True)

                def save_company():
                    updated_name = (name_input.value or "").strip()
                    if not updated_name:
                        ui.notify("Indica el nombre de la empresa.", type="warning")
                        return
                    result = api_put(
                        f"/empresas/{company_id}",
                        json={
                            "nombre": updated_name,
                            "contacto_nombre": (
                                contact_name.value or ""
                            ).strip() or None,
                            "contacto_email": (
                                contact_email.value or ""
                            ).strip() or None,
                            "contacto_telefono": (
                                contact_phone.value or ""
                            ).strip() or None,
                            "estado": state.value,
                        },
                        token=token,
                        base_url=api_base_url,
                    )
                    if _notify_result(result, "Empresa actualizada."):
                        for control in controls:
                            control.set_enabled(False)
                        save_button.set_visibility(False)
                        edit_button.set_visibility(True)

                edit_button.on("click", start_editing)
                save_button.on("click", save_company)

            ui.label(f"ID: {company_id}").classes("text-xs text-slate-500")
            if company_id not in overview_cache:
                overview_cache[company_id] = api_get(
                    f"/empresas/{company_id}/overview",
                    params={"limit": DASHBOARD_EXPANDED_ROWS},
                    token=token,
                    base_url=api_base_url,
                )
            overview = overview_cache[company_id]
            if isinstance(overview, dict) and overview.get("error"):
                _show_api_error(
                    overview,
                    f"No se pudo cargar el resumen de {company.get('nombre')}.",
                )
                return
            metrics = overview.get("metricas") if isinstance(overview, dict) else None
            if not isinstance(metrics, dict):
                ui.label("No se pudo cargar el resumen de la empresa.").classes(
                    "text-sm text-amber-300"
                )
                return
            with ui.row().classes("w-full flex-wrap gap-3"):
                for label, key in (
                    ("Cargadores", "cargadores"),
                    ("Usuarios", "usuarios"),
                    ("Clientes", "clientes"),
                    ("Dueños", "duenos"),
                    ("Sesiones", "sesiones"),
                    ("Activas", "sesiones_activas"),
                    ("Finalizadas", "sesiones_finalizadas"),
                ):
                    with ui.card().classes("chargecore-card min-w-24 flex-1 p-3"):
                        ui.label(label).classes("text-xs text-slate-400")
                        ui.label(str(metrics.get(key, 0))).classes(
                            "text-lg font-semibold text-white"
                        )
            render_company_activity(
                company_id,
                company.get("nombre") or "empresa",
                overview,
            )

    def render_companies():
        filtered_companies = _filter_companies(companies, search.value)
        company_container.clear()
        with company_container:
            if not filtered_companies:
                ui.label(
                    "No hay empresas que coincidan con la búsqueda."
                    if search.value
                    else "No hay empresas registradas."
                ).classes("text-slate-400")
                return
            for company in filtered_companies:
                render_company(company)

    search.on_value_change(render_companies)
    render_companies()


def _render_company_profile(token, api_base_url):
    company = api_get("/empresas/me", token=token, base_url=api_base_url)
    if isinstance(company, dict) and company.get("error"):
        _show_api_error(company, "No fue posible cargar la empresa.")
        return
    if not isinstance(company, dict):
        show_error("La respuesta de empresa no tiene el formato esperado.")
        return
    with ui.card().classes("chargecore-card w-full max-w-3xl gap-4 p-5 sm:p-7"):
        ui.label(company.get("nombre") or "Empresa").classes(
            "text-xl font-bold text-white"
        )
        ui.label(f"Estado: {'Activa' if company.get('activo', True) else 'Inactiva'}")
        ui.label(f"Identificador: {company.get('_id') or '—'}").classes(
            "text-sm text-slate-400"
        )


def _filter_qr_operations(operations, query):
    normalized_query = (query or "").strip().casefold()
    if not normalized_query:
        return operations
    return [
        operation
        for operation in operations
        if normalized_query
        in " ".join(str(value or "") for value in operation.values()).casefold()
    ]


def _qr_operation_display_rows(operations):
    status_labels = {
        "pending": "Pendiente",
        "processing": "Procesándose",
        "started": "Iniciada",
        "rejected": "Rechazada",
        "finished": "Finalizada",
        "error": "Error",
        "expired": "Expirada",
    }
    rows = []
    for index, operation in enumerate(operations):
        row = dict(operation)
        row["fila"] = str(operation.get("id") or index)
        row["estado_visible"] = status_labels.get(
            operation.get("estado_operacion"),
            str(operation.get("estado_operacion") or "Desconocido"),
        )
        for field in ("created_at", "started_at", "finished_at"):
            row[field] = (
                _format_dashboard_timestamp(operation.get(field))
                if operation.get(field) is not None
                else "—"
            )
        rows.append(row)
    return rows


def _render_qr_operations(token, api_base_url):
    ui.add_head_html(
        """
        <style>
            .cc-qr-operations-table .q-table__middle {
                scrollbar-width: none;
                -ms-overflow-style: none;
            }
            .cc-qr-operations-table .q-table__middle::-webkit-scrollbar {
                display: none;
            }
        </style>
        """
    )
    result = api_get(
        "/empresas/qr-operations",
        params={"limit": 500},
        token=token,
        base_url=api_base_url,
    )
    if isinstance(result, dict) and result.get("error"):
        _show_api_error(result, "No fue posible cargar las operaciones QR.")
        return
    if not isinstance(result, list):
        show_error("La respuesta de operaciones QR no tiene el formato esperado.")
        return
    ui.label(
        "Consulta solicitudes QR, su seguimiento y las sesiones relacionadas."
    ).classes("mb-3 text-sm text-slate-400")
    operations = _qr_operation_display_rows(result)
    with ui.card().classes("cc-report-search-card w-full"):
        search = ui.input(
            "Buscar operación QR",
            placeholder="Empresa, cliente, vehículo, cargador, estado o transacción",
        ).props("outlined dense clearable").classes("w-full")
    table_container = ui.column().classes(
        "w-full cc-qr-operations-table"
    )
    fields = (
        "empresa",
        "cliente",
        "vehiculo",
        "cargador",
        "charge_point_id",
        "estado_visible",
        "created_at",
        "started_at",
        "finished_at",
        "transaction_id",
        "seguimiento",
    )
    labels = {
        "empresa": "Empresa",
        "cliente": "Cliente",
        "vehiculo": "Vehículo",
        "cargador": "Cargador",
        "charge_point_id": "ID OCPP",
        "estado_visible": "Estado",
        "created_at": "Solicitada",
        "started_at": "Inicio",
        "finished_at": "Fin",
        "transaction_id": "Transacción",
        "seguimiento": "Seguimiento",
    }

    def render_filtered(_=None):
        filtered = _filter_qr_operations(operations, search.value)
        table_container.clear()
        with table_container:
            _render_expandable_dashboard_rows(
                filtered,
                lambda rows: _display_table(
                    rows,
                    row_key="fila",
                    fields=fields,
                    labels=labels,
                    responsive=True,
                ),
            )

    search.on_value_change(render_filtered)
    render_filtered()


def _render_create_button(page_key, token, api_base_url):
    role = (app.storage.user.get("user") or {}).get("rol")
    if role not in {"superadmin", "dueno_empresa"}:
        return
    if page_key not in {"usuarios", "vehiculos", "tarjetas", "cargadores", "recompensas"}:
        return
    if role == "dueno_empresa" and page_key == "recompensas":
        return
    title = {
        "usuarios": "Registrar usuario",
        "vehiculos": "Registrar vehículo",
        "tarjetas": "Registrar tarjeta NFC",
        "cargadores": "Registrar cargador",
        "recompensas": "Registrar recompensa",
    }[page_key]
    company_options = None
    if role == "superadmin" and page_key in {"usuarios", "cargadores"}:
        companies = _load_all_companies(token, api_base_url)
        if isinstance(companies, dict) and companies.get("error"):
            _show_api_error(companies, "No se pudieron cargar las empresas.")
            return
        company_options = {
            company["_id"]: company["nombre"]
            for company in companies
            if (
                isinstance(company, dict)
                and company.get("_id")
                and company.get("activo", True)
            )
        }
        if page_key == "cargadores" and not company_options:
            ui.label("Registra primero una empresa activa para asignar el cargador.").classes(
                "text-sm text-amber-300"
            )
            return
    client_options = None
    if page_key in {"vehiculos", "tarjetas"}:
        users = _load_all_users(token, api_base_url)
        if isinstance(users, dict) and users.get("error"):
            _show_api_error(
                users,
                "No se pudieron cargar los clientes.",
            )
            return
        client_options = {
            user["_id"]: (
                f"{user.get('nombre', 'Cliente')} · {user.get('email', '')}"
            )
            for user in (_rows_from_response(users) or [])
            if isinstance(user, dict)
            and user.get("_id")
            and user.get("rol") == "cliente"
            and user.get("activo", True)
        }
        if not client_options:
            ui.label(
                "Registra primero un cliente activo para asignarle "
                "vehículos o tarjetas NFC."
            ).classes("text-sm text-amber-300")
            return
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-xl gap-4 p-6"
    ):
        ui.label(title).classes("text-xl font-bold text-white")
        payload_fields = {}
        connector_rows = None
        if page_key == "usuarios":
            payload_fields["nombre"] = ui.input("Nombre completo").props("outlined").classes("w-full")
            payload_fields["email"] = ui.input("Correo electrónico").props("outlined type=email").classes("w-full")
            payload_fields["password"] = ui.input("Contraseña inicial", password=True).props("outlined").classes("w-full")
            payload_fields["telefono"] = ui.input("Teléfono").props("outlined").classes("w-full")
            payload_fields["rol"] = ui.select(
                (
                    {
                        "superadmin": "Superadministrador",
                        "dueno_empresa": "Dueño de empresa",
                        "cliente": "Cliente",
                    }
                    if role == "superadmin"
                    else {"cliente": "Cliente"}
                ),
                value="cliente",
                label="Rol",
            ).props("outlined").classes("w-full")
            if role == "superadmin":
                payload_fields["empresa_id"] = ui.select(
                    company_options,
                    label="Empresa (requerida para dueño y cliente)",
                    clearable=True,
                ).props("outlined").classes("w-full")
            payload_fields["activo"] = ui.checkbox(
                "Activar usuario",
                value=True,
            )
        elif page_key == "vehiculos":
            payload_fields["usuario_id"] = ui.select(
                client_options,
                label="Cliente",
                with_input=True,
            ).props("outlined").classes("w-full")
            payload_fields["marca"] = ui.input("Marca").props("outlined").classes("w-full")
            payload_fields["modelo"] = ui.input("Modelo").props("outlined").classes("w-full")
            payload_fields["anio"] = ui.number("Año", value=2024, min=2000, max=2030, step=1).props("outlined").classes("w-full")
            payload_fields["placa"] = ui.input("Placa").props("outlined").classes("w-full")
            payload_fields["tipo_conector"] = ui.select(
                ["Type1", "Type2", "CCS", "CHAdeMO", "Tesla"],
                value="Type2",
                label="Tipo de conector",
            ).props("outlined").classes("w-full")
        elif page_key == "tarjetas":
            payload_fields["uid"] = ui.input("UID de la tarjeta").props("outlined").classes("w-full")
            payload_fields["usuario_id"] = ui.select(
                client_options,
                label="Cliente",
                with_input=True,
            ).props("outlined").classes("w-full")
        elif page_key == "cargadores":
            if role == "superadmin":
                payload_fields["empresa_id"] = ui.select(
                    company_options,
                    label="Empresa",
                ).props("outlined").classes("w-full")
            payload_fields["charge_point_id"] = ui.input("Identificador OCPP").props("outlined").classes("w-full")
            payload_fields["nombre"] = ui.input("Nombre").props("outlined").classes("w-full")
            payload_fields["ubicacion"] = ui.input("Ubicación").props("outlined").classes("w-full")
            payload_fields["fabricante"] = ui.input("Fabricante (opcional)").props("outlined").classes("w-full")
            payload_fields["modelo"] = ui.input("Modelo (opcional)").props("outlined").classes("w-full")
            payload_fields["ocpp_version"] = ui.select(
                OCPP_PROTOCOL_OPTIONS,
                value="ocpp1.6",
                label="Versión OCPP que utiliza el cargador",
            ).props("outlined").classes("w-full")
            payload_fields["ocpp_password"] = ui.input("Contraseña OCPP (mínimo 12 caracteres)", password=True).props("outlined").classes("w-full")
        else:
            payload_fields["nombre"] = ui.input("Nombre de la recompensa").props("outlined").classes("w-full")
            payload_fields["empresa"] = ui.input("Empresa").props("outlined").classes("w-full")
            payload_fields["descripcion"] = ui.input("Descripción").props("outlined").classes("w-full")
            payload_fields["condiciones"] = ui.input("Condiciones").props("outlined").classes("w-full")
            payload_fields["fecha_inicio"] = ui.input(
                "Fecha de inicio",
                value=date.today().isoformat(),
            ).props("outlined type=date").classes("w-full")
            payload_fields["fecha_finalizacion"] = ui.input(
                "Fecha de finalización",
                value=date.today().isoformat(),
            ).props("outlined type=date").classes("w-full")

        if page_key == "cargadores":
            connector_rows = _build_connector_editor()

        def submit():
            payload = {
                name: (field.value or "").strip()
                if isinstance(field.value, str)
                else field.value
                for name, field in payload_fields.items()
            }
            optional_fields = {
                "telefono",
                "placa",
                "condiciones",
                "fabricante",
                "modelo",
            }
            for name in optional_fields:
                if name in payload and not payload[name]:
                    payload.pop(name)
            if page_key == "usuarios" and role == "superadmin":
                if payload.get("rol") == "superadmin":
                    payload.pop("empresa_id", None)
                elif not payload.get("empresa_id"):
                    ui.notify(
                        "Selecciona la empresa para este usuario.",
                        type="warning",
                    )
                    return
            if any(value in (None, "") for value in payload.values()):
                ui.notify("Completa todos los campos obligatorios.", type="warning")
                return
            if page_key == "usuarios" and len(payload["password"]) < 6:
                ui.notify(
                    "La contraseña inicial debe tener al menos 6 caracteres.",
                    type="warning",
                )
                return
            if page_key == "cargadores" and len(payload["ocpp_password"]) < 12:
                ui.notify(
                    "La contraseña OCPP debe tener al menos 12 caracteres.",
                    type="warning",
                )
                return
            if (
                page_key == "recompensas"
                and payload["fecha_inicio"] > payload["fecha_finalizacion"]
            ):
                ui.notify(
                    "La fecha de inicio no puede ser posterior a la fecha final.",
                    type="warning",
                )
                return
            if page_key == "cargadores":
                try:
                    payload["conectores"] = _serialize_connectors(connector_rows)
                except (TypeError, ValueError) as error:
                    ui.notify(str(error), type="warning")
                    return
            endpoint = {
                "usuarios": "/usuarios/",
                "vehiculos": "/vehiculos/",
                "tarjetas": "/nfc-cards/",
                "cargadores": "/cargadores/",
                "recompensas": "/recompensas/catalogo",
            }[page_key]
            if page_key == "recompensas":
                for field in ("fecha_inicio", "fecha_finalizacion"):
                    payload[field] = f"{payload[field]}T00:00:00"
            result = api_post(
                endpoint,
                json=payload,
                token=token,
                base_url=api_base_url,
            )
            if isinstance(result, dict) and result.get("error"):
                ui.notify(result["error"], type="negative")
                return
            dialog.close()
            if page_key == "cargadores":
                ocpp_password = payload.pop("ocpp_password")
                charger_record = {
                    **payload,
                    "_id": result.get("id"),
                    "ocpp_credentials_configured": True,
                }
                ui.notify(
                    "Cargador registrado. Configura ahora su conexión.",
                    type="positive",
                )
                _show_charger_connection(
                    charger_record,
                    api_base_url,
                    ocpp_password=ocpp_password,
                    reload_on_close=True,
                )
                return
            ui.notify("Registro creado correctamente.", type="positive")
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            ui.button("Guardar", icon="save", on_click=submit).props(
                "no-caps"
            ).classes("chargecore-primary bg-[#ff500a] text-white")

    ui.button(
        title,
        icon="add",
        on_click=dialog.open,
    ).props("no-caps").classes("chargecore-primary bg-[#ff500a] text-white")


def _render_reward_rule_button(token, api_base_url):
    catalog = api_get(
        "/recompensas/catalogo",
        token=token,
        base_url=api_base_url,
    )
    if isinstance(catalog, dict) and catalog.get("error"):
        ui.notify(
            f"No se pudo cargar el catálogo: {catalog['error']}",
            type="negative",
        )
        return
    catalog_rows = _rows_from_response(catalog) or []
    options = {
        row.get("_id"): f"{row.get('nombre', 'Recompensa')} · {row.get('empresa', '')}"
        for row in catalog_rows
        if row.get("_id") and row.get("activa")
    }
    if not options:
        ui.notify("Registra una recompensa activa antes de crear una regla.", type="warning")
        return

    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-xl gap-4 p-6"
    ):
        ui.label("Crear regla de recompensa").classes(
            "text-xl font-bold text-white"
        )
        required_loads = ui.number(
            "Cargas requeridas",
            value=5,
            min=1,
            step=1,
        ).props("outlined").classes("w-full")
        reward = ui.select(
            options,
            label="Recompensa del catálogo",
        ).props("outlined").classes("w-full")

        def create_rule():
            try:
                count = int(required_loads.value)
            except (TypeError, ValueError):
                ui.notify("Ingresa una cantidad válida de cargas.", type="warning")
                return
            if count < 1 or not reward.value:
                ui.notify("Completa todos los campos.", type="warning")
                return
            result = api_post(
                "/recompensas/reglas",
                json={
                    "cargas_requeridas": count,
                    "recompensa_catalogo_id": reward.value,
                    "activa": True,
                },
                token=token,
                base_url=api_base_url,
            )
            if _notify_result(result, "Regla de recompensa creada."):
                dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            ui.button("Crear regla", icon="add", on_click=create_rule).props(
                "no-caps"
            ).classes("chargecore-primary bg-[#ff500a] text-white")
    dialog.open()


def _edit_reward_rule(rule, token, api_base_url):
    rule_id = rule.get("_id") or rule.get("id")
    if not rule_id:
        ui.notify("La regla no contiene identificador.", type="negative")
        return
    catalog = api_get(
        "/recompensas/catalogo",
        token=token,
        base_url=api_base_url,
    )
    if isinstance(catalog, dict) and catalog.get("error"):
        ui.notify(
            f"No se pudo cargar el catálogo: {catalog['error']}",
            type="negative",
        )
        return
    current_catalog_id = rule.get("recompensa_catalogo_id")
    options = {
        item["_id"]: f"{item.get('nombre', 'Recompensa')} · {item.get('empresa', '')}"
        for item in (_rows_from_response(catalog) or [])
        if item.get("_id")
        and (item.get("activa") or item["_id"] == current_catalog_id)
    }
    if not options:
        ui.notify("No hay recompensas disponibles para esta regla.", type="warning")
        return
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-xl gap-4 p-6"
    ):
        ui.label("Editar regla de recompensa").classes(
            "text-xl font-bold text-white"
        )
        required_loads = ui.number(
            "Cargas requeridas",
            value=rule.get("cargas_requeridas", 1),
            min=1,
            step=1,
        ).props("outlined").classes("w-full")
        reward = ui.select(
            options,
            value=current_catalog_id,
            label="Recompensa del catálogo",
        ).props("outlined").classes("w-full")
        active = ui.checkbox(
            "Regla activa",
            value=bool(rule.get("activa", True)),
        )

        def save():
            try:
                count = int(required_loads.value)
            except (TypeError, ValueError):
                ui.notify("Ingresa una cantidad válida de cargas.", type="warning")
                return
            if count < 1 or not reward.value:
                ui.notify("Completa todos los campos.", type="warning")
                return
            result = api_put(
                f"/recompensas/reglas/{rule_id}",
                json={
                    "cargas_requeridas": count,
                    "recompensa_catalogo_id": reward.value,
                    "activa": bool(active.value),
                },
                token=token,
                base_url=api_base_url,
            )
            if _notify_result(result, "Regla de recompensa actualizada."):
                dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            ui.button("Guardar cambios", icon="save", on_click=save).props(
                "no-caps"
            ).classes("chargecore-primary bg-[#ff500a] text-white")
    dialog.open()


def _render_reward_rules(rows, token, api_base_url, can_manage, issued_rows=None):
    issued_rows = issued_rows or []
    issued_by_rule = {}
    for issued in issued_rows:
        rule_id = issued.get("recompensa_regla_id")
        if rule_id:
            rule_key = str(rule_id)
            issued_by_rule[rule_key] = issued_by_rule.get(rule_key, 0) + 1

    with ui.card().classes("chargecore-card w-full overflow-hidden p-0"):
        with ui.element("div").classes("cc-reward-rule-table"):
            with ui.element("div").classes(
                "cc-reward-rule-row cc-reward-rule-heading"
            ):
                for label in (
                    "Cargas requeridas",
                    "Beneficio",
                    "Empresa",
                    "Otorgadas",
                    "Estado",
                    "Acciones",
                ):
                    ui.label(label)
            if not rows:
                with ui.element("div").classes("cc-reward-rule-empty"):
                    ui.label("No hay reglas registradas.")
            for rule in rows:
                reward = rule.get("recompensa")
                reward_name = (
                    reward.get("nombre", "Recompensa")
                    if isinstance(reward, dict)
                    else "Recompensa"
                )
                company = (
                    reward.get("empresa", "—")
                    if isinstance(reward, dict)
                    else "—"
                )
                active = bool(rule.get("activa", True))
                rule_id = rule.get("_id") or rule.get("id")
                with ui.element("div").classes("cc-reward-rule-row"):
                    ui.label(
                        f"{rule.get('cargas_requeridas', '—')} cargas"
                    ).classes("font-semibold")
                    ui.label(reward_name).classes("cc-reward-cell-strong")
                    ui.label(company)
                    ui.label(str(issued_by_rule.get(str(rule_id), 0)))
                    ui.badge("Activa" if active else "Inactiva").classes(
                        "bg-green-950 text-green-300"
                        if active
                        else "bg-slate-700 text-slate-300"
                    )
                    if can_manage and rule_id:
                        with ui.button(icon="more_vert").props(
                            "flat round dense"
                        ):
                            with ui.menu().props(
                                'anchor="bottom right" self="top right"'
                            ):
                                ui.menu_item(
                                    "Editar regla",
                                    on_click=lambda item=rule: _edit_reward_rule(
                                        item, token, api_base_url
                                    ),
                                )

                                def toggle_rule(item=rule):
                                    selected_id = item.get("_id") or item.get("id")
                                    result = api_put(
                                        f"/recompensas/reglas/{selected_id}/estado",
                                        json={
                                            "activa": not bool(
                                                item.get("activa", True)
                                            )
                                        },
                                        token=token,
                                        base_url=api_base_url,
                                    )
                                    _notify_result(
                                        result,
                                        "Estado de regla actualizado.",
                                    )

                                ui.menu_item(
                                    "Desactivar" if active else "Activar",
                                    on_click=toggle_rule,
                                )
                    else:
                        ui.label("—")


def _reward_date_value(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _reward_summary_cards(catalog_rows, rule_rows, issued_rows, now=None):
    now = now or datetime.utcnow()
    active_catalog = sum(bool(row.get("activa", True)) for row in catalog_rows)
    active_rules = sum(bool(row.get("activa", True)) for row in rule_rows)
    this_month = sum(
        (issued_date := _reward_date_value(row.get("fecha_otorgamiento")))
        is not None
        and (issued_date.year, issued_date.month) == (now.year, now.month)
        for row in issued_rows
    )
    issued_count = len(issued_rows)
    used_count = sum(bool(row.get("usado")) for row in issued_rows)
    redemption_rate = round(used_count * 100 / issued_count) if issued_count else 0
    return [
        (
            "Recompensas activas",
            active_catalog,
            "Beneficios disponibles",
            "card_giftcard",
            "#8b5cf6",
        ),
        (
            "Reglas activas",
            active_rules,
            "Hitos configurados",
            "sell",
            "#3b82f6",
        ),
        (
            "Otorgadas este mes",
            this_month,
            "Beneficios asignados",
            "emoji_events",
            "#f59e0b",
        ),
        (
            "Tasa de canje",
            f"{redemption_rate}%",
            "Usadas sobre otorgadas",
            "currency_exchange",
            "#22c55e",
        ),
    ]


def _reward_ranking_rows(report_rows, issued_rows, rule_rows):
    rewards_by_user = {}
    for reward in issued_rows:
        username = str(reward.get("usuario") or "Sin usuario")
        rewards_by_user[username] = rewards_by_user.get(username, 0) + 1

    thresholds = sorted(
        {
            int(rule["cargas_requeridas"])
            for rule in rule_rows
            if rule.get("activa", True)
            and isinstance(rule.get("cargas_requeridas"), (int, float))
            and int(rule["cargas_requeridas"]) > 0
        }
    )
    ranking = []
    for row in report_rows:
        username = str(row.get("usuario") or "Sin usuario")
        try:
            loads = max(0, int(row.get("total_cargas") or 0))
        except (TypeError, ValueError):
            loads = 0
        next_threshold = next(
            (threshold for threshold in thresholds if threshold > loads),
            None,
        )
        if next_threshold:
            progress = min(100, round(loads * 100 / next_threshold))
            progress_caption = (
                f"{max(0, next_threshold - loads)} cargas para el siguiente hito"
            )
        elif thresholds:
            progress = 100
            progress_caption = "Todos los hitos alcanzados"
        else:
            progress = 0
            progress_caption = "Sin reglas activas"
        ranking.append(
            {
                "usuario": username,
                "cargas": loads,
                "recompensas": rewards_by_user.get(username, 0),
                "nivel": row.get("nivel") or "Estándar",
                "progreso": progress,
                "progreso_texto": progress_caption,
            }
        )
    return sorted(
        ranking,
        key=lambda item: (-item["cargas"], item["usuario"].casefold()),
    )


def _render_reward_create_button(token, api_base_url):
    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card cc-reward-dialog w-full max-w-2xl gap-5 p-6"
    ):
        ui.label("Nueva recompensa").classes("text-xl font-bold text-white")
        name = ui.input("Nombre del beneficio").props("outlined").classes("w-full")
        company = ui.input("Empresa").props("outlined").classes("w-full")
        with ui.row().classes("w-full gap-3"):
            start = ui.input(
                "Fecha de inicio",
                value=date.today().isoformat(),
            ).props("outlined type=date").classes("flex-1")
            end = ui.input(
                "Fecha de finalización",
                value=date.today().isoformat(),
            ).props("outlined type=date").classes("flex-1")
        description = ui.textarea("Descripción").props("outlined").classes("w-full")
        conditions = ui.textarea("Condiciones").props("outlined").classes("w-full")
        active = ui.checkbox("Recompensa activa", value=True)

        def create_reward():
            values = {
                "nombre": (name.value or "").strip(),
                "empresa": (company.value or "").strip(),
                "descripcion": (description.value or "").strip(),
                "condiciones": (conditions.value or "").strip(),
                "fecha_inicio": start.value or "",
                "fecha_finalizacion": end.value or "",
                "activa": bool(active.value),
            }
            if any(
                not values[field]
                for field in (
                    "nombre",
                    "empresa",
                    "descripcion",
                    "fecha_inicio",
                    "fecha_finalizacion",
                )
            ):
                ui.notify("Completa todos los campos obligatorios.", type="warning")
                return
            if values["fecha_inicio"] > values["fecha_finalizacion"]:
                ui.notify(
                    "La fecha de inicio no puede ser posterior a la fecha final.",
                    type="warning",
                )
                return
            values["fecha_inicio"] += "T00:00:00"
            values["fecha_finalizacion"] += "T00:00:00"
            result = api_post(
                "/recompensas/catalogo",
                json=values,
                token=token,
                base_url=api_base_url,
            )
            if _notify_result(result, "Recompensa creada."):
                dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            ui.button("Crear recompensa", icon="add", on_click=create_reward).props(
                "no-caps"
            ).classes("chargecore-primary bg-[#ff500a] text-white")
    return ui.button(
        "Nueva recompensa",
        icon="add",
        on_click=dialog.open,
    ).props("no-caps").classes("chargecore-primary bg-[#ff500a] text-white")


def _render_reward_styles():
    ui.add_head_html(
        """
        <style>
            .cc-rewards-page {
                gap: 20px !important;
            }
            .cc-rewards-page .cc-summary-card {
                min-height: 148px;
            }
            .cc-reward-tabs {
                border-bottom: 1px solid var(--cc-border);
            }
            .cc-reward-tabs .q-tab {
                min-height: 50px;
                color: var(--cc-text-muted);
                font-weight: 600;
            }
            .cc-reward-tabs .q-tab--active {
                color: var(--cc-primary);
            }
            .cc-reward-filter-row {
                align-items: center;
                flex-wrap: wrap;
                gap: 12px;
            }
            .cc-reward-filter-row .q-field {
                min-width: 190px;
            }
            .cc-reward-grid {
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 16px;
            }
            .cc-reward-card {
                min-width: 0;
                border: 1px solid var(--cc-border) !important;
                border-radius: 14px !important;
                background: var(--cc-bg-card) !important;
                box-shadow: none !important;
            }
            .cc-reward-card-title {
                overflow-wrap: anywhere;
                color: var(--cc-text-primary);
                font-size: 16px;
                font-weight: 700;
            }
            .cc-reward-table-wrap {
                overflow-x: auto;
                width: 100%;
            }
            .cc-reward-rule-table {
                min-width: 760px;
            }
            .cc-reward-rule-row {
                display: grid;
                grid-template-columns: 1fr 1.5fr 1.1fr .65fr .8fr .55fr;
                align-items: center;
                gap: 14px;
                min-height: 58px;
                padding: 10px 18px;
                border-bottom: 1px solid var(--cc-border);
                color: var(--cc-text-secondary);
                font-size: 13px;
            }
            .cc-reward-rule-heading {
                min-height: 48px;
                background: var(--cc-field);
                color: var(--cc-text-muted);
                font-size: 11px;
                font-weight: 700;
                text-transform: uppercase;
            }
            .cc-reward-rule-row:last-child {
                border-bottom: 0;
            }
            .cc-reward-cell-strong {
                color: var(--cc-text-primary);
                font-weight: 600;
            }
            .cc-reward-rule-empty {
                padding: 22px;
                color: var(--cc-text-muted);
                text-align: center;
            }
            .cc-reward-route {
                min-height: 100%;
            }
            .cc-reward-route-item {
                position: relative;
                display: grid;
                grid-template-columns: 28px minmax(0, 1fr);
                gap: 12px;
                padding-bottom: 20px;
            }
            .cc-reward-route-item:not(:last-child)::before {
                position: absolute;
                top: 24px;
                bottom: 0;
                left: 11px;
                width: 2px;
                background: var(--cc-border);
                content: "";
            }
            .cc-reward-route-dot {
                z-index: 1;
                display: grid;
                width: 24px;
                height: 24px;
                place-items: center;
                border-radius: 50%;
                background: var(--cc-primary);
                color: white;
                font-size: 12px;
                font-weight: 700;
            }
            .cc-reward-data-head,
            .cc-reward-data-row {
                display: grid;
                align-items: center;
                gap: 14px;
                min-width: 680px;
                padding: 12px 16px;
            }
            .cc-reward-data-head {
                background: var(--cc-field);
                color: var(--cc-text-muted);
                font-size: 11px;
                font-weight: 700;
                text-transform: uppercase;
            }
            .cc-reward-data-row {
                min-height: 54px;
                border-top: 1px solid var(--cc-border);
                color: var(--cc-text-secondary);
                font-size: 13px;
            }
            .cc-reward-data-empty {
                min-width: 680px;
                padding: 24px 16px;
                color: var(--cc-text-muted);
                text-align: center;
            }
            .cc-reward-medal {
                min-height: 150px;
                align-items: center;
                text-align: center;
            }
            .cc-reward-medal-icon {
                font-size: 28px;
            }
            .cc-reward-dialog {
                max-height: 90vh;
                overflow-y: auto;
            }
            @media (max-width: 1050px) {
                .cc-reward-grid {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }
            }
            @media (max-width: 700px) {
                .cc-reward-grid {
                    grid-template-columns: minmax(0, 1fr);
                }
                .cc-reward-filter-row .q-field {
                    width: 100%;
                }
            }
        </style>
        """
    )


def _render_reward_catalog(rows, token, api_base_url, can_manage):
    search = ui.input(
        "Buscar beneficios",
        placeholder="Nombre, empresa o descripción",
    ).props("outlined clearable").classes("flex-1")
    state_filter = ui.select(
        {
            "": "Todos los estados",
            "activa": "Activas",
            "inactiva": "Inactivas",
        },
        value="",
        label="Estado",
    ).props("outlined").classes("w-full max-w-xs")
    container = ui.column().classes("w-full gap-4")
    page_state = {"number": 1}

    def render_cards():
        query = (search.value or "").strip().casefold()
        selected_state = state_filter.value or ""
        filtered_rows = [
            row
            for row in rows
            if (
                not query
                or query
                in " ".join(
                    str(row.get(key) or "")
                    for key in ("nombre", "empresa", "descripcion", "condiciones")
                ).casefold()
            )
            and (
                not selected_state
                or bool(row.get("activa", True)) == (selected_state == "activa")
            )
        ]
        page_count = max(
            1,
            (len(filtered_rows) + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE,
        )
        page_state["number"] = min(max(page_state["number"], 1), page_count)
        start_index = (page_state["number"] - 1) * RECORDS_PER_PAGE
        visible_rows = filtered_rows[start_index : start_index + RECORDS_PER_PAGE]
        container.clear()
        with container:
            ui.label(
                f"{len(filtered_rows)} beneficios · página "
                f"{page_state['number']} de {page_count}"
            ).classes("text-sm text-slate-400")
            if visible_rows:
                with ui.element("div").classes("cc-reward-grid w-full"):
                    for record in visible_rows:
                        active = bool(record.get("activa", True))
                        with ui.card().classes(
                            "cc-reward-card w-full gap-4 p-5"
                        ):
                            with ui.row().classes(
                                "w-full items-start justify-between gap-3"
                            ):
                                with ui.row().classes("min-w-0 items-center gap-3"):
                                    with ui.element("div").classes(
                                        "cc-summary-icon-box"
                                    ):
                                        ui.icon("card_giftcard").classes(
                                            "text-xl text-violet-400"
                                        )
                                    ui.label(
                                        record.get("nombre") or "Beneficio"
                                    ).classes("cc-reward-card-title")
                                ui.badge(
                                    "Activa" if active else "Inactiva"
                                ).classes(
                                    "bg-green-950 text-green-300"
                                    if active
                                    else "bg-slate-700 text-slate-300"
                                )
                            ui.label(record.get("empresa") or "Sin empresa").classes(
                                "text-sm text-slate-400"
                            )
                            ui.label(record.get("descripcion") or "Sin descripción").classes(
                                "text-sm text-slate-200"
                            )
                            ui.separator()
                            with ui.column().classes("w-full gap-2"):
                                with ui.column().classes("gap-1"):
                                    ui.label("Condiciones").classes(
                                        "text-xs font-semibold uppercase text-slate-500"
                                    )
                                    ui.label(
                                        record.get("condiciones") or "Sin condiciones"
                                    ).classes("text-sm text-slate-300")
                                with ui.row().classes(
                                    "w-full justify-between gap-2 text-xs text-slate-400"
                                ):
                                    ui.label(
                                        f"Desde {str(record.get('fecha_inicio') or '—')[:10]}"
                                    )
                                    ui.label(
                                        f"Hasta {str(record.get('fecha_finalizacion') or '—')[:10]}"
                                    )
                            if can_manage and (record.get("_id") or record.get("id")):
                                with ui.row().classes(
                                    "w-full flex-wrap justify-end gap-2"
                                ):
                                    ui.button(
                                        "Editar",
                                        icon="edit",
                                        on_click=lambda item=record: _edit_reward_catalog(
                                            item, token, api_base_url
                                        ),
                                    ).props("flat no-caps").classes(
                                        "text-orange-400"
                                    )

                                    def toggle_catalog(item=record):
                                        catalog_id = item.get("_id") or item.get("id")
                                        result = api_put(
                                            f"/recompensas/catalogo/{catalog_id}/estado",
                                            json={
                                                "activa": not bool(
                                                    item.get("activa", True)
                                                )
                                            },
                                            token=token,
                                            base_url=api_base_url,
                                        )
                                        _notify_result(
                                            result,
                                            "Estado de recompensa actualizado.",
                                        )

                                    ui.button(
                                        "Desactivar" if active else "Activar",
                                        icon="toggle_on" if active else "toggle_off",
                                        on_click=toggle_catalog,
                                    ).props("flat no-caps").classes(
                                        "text-orange-400"
                                    )
            else:
                with ui.card().classes("chargecore-card w-full p-8"):
                    ui.label(
                        "No hay beneficios que coincidan con los filtros."
                    ).classes("w-full text-center text-slate-400")
            with ui.row().classes("w-full items-center justify-between"):
                ui.button(
                    "Anterior",
                    icon="chevron_left",
                    on_click=lambda: change_page(-1),
                ).props("flat no-caps").set_enabled(page_state["number"] > 1)
                ui.button(
                    "Siguiente",
                    icon="chevron_right",
                    on_click=lambda: change_page(1),
                ).props("flat no-caps").set_enabled(
                    page_state["number"] < page_count
                )

    def change_page(offset):
        page_state["number"] += offset
        render_cards()

    def reset_page(_):
        page_state["number"] = 1
        render_cards()

    search.on_value_change(reset_page)
    state_filter.on_value_change(reset_page)
    with ui.row().classes("cc-reward-filter-row w-full"):
        search
        state_filter
    render_cards()


def _render_reward_route(rows):
    with ui.card().classes("chargecore-card cc-reward-route w-full gap-4 p-5"):
        ui.label("Ruta de recompensas").classes("text-lg font-bold text-white")
        ordered_rules = sorted(
            rows,
            key=lambda rule: int(rule.get("cargas_requeridas") or 0),
        )
        if not ordered_rules:
            ui.label("Agrega reglas para visualizar la ruta.").classes(
                "text-sm text-slate-400"
            )
        for rule in ordered_rules:
            reward = rule.get("recompensa")
            reward_name = (
                reward.get("nombre", "Recompensa")
                if isinstance(reward, dict)
                else "Recompensa"
            )
            with ui.element("div").classes("cc-reward-route-item"):
                ui.label(str(rule.get("cargas_requeridas") or "—")).classes(
                    "cc-reward-route-dot"
                )
                with ui.column().classes("gap-1"):
                    ui.label(
                        f"{rule.get('cargas_requeridas', '—')} cargas"
                    ).classes("font-semibold text-white")
                    ui.label(reward_name).classes("text-sm text-slate-300")
                    ui.label(
                        "Activa" if rule.get("activa", True) else "Inactiva"
                    ).classes(
                        "text-xs text-green-400"
                        if rule.get("activa", True)
                        else "text-xs text-slate-500"
                    )


def _render_reward_data_table(headers, rows, column_template, empty_message):
    with ui.card().classes("chargecore-card w-full overflow-hidden p-0"):
        with ui.element("div").classes("cc-reward-table-wrap"):
            with ui.element("div").classes("cc-reward-data-head").style(
                f"grid-template-columns: {column_template};"
            ):
                for header in headers:
                    ui.label(header)
            if rows:
                for row in rows:
                    with ui.element("div").classes("cc-reward-data-row").style(
                        f"grid-template-columns: {column_template};"
                    ):
                        for value in row:
                            ui.label(str(value or "—")).classes(
                                "min-w-0 break-words"
                            )
            else:
                with ui.element("div").classes("cc-reward-data-empty"):
                    ui.label(empty_message)


def _render_reward_issued(rows):
    type_labels = {
        "carga_gratuita": "Carga gratuita",
        "beneficio_especial": "Beneficio especial",
    }
    normalized_rows = [
        {
            "usuario": row.get("usuario") or "Sin usuario",
            "recompensa": type_labels.get(
                row.get("tipo_de_recompensa"),
                row.get("tipo_de_recompensa") or "Recompensa",
            ),
            "regla": row.get("regla") or "—",
            "fecha": row.get("fecha_generacion") or "—",
            "estado": row.get("estado") or "—",
        }
        for row in rows
    ]
    search = ui.input(
        "Buscar otorgamientos",
        placeholder="Usuario o recompensa",
    ).props("outlined clearable").classes("flex-1")
    state_filter = ui.select(
        {
            "": "Todos los estados",
            "Pendiente": "Disponibles",
            "Usada": "Usadas",
        },
        value="",
        label="Estado",
    ).props("outlined").classes("w-full max-w-xs")
    container = ui.column().classes("w-full gap-4")
    page_state = {"number": 1}

    def render():
        query = (search.value or "").strip().casefold()
        selected_state = state_filter.value or ""
        filtered = [
            row
            for row in normalized_rows
            if (
                not query
                or query
                in f"{row['usuario']} {row['recompensa']}".casefold()
            )
            and (not selected_state or row["estado"] == selected_state)
        ]
        page_count = max(
            1,
            (len(filtered) + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE,
        )
        page_state["number"] = min(max(page_state["number"], 1), page_count)
        start = (page_state["number"] - 1) * RECORDS_PER_PAGE
        visible = filtered[start : start + RECORDS_PER_PAGE]
        container.clear()
        with container:
            _render_reward_data_table(
                ("Usuario", "Recompensa", "Regla", "Fecha", "Estado"),
                [
                    (
                        row["usuario"],
                        row["recompensa"],
                        row["regla"],
                        row["fecha"],
                        row["estado"],
                    )
                    for row in visible
                ],
                "1.25fr 1.25fr 1fr 1fr .8fr",
                "No hay recompensas otorgadas.",
            )
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(
                    f"{len(filtered)} registros · página "
                    f"{page_state['number']} de {page_count}"
                ).classes("text-sm text-slate-400")
                with ui.row().classes("gap-2"):
                    ui.button(
                        "Anterior",
                        icon="chevron_left",
                        on_click=lambda: change_page(-1),
                    ).props("flat no-caps").set_enabled(page_state["number"] > 1)
                    ui.button(
                        "Siguiente",
                        icon="chevron_right",
                        on_click=lambda: change_page(1),
                    ).props("flat no-caps").set_enabled(
                        page_state["number"] < page_count
                    )

    def change_page(offset):
        page_state["number"] += offset
        render()

    def reset_page(_):
        page_state["number"] = 1
        render()

    search.on_value_change(reset_page)
    state_filter.on_value_change(reset_page)
    with ui.row().classes("cc-reward-filter-row w-full"):
        search
        state_filter
    render()


def _render_reward_ranking(ranking_rows):
    top_users = ranking_rows[:3]
    if top_users:
        with ui.element("div").classes(
            "grid w-full grid-cols-1 gap-4 md:grid-cols-3"
        ):
            medals = ("looks_one", "looks_two", "looks_3")
            colors = ("#f59e0b", "#94a3b8", "#c08457")
            for index, row in enumerate(top_users):
                with ui.card().classes(
                    "chargecore-card cc-reward-medal w-full gap-3 p-5"
                ):
                    ui.icon(medals[index]).classes(
                        "cc-reward-medal-icon"
                    ).style(f"color: {colors[index]};")
                    ui.label(row["usuario"]).classes(
                        "text-base font-bold text-white"
                    )
                    ui.label(
                        f"{row['cargas']} cargas · {row['recompensas']} recompensas"
                    ).classes("text-sm text-slate-400")
    search = ui.input(
        "Buscar usuario",
        placeholder="Nombre del usuario",
    ).props("outlined clearable").classes("w-full max-w-sm")
    container = ui.column().classes("w-full gap-4")
    page_state = {"number": 1}
    columns = "50px 1.3fr .7fr .8fr .8fr 1.5fr"

    def render():
        query = (search.value or "").strip().casefold()
        filtered = [
            row
            for row in ranking_rows
            if not query or query in row["usuario"].casefold()
        ]
        page_count = max(
            1,
            (len(filtered) + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE,
        )
        page_state["number"] = min(max(page_state["number"], 1), page_count)
        start = (page_state["number"] - 1) * RECORDS_PER_PAGE
        visible = filtered[start : start + RECORDS_PER_PAGE]
        container.clear()
        with container:
            with ui.card().classes("chargecore-card w-full gap-4 p-4"):
                ui.label("Clasificación de usuarios").classes(
                    "text-lg font-bold text-white"
                )
                with ui.element("div").classes("cc-reward-table-wrap"):
                    with ui.element("div").classes("cc-reward-data-head").style(
                        f"grid-template-columns: {columns};"
                    ):
                        for header in (
                            "#",
                            "Usuario",
                            "Cargas",
                            "Recompensas",
                            "Nivel",
                            "Progreso",
                        ):
                            ui.label(header)
                    if visible:
                        for index, row in enumerate(visible, start=start + 1):
                            with ui.element("div").classes(
                                "cc-reward-data-row"
                            ).style(f"grid-template-columns: {columns};"):
                                ui.label(str(index)).classes(
                                    "font-semibold text-orange-400"
                                )
                                ui.label(row["usuario"]).classes(
                                    "font-semibold text-white"
                                )
                                ui.label(str(row["cargas"]))
                                ui.label(str(row["recompensas"]))
                                ui.label(row["nivel"])
                                with ui.column().classes("min-w-0 gap-1"):
                                    ui.linear_progress(
                                        value=row["progreso"] / 100
                                    ).props("rounded color=orange")
                                    ui.label(row["progreso_texto"]).classes(
                                        "text-xs text-slate-400"
                                    )
                    else:
                        with ui.element("div").classes("cc-reward-data-empty"):
                            ui.label("Aún no hay actividad para clasificar.")
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(
                    f"{len(filtered)} usuarios · página "
                    f"{page_state['number']} de {page_count}"
                ).classes("text-sm text-slate-400")
                with ui.row().classes("gap-2"):
                    ui.button(
                        "Anterior",
                        icon="chevron_left",
                        on_click=lambda: change_page(-1),
                    ).props("flat no-caps").set_enabled(page_state["number"] > 1)
                    ui.button(
                        "Siguiente",
                        icon="chevron_right",
                        on_click=lambda: change_page(1),
                    ).props("flat no-caps").set_enabled(
                        page_state["number"] < page_count
                    )

    def change_page(offset):
        page_state["number"] += offset
        render()

    def reset_page(_):
        page_state["number"] = 1
        render()

    search.on_value_change(reset_page)
    render()


def _render_rewards_page(token, api_base_url, user):
    _render_reward_styles()
    catalog_result = api_get(
        "/recompensas/catalogo",
        token=token,
        base_url=api_base_url,
    )
    rules_result = api_get(
        "/recompensas/reglas",
        token=token,
        base_url=api_base_url,
    )
    issued_result = api_get(
        "/recompensas/",
        token=token,
        base_url=api_base_url,
    )
    for result in (catalog_result, rules_result, issued_result):
        if isinstance(result, dict) and result.get("error"):
            _show_api_error(result)
            return
    catalog_rows = _rows_from_response(catalog_result)
    rule_rows = _rows_from_response(rules_result)
    issued_rows = _rows_from_response(issued_result)
    if catalog_rows is None or rule_rows is None or issued_rows is None:
        show_error("La respuesta del servidor no tiene el formato esperado.")
        return

    can_manage = user.get("rol") == "superadmin"
    with ui.row().classes("w-full flex-wrap items-center justify-between gap-4"):
        render_page_heading(
            "Sistema de recompensas",
            "Administra beneficios, reglas y reconocimientos para tus usuarios.",
        )
        new_reward_button = (
            _render_reward_create_button(token, api_base_url)
            if can_manage
            else None
        )
        new_rule_button = (
            ui.button(
                "Nueva regla",
                icon="add",
                on_click=lambda: _render_reward_rule_button(token, api_base_url),
            )
            .props("no-caps")
            .classes("chargecore-primary bg-[#ff500a] text-white")
            if can_manage
            else None
        )

    _render_summary_cards(
        _reward_summary_cards(catalog_rows, rule_rows, issued_rows)
    )
    tabs = ui.tabs().classes("cc-reward-tabs w-full")
    with tabs:
        ui.tab("catalogo", label="Catálogo")
        ui.tab("reglas", label="Reglas")
        ui.tab("otorgadas", label="Otorgadas")
        ui.tab("ranking", label="Ranking")
    panel = ui.column().classes("w-full gap-4")
    tab_cache = {}

    def load_report_rows(endpoint, params=None):
        result = api_get(
            endpoint,
            params=params,
            token=token,
            base_url=api_base_url,
        )
        if isinstance(result, dict) and result.get("error"):
            _show_api_error(result)
            return None
        rows = _rows_from_response(result)
        if rows is None:
            show_error("La respuesta del servidor no tiene el formato esperado.")
            return None
        return rows

    def render_tab(tab_name):
        if new_reward_button:
            new_reward_button.set_visibility(tab_name == "catalogo")
        if new_rule_button:
            new_rule_button.set_visibility(tab_name == "reglas")
        panel.clear()
        with panel:
            if tab_name == "catalogo":
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("Catálogo de beneficios").classes(
                        "text-lg font-bold text-white"
                    )
                _render_reward_catalog(
                    catalog_rows,
                    token,
                    api_base_url,
                    can_manage,
                )
            elif tab_name == "reglas":
                with ui.element("div").classes(
                    "grid w-full grid-cols-1 gap-4 xl:grid-cols-[1.7fr_1fr]"
                ):
                    _render_reward_rules(
                        rule_rows,
                        token,
                        api_base_url,
                        can_manage,
                        issued_rows=issued_rows,
                    )
                    _render_reward_route(rule_rows)
            elif tab_name == "otorgadas":
                if "otorgadas" not in tab_cache:
                    granted_rows = load_report_rows("/reportes/recompensas")
                    if granted_rows is not None:
                        tab_cache["otorgadas"] = granted_rows
                else:
                    granted_rows = tab_cache["otorgadas"]
                if granted_rows is not None:
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("Recompensas otorgadas").classes(
                            "text-lg font-bold text-white"
                        )
                    _render_reward_issued(granted_rows)
            elif tab_name == "ranking":
                if "ranking" not in tab_cache:
                    session_totals = load_report_rows(
                        "/reportes/consumo",
                        params={"limit": 1000},
                    )
                    granted_rows = tab_cache.get("otorgadas")
                    if granted_rows is None:
                        granted_rows = load_report_rows("/reportes/recompensas")
                        if granted_rows is not None:
                            tab_cache["otorgadas"] = granted_rows
                    if session_totals is not None and granted_rows is not None:
                        tab_cache["ranking"] = _reward_ranking_rows(
                            session_totals,
                            granted_rows,
                            rule_rows,
                        )
                if "ranking" in tab_cache:
                    _render_reward_ranking(tab_cache["ranking"])

    tabs.on_value_change(lambda event: render_tab(str(event.value)))
    render_tab("catalogo")


def render_page(page_key: str, token: str, api_base_url: str):
    title, endpoint, description = PAGE_CONFIG[page_key]
    user = app.storage.user.get("user") or {}
    _schedule_charger_auto_refresh(page_key)
    add_nicegui_styles()
    drawer = render_sidebar(user, page_key)
    render_header(
        drawer=drawer,
        on_scan=lambda: ui.navigate.to("/pages/cargadores"),
        notifications=[],
        unread_count=0,
        mark_all_read=lambda: None,
        show_scan=False,
    )

    if page_key == "configuracion":
        with page_container():
            render_page_heading(title, description)
            _render_config(token, api_base_url)
        return

    if page_key in {"empresas", "empresa", "operaciones_qr"}:
        with page_container():
            render_page_heading(title, description)
            if page_key == "empresas":
                _render_company_management(token, api_base_url)
            elif page_key == "empresa":
                _render_company_profile(token, api_base_url)
            else:
                _render_qr_operations(token, api_base_url)
        return

    if page_key == "dashboard":
        content = page_container()
        content.classes("cc-dashboard")
        with content:
            _render_dashboard(token, api_base_url)
        return

    content = page_container()
    if page_key == "alertas":
        content.classes("cc-alerts-page")
    elif page_key == "recompensas":
        content.classes("cc-rewards-page")
    elif page_key == "reportes":
        content.classes("cc-reports-page")
    with content:
        if page_key == "alertas":
            _render_alerts_page(token, api_base_url)
            return

        if page_key == "reportes":
            _render_reports(token, api_base_url)
            return

        if page_key == "recompensas":
            _render_rewards_page(token, api_base_url, user)
            return

        if page_key == "sesiones":
            with ui.row().classes("w-full flex-wrap items-center justify-between gap-4"):
                render_page_heading(title, description)
                ui.button(
                    "Actualizar",
                    icon="refresh",
                    on_click=ui.navigate.reload,
                ).props("flat no-caps").classes("text-orange-400")
            _render_sessions_page(token, api_base_url, user)
            return

        if page_key == "eventos":
            with ui.row().classes("w-full flex-wrap items-center justify-between gap-4"):
                render_page_heading(title, description)
                with ui.row().classes("items-center gap-3"):
                    live_toggle = ui.checkbox(
                        value=bool(
                            app.storage.user.get("ocpp_events_live", False)
                        ),
                    ).props("dense")
                    live_toggle.on_value_change(
                        lambda event: app.storage.user.__setitem__(
                            "ocpp_events_live",
                            bool(event.value),
                        )
                    )
                    with ui.row().classes("items-center gap-1"):
                        ui.icon("circle").classes("text-xs text-green-500")
                        ui.label("En vivo").classes("text-sm text-slate-300")
                    ui.button(
                        "Actualizar",
                        icon="refresh",
                        on_click=ui.navigate.reload,
                    ).props("flat no-caps").classes("text-orange-400")
            ui.timer(
                OCPP_LIVE_REFRESH_INTERVAL_SECONDS,
                _reload_ocpp_events_when_idle,
                immediate=False,
            )
            _render_ocpp_events(token, api_base_url)
            return

        with ui.row().classes("w-full flex-wrap items-center justify-between gap-4"):
            render_page_heading(title, description)
            _render_create_button(page_key, token, api_base_url)
            if (
                page_key == "recompensas"
                and user.get("rol") == "superadmin"
            ):
                ui.button(
                    "Crear regla",
                    icon="rule",
                    on_click=lambda: _render_reward_rule_button(
                        token, api_base_url
                    ),
                ).props("no-caps").classes(
                    "chargecore-primary bg-[#ff500a] text-white"
                )
            ui.button(
                "Actualizar",
                icon="refresh",
                on_click=ui.navigate.reload,
            ).props("flat no-caps").classes("text-orange-400")
        params = None
        if page_key == "alertas":
            saved_filters = (
                app.storage.user.get("nicegui_filters", {}).get(page_key, {})
            )
            with ui.row().classes("w-full flex-wrap gap-3"):
                days = ui.select(
                    {7: "Últimos 7 días", 30: "Últimos 30 días", 90: "Últimos 90 días"},
                    value=saved_filters.get("dias", 7),
                    label="Periodo",
                ).props("outlined").classes("w-full max-w-xs")
                filter_controls = {"dias": days}
                if page_key == "alertas":
                    severity = ui.select(
                        {
                            "": "Todos los niveles",
                            "info": "Información",
                            "warning": "Advertencia",
                            "error": "Error",
                            "debug": "Depuración",
                        },
                        value=saved_filters.get("nivel", ""),
                        label="Nivel",
                    ).props("outlined").classes("w-full max-w-xs")
                    filter_controls["nivel"] = severity
                    module = ui.input(
                        "Módulo",
                        value=saved_filters.get("modulo", ""),
                        placeholder="Opcional",
                    ).props("outlined").classes("w-full max-w-xs")
                    filter_controls["modulo"] = module
                params = _build_list_params(
                    page_key,
                    {
                        name: element.value
                        for name, element in filter_controls.items()
                    },
                )

                def apply_monitoring_filters():
                    all_filters = app.storage.user.get("nicegui_filters", {})
                    all_filters[page_key] = {
                        name: element.value for name, element in filter_controls.items()
                    }
                    app.storage.user["nicegui_filters"] = all_filters
                    ui.navigate.reload()

                ui.button(
                    "Aplicar filtros",
                    icon="filter_alt",
                    on_click=apply_monitoring_filters,
                ).props("no-caps").classes(
                    "chargecore-primary bg-[#ff500a] text-white"
                )
        result = (
            _load_all_users(token, api_base_url)
            if page_key == "usuarios"
            else api_get(
                endpoint,
                params=params,
                token=token,
                base_url=api_base_url,
            )
        )
        if isinstance(result, dict) and "error" in result:
            _show_api_error(result)
            return

        if page_key == "recompensas":
            with ui.card().classes("chargecore-card w-full gap-4 p-5"):
                ui.label("Catálogo de recompensas").classes(
                    "text-lg font-bold text-white"
                )
                catalog_rows = _rows_from_response(result) or []
                _render_records(page_key, catalog_rows, token, api_base_url)
            rules = api_get(
                "/recompensas/reglas",
                token=token,
                base_url=api_base_url,
            )
            with ui.card().classes("chargecore-card w-full gap-4 p-5"):
                ui.label("Reglas de recompensas").classes(
                    "text-lg font-bold text-white"
                )
                if isinstance(rules, dict) and "error" in rules:
                    _show_api_error(rules)
                else:
                    _render_reward_rules(
                        _rows_from_response(rules) or [],
                        token,
                        api_base_url,
                        can_manage=user.get("rol") == "superadmin",
                    )
            issued = api_get(
                "/recompensas/",
                token=token,
                base_url=api_base_url,
            )
            with ui.card().classes("chargecore-card w-full gap-4 p-5"):
                ui.label("Recompensas otorgadas").classes(
                    "text-lg font-bold text-white"
                )
                if isinstance(issued, dict) and issued.get("error"):
                    _show_api_error(issued)
                else:
                    _display_table(_rows_from_response(issued) or [])
            return

        rows = _rows_from_response(result)
        if rows is None:
            show_error("La respuesta del servidor no tiene el formato esperado.")
            return
        if page_key == "cargadores":
            _render_charger_management(rows, token, api_base_url)
        elif page_key == "usuarios":
            _render_users_records(rows, token, api_base_url)
        elif page_key in {"vehiculos", "tarjetas"}:
            _render_registry_records(page_key, rows, token, api_base_url)
        else:
            with ui.card().classes("chargecore-card w-full gap-4 p-5"):
                _render_records(page_key, rows, token, api_base_url)
