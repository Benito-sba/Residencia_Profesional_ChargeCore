from nicegui import app, ui

from components.navigation import NAVIGATION_GROUPS, PAGE_ROUTES, ROLE_PAGE_KEYS
from utils.api_client import api_post
from utils.api_config import default_api_base_url


def render_sidebar(user, active_page):
    role = user.get("rol")
    allowed_page_keys = ROLE_PAGE_KEYS.get(role, frozenset())
    sidebar_collapsed = bool(app.storage.user.get("sidebar_collapsed", False))
    color_theme = app.storage.user.get("color_theme", "auto")

    drawer = ui.left_drawer(value=False).props(
        f"show-if-above width={80 if sidebar_collapsed else 312} breakpoint=320"
    ).classes(
        "chargecore-drawer"
        + (" chargecore-drawer-collapsed" if sidebar_collapsed else "")
    )
    with drawer:
        with ui.row().classes("chargecore-brand-row items-center gap-3"):
            with ui.element("span").classes("chargecore-brand-icon"):
                ui.icon("bolt")
            with ui.column().classes("chargecore-brand-copy gap-0"):
                ui.label("ChargeCore").classes("chargecore-brand-title")
                ui.label("Centro de monitoreo de carga").classes(
                    "chargecore-brand-caption"
                )
            collapse_button = ui.button(
                icon="chevron_right" if sidebar_collapsed else "chevron_left"
            ).props("flat round dense").classes("chargecore-sidebar-toggle")

        def toggle_sidebar():
            nonlocal sidebar_collapsed
            sidebar_collapsed = not sidebar_collapsed
            app.storage.user["sidebar_collapsed"] = sidebar_collapsed
            if sidebar_collapsed:
                drawer.classes(add="chargecore-drawer-collapsed")
            else:
                drawer.classes(remove="chargecore-drawer-collapsed")
            drawer.props(f"width={80 if sidebar_collapsed else 312}")
            collapse_button.props(
                f"icon={'chevron_right' if sidebar_collapsed else 'chevron_left'}"
            )

        collapse_button.on_click(toggle_sidebar)

        with ui.column().classes("chargecore-nav-list"):
            for group_title, group_pages in NAVIGATION_GROUPS:
                visible_pages = [
                    page for page in group_pages if page[0] in allowed_page_keys
                ]
                if not visible_pages:
                    continue

                with ui.column().classes("chargecore-nav-group"):
                    ui.label(group_title.upper()).classes("chargecore-group-title")
                    for key, icon, label in visible_pages:
                        is_active = active_page == key
                        action = lambda page_url=PAGE_ROUTES[key]: ui.navigate.to(
                            page_url
                        )
                        classes = (
                            "chargecore-nav-item chargecore-nav-active"
                            if is_active
                            else "chargecore-nav-item"
                        )
                        with ui.button(on_click=action).props(
                            "flat no-caps"
                        ).classes(classes):
                            ui.label(icon).classes("chargecore-nav-icon")
                            ui.label(label).classes("chargecore-nav-label")
                            ui.tooltip(label)

        user_name = str(user.get("nombre") or "Admin").strip() or "Admin"
        initials = user_name[:1].upper() or "U"
        user_email = str(user.get("email") or "admin@crode.mx")
        role_label = {
            "superadmin": "Superadministrador",
            "dueno_empresa": "Dueño de empresa",
            "cliente": "Cliente",
        }.get(role, str(role or "Usuario"))
        password_dialog = ui.dialog()
        with password_dialog, ui.card().classes(
            "chargecore-card w-full max-w-md gap-4 p-6"
        ):
            ui.label("Cambiar contraseña").classes(
                "text-xl font-bold text-white"
            )
            ui.label("Ingresa tu contraseña actual y elige una nueva.").classes(
                "text-sm text-slate-400"
            )
            current_password = ui.input(
                "Contraseña actual",
                password=True,
                password_toggle_button=True,
            ).props("outlined autocomplete=current-password").classes("w-full")
            new_password = ui.input(
                "Nueva contraseña",
                password=True,
                password_toggle_button=True,
            ).props("outlined autocomplete=new-password").classes("w-full")
            confirm_password = ui.input(
                "Confirmar nueva contraseña",
                password=True,
                password_toggle_button=True,
            ).props("outlined autocomplete=new-password").classes("w-full")

            def update_password():
                if not current_password.value or not new_password.value:
                    ui.notify("Completa todos los campos.", type="warning")
                    return
                if len(new_password.value) < 6:
                    ui.notify(
                        "La nueva contraseña debe tener al menos 6 caracteres.",
                        type="warning",
                    )
                    return
                if new_password.value != confirm_password.value:
                    ui.notify("Las contraseñas nuevas no coinciden.", type="warning")
                    return
                result = api_post(
                    "/auth/change-password",
                    json={
                        "current_password": current_password.value,
                        "new_password": new_password.value,
                    },
                    token=app.storage.user.get("token"),
                    base_url=default_api_base_url(),
                )
                if isinstance(result, dict) and "error" in result:
                    ui.notify(result["error"], type="negative")
                    return
                ui.notify("Contraseña actualizada correctamente.", type="positive")
                password_dialog.close()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(
                    "Cancelar",
                    on_click=password_dialog.close,
                ).props("flat no-caps")
                ui.button(
                    "Actualizar contraseña",
                    on_click=update_password,
                ).props("no-caps").classes("chargecore-primary text-white")

        with ui.button().classes("chargecore-account-trigger w-full"):
            with ui.row().classes("chargecore-account-row items-center gap-3 text-left"):
                ui.avatar(initials).classes(
                    "chargecore-account-avatar bg-[#ff500a] text-white"
                )
                with ui.column().classes(
                    "chargecore-account-copy min-w-0 flex-1 gap-0 text-left"
                ):
                    ui.label(user_name).classes("chargecore-account-name")
                    with ui.row().classes("items-center gap-1"):
                        ui.element("span").classes("chargecore-account-online")
                        ui.label(role_label).classes("text-xs text-slate-400")
                ui.icon("keyboard_arrow_up").classes(
                    "chargecore-account-chevron text-slate-400"
                )
            with ui.menu().props(
                "anchor=top right self=top left"
            ).classes("chargecore-account-menu"):
                with ui.column().classes(
                    "chargecore-account-menu-content gap-1 p-2"
                ):
                    with ui.row().classes(
                        "items-center gap-3 px-2 py-2"
                    ):
                        ui.avatar(initials).classes(
                            "chargecore-account-avatar bg-[#ff500a] text-white"
                        )
                        with ui.column().classes("min-w-0 gap-0"):
                            ui.label(user_name).classes(
                                "chargecore-account-name truncate"
                            )
                            ui.label(user_email).classes(
                                "truncate text-xs text-slate-400"
                            )
                            ui.label(role_label).classes(
                                "chargecore-account-role"
                            )
                    ui.separator()
                    if "perfil" in allowed_page_keys:
                        profile_dialog = ui.dialog()
                        with profile_dialog:
                            profile_content = ui.element("div").classes("w-full")

                        def open_profile():
                            token = app.storage.user.get("token")
                            if not isinstance(token, str) or not token:
                                ui.notify(
                                    "La sesión expiró. Inicia sesión nuevamente.",
                                    type="negative",
                                )
                                return
                            profile_content.clear()
                            with profile_content:
                                from pages.Mis_Perfil import render_profile_dialog

                                render_profile_dialog(
                                    token,
                                    default_api_base_url(),
                                    profile_dialog,
                                )
                            profile_dialog.open()

                        ui.button(
                            "Mi perfil",
                            icon="person",
                            on_click=open_profile,
                        ).props("flat no-caps").classes(
                            "chargecore-account-menu-action w-full justify-start"
                        )
                    ui.button(
                        "Cambiar contraseña",
                        icon="key",
                        on_click=password_dialog.open,
                    ).props("flat no-caps").classes(
                        "chargecore-account-menu-action w-full justify-start"
                    )
                    if "configuracion" in allowed_page_keys:
                        ui.button(
                            "Configuración",
                            icon="settings",
                            on_click=lambda: ui.navigate.to(
                                PAGE_ROUTES["configuracion"]
                            ),
                        ).props("flat no-caps").classes(
                            "chargecore-account-menu-action w-full justify-start"
                        )

                    def set_color_theme(event):
                        theme = event.value
                        if theme not in {"auto", "light", "dark"}:
                            raise ValueError(f"Tema de apariencia no válido: {theme}")
                        app.storage.user["color_theme"] = theme
                        ui.run_javascript(
                            "window.chargecoreSetTheme("
                            f"{theme!r}, false);"
                        )

                    ui.select(
                        {
                            "auto": "Automático",
                            "light": "Claro",
                            "dark": "Oscuro",
                        },
                        value=color_theme,
                        label="Apariencia",
                        on_change=set_color_theme,
                    ).props("dense outlined options-dense").classes(
                        "chargecore-theme-select w-full"
                    )
                    ui.separator()
                    ui.button(
                        "Cerrar sesión",
                        icon="logout",
                        on_click=lambda: ui.navigate.to("/logout"),
                    ).props("flat no-caps").classes(
                        "chargecore-account-menu-action chargecore-logout "
                        "w-full justify-start"
                    )
    return drawer


