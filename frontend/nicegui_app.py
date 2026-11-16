import os
import secrets
from importlib import import_module
from pathlib import Path

from nicegui import app, ui

from components.navigation import (
    PAGE_MODULES,
    PAGE_ROUTES,
    ROLE_HOME_ROUTES,
    ROLE_PAGE_KEYS,
)
from components.nicegui_layout import add_nicegui_styles
from utils.api_client import api_login
from utils.api_config import default_api_base_url
from utils.nicegui_auth import request_password_reset as submit_password_reset


API_BASE_URL = default_api_base_url()


def _session():
    return app.storage.user


def _redirect_for_role():
    user = _session().get("user") or {}
    role = user.get("rol")
    destination = ROLE_HOME_ROUTES.get(role)
    if destination:
        ui.navigate.to(destination)
        return True
    return False


def _render_login():
    add_nicegui_styles()
    if _redirect_for_role():
        return

    ui.add_head_html(
        """
        <style>
            .cc-login-shell {
                display: grid !important;
                grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
                grid-template-rows: auto minmax(0, 1fr);
                align-items: center;
                gap: 20px 32px;
                position: relative;
                isolation: isolate;
                box-sizing: border-box;
                width: 100%;
                max-width: 1200px;
                min-height: 100vh;
                margin: 0 auto;
                padding: 18px 5vw 24px;
                background: #fde8cc;
                color: #1c1c1c;
                font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
            }
            .cc-login-shell::before {
                position: fixed;
                z-index: -1;
                inset: 0;
                background: #fde8cc;
                content: "";
            }
            .cc-login-header {
                grid-column: 1 / -1;
                width: 100%;
                align-self: start;
            }
            .cc-login-header-inner {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 16px;
                font-size: 18px;
                font-weight: 800;
            }
            .cc-login-brand { letter-spacing: -.02em; }
            .cc-login-live {
                display: inline-flex;
                align-items: center;
                gap: 8px;
                border-radius: 999px;
                background: rgba(255, 255, 255, .65);
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            .cc-login-dot {
                width: 8px;
                height: 8px;
                border-radius: 50%;
                background: #ee5a24;
                animation: cc-login-pulse 1.6s infinite;
            }
            .cc-login-hero { grid-column: 1; grid-row: 2; min-width: 0; }
            .cc-login-hero h1 {
                margin: 0 0 8px;
                font-size: clamp(26px, 3.2vw, 40px);
                font-weight: 800;
                line-height: 1.1;
                letter-spacing: -.02em;
            }
            .cc-login-hero p {
                max-width: 440px;
                margin: 0 0 18px;
                color: #6b6b6b;
            }
            .cc-login-stage { position: relative; max-width: 460px; margin: 0 auto; }
            .cc-login-stage svg { display: block; width: 100%; height: auto; }
            .cc-login-chip {
                position: absolute;
                border: 1px solid #e4e1dc;
                border-radius: 12px;
                background: #fff;
                padding: 8px 12px;
                box-shadow: 0 6px 18px rgba(0, 0, 0, .1);
                font-size: 12px;
                animation: cc-login-float 4s ease-in-out infinite;
            }
            .cc-login-chip strong { display: block; font-size: 15px; }
            .cc-login-chip-one { top: 6%; right: -2%; }
            .cc-login-chip-two { bottom: 20%; left: -3%; animation-delay: .6s; }
            .cc-login-chip-three { right: 4%; bottom: 2%; animation-delay: 1.1s; }
            .cc-login-card.q-card {
                grid-column: 2;
                grid-row: 2;
                justify-self: center;
                gap: 10px;
                box-sizing: border-box;
                width: 100%;
                max-width: 400px;
                overflow: hidden;
                border: 1px solid #1c1c1c !important;
                border-radius: 14px !important;
                background: #fff !important;
                padding: 28px 28px 0 !important;
                color: #1c1c1c !important;
                box-shadow: 0 14px 40px rgba(0, 0, 0, .14);
            }
            .cc-login-logo {
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
                font-size: 28px;
                font-weight: 800;
                letter-spacing: -.02em;
            }
            .cc-login-logo span {
                display: grid;
                width: 36px;
                height: 36px;
                place-items: center;
                border-radius: 10px;
                background: #ee5a24;
                color: #fff;
                font-size: 20px;
            }
            .cc-login-card .text-white { color: #1c1c1c !important; }
            .cc-login-card .text-slate-400 { color: #6b6b6b !important; }
            .cc-login-card .cc-login-subtitle {
                margin: 0 0 8px;
                color: #6b6b6b !important;
                font-size: 13px;
                text-align: center;
            }
            .cc-login-card .cc-login-title {
                margin: 4px 0 0;
                font-size: 20px;
                font-weight: 700;
                text-align: center;
            }
            .cc-login-field { width: 100%; color: #1c1c1c; }
            .cc-login-field .q-field__control {
                border-radius: 8px;
                background: #f6f5f3;
            }
            .cc-login-field .q-field__native,
            .cc-login-field .q-field__label,
            .cc-login-field .q-field__marginal { color: #1c1c1c !important; }
            .cc-login-field .q-field__control:before { border-color: #e4e1dc; }
            .cc-login-field .q-field__control:hover:before { border-color: #a9a39c; }
            .cc-login-field .q-field__control:after { border-color: #ee5a24; }
            .cc-login-submit.q-btn {
                width: 100%;
                min-height: 46px;
                border-radius: 8px;
                background: #1c1c1c !important;
                color: #fff !important;
                font-size: 14px;
                font-weight: 700;
            }
            .cc-login-submit.q-btn:hover { opacity: .9; }
            .cc-login-forgot.q-btn {
                align-self: flex-end;
                min-height: 28px;
                color: #2457a6 !important;
                font-size: 12px;
            }
            .cc-login-error {
                min-height: 16px;
                margin: 0;
                color: #ee5a24 !important;
                font-size: 12px;
                text-align: center;
            }
            .cc-login-security {
                margin: 2px 0 0;
                color: #6b6b6b !important;
                font-size: 11px;
                text-align: center;
            }
            .cc-login-footer {
                width: calc(100% + 56px);
                margin: 4px -28px 0;
                border-top: 1px solid #e4e1dc;
                padding: 12px;
                color: #6b6b6b !important;
                font-size: 11px;
                line-height: 1.5;
                text-align: center;
            }
            .cc-reset-card.q-card {
                position: relative;
                box-sizing: border-box;
                width: min(380px, calc(100vw - 32px));
                border-radius: 14px !important;
                background: #fff !important;
                padding: 24px !important;
                color: #1c1c1c !important;
                box-shadow: 0 20px 50px rgba(0, 0, 0, .3);
                animation: cc-login-enter .25s ease-out;
            }
            .cc-reset-close.q-btn { position: absolute; top: 8px; right: 8px; color: #6b6b6b; }
            .cc-reset-title { margin: 0 32px 4px 0; font-size: 18px; font-weight: 700; }
            .cc-reset-copy { margin: 0 0 10px; color: #6b6b6b; font-size: 13px; }
            .cc-reset-feedback { min-height: 18px; font-size: 12px; }
            .cc-reset-card .text-green-300 { color: #1f8d4e !important; }
            .cc-reset-card .text-red-300 { color: #c43d2b !important; }
            @keyframes cc-login-pulse {
                50% { opacity: .3; transform: scale(1.5); }
            }
            @keyframes cc-login-float {
                50% { transform: translateY(-4px); }
            }
            @keyframes cc-login-enter {
                from { opacity: 0; transform: translateY(8px); }
                to { opacity: 1; transform: translateY(0); }
            }
            @media (max-width: 860px) {
                .cc-login-shell {
                    grid-template-columns: minmax(0, 1fr);
                    grid-template-rows: auto auto auto;
                    gap: 18px;
                    min-height: 100vh;
                    padding: 16px 5vw 24px;
                }
                .cc-login-header { grid-column: 1; grid-row: 1; }
                .cc-login-hero { grid-column: 1; grid-row: 3; text-align: center; }
                .cc-login-hero p { margin-right: auto; margin-left: auto; }
                .cc-login-card.q-card { grid-column: 1; grid-row: 2; }
                .cc-login-chip-one { right: 0; }
                .cc-login-chip-two { left: 0; }
            }
            @media (max-width: 420px) {
                .cc-login-header-inner { align-items: flex-start; }
                .cc-login-live { max-width: 150px; font-size: 11px; }
                .cc-login-card.q-card { padding: 22px 18px 0 !important; }
                .cc-login-footer {
                    width: calc(100% + 36px);
                    margin-right: -18px;
                    margin-left: -18px;
                }
            }
        </style>
        """
    )

    with ui.dialog() as reset_dialog:
        with ui.card().classes("cc-reset-card"):
            ui.button(
                icon="close",
                on_click=reset_dialog.close,
            ).props("flat round dense").classes("cc-reset-close")
            ui.label("Restablecer contraseña").classes("cc-reset-title")
            ui.label(
                "Ingresa tu correo y enviaremos la solicitud al soporte de CRODE."
            ).classes("cc-reset-copy")
            reset_email = ui.input(
                "Correo electrónico",
                placeholder="usuario@crode.mx",
            ).props("outlined type=email autocomplete=email").classes(
                "cc-login-field"
            )
            reset_feedback = ui.label().classes("cc-reset-feedback")

            def request_password_reset():
                message, severity = submit_password_reset(
                    reset_email.value,
                    API_BASE_URL,
                )
                reset_feedback.set_text(message)
                if severity == "positive":
                    reset_feedback.classes(
                        add="text-green-300", remove="text-red-300"
                    )
                else:
                    reset_feedback.classes(
                        add="text-red-300", remove="text-green-300"
                    )

            ui.button(
                "Enviar solicitud",
                icon="send",
                on_click=request_password_reset,
            ).props("no-caps color=dark").classes("cc-login-submit")

    with ui.column().classes(
        "cc-login-shell"
    ):
        ui.html(
            """
            <header class="cc-login-header-inner">
                <div class="cc-login-brand">⚡ ChargeCore</div>
                <div class="cc-login-live"><i class="cc-login-dot"></i>Acceso a la plataforma</div>
            </header>
            """,
        ).classes("cc-login-header")
        ui.html(
            """
            <section>
                <h1>Monitoreo de cargadores eléctricos</h1>
                <p>Administra la operación de carga de ChargeCore desde una sola plataforma.</p>
                <div class="cc-login-stage">
                    <svg viewBox="0 0 400 300" role="img" aria-label="Ilustración de un cargador eléctrico ChargeCore">
                        <ellipse cx="200" cy="262" rx="150" ry="20" fill="#1c1c1c" opacity=".18"/>
                        <rect x="132" y="48" width="140" height="210" rx="22" fill="#1c1c1c" transform="translate(8 8)"/>
                        <rect x="132" y="48" width="140" height="210" rx="22" fill="#ee5a24" stroke="#1c1c1c" stroke-width="2"/>
                        <rect x="152" y="72" width="100" height="68" rx="10" fill="#fff" stroke="#1c1c1c" stroke-width="2"/>
                        <path d="M208 80l-24 34h16l-6 20 26-36h-17z" fill="#ee5a24"/>
                        <rect x="152" y="156" width="100" height="10" rx="5" fill="#fff" opacity=".5"/>
                        <rect x="152" y="156" width="64" height="10" rx="5" fill="#fff"/>
                        <circle cx="168" cy="196" r="8" fill="#1c1c1c"/>
                        <circle cx="196" cy="196" r="8" fill="#fff" stroke="#1c1c1c" stroke-width="2"/>
                        <path d="M272 190c40 0 44 30 44 60" fill="none" stroke="#1c1c1c" stroke-width="7" stroke-linecap="round"/>
                        <rect x="306" y="244" width="22" height="30" rx="6" fill="#1c1c1c"/>
                    </svg>
                    <div class="cc-login-chip cc-login-chip-one">Plataforma<strong>ChargeCore</strong></div>
                    <div class="cc-login-chip cc-login-chip-two">Gestión<strong>Operativa</strong></div>
                    <div class="cc-login-chip cc-login-chip-three">Movilidad<strong>Eléctrica</strong></div>
                </div>
            </section>
            """,
        ).classes("cc-login-hero")
        with ui.card().classes("cc-login-card"):
            ui.html(
                '<div class="cc-login-logo"><span>⚡</span><strong>ChargeCore</strong></div>',
            )
            ui.label("Acceso a tu espacio de carga y operación").classes(
                "cc-login-subtitle"
            )
            ui.label("Inicia sesión").classes("cc-login-title")
            email = ui.input(
                "Correo electrónico",
                placeholder="usuario@crode.mx",
            ).props("outlined type=email autocomplete=email").classes(
                "cc-login-field"
            )
            password = ui.input(
                "Contraseña",
                password=True,
                password_toggle_button=True,
            ).props("outlined autocomplete=current-password").classes(
                "cc-login-field"
            )
            login_error = ui.label().classes("cc-login-error")

            def submit_login():
                address = (email.value or "").strip()
                if not address or not password.value:
                    login_error.set_text("Completa el correo y la contraseña.")
                    return
                result = api_login(
                    address,
                    password.value,
                    base_url=API_BASE_URL,
                )
                if not isinstance(result, dict) or result.get("error"):
                    login_error.set_text(
                        result.get("error", "No fue posible iniciar sesión.")
                        if isinstance(result, dict)
                        else "El servidor devolvió una respuesta inesperada."
                    )
                    return
                user = result.get("user")
                access_token = result.get("access_token")
                role = user.get("rol") if isinstance(user, dict) else None
                if (
                    not isinstance(access_token, str)
                    or not access_token
                    or not role
                    or role not in ROLE_PAGE_KEYS
                ):
                    login_error.set_text(
                        "La respuesta de inicio de sesión no contiene una "
                        "sesión válida para esta cuenta."
                    )
                    return
                _session()["token"] = access_token
                _session()["user"] = user
                ui.navigate.to(ROLE_HOME_ROUTES[role])

            ui.button(
                "Iniciar sesión",
                on_click=submit_login,
            ).props("no-caps color=dark").classes("cc-login-submit")
            ui.button(
                "¿Olvidaste tu contraseña?",
                on_click=reset_dialog.open,
            ).props("flat no-caps").classes("cc-login-forgot")
            ui.label("🔒 Acceso mediante tu cuenta ChargeCore").classes(
                "cc-login-security"
            )
            ui.label("ChargeCore · CRODE Mérida").classes(
            "cc-login-footer"
            )


