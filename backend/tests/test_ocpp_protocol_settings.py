import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import require_superadmin
from app.routers import ocpp_settings
from app.services import ocpp_protocol_settings as protocol_settings


class MemorySettingsCollection:
    def __init__(self):
        self.document = None

    async def update_one(self, query, update, upsert=False):
        if self.document is None:
            if not upsert:
                return type("UpdateResult", (), {"matched_count": 0})()
            self.document = deepcopy(query)
            self.document.update(deepcopy(update.get("$setOnInsert", {})))
        if any(self.document.get(key) != value for key, value in query.items()):
            return type("UpdateResult", (), {"matched_count": 0})()
        self.document.update(deepcopy(update.get("$set", {})))
        for key, value in update.get("$inc", {}).items():
            self.document[key] = self.document.get(key, 0) + value
        return type("UpdateResult", (), {"matched_count": 1})()

    async def find_one(self, query):
        if self.document is not None and all(
            self.document.get(key) == value for key, value in query.items()
        ):
            return deepcopy(self.document)
        return None


class OCPPProtocolSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_defaults_are_initialized_and_persisted(self):
        collection = MemorySettingsCollection()
        with patch.object(
            protocol_settings,
            "get_collection",
            return_value=collection,
        ):
            configuration = await protocol_settings.get_ocpp_protocol_settings()

        self.assertTrue(configuration.automatic_detection)
        self.assertEqual(
            configuration.enabled_protocols,
            ["ocpp1.6", "ocpp2.0.1", "ocpp2.1"],
        )
        self.assertEqual(configuration.preferred_protocol, "ocpp1.6")
        self.assertEqual(collection.document["revision"], 0)
        self.assertEqual(
            collection.document["_id"],
            protocol_settings.OCPP_PROTOCOL_SETTINGS_ID,
        )

    async def test_settings_are_saved_with_revision_and_stale_writes_conflict(self):
        collection = MemorySettingsCollection()
        with patch.object(
            protocol_settings,
            "get_collection",
            return_value=collection,
        ):
            original = await protocol_settings.get_ocpp_protocol_settings()
            updated = original.model_copy(
                update={
                    "enabled_protocols": ["ocpp1.6", "ocpp2.1"],
                    "preferred_protocol": "ocpp1.6",
                }
            )
            saved = await protocol_settings.save_ocpp_protocol_settings(updated)
            self.assertEqual(saved.revision, 1)
            self.assertEqual(
                saved.enabled_protocols,
                ["ocpp1.6", "ocpp2.1"],
            )

            with self.assertRaises(
                protocol_settings.OCPPProtocolSettingsConflict
            ):
                await protocol_settings.save_ocpp_protocol_settings(original)

    async def test_connection_counts_cover_standard_and_legacy_16j_names(self):
        class ChargerCollection:
            def __init__(self):
                self.queries = []

            async def count_documents(self, query):
                self.queries.append(query)
                return len(self.queries)

        collection = ChargerCollection()
        with patch.object(
            protocol_settings,
            "get_collection",
            return_value=collection,
        ):
            counts = await protocol_settings.count_ocpp_connections_by_version()

        self.assertEqual(counts, {"ocpp1.6": 1, "ocpp2.0.1": 2, "ocpp2.1": 3})
        self.assertEqual(
            collection.queries[0]["subprotocolo_ocpp"]["$in"],
            ("ocpp1.6", "ocpp1.6j"),
        )
        self.assertTrue(all(query["conectado"] for query in collection.queries))

    def test_validation_rejects_empty_or_inconsistent_protocol_settings(self):
        with self.assertRaises(ValidationError):
            protocol_settings.OCPPProtocolSettings(enabled_protocols=[])
        with self.assertRaises(ValidationError):
            protocol_settings.OCPPProtocolSettings(
                automatic_detection=False,
                enabled_protocols=["ocpp1.6"],
                preferred_protocol="ocpp2.0.1",
            )
        with self.assertRaises(ValidationError):
            protocol_settings.OCPPProtocolSettings(
                automatic_detection=False,
                enabled_protocols=["ocpp1.6"],
                preferred_protocol=None,
            )


class OCPPProtocolSettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(ocpp_settings.router)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()

    def test_settings_routes_reject_non_admin_users(self):
        def reject_non_admin():
            raise HTTPException(status_code=403, detail="Permisos insuficientes")

        self.app.dependency_overrides[require_superadmin] = reject_non_admin
        response = self.client.get("/api/ocpp-settings/protocols")
        self.assertEqual(response.status_code, 403)

        response = self.client.put(
            "/api/ocpp-settings/protocols",
            json={
                "automatic_detection": True,
                "enabled_protocols": ["ocpp1.6"],
                "preferred_protocol": "ocpp1.6",
                "revision": 0,
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_api_rejects_empty_enabled_protocols_as_validation_error(self):
        self.app.dependency_overrides[require_superadmin] = lambda: {
            "rol": "superadmin"
        }
        response = self.client.put(
            "/api/ocpp-settings/protocols",
            json={
                "automatic_detection": True,
                "enabled_protocols": [],
                "preferred_protocol": None,
                "revision": 0,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_admin_can_read_and_update_protocol_settings(self):
        self.app.dependency_overrides[require_superadmin] = lambda: {
            "rol": "superadmin"
        }
        configuration = protocol_settings.OCPPProtocolSettings()
        with (
            patch.object(
                ocpp_settings,
                "get_ocpp_protocol_settings",
                new=AsyncMock(return_value=configuration),
            ),
            patch.object(
                ocpp_settings,
                "count_ocpp_connections_by_version",
                new=AsyncMock(
                    return_value={"ocpp1.6": 1, "ocpp2.0.1": 0, "ocpp2.1": 0}
                ),
            ),
            patch.object(
                ocpp_settings,
                "save_ocpp_protocol_settings",
                new=AsyncMock(return_value=configuration),
            ) as save_settings,
        ):
            read_response = self.client.get("/api/ocpp-settings/protocols")
            self.assertEqual(read_response.status_code, 200)
            self.assertEqual(
                read_response.json()["connections_by_version"]["ocpp1.6"],
                1,
            )

            update_response = self.client.put(
                "/api/ocpp-settings/protocols",
                json={
                    "automatic_detection": True,
                    "enabled_protocols": ["ocpp1.6"],
                    "preferred_protocol": "ocpp1.6",
                    "revision": 0,
                },
            )

        self.assertEqual(update_response.status_code, 200)
        save_settings.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
