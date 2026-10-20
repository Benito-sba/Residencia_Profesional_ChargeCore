import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException

from app.models.client_portal import ClientRewardSelectionRequest
from app.routers import client_portal


class MemoryCollection:
    def __init__(self, documents):
        self.documents = documents

    async def find_one(self, query, *, session=None):
        return next(
            (
                document.copy()
                for document in self.documents
                if self._matches(document, query)
            ),
            None,
        )

    async def update_one(self, query, update, *, session=None):
        for document in self.documents:
            if not self._matches(document, query):
                continue
            for key, value in update.get("$set", {}).items():
                document[key] = value
            for key in update.get("$unset", {}):
                document.pop(key, None)
            return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    @classmethod
    def _matches(cls, document, query):
        for key, expected in query.items():
            if key == "$or":
                if not any(cls._matches(document, item) for item in expected):
                    return False
                continue
            actual = document.get(key)
            if isinstance(expected, dict):
                for operator, value in expected.items():
                    if operator == "$exists" and (key in document) != value:
                        return False
                    if operator == "$ne" and actual == value:
                        return False
                    if operator == "$in" and actual not in value:
                        return False
            elif actual != expected:
                return False
        return True


class ClientRewardSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user_id = ObjectId()
        self.session_id = ObjectId()
        self.reward_id = ObjectId()
        self.session = {
            "_id": self.session_id,
            "usuario_id": self.user_id,
            "estado": "activa",
        }
        self.reward = {
            "_id": self.reward_id,
            "usuario_id": self.user_id,
            "usado": False,
            "snapshot_fecha_finalizacion": datetime.utcnow() + timedelta(days=1),
        }
        self.sessions = MemoryCollection([self.session])
        self.rewards = MemoryCollection([self.reward])
        self.collections = {
            "sesiones": self.sessions,
            "recompensas": self.rewards,
        }
        self.mongo_client = MemoryMongoClient()

    async def select(self, reward_id):
        with patch.object(
            client_portal,
            "get_collection",
            side_effect=self.collections.__getitem__,
        ), patch.object(client_portal.db, "client", self.mongo_client):
            return await client_portal.select_client_reward(
                str(self.session_id),
                ClientRewardSelectionRequest(reward_id=reward_id),
                current_user={"_id": self.user_id, "rol": "cliente"},
            )

    async def test_selection_reserves_reward_for_owned_active_session(self):
        response = await self.select(str(self.reward_id))

        self.assertEqual(response["reward_id"], str(self.reward_id))
        self.assertEqual(self.session["selected_reward_id"], self.reward_id)
        self.assertEqual(self.reward["reservada_para_sesion"], self.session_id)
        self.assertFalse(self.reward["usado"])

    async def test_selection_cannot_use_another_clients_reward(self):
        self.reward["usuario_id"] = ObjectId()

        with self.assertRaises(HTTPException) as raised:
            await self.select(str(self.reward_id))

        self.assertEqual(raised.exception.status_code, 404)
        self.assertNotIn("selected_reward_id", self.session)

    async def test_selection_cannot_reserve_reward_for_another_session(self):
        self.reward["reservada_para_sesion"] = ObjectId()

        with self.assertRaises(HTTPException) as raised:
            await self.select(str(self.reward_id))

        self.assertEqual(raised.exception.status_code, 409)
        self.assertNotIn("selected_reward_id", self.session)

    async def test_selection_rejects_reward_before_its_validity_starts(self):
        self.reward["snapshot_fecha_inicio"] = datetime.utcnow() + timedelta(days=1)

        with self.assertRaises(HTTPException) as raised:
            await self.select(str(self.reward_id))

        self.assertEqual(raised.exception.status_code, 409)
        self.assertNotIn("reservada_para_sesion", self.reward)

    async def test_clearing_selection_releases_reservation(self):
        self.session["selected_reward_id"] = self.reward_id
        self.reward["reservada_para_sesion"] = self.session_id

        response = await self.select(None)

        self.assertIsNone(response["reward_id"])
        self.assertNotIn("selected_reward_id", self.session)
        self.assertNotIn("reservada_para_sesion", self.reward)


class MemoryMongoSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def with_transaction(self, callback):
        return await callback(self)


class MemoryMongoClient:
    async def start_session(self):
        return MemoryMongoSession()


if __name__ == "__main__":
    unittest.main()
