import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException

from app.models.user import UsuarioUpdate
from app.routers import users


class FakeUsersCollection:
    def __init__(self, documents):
        self.documents = documents

    async def find_one(self, query):
        for document in self.documents:
            if self._matches(document, query):
                return document.copy()
        return None

    async def count_documents(self, query):
        return sum(self._matches(document, query) for document in self.documents)

    async def update_one(self, query, update):
        for document in self.documents:
            if self._matches(document, query):
                document.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    async def delete_one(self, query):
        for index, document in enumerate(self.documents):
            if self._matches(document, query):
                self.documents.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    @classmethod
    def _matches(cls, document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
                if "$ne" in expected:
                    continue
            elif actual != expected:
                return False
        return True


class UserAdministratorSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_cannot_demote_last_active_administrator(self):
        admin_id = ObjectId()
        collection = FakeUsersCollection([{
            "_id": admin_id,
            "rol": "admin",
            "activo": True,
        }])

        with patch.object(users, "get_collection", return_value=collection):
            with self.assertRaises(HTTPException) as raised:
                await users.update_user(
                    str(admin_id), UsuarioUpdate(rol="operador"), current_user={}
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(collection.documents[0]["rol"], "admin")

    async def test_can_demote_administrator_when_another_active_admin_exists(self):
        admin_id = ObjectId()
        collection = FakeUsersCollection([
            {"_id": admin_id, "rol": "admin", "activo": True},
            {"_id": ObjectId(), "rol": "admin", "activo": True},
        ])

        with patch.object(users, "get_collection", return_value=collection):
            result = await users.update_user(
                str(admin_id), UsuarioUpdate(rol="operador"), current_user={}
            )

        self.assertEqual(result["mensaje"], "Usuario actualizado")
        self.assertEqual(collection.documents[0]["rol"], "operador")

    async def test_cannot_delete_last_active_administrator(self):
        admin_id = ObjectId()
        collection = FakeUsersCollection([{
            "_id": admin_id,
            "rol": "admin",
            "activo": True,
        }])

        with patch.object(users, "get_collection", return_value=collection):
            with self.assertRaises(HTTPException) as raised:
                await users.delete_user(str(admin_id), current_user={})

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(len(collection.documents), 1)

    async def test_deleting_client_deactivates_account_and_preserves_history(self):
        user_id = ObjectId()
        collection = FakeUsersCollection([{
            "_id": user_id,
            "rol": "cliente",
            "activo": True,
            "total_cargas": 7,
        }])

        with patch.object(users, "get_collection", return_value=collection):
            result = await users.delete_user(str(user_id), current_user={})

        self.assertEqual(
            result["mensaje"],
            "Usuario desactivado; historial conservado",
        )
        self.assertEqual(len(collection.documents), 1)
        self.assertFalse(collection.documents[0]["activo"])
        self.assertEqual(collection.documents[0]["total_cargas"], 7)
        self.assertIn("fecha_baja", collection.documents[0])

    async def test_rejects_duplicate_email_when_updating_user(self):
        user_id = ObjectId()
        collection = FakeUsersCollection([
            {"_id": user_id, "email": "first@example.com", "rol": "cliente"},
            {"_id": ObjectId(), "email": "taken@example.com", "rol": "cliente"},
        ])

        with patch.object(users, "get_collection", return_value=collection):
            with self.assertRaises(HTTPException) as raised:
                await users.update_user(
                    str(user_id),
                    UsuarioUpdate(email="taken@example.com"),
                    current_user={},
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(collection.documents[0]["email"], "first@example.com")

    async def test_deactivating_user_records_deactivation_time(self):
        user_id = ObjectId()
        collection = FakeUsersCollection([{
            "_id": user_id,
            "email": "client@example.com",
            "rol": "cliente",
            "activo": True,
        }])

        with patch.object(users, "get_collection", return_value=collection):
            await users.update_user(
                str(user_id),
                UsuarioUpdate(activo=False),
                current_user={},
            )

        self.assertFalse(collection.documents[0]["activo"])
        self.assertIn("fecha_baja", collection.documents[0])


if __name__ == "__main__":
    unittest.main()