def render_header(
    *,
    drawer,
    on_scan,
    notifications,
    unread_count,
    mark_all_read,
    show_scan=True,
):
    with ui.header().classes("chargecore-header"):
        with ui.row().classes("w-full items-center justify-between gap-3"):
            def toggle_drawer():
                drawer.value = not drawer.value
                drawer.update()

            ui.button(
                icon="menu",
                on_click=toggle_drawer,
            ).props("flat round").classes("chargecore-drawer-toggle")
            with ui.row().classes("items-center justify-end gap-3"):
                if show_scan:
                    ui.button(
                        "Acercar tarjeta al cargador",
                        icon="sensors",
                        on_click=on_scan,
                    ).props("no-caps").classes(
                        "chargecore-primary bg-[#ff500a] text-white"
                    )
                with ui.button(
                    str(unread_count) if unread_count else "",
                    icon="notifications",
                ).props("flat round").classes("chargecore-notification-trigger"):
                    with ui.menu().props("anchor=bottom right self=top right").classes(
                        "chargecore-notification-menu"
                    ):
                        with ui.column().classes("w-80 max-w-[90vw] gap-2 p-3"):
                            with ui.row().classes(
                                "w-full items-center justify-between"
                            ):
                                ui.label("Notificaciones").classes(
                                    "text-base font-bold text-white"
                                )
                                if unread_count:
                                    ui.button(
                                        "Marcar leídas",
                                        on_click=mark_all_read,
                                    ).props("flat dense no-caps").classes(
                                        "text-xs text-orange-400"
                                    )

                            if notifications is None:
                                ui.label(
                                    "No se pudieron cargar las notificaciones."
                                ).classes("text-sm text-red-300")
                            elif not notifications:
                                ui.label("Sin notificaciones por ahora.").classes(
                                    "text-sm text-slate-400"
                                )
                            else:
                                for item in notifications:
                                    item_classes = (
                                        "chargecore-notification-unread"
                                        if not item["is_read"]
                                        else ""
                                    )
                                    with ui.card().classes(
                                        "w-full rounded-xl border border-[#393633] "
                                        "bg-[#292622] p-3 shadow-none "
                                        f"{item_classes}"
                                    ):
                                        with ui.row().classes(
                                            "items-start gap-3"
                                        ):
                                            ui.icon(item["icon"]).classes(
                                                "text-xl text-orange-500"
                                            )
                                            with ui.column().classes(
                                                "min-w-0 gap-1"
                                            ):
                                                ui.label(item["title"]).classes(
                                                    "text-sm font-semibold text-white"
                                                )
                                                ui.label(item["detail"]).classes(
                                                    "text-xs text-slate-300"
                                                )
                                                ui.label(item["time"]).classes(
                                                    "text-xs text-slate-500"
                                                )


