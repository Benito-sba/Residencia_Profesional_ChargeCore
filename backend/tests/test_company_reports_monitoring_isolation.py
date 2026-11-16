import unittest
from unittest.mock import AsyncMock, patch

from bson import ObjectId

from app.routers import dashboard, monitoring, reports


class Cursor:
    def __init__(self, documents=None):
        self.documents = list(documents or [])

    def sort(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def __aiter__(self):
        self._iterator = iter(self.documents)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


class MemoryCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])
        self.find_query = None

    def find(self, query, *_args):
        self.find_query = query
        return Cursor([
            document
            for document in self.documents
            if all(
                (
                    document.get(key) in value["$in"]
                    if isinstance(value, dict) and "$in" in value
                    else document.get(key) == value
                )
                for key, value in query.items()
            )
        ])


class CompanyReportsMonitoringIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_events_serialize_nested_object_ids(self):
        company_id = ObjectId()
        user_id = ObjectId()
        events = MemoryCollection([{
            "_id": ObjectId(),
            "empresa_id": company_id,
            "details": {"usuario_id": user_id},
        }])

        with patch.object(dashboard, "get_collection", return_value=events):
            result = await dashboard.get_recent_events(
                limit=5,
                current_user={"rol": "superadmin"},
            )

        self.assertEqual(result[0]["empresa_id"], str(company_id))
        self.assertEqual(result[0]["details"]["usuario_id"], str(user_id))

    async def test_monitoring_messages_serialize_company_object_ids(self):
        company_id = ObjectId()
        messages = MemoryCollection([{
            "_id": ObjectId(),
            "charge_point_id": "CP-A",
            "empresa_id": company_id,
        }])

        with patch.object(monitoring, "get_collection", return_value=messages):
            result = await monitoring.list_ocpp_messages(
                "CP-A",
                action=None,
                message_type_name=None,
                limit=100,
                current_user={"rol": "superadmin"},
            )

        self.assertEqual(result[0]["empresa_id"], str(company_id))

    async def test_report_sessions_serialize_referenced_object_ids(self):
        company_id = ObjectId()
        user_id = ObjectId()
        vehicle_id = ObjectId()
        collection = MemoryCollection([{
            "_id": ObjectId(),
            "empresa_id": company_id,
            "usuario_id": user_id,
            "vehiculo_id": vehicle_id,
            "estado": "finalizada",
            "energia_consumida_kwh": 1.5,
        }])

        with (
            patch.object(reports, "get_collection", return_value=collection),
            patch.object(
                reports,
                "_resolve_user_name",
                new=AsyncMock(return_value="Cliente"),
            ),
            patch.object(
                reports,
                "_resolve_vehicle_name",
                new=AsyncMock(return_value="Vehículo"),
            ),
            patch.object(
                reports,
                "_resolve_nfc",
                new=AsyncMock(return_value="Tarjeta"),
            ),
        ):
            result = await reports.report_sessions(
                current_user={"rol": "dueno_empresa", "empresa_id": company_id}
            )

        self.assertEqual(result[0]["empresa_id"], str(company_id))
        self.assertEqual(result[0]["usuario_id"], str(user_id))
        self.assertEqual(result[0]["vehiculo_id"], str(vehicle_id))

    async def test_dashboard_activity_query_excludes_unscoped_history(self):
        company_id = ObjectId()
        query = dashboard._activity_query(
            {"rol": "dueno_empresa", "empresa_id": company_id},
            {"nivel": "error"},
            ["CP-A"],
        )

        self.assertEqual(
            query,
            {"nivel": "error", "empresa_id": company_id},
        )

    async def test_report_sessions_filter_by_authenticated_company(self):
        company_id = ObjectId()
        collection = MemoryCollection()

        with patch.object(
            reports,
            "get_collection",
            return_value=collection,
        ):
            result = await reports.report_sessions(
                current_user={
                    "rol": "dueno_empresa",
                    "empresa_id": company_id,
                }
            )

        self.assertEqual(result, [])
        self.assertEqual(collection.find_query["empresa_id"], company_id)

    async def test_owner_ocpp_message_query_is_limited_to_company_chargers(self):
        company_id = ObjectId()
        chargers = MemoryCollection([{
            "charge_point_id": "CP-A",
            "empresa_id": company_id,
            "activo": True,
        }])
        messages = MemoryCollection()
        collections = {
            "cargadores": chargers,
            "cargador_eliminaciones": MemoryCollection(),
            "ocpp_messages": messages,
        }

        with patch.object(
            monitoring,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            result = await monitoring.list_ocpp_messages(
                "CP-A",
                limit=100,
                current_user={
                    "rol": "dueno_empresa",
                    "empresa_id": company_id,
                },
            )

        self.assertEqual(result, [])
        query = messages.find_query
        self.assertEqual(query["$and"][0]["charge_point_id"], "CP-A")
        self.assertEqual(
            query["$and"][1]["$and"][0]["charge_point_id"]["$in"],
            ["CP-A"],
        )
        self.assertEqual(
            query["$and"][1]["$and"][1],
            {"empresa_id": company_id},
        )

    async def test_owner_logs_are_limited_to_company_or_verified_chargers(self):
        company_id = ObjectId()
        chargers = MemoryCollection([{
            "charge_point_id": "CP-A",
            "empresa_id": company_id,
            "activo": True,
        }])
        logs = MemoryCollection()
        collections = {
            "cargadores": chargers,
            "cargador_eliminaciones": MemoryCollection(),
            "logs": logs,
        }

        with patch.object(
            monitoring,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            result = await monitoring.list_alerts(
                nivel=None,
                modulo=None,
                dias=7,
                limit=50,
                current_user={
                    "rol": "dueno_empresa",
                    "empresa_id": company_id,
                }
            )

        self.assertEqual(result, [])
        self.assertEqual(
            logs.find_query["$and"][1],
            {"empresa_id": company_id},
        )

    async def test_owner_history_fallback_excludes_retired_charge_point_ids(self):
        company_id = ObjectId()
        chargers = MemoryCollection([{
            "charge_point_id": "CP-REUSED",
            "empresa_id": company_id,
            "activo": True,
        }])
        deleted = MemoryCollection([{"charge_point_id": "CP-REUSED"}])
        logs = MemoryCollection()
        collections = {
            "cargadores": chargers,
            "cargador_eliminaciones": deleted,
            "logs": logs,
        }

        with patch.object(
            monitoring,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            await monitoring.list_alerts(
                nivel=None,
                modulo=None,
                dias=7,
                limit=50,
                current_user={"rol": "dueno_empresa", "empresa_id": company_id},
            )

        self.assertEqual(
            logs.find_query["$and"][1],
            {"empresa_id": company_id},
        )


if __name__ == "__main__":
    unittest.main()
