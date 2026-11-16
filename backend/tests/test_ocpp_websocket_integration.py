import base64
from concurrent.futures import ThreadPoolExecutor
import json
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from bson import ObjectId
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from app.routers import chargers, client_portal
from app.services import ocpp_command_service
from app.ocpp_auth import hash_ocpp_password
from app.websocket import ocpp_v201, ocpp_v21
from app.services.ocpp_protocol_settings import OCPPProtocolSettings
from app.websocket import ocpp_server


class MemoryCursor:
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


class MemoryCollection:
    def __init__(self, documents=None):
        self.documents = [deepcopy(document) for document in documents or []]

    @staticmethod
    def _value_at_path(document, path):
        value = document
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        return value

    @classmethod
    def _matches(cls, document, query):
        for key, expected in query.items():
            if key == "$or":
                if not any(cls._matches(document, branch) for branch in expected):
                    return False
                continue
            if key == "$and":
                if not all(cls._matches(document, branch) for branch in expected):
                    return False
                continue

            value = cls._value_at_path(document, key)
            if isinstance(expected, dict) and any(
                operator.startswith("$") for operator in expected
            ):
                if "$exists" in expected:
                    exists = cls._value_at_path(document, key) is not None
                    if exists != expected["$exists"]:
                        return False
                if "$in" in expected and value not in expected["$in"]:
                    return False
                if "$gte" in expected and (value is None or value < expected["$gte"]):
                    return False
                if "$lte" in expected and (value is None or value > expected["$lte"]):
                    return False
                if "$ne" in expected and value == expected["$ne"]:
                    return False
            elif value != expected:
                return False
        return True

    @staticmethod
    def _set_path(document, path, value):
        parts = path.split(".")
        target = document
        for key in parts[:-1]:
            target = target.setdefault(key, {})
        target[parts[-1]] = deepcopy(value)

    @staticmethod
    def _unset_path(document, path):
        parts = path.split(".")
        target = document
        for key in parts[:-1]:
            target = target.get(key)
            if not isinstance(target, dict):
                return
        target.pop(parts[-1], None)

    async def find_one(self, query, projection=None, *, session=None):
        return next(
            (document for document in self.documents if self._matches(document, query)),
            None,
        )

    def find(self, query):
        return MemoryCursor(
            [
                document
                for document in self.documents
                if self._matches(document, query)
            ]
        )

    async def update_one(self, query, update, upsert=False, *, session=None):
        document = next(
            (item for item in self.documents if self._matches(item, query)),
            None,
        )
        inserted = False
        if document is None:
            if not upsert:
                return type(
                    "UpdateResult",
                    (),
                    {"matched_count": 0, "modified_count": 0},
                )()
            document = {
                key: deepcopy(value)
                for key, value in query.items()
                if not key.startswith("$") and not isinstance(value, dict)
            }
            self.documents.append(document)
            inserted = True

        if isinstance(update, list):
            return type(
                "UpdateResult",
                (),
                {"matched_count": 1, "modified_count": 1},
            )()

        if inserted:
            for key, value in update.get("$setOnInsert", {}).items():
                self._set_path(document, key, value)
        for key, value in update.get("$set", {}).items():
            self._set_path(document, key, value)
        for key, value in update.get("$inc", {}).items():
            current = self._value_at_path(document, key) or 0
            self._set_path(document, key, current + value)
        for key, value in update.get("$max", {}).items():
            current = self._value_at_path(document, key)
            if current is None or value > current:
                self._set_path(document, key, value)
        for key in update.get("$unset", {}):
            self._unset_path(document, key)
        return type(
            "UpdateResult",
            (),
            {"matched_count": 1, "modified_count": 1},
        )()

    async def insert_one(self, document, *, session=None):
        inserted = deepcopy(document)
        inserted.setdefault("_id", ObjectId())
        self.documents.append(inserted)
        return type("InsertOneResult", (), {"inserted_id": len(self.documents)})()


class MemoryTransaction:
    def __init__(self, collections):
        self.collections = collections
        self.snapshot = None

    async def __aenter__(self):
        self.snapshot = {
            name: deepcopy(collection.documents)
            for name, collection in self.collections.items()
        }
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            for name, documents in self.snapshot.items():
                self.collections[name].documents = documents
        return False


class MemorySession:
    def __init__(self, collections):
        self.collections = collections

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def start_transaction(self):
        return MemoryTransaction(self.collections)

    async def commit_transaction(self):
        return None

    async def abort_transaction(self):
        return None



class MemoryMongoClient:
    def __init__(self, collections):
        self.collections = collections

    async def start_session(self):
        return MemorySession(self.collections)


