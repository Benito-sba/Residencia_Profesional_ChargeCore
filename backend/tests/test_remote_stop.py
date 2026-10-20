import asyncio
import base64
import copy
import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from bson import ObjectId
from pymongo.errors import DuplicateKeyError, OperationFailure

from app.services import ocpp_command_service
from app.services import reward_service
from app.websocket import ocpp_server
from app.ocpp_auth import hash_ocpp_password
from app.models.charger import Conector


class FakeCollection:
    def __init__(self, document=None, *, lose_update_race=False):
        self.document = document
        self.lose_update_race = lose_update_race
        self.update_calls = []
        self.insert_calls = []

    async def find_one(self, query):
        if self._matches(query):
            return self.document.copy()
        return None

    async def update_one(self, query, update, upsert=False):
        self.update_calls.append((query, update))
        if self.lose_update_race:
            return SimpleNamespace(matched_count=0, modified_count=0)
        if self._matches(query):
            for key, value in update.get("$set", {}).items():
                self._set_path(self.document, key, value)
            return SimpleNamespace(matched_count=1, modified_count=1)
        if not upsert:
            return SimpleNamespace(matched_count=0, modified_count=0)
        self.document = {"_id": query.get("_id")}
        for key, value in update.get("$setOnInsert", {}).items():
            self._set_path(self.document, key, value)
        return SimpleNamespace(
            matched_count=0,
            modified_count=0,
            upserted_id=self.document["_id"],
        )

    async def insert_one(self, document):
        self.insert_calls.append(document)

    def _matches(self, query):
        if self.document is None:
            return False
        for key, value in query.items():
            if key == "$or":
                if not any(self._matches(condition) for condition in value):
                    return False
                continue
            if key == "$and":
                if not all(self._matches(condition) for condition in value):
                    return False
                continue
            actual = self.document
            exists = True
            for part in key.split("."):
                if not isinstance(actual, dict):
                    exists = False
                    actual = None
                    break
                if part not in actual:
                    exists = False
                    actual = None
                    break
                actual = actual.get(part)
            if isinstance(value, dict) and "$exists" in value:
                if exists != value["$exists"]:
                    return False
                continue
            if actual != value:
                return False
        return True

    @staticmethod
    def _set_path(document, path, value):
        parts = path.split(".")
        target = document
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value


class OCPPMessageTrackingTests(unittest.TestCase):
    def test_completed_calls_preserve_correlation_without_leaking_registry_entries(self):
        manager = ocpp_server.OCPPConnectionManager()

        for index in range(100):
            unique_id = str(index)
            manager.track_message(
                "CP-1",
                json.dumps([2, unique_id, "Heartbeat", {}]),
                "incoming",
            )
            response_message = (
                [3, unique_id, {"currentTime": "2026-10-12T00:00:00Z"}]
                if index % 2 == 0
                else [4, unique_id, "InternalError", "failure", {}]
            )
            response = manager.track_message(
                "CP-1", json.dumps(response_message), "outgoing"
            )
            self.assertEqual(response["action"], "Heartbeat")

        self.assertEqual(manager.message_registry, {})


def active_session(charge_point_id="CP-1"):
    return {
        "_id": "session-1",
        "transaction_id": 123,
        "charge_point_id": charge_point_id,
        "connector_id": 1,
        "estado": "activa",
        "fecha_inicio": datetime(2024, 1, 1) - timedelta(minutes=5),
        "meter_start": 1000,
        "usuario_id": "user-1",
    }


class RemoteStopServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_start_sends_expected_payload(self):
        with (
            patch.object(
                ocpp_command_service,
                "_charger_exists",
                new_callable=AsyncMock,
            ) as charger_exists,
            patch.object(
                ocpp_command_service,
                "_call",
                new_callable=AsyncMock,
                return_value={"status": "Accepted"},
            ) as send,
        ):
            result = await ocpp_command_service.remote_start("CP-1", "TAG-1", 2)

        self.assertEqual(result["status"], "Accepted")
        charger_exists.assert_awaited_once_with("CP-1")
        charge_point_id, action, payload = send.await_args.args
        self.assertEqual(action, "RemoteStartTransaction")
        self.assertEqual(charge_point_id, "CP-1")
        self.assertEqual(payload.id_tag, "TAG-1")
        self.assertEqual(payload.connector_id, 2)

    async def test_reset_sends_requested_reset_type(self):
        with (
            patch.object(
                ocpp_command_service,
                "_charger_exists",
                new_callable=AsyncMock,
            ),
            patch.object(
                ocpp_command_service,
                "_call",
                new_callable=AsyncMock,
                return_value={"status": "Accepted"},
            ) as send,
        ):
            result = await ocpp_command_service.reset("CP-1", "Hard")

        self.assertEqual(result["status"], "Accepted")
        charge_point_id, action, payload = send.await_args.args
        self.assertEqual(action, "Reset")
        self.assertEqual(charge_point_id, "CP-1")
        self.assertEqual(payload.type.value, "Hard")

    async def test_change_availability_sends_requested_connector_and_type(self):
        with (
            patch.object(
                ocpp_command_service,
                "_charger_exists",
                new_callable=AsyncMock,
            ),
            patch.object(
                ocpp_command_service,
                "_call",
                new_callable=AsyncMock,
                return_value={"status": "Accepted"},
            ) as send,
        ):
            result = await ocpp_command_service.change_availability(
                "CP-1", 0, "Inoperative"
            )

        self.assertEqual(result["status"], "Accepted")
        charge_point_id, action, payload = send.await_args.args
        self.assertEqual(action, "ChangeAvailability")
        self.assertEqual(charge_point_id, "CP-1")
        self.assertEqual(payload.connector_id, 0)
        self.assertEqual(payload.type.value, "Inoperative")

    async def test_missing_transaction_does_not_send_command(self):
        sessions = FakeCollection()
        with (
            patch.object(ocpp_command_service, "get_collection", return_value=sessions),
            patch.object(ocpp_command_service, "_call", new_callable=AsyncMock) as send,
        ):
            with self.assertRaises(HTTPException) as raised:
                await ocpp_command_service.remote_stop("CP-1", 123)

        self.assertEqual(raised.exception.status_code, 404)
        send.assert_not_awaited()

    async def test_disconnected_charger_is_reported(self):
        sessions = FakeCollection(active_session())
        with (
            patch.object(ocpp_command_service, "get_collection", return_value=sessions),
            patch.object(
                ocpp_command_service.ocpp_connection_manager,
                "get_handler",
                return_value=None,
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await ocpp_command_service.remote_stop("CP-1", 123)

        self.assertEqual(raised.exception.status_code, 409)

    async def test_disconnect_during_command_is_reported(self):
        sessions = FakeCollection(active_session())
        disconnected = asyncio.Event()

        class DisconnectingHandler:
            async def call(self, payload, *, suppress, unique_id):
                self.call_arguments = (payload, suppress, unique_id)
                disconnected.set()
                await asyncio.Event().wait()

        with (
            patch.object(ocpp_command_service, "get_collection", return_value=sessions),
            patch.object(
                ocpp_command_service.ocpp_connection_manager,
                "get_handler",
                return_value=DisconnectingHandler(),
            ),
            patch.object(
                ocpp_command_service.ocpp_connection_manager,
                "get_disconnect_event",
                return_value=disconnected,
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await ocpp_command_service.remote_stop("CP-1", 123)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(sessions.document["estado"], "activa")
        self.assertEqual(sessions.update_calls, [])

    async def test_accepted_command_does_not_finalize_session(self):
        sessions = FakeCollection(active_session())
        with (
            patch.object(ocpp_command_service, "get_collection", return_value=sessions),
            patch.object(
                ocpp_command_service,
                "_call",
                new_callable=AsyncMock,
                return_value={"status": "Accepted", "action": "RemoteStopTransaction"},
            ),
        ):
            result = await ocpp_command_service.remote_stop("CP-1", 123)

        self.assertEqual(result["status"], "Accepted")
        self.assertEqual(sessions.document["estado"], "activa")
        self.assertEqual(sessions.update_calls, [])

    async def test_rejected_command_is_reported_as_conflict(self):
        sessions = FakeCollection(active_session())
        with (
            patch.object(ocpp_command_service, "get_collection", return_value=sessions),
            patch.object(
                ocpp_command_service,
                "_call",
                new_callable=AsyncMock,
                return_value={"status": "Rejected", "action": "RemoteStopTransaction"},
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await ocpp_command_service.remote_stop("CP-1", 123)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["status"], "Rejected")
        self.assertEqual(sessions.document["estado"], "activa")

    async def test_timeout_is_reported_without_finalizing_session(self):
        sessions = FakeCollection(active_session())

        class HangingHandler:
            async def call(self, payload, *, suppress, unique_id):
                self.call_arguments = (payload, suppress, unique_id)
                await asyncio.Event().wait()

        with (
            patch.object(ocpp_command_service, "get_collection", return_value=sessions),
            patch.object(
                ocpp_command_service.ocpp_connection_manager,
                "get_handler",
                return_value=HangingHandler(),
            ),
            patch.object(
                ocpp_command_service.ocpp_connection_manager,
                "get_disconnect_event",
                return_value=asyncio.Event(),
            ),
            patch.object(
                ocpp_command_service,
                "settings",
                SimpleNamespace(OCPP_COMMAND_TIMEOUT_SECONDS=0.01),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await ocpp_command_service.remote_stop("CP-1", 123)

        self.assertEqual(raised.exception.status_code, 504)
        self.assertEqual(sessions.document["estado"], "activa")
        self.assertEqual(sessions.update_calls, [])

    async def test_cancelled_request_cancels_pending_charger_command(self):
        call_started = asyncio.Event()
        call_cancelled = asyncio.Event()

        class HangingHandler:
            async def call(self, payload, *, suppress, unique_id):
                call_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    call_cancelled.set()
                    raise

        with (
            patch.object(
                ocpp_command_service.ocpp_connection_manager,
                "get_handler",
                return_value=HangingHandler(),
            ),
            patch.object(
                ocpp_command_service.ocpp_connection_manager,
                "get_disconnect_event",
                return_value=asyncio.Event(),
            ),
        ):
            request = asyncio.create_task(
                ocpp_command_service._call("CP-1", "Reset", object())
            )
            await call_started.wait()
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request

        self.assertTrue(call_cancelled.is_set())


class StopTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def _send_stop(
        self,
        session_collection,
        charger_collection=None,
        log_collection=None,
        event_collection=None,
        apply_reward=None,
        grant_reward=None,
    ):
        collections = {
            "sesiones": session_collection,
            "cargadores": charger_collection or FakeCollection(),
            "logs": log_collection or FakeCollection(),
            "eventos_ocpp": event_collection or FakeCollection(),
        }
        handler = ocpp_server.ChargePointHandler("CP-1", None)
        apply_reward = apply_reward or AsyncMock(
            return_value={"tipo": "ninguna", "mensaje": ""}
        )
        grant_reward = grant_reward or AsyncMock()
        with (
            patch.object(ocpp_server, "get_collection", side_effect=collections.__getitem__),
            patch.object(
                ocpp_server,
                "aplicar_recompensa_en_sesion",
                new=apply_reward,
            ),
            patch.object(
                ocpp_server, "verificar_y_otorgar_recompensa", new=grant_reward
            ),
        ):
            response = await handler.on_stop_transaction(
                123, 1500, datetime(2024, 1, 1)
            )
        return response, collections, apply_reward, grant_reward

    async def test_concurrent_stop_messages_finalize_once_and_record_progress(self):
        class ConcurrentSessionCollection(FakeCollection):
            def __init__(self, document):
                super().__init__(document)
                self.read_barrier = asyncio.Barrier(2)
                self.read_count = 0
                self.update_lock = asyncio.Lock()
                self.transition_successes = 0

            async def find_one(self, query):
                document = await super().find_one(query)
                if (
                    query.get("estado") == "activa"
                    and self.read_count < 2
                ):
                    self.read_count += 1
                    await self.read_barrier.wait()
                return document

            async def update_one(self, query, update):
                async with self.update_lock:
                    result = await super().update_one(query, update)
                    if query.get("estado") == "activa" and result.modified_count:
                        self.transition_successes += 1
                    return result

        sessions = ConcurrentSessionCollection(active_session())
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 123},
        })
        collections = {
            "sesiones": sessions,
            "cargadores": charger,
            "logs": FakeCollection(),
            "eventos_ocpp": FakeCollection(),
        }
        handlers = [
            ocpp_server.ChargePointHandler("CP-1", None)
            for _ in range(2)
        ]
        with (
            patch.object(ocpp_server, "get_collection", side_effect=collections.__getitem__),
            patch.object(
                ocpp_server,
                "aplicar_recompensa_en_sesion",
                new_callable=AsyncMock,
                return_value={"tipo": "ninguna", "mensaje": ""},
            ) as apply_reward,
            patch.object(
                ocpp_server, "verificar_y_otorgar_recompensa", new_callable=AsyncMock
            ) as grant_reward,
        ):
            await asyncio.gather(*(
                handler.on_stop_transaction(123, 1500, datetime(2024, 1, 1))
                for handler in handlers
            ))

        self.assertEqual(sessions.document["estado"], "finalizada")
        self.assertEqual(sessions.transition_successes, 1)
        self.assertGreaterEqual(len(charger.update_calls), 1)
        self.assertGreaterEqual(apply_reward.await_count, 1)
        self.assertGreaterEqual(grant_reward.await_count, 1)
        self.assertTrue(sessions.document["stop_closure"]["event_recorded"])

    async def test_valid_stop_finalizes_and_clears_matching_charger_reference(self):
        sessions = FakeCollection(active_session())
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 123},
        })

        response, collections, apply_reward, grant_reward = await self._send_stop(
            sessions, charger_collection=charger
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        query, _ = sessions.update_calls[0]
        self.assertEqual(query, {
            "transaction_id": 123,
            "charge_point_id": "CP-1",
            "estado": "activa",
            "selected_reward_id": None,
        })
        self.assertEqual(sessions.document["estado"], "finalizada")
        self.assertEqual(charger.document["estado_general"], "disponible")
        self.assertIsNone(charger.document["sesion_actual"])
        self.assertEqual(len(charger.update_calls), 1)
        receipt_path = ocpp_server._charger_cleanup_receipt_path(
            sessions.document["_id"]
        )
        receipt_key = receipt_path.rsplit(".", 1)[1]
        self.assertEqual(
            charger.document["stop_closure_receipts"][receipt_key],
            {
                "session_id": sessions.document["_id"],
                "transaction_id": 123,
                "status": "completed",
            },
        )
        charger_update = charger.update_calls[0][1]
        self.assertIsNone(charger_update["$set"]["sesion_actual"])
        self.assertEqual(
            charger_update["$set"][receipt_path]["status"], "completed"
        )
        apply_reward.assert_awaited_once()
        grant_reward.assert_awaited_once()
        self.assertEqual(
            collections["eventos_ocpp"].document["_id"], "stop:CP-1:123"
        )

    async def test_missing_charger_does_not_complete_clear_stage_or_accept(self):
        sessions = FakeCollection(active_session())
        charger = FakeCollection()

        with self.assertRaisesRegex(RuntimeError, "No existe el cargador CP-1"):
            await self._send_stop(sessions, charger_collection=charger)

        self.assertEqual(sessions.document["estado"], "finalizada")
        self.assertTrue(sessions.document["stop_closure"]["rewards_applied"])
        self.assertTrue(sessions.document["stop_closure"]["rewards_granted"])
        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "pending",
        )
        self.assertEqual(charger.update_calls, [])
        self.assertNotIn("stop_closure_receipts", charger.document or {})

    async def test_matched_charger_update_with_zero_modified_count_is_success(self):
        class UnchangedCharger(FakeCollection):
            async def update_one(self, query, update, upsert=False):
                result = await super().update_one(query, update, upsert=upsert)
                return SimpleNamespace(
                    matched_count=result.matched_count, modified_count=0
                )

        sessions = FakeCollection(active_session())
        charger = UnchangedCharger({
            "charge_point_id": "CP-1",
            "estado_general": "disponible",
            "ultimo_estado_conocido": "Available",
            "sesion_actual": None,
        })

        response, _, _, _ = await self._send_stop(
            sessions, charger_collection=charger
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(charger.document["estado_general"], "disponible")
        self.assertIsNone(charger.document["sesion_actual"])
        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "completed",
        )

    async def test_foreign_charger_transaction_is_not_finalized(self):
        sessions = FakeCollection(active_session(charge_point_id="CP-OTHER"))
        charger = FakeCollection()

        response, collections, apply_reward, grant_reward = await self._send_stop(
            sessions, charger_collection=charger
        )

        self.assertEqual(response.id_tag_info["status"], "Invalid")
        self.assertEqual(sessions.document["estado"], "activa")
        self.assertEqual(sessions.update_calls, [])
        self.assertEqual(charger.update_calls, [])
        apply_reward.assert_not_awaited()
        grant_reward.assert_not_awaited()
        self.assertEqual(len(collections["logs"].insert_calls), 1)
        self.assertEqual(
            collections["eventos_ocpp"].document["_id"], "stop:CP-1:123"
        )

    async def test_duplicate_stop_does_not_repeat_closure_side_effects(self):
        sessions = FakeCollection(active_session())
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 123},
        })
        events = FakeCollection()

        _, _, apply_reward, grant_reward = await self._send_stop(
            sessions, charger_collection=charger, event_collection=events
        )
        _, _, apply_reward_again, grant_reward_again = await self._send_stop(
            sessions, charger_collection=charger, event_collection=events
        )

        self.assertEqual(sessions.document["estado"], "finalizada")
        self.assertEqual(sessions.document["stop_closure"], {
            "reward_selection_complete": False,
            "reward_id": None,
            "rewards_applied": True,
            "rewards_granted": True,
            "charger_cleanup_status": "completed",
            "event_recorded": True,
        })
        self.assertEqual(events.document["_id"], "stop:CP-1:123")
        self.assertEqual(len(events.update_calls), 1)
        self.assertEqual(len(charger.update_calls), 1)
        apply_reward.assert_awaited_once()
        grant_reward.assert_awaited_once()
        apply_reward_again.assert_not_awaited()
        grant_reward_again.assert_not_awaited()

    async def test_lost_atomic_update_race_skips_rewards_and_charger_update(self):
        sessions = FakeCollection(active_session(), lose_update_race=True)
        charger = FakeCollection()

        _, _, apply_reward, grant_reward = await self._send_stop(
            sessions, charger_collection=charger
        )

        self.assertEqual(sessions.document["estado"], "activa")
        self.assertEqual(charger.update_calls, [])
        apply_reward.assert_not_awaited()
        grant_reward.assert_not_awaited()

    async def test_retry_after_charger_failure_completes_only_pending_stages(self):
        class FailOnceCharger(FakeCollection):
            fail = True

            async def update_one(self, query, update, upsert=False):
                self.update_calls.append((query, update))
                if self.fail:
                    self.fail = False
                    raise RuntimeError("charger write failed")
                return await super().update_one(query, update, upsert=upsert)

        sessions = FakeCollection(active_session())
        charger = FailOnceCharger({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 123},
        })
        events = FakeCollection()
        apply_reward = AsyncMock(return_value={"tipo": "ninguna", "mensaje": ""})
        grant_reward = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "charger write failed"):
            await self._send_stop(
                sessions,
                charger_collection=charger,
                event_collection=events,
                apply_reward=apply_reward,
                grant_reward=grant_reward,
            )

        self.assertEqual(sessions.document["estado"], "finalizada")
        self.assertTrue(sessions.document["stop_closure"]["rewards_applied"])
        self.assertTrue(sessions.document["stop_closure"]["rewards_granted"])
        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "pending",
        )
        self.assertIsNone(events.document)
        self.assertEqual(charger.document["estado_general"], "ocupado")
        self.assertEqual(
            charger.document["sesion_actual"]["transaction_id"], 123
        )

        response, _, apply_retry, grant_retry = await self._send_stop(
            sessions,
            charger_collection=charger,
            event_collection=events,
            apply_reward=apply_reward,
            grant_reward=grant_reward,
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(sessions.document["estado"], "finalizada")
        self.assertEqual(charger.document["estado_general"], "disponible")
        self.assertIsNone(charger.document["sesion_actual"])
        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "completed",
        )
        self.assertEqual(sessions.document["stop_closure"]["event_recorded"], True)
        apply_retry.assert_awaited_once()
        grant_retry.assert_awaited_once()
        self.assertEqual(events.document["_id"], "stop:CP-1:123")

    async def test_charger_verification_failure_keeps_cleanup_pending(self):
        class VerificationFailureCharger(FakeCollection):
            async def update_one(self, query, update, upsert=False):
                self.update_calls.append((query, update))
                return SimpleNamespace(matched_count=0, modified_count=0)

            async def find_one(self, query):
                raise RuntimeError("charger verification failed")

        sessions = FakeCollection(active_session())
        charger = VerificationFailureCharger({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 123},
        })

        with self.assertRaisesRegex(RuntimeError, "charger verification failed"):
            await self._send_stop(sessions, charger_collection=charger)

        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "pending",
        )
        self.assertEqual(sessions.document["estado"], "finalizada")
        self.assertEqual(charger.document["sesion_actual"]["transaction_id"], 123)

    async def test_lost_session_marker_recovers_completed_receipt_after_new_transaction(self):
        sessions = FakeCollection(active_session())
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 123},
        })
        mark_status = ocpp_server._mark_charger_cleanup_status
        fail_marker = True

        async def fail_once(*args, **kwargs):
            nonlocal fail_marker
            if fail_marker:
                fail_marker = False
                raise RuntimeError("session status write failed")
            await mark_status(*args, **kwargs)

        with patch.object(
            ocpp_server,
            "_mark_charger_cleanup_status",
            new=AsyncMock(side_effect=fail_once),
        ):
            with self.assertRaisesRegex(RuntimeError, "session status write failed"):
                await self._send_stop(sessions, charger_collection=charger)

            receipt_path = ocpp_server._charger_cleanup_receipt_path(
                sessions.document["_id"]
            )
            receipt_key = receipt_path.rsplit(".", 1)[1]
            self.assertEqual(
                charger.document["stop_closure_receipts"][receipt_key]["status"],
                "completed",
            )

        charger.document["sesion_actual"] = {"transaction_id": 456}
        response, _, _, _ = await self._send_stop(
            sessions, charger_collection=charger
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(len(charger.update_calls), 1)
        self.assertEqual(charger.document["sesion_actual"]["transaction_id"], 456)
        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "completed",
        )

    async def test_selected_reward_is_carried_into_the_completed_session(self):
        session = active_session()
        reward_id = ObjectId()
        session["selected_reward_id"] = reward_id
        sessions = FakeCollection(session)
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 123},
        })
        apply_reward = AsyncMock(
            return_value={
                "tipo": "carga_gratuita",
                "mensaje": "Carga gratuita aplicada",
            }
        )

        response, _, _, _ = await self._send_stop(
            sessions,
            charger_collection=charger,
            apply_reward=apply_reward,
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertTrue(
            sessions.document["stop_closure"]["reward_selection_complete"]
        )
        self.assertEqual(sessions.document["stop_closure"]["reward_id"], reward_id)
        apply_reward.assert_awaited_once_with("user-1", "session-1")
        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "completed",
        )

    async def test_existing_completed_receipt_recovers_without_repeating_cleanup(self):
        sessions = FakeCollection(active_session())
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 456},
            "stop_closure_receipts": {},
        })
        receipt_path = ocpp_server._charger_cleanup_receipt_path(
            sessions.document["_id"]
        )
        receipt_key = receipt_path.rsplit(".", 1)[1]
        charger.document["stop_closure_receipts"][receipt_key] = {
            "session_id": sessions.document["_id"],
            "transaction_id": 123,
            "status": "completed",
        }

        response, _, _, _ = await self._send_stop(
            sessions, charger_collection=charger
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(charger.update_calls, [])
        self.assertEqual(charger.document["sesion_actual"]["transaction_id"], 456)
        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "completed",
        )

    async def test_foreign_receipt_is_conditional_and_preserves_reference(self):
        sessions = FakeCollection(active_session())
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 456},
        })

        response, _, _, _ = await self._send_stop(
            sessions, charger_collection=charger
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(charger.document["sesion_actual"]["transaction_id"], 456)
        self.assertEqual(charger.document["estado_general"], "ocupado")
        receipt_path = ocpp_server._charger_cleanup_receipt_path(
            sessions.document["_id"]
        )
        receipt_key = receipt_path.rsplit(".", 1)[1]
        self.assertEqual(
            charger.document["stop_closure_receipts"][receipt_key],
            {
                "session_id": sessions.document["_id"],
                "transaction_id": 123,
                "status": "skipped_foreign_transaction",
                "observed_transaction_id": 456,
            },
        )
        skip_update_filter = charger.update_calls[0][0]
        self.assertEqual(
            skip_update_filter["sesion_actual.transaction_id"], 456
        )
        self.assertEqual(
            charger.update_calls[0][1]["$set"][receipt_path]["status"],
            "skipped_foreign_transaction",
        )

    async def test_stale_foreign_observation_is_rechecked_before_receipt(self):
        class ChangesForeignReference(FakeCollection):
            changed = False

            async def update_one(self, query, update, upsert=False):
                receipt_updates = [
                    value for key, value in update.get("$set", {}).items()
                    if key.startswith("stop_closure_receipts.")
                ]
                if (
                    receipt_updates
                    and receipt_updates[0].get("status")
                    == "skipped_foreign_transaction"
                    and not self.changed
                ):
                    self.changed = True
                    self.document["sesion_actual"] = {"transaction_id": 789}
                return await super().update_one(query, update, upsert=upsert)

        sessions = FakeCollection(active_session())
        charger = ChangesForeignReference({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 456},
        })

        response, _, _, _ = await self._send_stop(
            sessions, charger_collection=charger
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(charger.document["sesion_actual"]["transaction_id"], 789)
        receipt_key = ocpp_server._charger_cleanup_receipt_path(
            sessions.document["_id"]
        ).rsplit(".", 1)[1]
        self.assertEqual(
            charger.document["stop_closure_receipts"][receipt_key][
                "observed_transaction_id"
            ],
            789,
        )
        self.assertEqual(
            sessions.document["stop_closure"][
                "charger_cleanup_observed_transaction_id"
            ],
            789,
        )

    async def test_lost_charger_write_ack_recovers_receipt_without_accepting_early(self):
        class ApplyThenLoseAck(FakeCollection):
            fail = True

            async def update_one(self, query, update, upsert=False):
                result = await super().update_one(query, update, upsert=upsert)
                if self.fail and any(
                    key.startswith("stop_closure_receipts.")
                    for key in update.get("$set", {})
                ):
                    self.fail = False
                    raise RuntimeError("charger write acknowledgement lost")
                return result

        sessions = FakeCollection(active_session())
        charger = ApplyThenLoseAck({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 123},
        })

        with self.assertRaisesRegex(
            RuntimeError, "charger write acknowledgement lost"
        ):
            await self._send_stop(sessions, charger_collection=charger)

        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "pending",
        )
        self.assertIsNone(charger.document["sesion_actual"])
        self.assertTrue(charger.document["stop_closure_receipts"])

        response, _, _, _ = await self._send_stop(
            sessions, charger_collection=charger
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "completed",
        )
        self.assertEqual(len(charger.update_calls), 1)

    async def test_incompatible_existing_receipt_is_never_overwritten(self):
        sessions = FakeCollection(active_session())
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 456},
            "stop_closure_receipts": {},
        })
        receipt_key = ocpp_server._charger_cleanup_receipt_path(
            sessions.document["_id"]
        ).rsplit(".", 1)[1]
        existing_receipt = {
            "session_id": sessions.document["_id"],
            "transaction_id": 999,
            "status": "completed",
        }
        charger.document["stop_closure_receipts"][receipt_key] = existing_receipt

        with self.assertRaisesRegex(RuntimeError, "recibo de limpieza"):
            await self._send_stop(sessions, charger_collection=charger)

        self.assertEqual(
            charger.document["stop_closure_receipts"][receipt_key],
            existing_receipt,
        )
        self.assertEqual(charger.document["sesion_actual"]["transaction_id"], 456)
        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "pending",
        )

    async def test_concurrent_completed_receipt_wins_over_stale_skip_attempt(self):
        sessions = FakeCollection(active_session())

        class ConcurrentReceiptCharger(FakeCollection):
            injected = False

            async def update_one(self, query, update, upsert=False):
                receipt_paths = [
                    path for path in update.get("$set", {})
                    if path.startswith("stop_closure_receipts.")
                ]
                if (
                    receipt_paths
                    and update["$set"][receipt_paths[0]].get("status")
                    == "skipped_foreign_transaction"
                    and not self.injected
                ):
                    self.injected = True
                    receipt_path = receipt_paths[0]
                    self.document["sesion_actual"] = {"transaction_id": 789}
                    self._set_path(
                        self.document,
                        receipt_path,
                        {
                            "session_id": sessions.document["_id"],
                            "transaction_id": 123,
                            "status": "completed",
                        },
                    )
                return await super().update_one(query, update, upsert=upsert)

        charger = ConcurrentReceiptCharger({
            "charge_point_id": "CP-1",
            "estado_general": "disponible",
            "sesion_actual": {"transaction_id": 456},
        })

        response, _, _, _ = await self._send_stop(
            sessions, charger_collection=charger
        )

        receipt_key = ocpp_server._charger_cleanup_receipt_path(
            sessions.document["_id"]
        ).rsplit(".", 1)[1]
        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(
            charger.document["stop_closure_receipts"][receipt_key]["status"],
            "completed",
        )
        self.assertEqual(charger.document["sesion_actual"]["transaction_id"], 789)
        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "completed",
        )

    async def test_charger_read_failure_keeps_cleanup_pending_and_propagates(self):
        class ReadFailureCharger(FakeCollection):
            async def find_one(self, query):
                raise RuntimeError("charger read failed")

        sessions = FakeCollection(active_session())
        charger = ReadFailureCharger({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 123},
        })

        with self.assertRaisesRegex(RuntimeError, "charger read failed"):
            await self._send_stop(sessions, charger_collection=charger)

        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "pending",
        )
        self.assertEqual(charger.update_calls, [])

    async def test_retry_preserves_terminal_skipped_cleanup_status(self):
        sessions = FakeCollection(active_session())
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 456},
        })

        await self._send_stop(sessions, charger_collection=charger)
        update_count = len(charger.update_calls)
        charger.document["sesion_actual"] = {"transaction_id": 123}

        response, _, _, _ = await self._send_stop(
            sessions, charger_collection=charger
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(len(charger.update_calls), update_count)
        self.assertEqual(charger.document["sesion_actual"]["transaction_id"], 123)
        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "skipped_foreign_transaction",
        )
        self.assertEqual(
            sessions.document["stop_closure"][
                "charger_cleanup_observed_transaction_id"
            ],
            456,
        )

    async def test_concurrent_incompatible_cleanup_results_do_not_overwrite(self):
        session = active_session()
        session["estado"] = "finalizada"
        session["stop_closure"] = {
            "rewards_applied": True,
            "rewards_granted": True,
            "charger_cleanup_status": "pending",
            "event_recorded": True,
        }
        sessions = FakeCollection(session)

        results = await asyncio.gather(
            ocpp_server._mark_charger_cleanup_status(
                sessions, session, 123, "completed"
            ),
            ocpp_server._mark_charger_cleanup_status(
                sessions, session, 123, "skipped_foreign_transaction", 456
            ),
            return_exceptions=True,
        )

        self.assertEqual(sum(result is None for result in results), 1)
        self.assertEqual(
            sum(isinstance(result, RuntimeError) for result in results), 1
        )
        status = sessions.document["stop_closure"]["charger_cleanup_status"]
        self.assertIn(status, {"completed", "skipped_foreign_transaction"})
        if status == "skipped_foreign_transaction":
            self.assertEqual(
                sessions.document["stop_closure"][
                    "charger_cleanup_observed_transaction_id"
                ],
                456,
            )

    async def test_legacy_ambiguous_charger_cleared_state_is_rejected(self):
        session = active_session()
        session["estado"] = "finalizada"
        session["stop_closure"] = {
            "rewards_applied": True,
            "rewards_granted": True,
            "charger_cleared": True,
            "event_recorded": True,
        }
        sessions = FakeCollection(session)
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "disponible",
            "sesion_actual": None,
        })

        with self.assertRaisesRegex(RuntimeError, "estado antiguo y ambiguo"):
            await self._send_stop(sessions, charger_collection=charger)

        self.assertTrue(sessions.document["stop_closure"]["charger_cleared"])
        self.assertNotIn(
            "charger_cleanup_status", sessions.document["stop_closure"]
        )
        self.assertEqual(charger.update_calls, [])

    async def test_reward_stage_failure_retries_without_repeating_completed_apply(self):
        sessions = FakeCollection(active_session())
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 123},
        })
        events = FakeCollection()
        applied_rewards = []

        async def apply_once(user_id, session_id):
            if "session-1" not in applied_rewards:
                applied_rewards.append(session_id)
            return {
                "tipo": "carga_gratuita",
                "mensaje": "Carga gratuita aplicada",
            }

        granted_rewards = []

        async def grant_then_fail_once(user_id):
            if not granted_rewards:
                granted_rewards.append(user_id)
                raise RuntimeError("reward grant write failed after insert")

        apply_reward = AsyncMock(side_effect=apply_once)
        grant_reward = AsyncMock(side_effect=grant_then_fail_once)

        with self.assertRaisesRegex(RuntimeError, "reward grant write failed"):
            await self._send_stop(
                sessions,
                charger_collection=charger,
                event_collection=events,
                apply_reward=apply_reward,
                grant_reward=grant_reward,
            )

        self.assertTrue(sessions.document["stop_closure"]["rewards_applied"])
        self.assertFalse(sessions.document["stop_closure"]["rewards_granted"])
        self.assertEqual(len(applied_rewards), 1)
        self.assertEqual(len(granted_rewards), 1)
        self.assertIsNone(events.document)

        response, _, _, _ = await self._send_stop(
            sessions,
            charger_collection=charger,
            event_collection=events,
            apply_reward=apply_reward,
            grant_reward=grant_reward,
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(len(applied_rewards), 1)
        self.assertEqual(len(granted_rewards), 1)
        self.assertEqual(apply_reward.await_count, 1)
        self.assertEqual(grant_reward.await_count, 2)
        self.assertTrue(sessions.document["stop_closure"]["rewards_granted"])
        self.assertEqual(charger.document["estado_general"], "disponible")
        self.assertEqual(events.document["_id"], "stop:CP-1:123")

    async def test_event_write_with_lost_ack_is_idempotent_on_retry(self):
        class FailAfterFirstWrite(FakeCollection):
            fail = True

            async def update_one(self, query, update, upsert=False):
                result = await super().update_one(query, update, upsert=upsert)
                if self.fail:
                    self.fail = False
                    raise RuntimeError("event write acknowledgement lost")
                return result

        sessions = FakeCollection(active_session())
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 123},
        })
        events = FailAfterFirstWrite()
        apply_reward = AsyncMock(return_value={"tipo": "ninguna", "mensaje": ""})
        grant_reward = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "acknowledgement lost"):
            await self._send_stop(
                sessions,
                charger_collection=charger,
                event_collection=events,
                apply_reward=apply_reward,
                grant_reward=grant_reward,
            )

        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "completed",
        )
        self.assertFalse(sessions.document["stop_closure"]["event_recorded"])
        self.assertEqual(events.document["_id"], "stop:CP-1:123")

        response, _, _, _ = await self._send_stop(
            sessions,
            charger_collection=charger,
            event_collection=events,
            apply_reward=apply_reward,
            grant_reward=grant_reward,
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(events.document["_id"], "stop:CP-1:123")
        self.assertEqual(len(events.update_calls), 2)
        self.assertTrue(sessions.document["stop_closure"]["event_recorded"])
        self.assertEqual(apply_reward.await_count, 1)
        self.assertEqual(grant_reward.await_count, 1)

    async def test_event_insert_failure_retries_pending_event_stage(self):
        class FailBeforeFirstWrite(FakeCollection):
            fail = True

            async def update_one(self, query, update, upsert=False):
                if self.fail:
                    self.fail = False
                    self.update_calls.append((query, update))
                    raise RuntimeError("event insert failed")
                return await super().update_one(query, update, upsert=upsert)

        sessions = FakeCollection(active_session())
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 123},
        })
        events = FailBeforeFirstWrite()
        apply_reward = AsyncMock(return_value={"tipo": "ninguna", "mensaje": ""})
        grant_reward = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "event insert failed"):
            await self._send_stop(
                sessions,
                charger_collection=charger,
                event_collection=events,
                apply_reward=apply_reward,
                grant_reward=grant_reward,
            )

        self.assertIsNone(events.document)
        self.assertFalse(sessions.document["stop_closure"]["event_recorded"])
        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "completed",
        )

        response, _, _, _ = await self._send_stop(
            sessions,
            charger_collection=charger,
            event_collection=events,
            apply_reward=apply_reward,
            grant_reward=grant_reward,
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(events.document["_id"], "stop:CP-1:123")
        self.assertTrue(sessions.document["stop_closure"]["event_recorded"])
        self.assertEqual(apply_reward.await_count, 1)
        self.assertEqual(grant_reward.await_count, 1)

    async def test_stop_preserves_charger_reference_for_another_transaction(self):
        sessions = FakeCollection(active_session())
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 456},
        })

        response, _, _, _ = await self._send_stop(
            sessions, charger_collection=charger
        )

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(sessions.document["estado"], "finalizada")
        self.assertEqual(charger.document["estado_general"], "ocupado")
        self.assertEqual(charger.document["sesion_actual"]["transaction_id"], 456)
        self.assertEqual(
            sessions.document["stop_closure"]["charger_cleanup_status"],
            "skipped_foreign_transaction",
        )
        self.assertEqual(
            sessions.document["stop_closure"][
                "charger_cleanup_observed_transaction_id"
            ],
            456,
        )

    async def test_unknown_transaction_does_not_touch_sessions_or_rewards(self):
        sessions = FakeCollection()
        charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "disponible",
            "sesion_actual": None,
        })
        apply_reward = AsyncMock()
        grant_reward = AsyncMock()

        response, collections, _, _ = await self._send_stop(
            sessions,
            charger_collection=charger,
            apply_reward=apply_reward,
            grant_reward=grant_reward,
        )

        self.assertEqual(response.id_tag_info["status"], "Invalid")
        self.assertIsNone(sessions.document)
        self.assertEqual(sessions.update_calls, [])
        self.assertEqual(charger.update_calls, [])
        apply_reward.assert_not_awaited()
        grant_reward.assert_not_awaited()
        self.assertEqual(
            collections["eventos_ocpp"].document["_id"], "stop:CP-1:123"
        )


