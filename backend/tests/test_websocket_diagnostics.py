import unittest

from app.websocket.diagnostics import OCPPWebSocketDiagnosticsMiddleware


class OCPPWebSocketDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def make_scope(self):
        return {
            "type": "websocket",
            "path": "/ocpp/ABC1234",
            "client": ("192.168.0.212", 43120),
            "headers": [
                (b"host", b"192.168.0.50:8000"),
                (b"origin", b"http://192.168.0.212"),
                (b"sec-websocket-protocol", b"ocpp1.6j"),
                (
                    b"authorization",
                    b"Basic c2VjcmV0LXVzZXI6c2VjcmV0LXBhc3N3b3Jk",
                ),
            ],
        }

    async def test_logs_safe_handshake_details_without_credentials(self):
        sent = []

        async def app(scope, receive, send):
            await receive()
            await send({"type": "websocket.accept", "subprotocol": "ocpp1.6j"})

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            sent.append(message)

        middleware = OCPPWebSocketDiagnosticsMiddleware(app)
        with self.assertLogs("app.websocket.diagnostics", level="WARNING") as captured:
            await middleware(self.make_scope(), receive, send)

        output = "\n".join(captured.output)
        self.assertIn("cliente=192.168.0.212:43120", output)
        self.assertIn("subprotocolos=ocpp1.6j", output)
        self.assertIn("auth=Basic", output)
        self.assertIn("Handshake WebSocket aceptado", output)
        self.assertNotIn("c2VjcmV0", output)
        self.assertNotIn("secret-password", output)
        self.assertEqual(sent, [{"type": "websocket.accept", "subprotocol": "ocpp1.6j"}])

    async def test_logs_handshake_rejection_reason(self):
        async def app(scope, receive, send):
            await send({
                "type": "websocket.close",
                "code": 1008,
                "reason": "Subprotocolo OCPP no soportado",
            })

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            return None

        middleware = OCPPWebSocketDiagnosticsMiddleware(app)
        with self.assertLogs("app.websocket.diagnostics", level="WARNING") as captured:
            await middleware(self.make_scope(), receive, send)

        self.assertIn("Handshake WebSocket rechazado", captured.output[1])
        self.assertIn("codigo=1008", captured.output[1])
        self.assertIn("Subprotocolo OCPP no soportado", captured.output[1])

    async def test_logs_exception_and_propagates_it(self):
        async def app(scope, receive, send):
            raise RuntimeError("test failure")

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            return None

        middleware = OCPPWebSocketDiagnosticsMiddleware(app)
        with (
            self.assertLogs("app.websocket.diagnostics", level="ERROR") as captured,
            self.assertRaisesRegex(RuntimeError, "test failure"),
        ):
            await middleware(self.make_scope(), receive, send)

        self.assertIn("Excepción ASGI durante WebSocket", captured.output[0])
        self.assertIn("RuntimeError: test failure", captured.output[0])