def _make_client_start_http_case(monkeypatch):
    charge_point_id = f"SUNTREE-{uuid4().hex}"
    password = "local-test-password"
    company_id = ObjectId()
    user_id = ObjectId()
    vehicle_id = ObjectId()
    card_id = ObjectId()
    request_id = ObjectId()
    charger = {
        "_id": ObjectId(),
        "charge_point_id": charge_point_id,
        "empresa_id": company_id,
        "activo": True,
        "fabricante": "Suntree",
        "modelo": "SWG5T-7",
        "ocpp_password_hash": hash_ocpp_password(password),
        "estado_general": "disponible",
        "conectores": [{"id_conector": 1, "estado": "Available"}],
        "sesion_actual": None,
    }
    request = {
        "_id": request_id,
        "charge_point_id": charge_point_id,
        "empresa_id": company_id,
        "uid": "TAG-HTTP-1",
        "card_id": card_id,
        "usuario_id": user_id,
        "status": "awaiting_vehicle",
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(minutes=3),
    }
    collections = {
        "cargadores": MemoryCollection([charger]),
        "empresas": MemoryCollection([{
            "_id": company_id,
            "activo": True,
            "owner_change_version": 0,
        }]),
        "usuarios": MemoryCollection([{
            "_id": user_id,
            "empresa_id": company_id,
            "rol": "cliente",
            "activo": True,
        }]),
        "vehiculos": MemoryCollection([{
            "_id": vehicle_id,
            "usuario_id": user_id,
            "empresa_id": company_id,
            "activo": True,
        }]),
        "nfc_cards": MemoryCollection([{
            "_id": card_id,
            "uid": "TAG-HTTP-1",
            "usuario_id": user_id,
            "empresa_id": company_id,
            "estado": "activa",
        }]),
        "nfc_charge_requests": MemoryCollection([request]),
        "sesiones": MemoryCollection(),
        "eventos_ocpp": MemoryCollection(),
        "heartbeats": MemoryCollection(),
        "status_notifications": MemoryCollection(),
        "ocpp_messages": MemoryCollection(),
        "logs": MemoryCollection(),
        "meter_values": MemoryCollection(),
        "telemetria": MemoryCollection(),
        "recompensa_progreso": MemoryCollection(),
        "recompensas": MemoryCollection(),
        "company_reward_balances": MemoryCollection(),
        "company_reward_ledger": MemoryCollection(),
    }
    actor_state = {
        "user": {
            "_id": user_id,
            "empresa_id": company_id,
            "rol": "cliente",
            "activo": True,
        },
    }

    app = FastAPI()
    app.include_router(client_portal.router)
    app.include_router(chargers.router)
    app.dependency_overrides[client_portal.require_client] = (
        lambda: actor_state["user"]
    )
    app.dependency_overrides[chargers.require_company_admin_access] = (
        lambda: actor_state["user"]
    )

    @app.websocket("/ocpp/{path_charge_point_id}")
    async def websocket_endpoint(websocket: WebSocket, path_charge_point_id: str):
        await ocpp_server.ocpp_endpoint(websocket, path_charge_point_id)

    _allow_all_ocpp_protocols(monkeypatch)
    monkeypatch.setattr(ocpp_server, "get_collection", lambda name: collections[name])
    monkeypatch.setattr(client_portal, "get_collection", lambda name: collections[name])
    monkeypatch.setattr(chargers, "get_collection", lambda name: collections[name])
    monkeypatch.setattr(
        ocpp_command_service,
        "get_collection",
        lambda name: collections[name],
    )
    monkeypatch.setattr(
        ocpp_server.db,
        "client",
        MemoryMongoClient(collections),
    )

    async def next_transaction_id():
        return 456

    async def no_reward(*args, **kwargs):
        return {"tipo": "ninguna"}

    async def no_op(*args, **kwargs):
        return None

    monkeypatch.setattr(ocpp_server, "next_transaction_id", next_transaction_id)
    monkeypatch.setattr(ocpp_server, "aplicar_recompensa_en_sesion", no_reward)
    monkeypatch.setattr(ocpp_server, "verificar_y_otorgar_recompensa", no_op)
    monkeypatch.setattr(ocpp_server, "accrue_points_for_completed_session", no_op)
    authorization = base64.b64encode(
        f"{charge_point_id}:{password}".encode("utf-8")
    ).decode("ascii")
    return {
        "app": app,
        "actor_state": actor_state,
        "authorization": authorization,
        "charge_point_id": charge_point_id,
        "collections": collections,
        "company_id": company_id,
        "request_id": request_id,
        "user_id": user_id,
        "vehicle_id": vehicle_id,
    }


def _allow_all_ocpp_protocols(monkeypatch):
    async def load_settings():
        return OCPPProtocolSettings()

    monkeypatch.setattr(ocpp_server, "get_ocpp_protocol_settings", load_settings)


def _transaction_event_message(
    unique_id,
    transaction_id,
    *,
    event_type="Ended",
    energy_kwh=3.0,
):
    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    trigger_reason = {
        "Started": "Authorized",
        "Updated": "MeterValuePeriodic",
        "Ended": "EVDeparted",
    }[event_type]
    transaction_info = {"transactionId": transaction_id}
    if event_type == "Ended":
        transaction_info["stoppedReason"] = "EVDisconnected"
    payload = {
        "eventType": event_type,
        "timestamp": timestamp,
        "triggerReason": trigger_reason,
        "seqNo": 0 if event_type == "Started" else 1,
        "transactionInfo": transaction_info,
        "evse": {"id": 1, "connectorId": 1},
        "meterValue": [
            {
                "timestamp": timestamp,
                "sampledValue": [
                    {
                        "value": energy_kwh,
                        "measurand": "Energy.Active.Import.Register",
                        "unitOfMeasure": {"unit": "kWh"},
                    }
                ],
            }
        ],
    }
    if event_type == "Started":
        payload["idToken"] = {"idToken": "TAG-1", "type": "Central"}
    return [
        2,
        unique_id,
        "TransactionEvent",
        payload,
    ]


def _make_transaction_event_case(
    monkeypatch,
    protocol,
    *,
    sessions=None,
    fail_stage=None,
):
    charge_point_id = f"SUNTREE-{uuid4().hex}"
    password = "local-test-password"
    transaction_id = "opaque-transaction-id"
    user_id = ObjectId()
    session = {
        "_id": ObjectId(),
        "charge_point_id": charge_point_id,
        "transaction_id": 271,
        "ocpp_transaction_id": transaction_id,
        "connector_id": 1,
        "meter_start": 1000,
        "fecha_inicio": datetime.utcnow() - timedelta(minutes=8),
        "usuario_id": user_id,
        "estado": "activa",
    }
    session_documents = sessions if sessions is not None else [session]
    collections = {
        "cargadores": MemoryCollection(
            [
                {
                    "charge_point_id": charge_point_id,
                    "activo": True,
                    "ocpp_password_hash": hash_ocpp_password(password),
                    "sesion_actual": {"transaction_id": 271},
                }
            ]
        ),
        "sesiones": MemoryCollection(session_documents),
        "eventos_ocpp": MemoryCollection(),
        "heartbeats": MemoryCollection(),
        "status_notifications": MemoryCollection(),
        "ocpp_messages": MemoryCollection(),
        "logs": MemoryCollection(),
        "meter_values": MemoryCollection(),
        "telemetria": MemoryCollection(),
        "usuarios": MemoryCollection(
            [{"_id": user_id, "total_cargas": 19}]
        ),
        "recompensa_progreso": MemoryCollection(),
        "recompensas": MemoryCollection(),
        "company_reward_balances": MemoryCollection(),
        "company_reward_ledger": MemoryCollection(),
    }
    reward_state = {
        "legacy_calls": [],
        "legacy_sessions": set(),
        "points_calls": [],
        "point_sessions": set(),
        "movements": set(),
        "legacy_failure_used": False,
        "points_failure_used": False,
    }

    async def verify_legacy_reward(completed_session):
        assert completed_session["estado"] == "finalizada"
        session_id = completed_session["_id"]
        reward_state["legacy_calls"].append(session_id)
        first_processing = session_id not in reward_state["legacy_sessions"]
        if first_processing:
            reward_state["legacy_sessions"].add(session_id)
            collections["recompensa_progreso"].documents.append({
                "_id": f"legacy-reward-session:{session_id}",
                "tipo": "sesion_procesada",
                "sesion_id": session_id,
            })
            collections["recompensas"].documents.append({
                "_id": session_id,
                "usuario_id": completed_session["usuario_id"],
            })
        if fail_stage == "legacy" and not reward_state["legacy_failure_used"]:
            reward_state["legacy_failure_used"] = True
            raise RuntimeError("simulated legacy reward failure")
        return 1 if first_processing else 0

    async def accrue_company_points(completed_session):
        assert completed_session["estado"] == "finalizada"
        session_id = completed_session["_id"]
        reward_state["points_calls"].append(session_id)
        first_processing = session_id not in reward_state["point_sessions"]
        if first_processing:
            reward_state["point_sessions"].add(session_id)
            reward_state["movements"].add(session_id)
            collections["company_reward_ledger"].documents.append({
                "_id": session_id,
                "session_id": session_id,
                "tipo": "acumulacion",
                "puntos": 7,
            })
            collections["company_reward_balances"].documents.append({
                "_id": completed_session["usuario_id"],
                "puntos_disponibles": 7,
            })
        if fail_stage == "points" and not reward_state["points_failure_used"]:
            reward_state["points_failure_used"] = True
            raise RuntimeError("simulated company points failure")
        return 7 if first_processing else 0

    async def apply_selected_reward(_user_id, _session_id):
        return {"tipo": "ninguna"}

    async def load_settings():
        return OCPPProtocolSettings()

    monkeypatch.setattr(ocpp_server, "get_ocpp_protocol_settings", load_settings)
    monkeypatch.setattr(
        ocpp_server,
        "get_collection",
        lambda name: collections[name],
    )
    monkeypatch.setattr(
        ocpp_v201,
        "get_collection",
        lambda name: collections[name],
    )
    monkeypatch.setattr(
        ocpp_server.db,
        "client",
        MemoryMongoClient(collections),
    )
    monkeypatch.setattr(
        ocpp_server,
        "verificar_y_otorgar_recompensa",
        verify_legacy_reward,
    )
    monkeypatch.setattr(
        ocpp_server,
        "accrue_points_for_completed_session",
        accrue_company_points,
    )
    monkeypatch.setattr(
        ocpp_server,
        "aplicar_recompensa_en_sesion",
        apply_selected_reward,
    )

    app = FastAPI()

    @app.websocket("/ocpp/{path_charge_point_id}")
    async def websocket_endpoint(websocket: WebSocket, path_charge_point_id: str):
        await ocpp_server.ocpp_endpoint(websocket, path_charge_point_id)

    authorization = base64.b64encode(
        f"{charge_point_id}:{password}".encode("utf-8")
    ).decode("ascii")
    return {
        "app": app,
        "authorization": authorization,
        "charge_point_id": charge_point_id,
        "collections": collections,
        "protocol": protocol,
        "reward_state": reward_state,
        "transaction_id": transaction_id,
    }


