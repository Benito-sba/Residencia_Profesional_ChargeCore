import asyncio
import unittest
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import require_client, require_company_owner, require_superadmin
from app.models.phase4_rewards import (
    CommercialBenefitAssignmentRequest,
    CommercialBenefitCreate,
    CommercialBenefitUpdate,
    CompanyRedemptionDecision,
    CompanyRedemptionRequest,
    CompanyRewardCreate,
    CompanyRewardUpdate,
)
from app.routers import commercial_benefits, company_rewards
from app.services import company_reward_points
from app.services.company_reward_points import _balance_id, _stable_id


class Cursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, key, direction):
        self.documents.sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __aiter__(self):
        self.index = 0
        return self

    async def __anext__(self):
        if self.index == len(self.documents):
            raise StopAsyncIteration
        value = deepcopy(self.documents[self.index])
        self.index += 1
        return value


class Collection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])

    @classmethod
    def matches(cls, document, query):
        for key, expected in query.items():
            if key == "$or":
                if not any(cls.matches(document, part) for part in expected):
                    return False
                continue
            actual = document.get(key)
            if isinstance(expected, dict):
                for operator, value in expected.items():
                    if operator == "$gte" and (actual is None or actual < value):
                        return False
                    if operator == "$lte" and (actual is None or actual > value):
                        return False
                    if operator == "$ne" and actual == value:
                        return False
                    if operator == "$in" and actual not in value:
                        return False
                    if operator == "$exists" and (key in document) != value:
                        return False
            elif actual != expected:
                return False
        return True

    async def find_one(self, query, *_, session=None, **__):
        for document in self.documents:
            if self.matches(document, query):
                return deepcopy(document)
        return None

    def find(self, query=None, *_, **__):
        return Cursor([
            deepcopy(item)
            for item in self.documents
            if self.matches(item, query or {})
        ])

    async def insert_one(self, document, *, session=None):
        if any(item.get("_id") == document.get("_id") for item in self.documents):
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError("duplicate _id")
        self.documents.append(deepcopy(document))
        return SimpleNamespace(inserted_id=document["_id"])

    async def count_documents(self, query, *, session=None):
        return sum(self.matches(item, query) for item in self.documents)

    async def update_one(self, query, update, *, upsert=False, session=None):
        document = next(
            (item for item in self.documents if self.matches(item, query)),
            None,
        )
        inserted = False
        if document is None and upsert:
            document = {"_id": query.get("_id", ObjectId())}
            self.documents.append(document)
            inserted = True
        if document is None:
            return SimpleNamespace(matched_count=0, modified_count=0)
        for key, value in update.get("$setOnInsert", {}).items():
            if inserted:
                document[key] = deepcopy(value)
        for key, value in update.get("$set", {}).items():
            document[key] = deepcopy(value)
        for key, value in update.get("$inc", {}).items():
            document[key] = document.get(key, 0) + value
        for key in update.get("$unset", {}):
            document.pop(key, None)
        return SimpleNamespace(matched_count=1, modified_count=1)


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
            for name, docs in self.snapshot.items():
                self.client.collections[name].documents = docs
        self.client.lock.release()
        return False


class MemorySession:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def with_transaction(self, callback):
        async with MemoryTransaction(self.client):
            return await callback(self)


class MemoryMongoClient:
    def __init__(self, collections):
        self.collections = collections
        self.lock = asyncio.Lock()

    async def start_session(self):
        return MemorySession(self)


