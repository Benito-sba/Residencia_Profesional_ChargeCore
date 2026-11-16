import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import HTTPException

from app.models.client_portal import ClientStartChargeRequest
from app.routers import client_portal


class MemoryCollection:
    def __init__(self, document=None):
        self.document = document
        self.update_calls = []

    async def find_one(self, query):
        if self.document is None:
            return None
        for key, value in query.items():
            if self.document.get(key) != value:
                return None
        return self.document

    async def update_one(self, query, update):
        self.update_calls.append((query, update))
        if self.document is None:
            return SimpleNamespace(matched_count=0)
        for key, value in query.items():
            if key == "expires_at":
                continue
            if self.document.get(key) != value:
                return SimpleNamespace(matched_count=0)
        for key, value in update.get("$set", {}).items():
            self.document[key] = value
        return SimpleNamespace(matched_count=1)


class ClientPortalStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_connector_does_not_send_start_or_create_session(self):
        request_id = ObjectId()
        user_id = ObjectId()
        company_id = ObjectId()
        vehicle_id = ObjectId()
        request = MemoryCollection({
            "_id": request_id,
            "usuario_id": user_id,
            "empresa_id": company_id,
            "charge_point_id": "CP-1",
            "uid": "TAG-1",
            "status": "awaiting_vehicle",
            "expires_at": datetime.utcnow() + timedelta(minutes=2),
        })
        collections = {
            "nfc_charge_requests": request,
            "vehiculos": MemoryCollection({
                "_id": vehicle_id,
                "usuario_id": user_id,
                "empresa_id": company_id,
                "activo": True,
            }),
            "cargadores": MemoryCollection({
                "charge_point_id": "CP-1",
                "empresa_id": company_id,
                "activo": True,
                "conectores": [{"id_conector": 1, "estado": "Available"}],
            }),
            "sesiones": MemoryCollection(),
        }

        with (
            patch.object(
                client_portal,
                "get_collection",
                side_effect=collections.__getitem__,
            ),
            patch.object(
                client_portal.ocpp_command_service,
                "remote_start",
                new=AsyncMock(),
            ) as remote_start,
        ):
            with self.assertRaises(HTTPException) as raised:
                await client_portal.start_client_charge(
                    str(request_id),
                    ClientStartChargeRequest(
                        vehicle_id=str(vehicle_id),
                        connector_id=99,
                    ),
                    current_user={"_id": user_id, "empresa_id": company_id},
                )

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(request.document["status"], "awaiting_vehicle")
        self.assertIsNone(collections["sesiones"].document)
        remote_start.assert_not_awaited()

    async def test_valid_request_sends_start_command_to_charger(self):
        request_id = ObjectId()
        user_id = ObjectId()
        company_id = ObjectId()
        vehicle_id = ObjectId()
        request = MemoryCollection({
            "_id": request_id,
            "usuario_id": user_id,
            "empresa_id": company_id,
            "charge_point_id": "CP-1",
            "uid": "TAG-1",
            "status": "awaiting_vehicle",
            "expires_at": datetime.utcnow() + timedelta(minutes=2),
        })
        collections = {
            "nfc_charge_requests": request,
            "vehiculos": MemoryCollection({
                "_id": vehicle_id,
                "usuario_id": user_id,
                "empresa_id": company_id,
                "activo": True,
            }),
            "cargadores": MemoryCollection({
                "charge_point_id": "CP-1",
                "empresa_id": company_id,
                "activo": True,
                "conectores": [{"id_conector": 1, "estado": "Available"}],
            }),
            "sesiones": MemoryCollection(),
        }

        with (
            patch.object(
                client_portal,
                "get_collection",
                side_effect=collections.__getitem__,
            ),
            patch.object(
                client_portal.ocpp_command_service,
                "remote_start",
                new=AsyncMock(return_value={"status": "Accepted"}),
            ) as remote_start,
        ):
            result = await client_portal.start_client_charge(
                str(request_id),
                ClientStartChargeRequest(
                    vehicle_id=str(vehicle_id),
                    connector_id=1,
                ),
                current_user={"_id": user_id, "empresa_id": company_id},
            )

        self.assertEqual(
            result,
            {
                "mensaje": "Orden de inicio enviada al cargador",
                "status": "Accepted",
            },
        )
        self.assertEqual(request.document["status"], "start_requested")
        remote_start.assert_awaited_once_with(
            "CP-1",
            "TAG-1",
            1,
            request_id=request_id,
        )

    async def test_indeterminate_start_keeps_request_claimed_to_prevent_retry(self):
        request_id = ObjectId()
        user_id = ObjectId()
        company_id = ObjectId()
        vehicle_id = ObjectId()
        request = MemoryCollection({
            "_id": request_id,
            "usuario_id": user_id,
            "empresa_id": company_id,
            "charge_point_id": "CP-1",
            "uid": "TAG-1",
            "status": "awaiting_vehicle",
            "expires_at": datetime.utcnow() + timedelta(minutes=2),
        })
        collections = {
            "nfc_charge_requests": request,
            "vehiculos": MemoryCollection({
                "_id": vehicle_id,
                "usuario_id": user_id,
                "empresa_id": company_id,
                "activo": True,
            }),
            "cargadores": MemoryCollection({
                "charge_point_id": "CP-1",
                "empresa_id": company_id,
                "activo": True,
                "conectores": [{"id_conector": 1, "estado": "Available"}],
            }),
            "sesiones": MemoryCollection(),
        }
        timeout = HTTPException(
            status_code=504,
            detail={"outcome": "indeterminate"},
        )

        with (
            patch.object(
                client_portal,
                "get_collection",
                side_effect=collections.__getitem__,
            ),
            patch.object(
                client_portal.ocpp_command_service,
                "remote_start",
                new=AsyncMock(side_effect=timeout),
            ) as remote_start,
        ):
            with self.assertRaises(HTTPException):
                await client_portal.start_client_charge(
                    str(request_id),
                    ClientStartChargeRequest(
                        vehicle_id=str(vehicle_id),
                        connector_id=1,
                    ),
                    current_user={"_id": user_id, "empresa_id": company_id},
                )

        self.assertEqual(request.document["status"], "start_requested")
        self.assertEqual(len(request.update_calls), 1)
        remote_start.assert_awaited_once_with(
            "CP-1",
            "TAG-1",
            1,
            request_id=request_id,
        )

        with patch.object(
            client_portal,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            with self.assertRaises(HTTPException) as raised:
                await client_portal.start_client_charge(
                    str(request_id),
                    ClientStartChargeRequest(
                        vehicle_id=str(vehicle_id),
                        connector_id=1,
                    ),
                    current_user={"_id": user_id, "empresa_id": company_id},
                )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(len(request.update_calls), 1)


if __name__ == "__main__":
    unittest.main()
