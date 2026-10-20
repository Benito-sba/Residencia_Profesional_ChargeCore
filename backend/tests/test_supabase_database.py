import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from app import supabase_database
from app.config import settings


class SupabaseConnectionTests(unittest.TestCase):
    def setUp(self):
        self.previous_client = supabase_database._client
        supabase_database._client = None

    def tearDown(self):
        supabase_database._client = self.previous_client

    def test_unconfigured_supabase_keeps_existing_backend_available(self):
        with (
            patch.object(settings, "SUPABASE_URL", None),
            patch.object(settings, "SUPABASE_SECRET_KEY", None),
        ):
            supabase_database.connect_supabase()

        with self.assertRaisesRegex(RuntimeError, "no está conectado"):
            supabase_database.get_supabase_client()

    def test_partial_configuration_fails_explicitly(self):
        with (
            patch.object(settings, "SUPABASE_URL", "https://project.supabase.co"),
            patch.object(settings, "SUPABASE_SECRET_KEY", None),
        ):
            with self.assertRaisesRegex(RuntimeError, "deben configurarse juntos"):
                supabase_database.connect_supabase()

    def test_configured_connection_checks_schema_and_returns_client(self):
        client = MagicMock()
        client.table.return_value.select.return_value.limit.return_value.execute.return_value = (
            SimpleNamespace(data=[])
        )
        sdk = SimpleNamespace(create_client=MagicMock(return_value=client))

        with (
            patch.object(settings, "SUPABASE_URL", "https://project.supabase.co"),
            patch.object(
                settings,
                "SUPABASE_SECRET_KEY",
                SecretStr("service-role-secret"),
            ),
            patch.dict(sys.modules, {"supabase": sdk}),
        ):
            supabase_database.connect_supabase()

        client.table.assert_called_once_with("cargadores")
        self.assertIs(supabase_database.get_supabase_client(), client)

    def test_schema_check_failure_is_not_hidden_or_cached(self):
        client = MagicMock()
        client.table.return_value.select.return_value.limit.return_value.execute.side_effect = (
            RuntimeError("schema check failed")
        )
        sdk = SimpleNamespace(create_client=MagicMock(return_value=client))

        with (
            patch.object(settings, "SUPABASE_URL", "https://project.supabase.co"),
            patch.object(
                settings,
                "SUPABASE_SECRET_KEY",
                SecretStr("service-role-secret"),
            ),
            patch.dict(sys.modules, {"supabase": sdk}),
        ):
            with self.assertRaisesRegex(RuntimeError, "schema check failed"):
                supabase_database.connect_supabase()

        with self.assertRaisesRegex(RuntimeError, "no está conectado"):
            supabase_database.get_supabase_client()


if __name__ == "__main__":
    unittest.main()