class Phase4RewardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.company_a = ObjectId()
        self.company_b = ObjectId()
        self.owner_a = {"_id": ObjectId(), "rol": "dueno_empresa", "empresa_id": self.company_a}
        self.owner_b = {"_id": ObjectId(), "rol": "dueno_empresa", "empresa_id": self.company_b}
        self.client_a = {"_id": ObjectId(), "rol": "cliente", "empresa_id": self.company_a}
        self.superadmin = {"_id": ObjectId(), "rol": "superadmin"}
        self.collections = {
            "empresas": Collection([
                {"_id": self.company_a, "activo": True, "nombre": "Empresa A"},
                {"_id": self.company_b, "activo": True, "nombre": "Empresa B"},
            ]),
            "usuarios": Collection([
                {**self.owner_a, "activo": True},
                {**self.owner_b, "activo": True},
                {**self.client_a, "activo": True},
            ]),
            "sesiones": Collection(),
            "cargadores": Collection(),
            "commercial_benefits": Collection(),
            "commercial_benefit_assignments": Collection(),
            "commercial_benefit_movements": Collection(),
            "company_reward_catalog": Collection(),
            "company_reward_rules": Collection(),
            "company_reward_balances": Collection(),
            "company_reward_ledger": Collection(),
            "company_reward_redemptions": Collection(),
        }
        self.mongo = MemoryMongoClient(self.collections)

    def _patch_routes(self):
        @contextmanager
        def patch_all():
            with (
                patch.object(
                    commercial_benefits,
                    "get_collection",
                    side_effect=self.collections.__getitem__,
                ),
                patch.object(
                    commercial_benefits,
                    "db",
                    SimpleNamespace(client=self.mongo),
                ),
                patch.object(
                    company_rewards,
                    "get_collection",
                    side_effect=self.collections.__getitem__,
                ),
                patch.object(
                    company_rewards,
                    "db",
                    SimpleNamespace(client=self.mongo),
                ),
            ):
                yield
        return patch_all()

    async def test_superadmin_creates_and_assigns_benefit_with_snapshot(self):
        with self._patch_routes():
            benefit = await commercial_benefits.create_benefit(
                CommercialBenefitCreate(
                    nombre="Crédito de prueba",
                    categoria="creditos_descuentos",
                    tipo="credito",
                    descripcion="Crédito mensual",
                    condiciones="Solo servicios activos",
                    valor=500,
                    fecha_inicio=datetime.utcnow() - timedelta(days=1),
                    fecha_finalizacion=datetime.utcnow() + timedelta(days=30),
                    max_assignments_per_company=1,
                ),
                self.superadmin,
            )
            assigned = await commercial_benefits.assign_benefit(
                benefit["_id"],
                CommercialBenefitAssignmentRequest(
                    empresa_ids=[str(self.company_a)]
                ),
                self.superadmin,
            )
            self.assertEqual(len(assigned), 1)
            self.assertEqual(assigned[0]["snapshot_benefit"]["valor"], 500)

            duplicate_retry = await commercial_benefits.assign_benefit(
                benefit["_id"],
                CommercialBenefitAssignmentRequest(
                    empresa_ids=[str(self.company_a)]
                ),
                self.superadmin,
            )
        self.assertEqual(duplicate_retry[0]["_id"], assigned[0]["_id"])

    async def test_commercial_benefit_update_distinguishes_omitted_new_and_null_end_date(self):
        with self._patch_routes():
            benefit = await commercial_benefits.create_benefit(
                CommercialBenefitCreate(
                    nombre="Vigencia editable",
                    categoria="creditos_descuentos",
                    tipo="credito",
                    descripcion="Beneficio de prueba",
                    valor=25,
                    fecha_inicio=datetime.utcnow() - timedelta(days=1),
                    fecha_finalizacion=datetime.utcnow() + timedelta(days=10),
                ),
                self.superadmin,
            )
            benefit_id = benefit["_id"]
            original_end = self.collections["commercial_benefits"].documents[0][
                "fecha_finalizacion"
            ]

            unchanged = await commercial_benefits.update_benefit(
                benefit_id,
                CommercialBenefitUpdate(nombre="Vigencia conservada"),
                self.superadmin,
            )
            self.assertEqual(
                self.collections["commercial_benefits"].documents[0][
                    "fecha_finalizacion"
                ],
                original_end,
            )

            next_end = datetime.utcnow() + timedelta(days=20)
            updated = await commercial_benefits.update_benefit(
                benefit_id,
                CommercialBenefitUpdate(fecha_finalizacion=next_end),
                self.superadmin,
            )
            self.assertEqual(
                self.collections["commercial_benefits"].documents[0][
                    "fecha_finalizacion"
                ],
                next_end,
            )

            before_clear = deepcopy(
                self.collections["commercial_benefits"].documents[0]
            )
            cleared = await commercial_benefits.update_benefit(
                benefit_id,
                CommercialBenefitUpdate(fecha_finalizacion=None),
                self.superadmin,
            )
            stored = self.collections["commercial_benefits"].documents[0]

        self.assertEqual(unchanged["fecha_finalizacion"], original_end)
        self.assertEqual(updated["fecha_finalizacion"], next_end)
        self.assertNotIn("fecha_finalizacion", cleared)
        self.assertNotIn("fecha_finalizacion", stored)
        for key, value in before_clear.items():
            if key not in {"updated_at", "fecha_finalizacion"}:
                self.assertEqual(stored.get(key), value)
        self.assertNotIn("fecha_finalizacion", CommercialBenefitUpdate().model_fields_set)
        self.assertIn(
            "fecha_finalizacion",
            CommercialBenefitUpdate(fecha_finalizacion=None).model_fields_set,
        )
        with self.assertRaises(ValidationError):
            CommercialBenefitUpdate(
                fecha_inicio=next_end,
                fecha_finalizacion=original_end,
            )

    async def test_unexpired_commercial_benefit_can_be_assigned_and_used(self):
        with self._patch_routes():
            benefit = await commercial_benefits.create_benefit(
                CommercialBenefitCreate(
                    nombre="Sin vencimiento",
                    categoria="creditos_descuentos",
                    tipo="credito",
                    descripcion="Beneficio sin vencimiento",
                    valor=25,
                    fecha_inicio=datetime.utcnow() - timedelta(days=1),
                    fecha_finalizacion=datetime.utcnow() + timedelta(days=10),
                ),
                self.superadmin,
            )
            await commercial_benefits.update_benefit(
                benefit["_id"],
                CommercialBenefitUpdate(fecha_finalizacion=None),
                self.superadmin,
            )
            assigned = await commercial_benefits.assign_benefit(
                benefit["_id"],
                CommercialBenefitAssignmentRequest(
                    empresa_ids=[str(self.company_a)],
                    assignment_request_id=uuid4(),
                ),
                self.superadmin,
            )
            used = await commercial_benefits.use_my_benefit(
                assigned[0]["_id"],
                self.owner_a,
            )

        self.assertIsNone(assigned[0]["expires_at"])
        self.assertEqual(used["beneficio"]["estado"], "utilizado")
        self.assertIsNone(used["beneficio"]["expires_at"])

    async def test_benefit_requirements_snapshot_and_company_assignment_limit(self):
        sessions = self.collections["sesiones"]
        chargers = self.collections["cargadores"]
        sessions.documents.append({
            "_id": ObjectId(),
            "empresa_id": self.company_a,
            "estado": "finalizada",
        })
        chargers.documents.append({
            "_id": ObjectId(),
            "empresa_id": self.company_a,
            "activo": True,
        })
        with self._patch_routes():
            benefit = await commercial_benefits.create_benefit(
                CommercialBenefitCreate(
                    nombre="Beneficio por crecimiento",
                    categoria="crecimiento_permanencia",
                    tipo="credito",
                    descripcion="Crédito sujeto a requisitos",
                    valor=100,
                    fecha_inicio=datetime.utcnow() - timedelta(days=1),
                    fecha_finalizacion=datetime.utcnow() + timedelta(days=30),
                    max_assignments_per_company=2,
                    requisitos={
                        "min_completed_sessions": 2,
                        "min_active_clients": 1,
                        "min_active_chargers": 1,
                    },
                ),
                self.superadmin,
            )
            request_id = uuid4()
            with self.assertRaises(HTTPException) as unmet:
                await commercial_benefits.assign_benefit(
                    benefit["_id"],
                    CommercialBenefitAssignmentRequest(
                        empresa_ids=[str(self.company_a)],
                        assignment_request_id=request_id,
                    ),
                    self.superadmin,
                )
            sessions.documents.append({
                "_id": ObjectId(),
                "empresa_id": self.company_a,
                "estado": "finalizada",
            })
            assigned = await commercial_benefits.assign_benefit(
                benefit["_id"],
                CommercialBenefitAssignmentRequest(
                    empresa_ids=[str(self.company_a)],
                    assignment_request_id=uuid4(),
                ),
                self.superadmin,
            )
            self.assertEqual(
                assigned[0]["evidencia_requisitos"],
                {
                    "min_completed_sessions": 2,
                    "min_active_clients": 1,
                    "min_active_chargers": 1,
                },
            )
            second = await commercial_benefits.assign_benefit(
                benefit["_id"],
                CommercialBenefitAssignmentRequest(
                    empresa_ids=[str(self.company_a)],
                    assignment_request_id=uuid4(),
                ),
                self.superadmin,
            )
            with self.assertRaises(HTTPException) as limit:
                await commercial_benefits.assign_benefit(
                    benefit["_id"],
                    CommercialBenefitAssignmentRequest(
                        empresa_ids=[str(self.company_a)],
                        assignment_request_id=uuid4(),
                    ),
                    self.superadmin,
                )
        self.assertEqual(len(second), 1)
        self.assertEqual(unmet.exception.status_code, 409)
        self.assertEqual(limit.exception.status_code, 409)

    async def test_concurrent_assignments_cannot_exceed_company_limit(self):
        with self._patch_routes():
            benefit = await commercial_benefits.create_benefit(
                CommercialBenefitCreate(
                    nombre="Límite concurrente",
                    categoria="creditos_descuentos",
                    tipo="credito",
                    descripcion="Máximo dos asignaciones",
                    valor=10,
                    fecha_inicio=datetime.utcnow() - timedelta(days=1),
                    fecha_finalizacion=datetime.utcnow() + timedelta(days=30),
                    max_assignments_per_company=2,
                ),
                self.superadmin,
            )

            async def assign():
                try:
                    return await commercial_benefits.assign_benefit(
                        benefit["_id"],
                        CommercialBenefitAssignmentRequest(
                            empresa_ids=[str(self.company_a)],
                            assignment_request_id=uuid4(),
                        ),
                        self.superadmin,
                    )
                except HTTPException as error:
                    return error

            results = await asyncio.gather(assign(), assign(), assign())
        succeeded = [result for result in results if isinstance(result, list)]
        failed = [result for result in results if isinstance(result, HTTPException)]
        self.assertEqual(len(succeeded), 2)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].status_code, 409)
        self.assertEqual(
            await self.collections["commercial_benefit_assignments"].count_documents(
                {"benefit_id": ObjectId(benefit["_id"]), "empresa_id": self.company_a}
            ),
            2,
        )

    async def test_owner_only_sees_and_uses_own_assigned_benefits(self):
        benefit_id = ObjectId()
        assignment_id = _stable_id(f"commercial-benefit:{benefit_id}:{self.company_a}")
        self.collections["commercial_benefit_assignments"].documents.append({
            "_id": assignment_id,
            "benefit_id": benefit_id,
            "empresa_id": self.company_a,
            "estado": "disponible",
            "expires_at": datetime.utcnow() + timedelta(days=1),
            "snapshot_benefit": {"nombre": "Beneficio A"},
        })
        self.collections["commercial_benefit_assignments"].documents.append({
            "_id": ObjectId(),
            "benefit_id": ObjectId(),
            "empresa_id": self.company_b,
            "estado": "disponible",
            "expires_at": datetime.utcnow() + timedelta(days=1),
        })
        with self._patch_routes():
            mine = await commercial_benefits.list_my_benefits(self.owner_a)
            used = await commercial_benefits.use_my_benefit(str(assignment_id), self.owner_a)
            with self.assertRaises(HTTPException) as foreign:
                await commercial_benefits.use_my_benefit(
                    str(self.collections["commercial_benefit_assignments"].documents[1]["_id"]),
                    self.owner_a,
                )
        self.assertEqual(len(mine), 1)
        self.assertEqual(used["beneficio"]["empresa_id"], str(self.company_a))
        self.assertEqual(foreign.exception.status_code, 409)
        self.assertEqual(len(self.collections["commercial_benefit_movements"].documents), 1)

    async def test_benefit_use_enforces_window_and_records_applied_value(self):
        assignment_id = ObjectId()
        assignment = {
            "_id": assignment_id,
            "benefit_id": ObjectId(),
            "empresa_id": self.company_a,
            "estado": "disponible",
            "assigned_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(days=10),
            "condiciones_uso_snapshot": {
                "min_days_after_assignment": 2,
                "max_days_after_assignment": 5,
            },
            "snapshot_benefit": {
                "nombre": "Crédito",
                "tipo": "credito",
                "valor": 150,
            },
        }
        self.collections["commercial_benefit_assignments"].documents.append(
            assignment
        )
        with self._patch_routes():
            with self.assertRaises(HTTPException) as early:
                await commercial_benefits.use_my_benefit(
                    str(assignment_id),
                    self.owner_a,
                )
            self.collections["commercial_benefit_assignments"].documents[0][
                "assigned_at"
            ] = datetime.utcnow() - timedelta(days=3)
            used = await commercial_benefits.use_my_benefit(
                str(assignment_id),
                self.owner_a,
            )
        movement = self.collections["commercial_benefit_movements"].documents[0]
        self.assertEqual(early.exception.status_code, 409)
        self.assertEqual(used["beneficio"]["estado"], "utilizado")
        self.assertEqual(used["valor_aplicado"], 150)
        self.assertEqual(movement["valor_aplicado"], 150)
        self.assertEqual(movement["condiciones_uso_snapshot"]["min_days_after_assignment"], 2)

    async def test_expired_benefit_is_consistently_reported_and_cannot_be_used(self):
        assignment_id = ObjectId()
        self.collections["commercial_benefit_assignments"].documents.append({
            "_id": assignment_id,
            "benefit_id": ObjectId(),
            "empresa_id": self.company_a,
            "estado": "disponible",
            "assigned_at": datetime.utcnow() - timedelta(days=5),
            "expires_at": datetime.utcnow() - timedelta(days=1),
            "snapshot_benefit": {"nombre": "Vencido", "tipo": "credito", "valor": 20},
        })
        with self._patch_routes():
            listed = await commercial_benefits.list_my_benefits(self.owner_a)
            with self.assertRaises(HTTPException) as expired:
                await commercial_benefits.use_my_benefit(
                    str(assignment_id),
                    self.owner_a,
                )
        self.assertEqual(listed[0]["estado"], "vencido")
        self.assertEqual(expired.exception.status_code, 409)

    async def test_superadmin_can_consult_benefit_assignment_history(self):
        assignment_id = ObjectId()
        self.collections["commercial_benefit_assignments"].documents.append({
            "_id": assignment_id,
            "benefit_id": ObjectId(),
            "empresa_id": self.company_a,
            "estado": "disponible",
            "assigned_at": datetime.utcnow() - timedelta(days=3),
            "expires_at": datetime.utcnow() - timedelta(days=1),
            "snapshot_benefit": {"nombre": "Beneficio vencido"},
        })
        self.collections["commercial_benefit_movements"].documents.append({
            "_id": ObjectId(),
            "assignment_id": assignment_id,
            "empresa_id": self.company_a,
            "tipo": "asignacion",
        })
        with self._patch_routes():
            history = await commercial_benefits.list_all_benefit_assignments(
                self.superadmin
            )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["estado"], "vencido")
        self.assertEqual(history[0]["movimientos"][0]["tipo"], "asignacion")

    async def test_non_superadmin_cannot_access_commercial_benefit_administration(self):
        app = FastAPI()
        app.include_router(commercial_benefits.router)
        def reject_non_superadmin():
            raise HTTPException(status_code=403, detail="No autorizado")

        app.dependency_overrides[require_superadmin] = reject_non_superadmin
        client = TestClient(app)
        try:
            catalog_response = client.get("/api/commercial-benefits/catalog")
            assignment_response = client.get("/api/commercial-benefits/assignments")
        finally:
            client.close()
        self.assertEqual(catalog_response.status_code, 403)
        self.assertEqual(assignment_response.status_code, 403)

    async def test_http_permissions_isolate_benefits_and_company_rewards_by_role(self):
        app = FastAPI()
        app.include_router(commercial_benefits.router)
        app.include_router(company_rewards.router)
        app.include_router(company_rewards.client_router)
        owner_assignment = {
            "_id": ObjectId(),
            "benefit_id": ObjectId(),
            "empresa_id": self.company_a,
            "estado": "disponible",
            "assigned_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(days=10),
            "snapshot_benefit": {
                "nombre": "Beneficio de Empresa A",
                "tipo": "credito",
                "valor": 50,
            },
        }
        foreign_assignment = {
            **owner_assignment,
            "_id": ObjectId(),
            "empresa_id": self.company_b,
        }
        self.collections["commercial_benefit_assignments"].documents.extend(
            [owner_assignment, foreign_assignment]
        )

        def reject():
            raise HTTPException(status_code=403, detail="No autorizado")

        app.dependency_overrides[require_superadmin] = lambda: self.superadmin
        with self._patch_routes():
            with TestClient(app) as client:
                benefit_response = client.post(
                    "/api/commercial-benefits/catalog",
                    json={
                        "nombre": "Beneficio HTTP",
                        "categoria": "creditos_descuentos",
                        "tipo": "credito",
                        "descripcion": "Beneficio de prueba HTTP",
                        "valor": 25,
                        "fecha_inicio": (datetime.utcnow() - timedelta(days=1)).isoformat(),
                        "fecha_finalizacion": (datetime.utcnow() + timedelta(days=10)).isoformat(),
                        "max_assignments_per_company": 1,
                    },
                )
                self.assertEqual(benefit_response.status_code, 201)
                benefit = benefit_response.json()
                assignment_response = client.post(
                    f"/api/commercial-benefits/catalog/{benefit['_id']}/assign",
                    json={"empresa_ids": [str(self.company_a)]},
                )
                self.assertEqual(assignment_response.status_code, 200)

                app.dependency_overrides[require_superadmin] = reject
                app.dependency_overrides[require_company_owner] = lambda: self.owner_a
                owner_assignments = client.get("/api/commercial-benefits/mine")
                owner_admin_denied = client.post(
                    "/api/commercial-benefits/catalog",
                    json={},
                )
                own_reward_response = client.post(
                    "/api/company-rewards/catalog",
                    json={
                        "nombre": "Canje HTTP",
                        "descripcion": "Recompensa de la empresa A",
                        "puntos_requeridos": 50,
                    },
                )
                foreign_reward = {
                    "_id": ObjectId(),
                    "empresa_id": self.company_b,
                    "nombre": "Recompensa B",
                    "descripcion": "Privada",
                    "puntos_requeridos": 10,
                    "activa": True,
                }
                self.collections["company_reward_catalog"].documents.append(
                    foreign_reward
                )
                foreign_edit = client.put(
                    f"/api/company-rewards/catalog/{foreign_reward['_id']}",
                    json={"nombre": "Alterada"},
                )
                app.dependency_overrides[require_client] = lambda: self.client_a
                reward = own_reward_response.json()
                self.collections["company_reward_balances"].documents.extend([
                    {
                        "_id": _balance_id(self.company_a, self.client_a["_id"]),
                        "empresa_id": self.company_a,
                        "usuario_id": self.client_a["_id"],
                        "puntos_disponibles": 80,
                        "puntos_reservados": 0,
                    },
                    {
                        "_id": _balance_id(self.company_a, self.owner_b["_id"]),
                        "empresa_id": self.company_a,
                        "usuario_id": self.owner_b["_id"],
                        "puntos_disponibles": 999,
                        "puntos_reservados": 0,
                    },
                ])
                own_redemption_response = client.post(
                    "/api/client/me/company-rewards/redemptions",
                    json={"recompensa_id": reward["_id"]},
                )
                own_history_response = client.get(
                    "/api/client/me/company-rewards/redemptions"
                )
                foreign_redemption_response = client.post(
                    "/api/client/me/company-rewards/redemptions",
                    json={"recompensa_id": str(foreign_reward["_id"])},
                )

        self.assertEqual(owner_assignments.status_code, 200)
        self.assertEqual(len(owner_assignments.json()), 2)
        self.assertTrue(all(
            item["empresa_id"] == str(self.company_a)
            for item in owner_assignments.json()
        ))
        self.assertEqual(owner_admin_denied.status_code, 403)
        self.assertEqual(own_reward_response.status_code, 201)
        self.assertEqual(foreign_edit.status_code, 404)
        self.assertEqual(own_redemption_response.status_code, 201)
        self.assertEqual(len(own_history_response.json()), 1)
        self.assertEqual(
            own_history_response.json()[0]["usuario_id"],
            str(self.client_a["_id"]),
        )
        self.assertEqual(foreign_redemption_response.status_code, 404)
        self.assertEqual(
            self.collections["company_reward_balances"].documents[0][
                "puntos_disponibles"
            ],
            30,
        )

    async def test_company_reward_catalog_is_scoped_to_owner_company(self):
        with self._patch_routes():
            created = await company_rewards.create_company_reward(
                CompanyRewardCreate(
                    nombre="Carga gratis",
                    descripcion="Canje de ejemplo",
                    puntos_requeridos=50,
                ),
                self.owner_a,
            )
            foreign = {
                "_id": ObjectId(),
                "empresa_id": self.company_b,
                "nombre": "Otra empresa",
            }
            self.collections["company_reward_catalog"].documents.append(foreign)
            listed = await company_rewards.list_company_reward_catalog(self.owner_a)
            with self.assertRaises(HTTPException) as cross_company:
                await company_rewards.update_company_reward(
                    str(foreign["_id"]),
                    CompanyRewardUpdate(nombre="Robada"),
                    self.owner_a,
                )
        self.assertEqual(len(listed), 1)
        self.assertEqual(created["empresa_id"], str(self.company_a))
        self.assertEqual(cross_company.exception.status_code, 404)

    async def test_reward_expiration_update_distinguishes_null_from_omitted(self):
        first_end = datetime.utcnow() + timedelta(days=10)
        next_end = datetime.utcnow() + timedelta(days=20)
        with self._patch_routes():
            created = await company_rewards.create_company_reward(
                CompanyRewardCreate(
                    nombre="Recompensa con vigencia",
                    descripcion="Canje de ejemplo",
                    puntos_requeridos=20,
                    fecha_finalizacion=first_end,
                ),
                self.owner_a,
            )
            updated = await company_rewards.update_company_reward(
                created["_id"],
                CompanyRewardUpdate(fecha_finalizacion=next_end),
                self.owner_a,
            )
            omitted = await company_rewards.update_company_reward(
                created["_id"],
                CompanyRewardUpdate(nombre="Vigencia conservada"),
                self.owner_a,
            )
            cleared = await company_rewards.update_company_reward(
                created["_id"],
                CompanyRewardUpdate(fecha_finalizacion=None),
                self.owner_a,
            )
        self.assertEqual(updated["fecha_finalizacion"], next_end)
        self.assertEqual(omitted["fecha_finalizacion"], next_end)
        self.assertIsNone(cleared["fecha_finalizacion"])

    async def test_owner_balances_and_ledger_are_scoped_to_company(self):
        balance_a = {
            "_id": ObjectId(),
            "empresa_id": self.company_a,
            "usuario_id": self.client_a["_id"],
            "puntos_disponibles": 12,
            "puntos_reservados": 0,
        }
        balance_b = {
            "_id": ObjectId(),
            "empresa_id": self.company_b,
            "usuario_id": self.owner_b["_id"],
            "puntos_disponibles": 99,
            "puntos_reservados": 0,
        }
        ledger_a = {"_id": ObjectId(), "empresa_id": self.company_a, "puntos": 12}
        ledger_b = {"_id": ObjectId(), "empresa_id": self.company_b, "puntos": 99}
        self.collections["company_reward_balances"].documents.extend(
            [balance_a, balance_b]
        )
        self.collections["company_reward_ledger"].documents.extend(
            [ledger_a, ledger_b]
        )
        self.collections["company_reward_rules"].documents.append({
            "_id": self.company_a,
            "empresa_id": self.company_a,
            "puntos_por_carga": 10,
            "activa": True,
        })
        with self._patch_routes():
            balances = await company_rewards.list_company_reward_balances(self.owner_a)
            ledger = await company_rewards.list_company_reward_ledger(self.owner_a)
            client_payload = await company_rewards.get_my_company_rewards(self.client_a)
        self.assertEqual(len(balances), 1)
        self.assertEqual(balances[0]["_id"], str(balance_a["_id"]))
        self.assertEqual(balances[0]["empresa_id"], str(self.company_a))
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["_id"], str(ledger_a["_id"]))
        self.assertEqual(ledger[0]["empresa_id"], str(self.company_a))
        self.assertEqual(client_payload["puntos_disponibles"], 0)
        self.assertEqual(client_payload["puntos_por_carga"], 10)
        self.assertEqual(client_payload["movimientos"], [])

    async def _add_customer_reward_balance(self, points=100):
        reward = {
            "_id": ObjectId(),
            "empresa_id": self.company_a,
            "nombre": "Carga gratis",
            "descripcion": "Canje",
            "condiciones": "Válido para el titular",
            "puntos_requeridos": 60,
            "activa": True,
            "fecha_finalizacion": datetime.utcnow() + timedelta(days=30),
        }
        self.collections["company_reward_catalog"].documents.append(reward)
        self.collections["company_reward_balances"].documents.append({
            "_id": _balance_id(self.company_a, self.client_a["_id"]),
            "empresa_id": self.company_a,
            "usuario_id": self.client_a["_id"],
            "puntos_disponibles": points,
            "puntos_reservados": 0,
        })
        return reward

    async def test_client_can_request_own_redemption_owner_can_approve_with_history(self):
        reward = await self._add_customer_reward_balance()
        with self._patch_routes():
            redemption = await company_rewards.request_company_redemption(
                CompanyRedemptionRequest(recompensa_id=str(reward["_id"])),
                self.client_a,
            )
            result = await company_rewards.decide_company_redemption(
                redemption["_id"],
                CompanyRedemptionDecision(decision="aprobar"),
                self.owner_a,
            )
            payload = await company_rewards.get_my_company_rewards(self.client_a)
        self.assertEqual(result["estado"], "aprobada")
        self.assertEqual(payload["puntos_disponibles"], 40)
        self.assertEqual(payload["puntos_reservados"], 0)
        self.assertEqual(len(payload["movimientos"]), 2)
        self.assertIn("snapshot_recompensa", payload["solicitudes"][0])

    async def test_rejected_redemption_restores_points_and_duplicate_pending_is_blocked(self):
        reward = await self._add_customer_reward_balance()
        with self._patch_routes():
            redemption = await company_rewards.request_company_redemption(
                CompanyRedemptionRequest(recompensa_id=str(reward["_id"])),
                self.client_a,
            )
            with self.assertRaises(HTTPException) as duplicate:
                await company_rewards.request_company_redemption(
                    CompanyRedemptionRequest(recompensa_id=str(reward["_id"])),
                    self.client_a,
                )
            self.assertEqual(duplicate.exception.status_code, 409)
            await company_rewards.decide_company_redemption(
                redemption["_id"],
                CompanyRedemptionDecision(decision="rechazar", nota="Fuera de vigencia"),
                self.owner_a,
            )
            self.assertEqual(
                self.collections["company_reward_balances"].documents[0][
                    "puntos_disponibles"
                ],
                100,
            )
            self.assertEqual(
                self.collections["company_reward_balances"].documents[0][
                    "puntos_reservados"
                ],
                0,
            )
            retry = await company_rewards.request_company_redemption(
                CompanyRedemptionRequest(recompensa_id=str(reward["_id"])),
                self.client_a,
            )
        balance = self.collections["company_reward_balances"].documents[0]
        self.assertEqual(balance["puntos_disponibles"], 40)
        self.assertEqual(balance["puntos_reservados"], 60)
        self.assertNotIn(
            "pending_key",
            self.collections["company_reward_redemptions"].documents[0],
        )
        self.assertEqual(
            self.collections["company_reward_redemptions"].documents[1]["_id"],
            ObjectId(retry["_id"]),
        )

    async def test_insufficient_points_and_cross_company_client_reward_are_rejected(self):
        reward = await self._add_customer_reward_balance(points=20)
        with self._patch_routes():
            with self.assertRaises(HTTPException) as insufficient:
                await company_rewards.request_company_redemption(
                    CompanyRedemptionRequest(recompensa_id=str(reward["_id"])),
                    self.client_a,
                )
            other_client = {
                "_id": ObjectId(),
                "rol": "cliente",
                "empresa_id": self.company_b,
            }
            with self.assertRaises(HTTPException) as foreign:
                await company_rewards.request_company_redemption(
                    CompanyRedemptionRequest(recompensa_id=str(reward["_id"])),
                    other_client,
                )
        self.assertEqual(insufficient.exception.status_code, 409)
        self.assertEqual(foreign.exception.status_code, 404)

    async def test_concurrent_redemptions_do_not_overdraw_balance(self):
        reward = await self._add_customer_reward_balance(points=100)
        with self._patch_routes():
            results = await asyncio.gather(
                *[
                    company_rewards.request_company_redemption(
                        CompanyRedemptionRequest(recompensa_id=str(reward["_id"])),
                        self.client_a,
                    )
                    for _ in range(2)
                ],
                return_exceptions=True,
            )
        self.assertEqual(sum(not isinstance(item, Exception) for item in results), 1)
        balance = self.collections["company_reward_balances"].documents[0]
        self.assertEqual(balance["puntos_disponibles"], 40)
        self.assertEqual(balance["puntos_reservados"], 60)
        self.assertEqual(len(self.collections["company_reward_redemptions"].documents), 1)

    async def test_completed_session_accrual_is_idempotent_and_records_snapshot(self):
        session_id = ObjectId()
        user_id = self.client_a["_id"]
        rule = {
            "_id": self.company_a,
            "empresa_id": self.company_a,
            "puntos_por_carga": 15,
            "energia_minima_kwh": 2.5,
            "activa": True,
        }
        self.collections["company_reward_rules"].documents.append(rule)
        session_document = {
            "_id": session_id,
            "empresa_id": self.company_a,
            "usuario_id": user_id,
            "estado": "finalizada",
            "energia_consumida_kwh": 9.5,
        }
        with patch.object(
            company_reward_points,
            "get_collection",
            side_effect=self.collections.__getitem__,
        ), patch.object(
            company_reward_points.db,
            "client",
            self.mongo,
        ):
            first = await company_reward_points.accrue_points_for_completed_session(
                session_document
            )
            second = await company_reward_points.accrue_points_for_completed_session(
                session_document
            )
        self.assertEqual(first, 15)
        self.assertEqual(second, 0)
        self.assertEqual(
            self.collections["company_reward_balances"].documents[0]["puntos_disponibles"],
            15,
        )
        self.assertEqual(
            self.collections["company_reward_ledger"].documents[0]["snapshot_regla"][
                "energia_sesion_kwh"
            ],
            9.5,
        )

    async def test_points_are_not_accrued_for_non_completed_or_unowned_session(self):
        with patch.object(
            company_reward_points,
            "get_collection",
            side_effect=self.collections.__getitem__,
        ), patch.object(
            company_reward_points.db,
            "client",
            self.mongo,
        ):
            result = await company_reward_points.accrue_points_for_completed_session(
                {
                    "_id": ObjectId(),
                    "empresa_id": self.company_a,
                    "usuario_id": self.client_a["_id"],
                    "estado": "activa",
                }
            )
        self.assertEqual(result, 0)
        self.assertEqual(self.collections["company_reward_balances"].documents, [])
        self.assertEqual(self.collections["company_reward_ledger"].documents, [])


if __name__ == "__main__":
    unittest.main()
