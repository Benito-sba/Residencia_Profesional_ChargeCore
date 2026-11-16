from utils.api_client import api_post


def request_password_reset(email, base_url):
    address = (email or "").strip()
    if not address:
        return "Ingresa tu correo electrónico.", "negative"

    result = api_post(
        "/auth/password-reset-request",
        json={"email": address},
        base_url=base_url,
    )
    if isinstance(result, dict) and result.get("error"):
        return result["error"], "negative"
    if isinstance(result, dict) and isinstance(result.get("mensaje"), str):
        return result["mensaje"], "positive"
    return "El servidor devolvió una respuesta inesperada.", "negative"