@ui.page("/")
def login_page():
    _render_login()


@ui.page("/logout")
def logout_page():
    _session().clear()
    ui.navigate.to("/")


@ui.page("/portal-cliente")
def client_home_page():
    if not _require_page("inicio"):
        return
    from pages.nicegui_portal import render_page

    render_page(_session()["token"], API_BASE_URL)


def _require_page(page_key):
    session = _session()
    user = session.get("user") or {}
    token = session.get("token")
    role = user.get("rol")
    if not token or role not in ROLE_PAGE_KEYS:
        ui.navigate.to("/")
        return False
    if page_key not in ROLE_PAGE_KEYS[role]:
        ui.navigate.to(ROLE_HOME_ROUTES[role])
        return False
    return True


def _make_page_handler(page_key):
    def page_handler():
        if not _require_page(page_key):
            return
        token = _session()["token"]
        module_name = PAGE_MODULES.get(page_key)
        if module_name in {
            "Client_Mis_Vehiculos",
            "Client_Mis_Cargas",
            "Client_Carga_Activa",
            "Client_Mis_Recompensas",
            "Client_Company_Rewards",
            "Mis_Perfil",
        }:
            page = import_module(f"pages.{module_name}")
            page.render_page(token, API_BASE_URL)
            return
        if module_name in {"ChargeCore_Benefits", "Company_Rewards"}:
            page = import_module(f"pages.{module_name}")
            page.render_page(page_key, token, API_BASE_URL)
            return
        from pages.nicegui_admin import render_page

        render_page(page_key, token, API_BASE_URL)

    return page_handler


for _page_key, _page_route in PAGE_ROUTES.items():
    if _page_key in {"inicio"}:
        continue
    ui.page(_page_route)(_make_page_handler(_page_key))


def run():
    ui.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        title="ChargeCore",
        favicon=Path(__file__).resolve().parent / "assets" / "chargecore_favicon.svg",
        storage_secret=(
            os.getenv("NICEGUI_STORAGE_SECRET")
            or os.getenv("SECRET_KEY")
            or secrets.token_urlsafe(48)
        ),
        reload=False,
        show=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    run()
