import asyncio
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from ocpp.exceptions import OCPPError, ValidationError
from ocpp.v16 import call
from ocpp.v16.enums import AvailabilityType, ResetType
from ocpp.v201 import call as call_v201
from ocpp.v21 import call as call_v21
from starlette.websockets import WebSocketDisconnect

from app.database import get_collection
from app.config import settings
from app.websocket.ocpp_protocols import (
    IMPLEMENTED_OCPP_ADAPTERS,
    OCPP_PROTOCOL_ADAPTERS,
)
from app.websocket.ocpp_server import ocpp_connection_manager


def _status(response: Any) -> str:
    value = getattr(response, "status", None)
    if isinstance(value, str):
        return value
    enum_value = getattr(value, "value", None)
    if not isinstance(enum_value, str):
        raise ValueError("El cargador devolvio una respuesta OCPP invalida")
    return enum_value


async def _charger_exists(charge_point_id: str) -> dict[str, Any]:
    charger = await get_collection("cargadores").find_one({"charge_point_id": charge_point_id})
    if charger is None:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    return charger


def _handler_or_error(charge_point_id: str):
    handler = _protocol_handler(charge_point_id)
    if handler is None:
        raise HTTPException(status_code=409, detail="El cargador no esta conectado")
    return handler


def _protocol_handler(charge_point_id: str):
    handler = ocpp_connection_manager.get_handler(charge_point_id)
    if (
        handler is not None
        and OCPP_PROTOCOL_ADAPTERS.get(
            getattr(handler, "negotiated_subprotocol", None)
        )
        not in IMPLEMENTED_OCPP_ADAPTERS
    ):
        raise HTTPException(
            status_code=409,
            detail="La versión OCPP negociada no tiene un adaptador de comandos.",
        )
    return handler


def _protocol_adapter(handler: Any) -> str | None:
    return OCPP_PROTOCOL_ADAPTERS.get(
        getattr(handler, "negotiated_subprotocol", None)
    )


def _is_ocpp_2x(handler: Any) -> bool:
    return _protocol_adapter(handler) in {"v201", "v21"}


def _ocpp_2x_call_module(handler: Any):
    adapter = _protocol_adapter(handler)
    if adapter == "v201":
        return call_v201
    if adapter == "v21":
        return call_v21
    raise ValueError(f"No hay módulo OCPP 2.x para el adaptador {adapter!r}")


def _indeterminate_error(
    action: str,
    charge_point_id: str,
    unique_id: str,
) -> HTTPException:
    return HTTPException(
        status_code=504,
        detail={
            "message": "No se confirmó la respuesta del cargador; el resultado es indeterminado",
            "outcome": "indeterminate",
            "action": action,
            "charge_point_id": charge_point_id,
            "unique_id": unique_id,
        },
    )


def _has_indeterminate_outcome(error: HTTPException) -> bool:
    return (
        isinstance(error.detail, dict)
        and error.detail.get("outcome") == "indeterminate"
    )


async def _call(charge_point_id: str, action: str, payload: Any) -> dict[str, Any]:
    handler = _handler_or_error(charge_point_id)
    unique_id = str(uuid4())
    disconnect_event = ocpp_connection_manager.get_disconnect_event(charge_point_id)
    if disconnect_event is None:
        disconnect_event = asyncio.Event()
        disconnect_event.set()
    call_task = asyncio.create_task(
        handler.call(payload, suppress=False, unique_id=unique_id)
    )
    disconnect_task = asyncio.create_task(disconnect_event.wait())
    try:
        done, _ = await asyncio.wait(
            {call_task, disconnect_task},
            timeout=settings.OCPP_COMMAND_TIMEOUT_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            call_task.cancel()
            await asyncio.gather(call_task, return_exceptions=True)
            raise _indeterminate_error(action, charge_point_id, unique_id)
        if call_task in done:
            response = call_task.result()
        else:
            call_task.cancel()
            await asyncio.gather(call_task, return_exceptions=True)
            raise _indeterminate_error(action, charge_point_id, unique_id)
        return {
            "action": action,
            "charge_point_id": charge_point_id,
            "unique_id": unique_id,
            "status": _status(response),
        }
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise _indeterminate_error(action, charge_point_id, unique_id)
    except WebSocketDisconnect:
        raise _indeterminate_error(action, charge_point_id, unique_id)
    except OCPPError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "El cargador devolvio CALLERROR",
                "error_code": getattr(error, "code", "GenericError"),
                "description": error.description,
                "details": error.details,
                "unique_id": unique_id,
            },
        )
    except (ValidationError, ValueError, AttributeError) as error:
        raise HTTPException(status_code=502, detail=f"Respuesta OCPP invalida: {error}")
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Error ejecutando {action}: {error}")
    finally:
        if not call_task.done():
            call_task.cancel()
        await asyncio.gather(call_task, return_exceptions=True)
        if not disconnect_task.done():
            disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)


