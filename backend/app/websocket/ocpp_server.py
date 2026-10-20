import asyncio
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import uuid4
from bson import ObjectId
from fastapi import WebSocket, WebSocketDisconnect
from pymongo.errors import DuplicateKeyError, PyMongoError
from ocpp.v16 import ChargePoint as OCPPChargePoint
from ocpp.v16.enums import Action, DataTransferStatus, RegistrationStatus
from ocpp.v16 import call_result
from ocpp.routing import on
from app.database import db, get_collection, next_transaction_id
from app.services.reward_service import verificar_y_otorgar_recompensa, aplicar_recompensa_en_sesion
from app.ocpp_auth import parse_ocpp_basic_auth, verify_ocpp_password

connected_chargers: Dict[str, WebSocket] = {}
logger = logging.getLogger(__name__)
RFC3339_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


def _current_time() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class OCPPConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.active_handlers: Dict[str, "ChargePointHandler"] = {}
        self.pending_connections: set[str] = set()
        self.disconnect_events: Dict[str, asyncio.Event] = {}
        self.connection_tokens: Dict[str, str] = {}
        self.message_registry: Dict[tuple[str, str], Dict[str, Any]] = {}
        self.transaction_registry: Dict[str, Optional[int]] = {}

    def reserve_connection(self, charge_point_id: str) -> bool:
        if (
            charge_point_id in self.active_connections
            or charge_point_id in self.pending_connections
        ):
            return False
        self.pending_connections.add(charge_point_id)
        return True

    def release_connection_reservation(self, charge_point_id: str) -> None:
        self.pending_connections.discard(charge_point_id)

    def register(self, charge_point_id: str, websocket: WebSocket) -> str:
        connection_token = uuid4().hex
        self.active_connections[charge_point_id] = websocket
        self.disconnect_events[charge_point_id] = asyncio.Event()
        self.connection_tokens[charge_point_id] = connection_token
        connected_chargers[charge_point_id] = websocket
        return connection_token

    async def replace(self, charge_point_id: str, websocket: WebSocket) -> str:
        previous = self.active_connections.get(charge_point_id)
        if previous is not None and previous is not websocket:
            previous_event = self.disconnect_events.get(charge_point_id)
            if previous_event is not None:
                previous_event.set()
            try:
                await previous.close(code=1000, reason="Replaced by a newer connection")
            except Exception:
                pass
        return self.register(charge_point_id, websocket)

    def unregister(self, charge_point_id: str, websocket: WebSocket) -> None:
        current_websocket = self.active_connections.get(charge_point_id)
        if current_websocket is not websocket:
            return
        disconnect_event = self.disconnect_events.pop(charge_point_id, None)
        self.active_connections.pop(charge_point_id, None)
        self.active_handlers.pop(charge_point_id, None)
        self.connection_tokens.pop(charge_point_id, None)
        connected_chargers.pop(charge_point_id, None)
        self.transaction_registry.pop(charge_point_id, None)
        if disconnect_event is not None:
            disconnect_event.set()

    def get_connection(self, charge_point_id: str) -> Optional[WebSocket]:
        return self.active_connections.get(charge_point_id)

    def is_current_connection(self, charge_point_id: str, websocket: WebSocket) -> bool:
        return self.active_connections.get(charge_point_id) is websocket

    def register_handler(self, charge_point_id: str, handler: "ChargePointHandler") -> None:
        self.active_handlers[charge_point_id] = handler

    def get_handler(self, charge_point_id: str) -> Optional["ChargePointHandler"]:
        return self.active_handlers.get(charge_point_id)

    def get_disconnect_event(self, charge_point_id: str) -> Optional[asyncio.Event]:
        return self.disconnect_events.get(charge_point_id)

    def get_connection_token(self, charge_point_id: str) -> Optional[str]:
        return self.connection_tokens.get(charge_point_id)

    def track_message(self, charge_point_id: str, message: Any, direction: str) -> Optional[Dict[str, Any]]:
        try:
            if isinstance(message, str):
                payload = json.loads(message)
            else:
                payload = message
        except (TypeError, ValueError):
            return None

        if not isinstance(payload, list) or len(payload) < 2:
            return None

        message_type = payload[0]
        unique_id = payload[1] if len(payload) > 1 else None
        action = payload[2] if message_type == 2 and len(payload) > 2 and isinstance(payload[2], str) else None
        registry_key = (charge_point_id, str(unique_id)) if unique_id is not None else None
        if message_type in {3, 4} and registry_key is not None:
            previous = self.message_registry.pop(registry_key, None)
            action = previous.get("action") if previous else None

        parsed: Dict[str, Any] = {
            "direction": direction,
            "charge_point_id": charge_point_id,
            "message_type": message_type,
            "unique_id": unique_id,
            "action": action,
            "payload": payload[3] if message_type == 2 and len(payload) > 3 else payload[2] if message_type == 3 and len(payload) > 2 else payload[4] if message_type == 4 and len(payload) > 4 else {},
            "error_code": payload[2] if message_type == 4 and len(payload) > 2 else None,
            "error_description": payload[3] if message_type == 4 and len(payload) > 3 else None,
            "timestamp": datetime.utcnow(),
        }

        if parsed["message_type"] == 2 and parsed["unique_id"] is not None:
            self.message_registry[(charge_point_id, str(parsed["unique_id"]))] = parsed

        return parsed

    def track_incoming(self, charge_point_id: str, raw_message: str) -> Optional[Dict[str, Any]]:
        return self.track_message(charge_point_id, raw_message, "incoming")

    def track_outgoing(self, charge_point_id: str, raw_message: str) -> Optional[Dict[str, Any]]:
        return self.track_message(charge_point_id, raw_message, "outgoing")


ocpp_connection_manager = OCPPConnectionManager()


async def persist_ocpp_message(
    charge_point_id: str,
    raw_message: str,
    parsed: Optional[Dict[str, Any]],
) -> None:
    try:
        raw_payload = json.loads(raw_message)
        safe_raw_message = json.dumps(
            _redact_sensitive_ocpp_data(raw_payload),
            ensure_ascii=False,
        )
    except (TypeError, ValueError):
        safe_raw_message = "[mensaje OCPP no parseable; oculto]"

    document = {
        "charge_point_id": charge_point_id,
        "direction": parsed.get("direction") if parsed else "unknown",
        "message_type": parsed.get("message_type") if parsed else None,
        "message_type_name": {
            2: "CALL",
            3: "CALLRESULT",
            4: "CALLERROR",
        }.get(parsed.get("message_type") if parsed else None, "UNKNOWN"),
        "unique_id": parsed.get("unique_id") if parsed else None,
        "action": parsed.get("action") if parsed else None,
        "payload": _redact_sensitive_ocpp_data(parsed.get("payload", {})) if parsed else {},
        "error_code": parsed.get("error_code") if parsed else None,
        "error_description": parsed.get("error_description") if parsed else None,
        "raw_message": safe_raw_message,
        "timestamp": parsed.get("timestamp", datetime.utcnow()) if parsed else datetime.utcnow(),
    }
    await get_collection("ocpp_messages").insert_one(document)
    if parsed:
        await get_collection("cargadores").update_one(
            {"charge_point_id": charge_point_id},
            {"$set": {"ultima_comunicacion": document["timestamp"]}},
        )


