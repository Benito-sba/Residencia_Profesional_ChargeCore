import unittest
from unittest.mock import patch

from utils.nicegui_auth import request_password_reset


class PasswordResetUiTests(unittest.TestCase):
    @patch("utils.nicegui_auth.api_post")
    def test_blank_email_does_not_call_api(self, api_post):
        message, severity = request_password_reset("  ", "http://localhost:8000/api")

        self.assertEqual(message, "Ingresa tu correo electrónico.")
        self.assertEqual(severity, "negative")
        api_post.assert_not_called()

    @patch("utils.nicegui_auth.api_post")
    def test_success_shows_generic_confirmation(self, api_post):
        api_post.return_value = {"mensaje": "Solicitud recibida."}

        message, severity = request_password_reset(
            " usuario@example.com ",
            "http://localhost:8000/api",
        )

        self.assertEqual(message, "Solicitud recibida.")
        self.assertEqual(severity, "positive")
        api_post.assert_called_once_with(
            "/auth/password-reset-request",
            json={"email": "usuario@example.com"},
            base_url="http://localhost:8000/api",
        )

    @patch("utils.nicegui_auth.api_post")
    def test_api_error_is_shown(self, api_post):
        api_post.return_value = {"error": "Servicio no disponible."}

        message, severity = request_password_reset(
            "usuario@example.com",
            "http://localhost:8000/api",
        )

        self.assertEqual(message, "Servicio no disponible.")
        self.assertEqual(severity, "negative")

    @patch("utils.nicegui_auth.api_post")
    def test_unexpected_api_shape_is_reported(self, api_post):
        api_post.return_value = []

        message, severity = request_password_reset(
            "usuario@example.com",
            "http://localhost:8000/api",
        )

        self.assertEqual(
            message,
            "El servidor devolvió una respuesta inesperada.",
        )
        self.assertEqual(severity, "negative")


if __name__ == "__main__":
    unittest.main()
