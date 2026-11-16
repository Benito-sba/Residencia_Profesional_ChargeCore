from nicegui import app, ui

from components.nicegui_layout import render_header, render_sidebar
from pages.client_page_utils import (
    client_error,
    page_container,
    render_client_shell,
    render_page_heading,
    show_error,
)
from utils.api_client import api_get, api_put


def _render_admin_profile(token: str, api_base_url: str):
    ui.label("Mi perfil").classes("text-2xl font-bold text-white")
    ui.label("Administra tu cuenta y el correo de recuperación.").classes(
        "mb-5 text-base text-slate-400"
    )

    with ui.card().classes("chargecore-card w-full max-w-3xl gap-4 p-5 sm:p-7"):
        save_recipient = _render_admin_profile_content(token, api_base_url)
        ui.button(
            "Guardar correo",
            icon="save",
            on_click=save_recipient,
        ).props("no-caps").classes("chargecore-primary text-white")


def _render_admin_profile_content(token: str, api_base_url: str):
    user = app.storage.user.get("user") or {}
    ui.label("Cuenta de superadministrador").classes("text-lg font-semibold")
    ui.label(f"Nombre: {user.get('nombre') or 'Superadministrador'}")
    ui.label(f"Correo de la cuenta: {user.get('email') or '—'}")
    ui.separator()
    ui.label("Correo receptor de solicitudes").classes("text-lg font-semibold")
    ui.label(
        "Aquí llegarán los avisos cuando un usuario solicite recuperar "
        "su contraseña. El superadministrador podrá actualizarla desde Usuarios."
    ).classes("text-sm text-slate-400")

    configuration = api_get(
        "/auth/password-reset-recipient",
        token=token,
        base_url=api_base_url,
    )
    if isinstance(configuration, dict) and "error" in configuration:
        ui.label(
            f"No se pudo cargar el correo configurado: {configuration['error']}"
        ).classes("text-sm text-red-300")
        configured_email = ""
    elif isinstance(configuration, dict):
        configured_email = configuration.get("email") or ""
    else:
        ui.label(
            "La respuesta del servidor no tiene el formato esperado."
        ).classes("text-sm text-red-300")
        configured_email = ""

    recipient_input = ui.input(
        "Correo donde recibir las solicitudes",
        value=configured_email,
        placeholder="admin@crode.mx",
    ).props("outlined type=email autocomplete=email").classes("w-full")

    def save_recipient():
        email = (recipient_input.value or "").strip()
        if not email or "@" not in email:
            ui.notify("Ingresa un correo electrónico válido.", type="warning")
            return
        result = api_put(
            "/auth/password-reset-recipient",
            json={"email": email},
            token=token,
            base_url=api_base_url,
        )
        if isinstance(result, dict) and "error" in result:
            ui.notify(
                f"No se pudo guardar el correo: {result['error']}",
                type="negative",
            )
            return
        ui.notify("Correo receptor actualizado.", type="positive")

    return save_recipient


def _render_client_profile_content(profile, token, api_base_url, dialog=None):
    name_input = ui.input(
        "Nombre completo",
        value=profile.get("nombre") or "",
        validation={
            "Ingresa al menos 2 caracteres.": lambda value: len(
                (value or "").strip()
            )
            >= 2,
        },
    ).props("outlined").classes("w-full")
    ui.input(
        "Correo electrónico",
        value=profile.get("email") or "",
    ).props("outlined readonly").classes("w-full")
    phone_input = ui.input(
        "Teléfono",
        value=profile.get("telefono") or "",
        placeholder="Opcional",
    ).props("outlined").classes("w-full")
    with ui.row().classes("w-full flex-wrap gap-3"):
        ui.input(
            "Tipo de cuenta",
            value="Cliente",
        ).props("outlined readonly").classes("min-w-48 flex-1")
        ui.input(
            "Cargas registradas",
            value=str(profile.get("total_cargas", 0)),
        ).props("outlined readonly").classes("min-w-48 flex-1")

    def save_profile():
        name = (name_input.value or "").strip()
        if len(name) < 2:
            ui.notify("El nombre debe tener al menos 2 caracteres.", type="warning")
            return
        result = api_put(
            "/client/me",
            json={
                "nombre": name,
                "telefono": (phone_input.value or "").strip() or None,
            },
            token=token,
            base_url=api_base_url,
        )
        if isinstance(result, dict) and "error" in result:
            show_error(client_error(result, "No se pudo guardar el perfil."))
            return
        user = app.storage.user.get("user") or {}
        user["nombre"] = name
        app.storage.user["user"] = user
        ui.notify("Tu perfil se actualizó correctamente.", type="positive")
        if dialog is not None:
            dialog.close()
        ui.navigate.reload()

    return save_profile


