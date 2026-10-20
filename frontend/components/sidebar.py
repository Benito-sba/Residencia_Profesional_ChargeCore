import streamlit as st


def _redirect_to_login():
    st.session_state.authenticated = False
    st.session_state.token = None
    st.session_state.user = None
    st.session_state.current_page = "login"
    st.switch_page("app.py")
    st.stop()


def hide_streamlit_navigation(hide_sidebar=False):
    sidebar_rule = "section[data-testid=\"stSidebar\"] { display: none !important; }" if hide_sidebar else ""
    st.markdown(
        """
        <style>
        __SIDEBAR_RULE__
        [data-testid="stSidebarNav"],
        [data-testid="stSidebarNavSeparator"],
        [data-testid="stSidebarNavItems"],
        div[data-testid="stSidebarNav"],
        div[data-testid="stSidebarNavSeparator"],
        section[data-testid="stSidebar"] nav,
        section[data-testid="stSidebar"] hr,
        section[data-testid="stSidebar"] [role="navigation"],
        section[data-testid="stSidebar"] [aria-label="Pages"],
        section[data-testid="stSidebar"] [data-testid="stSidebarNav"],
        ul[data-testid="stSidebarNavItems"],
        div[data-testid="stSidebarHeader"] > div:not([data-testid="stSidebarCollapseButton"]) {
            display: none !important;
            height: 0 !important;
            min-height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
            border: none !important;
            visibility: hidden !important;
        }
        </style>
        """.replace("__SIDEBAR_RULE__", sidebar_rule),
        unsafe_allow_html=True,
    )


