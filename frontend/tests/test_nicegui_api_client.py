import unittest
from unittest.mock import MagicMock, patch

import requests

from utils.api_client import (
    OCPP_COMMAND_UNCERTAIN_ERROR,
    OCPP_COMMAND_TIMEOUT_SECONDS,
    api_get,
    api_post,
    api_put,
    api_delete,
)


class NiceGuiApiClientTests(unittest.TestCase):
    def setUp(self):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True}
        self.response = response

    @patch("utils.api_client.requests.get")
    def test_get_accepts_nicegui_session_context(self, request):
        request.return_value = self.response

        result = api_get(
            "/client/me",
            token="access-token",
            base_url="https://api.example.test/api/",
        )

        self.assertEqual(result, {"ok": True})
        request.assert_called_once_with(
            "https://api.example.test/api/client/me",
            headers={"Authorization": "Bearer access-token"},
            params=None,
            timeout=10,
        )

    @patch("utils.api_client.requests.post")
    def test_post_accepts_nicegui_session_context(self, request):
        request.return_value = self.response

        result = api_post(
            "/client/me/vehicles",
            json={"marca": "ChargeCore"},
            token="access-token",
            base_url="https://api.example.test/api",
        )

        self.assertEqual(result, {"ok": True})
        request.assert_called_once_with(
            "https://api.example.test/api/client/me/vehicles",
            headers={"Authorization": "Bearer access-token"},
            data=None,
            json={"marca": "ChargeCore"},
            timeout=10,
        )

    @patch("utils.api_client.requests.post")
    def test_post_supports_an_explicit_command_timeout(self, request):
        request.return_value = self.response

        result = api_post(
            "/cargadores/charger-1/remote-start",
            json={"idTag": "card-1"},
            token="access-token",
            base_url="https://api.example.test/api",
            timeout=OCPP_COMMAND_TIMEOUT_SECONDS,
        )

        self.assertEqual(result, {"ok": True})
        request.assert_called_once()
        args, kwargs = request.call_args
        self.assertEqual(
            args[0],
            "https://api.example.test/api/cargadores/charger-1/remote-start",
        )
        self.assertEqual(kwargs["json"], {"idTag": "card-1"})
        self.assertEqual(kwargs["timeout"], 40)

    @patch("utils.api_client.requests.post")
    def test_ocpp_timeout_reports_uncertain_result_without_retry(self, request):
        request.side_effect = requests.Timeout

        result = api_post(
            "/cargadores/charger-1/remote-start",
            json={"idTag": "card-1"},
            timeout=OCPP_COMMAND_TIMEOUT_SECONDS,
        )

        self.assertEqual(result, {"error": OCPP_COMMAND_UNCERTAIN_ERROR})
        request.assert_called_once()

    @patch("utils.api_client.requests.post")
    def test_ocpp_http_504_reports_uncertain_result(self, request):
        self.response.status_code = 504
        request.return_value = self.response

        result = api_post(
            "/cargadores/charger-1/reset",
            json={"type": "Soft"},
            timeout=OCPP_COMMAND_TIMEOUT_SECONDS,
        )

        self.assertEqual(
            result,
            {
                "error": OCPP_COMMAND_UNCERTAIN_ERROR,
                "_status_code": 504,
            },
        )
        request.assert_called_once()

    @patch("utils.api_client.requests.put")
    def test_put_accepts_nicegui_session_context(self, request):
        request.return_value = self.response

        result = api_put(
            "/client/me",
            json={"nombre": "Cliente"},
            token="access-token",
            base_url="https://api.example.test/api",
        )

        self.assertEqual(result, {"ok": True})
        request.assert_called_once_with(
            "https://api.example.test/api/client/me",
            headers={"Authorization": "Bearer access-token"},
            json={"nombre": "Cliente"},
            timeout=10,
        )

    @patch("utils.api_client.requests.delete")
    def test_delete_accepts_nicegui_session_context(self, request):
        request.return_value = self.response

        result = api_delete(
            "/client/me/vehicles/vehicle-1",
            token="access-token",
            base_url="https://api.example.test/api",
        )

        self.assertEqual(result, {"ok": True})
        request.assert_called_once_with(
            "https://api.example.test/api/client/me/vehicles/vehicle-1",
            headers={"Authorization": "Bearer access-token"},
            timeout=10,
        )

    @patch("utils.api_client.requests.post")
    def test_login_accepts_configured_api_base_url(self, request):
        self.response.json.return_value = {
            "access_token": "access-token",
            "user": {"rol": "cliente"},
        }
        request.return_value = self.response

        from utils.api_client import api_login

        result = api_login(
            "cliente@example.test",
            "password",
            base_url="https://api.example.test/api/",
        )

        self.assertEqual(result["access_token"], "access-token")
        request.assert_called_once_with(
            "https://api.example.test/api/auth/login",
            data={
                "username": "cliente@example.test",
                "password": "password",
            },
            timeout=10,
        )


if __name__ == "__main__":
    unittest.main()
