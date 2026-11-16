import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import HTTPException

from app.routers import client_portal


class MemorySessions:
    def __init__(self, session):
        self.session = session
        self.find_query = None
        self.update_query = None
        self.update = None
        self.update_calls = []

    async def find_one(self, query):
        self.find_query = query
        return self.session

    async def update_one(self, query, update):
        self.update_query = query
        self.update = update
        self.update_calls.append((query, update))
        for key, value in update.get("$set", {}).items():
            self.session[key] = value
        for key in update.get("$unset", {}):
            self.session.pop(key, None)
        return SimpleNamespace(matched_count=1)


class ClientPortalStopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session_id = ObjectId()
        self.user_id = ObjectId()
        self.company_id = ObjectId()
        self.session = {
            "_id": self.session_id,
            "usuario_id": self.user_id,
            "empresa_id": self.company_id,
            "estado": "iniciada",
            "transaction_id": 25,
            "charge_point_id": "CP-1",
        }
        self.sessions = MemorySessions(self.session)

    async def test_started_session_can_be_stopped_by_its_owner(self):
        with (
            patch.object(
                client_portal,
                "get_collection",
                return_value=self.sessions,
            ),
            patch.object(
                client_portal.ocpp_command_service,
                "remote_stop",
                new=AsyncMock(return_value={"status": "Accepted"}),
            ) as remote_stop,
        ):
            result = await client_portal.stop_client_charge(
                str(self.session_id),
                current_user={
                    "_id": self.user_id,
                    "empresa_id": self.company_id,
                },
            )

        self.assertEqual(
            result,
            {
                "mensaje": "Orden de detención enviada al cargador",
                "status": "Accepted",
            },
        )
        self.assertEqual(
            self.sessions.find_query["estado"],
            {"$in": ["activa", "iniciada"]},
        )
        self.assertEqual(
            self.sessions.find_query["empresa_id"],
            self.company_id,
        )
        self.assertEqual(
            self.sessions.update_query["estado"],
            {"$in": ["activa", "iniciada"]},
        )
        remote_stop.assert_awaited_once_with("CP-1", 25)

    async def test_started_session_without_transaction_cannot_be_stopped(self):
        self.session.pop("transaction_id")
        with patch.object(
            client_portal,
            "get_collection",
            return_value=self.sessions,
        ):
            with self.assertRaises(HTTPException) as raised:
                await client_portal.stop_client_charge(
                    str(self.session_id),
                    current_user={
                        "_id": self.user_id,
                        "empresa_id": self.company_id,
                    },
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIsNone(self.sessions.update_query)

    async def test_indeterminate_stop_keeps_request_claimed_to_prevent_retry(self):
        timeout = HTTPException(
            status_code=504,
            detail={"outcome": "indeterminate"},
        )
        with (
            patch.object(
                client_portal,
                "get_collection",
                return_value=self.sessions,
            ),
            patch.object(
                client_portal.ocpp_command_service,
                "remote_stop",
                new=AsyncMock(side_effect=timeout),
            ),
        ):
            with self.assertRaises(HTTPException):
                await client_portal.stop_client_charge(
                    str(self.session_id),
                    current_user={
                        "_id": self.user_id,
                        "empresa_id": self.company_id,
                    },
                )

        self.assertEqual(len(self.sessions.update_calls), 1)
        self.assertIn("stop_requested_at", self.session)


if __name__ == "__main__":
    unittest.main()
