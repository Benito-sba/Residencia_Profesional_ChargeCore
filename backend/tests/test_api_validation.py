import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth
from app.auth import require_admin, require_read_access
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
        self.app.dependency_overrides[require_read_access] = lambda: {"rol": "consulta"}
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
                nfc_cards.delete_nfc_card(str(card_id), current_user={})
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
                    current_user={"rol": "consulta"},
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
                    current_user={"rol": "consulta"},
                )
            )

        pipeline = collection.aggregate.call_args.args[0]
        self.assertEqual(
            pipeline[0]["$match"]["fecha_inicio"],
            {"$lt": datetime(2024, 3, 1)},
        )

    def test_export_rejects_unsupported_formats(self):
        paths = (
            "/api/reportes/sesiones/export?fmt=csv",
            "/api/reportes/usuarios/export?fmt=csv",
            "/api/reportes/recompensas/export?fmt=csv",
            "/api/reportes/cargadores/export?fmt=csv",
            "/api/reportes/consumo/export?fmt=csv",
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
                        current_user={"rol": "consulta"},
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
            {"find_one": AsyncMock(return_value=user)},
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

                asyncio.run(reports._resolve_user_name(str(ObjectId())))


if __name__ == "__main__":
    unittest.main()