def _redact_sensitive_ocpp_data(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower().replace("_", "") == "idtag"
            else _redact_sensitive_ocpp_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_ocpp_data(item) for item in value]
    return value


async def _mark_stop_closure_stage(
    sessions_col, session: dict, transaction_id: int, stage: str
) -> None:
    result = await sessions_col.update_one(
        {
            "_id": session["_id"],
            "charge_point_id": session["charge_point_id"],
            "transaction_id": transaction_id,
            "estado": "finalizada",
        },
        {"$set": {f"stop_closure.{stage}": True}},
    )
    if result.matched_count != 1:
        raise RuntimeError(
            f"No se pudo registrar la etapa {stage} del cierre "
            f"de transaction_id {transaction_id}"
        )
    session.setdefault("stop_closure", {})[stage] = True


async def _mark_charger_cleanup_status(
    sessions_col,
    session: dict,
    transaction_id: int,
    status: str,
    observed_transaction_id: Optional[int] = None,
) -> None:
    session_filter = {
        "_id": session["_id"],
        "charge_point_id": session["charge_point_id"],
        "transaction_id": transaction_id,
        "estado": "finalizada",
    }
    update_fields = {"stop_closure.charger_cleanup_status": status}
    if observed_transaction_id is not None:
        update_fields[
            "stop_closure.charger_cleanup_observed_transaction_id"
        ] = observed_transaction_id

    result = await sessions_col.update_one(
        {
            **session_filter,
            "stop_closure.charger_cleanup_status": "pending",
        },
        {"$set": update_fields},
    )
    if result.matched_count == 1:
        cierre = session.setdefault("stop_closure", {})
        cierre["charger_cleanup_status"] = status
        if observed_transaction_id is not None:
            cierre["charger_cleanup_observed_transaction_id"] = (
                observed_transaction_id
            )
        return

    current = await sessions_col.find_one(session_filter)
    current_cierre = current.get("stop_closure") if current else None
    current_status = (
        current_cierre.get("charger_cleanup_status")
        if isinstance(current_cierre, dict)
        else None
    )
    current_observed_id = (
        current_cierre.get("charger_cleanup_observed_transaction_id")
        if isinstance(current_cierre, dict)
        else None
    )
    if (
        current_status == status
        and current_observed_id == observed_transaction_id
    ):
        session["stop_closure"] = current_cierre
        return
    raise RuntimeError(
        f"Conflicto al registrar el estado de limpieza del cargador "
        f"para transaction_id {transaction_id}: estado actual "
        f"{current_status!r}, resultado solicitado {status!r}"
    )


def _charger_cleanup_receipt_path(session_id: Any) -> str:
    session_key = f"{type(session_id).__name__}_{str(session_id).encode('utf-8').hex()}"
    return f"stop_closure_receipts.{session_key}"


def _read_charger_cleanup_receipt(charger: dict, session_id: Any, transaction_id: int):
    receipts = charger.get("stop_closure_receipts", {})
    if not isinstance(receipts, dict):
        raise RuntimeError("El registro de recibos de cierre del cargador es inválido")

    receipt_key = _charger_cleanup_receipt_path(session_id).rsplit(".", 1)[1]
    receipt = receipts.get(receipt_key)
    if receipt is None:
        if receipt_key in receipts:
            raise RuntimeError("El recibo de cierre del cargador es inválido")
        return None
    if (
        not isinstance(receipt, dict)
        or receipt.get("session_id") != session_id
        or receipt.get("transaction_id") != transaction_id
    ):
        raise RuntimeError(
            f"El recibo de limpieza de transaction_id {transaction_id} es inválido"
        )

    status = receipt.get("status")
    observed_transaction_id = receipt.get("observed_transaction_id")
    if status == "completed" and observed_transaction_id is None:
        return status, None
    if (
        status == "skipped_foreign_transaction"
        and observed_transaction_id is not None
        and observed_transaction_id != transaction_id
    ):
        return status, observed_transaction_id
    raise RuntimeError(
        f"El recibo de limpieza de transaction_id {transaction_id} "
        "tiene un resultado inválido"
    )


async def _persist_stop_event(
    charge_point_id: str, transaction_id: int, meter_stop, kwargs
) -> None:
    await get_collection("eventos_ocpp").update_one(
        {"_id": f"stop:{charge_point_id}:{transaction_id}"},
        {"$setOnInsert": {
            "charge_point_id": charge_point_id,
            "tipo_mensaje": "StopTransaction",
            "accion": "request",
            "payload": {
                "transaction_id": transaction_id,
                "meter_stop": meter_stop,
                **kwargs,
            },
            "timestamp": datetime.utcnow(),
        }},
        upsert=True,
    )


def _sample_number(sampled: dict) -> Optional[float]:
    try:
        return float(sampled.get("value"))
    except (TypeError, ValueError, OverflowError):
        return None


def _energy_to_wh(value: float, unit: Optional[str]) -> Optional[float]:
    normalized = (unit or "").strip().lower()
    if normalized == "wh":
        return value
    if normalized == "kwh":
        return value * 1000
    return None


def _power_to_kw(value: float, unit: Optional[str]) -> Optional[float]:
    normalized = (unit or "").strip().lower()
    if normalized == "kw":
        return value
    if normalized == "w":
        return value / 1000
    return None


class OCPPWebSocketProxy:
    def __init__(self, websocket: WebSocket, charge_point_id: str, manager: OCPPConnectionManager):
        self._websocket = websocket
        self._charge_point_id = charge_point_id
        self._manager = manager

    def __getattr__(self, name: str):
        return getattr(self._websocket, name)

    async def recv(self) -> str:
        raw_message = await self._websocket.receive_text()
        parsed = self._manager.track_incoming(self._charge_point_id, raw_message)
        _log_ocpp_frame("recibido", self._charge_point_id, parsed)
        await persist_ocpp_message(self._charge_point_id, raw_message, parsed)
        return raw_message

    async def send(self, data: str) -> None:
        parsed = self._manager.track_outgoing(self._charge_point_id, data)
        _log_ocpp_frame("enviado", self._charge_point_id, parsed)
        await persist_ocpp_message(self._charge_point_id, data, parsed)
        await self._websocket.send_text(data)

    async def receive_text(self) -> str:
        return await self.recv()

    async def send_text(self, data: str) -> None:
        await self.send(data)