async def remote_start(
    charge_point_id: str,
    id_tag: str,
    connector_id: int | None = None,
    request_id: Any | None = None,
) -> dict[str, Any]:
    charger = await _charger_exists(charge_point_id)
    handler = _protocol_handler(charge_point_id)
    command_id = uuid4().hex
    chargers = get_collection("cargadores")
    requested_at = datetime.utcnow()
    start_lock = {
        "command_id": command_id,
        "action": "RemoteStartTransaction",
        "charge_point_id": charge_point_id,
        "empresa_id": charger.get("empresa_id"),
        "request_id": request_id,
        "id_tag": id_tag,
        "connector_id": connector_id,
        "requested_at": requested_at,
        "status": "pending",
    }
    claim = await chargers.update_one(
        {
            "charge_point_id": charge_point_id,
            "empresa_id": charger.get("empresa_id"),
            "pending_remote_start": {"$exists": False},
            "remote_start_lock": {"$exists": False},
        },
        {"$set": {
            "pending_remote_start": {
                **start_lock,
            },
            "remote_start_lock": {
                **start_lock,
            },
        }},
    )
    if claim.matched_count != 1:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Ya existe una orden de inicio sin reconciliar para este cargador",
                "outcome": "indeterminate",
                "action": "RemoteStartTransaction",
                "charge_point_id": charge_point_id,
            },
        )

    try:
        if _is_ocpp_2x(handler):
            payload = _ocpp_2x_call_module(handler).RequestStartTransaction(
                id_token={"id_token": id_tag, "type": "Central"},
                remote_start_id=(uuid4().int % 2_147_483_647) + 1,
                evse_id=connector_id if connector_id else None,
            )
            action = "RequestStartTransaction"
        else:
            payload = call.RemoteStartTransaction(
                id_tag=id_tag,
                connector_id=connector_id,
            )
            action = "RemoteStartTransaction"
        result = await _call(
            charge_point_id,
            action,
            payload,
        )
    except HTTPException as error:
        if _has_indeterminate_outcome(error):
            await chargers.update_one(
                {
                    "charge_point_id": charge_point_id,
                    "empresa_id": charger.get("empresa_id"),
                    "pending_remote_start.command_id": command_id,
                    "remote_start_lock.command_id": command_id,
                },
                {"$set": {
                    "pending_remote_start.status": "indeterminate",
                    "remote_start_lock.status": "indeterminate",
                }},
            )
        else:
            await chargers.update_one(
                {
                    "charge_point_id": charge_point_id,
                    "empresa_id": charger.get("empresa_id"),
                    "pending_remote_start.command_id": command_id,
                    "remote_start_lock.command_id": command_id,
                },
                {"$unset": {
                    "pending_remote_start": "",
                    "remote_start_lock": "",
                }},
            )
        raise

    if result["status"] in {"Accepted", "Scheduled"}:
        await chargers.update_one(
            {
                "charge_point_id": charge_point_id,
                "empresa_id": charger.get("empresa_id"),
                "pending_remote_start.command_id": command_id,
                "remote_start_lock.command_id": command_id,
            },
            {"$set": {
                "pending_remote_start.status": "accepted",
                "remote_start_lock.status": "accepted",
            }},
        )
    else:
        await chargers.update_one(
            {
                "charge_point_id": charge_point_id,
                "empresa_id": charger.get("empresa_id"),
                "pending_remote_start.command_id": command_id,
                "remote_start_lock.command_id": command_id,
            },
            {"$unset": {
                "pending_remote_start": "",
                "remote_start_lock": "",
            }},
        )
    return result