def _legacy_processed_session_ids(case):
    return {
        document["sesion_id"]
        for document in case["collections"]["recompensa_progreso"].documents
        if document.get("tipo") == "sesion_procesada"
    }


def _company_reward_session_ids(case):
    return {
        document["session_id"]
        for document in case["collections"]["company_reward_ledger"].documents
        if document.get("tipo") == "acumulacion"
    }


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
def test_client_request_and_transaction_lifecycle_run_over_websocket(
    monkeypatch,
    protocol,
):
    case = _make_transaction_event_case(monkeypatch, protocol, sessions=[])
    collections = case["collections"]
    company_id = ObjectId()
    user_id = collections["usuarios"].documents[0]["_id"]
    vehicle_id = ObjectId()
    card_id = ObjectId()
    requested_at = datetime.utcnow()
    charger = collections["cargadores"].documents[0]
    charger["_id"] = ObjectId()
    charger["empresa_id"] = company_id
    charger["sesion_actual"] = None
    charger["pending_remote_start"] = {
        "command_id": "client-start-command",
        "id_tag": "TAG-1",
        "connector_id": 1,
        "requested_at": requested_at,
        "status": "accepted",
    }
    collections["empresas"] = MemoryCollection([{
        "_id": company_id,
        "activo": True,
    }])
    collections["usuarios"].documents[0].update({
        "empresa_id": company_id,
        "rol": "cliente",
        "activo": True,
    })
    collections["nfc_cards"] = MemoryCollection([{
        "_id": card_id,
        "uid": "TAG-1",
        "usuario_id": user_id,
        "empresa_id": company_id,
        "estado": "activa",
    }])
    collections["vehiculos"] = MemoryCollection([{
        "_id": vehicle_id,
        "usuario_id": user_id,
        "empresa_id": company_id,
        "activo": True,
    }])
    collections["nfc_charge_requests"] = MemoryCollection([{
        "_id": ObjectId(),
        "charge_point_id": case["charge_point_id"],
        "empresa_id": company_id,
        "uid": "TAG-1",
        "usuario_id": user_id,
        "status": "start_requested",
        "vehicle_id": vehicle_id,
        "connector_id": 1,
        "expires_at": datetime.utcnow() + timedelta(minutes=2),
    }])

    async def next_transaction_id():
        return 456

    monkeypatch.setattr(ocpp_server, "next_transaction_id", next_transaction_id)

    with TestClient(case["app"]) as client:
        with client.websocket_connect(
            f"/ocpp/{case['charge_point_id']}",
            subprotocols=[protocol],
            headers={"Authorization": f"Basic {case['authorization']}"},
        ) as websocket:
            assert websocket.accepted_subprotocol == protocol
            websocket.send_text(json.dumps([
                2,
                "authorize-client",
                "Authorize",
                {"idToken": {"idToken": "TAG-1", "type": "Central"}},
            ]))
            authorize_response = json.loads(websocket.receive_text())
            assert authorize_response[:2] == [3, "authorize-client"]
            assert authorize_response[2]["idTokenInfo"]["status"] == "Accepted"

            websocket.send_text(json.dumps(_transaction_event_message(
                "started-client",
                "opaque-new-transaction",
                event_type="Started",
                energy_kwh=1.0,
            )))
            start_response = json.loads(websocket.receive_text())
            assert start_response[:2] == [3, "started-client"]
            assert start_response[2]["idTokenInfo"]["status"] == "Accepted"
            started_session = collections["sesiones"].documents[0]
            assert started_session["usuario_id"] == user_id
            assert started_session["vehiculo_id"] == vehicle_id
            assert started_session["connector_id"] == 1
            assert started_session["ocpp_transaction_id"] == "opaque-new-transaction"
            assert started_session["meter_start"] == 1000

            websocket.send_text(json.dumps(_transaction_event_message(
                "updated-client",
                "opaque-new-transaction",
                event_type="Updated",
                energy_kwh=3.0,
            )))
            updated_response = json.loads(websocket.receive_text())
            assert updated_response[:2] == [3, "updated-client"]
            assert collections["sesiones"].documents[0][
                "energia_consumida_kwh"
            ] == 2.0

            websocket.send_text(json.dumps(_transaction_event_message(
                "ended-client",
                "opaque-new-transaction",
                event_type="Ended",
                energy_kwh=4.0,
            )))
            ended_response = json.loads(websocket.receive_text())

    final_session = collections["sesiones"].documents[0]
    assert ended_response[:2] == [3, "ended-client"]
    assert ended_response[2] == {}
    assert len(collections["sesiones"].documents) == 1
    assert final_session["estado"] == "finalizada"
    assert final_session["energia_consumida_kwh"] == 3.0
    assert final_session["duracion_minutos"] >= 0
    assert isinstance(final_session["fecha_fin"], datetime)
    assert "pending_remote_start" not in charger
    assert charger["last_remote_command_reconciliation"]["status"] == "confirmed"
    assert _legacy_processed_session_ids(case) == {final_session["_id"]}
    assert _company_reward_session_ids(case) == {final_session["_id"]}
    assert len(collections["recompensas"].documents) == 1
    assert len(collections["company_reward_ledger"].documents) == 1


