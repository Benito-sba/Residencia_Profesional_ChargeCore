import unittest
from unittest.mock import Mock, patch

from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import require_company_admin_access
from app.routers import dashboard


class AggregateCursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length):
        return self.rows


class DashboardAnalyticsTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(dashboard.router)
        app.dependency_overrides[require_company_admin_access] = lambda: {
            "rol": "superadmin",
        }
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_consumption_history_returns_daily_rows_and_totals(self):
        sessions = Mock()
        sessions.aggregate.return_value = AggregateCursor([
            {
                "_id": {"year": 2026, "month": 10, "day": 1},
                "energia_kwh": 30,
                "sesiones": 2,
            },
            {
                "_id": {"year": 2026, "month": 10, "day": 3},
                "energia_kwh": 12.5,
                "sesiones": 1,
            },
        ])

        with patch.object(dashboard, "get_collection", return_value=sessions):
            response = self.client.get(
                "/api/dashboard/historial-consumo",
                params={"fecha_inicio": "2026-10-01", "fecha_fin": "2026-10-03"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "rows": [
                {
                    "fecha": "2026-10-01",
                    "sesiones": 2,
                    "energia_kwh": 30,
                    "promedio_kwh_por_sesion": 15,
                },
                {
                    "fecha": "2026-10-02",
                    "sesiones": 0,
                    "energia_kwh": 0,
                    "promedio_kwh_por_sesion": 0,
                },
                {
                    "fecha": "2026-10-03",
                    "sesiones": 1,
                    "energia_kwh": 12.5,
                    "promedio_kwh_por_sesion": 12.5,
                },
            ],
            "total_energia_kwh": 42.5,
            "total_sesiones": 3,
            "promedio_diario_kwh": 14.17,
            "dias": 3,
        })

    def test_consumption_history_rejects_reversed_date_range(self):
        with patch.object(dashboard, "get_collection") as get_collection:
            response = self.client.get(
                "/api/dashboard/historial-consumo",
                params={"fecha_inicio": "2026-10-03", "fecha_fin": "2026-10-01"},
            )

        self.assertEqual(response.status_code, 422)
        get_collection.assert_not_called()

    def test_consumption_history_scopes_company_sessions(self):
        sessions = Mock()
        sessions.aggregate.return_value = AggregateCursor([])
        company_id = ObjectId()
        self.client.app.dependency_overrides[require_company_admin_access] = (
            lambda: {
                "rol": "dueno_empresa",
                "empresa_id": company_id,
            }
        )

        with patch.object(dashboard, "get_collection", return_value=sessions):
            response = self.client.get(
                "/api/dashboard/historial-consumo",
                params={"fecha_inicio": "2026-10-01", "fecha_fin": "2026-10-01"},
            )

        self.assertEqual(response.status_code, 200)
        pipeline = sessions.aggregate.call_args.args[0]
        self.assertEqual(pipeline[0]["$match"]["empresa_id"], company_id)