class RewardServiceMemoryCollection:
    def __init__(self, documents=None, *, fail_after_upsert_once=False):
        self.documents = list(documents or [])
        self.fail_after_upsert_once = fail_after_upsert_once
        self.update_calls = []

    async def find_one(self, query):
        return next(
            (
                document.copy()
                for document in self.documents
                if self._matches(document, query)
            ),
            None,
        )

    def find(self, query):
        async def matching_documents():
            for document in self.documents:
                if self._matches(document, query):
                    yield document.copy()

        return matching_documents()

    async def count_documents(self, query):
        return sum(self._matches(document, query) for document in self.documents)

    async def update_one(self, query, update, upsert=False):
        self.update_calls.append((query, update))
        document = next(
            (
                item
                for item in self.documents
                if self._matches(item, query)
            ),
            None,
        )
        if document is not None:
            for path, value in update.get("$set", {}).items():
                FakeCollection._set_path(document, path, value)
            return SimpleNamespace(matched_count=1, modified_count=1)
        if not upsert:
            return SimpleNamespace(matched_count=0, modified_count=0)

        document = {}
        for path, value in update.get("$setOnInsert", {}).items():
            FakeCollection._set_path(document, path, value)
        self.documents.append(document)
        if self.fail_after_upsert_once:
            self.fail_after_upsert_once = False
            raise RuntimeError("reward insert acknowledgement lost")
        return SimpleNamespace(
            matched_count=0, modified_count=0, upserted_id=document.get("_id")
        )

    @classmethod
    def _matches(cls, document, query):
        for key, value in query.items():
            if key in {"$or", "$and"}:
                results = [cls._matches(document, condition) for condition in value]
                if key == "$or" and not any(results):
                    return False
                if key == "$and" and not all(results):
                    return False
                continue
            actual = document
            for part in key.split("."):
                actual = actual.get(part) if isinstance(actual, dict) else None
            if isinstance(value, dict):
                for operator, expected in value.items():
                    if operator == "$lte" and not actual <= expected:
                        return False
                    if operator == "$gte" and not actual >= expected:
                        return False
                    if operator == "$exists" and (actual is not None) != expected:
                        return False
            elif actual != value:
                return False
        return True


class RewardServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_retry_recovers_reward_claim_without_consuming_another(self):
        usuario_id = ObjectId()
        sesion_id = ObjectId()
        recompensa_id = ObjectId()
        session = {
            "_id": sesion_id,
            "stop_closure": {
                "reward_selection_complete": True,
                "reward_id": recompensa_id,
            },
            "es_carga_gratuita": False,
            "beneficio_aplicado": None,
        }

        class FailSessionBenefitOnce(RewardServiceMemoryCollection):
            fail = True

            async def update_one(self, query, update, upsert=False):
                if "beneficio_aplicado" in update.get("$set", {}) and self.fail:
                    self.fail = False
                    raise RuntimeError("session benefit write failed")
                return await super().update_one(query, update, upsert=upsert)

        sessions = FailSessionBenefitOnce([session])
        rewards = RewardServiceMemoryCollection([{
            "_id": recompensa_id,
            "usuario_id": usuario_id,
            "tipo": "carga_gratuita",
            "usado": False,
            "sesion_id_uso": None,
        }])
        collections = {"sesiones": sessions, "recompensas": rewards}

        with patch.object(
            reward_service, "get_collection", side_effect=collections.__getitem__
        ):
            with self.assertRaisesRegex(RuntimeError, "session benefit write failed"):
                await reward_service.aplicar_recompensa_en_sesion(
                    str(usuario_id), str(sesion_id)
                )
            result = await reward_service.aplicar_recompensa_en_sesion(
                str(usuario_id), str(sesion_id)
            )

        self.assertEqual(result["tipo"], "carga_gratuita")
        self.assertTrue(session["es_carga_gratuita"])
        self.assertEqual(
            session["beneficio_aplicado"], "Carga gratuita por recompensa"
        )
        self.assertTrue(rewards.documents[0]["usado"])
        self.assertEqual(rewards.documents[0]["sesion_id_uso"], sesion_id)
        self.assertEqual(len(rewards.documents), 1)
        self.assertEqual(
            sum(
                update.get("$set", {}).get("usado") is True
                for _, update in rewards.update_calls
            ),
            1,
        )

    async def test_stop_without_explicit_reward_selection_keeps_reward_available(self):
        usuario_id = ObjectId()
        sesion_id = ObjectId()
        recompensa_id = ObjectId()
        session = {
            "_id": sesion_id,
            "stop_closure": {
                "reward_selection_complete": False,
                "reward_id": None,
            },
            "es_carga_gratuita": False,
            "beneficio_aplicado": None,
        }
        rewards = RewardServiceMemoryCollection([{
            "_id": recompensa_id,
            "usuario_id": usuario_id,
            "tipo": "carga_gratuita",
            "usado": False,
            "sesion_id_uso": None,
        }])
        collections = {
            "sesiones": RewardServiceMemoryCollection([session]),
            "recompensas": rewards,
        }

        with patch.object(
            reward_service, "get_collection", side_effect=collections.__getitem__
        ):
            result = await reward_service.aplicar_recompensa_en_sesion(
                str(usuario_id), str(sesion_id)
            )

        self.assertEqual(result["tipo"], "ninguna")
        self.assertFalse(rewards.documents[0]["usado"])
        self.assertIsNone(rewards.documents[0]["sesion_id_uso"])
        self.assertFalse(session["es_carga_gratuita"])
        self.assertIsNone(session["beneficio_aplicado"])

    async def test_grant_retry_after_insert_ack_loss_does_not_duplicate_reward(self):
        usuario_id = ObjectId()
        regla_id = ObjectId()
        catalogo_id = ObjectId()
        ahora = datetime.utcnow()
        users = RewardServiceMemoryCollection([{"_id": usuario_id}])
        sessions = RewardServiceMemoryCollection([{
            "_id": ObjectId(),
            "usuario_id": usuario_id,
            "estado": "finalizada",
        }])
        rewards = RewardServiceMemoryCollection(fail_after_upsert_once=True)
        rules = RewardServiceMemoryCollection([{
            "_id": regla_id,
            "activa": True,
            "cargas_requeridas": 1,
            "recompensa_catalogo_id": catalogo_id,
        }])
        catalog = RewardServiceMemoryCollection([{
            "_id": catalogo_id,
            "activa": True,
            "fecha_inicio": ahora - timedelta(days=1),
            "fecha_finalizacion": ahora + timedelta(days=1),
            "nombre": "Carga",
            "empresa": "Proveedor",
            "descripcion": "Carga gratuita",
            "condiciones": "",
        }])
        collections = {
            "usuarios": users,
            "sesiones": sessions,
            "recompensas": rewards,
            "recompensa_reglas": rules,
            "recompensa_catalogo": catalog,
        }

        with patch.object(
            reward_service, "get_collection", side_effect=collections.__getitem__
        ):
            with self.assertRaisesRegex(RuntimeError, "acknowledgement lost"):
                await reward_service.verificar_y_otorgar_recompensa(
                    str(usuario_id)
                )
            await reward_service.verificar_y_otorgar_recompensa(str(usuario_id))

        self.assertEqual(users.documents[0]["total_cargas"], 1)
        self.assertEqual(len(rewards.documents), 1)
        self.assertEqual(rewards.documents[0]["usuario_id"], usuario_id)
        self.assertEqual(rewards.documents[0]["recompensa_regla_id"], regla_id)

    async def test_grants_every_active_reward_rule_for_the_completed_load_count(self):
        usuario_id = ObjectId()
        catalogo_ids = [ObjectId(), ObjectId()]
        regla_ids = [ObjectId(), ObjectId()]
        ahora = datetime.utcnow()
        users = RewardServiceMemoryCollection([{"_id": usuario_id}])
        sessions = RewardServiceMemoryCollection([
            {
                "_id": ObjectId(),
                "usuario_id": usuario_id,
                "estado": "finalizada",
            },
        ])
        rewards = RewardServiceMemoryCollection()
        rules = RewardServiceMemoryCollection([
            {
                "_id": regla_id,
                "activa": True,
                "cargas_requeridas": 1,
                "recompensa_catalogo_id": catalogo_id,
            }
            for regla_id, catalogo_id in zip(regla_ids, catalogo_ids)
        ])
        catalog = RewardServiceMemoryCollection([
            {
                "_id": catalogo_id,
                "activa": True,
                "fecha_inicio": ahora - timedelta(days=1),
                "fecha_finalizacion": ahora + timedelta(days=1),
                "nombre": f"Recompensa {index}",
                "empresa": "Proveedor",
                "descripcion": "Beneficio",
                "condiciones": "",
            }
            for index, catalogo_id in enumerate(catalogo_ids, 1)
        ])
        collections = {
            "usuarios": users,
            "sesiones": sessions,
            "recompensas": rewards,
            "recompensa_reglas": rules,
            "recompensa_catalogo": catalog,
        }

        with patch.object(
            reward_service, "get_collection", side_effect=collections.__getitem__
        ):
            await reward_service.verificar_y_otorgar_recompensa(str(usuario_id))

        self.assertEqual(len(rewards.documents), 2)
        self.assertEqual(
            {reward["recompensa_regla_id"] for reward in rewards.documents},
            set(regla_ids),
        )
        self.assertTrue(all(reward["usado"] is False for reward in rewards.documents))

    async def test_grants_a_missed_milestone_once_after_concurrent_session_closures(self):
        usuario_id = ObjectId()
        regla_id = ObjectId()
        catalogo_id = ObjectId()
        ahora = datetime.utcnow()
        users = RewardServiceMemoryCollection([{"_id": usuario_id}])
        sessions = RewardServiceMemoryCollection([
            {
                "_id": ObjectId(),
                "usuario_id": usuario_id,
                "estado": "finalizada",
            }
            for _ in range(3)
        ])
        rewards = RewardServiceMemoryCollection()
        rules = RewardServiceMemoryCollection([{
            "_id": regla_id,
            "activa": True,
            "cargas_requeridas": 2,
            "recompensa_catalogo_id": catalogo_id,
        }])
        catalog = RewardServiceMemoryCollection([{
            "_id": catalogo_id,
            "activa": True,
            "fecha_inicio": ahora - timedelta(days=1),
            "fecha_finalizacion": ahora + timedelta(days=1),
            "nombre": "Hito 2",
            "empresa": "Proveedor",
            "descripcion": "Beneficio",
            "condiciones": "",
        }])
        collections = {
            "usuarios": users,
            "sesiones": sessions,
            "recompensas": rewards,
            "recompensa_reglas": rules,
            "recompensa_catalogo": catalog,
        }

        with patch.object(
            reward_service, "get_collection", side_effect=collections.__getitem__
        ):
            await reward_service.verificar_y_otorgar_recompensa(str(usuario_id))
            await reward_service.verificar_y_otorgar_recompensa(str(usuario_id))

        self.assertEqual(len(rewards.documents), 1)
        self.assertEqual(rewards.documents[0]["cargas_requeridas"], 2)
        self.assertEqual(rewards.documents[0]["cargas_acumuladas"], 3)
        self.assertEqual(rewards.documents[0]["snapshot_nombre"], "Hito 2")


class StartTransactionCollection:
    def __init__(
        self,
        documents=None,
        *,
        find_error=None,
        insert_error=None,
        update_error=None,
        enforce_active_client_unique=False,
    ):
        self.documents = list(documents or [])
        self.name = None
        self.find_error = find_error
        self.insert_error = insert_error
        self.update_error = update_error
        self.enforce_active_client_unique = enforce_active_client_unique
        self.find_calls = []
        self.insert_calls = []
        self.update_calls = []
        self.session_calls = []

    async def find_one(self, query, *, session=None):
        self.find_calls.append(query)
        if session:
            self.session_calls.append(session)
            await session.before_find(self, query)
        if self.find_error:
            raise self.find_error
        for document in self.documents:
            if all(self._get_path(document, key) == value for key, value in query.items()):
                return document.copy()
        return None

    def find(self, query):
        documents = [
            document.copy()
            for document in self.documents
            if all(self._get_path(document, key) == value for key, value in query.items())
        ]
        return MemoryAsyncCursor(documents)

    async def insert_one(self, document, *, session=None):
        if self.enforce_active_client_unique and document.get("estado") in {
            "activa",
            "iniciada",
        }:
            if any(
                existing.get("usuario_id") == document.get("usuario_id")
                and existing.get("estado") in {"activa", "iniciada"}
                for existing in self.documents
            ):
                raise DuplicateKeyError(
                    "index: one_active_session_per_client dup key"
                )
        self.insert_calls.append(document.copy())
        if self.insert_error:
            raise self.insert_error
        if session:
            self.session_calls.append(session)
            session.record_write()
        self.documents.append(document.copy())

    async def update_one(self, query, update, *, session=None):
        self.update_calls.append((query, update))
        if session:
            self.session_calls.append(session)
            await session.before_write(self, query)
        if self.update_error:
            raise self.update_error
        for document in self.documents:
            if all(self._get_path(document, key) == value for key, value in query.items()):
                if session:
                    session.record_write()
                for path, value in update.get("$set", {}).items():
                    parts = path.split(".")
                    target = document
                    for part in parts[:-1]:
                        target = target.setdefault(part, {})
                    target[parts[-1]] = value
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    @staticmethod
    def _get_path(document, path):
        value = document
        for part in path.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value


class StartTransactionTestCoordinator:
    """Test-only conflict model; it does not prove MongoDB transaction behavior."""

    def __init__(self, synchronize_active_reads=False):
        self.synchronize_active_reads = synchronize_active_reads
        self.active_read_count = 0
        self.active_reads_ready = asyncio.Event()
        self.write_owners = {}
        self.write_released = {}

    async def before_find(self, session, collection, query):
        if (
            not self.synchronize_active_reads
            or collection.name != "sesiones"
            or query.get("estado") != "activa"
            or session.waited_for_active_read
        ):
            return
        session.waited_for_active_read = True
        self.active_read_count += 1
        if self.active_read_count >= 2:
            self.active_reads_ready.set()
        await self.active_reads_ready.wait()

    async def before_write(self, session, collection, query):
        if collection.name != "cargadores":
            return
        charge_point_id = query["charge_point_id"]
        owner = self.write_owners.get(charge_point_id)
        if owner is None:
            self.write_owners[charge_point_id] = session
            self.write_released.setdefault(charge_point_id, asyncio.Event())
            return
        if owner is session:
            return

        await self.write_released[charge_point_id].wait()
        raise SimulatedTransientTransactionError(
            "simulated transaction write conflict"
        )

    def release_write(self, session):
        for charge_point_id, owner in list(self.write_owners.items()):
            if owner is session:
                self.write_released[charge_point_id].set()
                del self.write_owners[charge_point_id]


class StartTransactionTestSession:
    def __init__(self, client):
        self.client = client
        self.coordinator = client.coordinator
        self.waited_for_active_read = False
        self.snapshot = None
        self.writes_started = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if self.snapshot is not None:
            await self.abort_transaction()

    def start_transaction(self):
        self.snapshot = {
            collection: copy.deepcopy(collection.documents)
            for collection in self.client.collections.values()
        }
        self.writes_started = False

    async def commit_transaction(self):
        self.snapshot = None
        self.coordinator.release_write(self)

    async def abort_transaction(self):
        if self.snapshot is not None and self.writes_started:
            for collection, documents in self.snapshot.items():
                collection.documents = documents
        self.snapshot = None
        self.coordinator.release_write(self)

    async def before_find(self, collection, query):
        await self.coordinator.before_find(self, collection, query)

    async def before_write(self, collection, query):
        await self.coordinator.before_write(self, collection, query)

    def record_write(self):
        self.writes_started = True


class StartTransactionTestClient:
    def __init__(self, collections, coordinator=None):
        self.collections = collections
        self.coordinator = coordinator or StartTransactionTestCoordinator()

    async def start_session(self):
        return StartTransactionTestSession(self)


class SimulatedTransientTransactionError(OperationFailure):
    def has_error_label(self, label):
        return (
            label == "TransientTransactionError"
            or super().has_error_label(label)
        )


class MemoryAsyncCursor:
    def __init__(self, documents):
        self.documents = documents

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for document in self.documents:
            yield document


