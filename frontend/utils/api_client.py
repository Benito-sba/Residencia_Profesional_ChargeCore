import requests
import streamlit as st

from utils.state import default_api_base_url

LOGIN_CONNECTION_ERROR = "No se pudo conectar con el servidor. Verifica que el sistema esté disponible."
LOGIN_INVALID_CREDENTIALS = "El correo o la contraseña son incorrectos."
LOGIN_INACTIVE_USER = "El usuario está inactivo. Contacta al administrador del sistema."
LOGIN_UNEXPECTED_RESPONSE = "El servidor devolvió una respuesta inesperada. Intenta nuevamente."
ERROR_CONNECTION = "No fue posible conectar con el servidor."
ERROR_INTERNAL = "Ocurrió un error inesperado. Intenta nuevamente."


def _friendly_validation_error(detail):
    if not isinstance(detail, list):
        return None

    fields = " ".join(str(item.get("loc", "")) for item in detail if isinstance(item, dict)).lower()
    types = " ".join(str(item.get("type", "")) for item in detail if isinstance(item, dict)).lower()
    messages = " ".join(str(item.get("msg", "")) for item in detail if isinstance(item, dict)).lower()

    if "email" in fields or "email" in messages:
        return "Ingresa un correo electrónico válido."
    if "missing" in types or "required" in messages or "obligatorio" in messages:
        return "Completa todos los campos obligatorios."
    if "date" in types or "fecha" in fields or "fecha" in messages:
        return "Ingresa una fecha válida."
    if any(value in types for value in ("int", "float", "number", "greater", "less")):
        return "Ingresa un valor numérico válido."
    return "Revisa los datos ingresados."


def _friendly_error(response):
    status_code = response.status_code
    if status_code in (401, 403):
        return "No tienes permisos para realizar esta acción."
    if status_code == 404:
        return "El registro solicitado no existe."

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    detail = payload.get("detail") if isinstance(payload, dict) else None
    detail_text = detail.lower() if isinstance(detail, str) else ""

    if any(value in detail_text for value in ("ya existe", "duplic", "registrado")):
        return "Ya existe un registro con esa información."
    if status_code == 409 and isinstance(detail, str) and detail.strip():
        return detail
    if "fecha de inicio" in detail_text or "fecha final" in detail_text:
        return "La fecha final no puede ser anterior a la fecha inicial."
    if status_code == 422:
        return _friendly_validation_error(detail) or "Revisa los datos ingresados."
    if status_code >= 500:
        return ERROR_INTERNAL
    return "No fue posible completar la operación."


def _friendly_exception(error):
    if isinstance(error, (requests.ConnectionError, requests.Timeout)):
        return ERROR_CONNECTION
    return ERROR_INTERNAL


def _error_result(response):
    return {
        "error": _friendly_error(response),
        "_status_code": response.status_code,
    }

def get_base_url():
    return st.session_state.get("api_base_url", default_api_base_url()).rstrip("/")

def get_headers():
    token = st.session_state.get("token")
    return {"Authorization": f"Bearer {token}"} if token else {}

def api_get(endpoint, params=None):
    try:
        response = requests.get(f"{get_base_url()}{endpoint}", headers=get_headers(), params=params, timeout=10)
        return response.json() if response.status_code == 200 else _error_result(response)
    except Exception as error:
        return {"error": _friendly_exception(error)}

def api_post(endpoint, data=None, json=None):
    try:
        if endpoint == "/auth/login" and data:
            response = requests.post(f"{get_base_url()}{endpoint}", data=data, timeout=10)
        else:
            response = requests.post(f"{get_base_url()}{endpoint}", headers=get_headers(), data=data, json=json, timeout=10)
        return response.json() if response.status_code in [200, 201] else _error_result(response)
    except Exception as error:
        return {"error": _friendly_exception(error)}


def api_login(username, password):
    try:
        response = requests.post(
            f"{get_base_url()}/auth/login",
            data={"username": username, "password": password},
            timeout=10,
        )
    except (requests.ConnectionError, requests.Timeout):
        return {"error": LOGIN_CONNECTION_ERROR}
    except requests.RequestException:
        return {"error": LOGIN_CONNECTION_ERROR}

    if response.status_code == 401:
        return {"error": LOGIN_INVALID_CREDENTIALS}
    if response.status_code == 403:
        return {"error": LOGIN_INACTIVE_USER}
    if response.status_code != 200:
        return {"error": LOGIN_UNEXPECTED_RESPONSE}

    try:
        result = response.json()
    except ValueError:
        return {"error": LOGIN_UNEXPECTED_RESPONSE}

    if not isinstance(result, dict) or not result.get("access_token"):
        return {"error": LOGIN_UNEXPECTED_RESPONSE}

    return result

def api_put(endpoint, json=None):
    try:
        response = requests.put(f"{get_base_url()}{endpoint}", headers=get_headers(), json=json, timeout=10)
        return response.json() if response.status_code == 200 else _error_result(response)
    except Exception as error:
        return {"error": _friendly_exception(error)}

def api_delete(endpoint):
    try:
        response = requests.delete(f"{get_base_url()}{endpoint}", headers=get_headers(), timeout=10)
        return response.json() if response.status_code == 200 else _error_result(response)
    except Exception as error:
        return {"error": _friendly_exception(error)}


def get_chargers():
    return api_get("/cargadores/")


def get_charger_status(charge_point_id):
    return api_get(f"/cargadores/{charge_point_id}/status")


def get_connectors(charge_point_id):
    return api_get(f"/cargadores/{charge_point_id}/connectors")


def get_charger_meter_values(charge_point_id, limit=20):
    return api_get(f"/cargadores/{charge_point_id}/meter-values", params={"limit": limit})


def get_sessions(params=None):
    return api_get("/sesiones/", params=params)


def get_active_sessions():
    return api_get("/sesiones/activas")


def get_session(transaction_id):
    return api_get(f"/sesiones/transaction/{transaction_id}")


def get_meter_values(transaction_id, limit=100):
    return api_get(f"/sesiones/transaction/{transaction_id}/meter-values", params={"limit": limit})


def get_ocpp_messages(charge_point_id, params=None):
    return api_get(f"/monitoreo/ocpp-messages/{charge_point_id}", params=params)


def get_ocpp_errors(charge_point_id=None, limit=100):
    params = {"limit": limit}
    if charge_point_id:
        params["charge_point_id"] = charge_point_id
    return api_get("/monitoreo/ocpp-errors", params=params)