def test_ocpp_16j_boot_notification_and_heartbeat_use_memory_collections(monkeypatch):
    charge_point_id = f"SUNTREE-{uuid4().hex}"
    password = "local-test-password"
    company_id = ObjectId()
    collections = {
        "cargadores": MemoryCollection(
            [
                {
                    "charge_point_id": charge_point_id,
                    "empresa_id": company_id,
                    "activo": True,
                    "fabricante": "Suntree",
                    "modelo": "SWG5T-7",
                    "ocpp_password_hash": hash_ocpp_password(password),
                }
            ]
        ),
        "empresas": MemoryCollection([{
            "_id": company_id,
            "activo": True,
        }]),
        "sesiones": MemoryCollection(),
        "eventos_ocpp": MemoryCollection(),
        "heartbeats": MemoryCollection(),
        "status_notifications": MemoryCollection(),
        "ocpp_messages": MemoryCollection(),
        "logs": MemoryCollection(),
    }
    _allow_all_ocpp_protocols(monkeypatch)
    monkeypatch.setattr(
        ocpp_server,
        "get_collection",
        lambda name: collections[name],
    )
    monkeypatch.setattr(
        ocpp_server.db,
        "client",
        MemoryMongoClient(collections),
    )

    app = FastAPI()

    @app.websocket("/ocpp/{path_charge_point_id}")
    async def websocket_endpoint(websocket: WebSocket, path_charge_point_id: str):
        await ocpp_server.ocpp_endpoint(websocket, path_charge_point_id)

    authorization = base64.b64encode(
        f"{charge_point_id}:{password}".encode("utf-8")
    ).decode("ascii")

    with TestClient(app) as client:
        with client.websocket_connect(
            f"/ocpp/{charge_point_id}",
            subprotocols=["ocpp1.6"],
            headers={"Authorization": f"Basic {authorization}"},
        ) as websocket:
            assert websocket.accepted_subprotocol == "ocpp1.6"

            websocket.send_text(
                json.dumps(
                    [
                        2,
                        "boot-1",
                        "BootNotification",
                        {
                            "chargePointVendor": "Suntree",
                            "chargePointModel": "SWG5T-7",
                        },
                    ]
                )
            )
            boot_response = json.loads(websocket.receive_text())
            assert boot_response[0:2] == [3, "boot-1"]
            assert boot_response[2]["status"] == "Accepted"
            assert boot_response[2]["interval"] == 60
            assert boot_response[2]["currentTime"].endswith("Z")

            websocket.send_text(
                json.dumps([2, "heartbeat-1", "Heartbeat", {}])
            )
            heartbeat_response = json.loads(websocket.receive_text())
            assert heartbeat_response[0:2] == [3, "heartbeat-1"]
            assert heartbeat_response[2]["currentTime"].endswith("Z")

    events = collections["eventos_ocpp"].documents
    assert [
        (event["charge_point_id"], event["tipo_mensaje"], event["accion"])
        for event in events
    ] == [
        (charge_point_id, "BootNotification", "request"),
        (charge_point_id, "Heartbeat", "request"),
    ]
    assert events[0]["payload"] == {
        "vendor": "Suntree",
        "model": "SWG5T-7",
    }
    assert all(event["empresa_id"] == company_id for event in events)
    assert collections["heartbeats"].documents[0]["charge_point_id"] == charge_point_id
    assert len(collections["heartbeats"].documents) == 1
    assert collections["cargadores"].documents[0]["subprotocolo_ocpp"] == "ocpp1.6"
    assert collections["empresas"].documents[0]["owner_change_version"] == 1
    assert len(collections["ocpp_messages"].documents) == 4
    assert {
        message["subprotocolo_ocpp"]
        for message in collections["ocpp_messages"].documents
    } == {"ocpp1.6"}
    assert all(
        message["empresa_id"] == company_id
        for message in collections["ocpp_messages"].documents
    )


def test_ocpp_16_subprotocol_selection_prefers_standard_and_keeps_legacy_alias():
    assert ocpp_server._select_ocpp_16_subprotocol(
        ["ocpp1.6j", "ocpp1.6"]
    ) == "ocpp1.6"
    assert ocpp_server._select_ocpp_16_subprotocol(["ocpp1.6j"]) == "ocpp1.6j"
    assert ocpp_server._select_ocpp_16_subprotocol(["ocpp2.0.1"]) is None


def test_ocpp_16_meter_values_and_stop_are_processed_over_websocket(monkeypatch):
    case = _make_transaction_event_case(monkeypatch, "ocpp1.6")
    collections = case["collections"]
    collections["sesiones"].documents[0]["remote_stop_command"] = {
        "command_id": "stop-command-1",
        "status": "indeterminate",
        "requested_at": datetime.utcnow() - timedelta(seconds=2),
    }

    with TestClient(case["app"]) as client:
        with client.websocket_connect(
            f"/ocpp/{case['charge_point_id']}",
            subprotocols=["ocpp1.6"],
            headers={"Authorization": f"Basic {case['authorization']}"},
        ) as websocket:
            assert websocket.accepted_subprotocol == "ocpp1.6"
            websocket.send_text(json.dumps([
                2,
                "meter-values-1",
                "MeterValues",
                {
                    "connectorId": 1,
                    "transactionId": 271,
                    "meterValue": [{
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "sampledValue": [{
                            "value": "3000",
                            "measurand": "Energy.Active.Import.Register",
                            "unit": "Wh",
                        }],
                    }],
                },
            ]))
            meter_response = json.loads(websocket.receive_text())
            assert meter_response[:2] == [3, "meter-values-1"]
            assert collections["sesiones"].documents[0][
                "energia_consumida_kwh"
            ] == 2.0

            websocket.send_text(json.dumps([
                2,
                "stop-1",
                "StopTransaction",
                {
                    "transactionId": 271,
                    "meterStop": 3500,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "reason": "EVDisconnected",
                    "idTag": "TAG-1",
                },
            ]))
            stop_response = json.loads(websocket.receive_text())

    session = collections["sesiones"].documents[0]
    assert stop_response[:2] == [3, "stop-1"]
    assert stop_response[2]["idTagInfo"]["status"] == "Accepted"
    assert session["estado"] == "finalizada"
    assert session["energia_consumida_kwh"] == 2.5
    assert session["meter_stop"] == 3500
    assert session["remote_stop_command"]["status"] == "confirmed"
    assert (
        session["remote_stop_command"]["evidence"]
        == "matching_stop_transaction"
    )
    assert isinstance(session["fecha_fin"], datetime)
    assert session["duracion_minutos"] >= 7
    assert len(collections["meter_values"].documents) == 1
    assert len(collections["recompensas"].documents) == 1
    assert len(collections["company_reward_ledger"].documents) == 1


