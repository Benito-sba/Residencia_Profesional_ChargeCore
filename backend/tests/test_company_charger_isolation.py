import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import HTTPException

from app.models.charger import CargadorCreate
from app.models.ocpp_commands import RemoteStopRequest
from app.routers import chargers
from app.websocket import ocpp_server


class MemoryCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])

    @staticmethod
    def matches(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
            elif actual != expected:
                return False
        return True

    async def find_one(self, query, projection=None, **kwargs):
        return next(
            (
                document.copy() for document in self.documents
                if self.matches(document, query)
            ),
            None,
        )

    def find(self, query, projection=None):
        return MemoryCursor([
            document.copy() for document in self.documents
            if self.matches(document, query)
        ])

    async def insert_one(self, document):
        inserted_id = ObjectId()
        self.documents.append({"_id": inserted_id, **document})
        return SimpleNamespace(inserted_id=inserted_id)


class MemoryCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, *_):
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __aiter__(self):
        async def iterate():
            for document in self.documents:
                yield document

        return iterate()


class CompanyChargerIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.company_a = ObjectId()
        self.company_b = ObjectId()
        self.charger_a = {
            "_id": ObjectId(),
            "charge_point_id": "CP-A",
            "empresa_id": self.company_a,
            "activo": True,
            "nombre": "A",
            "ocpp_password_hash": "secret-hash",
        }
        self.charger_b = {
            "_id": ObjectId(),
            "charge_point_id": "CP-B",
            "empresa_id": self.company_b,
            "activo": True,
            "nombre": "B",
        }
        self.collections = {
            "cargadores": MemoryCollection([self.charger_a, self.charger_b, {
                "_id": ObjectId(),
                "charge_point_id": "CP-UNASSIGNED",
                "activo": True,
            }]),
            "empresas": MemoryCollection([{
                "_id": self.company_a,
                "activo": True,
            }]),
            "nfc_cards": MemoryCollection([{
                "_id": ObjectId(),
                "uid": "TAG-A",
                "empresa_id": self.company_a,
                "usuario_id": ObjectId(),
                "estado": "activa",
            }]),
            "usuarios": MemoryCollection(),
            "sesiones": MemoryCollection([{
                "charge_point_id": "CP-B",
                "transaction_id": 25,
                "empresa_id": self.company_b,
                "estado": "activa",
            }]),
            "meter_values": MemoryCollection(),
            "cargador_eliminaciones": MemoryCollection(),
        }
        self.collection_patch = patch.object(
            chargers,
            "get_collection",
            side_effect=self.collections.__getitem__,
        )
        self.collection_patch.start()
        self.addCleanup(self.collection_patch.stop)

    async def test_owner_lists_only_chargers_in_its_company(self):
        result = await chargers.list_chargers({
            "rol": "dueno_empresa",
            "empresa_id": self.company_a,
        })

        self.assertEqual([item["charge_point_id"] for item in result], ["CP-A"])
        self.assertNotIn("ocpp_password_hash", result[0])

    async def test_owner_cannot_query_foreign_or_unassigned_charger_by_global_id(self):
        owner = {"rol": "dueno_empresa", "empresa_id": self.company_a}
        with self.assertRaises(HTTPException) as foreign:
            await chargers.get_charger_status("CP-B", owner)
        with self.assertRaises(HTTPException) as unassigned:
            await chargers.get_charger_status("CP-UNASSIGNED", owner)

        self.assertEqual(foreign.exception.status_code, 404)
        self.assertEqual(unassigned.exception.status_code, 404)

    async def test_owner_meter_values_exclude_foreign_and_unassigned_history(self):
        value_a_id = ObjectId()
        self.collections["meter_values"] = MemoryCollection([
            {
                "_id": value_a_id,
                "charge_point_id": "CP-A",
                "empresa_id": self.company_a,
                "timestamp": 2,
            },
            {
                "_id": ObjectId(),
                "charge_point_id": "CP-A",
                "empresa_id": self.company_b,
                "timestamp": 1,
            },
            {
                "_id": ObjectId(),
                "charge_point_id": "CP-A",
                "timestamp": 0,
            },
        ])

        result = await chargers.get_charger_meter_values(
            "CP-A",
            current_user={
                "rol": "dueno_empresa",
                "empresa_id": self.company_a,
            },
        )

        self.assertEqual([item["_id"] for item in result], [str(value_a_id)])
        self.assertEqual(result[0]["empresa_id"], str(self.company_a))

    async def test_owner_charger_history_serializes_session_object_ids(self):
        session_ids = {
            "empresa_id": self.company_a,
            "usuario_id": ObjectId(),
            "vehiculo_id": ObjectId(),
        }
        self.collections["sesiones"] = MemoryCollection([{
            "_id": ObjectId(),
            "charge_point_id": "CP-A",
            **session_ids,
        }])

        result = await chargers.get_charger_history(
            str(self.charger_a["_id"]),
            current_user={
                "rol": "dueno_empresa",
                "empresa_id": self.company_a,
            },
        )

        for field, object_id in session_ids.items():
            self.assertEqual(result[0][field], str(object_id))

    async def test_owner_cannot_set_an_arbitrary_company_when_creating_charger(self):
        payload = CargadorCreate(
            charge_point_id="CP-NEW",
            nombre="Nuevo",
            ubicacion="Prueba",
            ocpp_password="safe-ocpp-password",
            empresa_id=self.company_b,
        )

        with self.assertRaises(HTTPException) as raised:
            await chargers.create_charger(
                payload,
                {"rol": "dueno_empresa", "empresa_id": self.company_a},
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(len(self.collections["cargadores"].documents), 3)

    async def test_owner_creation_inherits_authenticated_company(self):
        payload = CargadorCreate(
            charge_point_id="CP-NEW",
            nombre="Nuevo",
            ubicacion="Prueba",
            ocpp_password="safe-ocpp-password",
        )

        with patch.object(chargers, "hash_ocpp_password", return_value="hashed"):
            await chargers.create_charger(
                payload,
                {"rol": "dueno_empresa", "empresa_id": self.company_a},
            )

        self.assertEqual(
            self.collections["cargadores"].documents[-1]["empresa_id"],
            self.company_a,
        )

    async def test_owner_cannot_issue_remote_commands_for_another_company(self):
        owner = {"rol": "dueno_empresa", "empresa_id": self.company_a}
        with (
            patch.object(
                chargers.ocpp_command_service,
                "remote_stop",
                new=AsyncMock(),
            ) as remote_stop,
            self.assertRaises(HTTPException) as raised,
        ):
            await chargers.remote_stop(
                "CP-B",
                RemoteStopRequest(transaction_id=25),
                owner,
            )

        self.assertEqual(raised.exception.status_code, 404)
        remote_stop.assert_not_awaited()

    async def test_owner_cannot_reconcile_commands_for_another_company(self):
        owner = {"rol": "dueno_empresa", "empresa_id": self.company_a}
        with (
            patch.object(
                chargers,
                "get_collection",
                side_effect=self.collections.__getitem__,
            ),
            patch.object(
                chargers.ocpp_command_service,
                "reconcile_remote_commands",
                new=AsyncMock(),
            ) as reconcile,
        ):
            with self.assertRaises(HTTPException) as raised:
                await chargers.reconcile_remote_commands("CP-B", owner)

        self.assertEqual(raised.exception.status_code, 404)
        reconcile.assert_not_awaited()

    async def test_superadmin_can_list_chargers_globally(self):
        result = await chargers.list_chargers({"rol": "superadmin"})

        self.assertEqual(
            {item["charge_point_id"] for item in result},
            {"CP-A", "CP-B", "CP-UNASSIGNED"},
        )

    async def test_retired_charge_point_id_cannot_be_registered_again(self):
        self.collections["cargador_eliminaciones"].documents.append({
            "charge_point_id": "CP-RETIRED",
        })
        payload = CargadorCreate(
            charge_point_id="CP-RETIRED",
            nombre="Reutilizado",
            ubicacion="Prueba",
            ocpp_password="safe-ocpp-password",
        )

        with self.assertRaises(HTTPException) as raised:
            await chargers.create_charger(payload, {"rol": "superadmin"})

        self.assertEqual(raised.exception.status_code, 409)

    async def test_superadmin_can_assign_an_unassigned_charger_once(self):
        charger_id = ObjectId()
        unassigned = {
            "_id": charger_id,
            "charge_point_id": "CP-UNASSIGNED",
            "activo": True,
        }
        company_collection = SimpleNamespace(
            find_one=AsyncMock(return_value={"_id": self.company_b, "activo": True})
        )
        charger_collection = SimpleNamespace(
            find_one=AsyncMock(return_value=unassigned),
            update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)),
        )
        collections = {
            "cargadores": charger_collection,
            "empresas": company_collection,
            "cargador_eliminaciones": SimpleNamespace(
                find_one=AsyncMock(return_value=None)
            ),
            "sesiones": SimpleNamespace(
                count_documents=AsyncMock(return_value=0)
            ),
            "logs": SimpleNamespace(find_one=AsyncMock(return_value=None)),
            "eventos_ocpp": SimpleNamespace(find_one=AsyncMock(return_value=None)),
            "ocpp_messages": SimpleNamespace(find_one=AsyncMock(return_value=None)),
        }
        payload = chargers.CargadorUpdate(empresa_id=self.company_b)

        with patch.object(
            chargers,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            result = await chargers.update_charger(
                str(charger_id),
                payload,
                {"rol": "superadmin"},
            )

        self.assertEqual(result["mensaje"], "Cargador actualizado")
        query, update = charger_collection.update_one.await_args.args
        self.assertEqual(update["$set"]["empresa_id"], self.company_b)
        self.assertEqual(
            query["$or"],
            [{"empresa_id": {"$exists": False}}, {"empresa_id": None}],
        )
        self.assertEqual(query["conectado"], {"$ne": True})
        self.assertEqual(query["connection_token"], {"$in": [None]})

    async def test_superadmin_cannot_assign_connected_unassigned_charger(self):
        charger_id = ObjectId()
        charger_collection = SimpleNamespace(
            find_one=AsyncMock(return_value={
                "_id": charger_id,
                "charge_point_id": "CP-UNASSIGNED",
                "activo": True,
                "conectado": True,
                "connection_token": "active-token",
            }),
            update_one=AsyncMock(),
        )
        collections = {
            "cargadores": charger_collection,
            "empresas": SimpleNamespace(
                find_one=AsyncMock(return_value={
                    "_id": self.company_b,
                    "activo": True,
                })
            ),
        }

        with patch.object(
            chargers,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            with self.assertRaises(HTTPException) as raised:
                await chargers.update_charger(
                    str(charger_id),
                    chargers.CargadorUpdate(empresa_id=self.company_b),
                    {"rol": "superadmin"},
                )

        self.assertEqual(raised.exception.status_code, 409)
        charger_collection.update_one.assert_not_awaited()

    async def test_superadmin_cannot_assign_charger_with_unscoped_history(self):
        charger_id = ObjectId()
        charger_collection = SimpleNamespace(
            find_one=AsyncMock(return_value={
                "_id": charger_id,
                "charge_point_id": "CP-UNASSIGNED",
                "activo": True,
            }),
            update_one=AsyncMock(),
        )
        collections = {
            "cargadores": charger_collection,
            "empresas": SimpleNamespace(
                find_one=AsyncMock(return_value={
                    "_id": self.company_b,
                    "activo": True,
                })
            ),
            "cargador_eliminaciones": SimpleNamespace(
                find_one=AsyncMock(return_value=None)
            ),
            "sesiones": SimpleNamespace(count_documents=AsyncMock(return_value=0)),
            "logs": SimpleNamespace(find_one=AsyncMock(return_value=None)),
            "eventos_ocpp": SimpleNamespace(find_one=AsyncMock(return_value=None)),
            "ocpp_messages": SimpleNamespace(
                find_one=AsyncMock(return_value={"charge_point_id": "CP-UNASSIGNED"})
            ),
        }

        with patch.object(
            chargers,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            with self.assertRaises(HTTPException) as raised:
                await chargers.update_charger(
                    str(charger_id),
                    chargers.CargadorUpdate(empresa_id=self.company_b),
                    {"rol": "superadmin"},
                )

        self.assertEqual(raised.exception.status_code, 409)
        charger_collection.update_one.assert_not_awaited()

    async def test_superadmin_cannot_reassign_a_charger_to_another_company(self):
        charger_id = ObjectId()
        charger_collection = SimpleNamespace(
            find_one=AsyncMock(return_value={
                "_id": charger_id,
                "charge_point_id": "CP-A",
                "empresa_id": self.company_a,
                "activo": True,
            }),
            update_one=AsyncMock(),
        )
        collections = {
            "cargadores": charger_collection,
            "empresas": SimpleNamespace(
                find_one=AsyncMock(return_value={
                    "_id": self.company_b,
                    "activo": True,
                })
            ),
        }

        with patch.object(
            chargers,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            with self.assertRaises(HTTPException) as raised:
                await chargers.update_charger(
                    str(charger_id),
                    chargers.CargadorUpdate(empresa_id=self.company_b),
                    {"rol": "superadmin"},
                )

        self.assertEqual(raised.exception.status_code, 409)
        charger_collection.update_one.assert_not_awaited()

    async def test_owner_cannot_assign_company_to_a_charger(self):
        with patch.object(
            chargers,
            "get_collection",
            side_effect=AssertionError("Database must not be accessed"),
        ):
            with self.assertRaises(HTTPException) as raised:
                await chargers.update_charger(
                    str(ObjectId()),
                    chargers.CargadorUpdate(empresa_id=self.company_a),
                    {"rol": "dueno_empresa", "empresa_id": self.company_a},
                )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_ocpp_messages_persist_the_chargers_company(self):
        message_collection = SimpleNamespace(insert_one=AsyncMock())
        charger_collection = SimpleNamespace(
            find_one=AsyncMock(return_value={"empresa_id": self.company_a}),
            update_one=AsyncMock(),
        )
        collections = {
            "cargadores": charger_collection,
            "ocpp_messages": message_collection,
        }

        with patch.object(
            ocpp_server,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            await ocpp_server.persist_ocpp_message(
                "CP-A",
                "[]",
                {"direction": "incoming", "timestamp": None},
            )

        self.assertEqual(
            message_collection.insert_one.await_args.args[0]["empresa_id"],
            self.company_a,
        )

    async def test_unassigned_ocpp_activity_remains_unassigned(self):
        events = SimpleNamespace(insert_one=AsyncMock())
        collections = {
            "cargadores": self.collections["cargadores"],
            "eventos_ocpp": events,
        }

        with patch.object(
            ocpp_server,
            "get_collection",
            side_effect=collections.__getitem__,
        ):
            await ocpp_server._persist_company_activity(
                "eventos_ocpp",
                {
                    "charge_point_id": "CP-UNASSIGNED",
                    "tipo_mensaje": "Heartbeat",
                },
            )

        event = events.insert_one.await_args.args[0]
        self.assertNotIn("empresa_id", event)

    async def test_owner_cannot_start_a_client_charge(self):
        charger_collection = self.collections["cargadores"]
        with patch.object(
            chargers,
            "get_collection",
            return_value=charger_collection,
        ), patch.object(
            chargers.ocpp_command_service,
            "remote_start",
            new=AsyncMock(),
        ) as remote_start:
            with self.assertRaises(HTTPException) as raised:
                await chargers.remote_start(
                    "CP-A",
                    {"rol": "dueno_empresa", "empresa_id": self.company_a},
                )

        self.assertEqual(raised.exception.status_code, 403)
        remote_start.assert_not_awaited()

    async def test_superadmin_cannot_start_a_client_charge(self):
        with patch.object(
            chargers,
            "get_collection",
            return_value=self.collections["cargadores"],
        ), patch.object(
            chargers.ocpp_command_service,
            "remote_start",
            new=AsyncMock(),
        ) as remote_start:
            with self.assertRaises(HTTPException) as raised:
                await chargers.remote_start("CP-A", {"rol": "superadmin"})

        self.assertEqual(raised.exception.status_code, 403)
        remote_start.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