def _log_ocpp_frame(
    direction: str,
    charge_point_id: str,
    parsed: Optional[Dict[str, Any]],
) -> None:
    safe_charge_point_id = _safe_ocpp_log_value(charge_point_id)
    if parsed is None:
        logger.warning(
            "Trama OCPP %s no reconocida para %s; contenido omitido",
            direction,
            safe_charge_point_id,
        )
        return

    message_type = {
        2: "CALL",
        3: "CALLRESULT",
        4: "CALLERROR",
    }.get(parsed.get("message_type"), "UNKNOWN")
    action = _safe_ocpp_log_value(parsed.get("action") or "sin acción")
    error_code = parsed.get("error_code")
    if message_type == "CALLERROR":
        logger.warning(
            "Trama OCPP %s: cargador=%s tipo=%s acción=%s código=%s; "
            "payload omitido",
            direction,
            safe_charge_point_id,
            message_type,
            action,
            _safe_ocpp_log_value(error_code or "sin código"),
        )
        return

    logger.warning(
        "Trama OCPP %s: cargador=%s tipo=%s acción=%s",
        direction,
        safe_charge_point_id,
        message_type,
        action,
    )


def _safe_ocpp_log_value(value: Any) -> str:
    return "".join(
        character if character >= " " and character != "\x7f" else " "
        for character in str(value)
    )[:200]


