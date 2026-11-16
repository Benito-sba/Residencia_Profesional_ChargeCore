import re
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app import auth
from app.models.company import Empresa, normalize_company_name
from app.models.user import UsuarioCreate, UsuarioUpdate
from app.routers import auth as auth_router, users
import seed_initial_company
from seed_initial_company import ensure_initial_company


class FakeCompanies:
    def __init__(self, documents=None):
        self.documents = list(documents or [])
        self.indexes = []

    async def create_index(self, keys, **options):
        self.indexes.append((keys, options))

    async def find_one(self, query):
        if "_id" in query:
            expected_id = query["_id"]
            return next(
                (document.copy() for document in self.documents
                 if document.get("_id") == expected_id),
                None,
            )
        if isinstance(query.get("nombre_normalizado"), str):
            expected = query["nombre_normalizado"]
            return next(
                (document.copy() for document in self.documents
                 if document.get("nombre_normalizado") == expected),
                None,
            )
        if "nombre" in query:
            expression = query["nombre"]["$regex"]
            return next(
                (document.copy() for document in self.documents
                 if re.match(expression, document.get("nombre", ""), re.IGNORECASE)
                 and "nombre_normalizado" not in document),
                None,
            )
        return None

    async def count_documents(self, query):
        expression = query["nombre"]["$regex"]
        return sum(
            re.match(expression, document.get("nombre", ""), re.IGNORECASE) is not None
            for document in self.documents
        )

    async def insert_one(self, document):
        normalized = document["nombre_normalizado"]
        if any(item.get("nombre_normalizado") == normalized for item in self.documents):
            raise DuplicateKeyError("duplicate company name")
        inserted_id = ObjectId()
        self.documents.append({"_id": inserted_id, **document})
        return SimpleNamespace(inserted_id=inserted_id)


class CompanyModelTests(unittest.TestCase):
    def test_company_name_is_normalized_for_comparison(self):
        self.assertEqual(normalize_company_name("  TECNM  "), "tecnm")
        self.assertEqual(normalize_company_name("TeCnM"), "tecnm")

    def test_empresa_populates_normalized_name(self):
        company = Empresa(nombre=" TECNM ")
        self.assertEqual(company.nombre, "TECNM")
        self.assertEqual(company.nombre_normalizado, "tecnm")

    def test_empresa_rejects_mismatched_normalized_name(self):
        with self.assertRaises(ValidationError):
            Empresa(nombre="TECNM", nombre_normalizado="otra")

    def test_empresa_rejects_blank_display_name(self):
        with self.assertRaises(ValidationError):
            Empresa(nombre="   ")


class UserRoleCompatibilityTests(unittest.TestCase):
    def test_legacy_roles_remain_valid_without_company(self):
        for role in ("admin", "operador", "consulta", "cliente"):
            user = UsuarioCreate(
                nombre="Usuario de prueba",
                email=f"{role}@example.com",
                rol=role,
                password="safe-test-password",
            )
            self.assertIsNone(user.empresa_id)

    def test_new_company_owner_requires_company(self):
        with self.assertRaises(ValidationError):
            UsuarioCreate(
                nombre="Dueño de prueba",
                email="owner@example.com",
                rol="dueno_empresa",
                password="safe-test-password",
            )

    def test_superadmin_cannot_have_company(self):
        with self.assertRaises(ValidationError):
            UsuarioCreate(
                nombre="Superadmin de prueba",
                email="superadmin@example.com",
                rol="superadmin",
                empresa_id=ObjectId(),
                password="safe-test-password",
            )

    def test_company_owner_update_requires_company(self):
        with self.assertRaises(ValidationError):
            UsuarioUpdate(rol="dueno_empresa")


class CompanyAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_management_access_allows_superadmin_but_rejects_legacy_admin(self):
        superadmin = {"rol": "superadmin"}
        with patch.object(
            auth,
            "get_current_user",
            new=AsyncMock(return_value=superadmin),
        ):
            resolved = await auth.require_user_management_access(
                token="test-token"
            )
        self.assertEqual(resolved, superadmin)

        with patch.object(
            auth,
            "get_current_user",
            new=AsyncMock(return_value={"rol": "admin"}),
        ):
            with self.assertRaises(HTTPException) as raised:
                await auth.require_user_management_access(token="test-token")
        self.assertEqual(raised.exception.status_code, 403)

    async def test_user_management_access_validates_company_owner(self):
        company_id = ObjectId()
        owner = {"rol": "dueno_empresa", "empresa_id": company_id}
        company = {"_id": company_id, "nombre": "Empresa A", "activo": True}

        with (
            patch.object(
                auth,
                "get_current_user",
                new=AsyncMock(return_value=owner),
            ),
            patch.object(
                auth,
                "get_collection",
                return_value=FakeCompanies([company]),
            ),
        ):
            resolved = await auth.require_user_management_access(
                token="test-token"
            )

        self.assertEqual(resolved, owner)

    async def test_user_management_access_rejects_other_roles_and_invalid_superadmin(self):
        for user in (
            {"rol": "operador"},
            {"rol": "cliente"},
            {"rol": "superadmin", "empresa_id": ObjectId()},
        ):
            with self.subTest(user=user), patch.object(
                auth,
                "get_current_user",
                new=AsyncMock(return_value=user),
            ):
                with self.assertRaises(HTTPException) as raised:
                    await auth.require_user_management_access(
                        token="test-token"
                    )
                self.assertEqual(raised.exception.status_code, 403)

    async def test_superadmin_dependency_accepts_superadmin_without_company(self):
        user = {"rol": "superadmin"}
        with patch.object(
            auth,
            "get_current_user",
            new=AsyncMock(return_value=user),
        ):
            resolved = await auth.require_superadmin(token="test-token")
        self.assertEqual(resolved, user)

    async def test_superadmin_dependency_rejects_other_legacy_roles(self):
        for user in (
            {"rol": "admin"},
            {"rol": "consulta"},
            {"rol": "superadmin", "empresa_id": ObjectId()},
        ):
            with self.subTest(user=user), patch.object(
                auth,
                "get_current_user",
                new=AsyncMock(return_value=user),
            ):
                with self.assertRaises(HTTPException) as raised:
                    await auth.require_superadmin(token="test-token")
                self.assertEqual(raised.exception.status_code, 403)

        with patch.object(
            auth,
            "get_current_user",
            new=AsyncMock(return_value={"rol": "admin"}),
        ):
            legacy_admin = await auth.require_admin(token="test-token")
        self.assertEqual(legacy_admin["rol"], "admin")

    async def test_company_dependency_resolves_active_company_from_user_record(self):
        company_id = ObjectId()
        company = {"_id": company_id, "nombre": "TECNM", "activo": True}
        with patch.object(
            auth,
            "get_collection",
            return_value=FakeCompanies([company]),
        ):
            resolved = await auth.get_current_company(
                current_user={"rol": "dueno_empresa", "empresa_id": company_id}
            )
        self.assertEqual(resolved, company)

    async def test_company_dependency_rejects_missing_or_inactive_company(self):
        company_id = ObjectId()
        inactive = {"_id": company_id, "nombre": "TECNM", "activo": False}
        with patch.object(auth, "get_collection", return_value=FakeCompanies([inactive])):
            with self.assertRaises(HTTPException) as raised:
                await auth.get_current_company(
                    current_user={"rol": "cliente", "empresa_id": company_id}
                )
        self.assertEqual(raised.exception.status_code, 403)

    async def test_company_dependency_rejects_suspended_company(self):
        company_id = ObjectId()
        suspended = {
            "_id": company_id,
            "nombre": "TECNM",
            "activo": False,
            "estado": "suspendida",
        }
        with patch.object(
            auth,
            "get_collection",
            return_value=FakeCompanies([suspended]),
        ):
            with self.assertRaises(HTTPException) as raised:
                await auth.get_current_company(
                    current_user={"rol": "cliente", "empresa_id": company_id}
                )
        self.assertEqual(raised.exception.status_code, 403)

    async def test_company_owner_dependency_checks_company_membership(self):
        company_id = ObjectId()
        user = {"rol": "dueno_empresa", "empresa_id": company_id}
        company = {"_id": company_id, "nombre": "TECNM", "activo": True}
        with (
            patch.object(auth, "get_current_user", new=AsyncMock(return_value=user)),
            patch.object(auth, "get_collection", return_value=FakeCompanies([company])),
        ):
            result = await auth.require_company_owner(token="test-token")
        self.assertEqual(result, user)

    async def test_company_dependency_rejects_user_without_company(self):
        with self.assertRaises(HTTPException) as raised:
            await auth.get_current_company(
                current_user={"rol": "dueno_empresa"}
            )
        self.assertEqual(raised.exception.status_code, 403)

    async def test_legacy_admin_cannot_assign_new_role_through_user_api(self):
        payload = UsuarioCreate(
            nombre="Superadmin de prueba",
            email="superadmin@example.com",
            rol="superadmin",
            password="safe-test-password",
        )
        with self.assertRaises(HTTPException) as raised:
            await users.create_user(payload, current_user={"rol": "admin"})
        self.assertEqual(raised.exception.status_code, 403)

    async def test_legacy_admin_cannot_assign_new_role_through_register_route(self):
        payload = UsuarioCreate(
            nombre="Dueño de prueba",
            email="owner@example.com",
            rol="dueno_empresa",
            empresa_id=ObjectId(),
            password="safe-test-password",
        )
        with self.assertRaises(HTTPException) as raised:
            await auth_router.register(payload, current_user={"rol": "admin"})
        self.assertEqual(raised.exception.status_code, 403)

    async def test_legacy_admin_cannot_change_company_through_user_api(self):
        user_id = ObjectId()
        payload = UsuarioUpdate(empresa_id=ObjectId())
        with patch.object(
            users,
            "get_collection",
            return_value=SimpleNamespace(
                find_one=AsyncMock(return_value={"_id": user_id, "rol": "cliente"})
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await users.update_user(
                    str(user_id), payload, current_user={"rol": "admin"}
                )
        self.assertEqual(raised.exception.status_code, 403)


class InitialCompanySeedTests(unittest.IsolatedAsyncioTestCase):
    def test_seed_command_requires_explicit_confirmation(self):
        with (
            patch.object(sys, "argv", ["seed_initial_company.py"]),
            patch.object(
                seed_initial_company,
                "_register_initial_company",
                new=AsyncMock(),
            ) as register,
            self.assertRaises(SystemExit),
        ):
            seed_initial_company.main()
        register.assert_not_awaited()

    async def test_seed_is_explicit_and_idempotent(self):
        companies = FakeCompanies()
        first_id, created_first = await ensure_initial_company(companies)
        second_id, created_second = await ensure_initial_company(companies)

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first_id, second_id)
        self.assertEqual(len(companies.documents), 1)
        self.assertEqual(companies.documents[0]["nombre"], "TECNM")
        self.assertEqual(companies.indexes[0][1]["unique"], True)

    async def test_seed_stops_for_legacy_company_without_normalized_name(self):
        companies = FakeCompanies([{"_id": ObjectId(), "nombre": "TECNM"}])
        with self.assertRaisesRegex(RuntimeError, "inconsistente"):
            await ensure_initial_company(companies)
        self.assertEqual(len(companies.documents), 1)


if __name__ == "__main__":
    unittest.main()
