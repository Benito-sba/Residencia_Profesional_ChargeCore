import logging
import math
from datetime import datetime
from typing import Any

from ocpp.routing import on
from ocpp.exceptions import InternalError
from ocpp.v201 import call_result
from ocpp.v201.enums import Action
from ocpp.v201 import ChargePoint as OCPP201ChargePoint

from app.database import get_collection

logger = logging.getLogger(__name__)
STATUS_TO_INTERNAL = {
    "Available": "Available",
    "Occupied": "Occupied",
    "Reserved": "Reserved",
    "Unavailable": "Unavailable",
    "Faulted": "Faulted",
}


def _status(response: Any) -> str:
    value = getattr(response, "status", None)
    if isinstance(value, str):
        return value
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    raise ValueError("El manejador OCPP 1.6 devolvió un estado de Boot inválido")


def _meter_values_to_v16(meter_values: list[dict]) -> list[dict]:
    converted = []
    for meter in meter_values:
        if not isinstance(meter, dict):
            raise ValueError("TransactionEvent meter_value entries must be objects")
        sampled_values = meter.get("sampled_value", [])
        if not isinstance(sampled_values, list):
            raise ValueError("MeterValue sampled_value must be a list")
        converted_samples = []
        for sample in sampled_values:
            if not isinstance(sample, dict):
                raise ValueError("MeterValue samples must be objects")
            unit_of_measure = sample.get("unit_of_measure", {})
            if not isinstance(unit_of_measure, dict):
                raise ValueError("unit_of_measure must be an object")
            sample_value = dict(sample)
            sample_value["unit"] = unit_of_measure.get("unit", "Wh")
            multiplier = unit_of_measure.get("multiplier", 0)
            if multiplier:
                try:
                    scaled_value = float(sample["value"]) * (10 ** int(multiplier))
                except (KeyError, TypeError, ValueError, OverflowError) as error:
                    raise ValueError("MeterValue multiplier or value is invalid") from error
                if not math.isfinite(scaled_value):
                    raise ValueError("MeterValue multiplier produced a non-finite value")
                sample_value["value"] = str(scaled_value)
            sample_value.pop("unit_of_measure", None)
            converted_samples.append(sample_value)
        converted.append({
            "timestamp": meter.get("timestamp"),
            "sampled_value": converted_samples,
        })
    return converted


def _energy_register_wh(meter_values: list[dict]) -> float | None:
    unit_to_wh = {"Wh": 1, "kWh": 1000, "MWh": 1_000_000}
    for meter in reversed(meter_values):
        for sample in reversed(meter.get("sampled_value", [])):
            if sample.get("measurand") != "Energy.Active.Import.Register":
                continue
            unit_of_measure = sample.get("unit_of_measure", {})
            if not isinstance(unit_of_measure, dict):
                raise ValueError("Energy register unit_of_measure must be an object")
            unit = unit_of_measure.get("unit", "Wh")
            if unit not in unit_to_wh:
                raise ValueError(f"Unsupported energy register unit: {unit}")
            try:
                multiplier = int(unit_of_measure.get("multiplier", 0))
                energy_wh = (
                    float(sample["value"])
                    * (10 ** multiplier)
                    * unit_to_wh[unit]
                )
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                raise ValueError("Energy register value is invalid") from error
            if not math.isfinite(energy_wh):
                raise ValueError("Energy register value must be finite")
            return energy_wh
    return None


def _stop_closure_is_complete(closure: dict) -> bool:
    return (
        closure.get("rewards_applied") is True
        and closure.get("rewards_granted") is True
        and closure.get("event_recorded") is True
        and closure.get("charger_cleanup_status")
        in ("completed", "skipped_foreign_transaction")
    )