class ChargePointHandler(OCPPChargePoint):
    def __init__(
        self,
        charge_point_id: str,
        websocket: WebSocket,
        connection_token: Optional[str] = None,
        negotiated_subprotocol: str = "ocpp1.6j",
    ):
        super().__init__(charge_point_id, websocket)
        self.websocket = websocket
        self.charge_point_id = charge_point_id
        self.connection_token = connection_token
        self.negotiated_subprotocol = negotiated_subprotocol

    @on(Action.BootNotification)
    async def on_boot_notification(self, charge_point_vendor, charge_point_model, **kwargs):
        cargadores_col = get_collection("cargadores")
        try:
            active_sessions = [
                session
                async for session in get_collection("sesiones")
                .find({
                    "charge_point_id": self.charge_point_id,
                    "estado": "activa",
                })
                .limit(2)
            ]
        except Exception:
            logger.exception(
                "No se pudieron reconciliar sesiones al iniciar %s",
                self.charge_point_id,
            )
            return call_result.BootNotificationPayload(
                current_time=_current_time(),
                interval=60,
                status=RegistrationStatus.rejected,
            )

        updates = {
            "fabricante": charge_point_vendor,
            "modelo": charge_point_model,
            "ultimo_heartbeat": datetime.utcnow(),
            "conectado": True,
            "ultima_conexion": datetime.utcnow(),
            **({"connection_token": self.connection_token} if self.connection_token else {}),
            "subprotocolo_ocpp": self.negotiated_subprotocol,
            **({"firmware_version": kwargs["firmware_version"]} if kwargs.get("firmware_version") else {}),
        }
        if len(active_sessions) == 1:
            session = active_sessions[0]
            updates.update({
                "estado_general": "ocupado",
                "sesion_actual": {
                    "transaction_id": session["transaction_id"],
                    "usuario_id": (
                        str(session["usuario_id"])
                        if session.get("usuario_id") is not None
                        else None
                    ),
                    "vehiculo_id": (
                        str(session["vehiculo_id"])
                        if session.get("vehiculo_id") is not None
                        else None
                    ),
                    "hora_inicio": session.get("fecha_inicio"),
                    "energia_consumida_kwh": session.get(
                        "energia_consumida_kwh", 0.0
                    ),
                },
            })
        elif len(active_sessions) == 0:
            updates.update({
                "estado_general": "disponible",
                "ultimo_estado_conocido": "Available",
            })
        else:
            logger.error(
                "Se encontraron al menos %d sesiones activas para el cargador %s; "
                "se omite reconciliar su estado operativo",
                len(active_sessions),
                self.charge_point_id,
            )

        try:
            result = await cargadores_col.update_one(
                {"charge_point_id": self.charge_point_id, "activo": True},
                {"$set": updates},
            )
        except Exception:
            logger.exception(
                "No se pudieron actualizar los metadatos/reconciliar el cargador %s",
                self.charge_point_id,
            )
            return call_result.BootNotificationPayload(
                current_time=_current_time(),
                interval=60,
                status=RegistrationStatus.rejected,
            )

        await get_collection("eventos_ocpp").insert_one({
            "charge_point_id": self.charge_point_id,
            "tipo_mensaje": "BootNotification",
            "accion": "request",
            "payload": {"vendor": charge_point_vendor, "model": charge_point_model, **kwargs},
            "timestamp": datetime.utcnow()
        })

        if result.matched_count == 0:
            return call_result.BootNotificationPayload(
                current_time=_current_time(),
                interval=60,
                status=RegistrationStatus.rejected
            )

        return call_result.BootNotificationPayload(
            current_time=_current_time(),
            interval=60,  # Intervalo de 60 segundos sugerido para Heartbeat
            status=RegistrationStatus.accepted
        )

    @on(Action.Heartbeat)
    async def on_heartbeat(self):
        await get_collection("cargadores").update_one(
            {"charge_point_id": self.charge_point_id},
            {"$set": {
                "ultimo_heartbeat": datetime.utcnow(),
                "ultimo_estado_conocido": "Heartbeat",
            }}
        )
        
        # Registrar evento en la colección general de eventos OCPP para visibilidad en frontend
        await get_collection("eventos_ocpp").insert_one({
            "charge_point_id": self.charge_point_id,
            "tipo_mensaje": "Heartbeat",
            "accion": "request",
            "payload": {},
            "timestamp": datetime.utcnow()
        })

        await get_collection("heartbeats").insert_one({
            "charge_point_id": self.charge_point_id,
            "timestamp": datetime.utcnow()
        })
        
        return call_result.HeartbeatPayload(current_time=_current_time())

    @on(Action.DataTransfer)
    async def on_data_transfer(self, vendor_id, message_id=None, data=None):
        await get_collection("eventos_ocpp").insert_one({
            "charge_point_id": self.charge_point_id,
            "tipo_mensaje": "DataTransfer",
            "accion": "request",
            "payload": {
                "vendor_id": vendor_id,
                "message_id": message_id,
                "data": data,
                "resultado": DataTransferStatus.unknown_vendor_id.value,
            },
            "timestamp": datetime.utcnow(),
        })
        return call_result.DataTransferPayload(
            status=DataTransferStatus.unknown_vendor_id
        )

    @on(Action.DiagnosticsStatusNotification)
    async def on_diagnostics_status_notification(self, status, **kwargs):
        await get_collection("eventos_ocpp").insert_one({
            "charge_point_id": self.charge_point_id,
            "tipo_mensaje": "DiagnosticsStatusNotification",
            "accion": "request",
            "payload": {"status": status, **kwargs},
            "timestamp": datetime.utcnow(),
        })
        return call_result.DiagnosticsStatusNotificationPayload()

    @on(Action.FirmwareStatusNotification)
    async def on_firmware_status_notification(self, status, **kwargs):
        await get_collection("eventos_ocpp").insert_one({
            "charge_point_id": self.charge_point_id,
            "tipo_mensaje": "FirmwareStatusNotification",
            "accion": "request",
            "payload": {"status": status, **kwargs},
            "timestamp": datetime.utcnow(),
        })
        return call_result.FirmwareStatusNotificationPayload()

    @on(Action.StatusNotification)
    async def on_status_notification(self, connector_id, status, error_code, **kwargs):
        cargadores_col = get_collection("cargadores")
        connector_update_time = datetime.utcnow()
        connector_fields = {
            "estado": status,
            "error_code": error_code,
            "ultima_actualizacion": connector_update_time,
        }
        new_connector = {
            "id_conector": connector_id,
            **connector_fields,
        }
        await cargadores_col.update_one(
            {"charge_point_id": self.charge_point_id},
            [
                {
                    "$set": {
                        "conectores": {
                            "$let": {
                                "vars": {
                                    "connectors": {
                                        "$ifNull": ["$conectores", []]
                                    }
                                },
                                "in": {
                                    "$cond": [
                                        {
                                            "$in": [
                                                connector_id,
                                                "$$connectors.id_conector",
                                            ]
                                        },
                                        {
                                            "$map": {
                                                "input": "$$connectors",
                                                "as": "connector",
                                                "in": {
                                                    "$cond": [
                                                        {
                                                            "$eq": [
                                                                "$$connector.id_conector",
                                                                connector_id,
                                                            ]
                                                        },
                                                        {
                                                            "$mergeObjects": [
                                                                "$$connector",
                                                                {
                                                                    "$literal": connector_fields
                                                                },
                                                            ]
                                                        },
                                                        "$$connector",
                                                    ]
                                                },
                                            }
                                        },
                                        {
                                            "$concatArrays": [
                                                "$$connectors",
                                                {"$literal": [new_connector]},
                                            ]
                                        },
                                    ]
                                },
                            }
                        }
                    }
                },
                {
                    "$set": {
                        "estado_general": {
                            "$let": {
                                "vars": {
                                    "statuses": {
                                        "$map": {
                                            "input": {"$ifNull": ["$conectores", []]},
                                            "as": "connector",
                                            "in": "$$connector.estado",
                                        }
                                    }
                                },
                                "in": {
                                    "$switch": {
                                        "branches": [
                                            {
                                                "case": {
                                                    "$gt": [
                                                        {
                                                            "$size": {
                                                                "$setIntersection": [
                                                                    "$$statuses",
                                                                    [
                                                                        "Charging",
                                                                        "Preparing",
                                                                        "SuspendedEV",
                                                                        "SuspendedEVSE",
                                                                        "Finishing",
                                                                        "Reserved",
                                                                    ],
                                                                ]
                                                            }
                                                        },
                                                        0,
                                                    ]
                                                },
                                                "then": "ocupado",
                                            },
                                            {
                                                "case": {
                                                    "$in": ["Available", "$$statuses"]
                                                },
                                                "then": "disponible",
                                            },
                                            {
                                                "case": {
                                                    "$in": ["Faulted", "$$statuses"]
                                                },
                                                "then": "falla",
                                            },
                                            {
                                                "case": {
                                                    "$in": ["Unavailable", "$$statuses"]
                                                },
                                                "then": "fuera_de_servicio",
                                            },
                                        ],
                                        "default": "fuera_de_servicio",
                                    }
                                },
                            }
                        },
                        "ultimo_estado_conocido": status,
                        "ultimo_error": (
                            error_code if error_code != "NoError" else None
                        ),
                    }
                },
            ],
        )

        await get_collection("eventos_ocpp").insert_one({
            "charge_point_id": self.charge_point_id,
            "tipo_mensaje": "StatusNotification",
            "accion": "request",
            "payload": {"connector_id": connector_id, "status": status, "error_code": error_code, **kwargs},
            "timestamp": datetime.utcnow()
        })

        await get_collection("status_notifications").insert_one({
            "charge_point_id": self.charge_point_id,
            "connector_id": connector_id,
            "estado": status,
            "error_code": error_code,
            "timestamp": datetime.utcnow(),
            "info": kwargs.get("info"),
            "vendor_id": kwargs.get("vendor_id"),
            "vendor_error_code": kwargs.get("vendor_error_code")
        })

        if status == "Faulted":
            await get_collection("logs").insert_one({
                "nivel": "warning",
                "modulo": "ocpp",
                "mensaje": f"Cargador {self.charge_point_id} conector {connector_id} en Faulted",
                "datos": {"error_code": error_code},
                "timestamp": datetime.utcnow()
            })

        return call_result.StatusNotificationPayload()

    @on(Action.Authorize)
    async def on_authorize(self, id_tag):
        nfc_col = get_collection("nfc_cards")
        tarjeta = await nfc_col.find_one({"uid": id_tag})
        status = "Invalid"
        now = datetime.utcnow()

        if tarjeta and tarjeta.get("estado") == "activa":
            usuario = await get_collection("usuarios").find_one(
                {"_id": tarjeta.get("usuario_id")}
            )
            if usuario and usuario.get("activo", True):
                approved_request = await get_collection(
                    "nfc_charge_requests"
                ).find_one({
                    "charge_point_id": self.charge_point_id,
                    "uid": id_tag,
                    "usuario_id": tarjeta.get("usuario_id"),
                    "status": "start_requested",
                })
                if (
                    approved_request
                    and approved_request.get("expires_at")
                    and approved_request["expires_at"] > now
                ):
                    status = "Accepted"
                else:
                    await get_collection("nfc_charge_requests").insert_one({
                        "_id": ObjectId(),
                        "charge_point_id": self.charge_point_id,
                        "uid": id_tag,
                        "card_id": tarjeta["_id"],
                        "usuario_id": tarjeta["usuario_id"],
                        "status": "awaiting_vehicle",
                        "created_at": now,
                        "expires_at": now + timedelta(minutes=3),
                    })

        await get_collection("eventos_ocpp").insert_one({
            "charge_point_id": self.charge_point_id,
            "tipo_mensaje": "Authorize",
            "accion": "request",
            "payload": {"resultado": status, "tag_present": bool(id_tag)},
            "timestamp": datetime.utcnow()
        })

        return call_result.AuthorizePayload(id_tag_info={"status": status})

    @on(Action.StartTransaction)
    async def on_start_transaction(self, connector_id, id_tag, meter_start, timestamp, **kwargs):
        nfc_col = get_collection("nfc_cards")
        tarjeta = await nfc_col.find_one({"uid": id_tag})
        now = datetime.utcnow()

        if not tarjeta or tarjeta.get("estado") != "activa":
            await get_collection("eventos_ocpp").insert_one({
                "charge_point_id": self.charge_point_id,
                "tipo_mensaje": "StartTransaction",
                "accion": "request",
                "payload": {"connector_id": connector_id, "resultado": "Invalid"},
                "timestamp": datetime.utcnow(),
            })
            return call_result.StartTransactionPayload(
                transaction_id=0,
                id_tag_info={"status": "Invalid"},
            )

        usuario_id = tarjeta.get("usuario_id")
        usuario = await get_collection("usuarios").find_one({"_id": usuario_id})
        solicitud_col = get_collection("nfc_charge_requests")
        solicitud = await solicitud_col.find_one({
            "charge_point_id": self.charge_point_id,
            "uid": id_tag,
            "usuario_id": usuario_id,
            "status": "start_requested",
        })
        if solicitud is None:
            started_request = await solicitud_col.find_one({
                "charge_point_id": self.charge_point_id,
                "uid": id_tag,
                "usuario_id": usuario_id,
                "status": "transaction_started",
            })
            active_session = None
            if started_request is not None:
                active_session = await get_collection("sesiones").find_one({
                    "charge_point_id": self.charge_point_id,
                    "nfc_card_id": tarjeta["_id"],
                    "estado": "activa",
                })
            if active_session is not None:
                await get_collection("eventos_ocpp").insert_one({
                    "charge_point_id": self.charge_point_id,
                    "tipo_mensaje": "StartTransaction",
                    "accion": "request",
                    "payload": {
                        "connector_id": connector_id,
                        "resultado": "ConcurrentTx",
                        "transaction_id": active_session.get("transaction_id"),
                    },
                    "timestamp": now,
                })
                return call_result.StartTransactionPayload(
                    transaction_id=0,
                    id_tag_info={"status": "ConcurrentTx"},
                )
        if (
            usuario is None
            or not usuario.get("activo", True)
            or solicitud is None
            or solicitud.get("expires_at") is None
            or solicitud["expires_at"] <= now
        ):
            await get_collection("eventos_ocpp").insert_one({
                "charge_point_id": self.charge_point_id,
                "tipo_mensaje": "StartTransaction",
                "accion": "request",
                "payload": {"connector_id": connector_id, "resultado": "Invalid"},
                "timestamp": now,
            })
            return call_result.StartTransactionPayload(
                transaction_id=0,
                id_tag_info={"status": "Invalid"},
            )

        connector_id_requested = solicitud.get("connector_id")
        if connector_id_requested != connector_id:
            await get_collection("eventos_ocpp").insert_one({
                "charge_point_id": self.charge_point_id,
                "tipo_mensaje": "StartTransaction",
                "accion": "request",
                "payload": {"connector_id": connector_id, "resultado": "Invalid"},
                "timestamp": now,
            })
            return call_result.StartTransactionPayload(
                transaction_id=0,
                id_tag_info={"status": "Invalid"},
            )

        vehiculo_id = solicitud.get("vehicle_id")
        vehiculo = await get_collection("vehiculos").find_one({
            "_id": vehiculo_id,
            "usuario_id": usuario_id,
            "activo": True,
        })
        if vehiculo is None:
            return call_result.StartTransactionPayload(
                transaction_id=0,
                id_tag_info={"status": "Invalid"},
            )

        sesiones_col = get_collection("sesiones")
        cargadores_col = get_collection("cargadores")
        eventos_col = get_collection("eventos_ocpp")

        async def persist_start(mongo_session):
            sesion_activa = await sesiones_col.find_one(
                {
                    "charge_point_id": self.charge_point_id,
                    "estado": "activa",
                },
                session=mongo_session,
            )
            if sesion_activa:
                await eventos_col.insert_one(
                    {
                        "charge_point_id": self.charge_point_id,
                        "tipo_mensaje": "StartTransaction",
                        "accion": "request",
                        "payload": {
                            "connector_id": connector_id,
                            "resultado": "ConcurrentTx",
                            "transaction_id": sesion_activa.get("transaction_id"),
                        },
                        "timestamp": datetime.utcnow(),
                    },
                    session=mongo_session,
                )
                return call_result.StartTransactionPayload(
                    transaction_id=0,
                    id_tag_info={"status": "ConcurrentTx"},
                )

            request_update = await solicitud_col.update_one(
                {
                    "_id": solicitud["_id"],
                    "status": "start_requested",
                    "charge_point_id": self.charge_point_id,
                    "usuario_id": usuario_id,
                },
                {"$set": {
                    "status": "transaction_started",
                    "started_at": now,
                }},
                session=mongo_session,
            )
            if request_update.matched_count != 1:
                return call_result.StartTransactionPayload(
                    transaction_id=0,
                    id_tag_info={"status": "Invalid"},
                )

            transaction_id = await next_transaction_id()
            charger_update = await cargadores_col.update_one(
                {"charge_point_id": self.charge_point_id},
                {
                    "$set": {
                        "estado_general": "ocupado",
                        "sesion_actual": {
                            "transaction_id": transaction_id,
                            "usuario_id": str(usuario_id) if usuario_id else None,
                            "vehiculo_id": str(vehiculo_id) if vehiculo_id else None,
                            "hora_inicio": datetime.utcnow(),
                            "energia_consumida_kwh": 0.0,
                        },
                    }
                },
                session=mongo_session,
            )
            if charger_update.matched_count != 1:
                raise RuntimeError(
                    f"No se encontró el cargador {self.charge_point_id} "
                    f"al iniciar transaction_id {transaction_id}"
                )

            session_started_at = datetime.utcnow()
            sesion = {
                "charge_point_id": self.charge_point_id,
                "connector_id": connector_id,
                "usuario_id": usuario_id,
                "vehiculo_id": vehiculo_id,
                "nfc_card_id": tarjeta["_id"],
                "transaction_id": transaction_id,
                "estado": "activa",
                "fecha_inicio": session_started_at,
                "energia_consumida_kwh": 0.0,
                "potencia_maxima_kw": 0.0,
                "potencia_promedio_kw": 0.0,
                "duracion_minutos": 0,
                "es_carga_gratuita": False,
                "beneficio_aplicado": None,
                "meter_start": meter_start,
                "meter_stop": None,
                "created_at": session_started_at,
            }
            await sesiones_col.insert_one(sesion, session=mongo_session)
            await eventos_col.insert_one(
                {
                    "charge_point_id": self.charge_point_id,
                    "tipo_mensaje": "StartTransaction",
                    "accion": "request",
                    "payload": {
                        "transaction_id": transaction_id,
                        "connector_id": connector_id,
                    },
                    "timestamp": session_started_at,
                },
                session=mongo_session,
            )

            return call_result.StartTransactionPayload(
                transaction_id=transaction_id,
                id_tag_info={"status": "Accepted"},
            )

        try:
            async with await db.client.start_session() as mongo_session:
                for transaction_attempt in range(3):
                    mongo_session.start_transaction()
                    try:
                        response = await persist_start(mongo_session)
                    except PyMongoError as exc:
                        await mongo_session.abort_transaction()
                        if (
                            exc.has_error_label("TransientTransactionError")
                            and transaction_attempt < 2
                        ):
                            continue
                        raise
                    except Exception:
                        await mongo_session.abort_transaction()
                        raise

                    for commit_attempt in range(3):
                        try:
                            await mongo_session.commit_transaction()
                            return response
                        except PyMongoError as exc:
                            if (
                                exc.has_error_label("UnknownTransactionCommitResult")
                                and commit_attempt < 2
                            ):
                                continue
                            if (
                                exc.has_error_label("TransientTransactionError")
                                and transaction_attempt < 2
                            ):
                                await mongo_session.abort_transaction()
                                break
                            if not exc.has_error_label(
                                "UnknownTransactionCommitResult"
                            ):
                                await mongo_session.abort_transaction()
                            raise
                    else:
                        raise RuntimeError(
                            "No se pudo confirmar la transacción StartTransaction"
                        )

                raise RuntimeError(
                    "Se agotaron los reintentos de la transacción StartTransaction"
                )
        except DuplicateKeyError as error:
            if "one_active_session_per_client" not in str(error):
                raise
            await eventos_col.insert_one({
                "charge_point_id": self.charge_point_id,
                "tipo_mensaje": "StartTransaction",
                "accion": "request",
                "payload": {
                    "connector_id": connector_id,
                    "resultado": "ConcurrentTx",
                },
                "timestamp": datetime.utcnow(),
            })
            return call_result.StartTransactionPayload(
                transaction_id=0,
                id_tag_info={"status": "ConcurrentTx"},
            )

    @on(Action.MeterValues)
    async def on_meter_values(self, connector_id, transaction_id, meter_value, **kwargs):
        if not isinstance(meter_value, list):
            raise ValueError("MeterValues meter_value must be a list")

        validated_values = []
        for meter in meter_value:
            if not isinstance(meter, dict):
                raise ValueError("MeterValues entries must be objects")
            measurement_timestamp = meter.get("timestamp")
            if (
                not isinstance(measurement_timestamp, str)
                or not RFC3339_TIMESTAMP.fullmatch(measurement_timestamp)
            ):
                raise ValueError("MeterValues timestamp must be RFC 3339")
            try:
                parsed_timestamp = datetime.fromisoformat(
                    measurement_timestamp.replace("Z", "+00:00")
                )
            except ValueError as error:
                raise ValueError("MeterValues timestamp must be RFC 3339") from error
            if parsed_timestamp.utcoffset() is None:
                raise ValueError("MeterValues timestamp must include a timezone")

            sampled_values = meter.get("sampled_value", [])
            if not isinstance(sampled_values, list):
                raise ValueError("MeterValues sampled_value must be a list")
            validated_samples = []
            for sampled in sampled_values:
                if not isinstance(sampled, dict):
                    raise ValueError("MeterValues samples must be objects")
                number = _sample_number(sampled)
                if number is None or not math.isfinite(number):
                    raise ValueError("MeterValues sample value must be a finite number")
                validated_samples.append((sampled, number))
            validated_values.append(
                (measurement_timestamp, validated_samples)
            )

        energy_wh = None
        power_kw = None
        telemetry_documents = []
        for measurement_timestamp, validated_samples in validated_values:
            for sampled, number in validated_samples:
                tipo = "other"
                measurand = str(sampled.get("measurand", ""))
                if "Energy.Active.Import.Register" in measurand:
                    tipo = "energy"
                elif "Power.Active.Import" in measurand:
                    tipo = "power"
                elif "Voltage" in measurand:
                    tipo = "voltage"
                elif "Current.Import" in measurand:
                    tipo = "current"

                telemetry_documents.append({
                    "charge_point_id": self.charge_point_id,
                    "connector_id": connector_id,
                    "transaction_id": transaction_id,
                    "tipo_medicion": tipo,
                    "valor": number,
                    "unidad": sampled.get("unit", "Unknown"),
                    "timestamp_medicion": measurement_timestamp,
                    "timestamp": datetime.utcnow(),
                })
                if "Energy.Active.Import.Register" in measurand:
                    energy_wh = _energy_to_wh(number, sampled.get("unit"))
                if "Power.Active.Import" in measurand:
                    power_kw = _power_to_kw(number, sampled.get("unit"))

        sesiones_col = get_collection("sesiones")
        async with await db.client.start_session() as mongo_session:
            async with mongo_session.start_transaction():
                sesion = await sesiones_col.find_one(
                    {
                        "transaction_id": transaction_id,
                        "charge_point_id": self.charge_point_id,
                        "connector_id": connector_id,
                    },
                    session=mongo_session,
                )
                if sesion is None:
                    await get_collection("logs").insert_one(
                        {
                            "nivel": "warning",
                            "modulo": "ocpp",
                            "mensaje": (
                                "MeterValues recibido para una transacción o "
                                f"conector no asociado a este cargador: {transaction_id}"
                            ),
                            "datos": {
                                "charge_point_id": self.charge_point_id,
                                "connector_id": connector_id,
                                "transaction_id": transaction_id,
                            },
                            "timestamp": datetime.utcnow(),
                        },
                        session=mongo_session,
                    )
                    return call_result.MeterValuesPayload()

                await get_collection("meter_values").insert_one(
                    {
                        "charge_point_id": self.charge_point_id,
                        "connector_id": connector_id,
                        "transaction_id": transaction_id,
                        "sampled_values": meter_value,
                        "timestamp": datetime.utcnow(),
                    },
                    session=mongo_session,
                )
                for telemetry_document in telemetry_documents:
                    await get_collection("telemetria").insert_one(
                        telemetry_document,
                        session=mongo_session,
                    )

                if sesion.get("estado") == "activa":
                    updates = {}
                    if energy_wh is not None and sesion.get("meter_start") is not None:
                        updates["energia_consumida_kwh"] = round(
                            max(0, energy_wh - sesion["meter_start"]) / 1000, 3
                        )
                    if power_kw is not None:
                        updates["potencia_maxima_kw"] = power_kw
                    if updates:
                        session_update = {"$set": {}}
                        if "potencia_maxima_kw" in updates:
                            session_update["$set"]["energia_consumida_kwh"] = updates.get(
                                "energia_consumida_kwh",
                                sesion.get("energia_consumida_kwh", 0),
                            )
                            session_update["$max"] = {
                                "potencia_maxima_kw": updates["potencia_maxima_kw"]
                            }
                        else:
                            session_update["$set"] = updates
                        await sesiones_col.update_one(
                            {
                                "transaction_id": transaction_id,
                                "charge_point_id": self.charge_point_id,
                                "connector_id": connector_id,
                                "estado": "activa",
                            },
                            session_update,
                            session=mongo_session,
                        )

        return call_result.MeterValuesPayload()

    @on(Action.StopTransaction)
    async def on_stop_transaction(self, transaction_id, meter_stop, timestamp, **kwargs):
        sesiones_col = get_collection("sesiones")
        active_query = {
            "transaction_id": transaction_id,
            "charge_point_id": self.charge_point_id,
            "estado": "activa",
        }
        sesion = await sesiones_col.find_one(active_query)

        if sesion:
            fecha_inicio = sesion["fecha_inicio"]
            duracion = int((datetime.utcnow() - fecha_inicio).total_seconds() / 60)
            energia = max(0, meter_stop - (sesion.get("meter_start") or 0)) / 1000

            transition = None
            for _ in range(3):
                selection_filter = {
                    "selected_reward_id": sesion.get("selected_reward_id")
                }
                cierre = {
                    "reward_selection_complete": bool(
                        sesion.get("selected_reward_id")
                    ),
                    "reward_id": sesion.get("selected_reward_id"),
                    "rewards_applied": False,
                    "rewards_granted": False,
                    "charger_cleanup_status": "pending",
                    "event_recorded": False,
                }
                transition = await sesiones_col.update_one(
                    {**active_query, **selection_filter},
                    {"$set": {
                        "estado": "finalizada",
                        "fecha_fin": datetime.utcnow(),
                        "energia_consumida_kwh": round(energia, 3),
                        "duracion_minutos": duracion,
                        "meter_stop": meter_stop,
                        "motivo_fin": kwargs.get("reason", "Normal"),
                        "stop_closure": cierre,
                    }},
                )
                if transition.modified_count == 1:
                    break
                sesion = await sesiones_col.find_one(active_query)
                if sesion is None:
                    break

            if transition and transition.modified_count == 1:
                sesion["estado"] = "finalizada"
                sesion["stop_closure"] = cierre
            else:
                sesion = None

        if sesion is None:
            sesion = await sesiones_col.find_one({
                "transaction_id": transaction_id,
                "charge_point_id": self.charge_point_id,
                "estado": "finalizada",
            })

        if sesion is None:
            await get_collection("logs").insert_one({
                "nivel": "warning",
                "modulo": "ocpp",
                "mensaje": f"StopTransaction recibido para transaction_id inexistente: {transaction_id}",
                "datos": {"charge_point_id": self.charge_point_id, "transaction_id": transaction_id},
                "timestamp": datetime.utcnow(),
            })
            await _persist_stop_event(
                self.charge_point_id, transaction_id, meter_stop, kwargs
            )
            return call_result.StopTransactionPayload(
                id_tag_info={"status": "Invalid"}
            )

        cierre = sesion.get("stop_closure")
        if not isinstance(cierre, dict):
            raise RuntimeError(
                f"La sesión {transaction_id} ya estaba finalizada sin estado "
                "de progreso recuperable"
            )
        if "charger_cleared" in cierre:
            raise RuntimeError(
                f"La sesión {transaction_id} usa un estado antiguo y ambiguo "
                "de limpieza del cargador; requiere revisión administrativa"
            )
        charger_cleanup_status = cierre.get("charger_cleanup_status")
        if charger_cleanup_status not in {
            "pending",
            "completed",
            "skipped_foreign_transaction",
        }:
            raise RuntimeError(
                f"La sesión {transaction_id} tiene un estado de limpieza "
                "del cargador ausente o inválido"
            )

        usuario_id = sesion.get("usuario_id")
        if not cierre.get("rewards_applied"):
            if usuario_id:
                resultado = await aplicar_recompensa_en_sesion(
                    str(usuario_id), str(sesion["_id"])
                )
                if resultado.get("tipo") != "ninguna":
                    await get_collection("logs").update_one(
                        {"_id": f"stop-reward:{self.charge_point_id}:{transaction_id}"},
                        {"$setOnInsert": {
                            "nivel": "info",
                            "modulo": "recompensas",
                            "mensaje": resultado.get("mensaje"),
                            "datos": {
                                "usuario_id": str(usuario_id),
                                "sesion_id": str(sesion["_id"]),
                            },
                            "timestamp": datetime.utcnow(),
                        }},
                        upsert=True,
                    )
            await _mark_stop_closure_stage(
                sesiones_col, sesion, transaction_id, "rewards_applied"
            )

        if not cierre.get("rewards_granted"):
            if usuario_id:
                await verificar_y_otorgar_recompensa(str(usuario_id))
            await _mark_stop_closure_stage(
                sesiones_col, sesion, transaction_id, "rewards_granted"
            )

        if charger_cleanup_status == "pending":
            cargadores_col = get_collection("cargadores")
            receipt_path = _charger_cleanup_receipt_path(sesion["_id"])
            receipt_key = receipt_path.rsplit(".", 1)[1]
            charger_filter = {
                "charge_point_id": self.charge_point_id,
                "$or": [
                    {"sesion_actual": None},
                    {"sesion_actual.transaction_id": transaction_id},
                ],
            }
            cleanup_resolved = False
            for _ in range(3):
                charger = await cargadores_col.find_one(
                    {"charge_point_id": self.charge_point_id}
                )
                if charger is None:
                    raise RuntimeError(
                        f"No existe el cargador {self.charge_point_id} "
                        f"para cerrar transaction_id {transaction_id}"
                    )

                receipt = _read_charger_cleanup_receipt(
                    charger, sesion["_id"], transaction_id
                )
                if receipt is not None:
                    receipt_status, observed_transaction_id = receipt
                    await _mark_charger_cleanup_status(
                        sesiones_col,
                        sesion,
                        transaction_id,
                        receipt_status,
                        observed_transaction_id,
                    )
                    cleanup_resolved = True
                    break

                sesion_actual = charger.get("sesion_actual")
                observed_transaction_id = (
                    sesion_actual.get("transaction_id")
                    if isinstance(sesion_actual, dict)
                    else None
                )
                receipt_value = {
                    "session_id": sesion["_id"],
                    "transaction_id": transaction_id,
                }
                receipt_absent_filter = {
                    receipt_path: {"$exists": False},
                }
                if (
                    observed_transaction_id is None
                    or observed_transaction_id == transaction_id
                ):
                    cleanup_result = await cargadores_col.update_one(
                        {
                            **charger_filter,
                            **receipt_absent_filter,
                        },
                        {"$set": {
                            "estado_general": "disponible",
                            "ultimo_estado_conocido": "Available",
                            "sesion_actual": None,
                            receipt_path: {
                                **receipt_value,
                                "status": "completed",
                            },
                        }}
                    )
                    if cleanup_result.matched_count == 1:
                        await _mark_charger_cleanup_status(
                            sesiones_col, sesion, transaction_id, "completed"
                        )
                        cleanup_resolved = True
                        break
                    continue

                skipped_result = await cargadores_col.update_one(
                    {
                        "charge_point_id": self.charge_point_id,
                        "sesion_actual.transaction_id": observed_transaction_id,
                        **receipt_absent_filter,
                    },
                    {"$set": {
                        receipt_path: {
                            **receipt_value,
                            "status": "skipped_foreign_transaction",
                            "observed_transaction_id": observed_transaction_id,
                        },
                    }}
                )
                if skipped_result.matched_count == 1:
                    await _mark_charger_cleanup_status(
                        sesiones_col,
                        sesion,
                        transaction_id,
                        "skipped_foreign_transaction",
                        observed_transaction_id,
                    )
                    cleanup_resolved = True
                    break
            if not cleanup_resolved:
                charger = await cargadores_col.find_one(
                    {"charge_point_id": self.charge_point_id}
                )
                if charger is None:
                    raise RuntimeError(
                        f"No existe el cargador {self.charge_point_id} "
                        f"para cerrar transaction_id {transaction_id}"
                    )
                receipt = _read_charger_cleanup_receipt(
                    charger, sesion["_id"], transaction_id
                )
                if receipt is not None:
                    receipt_status, observed_transaction_id = receipt
                    await _mark_charger_cleanup_status(
                        sesiones_col,
                        sesion,
                        transaction_id,
                        receipt_status,
                        observed_transaction_id,
                    )
                else:
                    raise RuntimeError(
                        f"El estado del cargador {self.charge_point_id} cambió "
                        f"repetidamente al cerrar transaction_id {transaction_id}"
                    )

        if not cierre.get("event_recorded"):
            await _persist_stop_event(
                self.charge_point_id, transaction_id, meter_stop, kwargs
            )
            await _mark_stop_closure_stage(
                sesiones_col, sesion, transaction_id, "event_recorded"
            )

        return call_result.StopTransactionPayload(
            id_tag_info={"status": "Accepted"}
        )

