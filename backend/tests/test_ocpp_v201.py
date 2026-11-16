import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import ocpp_command_service
from app.websocket import ocpp_server
from app.websocket.ocpp_v201 import (
    OCPP201ChargePointHandler,
    _energy_register_wh,
    _meter_values_to_v16,
)
from app.websocket.ocpp_v21 import OCPP21ChargePointHandler


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

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


class FakeCollection:
    def __init__(self, document=None):
        self.document = document
        self.updates = []

    @staticmethod
    def _matches(document, query):
        for path, expected in query.items():
            value = document
            for key in path.split("."):
                value = value.get(key) if isinstance(value, dict) else None
            if isinstance(expected, dict):
                if "$in" in expected and value not in expected["$in"]:
                    return False
                if "$exists" in expected and (value is not None) != expected["$exists"]:
                    return False
            elif value != expected:
                return False
        return True

    async def find_one(self, query):
        if self.document is not None and self._matches(self.document, query):
            return self.document.copy()
        return None

    def find(self, query):
        documents = (
            [self.document.copy()]
            if self.document is not None and self._matches(self.document, query)
            else []
        )
        return FakeCursor(documents)

    async def update_one(self, query, update):
        self.updates.append((query, update))
        if self.document is None or not self._matches(self.document, query):
            return SimpleNamespace(matched_count=0)
        self.document.update(update.get("$set", {}))
        for path in update.get("$unset", {}):
            self.document.pop(path, None)
        return SimpleNamespace(matched_count=1)


