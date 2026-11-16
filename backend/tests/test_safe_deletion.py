import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import HTTPException

from app.models.nfc_card import NFCCardUpdate
from app.models.user import UsuarioUpdate
from app.models.vehicle import VehiculoUpdate
from app.routers import nfc_cards, users, vehicles


class MemoryCursor:
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
                yield copy.deepcopy(document)

        return iterate()


class MemoryCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])

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

    def find(self, query, projection=None, **kwargs):
        return MemoryCursor([
            document
            for document in self.documents
            if self._matches(document, query)
        ])

    async def find_one(self, query, **kwargs):
        return next(
            (
                copy.deepcopy(document)
                for document in self.documents
                if self._matches(document, query)
            ),
            None,
        )

    async def count_documents(self, query, **kwargs):
        return sum(
            self._matches(document, query)
            for document in self.documents
        )

    async def update_one(self, query, update, **kwargs):
        for document in self.documents:
            if self._matches(document, query):
                document.update(copy.deepcopy(update.get("$set", {})))
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    async def update_many(self, query, update, **kwargs):
        matched_count = 0
        for document in self.documents:
            if self._matches(document, query):
                document.update(copy.deepcopy(update.get("$set", {})))
                matched_count += 1
        return SimpleNamespace(matched_count=matched_count)

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


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def start_transaction(self):
        return FakeTransaction()


class FakeMongoClient:
    async def start_session(self):
        return FakeSession()


