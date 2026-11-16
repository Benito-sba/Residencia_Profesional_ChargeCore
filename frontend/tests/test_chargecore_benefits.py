import asyncio
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pages import ChargeCore_Benefits as benefits_page


class FakeElement:
    def __init__(self):
        self.disabled = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def classes(self, *_):
        return self

    def props(self, *_):
        return self

    def open(self):
        return self

    def close(self):
        return self

    def disable(self):
        self.disabled = True
        return self

    def enable(self):
        self.disabled = False
        return self


class FakeInput(FakeElement):
    def __init__(self, value=None):
        self.value = value


class FakeUI:
    def __init__(self):
        self.buttons = []
        self.inputs = {}
        self.selects = []
        self.navigate = SimpleNamespace(reload=Mock())
        self.notify = Mock()

    def button(self, label, **kwargs):
        element = FakeElement()
        self.buttons.append({"label": label, "element": element, **kwargs})
        return element

    def dialog(self):
        return FakeElement()

    def row(self):
        return FakeElement()

    def column(self):
        return FakeElement()

    def card(self):
        return FakeElement()

    def expansion(self, *_):
        return FakeElement()

    def label(self, *_):
        return FakeElement()

    def input(self, label, **kwargs):
        element = FakeInput(kwargs.get("value"))
        self.inputs[label] = element
        return element

    def select(self, _options, **kwargs):
        element = FakeInput(kwargs.get("value"))
        self.selects.append(element)
        return element

    def textarea(self, _label, **kwargs):
        return FakeInput(kwargs.get("value"))

    def number(self, _label, **kwargs):
        return FakeInput(kwargs.get("value"))

    def badge(self, *_):
        return FakeElement()


class ChargeCoreBenefitsPageTests(unittest.TestCase):
    def test_assignment_runs_without_blocking_the_ui_event_loop(self):
        fake_ui = FakeUI()

        async def run_io_bound(callback, *args, **kwargs):
            return callback(*args, **kwargs)

        with (
            patch.object(benefits_page, "ui", fake_ui),
            patch.object(
                benefits_page,
                "api_get",
                return_value=[{"_id": "company-a", "nombre": "Empresa A"}],
            ),
            patch.object(benefits_page, "api_post", return_value={}) as api_post,
            patch.object(benefits_page.run, "io_bound", new=run_io_bound),
        ):
            benefits_page._assign_dialog(
                "token",
                "http://api",
                {"_id": "benefit-a", "nombre": "Beneficio A"},
            )
            fake_ui.selects[0].value = ["company-a"]
            assign_button = next(
                button for button in fake_ui.buttons if button["label"] == "Asignar"
            )
            asyncio.run(assign_button["on_click"]())

        self.assertFalse(assign_button["element"].disabled)
        api_post.assert_called_once()
        self.assertEqual(
            api_post.call_args.args,
            ("/commercial-benefits/catalog/benefit-a/assign",),
        )
        self.assertEqual(
            api_post.call_args.kwargs["json"]["empresa_ids"],
            ["company-a"],
        )

    def test_each_benefit_card_renders_bound_actions_without_history(self):
        catalog = [
            {"_id": "benefit-a", "nombre": "Beneficio A", "categoria": "creditos_descuentos",
             "tipo": "credito", "activa": True},
            {"_id": "benefit-b", "nombre": "Beneficio B", "categoria": "crecimiento_permanencia",
             "tipo": "dias_servicio", "activa": False},
        ]
        fake_ui = FakeUI()
        with (
            patch.object(benefits_page, "ui", fake_ui),
            patch.object(benefits_page, "page_container", return_value=nullcontext()),
            patch.object(
                benefits_page,
                "api_get",
                side_effect=[catalog, [], []],
            ),
            patch.object(benefits_page, "_assign_dialog") as assign,
            patch.object(benefits_page, "_set_benefit_state") as set_state,
        ):
            benefits_page._render_admin("token", "http://api")
            assign_buttons = [
                button for button in fake_ui.buttons if button["label"] == "Asignar"
            ]
            state_buttons = [
                button
                for button in fake_ui.buttons
                if button["label"] in {"Activar", "Desactivar"}
            ]
            self.assertEqual(len(assign_buttons), 2)
            self.assertEqual(
                [button["label"] for button in state_buttons],
                ["Desactivar", "Activar"],
            )
            for button in assign_buttons + state_buttons:
                button["on_click"]()

        self.assertEqual(
            [call.args[2]["_id"] for call in assign.call_args_list],
            ["benefit-a", "benefit-b"],
        )
        self.assertEqual(
            [call.args[2]["_id"] for call in set_state.call_args_list],
            ["benefit-a", "benefit-b"],
        )

    def test_editing_can_clear_end_date_and_sends_null_to_backend(self):
        item = {
            "_id": "benefit-a",
            "nombre": "Beneficio A",
            "categoria": "creditos_descuentos",
            "tipo": "credito",
            "descripcion": "Crédito de prueba",
            "condiciones": "",
            "requisitos": {},
            "condiciones_uso": {},
            "valor": 50,
            "fecha_inicio": "2026-01-01T00:00:00",
            "fecha_finalizacion": "2026-02-01T00:00:00",
            "max_assignments_per_company": 1,
        }
        fake_ui = FakeUI()
        with (
            patch.object(benefits_page, "ui", fake_ui),
            patch.object(benefits_page, "api_put", return_value={}) as update,
        ):
            benefits_page._benefit_editor("token", "http://api", item)
            fake_ui.inputs["Fin"].value = ""
            save = next(
                button for button in fake_ui.buttons
                if button["label"] == "Guardar"
            )
            save["on_click"]()

        update.assert_called_once()
        self.assertIsNone(update.call_args.kwargs["json"]["fecha_finalizacion"])


if __name__ == "__main__":
    unittest.main()
