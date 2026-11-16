import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from pages import Company_Rewards as rewards_page


class FakeElement:
    def classes(self, *_):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def clear(self):
        return None

    def on_value_change(self, *_):
        return self

    def props(self, *_):
        return self

    def set_visibility(self, *_):
        return self


class FakeUI:
    def __init__(self):
        self.tab_labels = []
        self.panel_values = []

    def tabs(self):
        return FakeElement()

    def tab(self, name, **kwargs):
        self.tab_labels.append((name, kwargs.get("label")))
        return FakeElement()

    def tab_panels(self, _tabs, value=None):
        self.panel_values.append(value)
        return FakeElement()

    def tab_panel(self, *_args, **_kwargs):
        return FakeElement()

    def row(self):
        return FakeElement()

    def column(self):
        return FakeElement()

    def element(self, *_args, **_kwargs):
        return FakeElement()

    def button(self, *_args, **_kwargs):
        return FakeElement()

    def label(self, *_args, **_kwargs):
        return FakeElement()

    def notify(self, *_args, **_kwargs):
        return None


class SharedRewardsInterfaceTests(unittest.TestCase):
    def render_for_role(self, role):
        fake_ui = FakeUI()
        patches = (
            patch.object(rewards_page, "ui", fake_ui),
            patch.object(
                rewards_page,
                "app",
                SimpleNamespace(storage=SimpleNamespace(user={
                    "user": {"rol": role},
                })),
            ),
            patch.object(rewards_page, "add_nicegui_styles"),
            patch.object(rewards_page, "render_sidebar"),
            patch.object(rewards_page, "render_header"),
            patch.object(rewards_page, "page_container", return_value=nullcontext()),
            patch.object(rewards_page, "render_page_heading"),
        )
        return fake_ui, patches

    def test_superadmin_uses_company_benefits_and_keeps_load_rewards(self):
        _, patches = self.render_for_role("superadmin")
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patch("pages.ChargeCore_Benefits._render_admin") as render_benefits,
            patch("pages.nicegui_admin._render_rewards_page") as render_load_rewards,
        ):
            rewards_page.render_page("recompensas", "token", "http://api")

        render_benefits.assert_called_once_with("token", "http://api")
        render_load_rewards.assert_called_once()

    def test_company_owner_uses_client_rewards_and_received_benefits(self):
        fake_ui, patches = self.render_for_role("dueno_empresa")
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patch.object(
                rewards_page,
                "_render_owner_client_rewards",
            ) as render_client_rewards,
            patch("pages.ChargeCore_Benefits._render_owner") as render_benefits,
        ):
            rewards_page.render_page("recompensas", "token", "http://api")

        render_client_rewards.assert_called_once_with("token", "http://api")
        render_benefits.assert_called_once_with("token", "http://api")
        self.assertEqual(
            fake_ui.tab_labels,
            [
                ("assigned-benefits", "Beneficios recibidos"),
                ("client-rewards", "Recompensas para mis clientes"),
            ],
        )
        self.assertEqual(fake_ui.panel_values, ["assigned-benefits"])

    def test_owner_rewards_load_only_company_reward_data_and_render_shared_style(self):
        fake_ui = FakeUI()
        responses = {
            "/company-rewards/catalog": [],
            "/company-rewards/rules": None,
            "/company-rewards/balances": [],
            "/company-rewards/ledger": [],
            "/company-rewards/redemptions": [],
            "/usuarios/": [],
        }
        with (
            patch.object(rewards_page, "ui", fake_ui),
            patch.object(
                rewards_page,
                "api_get",
                side_effect=lambda endpoint, **_kwargs: responses[endpoint],
            ) as get_data,
            patch(
                "pages.nicegui_admin._render_reward_styles"
            ) as render_styles,
            patch(
                "pages.nicegui_admin._render_summary_cards"
            ) as render_summary,
            patch.object(rewards_page, "render_page_heading"),
        ):
            rewards_page._render_owner_client_rewards("owner-token", "http://api")

        self.assertEqual(
            [call.args[0] for call in get_data.call_args_list],
            list(responses),
        )
        render_styles.assert_called_once()
        summary_cards = render_summary.call_args.args[0]
        self.assertEqual(len(summary_cards), 4)


if __name__ == "__main__":
    unittest.main()