class StartTransactionTests(unittest.IsolatedAsyncioTestCase):
    def _collections(self, sessions, charger=None):
        collections = {
            "nfc_cards": StartTransactionCollection([{
                "_id": "card-1",
                "uid": "TAG-1",
                "estado": "activa",
                "usuario_id": "user-1",
            }]),
            "usuarios": StartTransactionCollection([{
                "_id": "user-1",
                "activo": True,
            }]),
            "vehiculos": StartTransactionCollection([{
                "_id": "vehicle-1",
                "usuario_id": "user-1",
                "activo": True,
            }]),
            "nfc_charge_requests": StartTransactionCollection([{
                "_id": "request-1",
                "charge_point_id": "CP-1",
                "uid": "TAG-1",
                "usuario_id": "user-1",
                "status": "start_requested",
                "vehicle_id": "vehicle-1",
                "connector_id": 1,
                "expires_at": datetime.utcnow() + timedelta(minutes=2),
            }]),
            "sesiones": sessions,
            "cargadores": charger or StartTransactionCollection([{
                "charge_point_id": "CP-1",
                "estado_general": "disponible",
                "sesion_actual": None,
            }]),
            "eventos_ocpp": StartTransactionCollection(),
        }
        for name, collection in collections.items():
            collection.name = name
        return collections

    async def _send_start(self, collections, transaction_id=456):
        handler = ocpp_server.ChargePointHandler("CP-1", None)
        with (
            patch.object(
                ocpp_server, "get_collection", side_effect=collections.__getitem__
            ),
            patch.object(
                ocpp_server.db, "client", StartTransactionTestClient(collections)
            ),
            patch.object(
                ocpp_server, "next_transaction_id", new_callable=AsyncMock,
                return_value=transaction_id,
            ) as next_id,
        ):
            response = await handler.on_start_transaction(
                1, "TAG-1", 1000, datetime(2024, 1, 1)
            )
        return response, next_id

    async def test_charger_without_active_session_starts_normally(self):
        sessions = StartTransactionCollection()
        collections = self._collections(sessions)

        response, next_id = await self._send_start(collections)

        self.assertEqual(response.transaction_id, 456)
        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(len(sessions.insert_calls), 1)
        self.assertEqual(sessions.insert_calls[0]["charge_point_id"], "CP-1")
        self.assertEqual(sessions.insert_calls[0]["estado"], "activa")
        self.assertEqual(sessions.insert_calls[0]["meter_start"], 1000)
        self.assertEqual(len(collections["cargadores"].update_calls), 1)
        self.assertEqual(
            collections["cargadores"].update_calls[0][1]["$set"]["sesion_actual"][
                "transaction_id"
            ],
            456,
        )
        next_id.assert_awaited_once()

    async def test_existing_active_session_rejects_start_without_changes(self):
        existing_session = {
            "charge_point_id": "CP-1",
            "transaction_id": 123,
            "estado": "activa",
            "usuario_id": "existing-user",
            "vehiculo_id": "existing-vehicle",
            "meter_start": 2500,
            "energia_consumida_kwh": 1.5,
        }
        charger_document = {
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {
                "transaction_id": 123,
                "usuario_id": "existing-user",
                "vehiculo_id": "existing-vehicle",
            },
        }
        sessions = StartTransactionCollection([existing_session])
        charger = StartTransactionCollection([charger_document.copy()])
        original_session = existing_session.copy()
        original_charger = charger_document.copy()
        collections = self._collections(sessions, charger)

        response, next_id = await self._send_start(collections)

        self.assertEqual(response.transaction_id, 0)
        self.assertEqual(response.id_tag_info["status"], "ConcurrentTx")
        self.assertEqual(sessions.documents, [original_session])
        self.assertEqual(sessions.insert_calls, [])
        self.assertEqual(charger.documents[0], original_charger)
        self.assertEqual(charger.update_calls, [])
        self.assertEqual(len(collections["eventos_ocpp"].insert_calls), 1)
        self.assertEqual(
            collections["eventos_ocpp"].insert_calls[0]["payload"]["resultado"],
            "ConcurrentTx",
        )
        next_id.assert_not_awaited()

    async def test_finalized_session_does_not_block_new_start(self):
        sessions = StartTransactionCollection([{
            "charge_point_id": "CP-1",
            "transaction_id": 123,
            "estado": "finalizada",
        }])
        collections = self._collections(sessions)

        response, _ = await self._send_start(collections)

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(len(sessions.insert_calls), 1)
        self.assertEqual(sessions.find_calls, [{
            "charge_point_id": "CP-1",
            "estado": "activa",
        }])

    async def test_active_session_for_another_charger_does_not_block_start(self):
        sessions = StartTransactionCollection([{
            "charge_point_id": "CP-OTHER",
            "transaction_id": 123,
            "estado": "activa",
        }])
        collections = self._collections(sessions)

        response, _ = await self._send_start(collections)

        self.assertEqual(response.id_tag_info["status"], "Accepted")
        self.assertEqual(len(sessions.insert_calls), 1)

    async def test_database_failure_while_checking_active_session_propagates(self):
        sessions = StartTransactionCollection(find_error=RuntimeError("database read failed"))
        collections = self._collections(sessions)

        with self.assertRaisesRegex(RuntimeError, "database read failed"):
            await self._send_start(collections)

        self.assertEqual(sessions.insert_calls, [])
        self.assertEqual(collections["cargadores"].update_calls, [])

    async def test_session_insert_failure_does_not_update_charger_or_record_event(self):
        sessions = StartTransactionCollection(
            insert_error=RuntimeError("session insert failed")
        )
        collections = self._collections(sessions)

        with self.assertRaisesRegex(RuntimeError, "session insert failed"):
            await self._send_start(collections)

        self.assertEqual(len(sessions.insert_calls), 1)
        self.assertEqual(sessions.documents, [])
        self.assertEqual(len(collections["cargadores"].update_calls), 1)
        self.assertEqual(
            collections["cargadores"].documents[0]["estado_general"], "disponible"
        )
        self.assertEqual(collections["eventos_ocpp"].insert_calls, [])

    async def test_charger_update_failure_rolls_back_the_start_transaction(self):
        sessions = StartTransactionCollection()
        charger = StartTransactionCollection(
            [{"charge_point_id": "CP-1", "activo": True}],
            update_error=RuntimeError("charger update failed"),
        )
        charger.name = "cargadores"
        collections = self._collections(sessions, charger)

        with self.assertRaisesRegex(RuntimeError, "charger update failed"):
            await self._send_start(collections)

        self.assertEqual(sessions.insert_calls, [])
        self.assertEqual(sessions.documents, [])
        self.assertEqual(len(charger.update_calls), 1)
        self.assertEqual(len(collections["eventos_ocpp"].insert_calls), 0)

    async def test_boot_sees_no_partial_session_after_start_charger_update_failure(self):
        sessions = StartTransactionCollection()
        charger = StartTransactionCollection(
            [{"charge_point_id": "CP-1", "activo": True, "estado_general": "disponible"}],
            update_error=RuntimeError("charger update failed"),
        )
        charger.name = "cargadores"
        collections = self._collections(sessions, charger)
        original_charger = copy.deepcopy(charger.documents)

        with self.assertRaisesRegex(RuntimeError, "charger update failed"):
            await self._send_start(collections)

        self.assertEqual(charger.documents, original_charger)
        self.assertEqual(sessions.documents, [])

        charger.update_error = None
        handler = ocpp_server.ChargePointHandler("CP-1", None)
        with patch.object(
            ocpp_server, "get_collection", side_effect=collections.__getitem__
        ):
            boot_response = await handler.on_boot_notification("Suntree", "Model")

        self.assertEqual(boot_response.status, "Accepted")
        self.assertEqual(charger.documents[0]["estado_general"], "disponible")
        self.assertEqual(sessions.documents, [])
        self.assertIsNone(charger.documents[0].get("sesion_actual"))

    async def test_missing_charger_does_not_leave_a_session_or_report_success(self):
        sessions = StartTransactionCollection()
        charger = StartTransactionCollection()
        charger.name = "cargadores"
        collections = self._collections(sessions, charger)

        with self.assertRaisesRegex(RuntimeError, "No se encontró el cargador CP-1"):
            await self._send_start(collections)

        self.assertEqual(sessions.documents, [])
        self.assertEqual(sessions.insert_calls, [])
        self.assertEqual(charger.update_calls[0][0], {"charge_point_id": "CP-1"})
        self.assertEqual(collections["eventos_ocpp"].insert_calls, [])

    async def test_event_failure_rolls_back_session_and_charger_updates(self):
        sessions = StartTransactionCollection()
        collections = self._collections(sessions)
        original_charger = copy.deepcopy(collections["cargadores"].documents)
        collections["eventos_ocpp"].insert_error = RuntimeError(
            "event insert failed"
        )

        with self.assertRaisesRegex(RuntimeError, "event insert failed"):
            await self._send_start(collections)

        self.assertEqual(sessions.documents, [])
        self.assertEqual(len(collections["cargadores"].update_calls), 1)
        self.assertEqual(collections["cargadores"].documents, original_charger)
        self.assertEqual(len(collections["eventos_ocpp"].insert_calls), 1)

    async def test_retried_start_payload_does_not_create_a_second_session(self):
        sessions = StartTransactionCollection()
        collections = self._collections(sessions)

        first_response, _ = await self._send_start(collections)
        retry_response, retry_next_id = await self._send_start(collections)

        self.assertEqual(first_response.id_tag_info["status"], "Accepted")
        self.assertEqual(first_response.transaction_id, 456)
        self.assertEqual(retry_response.id_tag_info["status"], "ConcurrentTx")
        self.assertEqual(retry_response.transaction_id, 0)
        self.assertEqual(len(sessions.documents), 1)
        retry_next_id.assert_not_awaited()

    async def test_retry_after_event_failure_does_not_duplicate_session(self):
        sessions = StartTransactionCollection()
        collections = self._collections(sessions)
        events = collections["eventos_ocpp"]
        events.insert_error = RuntimeError("event insert failed")

        with self.assertRaisesRegex(RuntimeError, "event insert failed"):
            await self._send_start(collections)

        events.insert_error = None
        retry_response, retry_next_id = await self._send_start(
            collections, transaction_id=457
        )

        self.assertEqual(retry_response.id_tag_info["status"], "Accepted")
        self.assertEqual(retry_response.transaction_id, 457)
        self.assertEqual(len(sessions.documents), 1)
        self.assertEqual(sessions.documents[0]["transaction_id"], 457)
        self.assertEqual(events.documents[-1]["payload"]["transaction_id"], 457)
        retry_next_id.assert_awaited_once()

    async def test_simultaneous_starts_conflict_and_only_one_is_accepted(self):
        sessions = StartTransactionCollection()
        collections = self._collections(sessions)
        coordinator = StartTransactionTestCoordinator(
            synchronize_active_reads=True
        )
        client = StartTransactionTestClient(collections, coordinator)
        handlers = [
            ocpp_server.ChargePointHandler("CP-1", None)
            for _ in range(2)
        ]

        with (
            patch.object(
                ocpp_server, "get_collection", side_effect=collections.__getitem__
            ),
            patch.object(ocpp_server.db, "client", client),
            patch.object(
                ocpp_server,
                "next_transaction_id",
                new_callable=AsyncMock,
                side_effect=[456, 457],
            ),
        ):
            responses = await asyncio.gather(*[
                handler.on_start_transaction(1, "TAG-1", 1000, datetime(2024, 1, 1))
                for handler in handlers
            ])

        self.assertCountEqual(
            [response.id_tag_info["status"] for response in responses],
            ["Accepted", "ConcurrentTx"],
        )
        self.assertEqual(
            [response.transaction_id for response in responses].count(0), 1
        )
        self.assertEqual(
            sum(
                session["estado"] == "activa"
                for session in sessions.documents
            ),
            1,
        )
        self.assertEqual(
            collections["cargadores"].documents[0]["sesion_actual"][
                "transaction_id"
            ],
            sessions.documents[0]["transaction_id"],
        )
        self.assertEqual(len(collections["eventos_ocpp"].documents), 2)

    async def test_active_session_on_another_charger_rejects_new_start(self):
        existing_session = {
            "charge_point_id": "CP-2",
            "transaction_id": 789,
            "estado": "activa",
            "usuario_id": "user-1",
        }
        sessions = StartTransactionCollection(
            [existing_session],
            enforce_active_client_unique=True,
        )
        charger = StartTransactionCollection([
            {"charge_point_id": "CP-1", "sesion_actual": None},
            {
                "charge_point_id": "CP-2",
                "sesion_actual": {
                    "transaction_id": 789,
                    "usuario_id": "user-1",
                },
            },
        ])
        collections = self._collections(sessions, charger)
        response, next_id = await self._send_start(collections)

        self.assertEqual(response.id_tag_info["status"], "ConcurrentTx")
        self.assertEqual(response.transaction_id, 0)
        self.assertEqual(sessions.documents, [existing_session])
        self.assertEqual(
            collections["eventos_ocpp"].insert_calls[-1]["payload"]["resultado"],
            "ConcurrentTx",
        )
        next_id.assert_awaited_once()

    async def test_transient_transaction_conflicts_retry_only_three_times(self):
        sessions = StartTransactionCollection()
        transient_error = SimulatedTransientTransactionError(
            "simulated transient conflict"
        )
        charger = StartTransactionCollection(
            [{"charge_point_id": "CP-1", "estado_general": "disponible"}],
            update_error=transient_error,
        )
        charger.name = "cargadores"
        collections = self._collections(sessions, charger)
        transaction_ids = AsyncMock(side_effect=[456, 457, 458])
        handler = ocpp_server.ChargePointHandler("CP-1", None)

        with (
            patch.object(
                ocpp_server, "get_collection", side_effect=collections.__getitem__
            ),
            patch.object(
                ocpp_server.db, "client", StartTransactionTestClient(collections)
            ),
            patch.object(ocpp_server, "next_transaction_id", transaction_ids),
        ):
            with self.assertRaises(OperationFailure):
                await handler.on_start_transaction(
                    1, "TAG-1", 1000, datetime(2024, 1, 1)
                )

        self.assertEqual(len(charger.update_calls), 3)
        self.assertEqual(transaction_ids.await_count, 3)
        self.assertEqual(sessions.documents, [])
        self.assertEqual(charger.documents[0]["estado_general"], "disponible")
        self.assertEqual(collections["eventos_ocpp"].documents, [])

    async def test_blocked_nfc_still_rejects_without_starting_transaction(self):
        sessions = StartTransactionCollection()
        collections = self._collections(sessions)
        collections["nfc_cards"].documents[0]["estado"] = "bloqueada"

        response, next_id = await self._send_start(collections)

        self.assertEqual(response.transaction_id, 0)
        self.assertEqual(response.id_tag_info["status"], "Invalid")
        self.assertEqual(sessions.documents, [])
        self.assertEqual(collections["cargadores"].update_calls, [])
        self.assertEqual(len(collections["eventos_ocpp"].documents), 1)
        next_id.assert_not_awaited()


class OCPPBasicHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_heartbeat_updates_charger_and_persists_event_and_heartbeat(self):
        charger = StartTransactionCollection([{"charge_point_id": "CP-1"}])
        events = StartTransactionCollection()
        heartbeats = StartTransactionCollection()
        collections = {
            "cargadores": charger,
            "eventos_ocpp": events,
            "heartbeats": heartbeats,
        }
        handler = ocpp_server.ChargePointHandler("CP-1", None)

        with patch.object(
            ocpp_server, "get_collection", side_effect=collections.__getitem__
        ):
            response = await handler.on_heartbeat()

        self.assertIsNotNone(response.current_time)
        self.assertIsNotNone(
            datetime.fromisoformat(
                response.current_time.replace("Z", "+00:00")
            ).utcoffset()
        )
        self.assertEqual(charger.documents[0]["ultimo_estado_conocido"], "Heartbeat")
        self.assertEqual(len(events.documents), 1)
        self.assertEqual(events.documents[0]["tipo_mensaje"], "Heartbeat")
        self.assertEqual(len(heartbeats.documents), 1)

    async def test_data_transfer_is_acknowledged_as_unknown_vendor_and_logged(self):
        events = StartTransactionCollection()
        handler = ocpp_server.ChargePointHandler("CP-1", None)

        with patch.object(
            ocpp_server, "get_collection", return_value=events
        ):
            response = await handler.on_data_transfer(
                "ExampleVendor",
                message_id="VendorStatus",
                data='{"state":"ready"}',
            )

        self.assertEqual(response.status.value, "UnknownVendorId")
        self.assertEqual(events.documents[0]["tipo_mensaje"], "DataTransfer")
        self.assertEqual(
            events.documents[0]["payload"]["vendor_id"], "ExampleVendor"
        )

    async def test_diagnostics_and_firmware_status_notifications_are_acknowledged(self):
        events = StartTransactionCollection()
        handler = ocpp_server.ChargePointHandler("CP-1", None)
        self.assertTrue({
            "DataTransfer",
            "DiagnosticsStatusNotification",
            "FirmwareStatusNotification",
        }.issubset(handler.route_map))

        with patch.object(
            ocpp_server, "get_collection", return_value=events
        ):
            diagnostics = await handler.on_diagnostics_status_notification(
                "Uploaded"
            )
            firmware = await handler.on_firmware_status_notification(
                "Installed"
            )

        self.assertIsNotNone(diagnostics)
        self.assertIsNotNone(firmware)
        self.assertEqual(
            [event["tipo_mensaje"] for event in events.documents],
            ["DiagnosticsStatusNotification", "FirmwareStatusNotification"],
        )

    async def test_authorize_accepts_only_active_cards_and_records_each_result(self):
        card = {
            "_id": "card-1",
            "uid": "TAG-1",
            "estado": "activa",
            "usuario_id": "user-1",
        }
        cards = StartTransactionCollection([card])
        events = StartTransactionCollection()
        users = StartTransactionCollection([{"_id": "user-1", "activo": True}])
        requests = StartTransactionCollection([{
            "charge_point_id": "CP-1",
            "uid": "TAG-1",
            "usuario_id": "user-1",
            "status": "start_requested",
            "expires_at": datetime.utcnow() + timedelta(minutes=2),
        }])
        collections = {
            "nfc_cards": cards,
            "usuarios": users,
            "nfc_charge_requests": requests,
            "eventos_ocpp": events,
        }
        handler = ocpp_server.ChargePointHandler("CP-1", None)

        with patch.object(
            ocpp_server, "get_collection", side_effect=collections.__getitem__
        ):
            accepted = await handler.on_authorize("TAG-1")
            card["estado"] = "bloqueada"
            blocked = await handler.on_authorize("TAG-1")
            unknown = await handler.on_authorize("UNKNOWN")

        self.assertEqual(accepted.id_tag_info["status"], "Accepted")
        self.assertEqual(blocked.id_tag_info["status"], "Invalid")
        self.assertEqual(unknown.id_tag_info["status"], "Invalid")
        self.assertEqual(
            [event["payload"]["resultado"] for event in events.documents],
            ["Accepted", "Invalid", "Invalid"],
        )

    async def test_status_notification_updates_connector_general_state_and_event(self):
        charger = Mock()
        charger.update_one = AsyncMock(
            side_effect=[
                SimpleNamespace(matched_count=1),
                SimpleNamespace(matched_count=1),
            ]
        )
        events = Mock()
        events.insert_one = AsyncMock()
        notifications = Mock()
        notifications.insert_one = AsyncMock()
        logs = Mock()
        logs.insert_one = AsyncMock()
        collections = {
            "cargadores": charger,
            "eventos_ocpp": events,
            "status_notifications": notifications,
            "logs": logs,
        }
        handler = ocpp_server.ChargePointHandler("CP-1", None)

        with patch.object(
            ocpp_server, "get_collection", side_effect=collections.__getitem__
        ):
            response = await handler.on_status_notification(
                1,
                "Faulted",
                "GroundFailure",
                info="simulated",
            )

        self.assertIsNotNone(response)
        self.assertEqual(charger.update_one.await_count, 1)
        connector_update = charger.update_one.await_args_list[0]
        self.assertEqual(
            connector_update.args[0],
            {"charge_point_id": "CP-1"},
        )
        self.assertIsInstance(connector_update.args[1], list)
        self.assertIn("$map", str(connector_update.args[1]))
        self.assertIn("$concatArrays", str(connector_update.args[1]))
        self.assertNotIn("$push", str(connector_update.args[1]))
        self.assertIn("$setIntersection", str(connector_update.args[1]))
        self.assertIn("Charging", str(connector_update.args[1]))
        events.insert_one.assert_awaited_once()
        self.assertEqual(
            events.insert_one.await_args.args[0]["payload"]["status"], "Faulted"
        )
        notifications.insert_one.assert_awaited_once()
        logs.insert_one.assert_awaited_once()