@pytest.mark.parametrize("role", ["superadmin", "dueno_empresa"])
def test_admin_http_route_rejects_starting_a_client_charge(monkeypatch, role):
    case = _make_client_start_http_case(monkeypatch)
    actor = {"_id": ObjectId(), "rol": role}
    if role == "dueno_empresa":
        actor["empresa_id"] = case["company_id"]
    case["actor_state"]["user"] = actor

    with patch.object(
        ocpp_command_service,
        "remote_start",
        new_callable=AsyncMock,
    ) as remote_start:
        with TestClient(case["app"]) as client:
            response = client.post(
                f"/api/cargadores/{case['charge_point_id']}/remote-start"
            )

    assert response.status_code == 403
    remote_start.assert_not_awaited()
    assert case["collections"]["sesiones"].documents == []


def test_client_http_start_rejects_invalid_or_incomplete_requests(monkeypatch):
    case = _make_client_start_http_case(monkeypatch)
    request_path = (
        f"/api/client/me/charge-requests/{case['request_id']}/start"
    )
    with patch.object(
        ocpp_command_service,
        "remote_start",
        new_callable=AsyncMock,
    ) as remote_start:
        with TestClient(case["app"]) as client:
            invalid_vehicle = client.post(
                request_path,
                json={
                    "vehicle_id": str(ObjectId()),
                    "connector_id": 1,
                },
            )
            invalid_connector = client.post(
                request_path,
                json={
                    "vehicle_id": str(case["vehicle_id"]),
                    "connector_id": 2,
                },
            )
            incomplete = client.post(
                request_path,
                json={"connector_id": 1},
            )

    assert invalid_vehicle.status_code == 404
    assert invalid_connector.status_code == 422
    assert incomplete.status_code == 422
    remote_start.assert_not_awaited()
    assert case["collections"]["sesiones"].documents == []
    assert case["collections"]["nfc_charge_requests"].documents[0]["status"] == (
        "awaiting_vehicle"
    )


