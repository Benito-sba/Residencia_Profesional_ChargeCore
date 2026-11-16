import unittest
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException

from app.routers import sessions


class Cursor:
    def __init__(self, documents):
        self.documents = list(documents)

    def sort(self, *_args):
        return self

    def skip(self, count):
        self.documents = self.documents[count:]
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __aiter__(self):
        self._iterator = iter(self.documents)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


class SessionCollection:
    def __init__(self, documents):
        self.documents = documents
        self.find_queries = []

    @staticmethod
    def _matches(document, query):
        for key, value in query.items():
            if isinstance(value, dict) and "$in" in value:
                if document.get(key) not in value["$in"]:
                    return False
            elif document.get(key) != value:
                return False
        return True

    def find(self, query, *_args):
        self.find_queries.append(query)
        return Cursor([
            document
            for document in self.documents
            if self._matches(document, query)
        ])

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


class CompanySessionIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_meter_values_serialize_company_object_ids(self):
        company_id = ObjectId()
        transaction_id = 101
        sessions_collection = SessionCollection([{
            "_id": ObjectId(),
            "empresa_id": company_id,
            "transaction_id": transaction_id,
            "charge_point_id": "CP-A",
        }])
        meter_values = SessionCollection([{
            "_id": ObjectId(),
            "empresa_id": company_id,
            "transaction_id": transaction_id,
            "charge_point_id": "CP-A",
        }])
        collections = {
            "sesiones": sessions_collection,
            "meter_values": meter_values,
        }

        with patch.object(
            sessions,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            result = await sessions.get_session_meter_values(
                transaction_id,
                current_user={"rol": "dueno_empresa", "empresa_id": company_id},
            )

        self.assertEqual(result[0]["empresa_id"], str(company_id))

    async def test_user_sessions_serialize_vehicle_and_card_object_ids(self):
        company_id = ObjectId()
        user_id = ObjectId()
        vehicle_id = ObjectId()
        card_id = ObjectId()
        session_id = ObjectId()
        collection = SessionCollection([{
            "_id": session_id,
            "empresa_id": company_id,
            "usuario_id": user_id,
            "vehiculo_id": vehicle_id,
            "nfc_card_id": card_id,
            "estado": "finalizada",
            "energia_consumida_kwh": 3.2,
        }])

        with patch.object(sessions, "get_collection", return_value=collection):
            result = await sessions.get_user_sessions(
                str(user_id),
                current_user={"rol": "dueno_empresa", "empresa_id": company_id},
            )

        self.assertEqual(result["sesiones"][0]["vehiculo_id"], str(vehicle_id))
        self.assertEqual(result["sesiones"][0]["nfc_card_id"], str(card_id))

    async def test_owner_lists_only_sessions_with_company_assignment(self):
        company_a = ObjectId()
        company_b = ObjectId()
        own_session = {
            "_id": ObjectId(),
            "empresa_id": company_a,
            "transaction_id": 101,
            "estado": "finalizada",
        }
        sessions_collection = SessionCollection([
            own_session,
            {
                "_id": ObjectId(),
                "empresa_id": company_b,
                "transaction_id": 202,
                "estado": "finalizada",
            },
            {
                "_id": ObjectId(),
                "transaction_id": 303,
                "estado": "finalizada",
            },
        ])
        collections = {
            "sesiones": sessions_collection,
            "usuarios": SessionCollection([]),
            "vehiculos": SessionCollection([]),
            "nfc_cards": SessionCollection([]),
            "cargadores": SessionCollection([]),
        }

        with patch.object(
            sessions,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            result = await sessions.list_sessions(
                current_user={
                    "rol": "dueno_empresa",
                    "empresa_id": company_a,
                }
            )

        self.assertEqual([item["transaction_id"] for item in result], [101])
        self.assertEqual(
            sessions_collection.find_queries[0]["empresa_id"],
            company_a,
        )

    async def test_owner_cannot_fetch_other_company_session_by_transaction(self):
        company_a = ObjectId()
        company_b = ObjectId()
        collection = SessionCollection([{
            "_id": ObjectId(),
            "empresa_id": company_b,
            "transaction_id": 202,
        }])

        with patch.object(
            sessions,
            "get_collection",
            return_value=collection,
        ):
            with self.assertRaises(HTTPException) as raised:
                await sessions.get_session_by_transaction(
                    202,
                    current_user={
                        "rol": "dueno_empresa",
                        "empresa_id": company_a,
                    },
                )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(collection.find_queries[0]["empresa_id"], company_a)

    async def test_superadmin_can_fetch_sessions_globally(self):
        session = {
            "_id": ObjectId(),
            "empresa_id": ObjectId(),
            "transaction_id": 202,
        }
        collection = SessionCollection([session])

        with patch.object(
            sessions,
            "get_collection",
            return_value=collection,
        ):
            result = await sessions.get_session_by_transaction(
                202,
                current_user={"rol": "superadmin"},
            )

        self.assertEqual(result["transaction_id"], 202)
        self.assertNotIn("empresa_id", collection.find_queries[0])


if __name__ == "__main__":
    unittest.main()