class OCPP201MessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_negotiates_enabled_protocols_using_16_first_policy(self):
        self.assertEqual(
            ocpp_server._select_ocpp_subprotocol(["ocpp1.6j"]),
            "ocpp1.6j",
        )
        self.assertEqual(
            ocpp_server._select_ocpp_subprotocol(["ocpp1.6j", "ocpp2.0.1"]),
            "ocpp1.6j",
        )
        self.assertEqual(
            ocpp_server._select_ocpp_subprotocol(
                ["ocpp1.6j", "ocpp2.0.1", "ocpp2.1"]
            ),
            "ocpp1.6j",
        )
        self.assertEqual(
            ocpp_server._select_ocpp_subprotocol(
                ["ocpp2.0.1", "ocpp2.1"],
                enabled_protocols=["ocpp2.0.1", "ocpp2.1"],
            ),
            "ocpp2.0.1",
        )
        self.assertIsNone(
            ocpp_server._select_ocpp_subprotocol(
                ["ocpp2.0.1"],
                enabled_protocols=["ocpp1.6"],
            )
        )
        self.assertEqual(
            ocpp_server._select_ocpp_subprotocol(
                ["ocpp1.6j", "ocpp2.0.1"],
                automatic_detection=False,
                preferred_protocol="ocpp2.0.1",
            ),
            "ocpp2.0.1",
        )
        self.assertIsNone(
            ocpp_server._select_ocpp_subprotocol(
                ["ocpp1.6j", "ocpp2.0.1"],
                enabled_protocols=["ocpp1.6"],
                automatic_detection=False,
                preferred_protocol="ocpp2.0.1",
            )
        )
        self.assertIsNone(ocpp_server._select_ocpp_subprotocol(["unsupported"]))
        self.assertNotIn("ocpp2.2", ocpp_server.OCPP_SUBPROTOCOLS)
        self.assertEqual(
            ocpp_server.OCPP_SUBPROTOCOLS,
            ("ocpp2.1", "ocpp2.0.1", "ocpp1.6", "ocpp1.6j"),
        )
        self.assertEqual(
            set(ocpp_server.OCPP_PROTOCOL_ADAPTERS),
            set(ocpp_server.OCPP_SUBPROTOCOLS),
        )
        self.assertEqual(
            set(ocpp_server.OCPP_PROTOCOL_ADAPTERS.values()),
            {"v16", "v201", "v21"},
        )
        handler = OCPP201ChargePointHandler("CP-1", None, None, None)
        self.assertEqual(
            {action.value for action in handler.route_map},
            {
                "BootNotification",
                "Heartbeat",
                "Authorize",
                "StatusNotification",
                "MeterValues",
                "TransactionEvent",
            },
        )
        handler_21 = OCPP21ChargePointHandler("CP-1", None, None, None)
        self.assertEqual(
            {action.value for action in handler_21.route_map},
            {action.value for action in handler.route_map},
        )
        self.assertEqual(handler_21.negotiated_subprotocol, "ocpp2.1")

    async def test_ocpp_21_boot_heartbeat_and_authorize_use_21_responses(self):
        legacy = SimpleNamespace(
            on_boot_notification=AsyncMock(
                return_value=SimpleNamespace(
                    current_time="2026-10-12T00:00:00Z",
                    interval=60,
                    status="Accepted",
                )
            ),
            on_heartbeat=AsyncMock(
                return_value=SimpleNamespace(
                    current_time="2026-10-12T00:00:00Z"
                )
            ),
            on_authorize=AsyncMock(
                return_value=SimpleNamespace(
                    id_tag_info={"status": "Accepted"}
                )
            ),
        )
        handler = OCPP21ChargePointHandler("CP-1", None, None, legacy)

        boot = await handler.on_boot_notification(
            {"vendor_name": "Vendor", "model": "Model"},
            "PowerUp",
        )
        heartbeat = await handler.on_heartbeat()
        authorization = await handler.on_authorize(
            {"id_token": "TAG-1", "type": "ISO14443"}
        )

        self.assertEqual(boot.status, "Accepted")
        self.assertEqual(heartbeat.current_time, "2026-10-12T00:00:00Z")
        self.assertEqual(authorization.id_token_info["status"], "Accepted")

    async def test_boot_and_authorize_are_adapted_to_201_responses(self):
        legacy = SimpleNamespace(
            on_boot_notification=AsyncMock(
                return_value=SimpleNamespace(
                    current_time="2026-10-12T00:00:00Z",
                    interval=60,
                    status="Accepted",
                )
            ),
            on_authorize=AsyncMock(
                return_value=SimpleNamespace(
                    id_tag_info={"status": "Accepted"}
                )
            ),
        )
        handler = OCPP201ChargePointHandler("CP-1", None, None, legacy)

        boot = await handler.on_boot_notification(
            {"vendor_name": "Vendor", "model": "Model"},
            "PowerUp",
        )
        authorization = await handler.on_authorize(
            {"id_token": "TAG-1", "type": "ISO14443"}
        )

        self.assertEqual(boot.status, "Accepted")
        self.assertEqual(authorization.id_token_info["status"], "Accepted")
        legacy.on_boot_notification.assert_awaited_once()
        legacy.on_authorize.assert_awaited_once_with(id_tag="TAG-1")

    async def test_status_notification_maps_ocpp_201_occupied_to_internal_charging(self):
        legacy = SimpleNamespace(on_status_notification=AsyncMock())
        handler = OCPP201ChargePointHandler("CP-1", None, None, legacy)

        await handler.on_status_notification(
            "2026-10-12T00:00:00Z",
            "Occupied",
            2,
            1,
        )

        legacy.on_status_notification.assert_awaited_once_with(
            connector_id=1,
            status="Occupied",
            error_code=None,
            evse_id=2,
            ocpp_status="Occupied",
            timestamp="2026-10-12T00:00:00Z",
        )

    async def test_started_transaction_keeps_ocpp_id_separate_from_internal_id(self):
        session = FakeCollection({
            "charge_point_id": "CP-1",
            "transaction_id": 123,
            "estado": "activa",
        })
        legacy = SimpleNamespace(
            on_meter_values=AsyncMock(),
            on_start_transaction=AsyncMock(
                return_value=SimpleNamespace(
                    transaction_id=123,
                    id_tag_info={"status": "Accepted"},
                )
            )
        )
        handler = OCPP201ChargePointHandler("CP-1", None, None, legacy)

        with patch(
            "app.websocket.ocpp_v201.get_collection",
            return_value=session,
        ):
            response = await handler.on_transaction_event(
                "Started",
                "2026-10-12T00:00:00Z",
                "Authorized",
                0,
                {"transaction_id": "opaque-id-1"},
                meter_value=[{
                    "timestamp": "2026-10-12T00:00:00Z",
                    "sampled_value": [{
                        "value": "1.5",
                        "measurand": "Energy.Active.Import.Register",
                        "unit_of_measure": {"unit": "kWh"},
                    }],
                }],
                evse={"id": 2, "connector_id": 1},
                id_token={"id_token": "TAG-1"},
            )

        self.assertEqual(response.id_token_info["status"], "Accepted")
        self.assertEqual(session.document["ocpp_transaction_id"], "opaque-id-1")
        self.assertEqual(session.document["evse_id"], 2)
        self.assertEqual(session.document["subprotocolo_ocpp"], "ocpp2.0.1")
        legacy.on_start_transaction.assert_awaited_once_with(
            connector_id=1,
            id_tag="TAG-1",
            meter_start=1500,
            timestamp="2026-10-12T00:00:00Z",
        )

    async def test_ocpp_21_started_and_ended_transaction_use_internal_session(self):
        started_session = FakeCollection({
            "charge_point_id": "CP-1",
            "transaction_id": 123,
            "estado": "activa",
        })
        legacy = SimpleNamespace(
            on_meter_values=AsyncMock(),
            on_start_transaction=AsyncMock(
                return_value=SimpleNamespace(
                    transaction_id=123,
                    id_tag_info={"status": "Accepted"},
                )
            ),
            on_stop_transaction=AsyncMock(),
        )
        handler = OCPP21ChargePointHandler("CP-1", None, None, legacy)
        with patch(
            "app.websocket.ocpp_v201.get_collection",
            return_value=started_session,
        ):
            start_response = await handler.on_transaction_event(
                "Started",
                "2026-10-12T00:00:00Z",
                "Authorized",
                0,
                {"transaction_id": "opaque-id-21"},
                evse={"id": 2, "connector_id": 1},
                id_token={"id_token": "TAG-1", "type": "ISO14443"},
            )

        self.assertEqual(start_response.id_token_info["status"], "Accepted")
        self.assertEqual(
            started_session.document["ocpp_transaction_id"], "opaque-id-21"
        )
        self.assertEqual(started_session.document["evse_id"], 2)
        self.assertEqual(
            started_session.document["subprotocolo_ocpp"], "ocpp2.1"
        )

        ended_session = FakeCollection({
            "charge_point_id": "CP-1",
            "transaction_id": 123,
            "ocpp_transaction_id": "opaque-id-21",
            "connector_id": 1,
            "meter_start": 1000,
            "estado": "activa",
        })
        with patch(
            "app.websocket.ocpp_v201.get_collection",
            return_value=ended_session,
        ):
            end_response = await handler.on_transaction_event(
                "Ended",
                "2026-10-12T00:05:00Z",
                "EVDeparted",
                1,
                {
                    "transaction_id": "opaque-id-21",
                    "stopped_reason": "EVDeparted",
                },
                meter_value=[{
                    "timestamp": "2026-10-12T00:05:00Z",
                    "sampled_value": [{
                        "value": "1.7",
                        "measurand": "Energy.Active.Import.Register",
                        "unit_of_measure": {"unit": "kWh"},
                    }],
                }],
            )

        self.assertIsNone(end_response.id_token_info)
        legacy.on_stop_transaction.assert_awaited_once_with(
            transaction_id=123,
            meter_stop=1700,
            timestamp="2026-10-12T00:05:00Z",
            reason="EVDeparted",
        )

    async def test_meter_values_conversion_applies_unit_multiplier(self):
        values = [{
            "timestamp": "2026-10-12T00:00:00Z",
            "sampled_value": [{
                "value": "12",
                "measurand": "Power.Active.Import",
                "unit_of_measure": {"unit": "W", "multiplier": 3},
            }],
        }]

        converted = _meter_values_to_v16(values)

        self.assertEqual(converted[0]["sampled_value"][0]["value"], "12000.0")
        self.assertEqual(converted[0]["sampled_value"][0]["unit"], "W")

    async def test_energy_register_converts_kwh_to_wh(self):
        values = [{
            "sampled_value": [{
                "value": "1.25",
                "measurand": "Energy.Active.Import.Register",
                "unit_of_measure": {"unit": "kWh"},
            }]
        }]

        self.assertEqual(_energy_register_wh(values), 1250)

    async def test_ended_transaction_forwards_final_meter_and_stop_reason(self):
        session = FakeCollection({
            "charge_point_id": "CP-1",
            "transaction_id": 123,
            "ocpp_transaction_id": "opaque-id-1",
            "connector_id": 1,
            "meter_start": 1000,
            "estado": "activa",
        })
        legacy = SimpleNamespace(
            on_meter_values=AsyncMock(),
            on_stop_transaction=AsyncMock(),
        )
        handler = OCPP201ChargePointHandler("CP-1", None, None, legacy)
        values = [{
            "timestamp": "2026-10-12T00:00:00Z",
            "sampled_value": [{
                "value": "1.7",
                "measurand": "Energy.Active.Import.Register",
                "unit_of_measure": {"unit": "kWh"},
            }],
        }]

        with patch(
            "app.websocket.ocpp_v201.get_collection",
            return_value=session,
        ):
            await handler.on_transaction_event(
                "Ended",
                "2026-10-12T00:00:00Z",
                "EVDeparted",
                1,
                {
                    "transaction_id": "opaque-id-1",
                    "stopped_reason": "EVDeparted",
                },
                meter_value=values,
            )

        legacy.on_meter_values.assert_awaited_once()
        legacy.on_stop_transaction.assert_awaited_once_with(
            transaction_id=123,
            meter_stop=1700,
            timestamp="2026-10-12T00:00:00Z",
            reason="EVDeparted",
        )

    async def test_ended_without_active_or_finalized_session_does_not_close(self):
        sessions = FakeCollection()
        legacy = SimpleNamespace(
            on_meter_values=AsyncMock(),
            on_stop_transaction=AsyncMock(),
        )
        handler = OCPP201ChargePointHandler("CP-1", None, None, legacy)

        with patch(
            "app.websocket.ocpp_v201.get_collection",
            return_value=sessions,
        ):
            response = await handler.on_transaction_event(
                "Ended",
                "2026-10-12T00:05:00Z",
                "EVDeparted",
                1,
                {"transaction_id": "unknown-transaction"},
            )
            sessions.document = {
                "charge_point_id": "CP-1",
                "transaction_id": 123,
                "ocpp_transaction_id": "malformed-finalized-session",
                "estado": "finalizada",
                "stop_closure": {
                    "rewards_applied": False,
                    "rewards_granted": False,
                    "event_recorded": False,
                    "charger_cleanup_status": "pending",
                },
            }
            malformed_response = await handler.on_transaction_event(
                "Ended",
                "2026-10-12T00:05:00Z",
                "EVDeparted",
                1,
                {"transaction_id": "malformed-finalized-session"},
            )

        self.assertIsNotNone(response)
        self.assertIsNotNone(malformed_response)
        legacy.on_stop_transaction.assert_not_awaited()

    async def test_duplicate_ended_for_completed_session_only_acknowledges(self):
        sessions = FakeCollection({
            "charge_point_id": "CP-1",
            "transaction_id": 123,
            "ocpp_transaction_id": "completed-transaction",
            "estado": "finalizada",
            "fecha_fin": datetime(2026, 10, 12, 0, 5),
            "stop_closure": {
                "rewards_applied": True,
                "rewards_granted": True,
                "event_recorded": True,
                "charger_cleanup_status": "completed",
            },
        })
        legacy = SimpleNamespace(on_stop_transaction=AsyncMock())
        handler = OCPP201ChargePointHandler("CP-1", None, None, legacy)

        with patch(
            "app.websocket.ocpp_v201.get_collection",
            return_value=sessions,
        ):
            response = await handler.on_transaction_event(
                "Ended",
                "2026-10-12T00:05:00Z",
                "EVDeparted",
                2,
                {"transaction_id": "completed-transaction"},
            )

        self.assertIsNotNone(response)
        legacy.on_stop_transaction.assert_not_awaited()


