import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth
from app.auth import (
    require_company_admin_access,
    require_company_member,
    require_admin,
    require_read_access,
    require_superadmin,
    require_user_management_access,
)
from app.routers import chargers, nfc_cards, reports, rewards, sessions, users, vehicles
from app.validation import parse_object_id


class EmptyAsyncCursor:
    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def __aiter__(self):
        async def iterate():
            if False:
                yield None

        return iterate()


class AsyncRowsCursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def __aiter__(self):
        async def iterate():
            for row in self.rows:
                yield row

        return iterate()


class FakeTransactionContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class FakeMongoSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def start_transaction(self):
        return FakeTransactionContext()


class FakeMongoClient:
    async def start_session(self):
        return FakeMongoSession()


class ApiValidationTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        for router_module in (
            users,
            vehicles,
            nfc_cards,
            chargers,
            sessions,
            rewards,
            reports,
        ):
            self.app.include_router(router_module.router)
        self.app.dependency_overrides[require_admin] = lambda: {"rol": "admin"}
        self.app.dependency_overrides[require_company_admin_access] = lambda: {
            "rol": "superadmin",
        }
        self.app.dependency_overrides[require_company_member] = lambda: {
            "rol": "superadmin",
        }
        self.app.dependency_overrides[require_read_access] = lambda: {
            "rol": "superadmin",
        }
        self.app.dependency_overrides[require_superadmin] = lambda: {
            "rol": "superadmin",
        }
        self.app.dependency_overrides[require_user_management_access] = (
            lambda: {"rol": "admin"}
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()

    def test_reward_document_serializer_converts_nested_object_ids(self):
        user_id = ObjectId()
        reward_id = ObjectId()
        rule_id = ObjectId()
        serialized = rewards._serialize_document({
            "_id": reward_id,
            "usuario_id": user_id,
            "recompensa_regla_id": rule_id,
            "stop_closure": {"reward_id": reward_id},
            "references": [user_id, {"id": rule_id}],
        })

        self.assertEqual(serialized["_id"], str(reward_id))
        self.assertEqual(serialized["usuario_id"], str(user_id))
        self.assertEqual(serialized["recompensa_regla_id"], str(rule_id))
        self.assertEqual(serialized["stop_closure"]["reward_id"], str(reward_id))
        self.assertEqual(
            serialized["references"],
            [str(user_id), {"id": str(rule_id)}],
        )

    def test_deleting_nfc_card_blocks_it_without_removing_its_uid(self):
        card_id = ObjectId()
        card = {"_id": card_id, "uid": "CARD-0001", "estado": "activa"}
        cards = Mock()
        cards.find_one = AsyncMock(return_value=card)
        cards.update_one = AsyncMock(return_value=Mock(matched_count=1))

        with patch.object(nfc_cards, "get_collection", return_value=cards):
            response = asyncio.run(
                nfc_cards.delete_nfc_card(
                    str(card_id),
                    current_user={"rol": "superadmin"},
                )
            )

        self.assertIn("historial conservado", response["mensaje"])
        update = cards.update_one.await_args.args[1]["$set"]
        self.assertEqual(update["estado"], "bloqueada")
        self.assertIn("fecha_baja", update)
        cards.delete_one.assert_not_called()

    def test_invalid_object_ids_return_422_without_database_access(self):
        cases = (
            ("get", "/api/usuarios/not-an-id"),
            ("get", "/api/vehiculos/usuario/not-an-id"),
            ("get", "/api/nfc-cards/usuario/not-an-id"),
            ("get", "/api/cargadores/not-an-id"),
            ("get", "/api/sesiones/usuario/not-an-id"),
            ("get", "/api/sesiones/?usuario_id=not-an-id"),
            ("get", "/api/recompensas/usuario/not-an-id"),
            ("get", "/api/recompensas/?usuario_id=not-an-id"),
            ("get", "/api/reportes/sesiones?usuario_id=not-an-id"),
        )
        patches = [
            patch.object(module, "get_collection")
            for module in (
                users,
                vehicles,
                nfc_cards,
                chargers,
                sessions,
                rewards,
                reports,
            )
        ]

        for context in patches:
            context.start()
        self.addCleanup(lambda: [context.stop() for context in reversed(patches)])

        for method, path in cases:
            with self.subTest(path=path):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 422, response.text)
        for context in patches:
            collection = context.target.get_collection.return_value
            collection.find_one.assert_not_called()
            collection.find.assert_not_called()

    def test_sessions_report_http_route_returns_company_scoped_report(self):
        company_id = ObjectId()
        foreign_company_id = ObjectId()
        session_id = ObjectId()
        sessions = [
            {
                "_id": session_id,
                "empresa_id": company_id,
                "estado": "finalizada",
                "charge_point_id": "CP-OWN",
                "fecha_inicio": datetime(2026, 1, 2, 8, 30),
                "fecha_fin": datetime(2026, 1, 2, 9, 0),
                "duracion_minutos": 30,
                "energia_consumida_kwh": 1.236,
            },
            {
                "_id": ObjectId(),
                "empresa_id": foreign_company_id,
                "estado": "finalizada",
                "charge_point_id": "CP-FOREIGN",
            },
        ]
        observed_queries = []

        def find_sessions(query):
            observed_queries.append(query)
            return AsyncRowsCursor([
                dict(session)
                for session in sessions
                if all(
                    session.get(key) == value
                    for key, value in query.items()
                )
            ])

        sessions_collection = Mock()
        sessions_collection.find = Mock(side_effect=find_sessions)
        companies_collection = Mock()
        companies_collection.find_one = AsyncMock(return_value={
            "_id": company_id,
            "activo": True,
        })
        owner = {"_id": ObjectId(), "rol": "dueno_empresa", "empresa_id": company_id}
        self.app.dependency_overrides.pop(require_read_access, None)
        self.app.dependency_overrides.pop(require_company_admin_access, None)

        with (
            patch.object(auth, "get_current_user", new=AsyncMock(return_value=owner)),
            patch.object(auth, "get_collection", return_value=companies_collection),
            patch.object(
                reports,
                "get_collection",
                return_value=sessions_collection,
            ),
        ):
            response = self.client.get(
                "/api/reportes/sesiones",
                headers={"Authorization": "Bearer test-token"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["_id"], str(session_id))
        self.assertEqual(report[0]["empresa_id"], str(company_id))
        self.assertEqual(report[0]["charge_point_id"], "CP-OWN")
        self.assertEqual(report[0]["fecha_inicio"], "2026-01-02 08:30:00")
        self.assertEqual(report[0]["duracion_minutos"], 30)
        self.assertEqual(report[0]["energia_consumida_kwh"], 1.24)
        self.assertEqual(
            observed_queries[0],
            {"estado": "finalizada", "empresa_id": company_id},
        )

        client_user = {
            "_id": ObjectId(),
            "rol": "cliente",
            "empresa_id": company_id,
        }
        with (
            patch.object(
                auth,
                "get_current_user",
                new=AsyncMock(return_value=client_user),
            ),
            patch.object(
                reports,
                "get_collection",
                side_effect=AssertionError("Unauthorized role accessed reports"),
            ),
        ):
            denied_response = self.client.get(
                "/api/reportes/sesiones",
                headers={"Authorization": "Bearer test-token"},
            )

        self.assertEqual(denied_response.status_code, 403, denied_response.text)

    def test_invalid_user_id_without_authentication_returns_401(self):
        self.app.dependency_overrides.pop(require_user_management_access)

        with patch.object(
            users,
            "get_collection",
            side_effect=AssertionError("Database must not be accessed"),
        ):
            response = self.client.get("/api/usuarios/not-an-id")

        self.assertEqual(response.status_code, 401, response.text)

    def test_charger_registration_persists_supported_ocpp_version(self):
        collection = Mock()
        collection.insert_one = AsyncMock(
            return_value=Mock(inserted_id=ObjectId())
        )
        payload = {
            "charge_point_id": "CP-201",
            "nombre": "Cargador OCPP 2.0.1",
            "ubicacion": "Estacionamiento",
            "ocpp_version": "ocpp2.0.1",
            "ocpp_password": "local-test-password",
        }

        with patch.object(chargers, "get_collection", return_value=collection):
            collection.find_one = AsyncMock(return_value=None)
            response = self.client.post("/api/cargadores/", json=payload)

        self.assertEqual(response.status_code, 201, response.text)
        stored_charger = collection.insert_one.await_args.args[0]
        self.assertEqual(stored_charger["ocpp_version"], "ocpp2.0.1")
        self.assertNotIn("ocpp_password", stored_charger)
        self.assertNotEqual(
            stored_charger["ocpp_password_hash"],
            payload["ocpp_password"],
        )

        with patch.object(
            chargers,
            "get_collection",
            side_effect=AssertionError("Database must not be accessed"),
        ):
            invalid_response = self.client.post(
                "/api/cargadores/",
                json={**payload, "ocpp_version": "ocpp9.9"},
            )
        self.assertEqual(invalid_response.status_code, 422)

    def test_charger_delete_keeps_a_safe_audit_record(self):
        charger_id = ObjectId()
        charger = {
            "_id": charger_id,
            "charge_point_id": "CP-DELETE",
            "nombre": "Cargador de prueba",
            "ubicacion": "Estacionamiento",
            "ocpp_password_hash": "must-not-be-audited",
        }
        charger_collection = Mock()
        charger_collection.find_one = AsyncMock(return_value=charger)
        charger_collection.update_one = AsyncMock(
            return_value=Mock(matched_count=1)
        )
        sessions_collection = Mock()
        sessions_collection.count_documents = AsyncMock(return_value=0)
        audit_collection = Mock()
        audit_collection.insert_one = AsyncMock()
        collections = {
            "cargadores": charger_collection,
            "sesiones": sessions_collection,
            "cargador_eliminaciones": audit_collection,
        }
        database = Mock(client=FakeMongoClient())

        with (
            patch.object(chargers, "get_database", return_value=database),
            patch.object(
                chargers,
                "get_collection",
                side_effect=lambda name: collections[name],
            ),
        ):
            response = self.client.delete(f"/api/cargadores/{charger_id}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("registrado en reportes", response.json()["mensaje"])
        audit_record = audit_collection.insert_one.await_args.args[0]
        self.assertEqual(audit_record["charge_point_id"], "CP-DELETE")
        self.assertNotIn("ocpp_password_hash", audit_record["charger_snapshot"])
        update = charger_collection.update_one.await_args.args[1]["$set"]
        self.assertFalse(update["activo"])
        self.assertIn("deleted_at", update)

    def test_charger_delete_is_blocked_while_session_is_active(self):
        charger_id = ObjectId()
        charger_collection = Mock()
        charger_collection.find_one = AsyncMock(
            return_value={
                "_id": charger_id,
                "charge_point_id": "CP-BUSY",
                "nombre": "Cargador ocupado",
            }
        )
        charger_collection.update_one = AsyncMock()
        sessions_collection = Mock()
        sessions_collection.count_documents = AsyncMock(return_value=1)
        audit_collection = Mock()
        audit_collection.insert_one = AsyncMock()
        collections = {
            "cargadores": charger_collection,
            "sesiones": sessions_collection,
            "cargador_eliminaciones": audit_collection,
        }
        database = Mock(client=FakeMongoClient())

        with (
            patch.object(chargers, "get_database", return_value=database),
            patch.object(
                chargers,
                "get_collection",
                side_effect=lambda name: collections[name],
            ),
        ):
            response = self.client.delete(f"/api/cargadores/{charger_id}")

        self.assertEqual(response.status_code, 409, response.text)
        charger_collection.update_one.assert_not_awaited()
        audit_collection.insert_one.assert_not_awaited()

    def test_deleted_charger_report_filters_by_full_dates(self):
        deleted_at = datetime(2026, 11, 12, 14, 30)
        collection = Mock()
        collection.find.return_value = AsyncRowsCursor(
            [
                {
                    "charge_point_id": "CP-DELETE",
                    "nombre": "Cargador de prueba",
                    "ubicacion": "Estacionamiento",
                    "deleted_at": deleted_at,
                    "deleted_by": "Admin",
                }
            ]
        )

        with patch.object(reports, "get_collection", return_value=collection):
            response = self.client.get(
                "/api/reportes/cargadores/eliminados"
                "?fecha_inicio=2026-11-12&fecha_fin=2026-11-12"
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            collection.find.call_args.args[0],
            {
                "deleted_at": {
                    "$gte": datetime(2026, 11, 12),
                    "$lt": datetime(2026, 11, 13),
                }
            },
        )
        self.assertEqual(response.json()[0]["charge_point_id"], "CP-DELETE")
        self.assertEqual(
            response.json()[0]["fecha_eliminacion"],
            "2026-11-12 14:30:00",
        )

    def test_invalid_report_dates_return_422(self):
        with patch.object(
            reports,
            "get_collection",
            side_effect=AssertionError("DB access"),
        ):
            response = self.client.get(
                "/api/reportes/consumo?fecha_inicio=not-a-date"
            )

        self.assertEqual(response.status_code, 422)

    def test_report_end_date_only_includes_the_entire_day(self):
        collection = Mock()
        collection.find.return_value = EmptyAsyncCursor()

        with patch.object(reports, "get_collection", return_value=collection):
            asyncio.run(
                reports.report_sessions(
                    fecha_fin="2024-02-29",
                    current_user={"rol": "superadmin"},
                )
            )

        collection.find.assert_called_once_with({
            "estado": "finalizada",
            "fecha_inicio": {"$lt": datetime(2024, 3, 1)},
        })

    def test_consumption_report_end_date_only_includes_the_entire_day(self):
        collection = Mock()
        collection.aggregate.return_value = EmptyAsyncCursor()

        with patch.object(reports, "get_collection", return_value=collection):
            asyncio.run(
                reports.report_consumo(
                    fecha_fin="2024-02-29",
                    current_user={"rol": "superadmin"},
                )
            )

        pipeline = collection.aggregate.call_args.args[0]
        self.assertEqual(
            pipeline[0]["$match"]["fecha_inicio"],
            {"$lt": datetime(2024, 3, 1)},
        )

    def test_consumption_report_can_return_all_users_without_a_limit(self):
        collection = Mock()
        collection.aggregate.return_value = EmptyAsyncCursor()

        with patch.object(reports, "get_collection", return_value=collection):
            asyncio.run(
                reports.report_consumo(
                    limit=None,
                    current_user={"rol": "superadmin"},
                )
            )

        pipeline = collection.aggregate.call_args.args[0]
        self.assertFalse(any("$limit" in stage for stage in pipeline))

    def test_export_rejects_unsupported_formats(self):
        paths = (
            "/api/reportes/sesiones/export?fmt=html",
            "/api/reportes/usuarios/export?fmt=html",
            "/api/reportes/recompensas/export?fmt=html",
            "/api/reportes/cargadores/export?fmt=html",
            "/api/reportes/consumo/export?fmt=html",
        )
        with patch.object(
            reports,
            "get_collection",
            side_effect=AssertionError("Database must not be accessed"),
        ):
            for path in paths:
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 422, response.text)

    def test_csv_exports_are_available_for_every_report_section(self):
        sample_rows = {
            "sessions": [{"_id": "session-1", "charge_point_id": "CP-1"}],
            "users": [{"nombre": "Ana", "correo": "ana@example.invalid"}],
            "rewards": [{"usuario": "Ana", "tipo_de_recompensa": "beneficio"}],
            "chargers": [{"charger_id": "CP-1", "estado": "disponible"}],
            "consumption": [{"usuario": "Ana", "total_cargas": 1}],
        }
        with (
            patch.object(
                reports,
                "report_sessions",
                new_callable=AsyncMock,
                return_value=sample_rows["sessions"],
            ),
            patch.object(
                reports,
                "report_usuarios",
                new_callable=AsyncMock,
                return_value=sample_rows["users"],
            ),
            patch.object(
                reports,
                "report_rewards",
                new_callable=AsyncMock,
                return_value=sample_rows["rewards"],
            ),
            patch.object(
                reports,
                "report_chargers",
                new_callable=AsyncMock,
                return_value=sample_rows["chargers"],
            ),
            patch.object(
                reports,
                "report_consumo",
                new_callable=AsyncMock,
                return_value=sample_rows["consumption"],
            ),
        ):
            for section in (
                "sesiones",
                "usuarios",
                "recompensas",
                "cargadores",
                "consumo",
            ):
                with self.subTest(section=section):
                    response = self.client.get(
                        f"/api/reportes/{section}/export?fmt=csv"
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
                    self.assertTrue(
                        response.headers["content-type"].startswith("text/csv")
                    )

    def test_general_csv_and_pdf_exports_include_all_report_sections(self):
        summary = {
            "kpis": {
                "usuarios_registrados": 1,
                "vehiculos_registrados": 2,
                "cargadores_conectados": 3,
                "cargadores_ocupados": 1,
                "sesiones_realizadas": 4,
                "kwh_consumidos": 12.5,
                "recompensas_otorgadas": 5,
            },
            "graficas": {},
        }
        with (
            patch.object(
                reports,
                "dashboard_report",
                new_callable=AsyncMock,
                return_value=summary,
            ),
            patch.object(
                reports,
                "report_sessions",
                new_callable=AsyncMock,
                return_value=[{"_id": "session-1", "charge_point_id": "CP-1"}],
            ),
            patch.object(
                reports,
                "report_consumo",
                new_callable=AsyncMock,
                return_value=[{"usuario": "Ana", "total_cargas": 1}],
            ),
            patch.object(
                reports,
                "report_usuarios",
                new_callable=AsyncMock,
                return_value=[{"nombre": "Ana", "correo": "ana@example.invalid"}],
            ),
            patch.object(
                reports,
                "report_chargers",
                new_callable=AsyncMock,
                return_value=[{"charger_id": "CP-1", "estado": "disponible"}],
            ),
            patch.object(
                reports,
                "report_rewards",
                new_callable=AsyncMock,
                return_value=[{"usuario": "Ana", "estado": "Pendiente"}],
            ),
        ):
            csv_response = self.client.get(
                "/api/reportes/general/export?fmt=csv"
            )
            pdf_response = self.client.get(
                "/api/reportes/general/export?fmt=pdf"
            )

        self.assertEqual(csv_response.status_code, 200, csv_response.text)
        csv_text = csv_response.content.decode("utf-8-sig")
        for section in ("Sesiones", "Consumo", "Usuarios", "Cargadores", "Recompensas"):
            self.assertIn(section, csv_text)
        self.assertIn("Energía consumida (kWh)", csv_text)
        self.assertEqual(pdf_response.status_code, 200, pdf_response.text)
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))

    def test_export_generation_failure_is_an_http_error(self):
        with (
            patch.object(reports, "report_usuarios", new_callable=AsyncMock, return_value=[]),
            patch.object(
                reports,
                "_export_report_excel",
                new_callable=AsyncMock,
                side_effect=RuntimeError("export library unavailable"),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    reports.export_users_report(
                        fmt="xlsx",
                        current_user={"rol": "superadmin"},
                    )
                )

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "export library unavailable")

    def test_xlsx_and_pdf_exports_return_real_in_memory_documents(self):
        rows = [{
            "nombre": "Usuario de prueba",
            "correo": "user@example.invalid",
            "nfc_asociado": "N/A",
            "vehiculos_registrados": 1,
            "numero_cargas": 2,
            "estado": "Activo",
        }]
        with patch.object(
            reports, "report_usuarios", new_callable=AsyncMock, return_value=rows
        ):
            xlsx = self.client.get("/api/reportes/usuarios/export?fmt=xlsx")
            pdf = self.client.get("/api/reportes/usuarios/export?fmt=pdf")

        self.assertEqual(xlsx.status_code, 200)
        self.assertTrue(
            xlsx.headers["content-type"].startswith(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        )
        self.assertTrue(xlsx.content.startswith(b"PK"))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_valid_object_id_preserves_admin_user_response_contract(self):
        user_id = ObjectId()
        user = {
            "_id": user_id,
            "nombre": "Usuario de prueba",
            "email": "user@example.invalid",
            "password": "not-returned",
            "rol": "consulta",
        }
        collection = type(
            "Collection",
            (),
            {
                "find_one": AsyncMock(return_value=user),
                "count_documents": AsyncMock(return_value=0),
            },
        )()

        with patch.object(users, "get_collection", return_value=collection):
            response = self.client.get(f"/api/usuarios/{user_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["_id"], str(user_id))
        self.assertEqual(response.json()["nombre"], user["nombre"])
        self.assertNotIn("password", response.json())
        collection.find_one.assert_awaited_once_with({"_id": user_id})
        self.assertEqual(parse_object_id(str(user_id), "usuario"), user_id)

    def test_invalid_subject_in_validly_decoded_token_is_unauthorized(self):
        with (
            patch.object(auth.jwt, "decode", return_value={"sub": "not-an-id"}),
            patch.object(auth, "get_collection") as get_collection,
        ):
            with self.assertRaises(HTTPException) as raised:
                import asyncio

                asyncio.run(auth.get_current_user("isolated-token"))

        self.assertEqual(raised.exception.status_code, 401)
        get_collection.assert_not_called()

    def test_report_resolver_does_not_hide_database_errors(self):
        collection = type(
            "Collection",
            (),
            {"find_one": AsyncMock(side_effect=RuntimeError("database unavailable"))},
        )()
        with patch.object(reports, "get_collection", return_value=collection):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                import asyncio

                asyncio.run(
                    reports._resolve_user_name(
                        str(ObjectId()),
                        {"rol": "superadmin"},
                    )
                )


if __name__ == "__main__":
    unittest.main()