def render_sidebar():
    st.markdown(
        """
        <style>
            [data-testid="stSidebarNav"] {display: none !important;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    if not st.session_state.get("authenticated"):
        _redirect_to_login()

    hide_streamlit_navigation()

    page_map = {
        "dashboard": "pages/Dashboard.py",
        "configuracion": "pages/Configuracion.py",
        "usuarios": "pages/Usuarios.py",
        "vehiculos": "pages/Vehiculos.py",
        "tarjetas": "pages/Tarjetas_NFC.py",
        "cargadores": "pages/Cargadores.py",
        "sesiones": "pages/Sesiones.py",
        "eventos": "pages/Eventos_OCPP.py",
        "recompensas": "pages/Recompensas.py",
        "reportes": "pages/Reportes.py",
        "alertas": "pages/Alertas.py",
        "inicio": "pages/Portal_Cliente.py",
        "perfil": "pages/Mi_Perfil.py",
        "mis_vehiculos": "pages/Mis_Vehiculos.py",
        "mis_cargas": "pages/Mis_Cargas.py",
        "carga_activa": "pages/Carga_Activa.py",
        "mis_recompensas": "pages/Mis_Recompensas.py",
    }
    navigation_groups = (
        (
            "Vista general",
            (
                ("dashboard", "▦", "Panel de control"),
                ("usuarios", "👥", "Usuarios"),
                ("vehiculos", "🚗", "Vehículos"),
                ("tarjetas", "💳", "Tarjetas NFC"),
            ),
        ),
        (
            "Administración",
            (("configuracion", "⚙️", "Configuración"),),
        ),
        (
            "Operación",
            (
                ("cargadores", "🔌", "Cargadores"),
                ("sesiones", "⏱️", "Sesiones"),
                ("eventos", "⚡", "Eventos OCPP"),
            ),
        ),
        (
            "Análisis",
            (
                ("recompensas", "🎁", "Recompensas"),
                ("reportes", "📊", "Reportes"),
                ("alertas", "🔔", "Alertas"),
            ),
        ),
        (
            "Cliente",
            (
                ("inicio", "🏠", "Inicio"),
                ("perfil", "👤", "Mi Perfil"),
                ("mis_vehiculos", "🚗", "Mis Vehículos"),
                ("mis_cargas", "⚡", "Mis Cargas"),
                ("carga_activa", "🔋", "Carga Activa"),
                ("mis_recompensas", "🎁", "Mis Recompensas"),
            ),
        ),
    )

    user_role_key = st.session_state.get("user", {}).get("rol")
    role_page_keys = {
        "admin": set(page_map) - {
            "inicio",
            "perfil",
            "mis_vehiculos",
            "mis_cargas",
            "carga_activa",
            "mis_recompensas",
        },
        "operador": {
            "dashboard",
            "cargadores",
            "sesiones",
            "eventos",
            "recompensas",
            "reportes",
            "alertas",
        },
        "consulta": {
            "dashboard",
            "cargadores",
            "sesiones",
            "recompensas",
            "reportes",
            "alertas",
        },
        "cliente": {"inicio", "perfil", "mis_vehiculos", "mis_cargas", "carga_activa", "mis_recompensas"},
    }
    allowed_page_keys = role_page_keys.get(user_role_key, set())
    navigation_groups = tuple(
        (group_title, tuple(page for page in group_pages if page[0] in allowed_page_keys))
        for group_title, group_pages in navigation_groups
        if any(page[0] in allowed_page_keys for page in group_pages)
    )

    user = st.session_state.get("user") or {}
    user_name = user.get("nombre", "Administrador CRODE")
    user_role = user.get("rol", "admin").upper()
    active_page = st.session_state.get("current_page", "dashboard")

    with st.sidebar:
        st.markdown(
            """
            <style>
            section[data-testid="stSidebar"] {
                width: 230px !important;
                min-width: 220px !important;
                max-width: 240px !important;
                overflow: hidden !important;
            }
            section[data-testid="stSidebar"] > div,
            section[data-testid="stSidebar"] div[data-testid="stSidebarContent"],
            section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] {
                height: 100vh !important;
                max-height: 100vh !important;
                overflow-y: auto !important;
                overflow-x: hidden !important;
                padding: 0.45rem 0.5rem 0.5rem !important;
            }
            section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
                gap: 0.2rem !important;
            }
            section[data-testid="stSidebar"] [data-testid="stElementContainer"] {
                margin: 0 !important;
                padding: 0 !important;
            }
            section[data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] {
                visibility: visible !important;
            }
            .chargecore-profile {
                display: flex;
                align-items: center;
                gap: 0.55rem;
                padding: 0.4rem 0.4rem;
                margin: 0.55rem 0 0.35rem;
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
            }
            .chargecore-avatar {
                display: grid;
                place-items: center;
                width: 1.85rem;
                height: 1.85rem;
                flex: 0 0 1.85rem;
                border-radius: 50%;
                background: #dbeafe;
                color: #1d4ed8;
                font-size: 0.9rem;
                font-weight: 800;
            }
            .chargecore-profile-name {
                overflow: hidden;
                color: #334155;
                font-size: 0.8125rem;
                font-weight: 600;
                line-height: 1.2;
                text-overflow: ellipsis;
                white-space: nowrap;
            }
            .chargecore-profile-role {
                color: #64748b;
                font-size: 0.6875rem;
                line-height: 1.2;
                letter-spacing: 0.04em;
            }
            .chargecore-brand {
                display: flex;
                align-items: center;
                gap: 0.55rem;
                padding: 0.15rem 0.2rem 0.65rem;
                color: #0f172a;
                font-size: 0.95rem;
                font-weight: 750;
                line-height: 1.1;
            }
            .chargecore-brand-icon {
                display: grid;
                place-items: center;
                width: 1.8rem;
                height: 1.8rem;
                flex: 0 0 1.8rem;
                border-radius: 8px;
                background: #eff6ff;
                color: #2563eb;
                font-size: 1.1rem;
            }
            .chargecore-brand span {
                display: block;
                margin-top: 0.12rem;
                color: #64748b;
                font-size: 0.64rem;
                font-weight: 500;
                line-height: 1.25 !important;
                white-space: normal !important;
                overflow-wrap: break-word;
                word-break: break-word;
            }
            .chargecore-group-title {
                margin: 0.42rem 0 0.08rem;
                padding: 0 0.2rem;
                color: #64748b;
                font-size: 0.68rem;
                font-weight: 600;
                letter-spacing: 0.07em;
                text-transform: uppercase;
            }
            .chargecore-group-divider {
                height: 1px;
                margin: 0.55rem 0 0.6rem;
                background: #e8eef5;
            }
            section[data-testid="stSidebar"] div[data-testid="stButton"] {
                margin: 0.04rem 0 !important;
            }
            section[data-testid="stSidebar"] div[data-testid="stButton"] > button {
                min-height: 2.1rem !important;
                height: 2.1rem !important;
                justify-content: flex-start !important;
                border: 1px solid transparent !important;
                border-radius: 8px !important;
                padding: 0.08rem 0.45rem !important;
                box-shadow: none !important;
                text-align: left !important;
            }
            section[data-testid="stSidebar"] div[data-testid="stButton"] > button p {
                margin: 0 !important;
                color: #334155 !important;
                font-size: 0.8125rem !important;
                font-weight: 500 !important;
                line-height: 1.15 !important;
            }
            section[data-testid="stSidebar"] div[data-testid="stButton"] > button:hover {
                border-color: #bfdbfe !important;
                background: #eff6ff !important;
            }
            section[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"] {
                border-color: #bfdbfe !important;
                border-left: 3px solid #2563eb !important;
                background: #dbeafe !important;
            }
            section[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"] p {
                color: #1d4ed8 !important;
                font-weight: 750 !important;
            }
            section[data-testid="stSidebar"] .st-key-logout_btn {
                margin-top: 0.55rem !important;
                padding-top: 0.45rem !important;
                border-top: 1px solid #e2e8f0;
            }
            section[data-testid="stSidebar"] .st-key-logout_btn button {
                min-height: 1.7rem !important;
                height: 1.7rem !important;
                border-color: #cbd5e1 !important;
                background: #fff !important;
                font-size: 0.8125rem;
                font-weight: 500;
            }
            section[data-testid="stSidebar"] .st-key-logout_btn button:hover {
                border-color: #ef4444 !important;
                background: #fef2f2 !important;
            }
            section[data-testid="stSidebar"][aria-expanded="false"] {
                width: 62px !important;
                min-width: 62px !important;
                max-width: 62px !important;
            }
            section[data-testid="stSidebar"][aria-expanded="false"] > div,
            section[data-testid="stSidebar"][aria-expanded="false"] div[data-testid="stSidebarContent"],
            section[data-testid="stSidebar"][aria-expanded="false"] div[data-testid="stSidebarUserContent"] {
                width: 62px !important;
                padding: 0.35rem 0.25rem !important;
                overflow-x: hidden !important;
                overflow-y: hidden !important;
            }
            section[data-testid="stSidebar"][aria-expanded="false"] .chargecore-profile {
                justify-content: center;
                padding: 0.3rem 0;
                margin-bottom: 0.35rem;
            }
            section[data-testid="stSidebar"][aria-expanded="false"] .chargecore-profile > div:not(.chargecore-avatar),
            section[data-testid="stSidebar"][aria-expanded="false"] .chargecore-brand,
            section[data-testid="stSidebar"][aria-expanded="false"] .chargecore-group-title {
                display: none !important;
            }
            section[data-testid="stSidebar"][aria-expanded="false"] .chargecore-brand {
                display: flex !important;
                justify-content: center;
                padding: 0.2rem 0 0.55rem;
            }
            section[data-testid="stSidebar"][aria-expanded="false"] .chargecore-brand > div:not(.chargecore-brand-icon) {
                display: none !important;
            }
            section[data-testid="stSidebar"][aria-expanded="false"] .chargecore-group-divider {
                margin: 0.45rem 0;
            }
            section[data-testid="stSidebar"][aria-expanded="false"] div[data-testid="stButton"] > button {
                width: 56px !important;
                min-height: 2.1rem !important;
                height: 2.1rem !important;
                justify-content: center !important;
                padding: 0 !important;
            }
            section[data-testid="stSidebar"][aria-expanded="false"] div[data-testid="stButton"] > button p {
                width: 100% !important;
                font-size: 0 !important;
                text-align: center !important;
            }
            section[data-testid="stSidebar"][aria-expanded="false"] div[data-testid="stButton"] > button p::before {
                display: inline-block;
                font-size: 1.1rem !important;
                line-height: 1.2;
                vertical-align: middle;
            }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_dashboard button p::before { content: "▦"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_usuarios button p::before { content: "👥"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_vehiculos button p::before { content: "🚗"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_tarjetas button p::before { content: "💳"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_cargadores button p::before { content: "🔌"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_sesiones button p::before { content: "⏱️"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_eventos button p::before { content: "⚡"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_recompensas button p::before { content: "🎁"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_reportes button p::before { content: "📊"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_alertas button p::before { content: "🔔"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_inicio button p::before { content: "🏠"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_perfil button p::before { content: "👤"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_mis_vehiculos button p::before { content: "🚗"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_mis_cargas button p::before { content: "⚡"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_carga_activa button p::before { content: "🔋"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-nav_mis_recompensas button p::before { content: "🎁"; }
            section[data-testid="stSidebar"][aria-expanded="false"] .st-key-logout_btn button p::before { content: "⇥"; font-size: 1rem; }
            @media (max-height: 700px) {
                section[data-testid="stSidebar"] > div,
                section[data-testid="stSidebar"] div[data-testid="stSidebarContent"],
                section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] {
                    padding-top: 0.25rem !important;
                }
                section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
                    gap: 0.1rem !important;
                }
                .chargecore-group-divider {
                    margin: 0.3rem 0 !important;
                }
                section[data-testid="stSidebar"] div[data-testid="stButton"] > button {
                    min-height: 1.8rem !important;
                    height: 1.8rem !important;
                }
            }
            @media (max-width: 768px) {
                section[data-testid="stSidebar"] {
                    min-width: 0 !important;
                }
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            '<div class="chargecore-brand"><div class="chargecore-brand-icon">ϟ</div>'
            '<div>ChargeCore<span>Centro de monitoreo de carga</span></div></div>',
            unsafe_allow_html=True,
        )

        for group_index, (group_title, group_pages) in enumerate(navigation_groups):
            st.markdown(f"**{group_title.upper()}**")
            for key, icon, label in group_pages:
                if st.button(
                    f"{icon}  {label}",
                    key=f"nav_{key}",
                    type="primary" if active_page == key else "secondary",
                    use_container_width=True,
                ):
                    st.session_state.current_page = key
                    try:
                        st.switch_page(page_map[key])
                    except Exception:
                        _redirect_to_login()
            if group_index < len(navigation_groups) - 1:
                st.markdown('<div class="chargecore-group-divider"></div>', unsafe_allow_html=True)

        st.markdown(
            f"""
            <div class="chargecore-profile">
                <div class="chargecore-avatar" aria-hidden="true">{user_name[:1].upper()}</div>
                <div>
                    <div class="chargecore-profile-name" title="{user_name}">{user_name}</div>
                    <div class="chargecore-profile-role">{user_role}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.button("⇥  Cerrar sesión", use_container_width=True, type="secondary", key="logout_btn"):
            _redirect_to_login()
