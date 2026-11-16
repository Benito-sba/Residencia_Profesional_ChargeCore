import unittest
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException

from app.models.nfc_card import NFCCardCreate
from app.routers import nfc_cards


class MemoryCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])
        self.find_queries = []
        self.inserted = []

    @staticmethod
    def _matches(document, query):
        for key, value in query.items():
            actual = document.get(key)
            if isinstance(value, dict):
                if "$ne" in value and actual == value["$ne"]:
                    return False
            elif actual != value:
                return False
        return True

    async def find_one(self, query):
        self.find_queries.append(query)
        return next(
            (
                document
                for document in self.documents
                if self._matches(document, query)
            ),
            None,
        )

    def find(self, query):
        self.find_queries.append(query)
        return Cursor([
            document
            for document in self.documents
            if self._matches(document, query)
        ])

    async def insert_one(self, document):
        self.inserted.append(document)
        return type("InsertResult", (), {"inserted_id": ObjectId()})()


class Cursor:
    def __init__(self, documents):
        self.documents = documents

    def __aiter__(self):
        self._iterator = iter(self.documents)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


class CompanyNFCIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_lists_only_company_cards_and_hides_unassigned_cards(self):
        company_a = ObjectId()
        company_b = ObjectId()
        cards = MemoryCollection([
            {"_id": ObjectId(), "uid": "CARD-A", "empresa_id": company_a},
            {"_id": ObjectId(), "uid": "CARD-B", "empresa_id": company_b},
            {"_id": ObjectId(), "uid": "CARD-OLD"},
            {
                "_id": ObjectId(),
                "uid": "CARD-ARCHIVED",
                "empresa_id": company_a,
                "archivado": True,
            },
        ])

        with patch.object(nfc_cards, "get_collection", return_value=cards):
            result = await nfc_cards.list_nfc_cards({
                "rol": "dueno_empresa",
                "empresa_id": company_a,
            })

        self.assertEqual([card["uid"] for card in result], ["CARD-A"])
        self.assertEqual(cards.find_queries[0]["empresa_id"], company_a)
        self.assertEqual(cards.find_queries[0]["archivado"], {"$ne": True})

    async def test_owner_cannot_assign_card_to_client_from_another_company(self):
        company_a = ObjectId()
        company_b = ObjectId()
        user_b = ObjectId()
        users = MemoryCollection([{
            "_id": user_b,
            "empresa_id": company_b,
            "rol": "cliente",
            "activo": True,
        }])
        cards = MemoryCollection()
        companies = MemoryCollection([
            {"_id": company_a, "activo": True},
            {"_id": company_b, "activo": True},
        ])
        collections = {
            "usuarios": users,
            "nfc_cards": cards,
            "empresas": companies,
        }

        with patch.object(
            nfc_cards,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            with self.assertRaises(HTTPException) as raised:
                await nfc_cards.create_nfc_card(
                    NFCCardCreate(uid="CARD-B", usuario_id=user_b),
                    current_user={
                        "rol": "dueno_empresa",
                        "empresa_id": company_a,
                    },
                )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(cards.inserted, [])

    async def test_owner_created_card_inherits_company_from_client(self):
        company_a = ObjectId()
        user_a = ObjectId()
        users = MemoryCollection([{
            "_id": user_a,
            "empresa_id": company_a,
            "rol": "cliente",
            "activo": True,
        }])
        cards = MemoryCollection()
        companies = MemoryCollection([{"_id": company_a, "activo": True}])
        collections = {
            "usuarios": users,
            "nfc_cards": cards,
            "empresas": companies,
        }

        with patch.object(
            nfc_cards,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            await nfc_cards.create_nfc_card(
                NFCCardCreate(uid="CARD-A", usuario_id=user_a),
                current_user={
                    "rol": "dueno_empresa",
                    "empresa_id": company_a,
                },
            )

        self.assertEqual(cards.inserted[0]["empresa_id"], company_a)


if __name__ == "__main__":
    unittest.main()
