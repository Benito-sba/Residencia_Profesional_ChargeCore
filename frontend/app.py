import streamlit as st
from datetime import datetime

from utils.api_client import api_login
from utils.state import init_session_state
from components.sidebar import hide_streamlit_navigation

st.set_page_config(
    page_title="ChargeCore | Inicio de Sesión",
    page_icon="⚡",
    layout="centered",
    initial_sidebar_state="collapsed"
)

init_session_state()

# Aplicar estilos personalizados
st.markdown("""
    <style>
    .block-container {
        width: 100%;
        max-width: 620px;
        margin: 0 auto;
        padding: 2.5rem 1.25rem 2rem;
    }
    .login-container {
        width: 100%;
        margin: 0 auto;
        padding: 0 0 1rem;
    }
    .login-header {
        text-align: center;
        margin-bottom: 40px;
    }
    .login-header h1 {
        color: #0f172a;
        font-size: clamp(2rem, 5vw, 2.5rem);
        margin-bottom: 10px;
        font-weight: 800;
    }
    .login-header p {
        color: #64748b;
        font-size: 1rem;
        margin-bottom: 5px;
    }
    .login-subtitle {
        color: #64748b;
        font-size: 0.9rem;
        text-align: center;
        margin-top: 30px;
    }
    .divider-text {
        text-align: center;
        color: #94a3b8;
        font-size: 0.85rem;
        margin: 25px 0;
    }
    .footer-text {
        text-align: center;
        color: #94a3b8;
        font-size: 0.85rem;
        line-height: 1.6;
        margin: 2rem auto 0;
        padding-top: 20px;
        border-top: 1px solid #e2e8f0;
        white-space: normal;
        overflow-wrap: normal;
    }
    .error-box {
        background-color: #fee2e2;
        border-left: 4px solid #ef4444;
        border-radius: 4px;
        padding: 12px 16px;
        color: #7f1d1d;
        margin-bottom: 20px;
        font-size: 0.9rem;
    }
    .success-box {
        background-color: #dcfce7;
        border-left: 4px solid #22c55e;
        border-radius: 4px;
        padding: 12px 16px;
        color: #15803d;
        margin-bottom: 20px;
        font-size: 0.9rem;
    }
    @media (max-width: 640px) {
        .block-container {
            padding: 1.5rem 1rem 1.5rem;
        }
        .login-header {
            margin-bottom: 2rem;
        }
        .login-header p {
            font-size: 0.95rem;
            line-height: 1.5;
        }
        div[data-testid="stHorizontalBlock"] {
            gap: 0.5rem;
        }
        div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
            min-width: 0 !important;
        }
        div[data-testid="stButton"] button {
            min-height: 2.75rem;
            padding-left: 0.35rem;
            padding-right: 0.35rem;
        }
    }
    </style>
    """, unsafe_allow_html=True)

if not st.session_state.get("authenticated"):
    hide_streamlit_navigation(hide_sidebar=True)
    
    # Encabezado
    st.markdown("""
        <div class="login-container">
            <div class="login-header">
                <h1>⚡ ChargeCore</h1>
                <p>Sistema de Gestión de Cargadores Eléctricos</p>
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown("### Inicia Sesión")
    st.markdown('<p class="login-subtitle">Acceso exclusivo para usuarios administrativos y operativos</p>', unsafe_allow_html=True)
        
    # Formulario de login
    with st.form("form_login", border=False):
            email = st.text_input(
                "Correo Electrónico",
                placeholder="ej. admin@crode.mx",
                label_visibility="collapsed"
            )
            
            password = st.text_input(
                "Contraseña",
                type="password",
                placeholder="Contraseña",
                label_visibility="collapsed"
            )
            
            st.markdown('<p style="height: 5px;"></p>', unsafe_allow_html=True)
            
            submitted = st.form_submit_button(
                "🔐 Inicia Sesión",
                use_container_width=True,
                type="primary"
            )
            
            if submitted:
                email = email.strip()
                if not email or not password:
                    st.markdown(
                        '<div class="error-box">❌ Por favor completa todos los campos</div>',
                        unsafe_allow_html=True
                    )
                    st.stop()
                
                with st.spinner("Verificando credenciales..."):
                    result = api_login(email, password)
                
                if "error" in result:
                    st.markdown(
                        f'<div class="error-box">❌ {result["error"]}</div>',
                        unsafe_allow_html=True
                    )
                else:
                    user = result.get("user")
                    role = user.get("rol") if isinstance(user, dict) else None
                    if not isinstance(user, dict) or not role:
                        st.markdown(
                            '<div class="error-box">❌ No se pudo validar la información de la cuenta.</div>',
                            unsafe_allow_html=True
                        )
                    elif role not in {"admin", "operador", "consulta", "cliente"}:
                        st.markdown(
                            '<div class="error-box">❌ Tu cuenta no tiene permisos para acceder al panel administrativo.</div>',
                            unsafe_allow_html=True
                        )
                    else:
                        st.session_state.authenticated = True
                        st.session_state.token = result["access_token"]
                        st.session_state.user = user
                        st.session_state.role = role
                        st.session_state.current_page = "inicio" if role == "cliente" else "dashboard"

                        st.markdown(
                            '<div class="success-box">✅ ¡Bienvenido! Redirigiendo...</div>',
                            unsafe_allow_html=True
                        )
                        destination = "pages/Portal_Cliente.py" if role == "cliente" else "pages/Dashboard.py"
                        st.switch_page(destination)
        
    # Footer
    st.markdown(f"""
        <p class="footer-text">
            ChargeCore v1.0.0 | © {datetime.now().year} CRODE Mérida<br>
            Todos los derechos reservados
        </p>
    """, unsafe_allow_html=True)

else:
    # Usuario autenticado - redirigir al dashboard
    role = (st.session_state.get("user") or {}).get("rol")
    destination = "pages/Portal_Cliente.py" if role == "cliente" else "pages/Dashboard.py"
    st.switch_page(destination)