def add_nicegui_styles():
    color_theme = app.storage.user.get("color_theme", "auto")
    if color_theme not in {"auto", "light", "dark"}:
        color_theme = "auto"
    ui.dark_mode(value=None if color_theme == "auto" else color_theme == "dark")
    ui.add_head_html(
        f"""
        <script>
        document.documentElement.dataset.chargecoreTheme = {color_theme!r};
        window.chargecoreUpdateCharts = () => {{
            if (typeof echarts === 'undefined') return;
            const root = getComputedStyle(document.documentElement);
            const text = root.getPropertyValue('--cc-text-primary').trim();
            const muted = root.getPropertyValue('--cc-text-muted').trim();
            const border = root.getPropertyValue('--cc-border').trim();
            const field = root.getPropertyValue('--cc-field').trim();
            const card = root.getPropertyValue('--cc-bg-card').trim();
            document.querySelectorAll('.cc-dashboard .nicegui-echart').forEach(element => {{
                const chart = echarts.getInstanceByDom(element);
                if (!chart) return;
                chart.setOption({{
                    textStyle: {{color: text}},
                    legend: {{textStyle: {{color: text}}}},
                    xAxis: {{
                        axisLabel: {{color: muted}},
                        axisLine: {{lineStyle: {{color: border}}}}
                    }},
                    yAxis: {{
                        axisLabel: {{color: muted}},
                        splitLine: {{lineStyle: {{color: field}}}}
                    }},
                    tooltip: {{
                        backgroundColor: card,
                        borderColor: border,
                        textStyle: {{color: text}}
                    }}
                }});
            }});
        }};
        window.chargecoreSetTheme = (theme, refresh) => {{
            if (!['auto', 'light', 'dark'].includes(theme)) return;
            document.documentElement.dataset.chargecoreTheme = theme;
            const darkMode = theme === 'dark' ||
                (theme === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches);
            if (window.Quasar && Quasar.Dark) {{
                Quasar.Dark.set(theme === 'auto' ? 'auto' : darkMode);
            }}
            document.body.classList.toggle('dark', darkMode);
            window.dispatchEvent(new CustomEvent('chargecore-theme-change', {{
                detail: {{theme, darkMode}}
            }}));
            window.chargecoreUpdateCharts();
            if (refresh) window.location.reload();
        }};
        if (window.matchMedia) {{
            const media = window.matchMedia('(prefers-color-scheme: dark)');
            media.addEventListener('change', event => {{
                if (document.documentElement.dataset.chargecoreTheme === 'auto') {{
                    window.chargecoreSetTheme('auto', false);
                }}
            }});
        }}
        window.addEventListener('load', () => {{
            setTimeout(window.chargecoreUpdateCharts, 500);
        }});
        </script>
        """
    )
    ui.add_head_html(
        """
        <style>
            :root {
                color-scheme: light;
                --cc-bg-main: #fdf1e0;
                --cc-bg-card: #fff;
                --cc-border: #eadfce;
                --cc-field: #f7f3ec;
                --cc-primary: #ee5a24;
                --cc-text-primary: #1c1c1c;
                --cc-text-secondary: #514d47;
                --cc-text-muted: #7a746b;
                --cc-success: #1fa65a;
                --cc-warning: #e8a010;
                --cc-danger: #d6382f;
                --cc-neutral: #8b8f98;
                --cc-blue: #2f6fe0;
            }
            html, body, #q-app {
                width: 100%; max-width: 100%; overflow-x: hidden !important;
            }
            html::-webkit-scrollbar:horizontal,
            body::-webkit-scrollbar:horizontal,
            #q-app::-webkit-scrollbar:horizontal {
                display: none; height: 0;
            }
            body { background: var(--cc-bg-main); color: var(--cc-text-primary); font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
            .q-layout, .q-page-container {
                width: 100%; max-width: 100%; min-width: 0; overflow-x: hidden;
            }
            .q-page { min-width: 0; min-height: 100vh; background: var(--cc-bg-main); color: var(--cc-text-secondary); }
            .chargecore-drawer {
                width: 312px !important; max-width: 100vw;
                background: #18181b; color: #fff;
                border-right: 1px solid #27272a;
                transition: width 300ms ease, transform 300ms ease;
            }
            .chargecore-drawer .q-drawer__content {
                display: flex; flex-direction: column; box-sizing: border-box;
                width: 100%; min-width: 0; height: 100vh; height: 100dvh;
                overflow: hidden; padding: 16px 14px 14px;
            }
            .chargecore-drawer,
            .chargecore-drawer .q-drawer__content {
                overflow-x: hidden !important;
            }
            .chargecore-brand-row {
                width: 100%; min-width: 0; flex: 0 0 auto;
                min-height: 48px; margin: 0 0 10px;
                flex-wrap: nowrap;
            }
            .chargecore-brand-icon {
                display: grid; place-items: center; width: 2.75rem; height: 2.75rem;
                flex: 0 0 2.75rem; border-radius: .75rem;
                background: #ff500a; color: white;
                font-size: 1.35rem;
            }
            .chargecore-brand-copy { min-width: 0; flex: 1 1 auto; overflow: hidden; }
            .chargecore-brand-title { overflow: hidden; color: #fff; font-size: 1.2rem; font-weight: 800; line-height: 1.2; text-overflow: ellipsis; white-space: nowrap; }
            .chargecore-brand-caption { overflow: hidden; color: #a1a1aa; font-size: .75rem; line-height: 1.3; text-overflow: ellipsis; white-space: nowrap; }
            .q-btn.chargecore-sidebar-toggle {
                width: 32px; height: 32px; min-height: 32px; margin-left: auto;
                color: #a1a1aa;
            }
            .q-btn.chargecore-sidebar-toggle:hover { background: #27272a; color: #fff; }
            .chargecore-group-title {
                flex: 0 0 auto; margin: 8px 8px 5px; color: #a1a1aa;
                font-size: .65rem; font-weight: 700; letter-spacing: .07em;
                line-height: 1.2;
            }
            .chargecore-nav-list {
                width: 100%; min-width: 0; min-height: 0; flex: 1 1 auto;
                display: flex; flex-direction: column; justify-content: flex-start;
                gap: 12px; overflow-x: hidden; overflow-y: auto;
                overscroll-behavior: contain; scrollbar-width: none;
                -ms-overflow-style: none;
            }
            .chargecore-nav-list::-webkit-scrollbar { display: none; }
            .chargecore-nav-group { width: 100%; min-width: 0; flex: 0 0 auto; }
            .chargecore-nav-item {
                width: 100%; min-width: 0; height: 40px; min-height: 40px !important;
                flex: 0 0 auto; margin: 0 0 1px; padding: 0 12px;
                border-radius: 11px; color: #d4d4d8 !important;
                font-size: .94rem; font-weight: 600;
            }
            .chargecore-nav-item .q-btn__content {
                width: 100%; justify-content: flex-start; flex-wrap: nowrap; gap: 12px;
            }
            .chargecore-nav-icon {
                display: inline-flex; width: 18px; flex: 0 0 18px;
                align-items: center; justify-content: center; font-size: 1rem;
            }
            .chargecore-nav-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
            .chargecore-nav-item:hover { background: rgba(39, 39, 42, .65) !important; color: #fff !important; }
            .chargecore-nav-active,
            .chargecore-nav-active:hover {
                background: #f0efea !important; color: #18181b !important;
            }
            .chargecore-user-panel {
                margin: auto .35rem .35rem; gap: .65rem; padding: .75rem;
                border: 1px solid #27272a; border-radius: .75rem; background: #18181b;
            }
            .q-btn.chargecore-account-trigger {
                width: 100%; min-width: 0; min-height: 68px; max-height: 76px;
                flex: 0 0 auto; margin: 8px 0 0; padding: .5rem .65rem;
                border: 1px solid #27272a; border-radius: 16px;
                background: #1c1c1e !important; color: #fff !important;
                text-align: left; overflow: hidden;
            }
            .q-btn.chargecore-account-trigger:hover {
                background: #232326 !important;
            }
            .chargecore-account-row,
            .chargecore-account-trigger .q-btn__content {
                width: 100%; min-width: 0; flex-wrap: nowrap; justify-content: flex-start;
                overflow: hidden;
            }
            .chargecore-account-avatar {
                width: 44px; height: 44px; flex: 0 0 44px;
                background: #ff500a !important; color: #fff !important;
                font-size: 1.1rem; font-weight: 700;
            }
            .chargecore-account-copy { width: 100%; min-width: 0; overflow: hidden; }
            .chargecore-account-name {
                display: -webkit-box; width: 100%; min-width: 0; overflow: hidden;
                color: #fff; font-size: .96rem; font-weight: 700; line-height: 1.2;
                text-align: left; text-overflow: ellipsis;
                -webkit-box-orient: vertical; -webkit-line-clamp: 2;
            }
            .chargecore-account-role {
                display: inline-flex; width: fit-content; max-width: 100%;
                overflow: hidden; margin-top: 2px; text-overflow: ellipsis;
                border-radius: 999px; background: rgba(34, 197, 94, .12);
                padding: 2px 8px; color: #86efac; font-size: .68rem;
                line-height: 1.3; white-space: nowrap;
            }
            .chargecore-account-online {
                width: .5rem; height: .5rem; border-radius: 50%;
                background: #22c55e;
            }
            .chargecore-account-menu {
                width: 19rem; max-width: calc(100vw - 24px); min-width: 16rem;
                box-sizing: border-box; border: 1px solid #27272a; border-radius: 16px;
                background: #1c1c1e !important; color: #fff;
                box-shadow: 0 12px 32px #0009;
            }
            .chargecore-account-menu-content {
                width: 100%; min-width: 0; box-sizing: border-box;
                align-items: stretch;
            }
            .chargecore-account-menu .q-separator { background: #303034; }
            .chargecore-account-menu-action {
                width: 100%; min-height: 42px; border-radius: 9px;
                color: #d4d4d8 !important;
            }
            .chargecore-account-menu-action .q-btn__content {
                width: 100%; justify-content: flex-start; flex-wrap: nowrap;
                text-align: left;
            }
            .chargecore-account-menu-action .q-icon {
                width: 20px; flex: 0 0 20px; margin-right: 8px;
                text-align: center;
            }
            .chargecore-account-menu-action:hover {
                background: #27272a !important; color: #fff !important;
            }
            .chargecore-account-menu-action .q-icon,
            .chargecore-appearance .q-icon { color: #a1a1aa; }
            .chargecore-appearance {
                width: 100%; min-height: 42px; flex-wrap: nowrap;
                color: #d4d4d8; font-size: .88rem;
            }
            .chargecore-account-menu-action.chargecore-logout,
            .chargecore-account-menu-action.chargecore-logout .q-icon {
                color: #f87171 !important;
            }
            .chargecore-account-menu-action.chargecore-logout:hover,
            .chargecore-account-menu-action.chargecore-logout:hover .q-icon {
                color: #fca5a5 !important;
            }
            .chargecore-header {
                min-height: 4rem; background: rgba(15, 15, 17, .96);
                border-bottom: 1px solid #27272a; padding: .5rem 1.25rem;
                backdrop-filter: blur(12px);
            }
            .q-btn.chargecore-notification-trigger { color: #a1a1aa; }
            .q-btn.chargecore-notification-trigger:hover { color: #fff; background: #27272a; }
            .q-btn.chargecore-primary,
            .q-btn[class*="bg-[#ff500a]"] { background: var(--cc-primary) !important; color: #fff !important; }
            .q-btn.chargecore-primary:hover,
            .q-btn[class*="bg-[#ff500a]"]:hover { background: #e64708 !important; filter: brightness(1.08); }
            .q-btn.chargecore-outline {
                color: var(--cc-primary) !important; border-color: var(--cc-primary) !important;
            }
            .q-btn:not(.q-btn--round) { border-radius: .5rem; font-weight: 500; }
            .q-btn:not(.chargecore-primary):not([class*="bg-[#ff500a]"]):not(.chargecore-nav-item):not(.chargecore-account-trigger):not(.chargecore-notification-trigger):not(.q-btn--flat):not(.q-btn--round):not(.q-btn--outline):not(.bg-red-700) {
                background: var(--cc-border); color: var(--cc-text-secondary);
            }
            .q-btn:not(.chargecore-primary):not([class*="bg-[#ff500a]"]):not(.chargecore-nav-item):not(.chargecore-account-trigger):not(.chargecore-notification-trigger):not(.q-btn--flat):not(.q-btn--round):not(.q-btn--outline):not(.bg-red-700):hover {
                background: #3f3f46; color: var(--cc-text-primary);
            }
            .q-btn--flat:not(.chargecore-nav-item):not(.chargecore-account-trigger):not(.chargecore-notification-trigger) { color: var(--cc-text-secondary); }
            .q-btn--flat:not(.chargecore-nav-item):not(.chargecore-account-trigger):not(.chargecore-notification-trigger):hover { background: #27272a; color: var(--cc-text-primary); }
            .q-btn--outline { color: var(--cc-text-secondary); }
            .q-btn--outline:before { border-color: #3f3f46; }
            .q-btn.text-negative, .q-btn[class*="text-red-"], .q-btn.bg-negative,
            .q-btn[class*="bg-red-"] { background: rgba(239, 68, 68, .14) !important; color: var(--cc-danger) !important; }
            .q-btn.text-negative:hover, .q-btn[class*="text-red-"]:hover,
            .q-btn.bg-negative:hover, .q-btn[class*="bg-red-"]:hover { background: rgba(239, 68, 68, .22) !important; }
            .q-btn.text-positive, .q-btn.bg-positive,
            .q-btn[class*="text-green-"], .q-btn[class*="bg-green-"] { background: rgba(34, 197, 94, .14) !important; color: var(--cc-success) !important; }
            .q-btn.text-warning, .q-btn.bg-warning,
            .q-btn[class*="text-amber-"], .q-btn[class*="bg-amber-"] { background: rgba(245, 158, 11, .14) !important; color: var(--cc-warning) !important; }
            .q-field--outlined .q-field__control:before { border-color: #3f3f46; }
            .q-field--outlined.q-field--focused .q-field__control:after { border-color: var(--cc-primary); }
            .q-field__control { border-radius: .5rem; background: #18181b; }
            .q-field__native, .q-field__input { color: var(--cc-text-primary); }
            .q-field__label { color: var(--cc-text-muted); font-size: .75rem; font-weight: 500; }
            .q-tab--active { color: var(--cc-primary) !important; }
            .q-tab__indicator { background: var(--cc-primary) !important; }
            .q-chip { border-radius: 9999px; background: var(--cc-border); color: var(--cc-text-secondary); }
            .q-checkbox__inner--truthy .q-checkbox__bg,
            .q-radio__inner--truthy .q-radio__bg { background: var(--cc-primary); border-color: var(--cc-primary); }
            .q-toggle__inner--truthy .q-toggle__track { background: rgba(255, 80, 10, .45); }
            .q-toggle__inner--truthy .q-toggle__thumb:after { background: var(--cc-primary); }
            .q-dialog__backdrop { background: rgba(0, 0, 0, .72); backdrop-filter: blur(3px); }
            .q-card:not(.chargecore-nfc-card):not(.chargecore-summary-card) {
                padding: 1.25rem; border: 1px solid var(--cc-border); border-radius: .75rem;
                background: var(--cc-bg-card); color: var(--cc-text-primary);
                box-shadow: 0 4px 12px rgba(0, 0, 0, .4);
            }
            .q-card.chargecore-card { padding: 1.25rem; }
            .q-page .text-slate-200, .q-page .text-slate-300,
            .q-page .text-slate-400, .q-page .text-stone-300,
            .q-page .text-stone-400, .q-page .text-gray-300,
            .q-page .text-gray-400, .q-page .text-zinc-300,
            .q-page .text-zinc-400 { color: var(--cc-text-secondary) !important; }
            .q-page .text-slate-500, .q-page .text-slate-600,
            .q-page .text-stone-500, .q-page .text-stone-600,
            .q-page .text-gray-500, .q-page .text-gray-600,
            .q-page .text-zinc-500, .q-page .text-zinc-600 { color: var(--cc-text-muted) !important; }
            .q-page [class*="text-orange-"] { color: var(--cc-primary) !important; }
            .q-page [class*="text-blue-"], .q-page [class*="text-cyan-"],
            .q-page [class*="text-indigo-"], .q-page [class*="text-violet-"],
            .q-page [class*="text-purple-"], .q-page [class*="text-pink-"] { color: var(--cc-text-secondary) !important; }
            .q-page [class*="bg-green-"] { background-color: rgba(34, 197, 94, .14) !important; }
            .q-page [class*="text-green-"] { color: var(--cc-success) !important; }
            .q-page [class*="bg-amber-"], .q-page [class*="bg-yellow-"] { background-color: rgba(245, 158, 11, .14) !important; }
            .q-page [class*="text-amber-"], .q-page [class*="text-yellow-"] { color: var(--cc-warning) !important; }
            .q-page [class*="bg-red-"] { background-color: rgba(239, 68, 68, .14) !important; }
            .q-page [class*="text-red-"] { color: var(--cc-danger) !important; }
            .q-page [class*="bg-orange-"] { background-color: var(--cc-primary) !important; }
            .q-page [class*="bg-blue-"], .q-page [class*="bg-cyan-"],
            .q-page [class*="bg-indigo-"], .q-page [class*="bg-violet-"],
            .q-page [class*="bg-purple-"], .q-page [class*="bg-pink-"] { background-color: var(--cc-border) !important; }
            .q-page [class*="border-blue-"], .q-page [class*="border-cyan-"],
            .q-page [class*="border-indigo-"], .q-page [class*="border-violet-"],
            .q-page [class*="border-purple-"], .q-page [class*="border-pink-"] { border-color: var(--cc-border) !important; }
            .chargecore-callout { border: 1px solid var(--cc-border); border-radius: .5rem; background: var(--cc-bg-card); color: var(--cc-text-secondary); padding: 1rem; }
            .chargecore-callout-warning { border-color: rgba(245, 158, 11, .35); background: rgba(245, 158, 11, .1); color: var(--cc-warning); }
            .q-badge { border-radius: 9999px; padding: .125rem .625rem; font-size: .75rem; font-weight: 500; }
            .q-table__container, .q-table, .q-table thead, .q-table tbody tr { background: var(--cc-bg-card); color: var(--cc-text-primary); }
            .q-table th { background: var(--cc-bg-card); color: var(--cc-text-muted); font-size: .75rem; font-weight: 500; text-transform: uppercase; letter-spacing: .06em; border-color: rgba(39, 39, 42, .5); }
            .q-table td { border-color: rgba(39, 39, 42, .5); }
            .q-table tbody tr:hover { background: #202024; }
            @media (min-width: 821px) {
                .chargecore-drawer-toggle { display: none; }
                .chargecore-header { padding-left: calc(312px + 1.25rem); }
                body:has(.chargecore-drawer-collapsed) .chargecore-header {
                    padding-left: calc(80px + 1.25rem);
                }
            }
            .chargecore-drawer.chargecore-drawer-collapsed { width: 80px !important; }
            .chargecore-drawer.chargecore-drawer-collapsed .q-drawer__content {
                align-items: center; padding-right: 10px; padding-left: 10px;
            }
            .chargecore-drawer-collapsed .chargecore-brand-row {
                width: 100%; flex-wrap: wrap; justify-content: center; gap: 8px;
            }
            .chargecore-drawer-collapsed .chargecore-brand-copy,
            .chargecore-drawer-collapsed .chargecore-group-title,
            .chargecore-drawer-collapsed .chargecore-nav-label,
            .chargecore-drawer-collapsed .chargecore-account-copy,
            .chargecore-drawer-collapsed .chargecore-account-chevron {
                display: none !important;
            }
            .chargecore-drawer-collapsed .chargecore-brand-icon {
                width: 44px; height: 44px; flex-basis: 44px;
            }
            .chargecore-drawer-collapsed .q-btn.chargecore-sidebar-toggle {
                margin: 0 auto;
            }
            .chargecore-drawer-collapsed .chargecore-nav-item {
                width: 48px; padding: 0; justify-content: center;
            }
            .chargecore-drawer-collapsed .chargecore-nav-list {
                align-items: center; justify-content: flex-start; gap: 4px;
                overflow-y: auto;
            }
            .chargecore-drawer-collapsed .chargecore-nav-group { align-items: center; }
            .chargecore-drawer-collapsed .chargecore-nav-item .q-btn__content {
                justify-content: center; gap: 0;
            }
            .chargecore-drawer-collapsed .chargecore-nav-icon {
                width: auto; flex-basis: auto; font-size: 1.1rem;
            }
            .chargecore-drawer-collapsed .q-btn.chargecore-account-trigger {
                width: 56px; min-width: 56px; justify-content: center; padding: 6px;
            }
            .chargecore-drawer-collapsed .chargecore-account-trigger .q-btn__content,
            .chargecore-drawer-collapsed .chargecore-account-trigger .q-focus-helper {
                justify-content: center;
            }
            @media (max-height: 700px) {
                .chargecore-drawer .q-drawer__content { padding: 10px 12px 10px; }
                .chargecore-brand-row { min-height: 42px; margin-bottom: 4px; }
                .chargecore-brand-icon { width: 40px; height: 40px; flex-basis: 40px; }
                .chargecore-brand-title { font-size: 1.05rem; }
                .chargecore-brand-caption { font-size: .68rem; }
                .chargecore-group-title { margin: 5px 7px 3px; font-size: .6rem; }
                .chargecore-nav-item { height: 32px; min-height: 32px !important; font-size: .84rem; }
                .q-btn.chargecore-account-trigger { min-height: 58px; max-height: 62px; margin-top: 5px; }
                .chargecore-account-avatar { width: 38px; height: 38px; flex-basis: 38px; }
                .chargecore-account-name { font-size: .86rem; }
            }
            @media (max-height: 500px) {
                .chargecore-drawer .q-drawer__content { padding: 6px 10px; }
                .chargecore-brand-row { min-height: 34px; margin-bottom: 2px; gap: 8px; }
                .chargecore-brand-icon { width: 32px; height: 32px; flex-basis: 32px; font-size: 1rem; }
                .chargecore-brand-title { font-size: .9rem; }
                .chargecore-brand-caption { font-size: .6rem; }
                .q-btn.chargecore-sidebar-toggle { width: 26px; height: 26px; min-height: 26px; }
                .chargecore-group-title { margin: 3px 6px 2px; font-size: .54rem; }
                .chargecore-nav-item { height: 24px; min-height: 24px !important; font-size: .75rem; }
                .chargecore-nav-icon { font-size: .82rem; }
                .q-btn.chargecore-account-trigger { min-height: 48px; max-height: 50px; margin-top: 3px; padding: 3px 8px; }
                .chargecore-account-avatar { width: 32px; height: 32px; flex-basis: 32px; font-size: .9rem; }
                .chargecore-account-name { font-size: .74rem; -webkit-line-clamp: 1; }
                .chargecore-account-role { margin-top: 0; padding: 1px 5px; font-size: .58rem; }
                .chargecore-account-online { width: 6px; height: 6px; }
            }
            .chargecore-notification-menu { background: #18181b; border: 1px solid #27272a; border-radius: .75rem; box-shadow: 0 8px 24px #0009; }
            .chargecore-notification-unread { border-color: var(--cc-primary) !important; }
            .chargecore-status-success { background: color-mix(in srgb, var(--cc-success) 14%, var(--cc-bg-card)) !important; color: var(--cc-success) !important; }
            .chargecore-status-progress { background: color-mix(in srgb, var(--cc-primary) 14%, var(--cc-bg-card)) !important; color: var(--cc-primary) !important; }
            .chargecore-status-warning { background: color-mix(in srgb, var(--cc-warning) 14%, var(--cc-bg-card)) !important; color: var(--cc-warning) !important; }
            .chargecore-status-error { background: color-mix(in srgb, var(--cc-danger) 14%, var(--cc-bg-card)) !important; color: var(--cc-danger) !important; }
            .chargecore-status-neutral { background: var(--cc-field) !important; color: var(--cc-text-secondary) !important; }
            .chargecore-card {
                border: 1px solid var(--cc-border); border-radius: .75rem;
                background: var(--cc-bg-card); color: var(--cc-text-primary);
                box-shadow: 0 4px 12px rgba(28, 28, 28, .08);
            }
            .chargecore-nfc-card {
                position: relative; overflow: hidden; min-height: 15rem;
                border: 1px solid rgba(255,255,255,.12); border-radius: .75rem;
                background: var(--cc-primary);
                box-shadow: 0 8px 24px rgba(0,0,0,.4);
            }
            .q-btn.chargecore-nfc-add {
                min-height: 15rem !important; color: #fff !important;
                background: var(--cc-primary) !important;
                box-shadow: 0 8px 24px rgba(0,0,0,.4) !important;
                text-align: left;
            }
            .q-btn.chargecore-nfc-add:hover { filter: brightness(1.06); }
            .chargecore-nfc-card:after {
                position: absolute; content: ""; width: 13rem; height: 13rem;
                right: -5rem; top: -5rem; border-radius: 50%;
                background: rgba(255,255,255,.10);
                pointer-events: none;
            }
            .chargecore-nfc-card > * { position: relative; z-index: 1; }
            .chargecore-chip {
                width: 2.5rem; height: 1.75rem; border-radius: .45rem;
                border: 1px solid rgba(255,255,255,.25);
                background: #71717a;
            }
            .chargecore-summary-card {
                border-radius: .75rem; background: var(--cc-primary);
                border: 1px solid rgba(255,255,255,.12);
            }
            :root[data-chargecore-theme="light"] {
                color-scheme: light;
                --cc-bg-main: #fdf1e0;
                --cc-bg-card: #fff;
                --cc-border: #eadfce;
                --cc-field: #f7f3ec;
                --cc-primary: #ee5a24;
                --cc-text-primary: #1c1c1c;
                --cc-text-secondary: #514d47;
                --cc-text-muted: #7a746b;
                --cc-success: #1fa65a;
                --cc-warning: #e8a010;
                --cc-danger: #d6382f;
                --cc-neutral: #8b8f98;
                --cc-blue: #2f6fe0;
            }
            :root[data-chargecore-theme="dark"] {
                color-scheme: dark;
                --cc-bg-main: #241a11;
                --cc-bg-card: #1f1f1f;
                --cc-border: #3a3430;
                --cc-field: #2a2927;
                --cc-primary: #ee5a24;
                --cc-text-primary: #f3f1ee;
                --cc-text-secondary: #d0cbc4;
                --cc-text-muted: #a8a49e;
                --cc-success: #1fa65a;
                --cc-warning: #e8a010;
                --cc-danger: #d6382f;
                --cc-neutral: #8b8f98;
                --cc-blue: #2f6fe0;
            }
            :root[data-chargecore-theme="light"] body,
            :root[data-chargecore-theme="light"] #q-app {
                background: var(--cc-bg-main);
                color: var(--cc-text-primary);
            }
            :root[data-chargecore-theme="light"] .q-page {
                background: var(--cc-bg-main) !important;
                color: var(--cc-text-secondary);
            }
            :root[data-chargecore-theme="light"] .chargecore-drawer,
            :root[data-chargecore-theme="light"] .chargecore-drawer .q-drawer__content {
                background: #fff !important;
                color: var(--cc-text-primary);
                border-color: var(--cc-border);
            }
            :root[data-chargecore-theme="light"] .chargecore-brand-icon,
            :root[data-chargecore-theme="light"] .chargecore-account-avatar,
            :root[data-chargecore-theme="dark"] .chargecore-brand-icon,
            :root[data-chargecore-theme="dark"] .chargecore-account-avatar {
                background: var(--cc-primary) !important;
            }
            :root[data-chargecore-theme="light"] .chargecore-brand-title,
            :root[data-chargecore-theme="light"] .chargecore-account-name {
                color: var(--cc-text-primary);
            }
            :root[data-chargecore-theme="light"] .chargecore-brand-caption,
            :root[data-chargecore-theme="light"] .chargecore-group-title {
                color: var(--cc-text-muted);
            }
            :root[data-chargecore-theme="light"] .chargecore-nav-item {
                color: #475569 !important;
            }
            :root[data-chargecore-theme="light"] .chargecore-nav-item:hover,
            :root[data-chargecore-theme="light"] .chargecore-nav-active,
            :root[data-chargecore-theme="light"] .chargecore-nav-active:hover {
                background: #eaf1fb !important;
                color: #244f8e !important;
            }
            :root[data-chargecore-theme="dark"] .chargecore-nav-active,
            :root[data-chargecore-theme="dark"] .chargecore-nav-active:hover {
                background: #29394d !important;
                color: #dbeafe !important;
            }
            :root[data-chargecore-theme="light"] .q-btn.chargecore-sidebar-toggle,
            :root[data-chargecore-theme="light"] .q-btn.chargecore-notification-trigger {
                color: var(--cc-text-muted);
            }
            :root[data-chargecore-theme="light"] .q-btn.chargecore-sidebar-toggle:hover,
            :root[data-chargecore-theme="light"] .q-btn.chargecore-notification-trigger:hover {
                background: #eef2f7;
                color: var(--cc-text-primary);
            }
            :root[data-chargecore-theme="light"] .chargecore-user-panel,
            :root[data-chargecore-theme="light"] .q-btn.chargecore-account-trigger,
            :root[data-chargecore-theme="light"] .chargecore-account-menu,
            :root[data-chargecore-theme="light"] .chargecore-notification-menu {
                background: #fff !important;
                color: var(--cc-text-primary);
                border-color: var(--cc-border);
                box-shadow: 0 8px 24px rgba(29, 41, 57, .1);
            }
            :root[data-chargecore-theme="light"] .q-btn.chargecore-account-trigger {
                color: var(--cc-text-primary) !important;
            }
            :root[data-chargecore-theme="light"] .q-btn.chargecore-account-trigger:hover,
            :root[data-chargecore-theme="light"] .chargecore-account-menu-action:hover {
                background: #f1f5f9 !important;
            }
            :root[data-chargecore-theme="light"] .chargecore-account-menu-action {
                color: var(--cc-text-secondary) !important;
            }
            :root[data-chargecore-theme="light"] .chargecore-account-menu-action .q-icon {
                color: var(--cc-text-muted);
            }
            :root[data-chargecore-theme="light"] .chargecore-account-menu .q-separator {
                background: var(--cc-border);
            }
            :root[data-chargecore-theme="light"] .chargecore-header {
                background: rgba(255, 255, 255, .96);
                border-color: var(--cc-border);
            }
            :root[data-chargecore-theme="light"] .q-page .q-card,
            :root[data-chargecore-theme="light"] .q-page .q-table,
            :root[data-chargecore-theme="light"] .q-page .q-table__container,
            :root[data-chargecore-theme="light"] .q-page .q-table thead,
            :root[data-chargecore-theme="light"] .q-page .q-table tbody tr {
                background: var(--cc-bg-card);
                color: var(--cc-text-primary);
            }
            :root[data-chargecore-theme="light"] .q-page .chargecore-card {
                border-color: var(--cc-border);
                color: var(--cc-text-primary);
                box-shadow: 0 4px 14px rgba(29, 41, 57, .06);
            }
            :root[data-chargecore-theme="light"] .q-page:not(:has(.cc-dashboard)) .text-white {
                color: var(--cc-text-primary) !important;
            }
            :root[data-chargecore-theme="light"] .q-page:not(:has(.cc-dashboard)) [class*="text-slate-"] {
                color: var(--cc-text-muted) !important;
            }
            :root[data-chargecore-theme="light"] .q-page .q-btn.text-white,
            :root[data-chargecore-theme="light"] .q-page .chargecore-nfc-card,
            :root[data-chargecore-theme="light"] .q-page .q-btn.chargecore-nfc-add {
                color: #fff !important;
            }
            :root[data-chargecore-theme="light"] .q-page .chargecore-nfc-card .text-white,
            :root[data-chargecore-theme="light"] .q-page .chargecore-summary-card .text-white {
                color: #fff !important;
            }
            :root[data-chargecore-theme="light"] .q-page .q-field__control {
                background: #fff;
            }
            :root[data-chargecore-theme="light"] .q-page .q-field__native,
            :root[data-chargecore-theme="light"] .q-page .q-field__input {
                color: var(--cc-text-primary);
            }
            :root[data-chargecore-theme="light"] .q-page .q-field--outlined .q-field__control:before,
            :root[data-chargecore-theme="light"] .q-page .q-btn--outline:before {
                border-color: var(--cc-border);
            }
            :root[data-chargecore-theme="light"] .q-page .q-table th,
            :root[data-chargecore-theme="light"] .q-page .q-table__bottom {
                color: var(--cc-text-muted);
            }
            :root[data-chargecore-theme="light"] .q-page .q-table th,
            :root[data-chargecore-theme="light"] .q-page .q-table td {
                border-color: var(--cc-border);
            }
            :root[data-chargecore-theme="light"] .q-page .q-table tbody tr:hover {
                background: #f1f5f9;
            }
            :root[data-chargecore-theme="light"] .q-page .q-btn:not(.chargecore-primary):not(.chargecore-nav-item):not(.chargecore-account-trigger):not(.chargecore-notification-trigger):not(.q-btn--flat):not(.q-btn--round):not(.q-btn--outline) {
                background: #eaf0f8;
                color: #334155;
            }
            :root[data-chargecore-theme="light"] .q-page .q-btn--flat:not(.chargecore-nav-item):not(.chargecore-account-trigger):not(.chargecore-notification-trigger) {
                color: var(--cc-text-secondary);
            }
            :root[data-chargecore-theme="light"] .q-page .q-btn--flat:not(.chargecore-nav-item):not(.chargecore-account-trigger):not(.chargecore-notification-trigger):hover {
                background: #eef2f7;
            }
            :root[data-chargecore-theme="dark"] .chargecore-header {
                background: rgba(17, 24, 33, .96);
                border-color: var(--cc-border);
            }
            :root[data-chargecore-theme="dark"] .chargecore-drawer,
            :root[data-chargecore-theme="dark"] .chargecore-drawer .q-drawer__content,
            :root[data-chargecore-theme="dark"] .chargecore-user-panel,
            :root[data-chargecore-theme="dark"] .q-btn.chargecore-account-trigger {
                background: var(--cc-bg-card) !important;
                border-color: var(--cc-border);
            }
            :root[data-chargecore-theme="dark"] .chargecore-account-menu,
            :root[data-chargecore-theme="dark"] .chargecore-notification-menu {
                background: var(--cc-bg-card) !important;
                border-color: var(--cc-border);
            }
            :root[data-chargecore-theme="dark"] .q-btn.chargecore-primary:hover,
            :root[data-chargecore-theme="dark"] .q-btn[class*="bg-[#ff500a]"]:hover,
            :root[data-chargecore-theme="light"] .q-btn.chargecore-primary:hover,
            :root[data-chargecore-theme="light"] .q-btn[class*="bg-[#ff500a]"]:hover {
                background: var(--cc-primary) !important;
                filter: brightness(1.06);
            }
            :root[data-chargecore-theme="dark"] .q-page .q-card,
            :root[data-chargecore-theme="dark"] .q-page .q-field__control {
                background-color: var(--cc-bg-card);
                color: var(--cc-text-primary);
            }
            @media (prefers-color-scheme: dark) {
                :root:not([data-chargecore-theme="light"]) {
                    color-scheme: dark;
                    --cc-bg-main: #241a11;
                    --cc-bg-card: #1f1f1f;
                    --cc-border: #3a3430;
                    --cc-field: #2a2927;
                    --cc-primary: #ee5a24;
                    --cc-text-primary: #f3f1ee;
                    --cc-text-secondary: #d0cbc4;
                    --cc-text-muted: #a8a49e;
                    --cc-success: #1fa65a;
                    --cc-warning: #e8a010;
                    --cc-danger: #d6382f;
                    --cc-neutral: #8b8f98;
                    --cc-blue: #2f6fe0;
                }
            }
            body, #q-app, .q-page {
                background: var(--cc-bg-main) !important;
                color: var(--cc-text-primary);
            }
            .chargecore-header {
                background: color-mix(in srgb, var(--cc-bg-card) 96%, transparent) !important;
                color: var(--cc-text-primary);
                border-color: var(--cc-border) !important;
            }
            .chargecore-drawer,
            .chargecore-drawer .q-drawer__content,
            .chargecore-user-panel,
            .q-btn.chargecore-account-trigger,
            .chargecore-account-menu,
            .chargecore-notification-menu {
                background: var(--cc-bg-card) !important;
                color: var(--cc-text-primary) !important;
                border-color: var(--cc-border) !important;
            }
            .chargecore-brand-title,
            .chargecore-account-name { color: var(--cc-text-primary) !important; }
            .chargecore-brand-caption,
            .chargecore-group-title,
            .chargecore-account-chevron { color: var(--cc-text-muted) !important; }
            .chargecore-nav-item { color: var(--cc-text-secondary) !important; }
            .chargecore-nav-item:hover {
                background: var(--cc-field) !important;
                color: var(--cc-text-primary) !important;
            }
            .chargecore-nav-active,
            .chargecore-nav-active:hover {
                background: var(--cc-text-primary) !important;
                color: var(--cc-bg-card) !important;
            }
            .chargecore-account-menu-action {
                color: var(--cc-text-secondary) !important;
            }
            .chargecore-account-menu-action:hover,
            .q-btn.chargecore-sidebar-toggle:hover,
            .q-btn.chargecore-notification-trigger:hover {
                background: var(--cc-field) !important;
                color: var(--cc-text-primary) !important;
            }
            .chargecore-account-menu .q-separator,
            .q-field--outlined .q-field__control:before,
            .q-btn--outline:before {
                border-color: var(--cc-border) !important;
            }
            .q-page .chargecore-card {
                background: var(--cc-bg-card) !important;
                color: var(--cc-text-primary) !important;
                border-color: var(--cc-border) !important;
                box-shadow: 0 4px 12px rgba(28, 28, 28, .08) !important;
            }
            .q-page .q-field__control { background: var(--cc-field); }
            .q-page .q-field__native,
            .q-page .q-field__input { color: var(--cc-text-primary); }
            .q-page .q-field__label { color: var(--cc-text-muted); }
            .q-page .q-table,
            .q-page .q-table__container,
            .q-page .q-table thead,
            .q-page .q-table tbody tr {
                background: var(--cc-bg-card) !important;
                color: var(--cc-text-primary);
            }
            .q-page .q-table th,
            .q-page .q-table td { border-color: var(--cc-border); }
            .q-page .q-table th,
            .q-page .q-table__bottom { color: var(--cc-text-muted); }
            .q-page .q-table tbody tr:hover { background: var(--cc-field) !important; }
            .chargecore-theme-select .q-field__control {
                background: var(--cc-bg-card);
            }
            .chargecore-theme-select .q-field__native,
            .chargecore-theme-select .q-field__label,
            .chargecore-theme-select .q-field__marginal {
                color: var(--cc-text-primary) !important;
            }
            .chargecore-theme-select .q-field__control:before {
                border-color: var(--cc-border) !important;
            }
            @layer theme {
                .chargecore-drawer .q-btn.chargecore-nav-item {
                    color: #d4d4d8 !important;
                }
                .chargecore-drawer .q-btn.chargecore-nav-item .chargecore-nav-label {
                    color: #d4d4d8 !important;
                }
                .chargecore-drawer .q-btn.chargecore-nav-item:hover {
                    background: #27272a !important;
                    color: #fff !important;
                }
                .chargecore-drawer .q-btn.chargecore-nav-item:hover .chargecore-nav-label {
                    color: #fff !important;
                }
                .chargecore-drawer .q-btn.chargecore-nav-active,
                .chargecore-drawer .q-btn.chargecore-nav-active:hover {
                    background: #f0efea !important;
                    color: #18181b !important;
                }
                .chargecore-drawer .q-btn.chargecore-nav-active .chargecore-nav-label,
                .chargecore-drawer .q-btn.chargecore-nav-active:hover .chargecore-nav-label {
                    color: #18181b !important;
                }
                .chargecore-drawer .q-btn.chargecore-account-trigger {
                    background: #1c1c1e !important;
                    color: #fff !important;
                    border-color: #27272a !important;
                }
                .chargecore-drawer .q-btn.chargecore-account-trigger:hover {
                    background: #232326 !important;
                }
                .chargecore-drawer .chargecore-account-avatar {
                    background: #ff500a !important;
                    color: #fff !important;
                }
                .chargecore-account-menu .q-btn.chargecore-account-menu-action,
                .chargecore-account-menu .q-btn.chargecore-account-menu-action .q-btn__content {
                    color: #d4d4d8 !important;
                }
                .chargecore-account-menu .q-btn.chargecore-account-menu-action .q-icon {
                    color: #a1a1aa !important;
                }
                .chargecore-account-menu .q-btn.chargecore-account-menu-action:hover,
                .chargecore-account-menu .q-btn.chargecore-account-menu-action:hover .q-btn__content {
                    background: #27272a !important;
                    color: #fff !important;
                }
                .chargecore-account-menu .q-btn.chargecore-account-menu-action.chargecore-logout,
                .chargecore-account-menu .q-btn.chargecore-account-menu-action.chargecore-logout .q-btn__content,
                .chargecore-account-menu .q-btn.chargecore-account-menu-action.chargecore-logout .q-icon {
                    color: #f87171 !important;
                }
                .chargecore-account-menu .q-btn.chargecore-account-menu-action.chargecore-logout:hover,
                .chargecore-account-menu .q-btn.chargecore-account-menu-action.chargecore-logout:hover .q-btn__content,
                .chargecore-account-menu .q-btn.chargecore-account-menu-action.chargecore-logout:hover .q-icon {
                    color: #fca5a5 !important;
                }
            }
            @media (prefers-reduced-motion: reduce) {
                .chargecore-drawer { transition: none; }
            }
            @media (max-width: 600px) {
                .chargecore-header { padding: .45rem .65rem; }
                .chargecore-nfc-card { min-height: 13rem; }
            }
        </style>
        """
    )