def render_profile_dialog(token: str, api_base_url: str, dialog):
    role = (app.storage.user.get("user") or {}).get("rol")
    is_superadmin = role == "superadmin"
    with ui.card().classes("chargecore-card w-full max-w-xl gap-4 p-6"):
        ui.label("Mi perfil").classes("text-xl font-bold text-white")
        ui.label(
            "Administra tu cuenta y el correo de recuperación."
            if is_superadmin
            else "Consulta la información de tu cuenta y empresa."
            if role == "dueno_empresa"
            else "Consulta y actualiza los datos de tu cuenta."
        ).classes("text-sm text-slate-400")

        if is_superadmin:
            save_profile = _render_admin_profile_content(token, api_base_url)
            save_label = "Guardar correo"
        elif role == "dueno_empresa":
            user = app.storage.user.get("user") or {}
            company = api_get(
                "/empresas/me",
                token=token,
                base_url=api_base_url,
            )
            ui.label(f"Nombre: {user.get('nombre') or '—'}")
            ui.label(f"Correo: {user.get('email') or '—'}")
            ui.label("Rol: Dueño de empresa")
            if isinstance(company, dict) and company.get("error"):
                show_error(client_error(company, "No fue posible cargar la empresa."))
            elif isinstance(company, dict):
                ui.label(f"Empresa: {company.get('nombre') or '—'}")
            else:
                show_error("La respuesta de la empresa no tiene el formato esperado.")
            save_profile = None
            save_label = ""
        else:
            profile = api_get("/client/me", token=token, base_url=api_base_url)
            if isinstance(profile, dict) and "error" in profile:
                show_error(client_error(profile, "No fue posible cargar tu perfil."))
                save_profile = None
            elif not isinstance(profile, dict):
                show_error("La respuesta del perfil no tiene el formato esperado.")
                save_profile = None
            else:
                save_profile = _render_client_profile_content(
                    profile,
                    token,
                    api_base_url,
                    dialog,
                )
            save_label = "Guardar cambios"

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
            if save_profile is not None:
                ui.button(save_label, on_click=save_profile).props(
                    "no-caps"
                ).classes("chargecore-primary text-white")


def render_page(token: str, api_base_url: str):
    user = app.storage.user.get("user") or {}
    if user.get("rol") in {"superadmin", "dueno_empresa"}:
        drawer = render_sidebar(user, "perfil")
        render_header(
            drawer=drawer,
            on_scan=lambda: None,
            notifications=[],
            unread_count=0,
            mark_all_read=lambda: None,
            show_scan=False,
        )
        with page_container():
            if user.get("rol") == "superadmin":
                _render_admin_profile(token, api_base_url)
            else:
                render_page_heading(
                    "Mi perfil",
                    "Consulta la información de tu cuenta y empresa.",
                )
                company = api_get(
                    "/empresas/me",
                    token=token,
                    base_url=api_base_url,
                )
                with ui.card().classes(
                    "chargecore-card w-full max-w-3xl gap-4 p-5 sm:p-7"
                ):
                    ui.label(f"Nombre: {user.get('nombre') or '—'}")
                    ui.label(f"Correo: {user.get('email') or '—'}")
                    ui.label("Rol: Dueño de empresa")
                    if isinstance(company, dict) and company.get("error"):
                        show_error(
                            client_error(
                                company,
                                "No fue posible cargar la empresa.",
                            )
                        )
                    elif isinstance(company, dict):
                        ui.label(f"Empresa: {company.get('nombre') or '—'}")
                    else:
                        show_error(
                            "La respuesta de la empresa no tiene el formato esperado."
                        )
        return
    if not render_client_shell("perfil", token, api_base_url):
        return

    profile = api_get("/client/me", token=token, base_url=api_base_url)
    with page_container():
        render_page_heading(
            "Mi perfil",
            "Consulta y actualiza los datos de tu cuenta.",
        )
        if isinstance(profile, dict) and "error" in profile:
            show_error(client_error(profile, "No fue posible cargar tu perfil."))
            return
        if not isinstance(profile, dict):
            show_error("La respuesta del perfil no tiene el formato esperado.")
            return

        with ui.card().classes("chargecore-card w-full max-w-3xl gap-5 p-5 sm:p-7"):
            save_profile = _render_client_profile_content(
                profile,
                token,
                api_base_url,
            )
            ui.button(
                "Guardar cambios",
                icon="save",
                on_click=save_profile,
            ).props("no-caps").classes(
                "chargecore-primary bg-[#ff500a] text-white"
            )
