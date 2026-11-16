import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import HTTPException

from app.models.client_portal import ClientQRChargeRequest, ClientStartChargeRequest
from app.routers import client_portal


class MemoryCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])

    @staticmethod
    def _matches(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$gt" in expected and not actual > expected["$gt"]:
                    return False
            elif actual != expected:
                return False
        return True

    async def find_one(self, query):
        return next(
            (
                document
                for document in self.documents
                if self._matches(document, query)
            ),
            None,
        )

    async def insert_one(self, document):
        self.documents.append(document)
        return SimpleNamespace(inserted_id=document["_id"])

    async def update_one(self, query, update):
        document = await self.find_one(query)
        if document is None:
            return SimpleNamespace(matched_count=0)
        document.update(update.get("$set", {}))
        for key in update.get("$unset", {}):
            document.pop(key, None)
        return SimpleNamespace(matched_count=1)


class ClientPortalQRStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_qr_request_reuses_assigned_card_and_existing_start_flow(self):
        user_id = ObjectId()
        company_id = ObjectId()
        vehicle_id = ObjectId()
        card = {
            "_id": ObjectId(),
            "usuario_id": user_id,
            "empresa_id": company_id,
            "uid": "CLIENT-TAG-1",
            "estado": "activa",
        }
        collections = {
            "sesiones": MemoryCollection(),
            "nfc_cards": MemoryCollection([card]),
            "cargadores": MemoryCollection([{
                "charge_point_id": "CP-1",
                "nombre": "Cargador principal",
                "empresa_id": company_id,
                "activo": True,
                "conectores": [
                    {
                        "id_conector": 1,
                        "tipo": "Type2",
                        "estado": "Available",
                    },
                ],
            }]),
            "nfc_charge_requests": MemoryCollection(),
            "vehiculos": MemoryCollection([{
                "_id": vehicle_id,
                "usuario_id": user_id,
                "empresa_id": company_id,
                "activo": True,
            }]),
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
            request = await client_portal.create_client_qr_charge_request(
                ClientQRChargeRequest(charge_point_id=" CP-1 "),
                current_user={"_id": user_id, "empresa_id": company_id},
            )
            start_result = await client_portal.start_client_charge(
                request["id"],
                ClientStartChargeRequest(
                    vehicle_id=str(vehicle_id),
                    connector_id=1,
                ),
                current_user={"_id": user_id, "empresa_id": company_id},
            )

        self.assertEqual(request["charge_point_id"], "CP-1")
        self.assertEqual(request["cargador"], "Cargador principal")
        self.assertEqual(request["status"], "awaiting_vehicle")
        self.assertEqual(request["conectores"][0]["id"], 1)
        self.assertEqual(
            start_result["mensaje"],
            "Orden de inicio enviada al cargador",
        )
        self.assertEqual(
            collections["nfc_charge_requests"].documents[0]["source"],
            "qr",
        )
        self.assertEqual(
            collections["nfc_charge_requests"].documents[0]["empresa_id"],
            company_id,
        )
        remote_start.assert_awaited_once_with(
            "CP-1",
            "CLIENT-TAG-1",
            1,
            request_id=collections["nfc_charge_requests"].documents[0]["_id"],
        )

    async def test_rejected_qr_start_keeps_a_visible_rejection_result(self):
        request_id = ObjectId()
        user_id = ObjectId()
        company_id = ObjectId()
        vehicle_id = ObjectId()
        request = {
            "_id": request_id,
            "empresa_id": company_id,
            "usuario_id": user_id,
            "charge_point_id": "CP-1",
            "uid": "CLIENT-TAG-1",
            "source": "qr",
            "status": "awaiting_vehicle",
            "expires_at": datetime.utcnow() + timedelta(minutes=2),
        }
        collections = {
            "nfc_charge_requests": MemoryCollection([request]),
            "vehiculos": MemoryCollection([{
                "_id": vehicle_id,
                "usuario_id": user_id,
                "empresa_id": company_id,
                "activo": True,
            }]),
            "cargadores": MemoryCollection([{
                "charge_point_id": "CP-1",
                "empresa_id": company_id,
                "activo": True,
                "conectores": [{"id_conector": 1, "estado": "Available"}],
            }]),
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
                new=AsyncMock(return_value={"status": "Rejected"}),
            ),
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

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(request["status"], "awaiting_vehicle")
        self.assertEqual(request["last_result"], "rejected")
        self.assertEqual(request["last_error"], "Rejected")

    async def test_qr_start_requires_an_active_card_assigned_to_the_client(self):
        user_id = ObjectId()
        company_id = ObjectId()
        collections = {
            "sesiones": MemoryCollection(),
            "nfc_cards": MemoryCollection(),
        }

        with patch.object(
            client_portal,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            with self.assertRaises(HTTPException) as raised:
                await client_portal.create_client_qr_charge_request(
                    ClientQRChargeRequest(charge_point_id="CP-1"),
                    current_user={"_id": user_id, "empresa_id": company_id},
                )

        self.assertEqual(raised.exception.status_code, 409)

    async def test_qr_start_rejects_a_charger_without_available_connectors(self):
        user_id = ObjectId()
        company_id = ObjectId()
        collections = {
            "sesiones": MemoryCollection(),
            "nfc_cards": MemoryCollection([{
                "_id": ObjectId(),
                "usuario_id": user_id,
                "empresa_id": company_id,
                "uid": "CLIENT-TAG-1",
                "estado": "activa",
            }]),
            "cargadores": MemoryCollection([{
                "charge_point_id": "CP-1",
                "empresa_id": company_id,
                "activo": True,
                "conectores": [{
                    "id_conector": 1,
                    "estado": "Charging",
                }],
            }]),
        }

        with patch.object(
            client_portal,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            with self.assertRaises(HTTPException) as raised:
                await client_portal.create_client_qr_charge_request(
                    ClientQRChargeRequest(charge_point_id="CP-1"),
                    current_user={"_id": user_id, "empresa_id": company_id},
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(str(raised.exception.detail), "El cargador no reporta conectores disponibles")

    async def test_qr_request_rejects_a_charger_from_another_company(self):
        user_id = ObjectId()
        company_id = ObjectId()
        collections = {
            "sesiones": MemoryCollection(),
            "nfc_cards": MemoryCollection([{
                "_id": ObjectId(),
                "usuario_id": user_id,
                "empresa_id": company_id,
                "uid": "CLIENT-TAG-1",
                "estado": "activa",
            }]),
            "cargadores": MemoryCollection([{
                "charge_point_id": "CP-OTHER",
                "empresa_id": ObjectId(),
                "activo": True,
            }]),
            "nfc_charge_requests": MemoryCollection(),
        }

        with patch.object(
            client_portal,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            with self.assertRaises(HTTPException) as raised:
                await client_portal.create_client_qr_charge_request(
                    ClientQRChargeRequest(charge_point_id="CP-OTHER"),
                    current_user={"_id": user_id, "empresa_id": company_id},
                )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(collections["nfc_charge_requests"].documents, [])


if __name__ == "__main__":
    unittest.main()