async def _limited_documents(collection, query, limit: int) -> list[dict[str, Any]]:
    cursor = collection.find(query).limit(limit)
    return [document async for document in cursor]


async def _persist_start_lock(
    charger: dict[str, Any],
    pending_start: dict[str, Any],
) -> None:
    command_id = pending_start.get("command_id")
    if command_id is None:
        raise HTTPException(
            status_code=409,
            detail="No se puede conservar el bloqueo de inicio sin identidad de comando",
        )
    lock = {
        **pending_start,
        "action": "RemoteStartTransaction",
        "charge_point_id": charger["charge_point_id"],
        "empresa_id": charger.get("empresa_id"),
        "status": "indeterminate",
    }
    collection = get_collection("cargadores")
    identity = {
        "_id": charger["_id"],
        "charge_point_id": charger["charge_point_id"],
        "empresa_id": charger.get("empresa_id"),
    }
    update = await collection.update_one(
        {
            **identity,
            "$or": [
                {"remote_start_lock": {"$exists": False}},
                {"remote_start_lock.command_id": command_id},
            ],
        },
        {"$set": {"remote_start_lock": lock}},
    )
    if update.matched_count == 1:
        return
    current = await collection.find_one(identity)
    if (
        current is None
        or (current.get("remote_start_lock") or {}).get("command_id")
        != command_id
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "No se pudo conservar el bloqueo de inicio sin modificar "
                "otro comando"
            ),
        )


async def _persist_stop_lock(
    charger: dict[str, Any],
    session: dict[str, Any],
    command: dict[str, Any],
) -> None:
    command_id = command.get("command_id")
    if command_id is None:
        raise HTTPException(
            status_code=409,
            detail="No se puede conservar el bloqueo de detención sin identidad de comando",
        )
    lock = {
        **command,
        "action": "RemoteStopTransaction",
        "charge_point_id": charger["charge_point_id"],
        "empresa_id": charger.get("empresa_id"),
        "session_id": session["_id"],
        "transaction_id": session.get("transaction_id"),
        "status": "indeterminate",
    }
    collection = get_collection("sesiones")
    identity = {
        "_id": session["_id"],
        "charge_point_id": charger["charge_point_id"],
        "empresa_id": charger.get("empresa_id"),
        "transaction_id": session.get("transaction_id"),
    }
    update = await collection.update_one(
        {
            **identity,
            "$or": [
                {"remote_stop_lock": {"$exists": False}},
                {"remote_stop_lock.command_id": command_id},
            ],
        },
        {"$set": {"remote_stop_lock": lock}},
    )
    if update.matched_count == 1:
        return
    current = await collection.find_one(identity)
    if (
        current is None
        or (current.get("remote_stop_lock") or {}).get("command_id")
        != command_id
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "No se pudo conservar el bloqueo de detención sin modificar "
                "otro comando"
            ),
        )


async def _record_reconciliation(
    *,
    charger: dict[str, Any],
    actor_id: Any,
    command_id: str | None,
    action: str,
    status: str,
    evidence: str | None,
    transaction_id: int | None = None,
) -> None:
    await get_collection("logs").insert_one({
        "_id": f"command-reconciliation:{uuid4().hex}",
        "empresa_id": charger.get("empresa_id"),
        "charge_point_id": charger["charge_point_id"],
        "nivel": "info" if status == "confirmed" else "warning",
        "modulo": "ocpp_command_reconciliation",
        "mensaje": f"Reconciliacion de {action}: {status}",
        "datos": {
            "command_id": command_id,
            "action": action,
            "status": status,
            "evidence": evidence,
            "transaction_id": transaction_id,
            "actor_id": str(actor_id),
        },
        "timestamp": datetime.utcnow(),
    })


