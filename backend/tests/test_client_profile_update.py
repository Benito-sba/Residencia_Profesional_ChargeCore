import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.client_portal import ClientProfileResponse, ClientProfileUpdate
from app.routers import client_portal


class MemoryUsers:
    def __init__(self, user):
        self.user = user
        self.updated = None

    async def update_one(self, query, update):
        self.updated = (query, update)
        return SimpleNamespace(matched_count=1)

    async def find_one(self, query, projection):
        return self.user | self.updated[1]["$set"]

    async def count_documents(self, query):
        return 0


class ClientProfileUpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user_id = ObjectId()
        self.company_id = ObjectId()
        self.user = {
            "_id": self.user_id,
            "empresa_id": self.company_id,
            "nombre": "Cliente",
            "email": "cliente@example.com",
            "telefono": None,
            "rol": "cliente",
            "created_at": datetime.utcnow(),
            "password": "hashed-password",
        }
        self.users = MemoryUsers(self.user)

    async def test_profile_update_only_changes_the_authenticated_client(self):
        with patch.object(
            client_portal,
            "get_collection",
            return_value=self.users,
        ):
            result = await client_portal.update_client_profile(
                ClientProfileUpdate(nombre="  Cliente Nuevo  ", telefono=" 123 "),
                current_user={
                    "_id": self.user_id,
                    "empresa_id": self.company_id,
                    "rol": "cliente",
                },
            )

        self.assertEqual(
            self.users.updated,
            (
                {
                    "_id": self.user_id,
                    "empresa_id": self.company_id,
                    "rol": "cliente",
                    "activo": {"$ne": False},
                },
                {"$set": {"nombre": "Cliente Nuevo", "telefono": "123"}},
            ),
        )
        response = ClientProfileResponse.model_validate(result)
        self.assertEqual(response.nombre, "Cliente Nuevo")
        self.assertNotIn("password", response.model_dump())

    async def test_profile_update_rejects_empty_payload(self):
        with self.assertRaises(HTTPException) as raised:
            await client_portal.update_client_profile(
                ClientProfileUpdate(),
                current_user={
                    "_id": self.user_id,
                    "empresa_id": self.company_id,
                    "rol": "cliente",
                },
            )

        self.assertEqual(raised.exception.status_code, 422)

    def test_profile_update_rejects_whitespace_only_name(self):
        with self.assertRaises(ValidationError):
            ClientProfileUpdate(nombre="   ")


if __name__ == "__main__":
    unittest.main()
