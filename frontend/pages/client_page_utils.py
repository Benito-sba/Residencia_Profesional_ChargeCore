from datetime import datetime

from nicegui import app, ui

from components.nicegui_layout import add_nicegui_styles, render_header, render_sidebar


def client_error(result, fallback="No fue posible cargar la información."):
    if not isinstance(result, dict):
        return fallback
    status_code = result.get("_status_code")
    if status_code == 401:
        app.storage.user.clear()
        ui.navigate.to("/")
        return "Tu sesión expiró. Inicia sesión nuevamente."
    if status_code == 403:
        return "No tienes permisos para acceder a esta sección."
    if status_code == 404:
        return result.get("error") or "No se encontró la información solicitada."
    if status_code == 409:
        return result.get("error") or "La operación no se pudo completar."
    if status_code == 422:
        return result.get("error") or "Revisa los datos ingresados."
    if isinstance(status_code, int) and status_code >= 500:
        return "El servidor no pudo completar la solicitud. Intenta nuevamente."
    return result.get("error") or fallback


def render_client_shell(active_page, token, api_base_url):
    user = app.storage.user.get("user") or {}
    if not token or user.get("rol") != "cliente":
        ui.navigate.to("/")
        return False
    add_nicegui_styles()
    drawer = render_sidebar(user, active_page)
    render_header(
        drawer=drawer,
        on_scan=lambda: ui.navigate.to("/pages/mis_cargas"),
        notifications=[],
        unread_count=0,
        mark_all_read=lambda: None,
        show_scan=True,
    )
    return True


def render_page_heading(title, subtitle):
    with ui.column().classes("gap-1"):
        ui.label(title).classes("text-2xl font-bold text-white")
        ui.label(subtitle).classes("text-sm text-slate-400")


def page_container():
    return ui.column().classes(
        "mx-auto w-full max-w-7xl gap-6 px-4 py-8 md:px-8"
    )


def display_value(value, fallback="—"):
    return fallback if value is None or value == "" else str(value)


def format_date(value):
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(value)
    return parsed.astimezone().strftime("%d %b %Y, %H:%M") if parsed.tzinfo else parsed.strftime(
        "%d %b %Y, %H:%M"
    )


def show_error(message):
    ui.label(message).classes(
        "rounded-xl border border-red-900 bg-red-950/50 p-4 text-red-200"
    )
