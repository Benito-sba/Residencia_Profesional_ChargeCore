import logging
from typing import Any


logger = logging.getLogger("app.websocket.diagnostics")


def _safe_log_value(value: Any, limit: int = 200) -> str:
    text = str(value)
    return "".join(
        character if character >= " " and character != "\x7f" else " "
        for character in text
    )[:limit]


class OCPPWebSocketDiagnosticsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return

        headers = {
            name.lower(): value
            for name, value in scope.get("headers", [])
        }
        client = scope.get("client")
        client_address = (
            f"{client[0]}:{client[1]}" if client else "unknown"
        )
        path = _safe_log_value(scope.get("path", ""))
        host = _safe_log_value(headers.get(b"host", b"").decode("latin-1"))
        origin = _safe_log_value(headers.get(b"origin", b"").decode("latin-1"))
        protocols = _safe_log_value(
            headers.get(b"sec-websocket-protocol", b"").decode("latin-1")
        )
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        auth_scheme = authorization.partition(" ")[0] if authorization else "missing"

        logger.warning(
            "WebSocket entrante: cliente=%s ruta=%s host=%s origin=%s "
            "subprotocolos=%s auth=%s",
            _safe_log_value(client_address),
            path,
            host or "missing",
            origin or "missing",
            protocols or "missing",
            _safe_log_value(auth_scheme),
        )

        accepted = False

        async def diagnostic_receive():
            message = await receive()
            if message["type"] == "websocket.disconnect":
                logger.warning(
                    "WebSocket desconectado por cliente: cliente=%s ruta=%s "
                    "codigo=%s razon=%s",
                    _safe_log_value(client_address),
                    path,
                    message.get("code", "unknown"),
                    _safe_log_value(message.get("reason", "")) or "sin detalle",
                )
            return message

        async def diagnostic_send(message):
            nonlocal accepted
            message_type = message["type"]
            if message_type == "websocket.accept":
                accepted = True
                logger.warning(
                    "Handshake WebSocket aceptado: cliente=%s ruta=%s "
                    "subprotocolo=%s",
                    _safe_log_value(client_address),
                    path,
                    _safe_log_value(message.get("subprotocol", "none")),
                )
            elif message_type == "websocket.close":
                reason = _safe_log_value(message.get("reason", "")) or "sin detalle"
                if accepted:
                    logger.warning(
                        "WebSocket cerrado por servidor: cliente=%s ruta=%s "
                        "codigo=%s razon=%s",
                        _safe_log_value(client_address),
                        path,
                        message.get("code", 1000),
                        reason,
                    )
                else:
                    logger.warning(
                        "Handshake WebSocket rechazado: cliente=%s ruta=%s "
                        "codigo=%s razon=%s",
                        _safe_log_value(client_address),
                        path,
                        message.get("code", 1000),
                        reason,
                    )
            elif message_type == "websocket.http.response.start":
                logger.warning(
                    "Handshake WebSocket rechazado con HTTP %s: "
                    "cliente=%s ruta=%s",
                    message.get("status", "unknown"),
                    _safe_log_value(client_address),
                    path,
                )
            await send(message)

        try:
            await self.app(scope, diagnostic_receive, diagnostic_send)
        except Exception:
            logger.exception(
                "Excepción ASGI durante WebSocket: cliente=%s ruta=%s",
                _safe_log_value(client_address),
                path,
            )
            raise