async def ocpp_endpoint(websocket: WebSocket, charge_point_id: str):
    reserved = False
    connection_token = None
    connection_registered = False
    logger.info("Intento de conexión OCPP recibido para %s", charge_point_id)
    try:
        charger = await get_collection("cargadores").find_one(
            {"charge_point_id": charge_point_id, "activo": True}
        )
        if charger is None:
            await _close_websocket_safely(
                websocket, charge_point_id, "Cargador no registrado o deshabilitado"
            )
            return

        credentials = parse_ocpp_basic_auth(
            websocket.headers.get("authorization")
        )
        password_hash = charger.get("ocpp_password_hash")
        if credentials is None:
            await _close_websocket_safely(
                websocket,
                charge_point_id,
                "Faltan credenciales Basic OCPP o tienen formato inválido",
            )
            return
        if credentials[0] != charge_point_id:
            await _close_websocket_safely(
                websocket,
                charge_point_id,
                "El usuario OCPP no coincide con el charge_point_id",
            )
            return
        if not password_hash:
            await _close_websocket_safely(
                websocket,
                charge_point_id,
                "No hay contraseña OCPP configurada para este cargador",
            )
            return
        if not verify_ocpp_password(credentials[1], password_hash):
            await _close_websocket_safely(
                websocket, charge_point_id, "La contraseña OCPP no coincide"
            )
            return

        if not ocpp_connection_manager.reserve_connection(charge_point_id):
            await _close_websocket_safely(
                websocket, charge_point_id, "Ya existe una conexión para este cargador"
            )
            return
        reserved = True

        # ChargeCore supports OCPP 1.6J over JSON WebSocket only.
        requested_protocols = [
            protocol.strip()
            for protocol in websocket.headers.get("sec-websocket-protocol", "").split(",")
            if protocol.strip()
        ]

        selected_subprotocol = "ocpp1.6j"
        if selected_subprotocol not in requested_protocols:
            await _close_websocket_safely(
                websocket,
                charge_point_id,
                "Subprotocolo OCPP no soportado; ofrecidos: "
                + (", ".join(requested_protocols) or "(ninguno)"),
            )
            return
        await websocket.accept(subprotocol=selected_subprotocol)
        connection_token = ocpp_connection_manager.register(charge_point_id, websocket)
        connection_registered = True
        connection_update = await get_collection("cargadores").update_one(
            {"charge_point_id": charge_point_id, "activo": True},
            {"$set": {
                "conectado": True,
                "subprotocolo_ocpp": selected_subprotocol,
                "ultima_conexion": datetime.utcnow(),
                "ultima_comunicacion": datetime.utcnow(),
                "connection_token": connection_token,
            }}
        )
        if connection_update.matched_count == 0:
            await _close_websocket_safely(
                websocket, charge_point_id, "Cargador deshabilitado"
            )
            return

        proxy = OCPPWebSocketProxy(websocket, charge_point_id, ocpp_connection_manager)
        cp = ChargePointHandler(
            charge_point_id,
            proxy,
            connection_token,
            negotiated_subprotocol=selected_subprotocol,
        )
        ocpp_connection_manager.register_handler(charge_point_id, cp)

        logger.info(
            "Cargador conectado: %s (Subprotocolo: %s)",
            charge_point_id,
            selected_subprotocol,
        )
        print(f"🔌 Cargador conectado: {charge_point_id} (Subprotocolo: {selected_subprotocol})")
        try:
            await cp.start()
        except WebSocketDisconnect:
            print(f"🔌 Cargador {charge_point_id} desconectado")
            await get_collection("cargadores").update_one(
                {"charge_point_id": charge_point_id, "connection_token": connection_token},
                {"$set": {"estado_general": "fuera_de_servicio", "conectado": False, "ultima_desconexion": datetime.utcnow()}}
            )
            await get_collection("logs").insert_one({
                "nivel": "warning",
                "modulo": "ocpp",
                "mensaje": f"Cargador {charge_point_id} desconectado",
                "timestamp": datetime.utcnow()
            })
        except Exception:
            logger.exception("Excepción en WebSocket para %s", charge_point_id)
    except Exception:
        logger.exception("Error estableciendo la conexión OCPP para %s", charge_point_id)
        await _close_websocket_safely(
            websocket, charge_point_id, "Error estableciendo conexión OCPP"
        )
        raise
    finally:
        if reserved:
            ocpp_connection_manager.release_connection_reservation(charge_point_id)
        if connection_registered:
            ocpp_connection_manager.unregister(charge_point_id, websocket)
            try:
                await get_collection("cargadores").update_one(
                    {
                        "charge_point_id": charge_point_id,
                        "connection_token": connection_token,
                    },
                    {
                        "$set": {
                            "conectado": False,
                            "ultima_desconexion": datetime.utcnow(),
                        },
                        "$unset": {"connection_token": ""},
                    },
                )
            except Exception:
                logger.exception(
                    "No se pudieron limpiar los metadatos de conexión OCPP para %s",
                    charge_point_id,
                )


async def _close_websocket_safely(
    websocket: WebSocket, charge_point_id: str, reason: str
) -> None:
    logger.warning(
        "Conexión OCPP rechazada para %s. Motivo: %s", charge_point_id, reason
    )
    try:
        await websocket.close(code=1008, reason=reason)
    except Exception:
        logger.exception(
            "No se pudo cerrar el WebSocket rechazado para %s", charge_point_id
        )