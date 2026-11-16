import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import OperationFailure

from app.models.user import UsuarioCreate, UsuarioUpdate
from app.routers import users


class FakeUsersCursor:
    def __init__(self, documents):
        self.documents = documents

    def skip(self, count):
        self.documents = self.documents[count:]
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __aiter__(self):
        async def iterate():
            for document in self.documents:
                yield document.copy()
        return iterate()


class FakeUsersCollection:
    def __init__(self, documents):
        self.documents = documents

    def find(self, query, projection=None, **kwargs):
        return FakeUsersCursor([
            document.copy()
            for document in self.documents
            if self._matches(document, query)
        ])

    async def find_one(self, query, **kwargs):
        for document in self.documents:
            if self._matches(document, query):
                return document.copy()
        return None

    async def insert_one(self, document):
        inserted_id = ObjectId()
        self.documents.append({"_id": inserted_id, **document})
        return SimpleNamespace(inserted_id=inserted_id)

    async def count_documents(self, query, **kwargs):
        return sum(self._matches(document, query) for document in self.documents)

    async def update_one(self, query, update, **kwargs):
        for document in self.documents:
            if self._matches(document, query):
                document.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    async def delete_one(self, query, **kwargs):
        for index, document in enumerate(self.documents):
            if self._matches(document, query):
                self.documents.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, query, **kwargs):
        matching = [
            document for document in self.documents
            if self._matches(document, query)
        ]
        self.documents[:] = [
            document for document in self.documents
            if not self._matches(document, query)
        ]
        return SimpleNamespace(deleted_count=len(matching))

    async def update_many(self, query, update, **kwargs):
        matched = 0
        for document in self.documents:
            if self._matches(document, query):
                document.update(update.get("$set", {}))
                matched += 1
        return SimpleNamespace(matched_count=matched)

    @classmethod
    def _matches(cls, document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$ne" in expected:
                    continue
                if "$in" in expected:
                    continue
            elif actual != expected:
                return False
        return True


class FakeTransactionContext:
    def __init__(self, lock):
        self.lock = lock

    async def __aenter__(self):
        await self.lock.acquire()
        return self

    async def __aexit__(self, *_):
        self.lock.release()
        return False


class FakeMongoSession:
    def __init__(self, lock):
        self.lock = lock

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def start_transaction(self):
        return FakeTransactionContext(self.lock)


class FakeMongoClient:
    def __init__(self):
        self.transaction_lock = asyncio.Lock()

    async def start_session(self):
        return FakeMongoSession(self.transaction_lock)


class UnknownCommitTransactionContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if exc_type is None:
            raise OperationFailure(
                "Commit result is unknown",
                code=91,
                details={"errorLabels": ["UnknownTransactionCommitResult"]},
            )
        return False


class UnknownCommitMongoSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def start_transaction(self):
        return UnknownCommitTransactionContext()


class FakeCompaniesCollection:
    def __init__(self, documents):
        self.documents = documents
        self.update_calls = []

    async def find_one(self, query):
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return document.copy()
        return None

    async def update_one(self, query, update, **kwargs):
        self.update_calls.append((query, update, kwargs))
        await asyncio.sleep(0)
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                for key, value in update.get("$inc", {}).items():
                    document[key] = document.get(key, 0) + value
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class UserAdministratorSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_company_owner_lists_only_clients_in_own_company(self):
        company_a = ObjectId()
        company_b = ObjectId()
        client_a_id = ObjectId()
        collection = FakeUsersCollection([
            {
                "_id": client_a_id,
                "nombre": "Cliente A",
                "rol": "cliente",
                "empresa_id": company_a,
            },
            {
                "_id": ObjectId(),
                "nombre": "Cliente B",
                "rol": "cliente",
                "empresa_id": company_b,
            },
            {
                "_id": ObjectId(),
                "nombre": "Cliente histórico",
                "rol": "cliente",
            },
            {
                "_id": ObjectId(),
                "nombre": "Dueño A",
                "rol": "dueno_empresa",
                "empresa_id": company_a,
            },
        ])

        with patch.object(users, "get_collection", return_value=collection):
            result = await users.list_users(
                current_user={
                    "rol": "dueno_empresa",
                    "empresa_id": company_a,
                }
            )

        self.assertEqual([user["_id"] for user in result], [str(client_a_id)])
        self.assertEqual(result[0]["empresa_id"], str(company_a))

    async def test_company_owner_cannot_read_other_company_client_by_id(self):
        company_a = ObjectId()
        client_b_id = ObjectId()
        collection = FakeUsersCollection([{
            "_id": client_b_id,
            "rol": "cliente",
            "empresa_id": ObjectId(),
        }])

        with patch.object(users, "get_collection", return_value=collection):
            with self.assertRaises(HTTPException) as raised:
                await users.get_user(
                    str(client_b_id),
                    current_user={
                        "rol": "dueno_empresa",
                        "empresa_id": company_a,
                    },
                )

        self.assertEqual(raised.exception.status_code, 404)

    async def test_company_owner_cannot_update_or_deactivate_other_company_client(self):
        company_a = ObjectId()
        client_b_id = ObjectId()
        client_b = {
            "_id": client_b_id,
            "nombre": "Cliente B",
            "rol": "cliente",
            "empresa_id": ObjectId(),
            "activo": True,
        }
        collection = FakeUsersCollection([client_b])
        owner = {"rol": "dueno_empresa", "empresa_id": company_a}

        with patch.object(users, "get_collection", return_value=collection):
            with self.assertRaises(HTTPException) as update_error:
                await users.update_user(
                    str(client_b_id),
                    UsuarioUpdate(nombre="Modificado"),
                    current_user=owner,
                )
            with self.assertRaises(HTTPException) as delete_error:
                await users.delete_user(str(client_b_id), current_user=owner)

        self.assertEqual(update_error.exception.status_code, 404)
        self.assertEqual(delete_error.exception.status_code, 404)
        self.assertEqual(client_b["nombre"], "Cliente B")
        self.assertTrue(client_b["activo"])

    async def test_company_owner_creates_client_in_authenticated_company(self):
        company_a = ObjectId()
        collection = FakeUsersCollection([])
        payload = UsuarioCreate(
            nombre="Cliente A",
            email="client-a@example.com",
            rol="cliente",
            password="safe-test-password",
        )

        with (
            patch.object(users, "get_collection", return_value=collection),
            patch.object(users, "get_password_hash", return_value="hashed"),
        ):
            result = await users.create_user(
                payload,
                current_user={
                    "rol": "dueno_empresa",
                    "empresa_id": company_a,
                },
            )

        self.assertEqual(result["id"], str(collection.documents[0]["_id"]))
        self.assertEqual(collection.documents[0]["empresa_id"], company_a)
        self.assertEqual(collection.documents[0]["rol"], "cliente")
        self.assertEqual(collection.documents[0]["password"], "hashed")

    async def test_company_owner_cannot_assign_client_to_another_company(self):
        company_a = ObjectId()
        company_b = ObjectId()
        collection = FakeUsersCollection([])
        payload = UsuarioCreate(
            nombre="Cliente B",
            email="client-b@example.com",
            rol="cliente",
            empresa_id=company_b,
            password="safe-test-password",
        )

        with patch.object(users, "get_collection", return_value=collection):
            with self.assertRaises(HTTPException) as raised:
                await users.create_user(
                    payload,
                    current_user={
                        "rol": "dueno_empresa",
                        "empresa_id": company_a,
                    },
                )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(collection.documents, [])

    async def test_company_owner_cannot_create_privileged_users(self):
        company_a = ObjectId()
        role_payloads = (
            ("admin", None),
            ("superadmin", None),
            ("dueno_empresa", ObjectId()),
        )

        for role, target_company in role_payloads:
            with self.subTest(role=role):
                payload = UsuarioCreate(
                    nombre="Usuario privilegiado",
                    email=f"{role}@example.com",
                    rol=role,
                    empresa_id=target_company,
                    password="safe-test-password",
                )
                collection = FakeUsersCollection([])
                with patch.object(
                    users,
                    "get_collection",
                    return_value=collection,
                ):
                    with self.assertRaises(HTTPException) as raised:
                        await users.create_user(
                            payload,
                            current_user={
                                "rol": "dueno_empresa",
                                "empresa_id": company_a,
                            },
                        )

                self.assertEqual(raised.exception.status_code, 403)
                self.assertEqual(collection.documents, [])

    async def test_superadmin_and_legacy_admin_keep_global_user_listing(self):
        company_a = ObjectId()
        company_b = ObjectId()
        collection = FakeUsersCollection([
            {"_id": ObjectId(), "rol": "cliente", "empresa_id": company_a},
            {"_id": ObjectId(), "rol": "cliente", "empresa_id": company_b},
            {"_id": ObjectId(), "rol": "cliente"},
        ])

        with patch.object(users, "get_collection", return_value=collection):
            for administrator in (
                {"rol": "superadmin"},
                {"rol": "admin"},
            ):
                with self.subTest(role=administrator["rol"]):
                    result = await users.list_users(current_user=administrator)
                    self.assertEqual(len(result), 3)

    async def test_superadmin_can_update_new_roles_across_companies(self):
        company_b = ObjectId()
        owner_id = ObjectId()
        client_id = ObjectId()
        collection = FakeUsersCollection([
            {
                "_id": owner_id,
                "nombre": "Dueño B",
                "rol": "dueno_empresa",
                "empresa_id": company_b,
                "activo": True,
            },
            {
                "_id": client_id,
                "nombre": "Cliente B",
                "rol": "cliente",
                "empresa_id": company_b,
                "activo": True,
            },
        ])
        company_collection = SimpleNamespace(
            find_one=AsyncMock(return_value={
                "_id": company_b,
                "activo": True,
            })
        )

        def get_collection(name):
            return {
                "usuarios": collection,
                "empresas": company_collection,
            }.get(name, FakeUsersCollection([]))

        with patch.object(users, "get_collection", side_effect=get_collection):
            owner_result = await users.update_user(
                str(owner_id),
                UsuarioUpdate(nombre="Dueño actualizado"),
                current_user={"rol": "superadmin"},
            )
            client_result = await users.update_user(
                str(client_id),
                UsuarioUpdate(rol="dueno_empresa", empresa_id=company_b),
                current_user={"rol": "superadmin"},
            )

        self.assertEqual(owner_result["mensaje"], "Usuario actualizado")
        self.assertEqual(client_result["mensaje"], "Usuario actualizado")
        self.assertEqual(collection.documents[0]["nombre"], "Dueño actualizado")
        self.assertEqual(collection.documents[1]["rol"], "dueno_empresa")
        self.assertEqual(collection.documents[1]["empresa_id"], company_b)
        company_collection.find_one.assert_awaited_once_with({
            "_id": company_b,
            "activo": True,
        })

    async def test_superadmin_cannot_assign_company_to_superadmin_account(self):
        company_id = ObjectId()
        superadmin_id = ObjectId()
        superadmin = {
            "_id": superadmin_id,
            "rol": "superadmin",
            "activo": True,
        }
        collection = FakeUsersCollection([superadmin])

        with patch.object(users, "get_collection", return_value=collection):
            with self.assertRaises(HTTPException) as raised:
                await users.update_user(
                    str(superadmin_id),
                    UsuarioUpdate(empresa_id=company_id),
                    current_user={"rol": "superadmin"},
                )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertNotIn("empresa_id", superadmin)

    async def test_superadmin_must_explicitly_clear_company_when_promoting_user(self):
        company_id = ObjectId()
        user_id = ObjectId()
        user = {
            "_id": user_id,
            "rol": "cliente",
            "empresa_id": company_id,
            "activo": True,
        }
        collection = FakeUsersCollection([user])

        with patch.object(users, "get_collection", return_value=collection):
            with self.assertRaises(HTTPException) as raised:
                await users.update_user(
                    str(user_id),
                    UsuarioUpdate(rol="superadmin"),
                    current_user={"rol": "superadmin"},
                )

            result = await users.update_user(
                str(user_id),
                UsuarioUpdate(rol="superadmin", empresa_id=None),
                current_user={"rol": "superadmin"},
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(result["mensaje"], "Usuario actualizado")
        self.assertEqual(user["rol"], "superadmin")
        self.assertIsNone(user["empresa_id"])

    async def test_company_owner_cannot_change_client_role(self):
        company_id = ObjectId()
        client_id = ObjectId()
        client = {
            "_id": client_id,
            "rol": "cliente",
            "empresa_id": company_id,
        }
        collection = FakeUsersCollection([client])

        with patch.object(users, "get_collection", return_value=collection):
            with self.assertRaises(HTTPException) as raised:
                await users.update_user(
                    str(client_id),
                    UsuarioUpdate(rol="admin"),
                    current_user={
                        "rol": "dueno_empresa",
                        "empresa_id": company_id,
                    },
                )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(client["rol"], "cliente")

    async def test_cannot_deactivate_last_active_superadmin(self):
        for operation in ("update", "delete"):
            with self.subTest(operation=operation):
                superadmin_id = ObjectId()
                superadmin = {
                    "_id": superadmin_id,
                    "rol": "superadmin",
                    "activo": True,
                }
                collection = FakeUsersCollection([superadmin])
                with patch.object(users, "get_collection", return_value=collection):
                    with self.assertRaises(HTTPException) as raised:
                        if operation == "update":
                            await users.update_user(
                                str(superadmin_id),
                                UsuarioUpdate(activo=False),
                                current_user={"rol": "superadmin"},
                            )
                        else:
                            await users.delete_user(
                                str(superadmin_id),
                                current_user={"rol": "superadmin"},
                            )

                self.assertEqual(raised.exception.status_code, 409)
                self.assertTrue(superadmin["activo"])

    async def test_superadmin_can_deactivate_one_when_another_is_active(self):
        first_id = ObjectId()
        collection = FakeUsersCollection([
            {"_id": first_id, "rol": "superadmin", "activo": True},
            {"_id": ObjectId(), "rol": "superadmin", "activo": True},
        ])

        with (
            patch.object(users, "get_collection", return_value=collection),
            patch.object(
                users,
                "get_database",
                return_value=SimpleNamespace(client=FakeMongoClient()),
            ),
        ):
            result = await users.delete_user(
                str(first_id),
                current_user={"rol": "superadmin"},
            )

        self.assertEqual(
            result["mensaje"],
            "Usuario archivado y desactivado; historial conservado para reportes",
        )
        self.assertFalse(collection.documents[0]["activo"])

    async def test_cannot_remove_last_active_company_owner(self):
        company_id = ObjectId()
        for operation in ("deactivate", "demote", "delete"):
            with self.subTest(operation=operation):
                owner_id = ObjectId()
                owner = {
                    "_id": owner_id,
                    "rol": "dueno_empresa",
                    "empresa_id": company_id,
                    "activo": True,
                }
                collection = FakeUsersCollection([owner])
                company_collection = FakeCompaniesCollection([{"_id": company_id}])

                def get_collection(name):
                    return {
                        "usuarios": collection,
                        "empresas": company_collection,
                    }.get(name, FakeUsersCollection([]))

                with (
                    patch.object(users, "get_collection", side_effect=get_collection),
                    patch.object(
                        users,
                        "get_database",
                        return_value=SimpleNamespace(client=FakeMongoClient()),
                    ),
                ):
                    with self.assertRaises(HTTPException) as raised:
                        if operation == "deactivate":
                            await users.update_user(
                                str(owner_id),
                                UsuarioUpdate(activo=False),
                                current_user={"rol": "superadmin"},
                            )
                        elif operation == "demote":
                            await users.update_user(
                                str(owner_id),
                                UsuarioUpdate(rol="cliente"),
                                current_user={"rol": "superadmin"},
                            )
                        else:
                            await users.delete_user(
                                str(owner_id),
                                current_user={"rol": "superadmin"},
                            )

                self.assertEqual(raised.exception.status_code, 409)
                self.assertEqual(owner["rol"], "dueno_empresa")
                self.assertTrue(owner["activo"])

    async def test_company_owner_can_be_removed_when_another_active_owner_exists(self):
        company_id = ObjectId()
        owner_id = ObjectId()
        collection = FakeUsersCollection([
            {
                "_id": owner_id,
                "rol": "dueno_empresa",
                "empresa_id": company_id,
                "activo": True,
            },
            {
                "_id": ObjectId(),
                "rol": "dueno_empresa",
                "empresa_id": company_id,
                "activo": True,
            },
        ])
        company_collection = FakeCompaniesCollection([{"_id": company_id}])

        def get_collection(name):
            return {
                "usuarios": collection,
                "empresas": company_collection,
            }.get(name, FakeUsersCollection([]))

        with (
            patch.object(users, "get_collection", side_effect=get_collection),
            patch.object(
                users,
                "get_database",
                return_value=SimpleNamespace(client=FakeMongoClient()),
            ),
        ):
            result = await users.delete_user(
                str(owner_id),
                current_user={"rol": "superadmin"},
            )

        self.assertEqual(
            result["mensaje"],
            "Usuario archivado y desactivado; relación empresarial conservada",
        )
        self.assertFalse(collection.documents[0]["activo"])

    async def test_cannot_reassign_last_active_company_owner(self):
        company_a = ObjectId()
        company_b = ObjectId()
        owner_id = ObjectId()
        owner = {
            "_id": owner_id,
            "rol": "dueno_empresa",
            "empresa_id": company_a,
            "activo": True,
        }
        collection = FakeUsersCollection([owner])
        company_collection = FakeCompaniesCollection([
            {"_id": company_a},
            {"_id": company_b, "activo": True},
        ])

        def get_collection(name):
            return {
                "usuarios": collection,
                "empresas": company_collection,
            }.get(name, FakeUsersCollection([]))

        with (
            patch.object(users, "get_collection", side_effect=get_collection),
            patch.object(
                users,
                "get_database",
                return_value=SimpleNamespace(client=FakeMongoClient()),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await users.update_user(
                    str(owner_id),
                    UsuarioUpdate(empresa_id=company_b),
                    current_user={"rol": "superadmin"},
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(owner["empresa_id"], company_a)

    async def test_concurrent_owner_deletions_leave_one_active_owner(self):
        company_id = ObjectId()
        first_id = ObjectId()
        second_id = ObjectId()
        collection = FakeUsersCollection([
            {
                "_id": first_id,
                "rol": "dueno_empresa",
                "empresa_id": company_id,
                "activo": True,
            },
            {
                "_id": second_id,
                "rol": "dueno_empresa",
                "empresa_id": company_id,
                "activo": True,
            },
        ])
        company_collection = FakeCompaniesCollection([{"_id": company_id}])
        client = FakeMongoClient()

        def get_collection(name):
            return {
                "usuarios": collection,
                "empresas": company_collection,
            }.get(name, FakeUsersCollection([]))

        with (
            patch.object(users, "get_collection", side_effect=get_collection),
            patch.object(
                users,
                "get_database",
                return_value=SimpleNamespace(client=client),
            ),
        ):
            results = await asyncio.gather(
                users.delete_user(
                    str(first_id),
                    current_user={"rol": "superadmin"},
                ),
                users.delete_user(
                    str(second_id),
                    current_user={"rol": "superadmin"},
                ),
                return_exceptions=True,
            )

        self.assertEqual(
            sum(isinstance(result, HTTPException) for result in results),
            1,
        )
        self.assertEqual(
            sum(user.get("activo", True) is not False for user in collection.documents),
            1,
        )

    async def test_unknown_owner_transaction_commit_result_returns_conflict(self):
        company_id = ObjectId()
        owner_id = ObjectId()
        owners = FakeUsersCollection([
            {
                "_id": owner_id,
                "rol": "dueno_empresa",
                "empresa_id": company_id,
                "activo": True,
            },
            {
                "_id": ObjectId(),
                "rol": "dueno_empresa",
                "empresa_id": company_id,
                "activo": True,
            },
        ])
        company_collection = FakeCompaniesCollection([{"_id": company_id}])
        database = SimpleNamespace(
            client=SimpleNamespace(
                start_session=AsyncMock(return_value=UnknownCommitMongoSession())
            )
        )

        with (
            patch.object(
                users,
                "get_collection",
                return_value=company_collection,
            ),
            patch.object(users, "get_database", return_value=database),
            self.assertRaises(HTTPException) as raised,
        ):
            await users._update_company_owner_atomically(
                owners,
                {"_id": owner_id},
                company_id,
                {"activo": False},
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("verifica el estado", raised.exception.detail)

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
        }, {
            "_id": ObjectId(),
            "rol": "superadmin",
            "activo": True,
        }])

        with patch.object(users, "get_collection", return_value=collection):
            with self.assertRaises(HTTPException) as raised:
                await users.delete_user(
                    str(admin_id),
                    current_user={"rol": "admin"},
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(len(collection.documents), 2)

    async def test_deleting_client_deactivates_account_and_preserves_history(self):
        user_id = ObjectId()
        collection = FakeUsersCollection([{
            "_id": user_id,
            "rol": "cliente",
            "activo": True,
            "total_cargas": 7,
        }])

        with (
            patch.object(users, "get_collection", return_value=collection),
            patch.object(
                users,
                "get_database",
                return_value=SimpleNamespace(client=FakeMongoClient()),
            ),
        ):
            result = await users.delete_user(str(user_id), current_user={})

        self.assertEqual(
            result["mensaje"],
            "Usuario archivado y desactivado; historial conservado para reportes",
        )
        self.assertEqual(len(collection.documents), 1)
        self.assertFalse(collection.documents[0]["activo"])
        self.assertTrue(collection.documents[0]["archivado"])
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