class ChargerConnectorModelTests(unittest.TestCase):
    def test_connector_accepts_unlisted_connector_standards(self):
        connector = Conector(
            id_conector=1,
            tipo="  NACS  ",
            potencia_max_kw=150,
        )

        self.assertEqual(connector.tipo, "NACS")


class FakeChargerCollection:
    def __init__(self, document=None):
        self.document = document
        if (
            self.document is not None
            and self.document.get("activo")
            and not self.document.get("ocpp_password_hash")
        ):
            self.document["ocpp_password_hash"] = TEST_OCPP_PASSWORD_HASH
        self.update_calls = []
        self.insert_calls = []

    async def find_one(self, query):
        if self._matches(query):
            return self.document.copy()
        return None

    async def update_one(self, query, update, **kwargs):
        self.update_calls.append((query, update, kwargs))
        if not self._matches(query):
            return SimpleNamespace(matched_count=0, modified_count=0)
        for path, value in update.get("$set", {}).items():
            target = self.document
            parts = path.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        for key in update.get("$unset", {}):
            self.document.pop(key, None)
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def insert_one(self, document):
        self.insert_calls.append(document)

    def _matches(self, query):
        if self.document is None:
            return False
        for key, value in query.items():
            if key == "$or":
                if not any(self._matches(condition) for condition in value):
                    return False
                continue
            actual = self.document
            exists = True
            for part in key.split("."):
                if not isinstance(actual, dict) or part not in actual:
                    exists = False
                    actual = None
                    break
                actual = actual.get(part)
            if isinstance(value, dict) and "$exists" in value:
                if exists != value["$exists"]:
                    return False
                continue
            if actual != value:
                return False
        return True


class FakeWebSocket:
    def __init__(
        self, protocols="ocpp1.6j", on_accept=None, accept_error=None, close_error=None
    ):
        self.headers = {
            "sec-websocket-protocol": protocols,
            "authorization": "Basic dGVzdDp0ZXN0",
        }
        self.accepted_subprotocol = None
        self.accept_calls = []
        self.close_calls = []
        self.on_accept = on_accept
        self.accept_error = accept_error
        self.close_error = close_error

    async def accept(self, subprotocol=None):
        self.accept_calls.append(subprotocol)
        self.accepted_subprotocol = subprotocol
        if self.accept_error:
            raise self.accept_error
        if self.on_accept:
            self.on_accept()

    async def close(self, code=1000, reason=None):
        self.close_calls.append((code, reason))
        if self.close_error:
            raise self.close_error


TEST_OCPP_PASSWORD = "test-ocpp-password"
TEST_OCPP_PASSWORD_HASH = hash_ocpp_password(TEST_OCPP_PASSWORD)


async def connect_ocpp(websocket, charge_point_id):
    credentials = base64.b64encode(
        f"{charge_point_id}:{TEST_OCPP_PASSWORD}".encode("utf-8")
    ).decode("ascii")
    websocket.headers["authorization"] = f"Basic {credentials}"
    await ocpp_server.ocpp_endpoint(websocket, charge_point_id)


class OCPPAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def assert_connection_cleaned(self, charge_point_id, manager=None):
        manager = manager or ocpp_server.ocpp_connection_manager
        self.assertNotIn(charge_point_id, manager.pending_connections)
        self.assertIsNone(manager.get_connection(charge_point_id))
        self.assertIsNone(manager.get_handler(charge_point_id))
        self.assertIsNone(manager.get_connection_token(charge_point_id))
        self.assertIsNone(manager.get_disconnect_event(charge_point_id))
        self.assertNotIn(charge_point_id, ocpp_server.connected_chargers)

    async def test_registered_active_charger_can_connect(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
            "ocpp_password_hash": TEST_OCPP_PASSWORD_HASH,
        })
        websocket = FakeWebSocket()

        async def inspect_registered_connection(handler):
            self.assertIs(
                ocpp_server.ocpp_connection_manager.get_connection(charge_point_id),
                websocket,
            )
            self.assertIs(
                ocpp_server.ocpp_connection_manager.get_handler(charge_point_id),
                handler,
            )
            self.assertTrue(charger.document["conectado"])

        with (
            patch.object(ocpp_server, "get_collection", return_value=charger),
            patch.object(
                ocpp_server,
                "parse_ocpp_basic_auth",
                return_value=(charge_point_id, "test-password"),
            ),
            patch.object(ocpp_server, "verify_ocpp_password", return_value=True),
            patch.object(
                ocpp_server.ChargePointHandler,
                "start",
                inspect_registered_connection,
            ),
            patch("builtins.print"),
        ):
            try:
                await connect_ocpp(websocket, charge_point_id)
            finally:
                ocpp_server.ocpp_connection_manager.unregister(
                    charge_point_id, websocket
                )

        self.assertEqual(websocket.accepted_subprotocol, "ocpp1.6j")
        self.assertEqual(websocket.accept_calls, ["ocpp1.6j"])
        self.assertEqual(websocket.close_calls, [])
        self.assertFalse(charger.document["conectado"])
        self.assert_connection_cleaned(charge_point_id)

    async def test_unknown_charger_is_rejected_without_registration_or_creation(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection()
        websocket = FakeWebSocket()

        with (
            patch.object(ocpp_server, "get_collection", return_value=charger),
            self.assertLogs("app.websocket.ocpp_server", level="INFO") as captured,
        ):
            await connect_ocpp(websocket, charge_point_id)

        self.assertIsNone(charger.document)
        self.assertEqual(charger.insert_calls, [])
        self.assertEqual(charger.update_calls, [])
        self.assert_connection_cleaned(charge_point_id)
        self.assertIsNone(websocket.accepted_subprotocol)
        self.assertEqual(websocket.close_calls[0][0], 1008)
        self.assertIn(
            f"Intento de conexión OCPP recibido para {charge_point_id}",
            captured.output[0],
        )
        self.assertIn(
            "Conexión OCPP rechazada",
            captured.output[1],
        )
        self.assertIn("no registrado o deshabilitado", captured.output[1])

    async def test_invalid_ocpp_password_is_logged_without_exposing_password(self):
        charge_point_id = "CP-LOGIN"
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
            "ocpp_password_hash": "stored-password-hash",
        })
        websocket = FakeWebSocket()

        with (
            patch.object(ocpp_server, "get_collection", return_value=charger),
            patch.object(
                ocpp_server,
                "parse_ocpp_basic_auth",
                return_value=(charge_point_id, "sensitive-test-password"),
            ),
            patch.object(ocpp_server, "verify_ocpp_password", return_value=False),
            self.assertLogs("app.websocket.ocpp_server", level="WARNING") as captured,
        ):
            await connect_ocpp(websocket, charge_point_id)

        self.assertEqual(websocket.close_calls[0][0], 1008)
        self.assertIn("La contraseña OCPP no coincide", captured.output[0])
        self.assertNotIn("sensitive-test-password", captured.output[0])
        self.assertEqual(charger.update_calls, [])

    async def test_disabled_charger_is_rejected_and_stays_disabled(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": False,
        })
        websocket = FakeWebSocket()

        with patch.object(ocpp_server, "get_collection", return_value=charger):
            await connect_ocpp(websocket, charge_point_id)

        self.assertFalse(charger.document["activo"])
        self.assertEqual(charger.update_calls, [])
        self.assert_connection_cleaned(charge_point_id)
        self.assertEqual(websocket.close_calls[0][0], 1008)

    async def test_connection_without_supported_ocpp_subprotocol_is_rejected(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
        })
        websocket = FakeWebSocket(protocols="chat, unknown")

        with patch.object(ocpp_server, "get_collection", return_value=charger):
            await connect_ocpp(websocket, charge_point_id)

        self.assertEqual(websocket.accept_calls, [])
        self.assertEqual(websocket.close_calls[0][0], 1008)
        self.assertEqual(charger.update_calls, [])
        self.assert_connection_cleaned(charge_point_id)

    async def test_connection_using_ocpp_16_without_j_is_rejected(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
        })
        websocket = FakeWebSocket(protocols="ocpp1.6")

        with patch.object(ocpp_server, "get_collection", return_value=charger):
            await connect_ocpp(websocket, charge_point_id)

        self.assertEqual(websocket.accept_calls, [])
        self.assertEqual(websocket.close_calls[0][0], 1008)
        self.assertEqual(charger.update_calls, [])
        self.assert_connection_cleaned(charge_point_id)

    async def test_connection_using_uppercase_ocpp_subprotocol_is_rejected(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
        })
        websocket = FakeWebSocket(protocols="ocpp1.6J")

        with patch.object(ocpp_server, "get_collection", return_value=charger):
            await connect_ocpp(websocket, charge_point_id)

        self.assertEqual(websocket.accept_calls, [])
        self.assertEqual(websocket.close_calls[0][0], 1008)
        self.assertEqual(charger.update_calls, [])
        self.assert_connection_cleaned(charge_point_id)

    async def test_charger_disabled_during_handshake_is_not_registered(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
        })
        websocket = FakeWebSocket(on_accept=lambda: charger.document.update({
            "activo": False,
        }))

        with (
            patch.object(ocpp_server, "get_collection", return_value=charger),
            patch("builtins.print"),
        ):
            await connect_ocpp(websocket, charge_point_id)

        self.assertFalse(charger.document["activo"])
        self.assertNotIn("conectado", charger.document)
        self.assert_connection_cleaned(charge_point_id)
        self.assertEqual(websocket.close_calls[0][0], 1008)

    async def test_close_error_after_accept_still_cleans_connection(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
        })
        websocket = FakeWebSocket(
            on_accept=lambda: charger.document.update({"activo": False}),
            close_error=RuntimeError("socket close failed"),
        )

        with (
            patch.object(ocpp_server, "get_collection", return_value=charger),
            self.assertLogs("app.websocket.ocpp_server", level="ERROR"),
        ):
            await connect_ocpp(websocket, charge_point_id)

        self.assert_connection_cleaned(charge_point_id)
        self.assertFalse(charger.document["activo"])

    async def test_duplicate_connection_does_not_replace_existing_connection(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
        })
        incumbent = FakeWebSocket()
        candidate = FakeWebSocket()
        manager = ocpp_server.ocpp_connection_manager
        manager.register(charge_point_id, incumbent)

        try:
            with patch.object(ocpp_server, "get_collection", return_value=charger):
                await connect_ocpp(candidate, charge_point_id)

            self.assertIs(manager.get_connection(charge_point_id), incumbent)
            self.assertEqual(incumbent.close_calls, [])
            self.assertEqual(candidate.close_calls[0][0], 1008)
            self.assertEqual(charger.update_calls, [])
        finally:
            manager.unregister(charge_point_id, incumbent)

    async def test_late_unregister_does_not_remove_new_websocket_state(self):
        charge_point_id = f"CP-{uuid4().hex}"
        manager = ocpp_server.OCPPConnectionManager()
        old_websocket = FakeWebSocket()
        new_websocket = FakeWebSocket()
        old_token = manager.register(charge_point_id, old_websocket)
        new_token = manager.register(charge_point_id, new_websocket)
        new_event = manager.get_disconnect_event(charge_point_id)
        new_handler = object()
        manager.register_handler(charge_point_id, new_handler)

        manager.unregister(charge_point_id, old_websocket)

        self.assertIs(manager.get_connection(charge_point_id), new_websocket)
        self.assertIs(manager.get_handler(charge_point_id), new_handler)
        self.assertEqual(manager.get_connection_token(charge_point_id), new_token)
        self.assertNotEqual(old_token, new_token)
        self.assertIs(manager.get_disconnect_event(charge_point_id), new_event)
        self.assertFalse(new_event.is_set())
        manager.unregister(charge_point_id, new_websocket)

    async def test_handler_construction_failure_cleans_connection_and_reservation(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
        })
        websocket = FakeWebSocket()

        with (
            patch.object(ocpp_server, "get_collection", return_value=charger),
            patch.object(
                ocpp_server,
                "ChargePointHandler",
                side_effect=RuntimeError("handler construction failed"),
            ),
            patch("builtins.print"),
        ):
            with self.assertRaisesRegex(RuntimeError, "handler construction failed"):
                await connect_ocpp(websocket, charge_point_id)

        self.assertEqual(len(websocket.close_calls), 1)
        self.assert_connection_cleaned(charge_point_id)
        self.assertFalse(charger.document["conectado"])
        self.assertNotIn("connection_token", charger.document)

    async def test_handler_registration_failure_cleans_connection_and_reservation(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
        })
        websocket = FakeWebSocket()
        manager = ocpp_server.ocpp_connection_manager
        original_register_handler = manager.register_handler

        def register_then_fail(cp_id, handler):
            original_register_handler(cp_id, handler)
            raise RuntimeError("handler registration failed")

        with (
            patch.object(ocpp_server, "get_collection", return_value=charger),
            patch.object(
                manager,
                "register_handler",
                side_effect=register_then_fail,
            ),
            patch("builtins.print"),
        ):
            with self.assertRaisesRegex(RuntimeError, "handler registration failed"):
                await connect_ocpp(websocket, charge_point_id)

        self.assertEqual(len(websocket.close_calls), 1)
        self.assert_connection_cleaned(charge_point_id)
        self.assertFalse(charger.document["conectado"])
        self.assertNotIn("connection_token", charger.document)

    async def test_accept_error_releases_reservation(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
        })
        websocket = FakeWebSocket(accept_error=RuntimeError("accept failed"))

        with (
            patch.object(ocpp_server, "get_collection", return_value=charger),
            patch("builtins.print"),
        ):
            with self.assertRaisesRegex(RuntimeError, "accept failed"):
                await connect_ocpp(websocket, charge_point_id)

        self.assert_connection_cleaned(charge_point_id)
        self.assertEqual(charger.update_calls, [])

    async def test_mongodb_error_during_establishment_cleans_memory_and_closes(self):
        class FailingUpdateCollection(FakeChargerCollection):
            async def update_one(self, query, update, **kwargs):
                self.update_calls.append((query, update, kwargs))
                if len(self.update_calls) == 1:
                    self.document.update(update.get("$set", {}))
                    raise RuntimeError("MongoDB update failed")
                return await super().update_one(query, update, **kwargs)

        charge_point_id = f"CP-{uuid4().hex}"
        charger = FailingUpdateCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
        })
        websocket = FakeWebSocket()

        with (
            patch.object(ocpp_server, "get_collection", return_value=charger),
            patch("builtins.print"),
        ):
            with self.assertRaisesRegex(RuntimeError, "MongoDB update failed"):
                await connect_ocpp(websocket, charge_point_id)

        self.assertEqual(len(websocket.close_calls), 1)
        self.assert_connection_cleaned(charge_point_id)
        self.assertFalse(charger.document.get("conectado", False))
        self.assertNotIn("connection_token", charger.document)

    async def test_mongodb_lookup_error_closes_websocket_without_reserving(self):
        class FailingLookupCollection(FakeChargerCollection):
            async def find_one(self, query):
                raise RuntimeError("MongoDB lookup failed")

        charge_point_id = f"CP-{uuid4().hex}"
        charger = FailingLookupCollection()
        websocket = FakeWebSocket()

        with (
            patch.object(ocpp_server, "get_collection", return_value=charger),
            patch("builtins.print"),
        ):
            with self.assertRaisesRegex(RuntimeError, "MongoDB lookup failed"):
                await connect_ocpp(websocket, charge_point_id)

        self.assertEqual(len(websocket.close_calls), 1)
        self.assert_connection_cleaned(charge_point_id)

    async def test_rejected_websocket_close_error_does_not_leak_reservation(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection()
        websocket = FakeWebSocket(close_error=RuntimeError("socket close failed"))

        with (
            patch.object(ocpp_server, "get_collection", return_value=charger),
            self.assertLogs("app.websocket.ocpp_server", level="ERROR"),
        ):
            await connect_ocpp(websocket, charge_point_id)

        self.assert_connection_cleaned(charge_point_id)
        self.assertIsNone(charger.document)

    async def test_concurrent_connections_for_same_charger_admit_only_one(self):
        class ConcurrentChargerCollection(FakeChargerCollection):
            def __init__(self, document):
                super().__init__(document)
                self.find_barrier = asyncio.Barrier(2)

            async def find_one(self, query):
                charger = await super().find_one(query)
                await self.find_barrier.wait()
                return charger

        charge_point_id = f"CP-{uuid4().hex}"
        charger = ConcurrentChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
        })
        first = FakeWebSocket()
        second = FakeWebSocket()
        connection_started = asyncio.Event()
        release_connection = asyncio.Event()
        manager = ocpp_server.ocpp_connection_manager

        async def hold_connection(handler):
            connection_started.set()
            await release_connection.wait()

        with (
            patch.object(ocpp_server, "get_collection", return_value=charger),
            patch.object(ocpp_server.ChargePointHandler, "start", hold_connection),
            patch("builtins.print"),
        ):
            tasks = [
                asyncio.create_task(
                    connect_ocpp(websocket, charge_point_id)
                )
                for websocket in (first, second)
            ]
            try:
                await asyncio.wait_for(connection_started.wait(), timeout=1)
                for _ in range(10):
                    if first.close_calls or second.close_calls:
                        break
                    await asyncio.sleep(0)

                self.assertEqual(
                    sum(websocket.accepted_subprotocol == "ocpp1.6j"
                        for websocket in (first, second)),
                    1,
                )
                self.assertEqual(
                    sum(bool(websocket.close_calls) for websocket in (first, second)),
                    1,
                )
                self.assertIsNotNone(manager.get_connection(charge_point_id))
            finally:
                release_connection.set()
                await asyncio.gather(*tasks)

        self.assertIsNone(manager.get_connection(charge_point_id))

    async def test_boot_notification_updates_authorized_charger_without_enabling_it(self):
        charger = FakeChargerCollection({
            "charge_point_id": "CP-1",
            "activo": True,
        })
        events = FakeCollection()
        collections = {
            "cargadores": charger,
            "sesiones": StartTransactionCollection(),
            "eventos_ocpp": events,
        }
        handler = ocpp_server.ChargePointHandler("CP-1", None, "connection-token")

        with patch.object(
            ocpp_server, "get_collection", side_effect=collections.__getitem__
        ):
            response = await handler.on_boot_notification(
                "Suntree", "SWG5T-7/32", firmware_version="1.2.3"
            )

        self.assertEqual(response.status, "Accepted")
        self.assertIsNotNone(
            datetime.fromisoformat(
                response.current_time.replace("Z", "+00:00")
            ).utcoffset()
        )
        query, update, kwargs = charger.update_calls[0]
        self.assertEqual(query, {"charge_point_id": "CP-1", "activo": True})
        self.assertNotIn("activo", update["$set"])
        self.assertEqual(kwargs, {})
        self.assertEqual(charger.document["fabricante"], "Suntree")
        self.assertEqual(charger.document["modelo"], "SWG5T-7/32")
        self.assertEqual(charger.document["firmware_version"], "1.2.3")
        self.assertTrue(charger.document["activo"])
        self.assertEqual(len(events.insert_calls), 1)

    async def test_boot_notification_preserves_negotiated_ocpp16j_subprotocol(self):
        charger = FakeChargerCollection({
            "charge_point_id": "CP-1",
            "activo": True,
        })
        collections = {
            "cargadores": charger,
            "sesiones": StartTransactionCollection(),
            "eventos_ocpp": FakeCollection(),
        }
        handler = ocpp_server.ChargePointHandler(
            "CP-1",
            None,
            negotiated_subprotocol="ocpp1.6j",
        )

        with patch.object(
            ocpp_server, "get_collection", side_effect=collections.__getitem__
        ):
            response = await handler.on_boot_notification("Suntree", "Model")

        self.assertEqual(response.status, "Accepted")
        self.assertEqual(charger.document["subprotocolo_ocpp"], "ocpp1.6j")

    async def test_boot_notification_rejects_disabled_charger_without_changes(self):
        original = {"charge_point_id": "CP-1", "activo": False}
        charger = FakeChargerCollection(original.copy())
        events = FakeCollection()
        collections = {
            "cargadores": charger,
            "sesiones": StartTransactionCollection(),
            "eventos_ocpp": events,
        }
        handler = ocpp_server.ChargePointHandler("CP-1", None)

        with patch.object(
            ocpp_server, "get_collection", side_effect=collections.__getitem__
        ):
            response = await handler.on_boot_notification("Vendor", "Model")

        self.assertEqual(response.status, "Rejected")
        self.assertEqual(charger.document, original)
        self.assertEqual(charger.update_calls[0][2], {})
        self.assertEqual(len(events.insert_calls), 1)

    async def test_boot_notification_does_not_upsert_unknown_charger(self):
        charger = FakeChargerCollection()
        events = FakeCollection()
        collections = {
            "cargadores": charger,
            "sesiones": StartTransactionCollection(),
            "eventos_ocpp": events,
        }
        handler = ocpp_server.ChargePointHandler("CP-unknown", None)

        with patch.object(
            ocpp_server, "get_collection", side_effect=collections.__getitem__
        ):
            response = await handler.on_boot_notification("Vendor", "Model")

        self.assertEqual(response.status, "Rejected")
        self.assertIsNone(charger.document)
        self.assertEqual(charger.update_calls[0][2], {})
        self.assertEqual(charger.insert_calls, [])
        self.assertEqual(len(events.insert_calls), 1)


class OCPPSessionConnectionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def _boot_collections(self, charger, sessions, events=None):
        return {
            "cargadores": charger,
            "sesiones": sessions,
            "eventos_ocpp": events or FakeCollection(),
        }

    async def _boot(self, collections, *, assert_logs=None):
        handler = ocpp_server.ChargePointHandler("CP-1", None, "boot-token")
        with patch.object(
            ocpp_server, "get_collection", side_effect=collections.__getitem__
        ):
            if assert_logs:
                with self.assertLogs("app.websocket.ocpp_server", level=assert_logs):
                    response = await handler.on_boot_notification("Suntree", "Model")
            else:
                response = await handler.on_boot_notification("Suntree", "Model")
        return response

    async def test_boot_restores_reference_for_exactly_one_active_session(self):
        session = active_session()
        session.update({
            "vehiculo_id": "vehicle-1",
            "fecha_inicio": datetime(2024, 1, 1),
            "energia_consumida_kwh": 2.4,
        })
        charger = FakeChargerCollection({
            "charge_point_id": "CP-1",
            "activo": True,
            "estado_general": "fuera_de_servicio",
            "sesion_actual": None,
        })
        collections = self._boot_collections(
            charger, StartTransactionCollection([session])
        )

        response = await self._boot(collections)

        self.assertEqual(response.status, "Accepted")
        self.assertEqual(charger.document["estado_general"], "ocupado")
        self.assertEqual(charger.document["sesion_actual"], {
            "transaction_id": 123,
            "usuario_id": "user-1",
            "vehiculo_id": "vehicle-1",
            "hora_inicio": datetime(2024, 1, 1),
            "energia_consumida_kwh": 2.4,
        })
        self.assertEqual(collections["sesiones"].documents, [session])

    async def test_boot_without_active_sessions_uses_normal_availability(self):
        charger = FakeChargerCollection({
            "charge_point_id": "CP-1",
            "activo": True,
            "sesion_actual": None,
        })
        collections = self._boot_collections(charger, StartTransactionCollection())

        response = await self._boot(collections)

        self.assertEqual(response.status, "Accepted")
        self.assertEqual(charger.document["estado_general"], "disponible")
        self.assertIsNone(charger.document["sesion_actual"])
        self.assertEqual(collections["sesiones"].insert_calls, [])

    async def test_boot_with_multiple_active_sessions_does_not_choose_or_report_available(self):
        sessions = StartTransactionCollection([
            active_session(),
            {**active_session(), "transaction_id": 124, "_id": "session-2"},
        ])
        charger = FakeChargerCollection({
            "charge_point_id": "CP-1",
            "activo": True,
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 999},
        })
        original = charger.document.copy()
        collections = self._boot_collections(charger, sessions)

        response = await self._boot(collections, assert_logs="ERROR")

        self.assertEqual(response.status, "Accepted")
        self.assertEqual(charger.document["estado_general"], original["estado_general"])
        self.assertEqual(charger.document["sesion_actual"], original["sesion_actual"])
        self.assertEqual(len(sessions.documents), 2)
        self.assertEqual(sessions.insert_calls, [])
        self.assertNotIn("estado_general", charger.update_calls[0][1]["$set"])
        self.assertNotIn("sesion_actual", charger.update_calls[0][1]["$set"])

    async def test_boot_session_lookup_error_is_logged_and_rejected(self):
        class FailingSessionLookup(StartTransactionCollection):
            def find(self, query):
                raise RuntimeError("session query failed")

        charger = FakeChargerCollection({
            "charge_point_id": "CP-1",
            "activo": True,
            "estado_general": "fuera_de_servicio",
            "sesion_actual": None,
        })
        collections = self._boot_collections(charger, FailingSessionLookup())

        response = await self._boot(collections, assert_logs="ERROR")

        self.assertEqual(response.status, "Rejected")
        self.assertEqual(charger.update_calls, [])
        self.assertEqual(charger.document["estado_general"], "fuera_de_servicio")

    async def test_boot_charger_update_error_is_logged_and_rejected(self):
        class UncertainChargerUpdate(FakeChargerCollection):
            async def update_one(self, query, update, **kwargs):
                self.update_calls.append((query, update, kwargs))
                self.document.update(update.get("$set", {}))
                raise RuntimeError("charger update outcome uncertain")

        charger = UncertainChargerUpdate({
            "charge_point_id": "CP-1",
            "activo": True,
            "estado_general": "fuera_de_servicio",
            "sesion_actual": None,
        })
        collections = self._boot_collections(charger, StartTransactionCollection())

        response = await self._boot(collections, assert_logs="ERROR")

        self.assertEqual(response.status, "Rejected")
        self.assertEqual(charger.document["estado_general"], "disponible")
        self.assertEqual(len(collections["sesiones"].documents), 0)
        self.assertEqual(collections["eventos_ocpp"].insert_calls, [])

    async def test_disconnect_preserves_active_session_and_charger_reference(self):
        charge_point_id = f"CP-{uuid4().hex}"
        session = active_session(charge_point_id)
        session["energia_consumida_kwh"] = 0.7
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
            "estado_general": "ocupado",
            "sesion_actual": {
                "transaction_id": session["transaction_id"],
                "energia_consumida_kwh": 0.7,
            },
        })
        original_energy = session["energia_consumida_kwh"]
        collections = {
            "cargadores": charger,
            "sesiones": FakeCollection(session),
            "logs": FakeCollection(),
        }
        websocket = FakeWebSocket()
        disconnected = asyncio.Event()

        async def disconnect_handler(handler):
            disconnected.set()
            raise ocpp_server.WebSocketDisconnect(code=1000)

        with (
            patch.object(
                ocpp_server, "get_collection", side_effect=collections.__getitem__
            ),
            patch.object(
                ocpp_server.ChargePointHandler, "start", disconnect_handler
            ),
            patch("builtins.print"),
        ):
            await connect_ocpp(websocket, charge_point_id)

        self.assertTrue(disconnected.is_set())
        self.assertEqual(collections["sesiones"].document["estado"], "activa")
        self.assertEqual(
            collections["sesiones"].document["energia_consumida_kwh"],
            original_energy,
        )
        self.assertEqual(
            charger.document["sesion_actual"]["transaction_id"],
            session["transaction_id"],
        )
        self.assertEqual(charger.document["estado_general"], "fuera_de_servicio")
        self.assertFalse(charger.document["conectado"])
        self.assertNotIn("connection_token", charger.document)

    async def test_stop_after_reconnect_clears_matching_restored_reference(self):
        session = active_session()
        session["fecha_inicio"] = datetime.utcnow() - timedelta(minutes=5)
        charger = FakeChargerCollection({
            "charge_point_id": "CP-1",
            "activo": True,
            "estado_general": "fuera_de_servicio",
            "sesion_actual": None,
        })
        sessions = StartTransactionCollection([session])
        events = FakeCollection()
        boot_collections = self._boot_collections(charger, sessions, events)
        boot_response = await self._boot(boot_collections)

        self.assertEqual(boot_response.status, "Accepted")
        self.assertEqual(charger.document["sesion_actual"]["transaction_id"], 123)

        collections = {
            "sesiones": sessions,
            "cargadores": charger,
            "logs": FakeCollection(),
            "eventos_ocpp": events,
        }
        handler = ocpp_server.ChargePointHandler("CP-1", None)

        with (
            patch.object(ocpp_server, "get_collection", side_effect=collections.__getitem__),
            patch.object(
                ocpp_server, "aplicar_recompensa_en_sesion", new_callable=AsyncMock,
                return_value={"tipo": "ninguna", "mensaje": ""},
            ),
            patch.object(
                ocpp_server, "verificar_y_otorgar_recompensa", new_callable=AsyncMock
            ),
        ):
            await handler.on_stop_transaction(123, 1500, datetime.utcnow())

        self.assertEqual(sessions.documents[0]["estado"], "finalizada")
        self.assertEqual(charger.document["estado_general"], "disponible")
        self.assertIsNone(charger.document["sesion_actual"])

    async def test_stop_with_missing_reference_clears_state_but_not_other_transaction(self):
        session = active_session()
        session["fecha_inicio"] = datetime.utcnow() - timedelta(minutes=5)
        charger_doc = {
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": None,
        }
        sessions = FakeCollection(session)
        charger = FakeCollection(charger_doc)
        collections = {
            "sesiones": sessions,
            "cargadores": charger,
            "logs": FakeCollection(),
            "eventos_ocpp": FakeCollection(),
        }
        handler = ocpp_server.ChargePointHandler("CP-1", None)

        with (
            patch.object(ocpp_server, "get_collection", side_effect=collections.__getitem__),
            patch.object(
                ocpp_server, "aplicar_recompensa_en_sesion", new_callable=AsyncMock,
                return_value={"tipo": "ninguna", "mensaje": ""},
            ),
            patch.object(
                ocpp_server, "verificar_y_otorgar_recompensa", new_callable=AsyncMock
            ),
        ):
            await handler.on_stop_transaction(123, 1500, datetime.utcnow())

        self.assertEqual(sessions.document["estado"], "finalizada")
        self.assertEqual(charger.document["estado_general"], "disponible")
        self.assertIsNone(charger.document["sesion_actual"])

        newer_session = active_session()
        newer_session["fecha_inicio"] = datetime.utcnow() - timedelta(minutes=5)
        newer_charger = FakeCollection({
            "charge_point_id": "CP-1",
            "estado_general": "ocupado",
            "sesion_actual": {"transaction_id": 456},
        })
        newer_collections = {
            "sesiones": FakeCollection(newer_session),
            "cargadores": newer_charger,
            "logs": FakeCollection(),
            "eventos_ocpp": FakeCollection(),
        }
        with (
            patch.object(ocpp_server, "get_collection", side_effect=newer_collections.__getitem__),
            patch.object(
                ocpp_server, "aplicar_recompensa_en_sesion", new_callable=AsyncMock,
                return_value={"tipo": "ninguna", "mensaje": ""},
            ),
            patch.object(
                ocpp_server, "verificar_y_otorgar_recompensa", new_callable=AsyncMock
            ),
        ):
            await handler.on_stop_transaction(123, 1500, datetime.utcnow())

        self.assertEqual(newer_collections["sesiones"].document["estado"], "finalizada")
        self.assertEqual(newer_charger.document["estado_general"], "ocupado")
        self.assertEqual(
            newer_charger.document["sesion_actual"]["transaction_id"], 456
        )
        self.assertEqual(len(newer_charger.update_calls), 1)

    async def test_late_old_endpoint_cleanup_preserves_new_connection_metadata(self):
        charge_point_id = f"CP-{uuid4().hex}"
        charger = FakeChargerCollection({
            "charge_point_id": charge_point_id,
            "activo": True,
        })
        old_websocket = FakeWebSocket()
        new_websocket = FakeWebSocket()
        manager = ocpp_server.ocpp_connection_manager
        started = asyncio.Event()
        release = asyncio.Event()

        async def hold_old_connection(handler):
            started.set()
            await release.wait()

        with (
            patch.object(ocpp_server, "get_collection", return_value=charger),
            patch.object(
                ocpp_server.ChargePointHandler, "start", hold_old_connection
            ),
            patch("builtins.print"),
        ):
            task = asyncio.create_task(
                connect_ocpp(old_websocket, charge_point_id)
            )
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                charger.document.update({
                    "conectado": True,
                    "connection_token": "new-connection-token",
                })
                manager.register(charge_point_id, new_websocket)
                release.set()
                await task
            finally:
                release.set()
                if not task.done():
                    await task
                manager.unregister(charge_point_id, new_websocket)

        self.assertTrue(charger.document["conectado"])
        self.assertEqual(
            charger.document["connection_token"], "new-connection-token"
        )
        self.assertIsNone(manager.get_connection(charge_point_id))


class MeterValuesTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class MeterValuesSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    def start_transaction(self):
        return MeterValuesTransaction()


class MeterValuesClient:
    async def start_session(self):
        return MeterValuesSession()


class MeterValuesCollection:
    def __init__(self, document=None):
        self.document = document
        self.find_calls = []
        self.update_calls = []
        self.insert_calls = []

    def _matches(self, query):
        if self.document is None:
            return False
        for key, value in query.items():
            actual = self.document
            for part in key.split("."):
                if not isinstance(actual, dict):
                    return False
                actual = actual.get(part)
            if actual != value:
                return False
        return True

    async def find_one(self, query, session=None):
        self.find_calls.append(query)
        return self.document.copy() if self._matches(query) else None

    async def update_one(self, query, update, session=None):
        self.update_calls.append((query, update))
        if not self._matches(query):
            return SimpleNamespace(matched_count=0, modified_count=0)
        self.document.update(update.get("$set", {}))
        for key, value in update.get("$max", {}).items():
            self.document[key] = max(self.document.get(key, value), value)
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def insert_one(self, document, session=None):
        self.insert_calls.append(document)


class ClosingSessionBeforeUpdateCollection(MeterValuesCollection):
    async def update_one(self, query, update, session=None):
        self.document["estado"] = "finalizada"
        return await super().update_one(query, update, session=session)


class MeterValuesTests(unittest.IsolatedAsyncioTestCase):
    def _collections(self, session=None):
        return {
            "meter_values": MeterValuesCollection(),
            "telemetria": MeterValuesCollection(),
            "sesiones": MeterValuesCollection(session),
            "logs": MeterValuesCollection(),
        }

    async def _send_meter_values(self, collections, samples=None, timestamp=None):
        handler = ocpp_server.ChargePointHandler("CP-1", None)
        if samples is None:
            samples = [
                {
                    "value": "1500",
                    "measurand": "Energy.Active.Import.Register",
                    "unit": "Wh",
                },
                {
                    "value": "1200",
                    "measurand": "Power.Active.Import",
                    "unit": "W",
                },
            ]
        meter_value = [{
            "timestamp": timestamp or "2024-01-01T00:00:00Z",
            "sampled_value": samples,
        }]
        with patch.object(
            ocpp_server, "get_collection", side_effect=collections.__getitem__
        ), patch.object(
            ocpp_server.db,
            "client",
            MeterValuesClient(),
        ):
            return await handler.on_meter_values(1, 123, meter_value)

    async def test_valid_measurement_updates_session_for_matching_charger(self):
        session = active_session()
        collections = self._collections(session)

        await self._send_meter_values(collections)

        self.assertEqual(
            collections["sesiones"].find_calls[0],
            {
                "transaction_id": 123,
                "charge_point_id": "CP-1",
                "connector_id": 1,
            },
        )
        self.assertEqual(len(collections["sesiones"].update_calls), 1)
        query, update = collections["sesiones"].update_calls[0]
        self.assertEqual(query, {
            "transaction_id": 123,
            "charge_point_id": "CP-1",
            "connector_id": 1,
            "estado": "activa",
        })
        self.assertEqual(session["energia_consumida_kwh"], 0.5)
        self.assertEqual(session["potencia_maxima_kw"], 1.2)
        self.assertEqual(update["$max"], {"potencia_maxima_kw": 1.2})

    async def test_transaction_owned_by_another_charger_is_not_updated(self):
        session = active_session(charge_point_id="CP-OTHER")
        original_session = session.copy()
        collections = self._collections(session)

        await self._send_meter_values(collections)

        self.assertEqual(collections["sesiones"].update_calls, [])
        self.assertEqual(session, original_session)
        self.assertEqual(len(collections["logs"].insert_calls), 1)
        self.assertEqual(len(collections["meter_values"].insert_calls), 0)
        self.assertEqual(len(collections["telemetria"].insert_calls), 0)

    async def test_meter_values_from_another_connector_are_not_persisted(self):
        session = active_session()
        collections = self._collections(session)

        await self._send_meter_values(collections)
        await self._send_meter_values_for_connector(collections, connector_id=2)

        self.assertEqual(len(collections["meter_values"].insert_calls), 1)
        self.assertEqual(len(collections["telemetria"].insert_calls), 2)
        self.assertEqual(len(collections["logs"].insert_calls), 1)

    async def _send_meter_values_for_connector(self, collections, connector_id):
        handler = ocpp_server.ChargePointHandler("CP-1", None)
        meter_value = [{
            "timestamp": "2024-01-01T00:00:00Z",
            "sampled_value": [{"value": "1", "unit": "W"}],
        }]
        with patch.object(
            ocpp_server, "get_collection", side_effect=collections.__getitem__
        ), patch.object(
            ocpp_server.db,
            "client",
            MeterValuesClient(),
        ):
            return await handler.on_meter_values(connector_id, 123, meter_value)

    async def test_nonexistent_transaction_does_not_update_a_session(self):
        collections = self._collections()

        await self._send_meter_values(collections)

        self.assertEqual(collections["sesiones"].update_calls, [])
        self.assertEqual(len(collections["logs"].insert_calls), 1)
        self.assertEqual(len(collections["meter_values"].insert_calls), 0)
        self.assertEqual(len(collections["telemetria"].insert_calls), 0)

    async def test_closed_session_is_not_updated_by_late_meter_values(self):
        session = active_session()
        session["estado"] = "finalizada"
        collections = self._collections(session)

        await self._send_meter_values(collections)

        self.assertEqual(collections["sesiones"].update_calls, [])
        self.assertEqual(session["estado"], "finalizada")
        self.assertEqual(len(collections["logs"].insert_calls), 0)
        self.assertEqual(len(collections["meter_values"].insert_calls), 1)
        self.assertEqual(len(collections["telemetria"].insert_calls), 2)

    async def test_session_closed_after_lookup_is_not_updated(self):
        session = active_session()
        collections = self._collections(session)
        collections["sesiones"] = ClosingSessionBeforeUpdateCollection(session)

        await self._send_meter_values(collections)

        self.assertEqual(session["estado"], "finalizada")
        self.assertNotIn("energia_consumida_kwh", session)
        self.assertNotIn("potencia_maxima_kw", session)
        query, _ = collections["sesiones"].update_calls[0]
        self.assertEqual(query["estado"], "activa")
        self.assertEqual(query["charge_point_id"], "CP-1")

    async def test_invalid_numeric_samples_do_not_partially_persist(self):
        for value in ("not-a-number", "NaN", "Infinity", "-Infinity", "1e9999"):
            with self.subTest(value=value):
                collections = self._collections(active_session())
                samples = [
                    {
                        "value": "1500",
                        "measurand": "Energy.Active.Import.Register",
                        "unit": "Wh",
                    },
                    {
                        "value": value,
                        "measurand": "Energy.Active.Import.Register",
                        "unit": "Wh",
                    },
                ]
                with self.assertRaises(ValueError):
                    await self._send_meter_values(collections, samples=samples)
                self.assertEqual(collections["meter_values"].insert_calls, [])
                self.assertEqual(collections["telemetria"].insert_calls, [])
                self.assertEqual(collections["sesiones"].update_calls, [])

    async def test_invalid_timestamp_does_not_partially_persist(self):
        for timestamp in (
            "not-a-timestamp",
            "2024-01-01T00:00:00",
            "2024-02-30T00:00:00Z",
        ):
            with self.subTest(timestamp=timestamp):
                collections = self._collections(active_session())
                with self.assertRaises(ValueError):
                    await self._send_meter_values(collections, timestamp=timestamp)
                self.assertEqual(collections["meter_values"].insert_calls, [])
                self.assertEqual(collections["telemetria"].insert_calls, [])
                self.assertEqual(collections["sesiones"].update_calls, [])

    async def test_repeated_accumulated_energy_reading_is_not_summed_as_delta(self):
        session = active_session()
        collections = self._collections(session)

        await self._send_meter_values(collections)
        await self._send_meter_values(collections)

        self.assertEqual(session["energia_consumida_kwh"], 0.5)
        self.assertEqual(session["potencia_maxima_kw"], 1.2)
        self.assertEqual(len(collections["sesiones"].update_calls), 2)

    async def test_one_message_updates_session_only_once(self):
        collections = self._collections(active_session())
        samples = [
            {
                "value": "1500",
                "measurand": "Energy.Active.Import.Register",
                "unit": "Wh",
            },
            {
                "value": "1600",
                "measurand": "Energy.Active.Import.Register",
                "unit": "Wh",
            },
        ]

        await self._send_meter_values(collections, samples=samples)

        self.assertEqual(len(collections["sesiones"].update_calls), 1)
        self.assertEqual(
            collections["sesiones"].document["energia_consumida_kwh"], 0.6
        )


if __name__ == "__main__":
    unittest.main()
