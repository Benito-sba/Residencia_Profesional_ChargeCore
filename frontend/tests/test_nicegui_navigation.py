import unittest
from pathlib import Path
from unittest.mock import patch

import nicegui_app
from components.navigation import (
    NAVIGATION_GROUPS,
    PAGE_MODULES,
    ROLE_HOME_ROUTES,
    ROLE_PAGE_KEYS,
)


class NiceGuiNavigationTests(unittest.TestCase):
    def test_only_current_roles_have_navigation(self):
        self.assertEqual(
            set(ROLE_PAGE_KEYS),
            {"superadmin", "dueno_empresa", "cliente"},
        )
        self.assertEqual(
            ROLE_HOME_ROUTES,
            {
                "superadmin": "/pages/dashboard",
                "dueno_empresa": "/pages/dashboard",
                "cliente": "/portal-cliente",
            },
        )

    def test_superadmin_can_open_global_administration_pages(self):
        with (
            patch.object(
                nicegui_app,
                "_session",
                return_value={"token": "token", "user": {"rol": "superadmin"}},
            ),
            patch.object(nicegui_app.ui.navigate, "to") as navigate,
        ):
            self.assertTrue(nicegui_app._require_page("empresas"))
            self.assertTrue(nicegui_app._require_page("operaciones_qr"))
            self.assertTrue(nicegui_app._require_page("configuracion"))
            navigate.assert_not_called()

    def test_company_owner_can_open_company_pages_but_not_global_settings(self):
        with (
            patch.object(
                nicegui_app,
                "_session",
                return_value={"token": "token", "user": {"rol": "dueno_empresa"}},
            ),
            patch.object(nicegui_app.ui.navigate, "to") as navigate,
        ):
            self.assertTrue(nicegui_app._require_page("empresa"))
            self.assertTrue(nicegui_app._require_page("operaciones_qr"))
            self.assertFalse(nicegui_app._require_page("empresas"))
            navigate.assert_called_once_with("/pages/dashboard")

    def test_client_and_legacy_roles_cannot_open_admin_pages(self):
        client_session = {
            "token": "token",
            "user": {"rol": "cliente"},
        }
        with (
            patch.object(nicegui_app, "_session", return_value=client_session),
            patch.object(nicegui_app.ui.navigate, "to") as navigate,
        ):
            self.assertFalse(nicegui_app._require_page("dashboard"))
            navigate.assert_called_once_with("/portal-cliente")

        with (
            patch.object(
                nicegui_app,
                "_session",
                return_value={"token": "legacy", "user": {"rol": "admin"}},
            ),
            patch.object(nicegui_app.ui.navigate, "to") as navigate,
        ):
            self.assertFalse(nicegui_app._require_page("dashboard"))
            navigate.assert_called_once_with("/")

    def test_phase4_pages_are_limited_to_their_roles(self):
        sessions = {
            "superadmin": {
                "token": "token",
                "user": {"rol": "superadmin"},
            },
            "dueno_empresa": {
                "token": "token",
                "user": {"rol": "dueno_empresa"},
            },
            "cliente": {
                "token": "token",
                "user": {"rol": "cliente"},
            },
        }
        cases = (
            ("superadmin", "recompensas", True),
            ("superadmin", "beneficios_chargecore", True),
            ("superadmin", "mis_beneficios_chargecore", False),
            ("superadmin", "recompensas_empresa", False),
            ("dueno_empresa", "recompensas", True),
            ("dueno_empresa", "beneficios_chargecore", False),
            ("dueno_empresa", "mis_beneficios_chargecore", True),
            ("dueno_empresa", "recompensas_empresa", True),
            ("dueno_empresa", "mis_recompensas_empresa", False),
            ("cliente", "mis_recompensas_empresa", False),
            ("cliente", "recompensas", False),
        )
        for role, page_key, expected in cases:
            with (
                self.subTest(role=role, page_key=page_key),
                patch.object(nicegui_app, "_session", return_value=sessions[role]),
                patch.object(nicegui_app.ui.navigate, "to"),
            ):
                self.assertEqual(nicegui_app._require_page(page_key), expected)

    def test_client_navigation_has_only_one_rewards_entry(self):
        client_items = next(
            items for group, items in NAVIGATION_GROUPS if group == "Cliente"
        )
        reward_items = [
            item for item in client_items if "recompensa" in item[0]
        ]
        self.assertEqual(
            reward_items,
            [("mis_recompensas", "🎁", "Mis Recompensas")],
        )
        self.assertNotIn("mis_recompensas_empresa", ROLE_PAGE_KEYS["cliente"])

    def test_admin_and_owner_share_the_rewards_page_module(self):
        for page_key in (
            "recompensas",
            "beneficios_chargecore",
            "mis_beneficios_chargecore",
            "recompensas_empresa",
        ):
            self.assertEqual(PAGE_MODULES[page_key], "Company_Rewards")
        self.assertIn("recompensas", ROLE_PAGE_KEYS["superadmin"])
        self.assertIn("recompensas", ROLE_PAGE_KEYS["dueno_empresa"])

    def test_run_uses_the_custom_charger_favicon(self):
        with patch.object(nicegui_app.ui, "run") as ui_run:
            nicegui_app.run()

        favicon = ui_run.call_args.kwargs["favicon"]
        self.assertIsInstance(favicon, Path)
        self.assertTrue(favicon.is_file())
        self.assertEqual(favicon.name, "chargecore_favicon.svg")


if __name__ == "__main__":
    unittest.main()
