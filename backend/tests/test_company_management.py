import asyncio
import unittest
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure

from app.database import serialize_company_change
from app.auth import (
    require_company_admin_access,
    require_company_owner,
    require_superadmin,
)
from app.models.company import normalize_company_name
from app.routers import companies
from app.routers.companies import EmpresaUpdate


class MemoryCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, key, direction):
        self.documents.sort(key=lambda document: document.get(key) or "")
        if direction < 0:
            self.documents.reverse()
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def skip(self, count):
        self.documents = self.documents[count:]
        return self

    def __aiter__(self):
        self.index = 0
        return self

    async def __anext__(self):
        if self.index >= len(self.documents):
            raise StopAsyncIteration
        document = self.documents[self.index]
        self.index += 1
        return deepcopy(document)


class MemoryCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])
        self.active_count = 0

    @staticmethod
    def _matches(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
                if "$in" in expected and actual not in expected["$in"]:
                    return False
            elif actual != expected:
                return False
        return True

    async def find_one(self, query, projection=None, *, session=None):
        for document in self.documents:
            if self._matches(document, query):
                return deepcopy(document)
        return None

    def find(self, query=None):
        return MemoryCursor([
            deepcopy(document)
            for document in self.documents
            if self._matches(document, query or {})
        ])

    async def insert_one(self, document, *, session=None):
        inserted = deepcopy(document)
        inserted["_id"] = ObjectId()
        self.documents.append(inserted)
        return SimpleNamespace(inserted_id=inserted["_id"])

    async def update_one(self, query, update, *, session=None):
        for document in self.documents:
            if self._matches(document, query):
                document.update(deepcopy(update.get("$set", {})))
                for key, value in update.get("$inc", {}).items():
                    document[key] = document.get(key, 0) + value
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    async def count_documents(self, query, *, session=None):
        return self.active_count + sum(
            self._matches(document, query) for document in self.documents
        )


class MemoryTransaction:
    def __init__(self, client):
        self.client = client
        self.snapshot = None

    async def __aenter__(self):
        await self.client.lock.acquire()
        self.snapshot = {
            name: deepcopy(collection.documents)
            for name, collection in self.client.collections.items()
        }
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            for name, documents in self.snapshot.items():
                self.client.collections[name].documents = documents
        self.client.lock.release()
        return False


class MemorySession:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def start_transaction(self):
        return MemoryTransaction(self.client)


class MemoryMongoClient:
    def __init__(self, collections):
        self.collections = collections
        self.lock = asyncio.Lock()

    async def start_session(self):
        return MemorySession(self)


class CompanyManagementApiTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(companies.router)
        self.client = TestClient(self.app)
        self.collections = {
            "empresas": MemoryCollection(),
            "sesiones": MemoryCollection(),
            "cargadores": MemoryCollection(),
            "nfc_charge_requests": MemoryCollection(),
            "usuarios": MemoryCollection(),
            "vehiculos": MemoryCollection(),
        }
        self.mongo_client = MemoryMongoClient(self.collections)
        self.app.dependency_overrides[require_superadmin] = lambda: {
            "rol": "superadmin"
        }
        self.company_id = ObjectId()
        self.app.dependency_overrides[require_company_owner] = lambda: {
            "rol": "dueno_empresa",
            "empresa_id": self.company_id,
        }
        self.app.dependency_overrides[require_company_admin_access] = lambda: {
            "rol": "dueno_empresa",
            "empresa_id": self.company_id,
        }

    def tearDown(self):
        self.client.close()

    @contextmanager
    def _mock_collections(self):
        with (
            patch.object(
                companies,
                "get_collection",
                side_effect=lambda name: self.collections[name],
            ),
            patch.object(
                companies,
                "get_database",
                return_value=SimpleNamespace(client=self.mongo_client),
            ),
        ):
            yield

    def test_superadmin_can_create_and_update_companies_and_owner_reads_own(self):
        with self._mock_collections():
            created = self.client.post(
                "/api/empresas/",
                json={
                    "nombre": "  Empresa   Uno  ",
                    "contacto_nombre": "  Ana Pérez ",
                    "contacto_email": " ana@example.com ",
                    "contacto_telefono": " 555-0101 ",
                },
            )
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["nombre"], "Empresa Uno")
            self.assertEqual(created.json()["contacto_nombre"], "Ana Pérez")
            self.assertEqual(created.json()["contacto_email"], "ana@example.com")
            self.assertEqual(created.json()["estado"], "activa")

            company_id = created.json()["_id"]
            listed = self.client.get("/api/empresas/")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(len(listed.json()), 1)

            updated = self.client.put(
                f"/api/empresas/{company_id}",
                json={
                    "nombre": "Empresa Renovada",
                    "contacto_email": "",
                    "estado": "suspendida",
                },
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["nombre"], "Empresa Renovada")
            self.assertIsNone(updated.json()["contacto_email"])
            self.assertEqual(updated.json()["estado"], "suspendida")
            self.assertFalse(updated.json()["activo"])

            owner_company = {
                "_id": self.company_id,
                "nombre": "Empresa Propia",
                "activo": True,
            }
            self.collections["empresas"].documents.append(owner_company)
            profile = self.client.get("/api/empresas/me")
            self.assertEqual(profile.status_code, 200)
            self.assertEqual(profile.json()["nombre"], "Empresa Propia")

    def test_company_overview_is_scoped_and_includes_recent_sessions(self):
        other_company_id = ObjectId()
        owner_id = ObjectId()
        client_id = ObjectId()
        self.collections["empresas"].documents.append({
            "_id": self.company_id,
            "nombre": "Empresa Uno",
            "nombre_normalizado": "empresa uno",
            "activo": True,
            "estado": "activa",
        })
        self.collections["usuarios"].documents.extend([
            {
                "_id": owner_id,
                "empresa_id": self.company_id,
                "rol": "dueno_empresa",
                "nombre": "Duena",
            },
            {
                "_id": client_id,
                "empresa_id": self.company_id,
                "rol": "cliente",
                "nombre": "Cliente",
            },
            {
                "_id": ObjectId(),
                "empresa_id": other_company_id,
                "rol": "cliente",
                "nombre": "Otra empresa",
            },
        ])
        self.collections["cargadores"].documents.extend([
            {"empresa_id": self.company_id, "charge_point_id": "CP-OWN"},
            {"empresa_id": other_company_id, "charge_point_id": "CP-OTHER"},
        ])
        self.collections["sesiones"].documents.extend([
            {
                "empresa_id": self.company_id,
                "usuario_id": client_id,
                "transaction_id": 101,
                "charge_point_id": "CP-OWN",
                "estado": "finalizada",
                "fecha_inicio": datetime(2026, 1, 2),
                "energia_consumida_kwh": 3.5,
            },
            {
                "empresa_id": other_company_id,
                "transaction_id": 202,
                "charge_point_id": "CP-OTHER",
                "estado": "activa",
                "fecha_inicio": datetime(2026, 1, 3),
            },
        ])

        with self._mock_collections():
            response = self.client.get(
                f"/api/empresas/{self.company_id}/overview"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metricas"], {
            "cargadores": 1,
            "usuarios": 2,
            "clientes": 1,
            "duenos": 1,
            "sesiones": 1,
            "sesiones_activas": 0,
            "sesiones_finalizadas": 1,
        })
        self.assertEqual(len(payload["actividad"]), 1)
        self.assertEqual(payload["actividad"][0]["transaction_id"], 101)
        self.assertEqual(payload["actividad"][0]["cliente"], "Cliente")

    def test_company_overview_activity_is_paginated_and_scoped(self):
        other_company_id = ObjectId()
        self.collections["empresas"].documents.append({
            "_id": self.company_id,
            "nombre": "Empresa Uno",
            "nombre_normalizado": "empresa uno",
            "activo": True,
            "estado": "activa",
        })
        self.collections["sesiones"].documents.extend([
            {
                "empresa_id": self.company_id,
                "transaction_id": index,
                "fecha_inicio": datetime(2026, 1, 1, 0, index),
            }
            for index in range(25)
        ])
        self.collections["sesiones"].documents.append({
            "empresa_id": other_company_id,
            "transaction_id": 999,
            "fecha_inicio": datetime(2026, 1, 2),
        })

        with self._mock_collections():
            first_page = self.client.get(
                f"/api/empresas/{self.company_id}/overview",
                params={"skip": 0, "limit": 20},
            )
            second_page = self.client.get(
                f"/api/empresas/{self.company_id}/overview",
                params={"skip": 20, "limit": 20},
            )

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(second_page.status_code, 200)
        first_activity = first_page.json()["actividad"]
        second_activity = second_page.json()["actividad"]
        self.assertEqual(len(first_activity), 20)
        self.assertEqual(len(second_activity), 5)
        self.assertEqual(
            {row["transaction_id"] for row in first_activity + second_activity},
            set(range(25)),
        )

    def test_company_status_rejects_conflicting_active_flag(self):
        with self._mock_collections():
            response = self.client.put(
                f"/api/empresas/{self.company_id}",
                json={"estado": "suspendida", "activo": True},
            )

        self.assertEqual(response.status_code, 422)

    def test_company_names_are_unique_and_owner_cannot_list_all_companies(self):
        with self._mock_collections():
            created = self.client.post(
                "/api/empresas/",
                json={"nombre": "Empresa Uno"},
            )
            duplicate = self.client.post(
                "/api/empresas/",
                json={"nombre": " empresa uno "},
            )
            self.assertEqual(created.status_code, 201)
            self.assertEqual(duplicate.status_code, 409)

            def reject_owner():
                raise HTTPException(status_code=403, detail="Permisos insuficientes")

            self.app.dependency_overrides[require_superadmin] = reject_owner
            denied = self.client.get("/api/empresas/")
            self.assertEqual(denied.status_code, 403)

    def test_company_cannot_be_deactivated_with_active_sessions(self):
        company = {
            "_id": self.company_id,
            "nombre": "Empresa Uno",
            "nombre_normalizado": "empresa uno",
            "activo": True,
        }
        self.collections["empresas"].documents.append(company)
        self.collections["sesiones"].active_count = 1

        with self._mock_collections():
            response = self.client.put(
                f"/api/empresas/{self.company_id}",
                json={"activo": False},
            )

        self.assertEqual(response.status_code, 409)
        self.assertTrue(self.collections["empresas"].documents[0]["activo"])
        self.assertNotIn(
            "owner_change_version",
            self.collections["empresas"].documents[0],
        )

    def test_company_cannot_be_deactivated_with_connected_chargers(self):
        self.collections["empresas"].documents.append({
            "_id": self.company_id,
            "nombre": "Empresa Uno",
            "nombre_normalizado": "empresa uno",
            "activo": True,
        })
        self.collections["cargadores"].documents.append({
            "empresa_id": self.company_id,
            "activo": True,
            "conectado": True,
        })

        with self._mock_collections():
            response = self.client.put(
                f"/api/empresas/{self.company_id}",
                json={"activo": False},
            )

        self.assertEqual(response.status_code, 409)
        company = self.collections["empresas"].documents[0]
        self.assertTrue(company["activo"])
        self.assertNotIn("owner_change_version", company)

    def test_company_can_be_deactivated_without_active_resources(self):
        self.collections["empresas"].documents.append({
            "_id": self.company_id,
            "nombre": "Empresa Uno",
            "nombre_normalizado": "empresa uno",
            "activo": True,
        })

        with self._mock_collections():
            response = self.client.put(
                f"/api/empresas/{self.company_id}",
                json={"activo": False},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["activo"])
        self.assertFalse(self.collections["empresas"].documents[0]["activo"])

    def test_company_can_be_suspended_while_charger_is_connected(self):
        self.collections["empresas"].documents.append({
            "_id": self.company_id,
            "nombre": "Empresa Uno",
            "nombre_normalizado": "empresa uno",
            "activo": True,
            "estado": "activa",
        })
        self.collections["sesiones"].active_count = 1
        self.collections["cargadores"].documents.append({
            "empresa_id": self.company_id,
            "activo": True,
            "conectado": True,
        })

        with self._mock_collections():
            response = self.client.put(
                f"/api/empresas/{self.company_id}",
                json={"estado": "suspendida"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["estado"], "suspendida")
        self.assertFalse(response.json()["activo"])

    def test_transaction_conflict_returns_409_without_partial_changes(self):
        self.collections["empresas"].documents.append({
            "_id": self.company_id,
            "nombre": "Empresa Uno",
            "nombre_normalizado": "empresa uno",
            "activo": True,
        })
        transient_error = OperationFailure(
            "simulated transaction conflict",
            details={"errorLabels": ["TransientTransactionError"]},
        )

        with (
            self._mock_collections(),
            patch.object(
                self.collections["sesiones"],
                "count_documents",
                new_callable=AsyncMock,
                side_effect=transient_error,
            ),
        ):
            response = self.client.put(
                f"/api/empresas/{self.company_id}",
                json={"activo": False},
            )

        self.assertEqual(response.status_code, 409)
        company = self.collections["empresas"].documents[0]
        self.assertTrue(company["activo"])
        self.assertNotIn("owner_change_version", company)

    def test_owner_qr_operations_are_scoped_to_own_company(self):
        other_company_id = ObjectId()
        self.collections["nfc_charge_requests"].documents.extend([
            {
                "_id": ObjectId(),
                "empresa_id": self.company_id,
                "usuario_id": ObjectId(),
                "charge_point_id": "CP-OWN",
                "source": "qr",
                "status": "awaiting_vehicle",
            },
            {
                "_id": ObjectId(),
                "empresa_id": other_company_id,
                "usuario_id": ObjectId(),
                "charge_point_id": "CP-OTHER",
                "source": "qr",
                "status": "awaiting_vehicle",
            },
        ])

        with self._mock_collections():
            response = self.client.get("/api/empresas/qr-operations")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["charge_point_id"], "CP-OWN")

    def test_superadmin_can_monitor_qr_operations_globally(self):
        self.collections["nfc_charge_requests"].documents.extend([
            {
                "_id": ObjectId(),
                "empresa_id": self.company_id,
                "charge_point_id": "CP-ONE",
                "source": "qr",
                "status": "awaiting_vehicle",
            },
            {
                "_id": ObjectId(),
                "empresa_id": ObjectId(),
                "charge_point_id": "CP-TWO",
                "source": "qr",
                "status": "start_requested",
            },
        ])
        self.app.dependency_overrides[require_company_admin_access] = lambda: {
            "rol": "superadmin"
        }

        with self._mock_collections():
            response = self.client.get("/api/empresas/qr-operations")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item["charge_point_id"] for item in response.json()},
            {"CP-ONE", "CP-TWO"},
        )

    def test_qr_operations_include_company_vehicle_and_linked_session_status(self):
        user_id = ObjectId()
        vehicle_id = ObjectId()
        request_id = ObjectId()
        started_at = datetime(2026, 1, 2, 10, 30)
        finished_at = datetime(2026, 1, 2, 11, 15)
        self.collections["empresas"].documents.append({
            "_id": self.company_id,
            "nombre": "Empresa Norte",
        })
        self.collections["cargadores"].documents.append({
            "charge_point_id": "CP-QR-1",
            "empresa_id": self.company_id,
            "nombre": "Cargador principal",
        })
        self.collections["usuarios"].documents.append({
            "_id": user_id,
            "empresa_id": self.company_id,
            "nombre": "Cliente Uno",
        })
        self.collections["vehiculos"].documents.append({
            "_id": vehicle_id,
            "empresa_id": self.company_id,
            "usuario_id": user_id,
            "marca": "Nissan",
            "modelo": "Leaf",
            "placa": "ABC-123",
        })
        self.collections["nfc_charge_requests"].documents.append({
            "_id": request_id,
            "empresa_id": self.company_id,
            "usuario_id": user_id,
            "vehicle_id": vehicle_id,
            "charge_point_id": "CP-QR-1",
            "source": "qr",
            "status": "transaction_started",
            "transaction_id": 456,
            "created_at": datetime(2026, 1, 2, 10, 25),
            "started_at": started_at,
        })
        self.collections["sesiones"].documents.append({
            "qr_request_id": request_id,
            "empresa_id": self.company_id,
            "usuario_id": user_id,
            "charge_point_id": "CP-QR-1",
            "transaction_id": 456,
            "estado": "finalizada",
            "fecha_inicio": started_at,
            "fecha_fin": finished_at,
        })

        with self._mock_collections():
            response = self.client.get("/api/empresas/qr-operations")

        self.assertEqual(response.status_code, 200)
        operation = response.json()[0]
        self.assertEqual(operation["empresa"], "Empresa Norte")
        self.assertEqual(operation["cliente"], "Cliente Uno")
        self.assertEqual(operation["vehiculo"], "Nissan Leaf · ABC-123")
        self.assertEqual(operation["cargador"], "Cargador principal")
        self.assertEqual(operation["estado_operacion"], "finished")
        self.assertEqual(operation["transaction_id"], 456)
        self.assertEqual(operation["seguimiento"], "Sesión finalizada · TX 456")
        self.assertEqual(operation["started_at"], started_at.isoformat())
        self.assertEqual(operation["finished_at"], finished_at.isoformat())


class CompanyUpdateConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_suspended_company_cannot_start_a_company_scoped_transaction(self):
        company_id = ObjectId()
        companies_collection = MemoryCollection([{
            "_id": company_id,
            "nombre": "Empresa Suspendida",
            "activo": True,
            "estado": "suspendida",
        }])

        result = await serialize_company_change(
            company_id,
            object(),
            companies_collection,
            require_active=True,
        )

        self.assertEqual(result.matched_count, 0)
        self.assertNotIn(
            "owner_change_version",
            companies_collection.documents[0],
        )

    async def test_concurrent_deactivation_and_rename_preserve_both_changes(self):
        company_id = ObjectId()
        collections = {
            "empresas": MemoryCollection([{
                "_id": company_id,
                "nombre": "Empresa Uno",
                "nombre_normalizado": normalize_company_name("Empresa Uno"),
                "activo": True,
            }]),
            "sesiones": MemoryCollection(),
            "cargadores": MemoryCollection(),
        }
        mongo_client = MemoryMongoClient(collections)

        with (
            patch.object(
                companies,
                "get_collection",
                side_effect=collections.__getitem__,
            ),
            patch.object(
                companies,
                "get_database",
                return_value=SimpleNamespace(client=mongo_client),
            ),
        ):
            deactivation, rename = await asyncio.gather(
                companies.update_company(
                    str(company_id),
                    EmpresaUpdate(activo=False),
                    {"rol": "superadmin"},
                ),
                companies.update_company(
                    str(company_id),
                    EmpresaUpdate(nombre="Empresa Renovada"),
                    {"rol": "superadmin"},
                ),
            )

        self.assertFalse(deactivation["activo"])
        self.assertEqual(rename["nombre"], "Empresa Renovada")
        self.assertFalse(collections["empresas"].documents[0]["activo"])
        self.assertEqual(
            collections["empresas"].documents[0]["nombre"],
            "Empresa Renovada",
        )

    async def test_concurrent_session_start_cannot_commit_against_stale_company_state(self):
        company_id = ObjectId()
        collections = {
            "empresas": MemoryCollection([{
                "_id": company_id,
                "nombre": "Empresa Uno",
                "nombre_normalizado": normalize_company_name("Empresa Uno"),
                "activo": True,
            }]),
            "sesiones": MemoryCollection(),
            "cargadores": MemoryCollection(),
        }
        mongo_client = MemoryMongoClient(collections)

        async def start_session():
            async with await mongo_client.start_session() as session:
                async with session.start_transaction():
                    company = await serialize_company_change(
                        company_id,
                        session,
                        collections["empresas"],
                        require_active=True,
                    )
                    if company.matched_count == 0:
                        return False
                    await asyncio.sleep(0)
                    await collections["sesiones"].insert_one({
                        "empresa_id": company_id,
                        "estado": "activa",
                    }, session=session)
                    return True

        with (
            patch.object(
                companies,
                "get_collection",
                side_effect=collections.__getitem__,
            ),
            patch.object(
                companies,
                "get_database",
                return_value=SimpleNamespace(client=mongo_client),
            ),
        ):
            start_result, deactivation_result = await asyncio.gather(
                start_session(),
                companies.update_company(
                    str(company_id),
                    EmpresaUpdate(activo=False),
                    {"rol": "superadmin"},
                ),
                return_exceptions=True,
            )

        company_is_active = collections["empresas"].documents[0]["activo"]
        session_was_created = bool(collections["sesiones"].documents)
        self.assertFalse(
            session_was_created and not company_is_active,
            "An active session must not commit after company deactivation",
        )
        if session_was_created:
            self.assertTrue(start_result)
            self.assertIsInstance(deactivation_result, HTTPException)
        else:
            self.assertFalse(start_result)
            self.assertFalse(company_is_active)
            self.assertIsInstance(deactivation_result, dict)


if __name__ == "__main__":
    unittest.main()
