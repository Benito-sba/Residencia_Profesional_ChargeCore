import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException

from app.models.vehicle import VehiculoCreate, VehiculoUpdate
from app.routers import vehicles


class MemoryCursor:
    def __init__(self, documents):
        self.documents = documents

    def __aiter__(self):
        async def iterate():
            for document in self.documents:
                yield document.copy()

        return iterate()


class MemoryCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])

    @staticmethod
    def matches(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
            elif actual != expected:
                return False
        return True

    def find(self, query):
        return MemoryCursor([
            document for document in self.documents
            if self.matches(document, query)
        ])

    async def find_one(self, query):
        return next(
            (
                document.copy() for document in self.documents
                if self.matches(document, query)
            ),
            None,
        )

    async def insert_one(self, document):
        inserted_id = ObjectId()
        self.documents.append({"_id": inserted_id, **document})
        return SimpleNamespace(inserted_id=inserted_id)

    async def update_one(self, query, update):
        for document in self.documents:
            if self.matches(document, query):
                document.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class CompanyVehicleIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.company_a = ObjectId()
        self.company_b = ObjectId()
        self.user_a = ObjectId()
        self.user_b = ObjectId()
        self.vehicle_a = ObjectId()
        self.vehicle_b = ObjectId()
        self.collections = {
            "empresas": MemoryCollection([
                {"_id": self.company_a, "activo": True},
                {"_id": self.company_b, "activo": True},
            ]),
            "usuarios": MemoryCollection([
                {
                    "_id": self.user_a,
                    "rol": "cliente",
                    "activo": True,
                    "empresa_id": self.company_a,
                },
                {
                    "_id": self.user_b,
                    "rol": "cliente",
                    "activo": True,
                    "empresa_id": self.company_b,
                },
            ]),
            "vehiculos": MemoryCollection([
                {
                    "_id": self.vehicle_a,
                    "usuario_id": self.user_a,
                    "empresa_id": self.company_a,
                    "activo": True,
                },
                {
                    "_id": self.vehicle_b,
                    "usuario_id": self.user_b,
                    "empresa_id": self.company_b,
                    "activo": True,
                },
                {
                    "_id": ObjectId(),
                    "usuario_id": self.user_a,
                    "activo": True,
                },
                {
                    "_id": ObjectId(),
                    "usuario_id": self.user_a,
                    "empresa_id": self.company_a,
                    "activo": True,
                    "archivado": True,
                },
            ]),
        }
        self.patcher = patch.object(
            vehicles,
            "get_collection",
            side_effect=self.collections.__getitem__,
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def test_owner_lists_only_vehicles_in_its_company(self):
        result = await vehicles.list_vehicles({
            "rol": "dueno_empresa",
            "empresa_id": self.company_a,
        })

        self.assertEqual([item["_id"] for item in result], [str(self.vehicle_a)])

    async def test_owner_cannot_read_or_modify_other_company_vehicle(self):
        owner = {"rol": "dueno_empresa", "empresa_id": self.company_a}
        with self.assertRaises(HTTPException) as read_error:
            await vehicles.get_user_vehicles(str(self.user_b), owner)
        with self.assertRaises(HTTPException) as update_error:
            await vehicles.update_vehicle(
                str(self.vehicle_b),
                VehiculoUpdate(marca="Ajena"),
                owner,
            )

        self.assertEqual(read_error.exception.status_code, 404)
        self.assertEqual(update_error.exception.status_code, 404)
        self.assertNotIn("marca", self.collections["vehiculos"].documents[1])

    async def test_client_can_only_list_own_company_vehicles(self):
        result = await vehicles.list_vehicles({
            "rol": "cliente",
            "empresa_id": self.company_a,
            "_id": self.user_a,
        })

        self.assertEqual([item["_id"] for item in result], [str(self.vehicle_a)])

    async def test_client_creation_uses_authenticated_company_relation(self):
        vehicle = VehiculoCreate(
            usuario_id=self.user_a,
            marca="Marca",
            modelo="Modelo",
            anio=2025,
        )
        await vehicles.create_vehicle(
            vehicle,
            {
                "rol": "cliente",
                "empresa_id": self.company_a,
                "_id": self.user_a,
            },
        )

        created = self.collections["vehiculos"].documents[-1]
        self.assertEqual(created["usuario_id"], self.user_a)
        self.assertEqual(created["empresa_id"], self.company_a)

    async def test_superadmin_can_read_globally_but_unassigned_owner_data_stays_hidden(self):
        global_result = await vehicles.list_vehicles({"rol": "superadmin"})
        owner_result = await vehicles.list_vehicles({
            "rol": "dueno_empresa",
            "empresa_id": self.company_a,
        })

        self.assertEqual(len(global_result), 3)
        self.assertEqual(len(owner_result), 1)


if __name__ == "__main__":
    unittest.main()
