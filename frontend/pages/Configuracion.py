import streamlit as st
import pandas as pd
from utils.state import init_session_state
from utils.api_client import api_get, api_put, api_post
from components.sidebar import render_sidebar, _redirect_to_login
from datetime import datetime

init_session_state()
render_sidebar()

if not st.session_state.get("authenticated"):
    _redirect_to_login()

# Verificar permisos de administrador
if st.session_state.get("role") != "admin":
    st.error("❌ Acceso denegado. Solo administradores pueden acceder a esta sección.")
    st.stop()

st.markdown("# ⚙️ Configuración del Sistema")
st.markdown("Administra los parámetros y configuración general de ChargeCore")

# ============= TABS DE CONFIGURACIÓN =============
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🎯 Parámetros", 
    "👨‍💼 Administradores", 
    "🎁 Recompensas",
    "🪪 NFC",
    "💾 Datos"
])

# ============= TAB 1: PARÁMETROS GENERALES =============
with tab1:
    st.info(
        "Los parámetros de despliegue (base de datos, clave de firma y URL de "
        "la API) se administran en las variables de entorno de Render. El backend "
        "no ofrece una API para guardar parámetros institucionales desde esta pantalla."
    )

# ============= TAB 2: ADMINISTRADORES =============
with tab2:
    st.markdown("**Gestión de Administradores**")
    
    with st.spinner("Cargando usuarios..."):
        usuarios = api_get("/usuarios/")
    
    if usuarios and "error" not in usuarios:
        admins = [u for u in usuarios if u.get("rol") == "admin"]
        
        st.markdown(f"**Administradores Actuales:** {len(admins)}")
        
        if admins:
            df_admins = pd.DataFrame([{
                "👤 Nombre": a.get("nombre", ""),
                "📧 Email": a.get("email", ""),
                "✅ Estado": "Activo" if a.get("activo", True) else "Inactivo"
            } for a in admins])
            st.dataframe(df_admins, use_container_width=True, hide_index=True)
        
        st.markdown("---")
        st.markdown("**Promover a Administrador**")
        
        usuarios_no_admin = [u for u in usuarios if u.get("rol") != "admin"]
        if usuarios_no_admin:
            with st.form("form_promover"):
                usuario_sel = st.selectbox(
                    "Seleccionar Usuario",
                    options=[f"{u['nombre']} ({u.get('email', '')})" for u in usuarios_no_admin]
                )
                usuario_id = next((u["_id"] for u in usuarios_no_admin if f"{u['nombre']} ({u.get('email', '')})" == usuario_sel), None)
                
                if st.form_submit_button("👑 Promover a administrador", use_container_width=True):
                    result = api_put(f"/usuarios/{usuario_id}", json={"rol": "admin"})
                    if "error" not in result:
                        st.success("✅ Usuario promovido a administrador")
                        st.rerun()
                    else:
                        st.error(f"❌ {result['error']}")
        else:
            st.info("Todos los usuarios son administradores")

# ============= TAB 3: RECOMPENSAS =============
with tab3:
    st.info(
        "Las recompensas persistentes se administran desde la interfaz "
        "**Recompensas**, donde se guardan el catálogo y sus reglas."
    )

# ============= TAB 4: CONFIGURACIÓN NFC =============
with tab4:
    st.info(
        "Las tarjetas y sus estados persistentes se administran desde la "
        "interfaz **Tarjetas NFC**. Las opciones avanzadas de lectura NFC aún "
        "no están implementadas por el backend."
    )

# ============= TAB 5: GESTIÓN DE DATOS =============
with tab5:
    st.markdown("**Gestión de Datos del Sistema**")
    
    st.warning("⚠️ Estas operaciones afectan directamente a los datos del sistema. Actúa con cuidado.")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**📊 Estadísticas Generales**")
        
        with st.spinner("Cargando estadísticas..."):
            usuarios = api_get("/usuarios/")
            cargadores = api_get("/cargadores/")
            sesiones = api_get("/sesiones/", params={"limit": 1})
        
        if usuarios and "error" not in usuarios:
            st.metric("Usuarios", len(usuarios))
        
        if cargadores and "error" not in cargadores:
            st.metric("Cargadores", len(cargadores))
        
        if sesiones and "error" not in sesiones:
            st.metric("Sesiones", len(sesiones) if isinstance(sesiones, list) else 0)
    
    with col2:
        st.markdown("**🔧 Herramientas de Mantenimiento**")
        
        st.info(
            "La exportación completa, sincronización y eliminación automática "
            "de datos antiguos no están disponibles. No se ejecutará ninguna "
            "operación de mantenimiento desde esta pantalla."
        )
    
    st.divider()
    
    st.markdown("**🔐 Seguridad y mantenimiento**")
    st.info(
        "El cambio de contraseña propio, los respaldos descargables y la "
        "auditoría aún no tienen operaciones disponibles en la API. Los "
        "administradores pueden cambiar la contraseña de una cuenta desde "
        "**Usuarios**; no se mostrarán confirmaciones de operaciones no realizadas."
    )

st.divider()

# ============= SECCIÓN: INFORMACIÓN DEL SISTEMA =============
st.markdown("**ℹ️ Información del Sistema**")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Versión", "1.0.0")
with col2:
    st.metric("Estado", "🟢 Operativo")
with col3:
    st.metric("Última Actualización", datetime.now().strftime("%Y-%m-%d"))

st.caption(f"ChargeCore - Sistema de Gestión de Cargadores | © 2024 CRODE Mérida")