class OCPP2xChargePointHandler:
    call_result_module = call_result
    negotiated_subprotocol = ""

    def __init__(
        self,
        charge_point_id: str,
        websocket: Any,
        connection_token: str | None,
        legacy_handler: Any,
    ):
        super().__init__(charge_point_id, websocket)
        self.charge_point_id = charge_point_id
        self.connection_token = connection_token
        self.legacy_handler = legacy_handler

    @on(Action.boot_notification)
    async def on_boot_notification(self, charging_station, reason, **kwargs):
        response = await self.legacy_handler.on_boot_notification(
            charge_point_vendor=charging_station.get("vendor_name", "Unknown"),
            charge_point_model=charging_station.get("model", "Unknown"),
            firmware_version=charging_station.get("firmware_version"),
            reason=reason,
        )
        return self.call_result_module.BootNotification(
            current_time=response.current_time,
            interval=response.interval,
            status=_status(response).capitalize(),
        )

    @on(Action.heartbeat)
    async def on_heartbeat(self, **kwargs):
        response = await self.legacy_handler.on_heartbeat()
        return self.call_result_module.Heartbeat(current_time=response.current_time)

    @on(Action.authorize)
    async def on_authorize(self, id_token, **kwargs):
        response = await self.legacy_handler.on_authorize(
            id_tag=id_token.get("id_token", "")
        )
        id_tag_info = getattr(response, "id_tag_info", None)
        if not isinstance(id_tag_info, dict) or not isinstance(
            id_tag_info.get("status"), str
        ):
            raise ValueError("El manejador OCPP 1.6 devolvió una autorización inválida")
        return self.call_result_module.Authorize(
            id_token_info={"status": id_tag_info["status"]}
        )

    @on(Action.status_notification)
    async def on_status_notification(
        self,
        timestamp,
        connector_status,
        evse_id,
        connector_id,
        **kwargs,
    ):
        status = STATUS_TO_INTERNAL.get(connector_status)
        if status is None:
            raise ValueError(
                f"Estado OCPP no compatible con el modelo interno: "
                f"{connector_status}"
            )
        await self.legacy_handler.on_status_notification(
            connector_id=connector_id,
            status=status,
            error_code=None,
            evse_id=evse_id,
            ocpp_status=connector_status,
            timestamp=timestamp,
        )
        return self.call_result_module.StatusNotification()

    @on(Action.meter_values)
    async def on_meter_values(self, evse_id, meter_value, **kwargs):
        session = await get_collection("sesiones").find_one({
            "charge_point_id": self.charge_point_id,
            "estado": "activa",
            "evse_id": evse_id,
        })
        if session is None:
            logger.warning(
                "MeterValues 2.0.1 recibido sin sesión activa para %s EVSE %s",
                self.charge_point_id,
                evse_id,
            )
            return self.call_result_module.MeterValues()
        await self.legacy_handler.on_meter_values(
            connector_id=session["connector_id"],
            transaction_id=session["transaction_id"],
            meter_value=_meter_values_to_v16(meter_value),
        )
        return self.call_result_module.MeterValues()

    @on(Action.transaction_event)
    async def on_transaction_event(
        self,
        event_type,
        timestamp,
        trigger_reason,
        seq_no,
        transaction_info,
        meter_value=None,
        evse=None,
        id_token=None,
        **kwargs,
    ):
        transaction_id = transaction_info.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            raise ValueError("TransactionEvent transaction_id must be a non-empty string")

        values = _meter_values_to_v16(meter_value or [])
        if event_type == "Started":
            if id_token is None:
                return self.call_result_module.TransactionEvent(
                    id_token_info={"status": "Invalid"}
                )
            connector_id = (evse or {}).get("connector_id", 1)
            evse_id = (evse or {}).get("id", connector_id)
            meter_start = _energy_register_wh(meter_value or []) or 0
            response = await self.legacy_handler.on_start_transaction(
                connector_id=connector_id,
                id_tag=id_token.get("id_token", ""),
                meter_start=meter_start,
                timestamp=timestamp,
            )
            id_tag_info = getattr(response, "id_tag_info", None)
            if not isinstance(id_tag_info, dict) or not isinstance(
                id_tag_info.get("status"), str
            ):
                raise ValueError("El manejador OCPP 1.6 devolvió un inicio inválido")
            status = id_tag_info["status"]
            internal_transaction_id = getattr(response, "transaction_id", 0)
            if status == "Accepted" and internal_transaction_id:
                update = await get_collection("sesiones").update_one(
                    {
                        "charge_point_id": self.charge_point_id,
                        "transaction_id": internal_transaction_id,
                        "estado": "activa",
                    },
                    {"$set": {
                        "ocpp_transaction_id": transaction_id,
                        "evse_id": evse_id,
                        "subprotocolo_ocpp": self.negotiated_subprotocol,
                    }},
                )
                if update.matched_count == 0:
                    raise RuntimeError(
                        "No se encontró la sesión creada para guardar el ID OCPP 2.0.1"
                    )
                if values:
                    await self.legacy_handler.on_meter_values(
                        connector_id=connector_id,
                        transaction_id=internal_transaction_id,
                        meter_value=values,
                    )
            return self.call_result_module.TransactionEvent(
                id_token_info={"status": status}
            )

        session_query = {
            "charge_point_id": self.charge_point_id,
            "ocpp_transaction_id": transaction_id,
        }
        session_cursor = get_collection("sesiones").find(session_query).limit(2)
        matching_sessions = [document async for document in session_cursor]
        if len(matching_sessions) > 1:
            logger.error(
                "Identidad OCPP ambigua: charge_point_id=%r "
                "transaction_id=%r coincidencias_al_menos=%d",
                self.charge_point_id,
                transaction_id,
                len(matching_sessions),
            )
            raise InternalError(
                "Ambiguous OCPP transaction identity; no session was modified."
            )

        session = matching_sessions[0] if matching_sessions else None
        if session is None:
            logger.warning(
                "TransactionEvent %s recibido para una transacción no activa %s",
                event_type,
                transaction_id,
            )
            return self.call_result_module.TransactionEvent()

        if session.get("estado") == "finalizada":
            if event_type == "Ended":
                closure = session.get("stop_closure")
                if (
                    isinstance(closure, dict)
                    and isinstance(session.get("fecha_fin"), datetime)
                ):
                    if _stop_closure_is_complete(closure):
                        return self.call_result_module.TransactionEvent()

                    required_stages = {
                        "rewards_applied",
                        "rewards_granted",
                        "event_recorded",
                    }
                    if (
                        required_stages.issubset(closure)
                        and closure.get("charger_cleanup_status")
                        in (
                            "pending",
                            "completed",
                            "skipped_foreign_transaction",
                        )
                    ):
                        meter_stop = session.get("meter_stop")
                        if meter_stop is None:
                            meter_stop = session.get("meter_start") or 0
                        await self.legacy_handler.on_stop_transaction(
                            transaction_id=session["transaction_id"],
                            meter_stop=meter_stop,
                            timestamp=session.get("fecha_fin", timestamp),
                            reason=session.get("motivo_fin", "Normal"),
                        )
                        return self.call_result_module.TransactionEvent()
            logger.warning(
                "TransactionEvent %s recibido para una sesión finalizada %s",
                event_type,
                transaction_id,
            )
            return self.call_result_module.TransactionEvent()

        if session.get("estado") != "activa":
            logger.warning(
                "TransactionEvent %s recibido para una transacción no activa %s",
                event_type,
                transaction_id,
            )
            return self.call_result_module.TransactionEvent()

        if values:
            await self.legacy_handler.on_meter_values(
                connector_id=session["connector_id"],
                transaction_id=session["transaction_id"],
                meter_value=values,
            )
        if event_type == "Ended":
            meter_stop = _energy_register_wh(meter_value or [])
            if meter_stop is None:
                meter_stop = session.get("meter_start") or 0
            await self.legacy_handler.on_stop_transaction(
                transaction_id=session["transaction_id"],
                meter_stop=meter_stop,
                timestamp=timestamp,
                reason=transaction_info.get("stopped_reason", "Normal"),
            )
        return self.call_result_module.TransactionEvent()


class OCPP201ChargePointHandler(OCPP2xChargePointHandler, OCPP201ChargePoint):
    call_result_module = call_result
    negotiated_subprotocol = "ocpp2.0.1"