def test_client_http_to_ocpp16_websocket_charge_lifecycle(monkeypatch):
    case = _make_client_start_http_case(monkeypatch)
    collections = case["collections"]
    collections["nfc_charge_requests"].documents[0]["source"] = "qr"
    assert collections["sesiones"].documents == []

    with TestClient(case["app"]) as client:
        with client.websocket_connect(
            f"/ocpp/{case['charge_point_id']}",
            subprotocols=["ocpp1.6"],
            headers={"Authorization": f"Basic {case['authorization']}"},
        ) as websocket:
            assert websocket.accepted_subprotocol == "ocpp1.6"
            websocket.send_text(json.dumps([
                2,
                "boot-http-1",
                "BootNotification",
                {
                    "chargePointVendor": "Suntree",
                    "chargePointModel": "SWG5T-7",
                },
            ]))
            boot_response = json.loads(websocket.receive_text())
            assert boot_response[2]["status"] == "Accepted"

            request_path = (
                f"/api/client/me/charge-requests/{case['request_id']}/start"
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                start_future = executor.submit(
                    client.post,
                    request_path,
                    json={
                        "vehicle_id": str(case["vehicle_id"]),
                        "connector_id": 1,
                    },
                )
                remote_start = json.loads(websocket.receive_text())
                assert remote_start[0] == 2
                assert remote_start[2] == "RemoteStartTransaction"
                assert remote_start[3] == {
                    "idTag": "TAG-HTTP-1",
                    "connectorId": 1,
                }
                pending = collections["cargadores"].documents[0][
                    "pending_remote_start"
                ]
                assert pending["id_tag"] == "TAG-HTTP-1"
                assert pending["connector_id"] == 1
                assert collections["nfc_charge_requests"].documents[0][
                    "status"
                ] == "start_requested"
                websocket.send_text(json.dumps([
                    3,
                    remote_start[1],
                    {"status": "Accepted"},
                ]))
                http_response = start_future.result(timeout=10)

            assert http_response.status_code == 200
            assert http_response.json()["status"] == "Accepted"
            start_request = collections["nfc_charge_requests"].documents[0]
            assert start_request["usuario_id"] == case["user_id"]
            assert start_request["vehicle_id"] == case["vehicle_id"]
            assert start_request["connector_id"] == 1

            websocket.send_text(json.dumps([
                2,
                "authorize-http-1",
                "Authorize",
                {"idTag": "TAG-HTTP-1"},
            ]))
            authorize_response = json.loads(websocket.receive_text())
            assert authorize_response[2]["idTagInfo"]["status"] == "Accepted"

            start_transaction = {
                "connectorId": 1,
                "idTag": "TAG-HTTP-1",
                "meterStart": 1000,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            websocket.send_text(json.dumps([
                2,
                "start-transaction-http-1",
                "StartTransaction",
                start_transaction,
            ]))
            start_response = json.loads(websocket.receive_text())
            assert start_response[2]["transactionId"] == 456
            assert start_response[2]["idTagInfo"]["status"] == "Accepted"
            assert len(collections["sesiones"].documents) == 1
            session = collections["sesiones"].documents[0]
            assert session["usuario_id"] == case["user_id"]
            assert session["vehiculo_id"] == case["vehicle_id"]
            assert session["nfc_card_id"] == collections["nfc_cards"].documents[0]["_id"]
            assert session["charge_point_id"] == case["charge_point_id"]
            assert session["connector_id"] == 1
            assert session["transaction_id"] == 456
            assert session["qr_request_id"] == case["request_id"]
            assert collections["nfc_charge_requests"].documents[0][
                "transaction_id"
            ] == 456

            websocket.send_text(json.dumps([
                2,
                "duplicate-start-http-1",
                "StartTransaction",
                start_transaction,
            ]))
            duplicate_start = json.loads(websocket.receive_text())
            assert duplicate_start[2]["transactionId"] == 0
            assert duplicate_start[2]["idTagInfo"]["status"] == "ConcurrentTx"
            assert len(collections["sesiones"].documents) == 1

            websocket.send_text(json.dumps([
                2,
                "meter-values-http-1",
                "MeterValues",
                {
                    "connectorId": 1,
                    "transactionId": 456,
                    "meterValue": [{
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "sampledValue": [{
                            "value": "3000",
                            "measurand": "Energy.Active.Import.Register",
                            "unit": "Wh",
                        }],
                    }],
                },
            ]))
            meter_response = json.loads(websocket.receive_text())
            assert meter_response[:2] == [3, "meter-values-http-1"]
            assert session["energia_consumida_kwh"] == 2.0
            assert len(collections["meter_values"].documents) == 1

            stop_message = [
                2,
                "stop-transaction-http-1",
                "StopTransaction",
                {
                    "transactionId": 456,
                    "meterStop": 3500,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "reason": "EVDisconnected",
                    "idTag": "TAG-HTTP-1",
                },
            ]
            websocket.send_text(json.dumps(stop_message))
            stop_response = json.loads(websocket.receive_text())
            assert stop_response[2]["idTagInfo"]["status"] == "Accepted"

            websocket.send_text(json.dumps([
                2,
                "duplicate-stop-http-1",
                "StopTransaction",
                stop_message[3],
            ]))
            duplicate_stop = json.loads(websocket.receive_text())
            assert duplicate_stop[2]["idTagInfo"]["status"] == "Accepted"

    assert len(collections["sesiones"].documents) == 1
    final_session = collections["sesiones"].documents[0]
    assert final_session["estado"] == "finalizada"
    assert final_session["energia_consumida_kwh"] == 2.5
    assert final_session["transaction_id"] == 456
    assert isinstance(final_session["fecha_fin"], datetime)
    assert collections["cargadores"].documents[0].get("pending_remote_start") is None
    assert collections["cargadores"].documents[0]["last_remote_command_reconciliation"][
        "status"
    ] == "confirmed"


def test_ocpp_connection_rejects_inactive_company(monkeypatch):
    charge_point_id = f"SUNTREE-{uuid4().hex}"
    password = "local-test-password"
    company_id = ObjectId()
    collections = {
        "cargadores": MemoryCollection([{
            "charge_point_id": charge_point_id,
            "empresa_id": company_id,
            "activo": True,
            "ocpp_password_hash": hash_ocpp_password(password),
        }]),
        "empresas": MemoryCollection([{
            "_id": company_id,
            "activo": False,
        }]),
        "sesiones": MemoryCollection(),
        "eventos_ocpp": MemoryCollection(),
        "heartbeats": MemoryCollection(),
        "status_notifications": MemoryCollection(),
        "ocpp_messages": MemoryCollection(),
        "logs": MemoryCollection(),
    }
    _allow_all_ocpp_protocols(monkeypatch)
    monkeypatch.setattr(ocpp_server, "get_collection", lambda name: collections[name])
    monkeypatch.setattr(
        ocpp_server.db,
        "client",
        MemoryMongoClient(collections),
    )

    app = FastAPI()

    @app.websocket("/ocpp/{path_charge_point_id}")
    async def websocket_endpoint(websocket: WebSocket, path_charge_point_id: str):
        await ocpp_server.ocpp_endpoint(websocket, path_charge_point_id)

    authorization = base64.b64encode(
        f"{charge_point_id}:{password}".encode("utf-8")
    ).decode("ascii")

    with TestClient(app) as client:
        with client.websocket_connect(
            f"/ocpp/{charge_point_id}",
            subprotocols=["ocpp1.6"],
            headers={"Authorization": f"Basic {authorization}"},
        ) as websocket:
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()

    assert collections["cargadores"].documents[0].get("conectado") is not True
    assert collections["empresas"].documents[0].get("owner_change_version", 0) == 0


def test_ocpp_201_boot_notification_and_heartbeat_use_active_websocket_path(monkeypatch):
    charge_point_id = f"SUNTREE-{uuid4().hex}"
    password = "local-test-password"
    collections = {
        "cargadores": MemoryCollection(
            [{
                "charge_point_id": charge_point_id,
                "activo": True,
                "ocpp_version": "ocpp2.0.1",
                "ocpp_password_hash": hash_ocpp_password(password),
            }]
        ),
        "sesiones": MemoryCollection(),
        "eventos_ocpp": MemoryCollection(),
        "heartbeats": MemoryCollection(),
        "status_notifications": MemoryCollection(),
        "ocpp_messages": MemoryCollection(),
        "logs": MemoryCollection(),
    }
    _allow_all_ocpp_protocols(monkeypatch)
    monkeypatch.setattr(
        ocpp_server,
        "get_collection",
        lambda name: collections[name],
    )

    app = FastAPI()

    @app.websocket("/ocpp/{path_charge_point_id}")
    async def websocket_endpoint(websocket: WebSocket, path_charge_point_id: str):
        await ocpp_server.ocpp_endpoint(websocket, path_charge_point_id)

    authorization = base64.b64encode(
        f"{charge_point_id}:{password}".encode("utf-8")
    ).decode("ascii")

    with TestClient(app) as client:
        with client.websocket_connect(
            f"/ocpp/{charge_point_id}",
            subprotocols=["ocpp1.6", "ocpp2.0.1"],
            headers={"Authorization": f"Basic {authorization}"},
        ) as websocket:
            assert websocket.accepted_subprotocol == "ocpp2.0.1"
            websocket.send_text(json.dumps([
                2,
                "boot-201",
                "BootNotification",
                {
                    "chargingStation": {
                        "vendorName": "Suntree",
                        "model": "SWG5T-7",
                    },
                    "reason": "PowerUp",
                },
            ]))
            boot_response = json.loads(websocket.receive_text())
            assert boot_response[0:2] == [3, "boot-201"]
            assert boot_response[2]["status"] == "Accepted"

            websocket.send_text(json.dumps([2, "heartbeat-201", "Heartbeat", {}]))
            heartbeat_response = json.loads(websocket.receive_text())
            assert heartbeat_response[0:2] == [3, "heartbeat-201"]
            assert heartbeat_response[2]["currentTime"].endswith("Z")

            websocket.send_text(json.dumps([
                2,
                "status-201",
                "StatusNotification",
                {
                    "timestamp": "2026-10-12T00:00:00Z",
                    "connectorStatus": "Occupied",
                    "evseId": 2,
                    "connectorId": 1,
                },
            ]))
            status_response = json.loads(websocket.receive_text())
            assert status_response[0:2] == [3, "status-201"]

    charger = collections["cargadores"].documents[0]
    assert charger["subprotocolo_ocpp"] == "ocpp2.0.1"
    assert charger["fabricante"] == "Suntree"
    assert len(collections["heartbeats"].documents) == 1
    status_notification = collections["status_notifications"].documents[0]
    assert status_notification["estado"] == "Occupied"
    assert status_notification["error_code"] is None
    assert status_notification["ocpp_status"] == "Occupied"
    assert status_notification["evse_id"] == 2
    status_event = next(
        event
        for event in collections["eventos_ocpp"].documents
        if event["tipo_mensaje"] == "StatusNotification"
    )
    assert status_event["payload"]["status"] == "Occupied"
    assert status_event["payload"]["ocpp_status"] == "Occupied"
    assert {
        message["subprotocolo_ocpp"]
        for message in collections["ocpp_messages"].documents
    } == {"ocpp2.0.1"}


def test_ocpp_21_boot_notification_and_heartbeat_use_active_websocket_path(monkeypatch):
    charge_point_id = f"SUNTREE-{uuid4().hex}"
    password = "local-test-password"
    collections = {
        "cargadores": MemoryCollection(
            [{
                "charge_point_id": charge_point_id,
                "activo": True,
                "ocpp_password_hash": hash_ocpp_password(password),
            }]
        ),
        "sesiones": MemoryCollection(),
        "eventos_ocpp": MemoryCollection(),
        "heartbeats": MemoryCollection(),
        "status_notifications": MemoryCollection(),
        "ocpp_messages": MemoryCollection(),
        "logs": MemoryCollection(),
    }
    _allow_all_ocpp_protocols(monkeypatch)
    monkeypatch.setattr(
        ocpp_server,
        "get_collection",
        lambda name: collections[name],
    )

    app = FastAPI()

    @app.websocket("/ocpp/{path_charge_point_id}")
    async def websocket_endpoint(websocket: WebSocket, path_charge_point_id: str):
        await ocpp_server.ocpp_endpoint(websocket, path_charge_point_id)

    authorization = base64.b64encode(
        f"{charge_point_id}:{password}".encode("utf-8")
    ).decode("ascii")

    with TestClient(app) as client:
        with client.websocket_connect(
            f"/ocpp/{charge_point_id}",
            subprotocols=["ocpp2.1"],
            headers={"Authorization": f"Basic {authorization}"},
        ) as websocket:
            assert websocket.accepted_subprotocol == "ocpp2.1"
            websocket.send_text(json.dumps([
                2,
                "boot-21",
                "BootNotification",
                {
                    "chargingStation": {
                        "vendorName": "Suntree",
                        "model": "SWG5T-7/32A",
                    },
                    "reason": "PowerUp",
                },
            ]))
            boot_response = json.loads(websocket.receive_text())
            assert boot_response[0:2] == [3, "boot-21"]
            assert boot_response[2]["status"] == "Accepted"

            websocket.send_text(json.dumps([2, "heartbeat-21", "Heartbeat", {}]))
            heartbeat_response = json.loads(websocket.receive_text())
            assert heartbeat_response[0:2] == [3, "heartbeat-21"]
            assert heartbeat_response[2]["currentTime"].endswith("Z")

            websocket.send_text(json.dumps([
                2,
                "status-21",
                "StatusNotification",
                {
                    "timestamp": "2026-10-12T00:00:00Z",
                    "connectorStatus": "Occupied",
                    "evseId": 2,
                    "connectorId": 1,
                },
            ]))
            status_response = json.loads(websocket.receive_text())
            assert status_response[0:2] == [3, "status-21"]

    charger = collections["cargadores"].documents[0]
    assert charger["subprotocolo_ocpp"] == "ocpp2.1"
    assert charger["fabricante"] == "Suntree"
    status_notification = collections["status_notifications"].documents[0]
    assert status_notification["estado"] == "Occupied"
    assert status_notification["ocpp_status"] == "Occupied"
    assert status_notification["evse_id"] == 2
    assert {
        message["subprotocolo_ocpp"]
        for message in collections["ocpp_messages"].documents
    } == {"ocpp2.1"}


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
def test_transaction_event_ended_closes_and_acknowledges_duplicate_over_websocket(
    monkeypatch,
    protocol,
):
    case = _make_transaction_event_case(monkeypatch, protocol)
    collections = case["collections"]
    reward_state = case["reward_state"]

    with TestClient(case["app"]) as client:
        with client.websocket_connect(
            f"/ocpp/{case['charge_point_id']}",
            subprotocols=[protocol],
            headers={"Authorization": f"Basic {case['authorization']}"},
        ) as websocket:
            assert websocket.accepted_subprotocol == protocol
            websocket.send_text(json.dumps(_transaction_event_message(
                "ended-first",
                case["transaction_id"],
            )))
            first_response = json.loads(websocket.receive_text())
            assert first_response[:2] == [3, "ended-first"]
            assert first_response[2] == {}

            stored_session = deepcopy(collections["sesiones"].documents[0])
            assert stored_session["estado"] == "finalizada"
            assert stored_session["energia_consumida_kwh"] == 2.0
            assert stored_session["duracion_minutos"] >= 7
            assert isinstance(stored_session["fecha_fin"], datetime)
            assert stored_session["stop_closure"]["rewards_granted"] is True
            assert _legacy_processed_session_ids(case) == {stored_session["_id"]}
            assert _company_reward_session_ids(case) == {stored_session["_id"]}
            assert len(collections["recompensas"].documents) == 1
            assert len(collections["company_reward_ledger"].documents) == 1
            assert collections["company_reward_balances"].documents[0][
                "puntos_disponibles"
            ] == 7

            websocket.send_text(json.dumps(_transaction_event_message(
                "ended-duplicate",
                case["transaction_id"],
            )))
            duplicate_response = json.loads(websocket.receive_text())

    assert duplicate_response[:2] == [3, "ended-duplicate"]
    assert duplicate_response[2] == {}
    assert len(collections["sesiones"].documents) == 1
    assert collections["sesiones"].documents[0]["fecha_fin"] == stored_session["fecha_fin"]
    assert collections["sesiones"].documents[0]["duracion_minutos"] == stored_session["duracion_minutos"]
    assert collections["sesiones"].documents[0]["energia_consumida_kwh"] == 2.0
    assert _legacy_processed_session_ids(case) == {
        collections["sesiones"].documents[0]["_id"]
    }
    assert _company_reward_session_ids(case) == {
        collections["sesiones"].documents[0]["_id"]
    }
    assert len(collections["recompensas"].documents) == 1
    assert len(collections["company_reward_ledger"].documents) == 1
    assert collections["company_reward_balances"].documents[0][
        "puntos_disponibles"
    ] == 7
    assert len(collections["usuarios"].documents) == 1
    assert collections["usuarios"].documents[0]["total_cargas"] == 19
    assert len([
        event for event in collections["eventos_ocpp"].documents
        if event.get("tipo_mensaje") == "StopTransaction"
    ]) == 1


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
@pytest.mark.parametrize("fail_stage", ["legacy", "points"])
def test_transaction_event_ended_retries_reward_failures_over_websocket(
    monkeypatch,
    protocol,
    fail_stage,
):
    case = _make_transaction_event_case(
        monkeypatch,
        protocol,
        fail_stage=fail_stage,
    )
    collections = case["collections"]
    reward_state = case["reward_state"]

    with TestClient(case["app"]) as client:
        with client.websocket_connect(
            f"/ocpp/{case['charge_point_id']}",
            subprotocols=[protocol],
            headers={"Authorization": f"Basic {case['authorization']}"},
        ) as websocket:
            assert websocket.accepted_subprotocol == protocol
            websocket.send_text(json.dumps(_transaction_event_message(
                "ended-failure",
                case["transaction_id"],
            )))
            failure_response = json.loads(websocket.receive_text())
            assert failure_response[:3] == [
                4,
                "ended-failure",
                "InternalError",
            ]

            stored_session = collections["sesiones"].documents[0]
            assert stored_session["estado"] == "finalizada"
            original_close_values = {
                key: stored_session[key]
                for key in (
                    "energia_consumida_kwh",
                    "duracion_minutos",
                    "fecha_fin",
                )
            }

            websocket.send_text(json.dumps(_transaction_event_message(
                "ended-retry",
                case["transaction_id"],
            )))
            retry_response = json.loads(websocket.receive_text())
            assert retry_response[:2] == [3, "ended-retry"]
            assert retry_response[2] == {}

            websocket.send_text(json.dumps(_transaction_event_message(
                "ended-after-completion",
                case["transaction_id"],
            )))
            duplicate_response = json.loads(websocket.receive_text())

    final_session = collections["sesiones"].documents[0]
    assert duplicate_response[:2] == [3, "ended-after-completion"]
    assert duplicate_response[2] == {}
    assert final_session["estado"] == "finalizada"
    assert {
        key: final_session[key]
        for key in original_close_values
    } == original_close_values
    assert final_session["stop_closure"]["rewards_granted"] is True
    assert _legacy_processed_session_ids(case) == {final_session["_id"]}
    assert _company_reward_session_ids(case) == {final_session["_id"]}
    assert len(collections["recompensas"].documents) == 1
    assert len(collections["company_reward_ledger"].documents) == 1
    assert collections["company_reward_balances"].documents[0][
        "puntos_disponibles"
    ] == 7
    assert len(collections["sesiones"].documents) == 1
    assert collections["usuarios"].documents[0]["total_cargas"] == 19
    assert len([
        event for event in collections["eventos_ocpp"].documents
        if event.get("tipo_mensaje") == "StopTransaction"
    ]) == 1


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
def test_transaction_event_ended_with_no_matching_session_is_acknowledged(
    monkeypatch,
    protocol,
):
    case = _make_transaction_event_case(monkeypatch, protocol, sessions=[])
    collections = case["collections"]

    with TestClient(case["app"]) as client:
        with client.websocket_connect(
            f"/ocpp/{case['charge_point_id']}",
            subprotocols=[protocol],
            headers={"Authorization": f"Basic {case['authorization']}"},
        ) as websocket:
            websocket.send_text(json.dumps(_transaction_event_message(
                "ended-unknown",
                case["transaction_id"],
            )))
            response = json.loads(websocket.receive_text())

    assert response[:2] == [3, "ended-unknown"]
    assert response[2] == {}
    assert collections["sesiones"].documents == []
    assert not case["reward_state"]["legacy_calls"]
    assert not case["reward_state"]["points_calls"]
    assert not case["collections"]["recompensas"].documents
    assert not case["collections"]["company_reward_ledger"].documents


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
@pytest.mark.parametrize("ambiguity", ["two_finalized", "active_and_finalized"])
def test_ambiguous_transaction_identity_is_rejected_without_side_effects(
    monkeypatch,
    caplog,
    protocol,
    ambiguity,
):
    case = _make_transaction_event_case(monkeypatch, protocol)
    session_collection = case["collections"]["sesiones"]
    first = deepcopy(session_collection.documents[0])
    second = deepcopy(first)
    second["_id"] = ObjectId()
    second["transaction_id"] = 272
    if ambiguity == "two_finalized":
        for session in (first, second):
            session.update({
                "estado": "finalizada",
                "fecha_fin": datetime.utcnow(),
                "stop_closure": {
                    "rewards_applied": False,
                    "rewards_granted": False,
                    "event_recorded": False,
                    "charger_cleanup_status": "pending",
                },
            })
    else:
        second.update({
            "estado": "finalizada",
            "fecha_fin": datetime.utcnow(),
            "stop_closure": {
                "rewards_applied": False,
                "rewards_granted": False,
                "event_recorded": False,
                "charger_cleanup_status": "pending",
            },
        })
    session_collection.documents = [first, second]
    sessions_before = deepcopy(session_collection.documents)

    with TestClient(case["app"]) as client:
        with client.websocket_connect(
            f"/ocpp/{case['charge_point_id']}",
            subprotocols=[protocol],
            headers={"Authorization": f"Basic {case['authorization']}"},
        ) as websocket:
            assert websocket.accepted_subprotocol == protocol
            websocket.send_text(json.dumps(_transaction_event_message(
                "ended-ambiguous",
                case["transaction_id"],
            )))
            response = json.loads(websocket.receive_text())

    assert response[:3] == [4, "ended-ambiguous", "InternalError"]
    assert "Identidad OCPP ambigua" in caplog.text
    assert case["charge_point_id"] in caplog.text
    assert case["transaction_id"] in caplog.text
    assert session_collection.documents == sessions_before
    assert not case["reward_state"]["legacy_calls"]
    assert not case["reward_state"]["points_calls"]
    assert not case["collections"]["recompensas"].documents
    assert not case["collections"]["company_reward_ledger"].documents
    assert len(case["collections"]["meter_values"].documents) == 0
    assert not [
        event for event in case["collections"]["eventos_ocpp"].documents
        if event.get("tipo_mensaje") == "StopTransaction"
    ]
