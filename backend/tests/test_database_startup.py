import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

from app import database
from app import main
from fastapi import HTTPException
from pymongo.errors import PyMongoError


class FakeCollection:
    def __init__(self):
        self.created_indexes = []

    async def create_index(self, keys, **options):
        self.created_indexes.append((keys, options))

    async def index_information(self):
        raise AssertionError("Startup must not inspect indexes to remove one")

    async def drop_index(self, name):
        raise AssertionError(f"Startup must not drop index {name}")


class FakeDatabase:
    def __init__(self):
        self.collections = {}
        self.command = AsyncMock(return_value={"setName": "test-replica"})

    def __getattr__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class DatabaseStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_creates_expected_indexes_without_dropping_existing_ones(self):
        fake_database = FakeDatabase()
        previous_client = database.db.client

        try:
            with (
                patch.object(database, "AsyncIOMotorClient"),
                patch.object(database, "get_database", return_value=fake_database),
                redirect_stdout(io.StringIO()),
            ):
                await database.connect_db()
        finally:
            database.db.client = previous_client

        ocpp_indexes = fake_database.ocpp_messages.created_indexes
        self.assertIn(
            ([("charge_point_id", 1), ("unique_id", 1)], {}),
            ocpp_indexes,
        )
        self.assertEqual(len(ocpp_indexes), 2)
        active_session_indexes = fake_database.sesiones.created_indexes
        self.assertTrue(any(
            options.get("name") == "one_active_session_per_client"
            and options.get("unique") is True
            for _, options in active_session_indexes
        ))
        reward_indexes = fake_database.recompensas.created_indexes
        self.assertTrue(any(
            options.get("name") == "one_reward_per_user_rule_milestone"
            and options.get("unique") is True
            for _, options in reward_indexes
        ))

    async def test_production_startup_rejects_standalone_mongodb(self):
        fake_database = FakeDatabase()
        fake_database.command = AsyncMock(return_value={"isWritablePrimary": True})
        previous_client = database.db.client

        try:
            with (
                patch.object(database, "AsyncIOMotorClient"),
                patch.object(database, "get_database", return_value=fake_database),
                patch.object(database.settings, "DEBUG", False),
                self.assertRaisesRegex(RuntimeError, "replica set"),
            ):
                await database.connect_db()
        finally:
            database.db.client = previous_client

    async def test_lifespan_closes_database_client_when_startup_fails(self):
        with (
            patch.object(
                main,
                "connect_db",
                new_callable=AsyncMock,
                side_effect=RuntimeError("isolated startup failure"),
            ),
            patch.object(main, "close_db", new_callable=AsyncMock) as close_db,
        ):
            with self.assertRaisesRegex(RuntimeError, "isolated startup failure"):
                async with main.lifespan(main.app):
                    self.fail("Lifespan must not yield after startup failure")

        close_db.assert_awaited_once()

    async def test_health_endpoint_checks_database_connectivity(self):
        class Admin:
            command = AsyncMock(return_value={"ok": 1})

        class Client:
            admin = Admin()

        with patch.object(main.db, "client", Client()):
            self.assertEqual(
                await main.health_check(),
                {"status": "healthy"},
            )

    async def test_health_endpoint_reports_database_failure(self):
        class Admin:
            command = AsyncMock(side_effect=PyMongoError("database unavailable"))

        class Client:
            admin = Admin()

        with patch.object(main.db, "client", Client()):
            with self.assertRaises(HTTPException) as raised:
                await main.health_check()

        self.assertEqual(raised.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