async def _matching_started_sessions(
    charge_point_id: str,
    company_id: Any,
    pending_start: dict[str, Any],
) -> list[dict[str, Any]]:
    requested_at = pending_start.get("requested_at")
    id_tag = pending_start.get("id_tag")
    if not requested_at or not id_tag or company_id is None:
        return []

    requests = await _limited_documents(
        get_collection("nfc_charge_requests"),
        {
            **({
                "_id": pending_start["request_id"],
            } if pending_start.get("request_id") is not None else {}),
            "charge_point_id": charge_point_id,
            "empresa_id": company_id,
            "uid": id_tag,
            "status": "transaction_started",
            "started_at": {"$gte": requested_at},
        },
        3,
    )
    matches = []
    for request in requests:
        if (
            request.get("started_at") is None
            or request.get("vehicle_id") is None
            or request.get("connector_id") is None
            or request.get("usuario_id") is None
        ):
            continue
        card = await get_collection("nfc_cards").find_one({
            "uid": request.get("uid"),
            "usuario_id": request.get("usuario_id"),
            "empresa_id": company_id,
        })
        if card is None or card.get("_id") is None:
            continue
        sessions = await _limited_documents(
            get_collection("sesiones"),
            {
                "charge_point_id": charge_point_id,
                "empresa_id": company_id,
                "usuario_id": request["usuario_id"],
                "vehiculo_id": request["vehicle_id"],
                "nfc_card_id": card["_id"],
                "connector_id": request["connector_id"],
                "estado": {"$in": ["activa", "iniciada", "finalizada"]},
                "fecha_inicio": {"$gte": request["started_at"]},
            },
            3,
        )
        matches.extend(sessions)
    return matches


def _confirmed_start_evidence(
    charger: dict[str, Any] | None,
    command_id: str,
    session: dict[str, Any],
) -> str | None:
    if charger is None:
        return None
    pending = charger.get("pending_remote_start") or {}
    lock = charger.get("remote_start_lock") or {}
    if (
        pending
        or lock and (
            lock.get("command_id") != command_id
            or lock.get("action") != "RemoteStartTransaction"
            or lock.get("charge_point_id") != charger.get("charge_point_id")
            or lock.get("empresa_id") != charger.get("empresa_id")
        )
    ):
        return None
    resolution = charger.get("last_remote_command_reconciliation") or {}
    if (
        resolution.get("command_id") != command_id
        or resolution.get("action") != "RemoteStartTransaction"
        or resolution.get("status") != "confirmed"
        or resolution.get("transaction_id") != session.get("transaction_id")
        or resolution.get("evidence")
        not in {"matching_start_transaction", "matching_started_session"}
    ):
        return None
    return resolution["evidence"]


def _confirmed_stop_evidence(
    session: dict[str, Any] | None,
    *,
    charge_point_id: str,
    company_id: Any,
    transaction_id: Any,
    command_id: str,
    requested_at: datetime,
) -> str | None:
    if (
        session is None
        or session.get("charge_point_id") != charge_point_id
        or session.get("empresa_id") != company_id
        or session.get("transaction_id") != transaction_id
        or session.get("estado") != "finalizada"
        or session.get("fecha_fin") is None
        or session["fecha_fin"] < requested_at
    ):
        return None
    lock = session.get("remote_stop_lock") or {}
    if lock and (
        lock.get("command_id") != command_id
        or lock.get("charge_point_id") != charge_point_id
        or lock.get("empresa_id") != company_id
        or lock.get("session_id") != session.get("_id")
        or lock.get("transaction_id") != transaction_id
    ):
        return None
    command = session.get("remote_stop_command") or {}
    if (
        command.get("command_id") != command_id
        or command.get("requested_at") != requested_at
        or command.get("status") != "confirmed"
    ):
        return None
    evidence = command.get("evidence")
    if evidence not in {
        "matching_stop_transaction",
        "session_finalized_after_command",
    }:
        return None
    return evidence


