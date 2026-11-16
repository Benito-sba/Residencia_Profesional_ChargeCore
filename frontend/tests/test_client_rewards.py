import asyncio
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pages import Client_Mis_Recompensas as rewards_page


class FakeElement:
    def __init__(self):
        self.handler = None
        self.disabled = False

    def classes(self, *_):
        return self

    def props(self, *_):
        return self

    def on_click(self, handler):
        self.handler = handler
        return self

    def disable(self):
        self.disabled = True
        return self

    def enable(self):
        self.disabled = False
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeUI:
    def __init__(self):
        self.labels = []
        self.buttons = []
        self.progress = []
        self.navigate = SimpleNamespace(reload=Mock())
        self.notify = Mock()

    def element(self, *_):
        return FakeElement()

    def row(self):
        return FakeElement()

    def column(self):
        return FakeElement()

    def card(self):
        return FakeElement()

    def label(self, value):
        self.labels.append(str(value))
        return FakeElement()

    def badge(self, value):
        self.labels.append(str(value))
        return FakeElement()

    def button(self, label, **kwargs):
        element = FakeElement()
        element.handler = kwargs.get("on_click")
        self.buttons.append((label, element))
        return element

    def linear_progress(self, value, **_kwargs):
        self.progress.append(value)
        return FakeElement()


def _company_rewards_response(points=0, requests=None):
    return {
        "puntos_disponibles": points,
        "puntos_reservados": 0,
        "puntos_por_carga": 10,
        "catalogo": [
            {
                "_id": "drink",
                "nombre": "Bebida gratis",
                "descripcion": "Una bebida sin costo",
                "condiciones": "Válido una vez",
                "puntos_requeridos": 100,
            },
            {
                "_id": "discount",
                "nombre": "Descuento 10%",
                "descripcion": "Descuento en tu próxima carga",
                "condiciones": "",
                "puntos_requeridos": 250,
            },
        ],
        "solicitudes": requests or [],
        "movimientos": [],
    }


class ClientRewardsPageTests(unittest.TestCase):
    def render(self, response):
        fake_ui = FakeUI()
        with (
            patch.object(rewards_page, "ui", fake_ui),
            patch.object(rewards_page, "render_client_shell", return_value=True),
            patch.object(rewards_page, "page_container", return_value=nullcontext()),
            patch.object(rewards_page, "render_page_heading"),
            patch.object(rewards_page, "api_get", return_value=response) as api_get,
        ):
            rewards_page.render_page("client-token", "http://api")
        return fake_ui, api_get

    def test_page_uses_company_rewards_and_renders_received_items(self):
        response = _company_rewards_response(
            points=0,
            requests=[
                {
                    "_id": "redemption-1",
                    "recompensa_id": "drink",
                    "estado": "aprobada",
                    "puntos_requeridos": 100,
                    "snapshot_recompensa": {
                        "nombre": "Bebida gratis",
                        "descripcion": "Una bebida sin costo",
                    },
                    "created_at": "2026-01-01T00:00:00",
                    "decided_at": "2026-01-02T00:00:00",
                },
                {
                    "_id": "redemption-2",
                    "recompensa_id": "discount",
                    "estado": "aprobada",
                    "puntos_requeridos": 250,
                    "snapshot_recompensa": {
                        "nombre": "Descuento 10%",
                        "descripcion": "Descuento en tu próxima carga",
                    },
                    "created_at": "2026-01-03T00:00:00",
                    "decided_at": "2026-01-04T00:00:00",
                },
            ],
        )
        fake_ui, api_get = self.render(response)

        api_get.assert_called_once_with(
            "/client/me/company-rewards",
            token="client-token",
            base_url="http://api",
        )
        self.assertIn("Mis recompensas recibidas", fake_ui.labels)
        self.assertIn("Bebida gratis", fake_ui.labels)
        self.assertIn("Descuento 10%", fake_ui.labels)
        self.assertIn("Tu próxima recompensa", fake_ui.labels)
        self.assertEqual(fake_ui.progress, [0])

    def test_requesting_a_reward_uses_the_company_redemption_endpoint(self):
        fake_ui = FakeUI()
        response = _company_rewards_response(points=100)

        async def run_io_bound(callback, *args, **kwargs):
            return callback(*args, **kwargs)

        with (
            patch.object(rewards_page, "ui", fake_ui),
            patch.object(rewards_page, "run") as run,
            patch.object(run, "io_bound", new=run_io_bound),
            patch.object(rewards_page, "render_client_shell", return_value=True),
            patch.object(rewards_page, "page_container", return_value=nullcontext()),
            patch.object(rewards_page, "render_page_heading"),
            patch.object(rewards_page, "api_get", return_value=response),
            patch.object(rewards_page, "api_post", return_value={}) as api_post,
        ):
            rewards_page.render_page("client-token", "http://api")
            button = next(
                element
                for label, element in fake_ui.buttons
                if label == "Solicitar canje"
            )
            asyncio.run(button.handler())

        api_post.assert_called_once_with(
            "/client/me/company-rewards/redemptions",
            json={"recompensa_id": "drink"},
            token="client-token",
            base_url="http://api",
        )
        self.assertFalse(button.disabled)
        fake_ui.navigate.reload.assert_called_once()


if __name__ == "__main__":
    unittest.main()