class SafeDeletionTests(unittest.IsolatedAsyncioTestCase):
    def _collections(self, **documents):
        collections = {
            name: MemoryCollection(items)
            for name, items in documents.items()
        }

        def get_collection(name):
            return collections.setdefault(name, MemoryCollection())

        return collections, get_collection

    def _database(self):
        return SimpleNamespace(client=FakeMongoClient())

    async def test_user_without_history_is_removed_with_unreferenced_assets(self):
        user_id = ObjectId()
        vehicle_id = ObjectId()
        card_id = ObjectId()
        collections, get_collection = self._collections(
            usuarios=[{"_id": user_id, "rol": "cliente", "activo": True}],
            vehiculos=[{"_id": vehicle_id, "usuario_id": user_id}],
            nfc_cards=[{"_id": card_id, "usuario_id": user_id}],
        )

        with (
            patch.object(users, "get_collection", side_effect=get_collection),
            patch.object(users, "get_database", return_value=self._database()),
        ):
            result = await users.delete_user(str(user_id), current_user={})

        self.assertIn("eliminado permanentemente", result["mensaje"])
        self.assertEqual(collections["usuarios"].documents, [])
        self.assertEqual(collections["vehiculos"].documents, [])
        self.assertEqual(collections["nfc_cards"].documents, [])

    async def test_user_with_asset_session_history_is_archived_without_changing_session(self):
        user_id = ObjectId()
        company_id = ObjectId()
        vehicle_id = ObjectId()
        card_id = ObjectId()
        session = {
            "_id": ObjectId(),
            "empresa_id": company_id,
            "vehiculo_id": vehicle_id,
            "nfc_card_id": card_id,
            "estado": "finalizada",
            "energia_consumida_kwh": 18.5,
        }
        collections, get_collection = self._collections(
            usuarios=[{
                "_id": user_id,
                "rol": "cliente",
                "empresa_id": company_id,
                "activo": True,
            }],
            vehiculos=[{
                "_id": vehicle_id,
                "usuario_id": user_id,
                "activo": True,
            }],
            nfc_cards=[{
                "_id": card_id,
                "usuario_id": user_id,
                "uid": "UID-HISTORICO",
                "estado": "activa",
            }],
            sesiones=[session],
        )
        original_session = copy.deepcopy(session)

        with (
            patch.object(users, "get_collection", side_effect=get_collection),
            patch.object(users, "get_database", return_value=self._database()),
            patch.object(
                users,
                "get_finalized_session_count",
                new=AsyncMock(return_value=1),
            ),
        ):
            result = await users.delete_user(str(user_id), current_user={})
            listed = await users.list_users(current_user={})
            with self.assertRaises(HTTPException) as update_error:
                await users.update_user(
                    str(user_id),
                    UsuarioUpdate(activo=True),
                    current_user={},
                )

        self.assertIn("historial conservado", result["mensaje"])
        self.assertEqual(update_error.exception.status_code, 409)
        self.assertTrue(collections["usuarios"].documents[0]["archivado"])
        self.assertFalse(collections["usuarios"].documents[0]["activo"])
        self.assertTrue(collections["vehiculos"].documents[0]["archivado"])
        self.assertFalse(collections["vehiculos"].documents[0]["activo"])
        self.assertTrue(collections["nfc_cards"].documents[0]["archivado"])
        self.assertEqual(
            collections["nfc_cards"].documents[0]["estado"],
            "bloqueada",
        )
        self.assertEqual(collections["sesiones"].documents, [original_session])
        self.assertEqual(listed, [])

    async def test_vehicle_delete_is_permanent_without_history_and_archived_with_history(self):
        company_id = ObjectId()
        unreferenced_id = ObjectId()
        historical_id = ObjectId()
        historical_session = {
            "_id": ObjectId(),
            "vehiculo_id": historical_id,
            "estado": "finalizada",
        }
        collections, get_collection = self._collections(
            vehiculos=[
                {"_id": unreferenced_id, "empresa_id": company_id},
                {"_id": historical_id, "empresa_id": company_id, "activo": True},
            ],
            sesiones=[historical_session],
        )
        current_user = {"rol": "dueno_empresa", "empresa_id": company_id}

        with (
            patch.object(vehicles, "get_collection", side_effect=get_collection),
            patch.object(vehicles, "get_database", return_value=self._database()),
        ):
            deleted = await vehicles.delete_vehicle(
                str(unreferenced_id),
                current_user=current_user,
            )
            archived = await vehicles.delete_vehicle(
                str(historical_id),
                current_user=current_user,
            )
            with self.assertRaises(HTTPException) as update_error:
                await vehicles.update_vehicle(
                    str(historical_id),
                    VehiculoUpdate(marca="Marca nueva"),
                    current_user=current_user,
                )

        self.assertIn("permanentemente", deleted["mensaje"])
        self.assertIn("historial conservado", archived["mensaje"])
        self.assertEqual(update_error.exception.status_code, 404)
        self.assertEqual(len(collections["vehiculos"].documents), 1)
        self.assertTrue(collections["vehiculos"].documents[0]["archivado"])
        self.assertEqual(collections["sesiones"].documents, [historical_session])

    async def test_nfc_delete_is_permanent_without_history_and_archived_with_history(self):
        company_id = ObjectId()
        unreferenced_id = ObjectId()
        historical_id = ObjectId()
        historical_session = {
            "_id": ObjectId(),
            "nfc_card_id": historical_id,
            "estado": "finalizada",
        }
        collections, get_collection = self._collections(
            nfc_cards=[
                {
                    "_id": unreferenced_id,
                    "empresa_id": company_id,
                    "uid": "UID-SIN-HISTORIAL",
                    "estado": "activa",
                },
                {
                    "_id": historical_id,
                    "empresa_id": company_id,
                    "uid": "UID-HISTORICO",
                    "estado": "activa",
                },
            ],
            sesiones=[historical_session],
        )
        current_user = {"rol": "dueno_empresa", "empresa_id": company_id}

        with (
            patch.object(nfc_cards, "get_collection", side_effect=get_collection),
            patch.object(
                nfc_cards,
                "get_database",
                return_value=self._database(),
            ),
        ):
            deleted = await nfc_cards.delete_nfc_card(
                str(unreferenced_id),
                current_user=current_user,
            )
            archived = await nfc_cards.delete_nfc_card(
                str(historical_id),
                current_user=current_user,
            )
            with self.assertRaises(HTTPException) as update_error:
                await nfc_cards.update_nfc_card(
                    str(historical_id),
                    NFCCardUpdate(estado="activa"),
                    current_user=current_user,
                )

        self.assertIn("permanentemente", deleted["mensaje"])
        self.assertIn("historial conservado", archived["mensaje"])
        self.assertEqual(update_error.exception.status_code, 404)
        self.assertEqual(len(collections["nfc_cards"].documents), 1)
        card = collections["nfc_cards"].documents[0]
        self.assertTrue(card["archivado"])
        self.assertEqual(card["estado"], "bloqueada")
        self.assertEqual(card["uid"], "UID-HISTORICO")
        self.assertEqual(collections["sesiones"].documents, [historical_session])