async def reconcile_remote_commands(
    charger: dict[str, Any],
    actor_id: Any,
) -> dict[str, Any]:
    """Resolve command locks only when a correlated backend session proves the outcome."""
    charge_point_id = charger["charge_point_id"]
    company_id = charger.get("empresa_id")
    results = []
    pending_start = (
        charger.get("remote_start_lock")
        or charger.get("pending_remote_start")
    )

    if pending_start:
        command_id = pending_start.get("command_id")
        matches = await _matching_started_sessions(
            charge_point_id,
            company_id,
            pending_start,
        )

        if len(matches) == 1:
            session = matches[0]
            reconciliation = {
                "command_id": command_id,
                "action": "RemoteStartTransaction",
                "status": "confirmed",
                "evidence": "matching_started_session",
                "transaction_id": session.get("transaction_id"),
                "reconciled_at": datetime.utcnow(),
                "reconciled_by": actor_id,
            }
            start_filter = {
                "_id": charger["_id"],
                "charge_point_id": charge_point_id,
                "empresa_id": company_id,
                "pending_remote_start.command_id": command_id,
                "$or": [
                    {"remote_start_lock.command_id": command_id},
                    {"remote_start_lock": {"$exists": False}},
                ],
            }
            updated = await get_collection("cargadores").update_one(
                start_filter,
                {
                    "$unset": {
                        "pending_remote_start": "",
                        "remote_start_lock": "",
                    },
                    "$set": {
                        "last_remote_command_reconciliation": reconciliation,
                    },
                },
            )
            resolved = updated.matched_count == 1
            evidence = "matching_started_session" if resolved else None
            current_session = session
            lock_retained = False
            if not resolved:
                current_charger = await get_collection("cargadores").find_one({
                    "_id": charger["_id"],
                    "charge_point_id": charge_point_id,
                    "empresa_id": company_id,
                })
                current_matches = await _matching_started_sessions(
                    charge_point_id,
                    company_id,
                    pending_start,
                )
                if (
                    len(current_matches) == 1
                    and current_matches[0].get("transaction_id")
                    == session.get("transaction_id")
                ):
                    persisted_evidence = _confirmed_start_evidence(
                        current_charger,
                        command_id,
                        current_matches[0],
                    )
                    if persisted_evidence is not None and current_charger is not None:
                        current_lock = (
                            current_charger.get("remote_start_lock") or {}
                        )
                        if current_lock.get("command_id") == command_id:
                            release = await get_collection("cargadores").update_one(
                                {
                                    "_id": charger["_id"],
                                    "charge_point_id": charge_point_id,
                                    "empresa_id": company_id,
                                    "remote_start_lock.command_id": command_id,
                                    "last_remote_command_reconciliation.command_id": command_id,
                                    "last_remote_command_reconciliation.action": (
                                        "RemoteStartTransaction"
                                    ),
                                    "last_remote_command_reconciliation.status": "confirmed",
                                    "last_remote_command_reconciliation.transaction_id": (
                                        current_matches[0].get("transaction_id")
                                    ),
                                    "$or": [
                                        {
                                            "pending_remote_start.command_id":
                                                command_id,
                                        },
                                        {
                                            "pending_remote_start": {
                                                "$exists": False,
                                            },
                                        },
                                    ],
                                },
                                {"$unset": {
                                    "pending_remote_start": "",
                                    "remote_start_lock": "",
                                }},
                            )
                            resolved = release.matched_count == 1
                            if not resolved:
                                current_charger = await get_collection(
                                    "cargadores"
                                ).find_one({
                                    "_id": charger["_id"],
                                    "charge_point_id": charge_point_id,
                                    "empresa_id": company_id,
                                })
                                resolved = (
                                    current_charger is not None
                                    and not current_charger.get("remote_start_lock")
                                    and not current_charger.get("pending_remote_start")
                                    and _confirmed_start_evidence(
                                        current_charger,
                                        command_id,
                                        current_matches[0],
                                    ) is not None
                                )
                        else:
                            resolved = (
                                current_lock == {}
                                and not current_charger.get("pending_remote_start")
                            )
                        if resolved:
                            evidence = persisted_evidence
                            current_session = current_matches[0]
                if not resolved:
                    await _persist_start_lock(
                        current_charger or charger,
                        pending_start,
                    )
                    lock_retained = True
            status = "confirmed" if resolved else "unknown"
            results.append({
                "action": "RemoteStartTransaction",
                "status": status,
                "lock_retained": lock_retained,
                "evidence": evidence,
                "transaction_id": (
                    current_session.get("transaction_id") if resolved else None
                ),
                "session_status": current_session.get("estado"),
            })
        else:
            await _persist_start_lock(charger, pending_start)
            results.append({
                "action": "RemoteStartTransaction",
                "status": "unknown",
                "lock_retained": True,
                "evidence": None,
                "session_status": "ambiguous" if matches else "not_found",
            })
            status = "unknown"
            evidence = None
        await _record_reconciliation(
            charger=charger,
            actor_id=actor_id,
            command_id=command_id,
            action="RemoteStartTransaction",
            status=results[-1]["status"],
            evidence=results[-1]["evidence"],
            transaction_id=results[-1].get("transaction_id"),
        )

    pending_stops = []
    if company_id is not None:
        pending_stops = await _limited_documents(
            get_collection("sesiones"),
            {
                "charge_point_id": charge_point_id,
                "empresa_id": company_id,
                "$or": [
                    {"remote_stop_lock.command_id": {"$exists": True}},
                    {"remote_stop_command.status": {
                        "$in": ["pending", "accepted", "indeterminate"],
                    }},
                ],
            },
            100,
        )
    for session in pending_stops:
        stop_lock = session.get("remote_stop_lock") or {}
        command = stop_lock or session.get("remote_stop_command") or {}
        command_id = command.get("command_id")
        requested_at = command.get("requested_at")
        finalized_at = session.get("fecha_fin")
        transaction_id = session.get("transaction_id")
        lock_identity_matches = (
            not stop_lock
            or (
                stop_lock.get("command_id") == command_id
                and stop_lock.get("charge_point_id") == charge_point_id
                and stop_lock.get("empresa_id") == company_id
                and stop_lock.get("session_id") == session.get("_id")
                and stop_lock.get("transaction_id") == transaction_id
            )
        )
        confirmed = (
            session.get("estado") == "finalizada"
            and requested_at is not None
            and finalized_at is not None
            and finalized_at >= requested_at
            and command_id is not None
            and transaction_id is not None
            and lock_identity_matches
        )
        evidence = None
        current_session = None
        if confirmed:
            reconciliation = {
                "status": "confirmed",
                "evidence": "session_finalized_after_command",
                "reconciled_at": datetime.utcnow(),
                "reconciled_by": actor_id,
            }
            stop_filter = {
                "_id": session["_id"],
                "charge_point_id": charge_point_id,
                "empresa_id": company_id,
                "transaction_id": transaction_id,
                "estado": "finalizada",
                "fecha_fin": {"$gte": requested_at},
            }
            if stop_lock:
                stop_filter["remote_stop_lock.command_id"] = command_id
                stop_filter["$or"] = [
                    {"remote_stop_command.command_id": command_id},
                    {"remote_stop_command": {"$exists": False}},
                ]
            else:
                stop_filter.update({
                    "remote_stop_command.command_id": command_id,
                    "remote_stop_command.status": {
                        "$in": ["pending", "accepted", "indeterminate"],
                    },
                })
            updated = await get_collection("sesiones").update_one(
                stop_filter,
                {
                    "$set": {
                        "remote_stop_command": {
                            **command,
                            "status": reconciliation["status"],
                            "evidence": reconciliation["evidence"],
                            "reconciled_at": reconciliation["reconciled_at"],
                            "reconciled_by": reconciliation["reconciled_by"],
                        },
                    },
                    "$unset": {"remote_stop_lock": ""},
                },
            )
            if updated.matched_count == 1:
                evidence = "session_finalized_after_command"
                current_session = await get_collection("sesiones").find_one({
                    "_id": session["_id"],
                    "charge_point_id": charge_point_id,
                    "empresa_id": company_id,
                    "transaction_id": transaction_id,
                })
            else:
                current_session = await get_collection("sesiones").find_one({
                    "_id": session["_id"],
                    "charge_point_id": charge_point_id,
                    "empresa_id": company_id,
                    "transaction_id": transaction_id,
                })
                evidence = _confirmed_stop_evidence(
                    current_session,
                    charge_point_id=charge_point_id,
                    company_id=company_id,
                    transaction_id=transaction_id,
                    command_id=command_id,
                    requested_at=requested_at,
                )
                confirmed = evidence is not None
        else:
            current_session = await get_collection("sesiones").find_one({
                "_id": session["_id"],
                "charge_point_id": charge_point_id,
                "empresa_id": company_id,
                "transaction_id": transaction_id,
            })
            evidence = _confirmed_stop_evidence(
                current_session,
                charge_point_id=charge_point_id,
                company_id=company_id,
                transaction_id=transaction_id,
                command_id=command_id,
                requested_at=requested_at,
            ) if command_id and requested_at and transaction_id is not None else None
            confirmed = evidence is not None
        if confirmed and current_session is not None:
            current_lock = current_session.get("remote_stop_lock") or {}
            if current_lock.get("command_id") == command_id:
                release = await get_collection("sesiones").update_one(
                    {
                        "_id": session["_id"],
                        "charge_point_id": charge_point_id,
                        "empresa_id": company_id,
                        "transaction_id": transaction_id,
                        "remote_stop_lock.command_id": command_id,
                        "remote_stop_command.command_id": command_id,
                        "remote_stop_command.status": "confirmed",
                        "remote_stop_command.evidence": evidence,
                    },
                    {"$unset": {"remote_stop_lock": ""}},
                )
                if release.matched_count != 1:
                    current_session = await get_collection("sesiones").find_one({
                        "_id": session["_id"],
                        "charge_point_id": charge_point_id,
                        "empresa_id": company_id,
                        "transaction_id": transaction_id,
                    })
                confirmed = (
                    current_session is not None
                    and not current_session.get("remote_stop_lock")
                    and _confirmed_stop_evidence(
                        current_session,
                        charge_point_id=charge_point_id,
                        company_id=company_id,
                        transaction_id=transaction_id,
                        command_id=command_id,
                        requested_at=requested_at,
                    ) is not None
                )
        if not confirmed:
            await _persist_stop_lock(charger, session, command)
        status = "confirmed" if confirmed else "unknown"
        lock_retained = not confirmed
        results.append({
            "action": "RemoteStopTransaction",
            "transaction_id": transaction_id,
            "session_status": (
                current_session.get("estado") if current_session else session.get("estado")
            ),
            "status": status,
            "lock_retained": lock_retained,
            "evidence": evidence,
        })
        await _record_reconciliation(
            charger=charger,
            actor_id=actor_id,
            command_id=command_id,
            action="RemoteStopTransaction",
            status=status,
            evidence=evidence,
            transaction_id=transaction_id,
        )

    return {
        "charge_point_id": charge_point_id,
        "connected": bool(charger.get("conectado")),
        "charger_status": charger.get("estado_general"),
        "commands": results,
    }


