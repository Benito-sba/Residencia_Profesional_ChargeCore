import asyncio
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from ocpp.exceptions import OCPPError, ValidationError
from ocpp.v16 import call
from ocpp.v16.enums import AvailabilityType, ResetType
from starlette.websockets import WebSocketDisconnect

from app.database import get_collection
from app.config import settings
from app.websocket.ocpp_server import ocpp_connection_manager


def _status(response: Any) -> str:
    value = getattr(response, "status", None)
    if isinstance(value, str):
        return value
    enum_value = getattr(value, "value", None)
    if not isinstance(enum_value, str):
        raise ValueError("El cargador devolvio una respuesta OCPP invalida")
    return enum_value


async def _charger_exists(charge_point_id: str) -> None:
    charger = await get_collection("cargadores").find_one({"charge_point_id": charge_point_id})
    if charger is None:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")


def _handler_or_error(charge_point_id: str):
    handler = ocpp_connection_manager.get_handler(charge_point_id)
    if handler is None:
        raise HTTPException(status_code=409, detail="El cargador no esta conectado")
    return handler


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
            raise HTTPException(status_code=504, detail="Timeout esperando respuesta del cargador")
        if call_task in done:
            response = call_task.result()
        else:
            call_task.cancel()
            await asyncio.gather(call_task, return_exceptions=True)
            raise HTTPException(status_code=409, detail="El cargador se desconecto durante el comando")
        return {
            "action": action,
            "charge_point_id": charge_point_id,
            "unique_id": unique_id,
            "status": _status(response),
        }
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timeout esperando respuesta del cargador")
    except WebSocketDisconnect:
        raise HTTPException(status_code=409, detail="El cargador se desconecto durante el comando")
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


async def remote_start(charge_point_id: str, id_tag: str, connector_id: int | None = None) -> dict[str, Any]:
    await _charger_exists(charge_point_id)
    return await _call(
        charge_point_id,
        "RemoteStartTransaction",
        call.RemoteStartTransactionPayload(id_tag=id_tag, connector_id=connector_id),
    )


async def remote_stop(charge_point_id: str, transaction_id: int) -> dict[str, Any]:
    session = await get_collection("sesiones").find_one({
        "transaction_id": transaction_id,
        "estado": "activa",
    })
    if session is None:
        raise HTTPException(status_code=404, detail="Sesion activa no encontrada")
    session_charge_point_id = session.get("charge_point_id")
    if session_charge_point_id != charge_point_id:
        raise HTTPException(status_code=409, detail="La sesion no pertenece al cargador indicado")
    result = await _call(
        charge_point_id,
        "RemoteStopTransaction",
        call.RemoteStopTransactionPayload(transaction_id=transaction_id),
    )
    if result["status"] == "Rejected":
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
    return result


async def reset(charge_point_id: str, reset_type: str) -> dict[str, Any]:
    await _charger_exists(charge_point_id)
    return await _call(
        charge_point_id,
        "Reset",
        call.ResetPayload(type=ResetType(reset_type)),
    )


async def change_availability(charge_point_id: str, connector_id: int, availability_type: str) -> dict[str, Any]:
    await _charger_exists(charge_point_id)
    return await _call(
        charge_point_id,
        "ChangeAvailability",
        call.ChangeAvailabilityPayload(
            connector_id=connector_id,
            type=AvailabilityType(availability_type),
        ),
    )