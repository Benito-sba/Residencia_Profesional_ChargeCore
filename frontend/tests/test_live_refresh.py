import unittest
from unittest.mock import AsyncMock, patch

from pages import Client_Carga_Activa, nicegui_admin


class LiveRefreshTests(unittest.IsolatedAsyncioTestCase):
    def test_charger_page_schedules_non_immediate_refresh(self):
        with patch.object(nicegui_admin.ui, "timer") as timer:
            nicegui_admin._schedule_charger_auto_refresh("cargadores")

        timer.assert_called_once()
        self.assertEqual(timer.call_args.args[0], 15)
        self.assertIs(
            timer.call_args.args[1],
            nicegui_admin._reload_chargers_when_idle,
        )
        self.assertFalse(timer.call_args.kwargs["immediate"])

    async def test_charger_refresh_does_not_reload_while_dialog_is_open(self):
        with (
            patch.object(
                nicegui_admin.ui,
                "run_javascript",
                new=AsyncMock(return_value=True),
            ),
            patch.object(nicegui_admin.ui.navigate, "reload") as reload_page,
        ):
            await nicegui_admin._reload_chargers_when_idle()
            reload_page.assert_not_called()

    async def test_charger_refresh_reloads_when_no_dialog_is_open(self):
        with (
            patch.object(
                nicegui_admin.ui,
                "run_javascript",
                new=AsyncMock(return_value=False),
            ),
            patch.object(nicegui_admin.ui.navigate, "reload") as reload_page,
        ):
            await nicegui_admin._reload_chargers_when_idle()
            reload_page.assert_called_once_with()

    def test_other_admin_pages_do_not_schedule_charger_refresh(self):
        with patch.object(nicegui_admin.ui, "timer") as timer:
            nicegui_admin._schedule_charger_auto_refresh("sesiones")

        timer.assert_not_called()

    def test_active_session_refresh_is_scheduled_every_15_seconds(self):
        with patch.object(Client_Carga_Activa.ui, "timer") as timer:
            Client_Carga_Activa._schedule_active_session_auto_refresh()

        timer.assert_called_once()
        self.assertEqual(timer.call_args.args[0], 15)
        self.assertEqual(
            timer.call_args.args[1],
            Client_Carga_Activa.ui.navigate.reload,
        )
        self.assertFalse(timer.call_args.kwargs["immediate"])

    def test_active_session_refresh_preserves_unsaved_reward_choice(self):
        storage = {}
        Client_Carga_Activa.remember_pending_reward_selection(
            storage,
            "session-1",
            "reward-2",
        )

        self.assertEqual(
            Client_Carga_Activa.pending_reward_selection(
                storage,
                "session-1",
                "reward-1",
            ),
            "reward-2",
        )

    def test_active_session_refresh_preserves_explicit_no_reward_choice(self):
        storage = {}
        Client_Carga_Activa.remember_pending_reward_selection(
            storage,
            "session-1",
            None,
        )

        self.assertIsNone(
            Client_Carga_Activa.pending_reward_selection(
                storage,
                "session-1",
                "reward-1",
            )
        )

    def test_saving_reward_clears_pending_selection(self):
        storage = {}
        Client_Carga_Activa.remember_pending_reward_selection(
            storage,
            "session-1",
            "reward-2",
        )
        Client_Carga_Activa.clear_pending_reward_selection(storage, "session-1")

        self.assertEqual(
            Client_Carga_Activa.pending_reward_selection(
                storage,
                "session-1",
                "reward-1",
            ),
            "reward-1",
        )


if __name__ == "__main__":
    unittest.main()