async def remote_stop(charge_point_id: str, transaction_id: int) -> dict[str, Any]:
    sessions = get_collection("sesiones")
    session = await sessions.find_one({
        "transaction_id": transaction_id,
        "estado": {"$in": ["activa", "iniciada"]},
    })
    if session is None:
        raise HTTPException(status_code=404, detail="Sesion activa no encontrada")
    session_charge_point_id = session.get("charge_point_id")
    if session_charge_point_id != charge_point_id:
        raise HTTPException(status_code=409, detail="La sesion no pertenece al cargador indicado")
    handler = _protocol_handler(charge_point_id)
    if _is_ocpp_2x(handler):
        ocpp_transaction_id = session.get("ocpp_transaction_id")
        if not isinstance(ocpp_transaction_id, str) or not ocpp_transaction_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    "La sesión no tiene un identificador de transacción OCPP 2.0.1; "
                    "no se puede enviar RequestStopTransaction."
                ),
            )
        action = "RequestStopTransaction"
        payload = _ocpp_2x_call_module(handler).RequestStopTransaction(
            transaction_id=ocpp_transaction_id
        )
    else:
        action = "RemoteStopTransaction"
        payload = call.RemoteStopTransaction(
            transaction_id=transaction_id
        )
    command_id = uuid4().hex
    requested_at = datetime.utcnow()
    stop_lock = {
        "command_id": command_id,
        "action": action,
        "charge_point_id": charge_point_id,
        "empresa_id": session.get("empresa_id"),
        "session_id": session["_id"],
        "transaction_id": transaction_id,
        "requested_at": requested_at,
        "status": "pending",
    }
    claim = await sessions.update_one(
        {
            "_id": session["_id"],
            "transaction_id": transaction_id,
            "charge_point_id": charge_point_id,
            "empresa_id": session.get("empresa_id"),
            "estado": {"$in": ["activa", "iniciada"]},
            "remote_stop_command": {"$exists": False},
            "remote_stop_lock": {"$exists": False},
        },
        {"$set": {
            "remote_stop_command": {
                **stop_lock,
            },
            "remote_stop_lock": {
                **stop_lock,
            },
        }},
    )
    if claim.matched_count != 1:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Ya existe una orden de detención sin reconciliar para esta sesión",
                "outcome": "indeterminate",
                "action": "RemoteStopTransaction",
                "charge_point_id": charge_point_id,
                "transaction_id": transaction_id,
            },
        )

    try:
        result = await _call(
            charge_point_id,
            action,
            payload,
        )
    except HTTPException as error:
        if _has_indeterminate_outcome(error):
            await sessions.update_one(
                {
                    "_id": session["_id"],
                    "charge_point_id": charge_point_id,
                    "empresa_id": session.get("empresa_id"),
                    "transaction_id": transaction_id,
                    "remote_stop_command.command_id": command_id,
                    "remote_stop_lock.command_id": command_id,
                },
                {"$set": {
                    "remote_stop_command.status": "indeterminate",
                    "remote_stop_lock.status": "indeterminate",
                }},
            )
        else:
            await sessions.update_one(
                {
                    "_id": session["_id"],
                    "charge_point_id": charge_point_id,
                    "empresa_id": session.get("empresa_id"),
                    "transaction_id": transaction_id,
                    "remote_stop_command.command_id": command_id,
                    "remote_stop_lock.command_id": command_id,
                },
                {"$unset": {
                    "remote_stop_command": "",
                    "remote_stop_lock": "",
                }},
            )
        raise
    if result["status"] == "Rejected":
        await sessions.update_one(
            {
                "_id": session["_id"],
                "charge_point_id": charge_point_id,
                "empresa_id": session.get("empresa_id"),
                "transaction_id": transaction_id,
                "remote_stop_command.command_id": command_id,
                "remote_stop_lock.command_id": command_id,
            },
            {"$unset": {
                "remote_stop_command": "",
                "remote_stop_lock": "",
            }},
        )
        raise HTTPException(
            status_code=409,
            detail={
                "message": "El cargador rechazo RemoteStopTransaction",
                "action": "RemoteStopTransaction",
                "charge_point_id": charge_point_id,
                "transaction_id": transaction_id,
                "status": "Rejected",
            },
        )
    await sessions.update_one(
        {
            "_id": session["_id"],
            "charge_point_id": charge_point_id,
            "empresa_id": session.get("empresa_id"),
            "transaction_id": transaction_id,
            "remote_stop_command.command_id": command_id,
            "remote_stop_lock.command_id": command_id,
            "remote_stop_command.status": {
                "$in": ["pending", "accepted", "indeterminate"],
            },
        },
        {"$set": {
            "remote_stop_command.status": "accepted",
            "remote_stop_lock.status": "accepted",
        }},
    )
    return result


async def reset(charge_point_id: str, reset_type: str) -> dict[str, Any]:
    await _charger_exists(charge_point_id)
    handler = _protocol_handler(charge_point_id)
    if _is_ocpp_2x(handler):
        reset_mapping = {"Soft": "OnIdle", "Hard": "Immediate"}
        return await _call(
            charge_point_id,
            "Reset",
            _ocpp_2x_call_module(handler).Reset(type=reset_mapping[reset_type]),
        )
    return await _call(
        charge_point_id,
        "Reset",
        call.Reset(type=ResetType(reset_type)),
    )


async def change_availability(charge_point_id: str, connector_id: int, availability_type: str) -> dict[str, Any]:
    await _charger_exists(charge_point_id)
    handler = _protocol_handler(charge_point_id)
    if _is_ocpp_2x(handler):
        return await _call(
            charge_point_id,
            "ChangeAvailability",
            _ocpp_2x_call_module(handler).ChangeAvailability(
                operational_status=availability_type,
                evse={"id": connector_id} if connector_id else None,
            ),
        )
    return await _call(
        charge_point_id,
        "ChangeAvailability",
        call.ChangeAvailability(
            connector_id=connector_id,
            type=AvailabilityType(availability_type),
        ),
    )