class OCPP201CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_ocpp_21_uses_21_command_payloads(self):
        handler = SimpleNamespace(negotiated_subprotocol="ocpp2.1")
        charger_document = {
            "charge_point_id": "CP-1",
            "empresa_id": "company-1",
            "activo": True,
        }
        charger_collection = FakeCollection(charger_document)
        with (
            patch.object(
                ocpp_command_service,
                "get_collection",
                return_value=charger_collection,
            ),
            patch.object(
                ocpp_command_service,
                "_charger_exists",
                new_callable=AsyncMock,
                return_value=charger_document,
            ),
            patch.object(
                ocpp_command_service,
                "_protocol_handler",
                return_value=handler,
            ),
            patch.object(
                ocpp_command_service,
                "_call",
                new_callable=AsyncMock,
                return_value={"status": "Accepted"},
            ) as send,
        ):
            await ocpp_command_service.remote_start("CP-1", "TAG-1", 2)

        _, action, payload = send.await_args.args
        self.assertEqual(action, "RequestStartTransaction")
        self.assertEqual(payload.__class__.__module__, "ocpp.v21.call")
        self.assertEqual(payload.evse_id, 2)

        session = FakeCollection({
            "_id": "session-21",
            "transaction_id": 123,
            "charge_point_id": "CP-1",
            "estado": "activa",
            "ocpp_transaction_id": "opaque-id-21",
        })
        with (
            patch.object(ocpp_command_service, "get_collection", return_value=session),
            patch.object(
                ocpp_command_service,
                "_protocol_handler",
                return_value=handler,
            ),
            patch.object(
                ocpp_command_service,
                "_call",
                new_callable=AsyncMock,
                return_value={"status": "Accepted"},
            ) as send,
        ):
            await ocpp_command_service.remote_stop("CP-1", 123)

        _, action, payload = send.await_args.args
        self.assertEqual(action, "RequestStopTransaction")
        self.assertEqual(payload.__class__.__module__, "ocpp.v21.call")
        self.assertEqual(payload.transaction_id, "opaque-id-21")

        with (
            patch.object(
                ocpp_command_service,
                "_charger_exists",
                new_callable=AsyncMock,
            ),
            patch.object(
                ocpp_command_service,
                "_protocol_handler",
                return_value=handler,
            ),
            patch.object(
                ocpp_command_service,
                "_call",
                new_callable=AsyncMock,
                return_value={"status": "Accepted"},
            ) as send,
        ):
            await ocpp_command_service.reset("CP-1", "Hard")

        self.assertEqual(send.await_args.args[2].__class__.__module__, "ocpp.v21.call")
        self.assertEqual(send.await_args.args[2].type, "Immediate")

        with (
            patch.object(
                ocpp_command_service,
                "_charger_exists",
                new_callable=AsyncMock,
            ),
            patch.object(
                ocpp_command_service,
                "_protocol_handler",
                return_value=handler,
            ),
            patch.object(
                ocpp_command_service,
                "_call",
                new_callable=AsyncMock,
                return_value={"status": "Accepted"},
            ) as send,
        ):
            await ocpp_command_service.change_availability(
                "CP-1", 2, "Inoperative"
            )

        self.assertEqual(send.await_args.args[2].__class__.__module__, "ocpp.v21.call")
        self.assertEqual(send.await_args.args[2].operational_status, "Inoperative")

    async def test_remote_start_uses_request_start_transaction(self):
        charger_document = {
            "charge_point_id": "CP-1",
            "empresa_id": "company-1",
            "activo": True,
        }
        with (
            patch.object(
                ocpp_command_service,
                "get_collection",
                return_value=FakeCollection(charger_document),
            ),
            patch.object(
                ocpp_command_service,
                "_charger_exists",
                new_callable=AsyncMock,
                return_value=charger_document,
            ),
            patch.object(
                ocpp_command_service,
                "_protocol_handler",
                return_value=SimpleNamespace(negotiated_subprotocol="ocpp2.0.1"),
            ),
            patch.object(
                ocpp_command_service,
                "_call",
                new_callable=AsyncMock,
                return_value={"status": "Accepted"},
            ) as send,
        ):
            await ocpp_command_service.remote_start("CP-1", "TAG-1", 2)

        _, action, payload = send.await_args.args
        self.assertEqual(action, "RequestStartTransaction")
        self.assertEqual(payload.id_token["id_token"], "TAG-1")
        self.assertEqual(payload.id_token["type"], "Central")
        self.assertEqual(payload.evse_id, 2)

    async def test_remote_stop_uses_charger_transaction_id(self):
        session = FakeCollection({
            "_id": "session-1",
            "transaction_id": 123,
            "charge_point_id": "CP-1",
            "estado": "activa",
            "ocpp_transaction_id": "opaque-id-1",
        })
        with (
            patch.object(ocpp_command_service, "get_collection", return_value=session),
            patch.object(
                ocpp_command_service,
                "_protocol_handler",
                return_value=SimpleNamespace(negotiated_subprotocol="ocpp2.0.1"),
            ),
            patch.object(
                ocpp_command_service,
                "_call",
                new_callable=AsyncMock,
                return_value={"status": "Accepted"},
            ) as send,
        ):
            await ocpp_command_service.remote_stop("CP-1", 123)

        _, action, payload = send.await_args.args
        self.assertEqual(action, "RequestStopTransaction")
        self.assertEqual(payload.transaction_id, "opaque-id-1")

    async def test_reset_maps_legacy_type_to_201_reset_type(self):
        with (
            patch.object(ocpp_command_service, "_charger_exists", new_callable=AsyncMock),
            patch.object(
                ocpp_command_service,
                "_protocol_handler",
                return_value=SimpleNamespace(negotiated_subprotocol="ocpp2.0.1"),
            ),
            patch.object(
                ocpp_command_service,
                "_call",
                new_callable=AsyncMock,
                return_value={"status": "Accepted"},
            ) as send,
        ):
            await ocpp_command_service.reset("CP-1", "Hard")

        self.assertEqual(send.await_args.args[2].type, "Immediate")

    async def test_change_availability_uses_201_operational_status(self):
        with (
            patch.object(ocpp_command_service, "_charger_exists", new_callable=AsyncMock),
            patch.object(
                ocpp_command_service,
                "_protocol_handler",
                return_value=SimpleNamespace(negotiated_subprotocol="ocpp2.0.1"),
            ),
            patch.object(
                ocpp_command_service,
                "_call",
                new_callable=AsyncMock,
                return_value={"status": "Accepted"},
            ) as send,
        ):
            await ocpp_command_service.change_availability(
                "CP-1", 2, "Inoperative"
            )

        _, action, payload = send.await_args.args
        self.assertEqual(action, "ChangeAvailability")
        self.assertEqual(payload.operational_status, "Inoperative")
        self.assertEqual(payload.evse, {"id": 2})
