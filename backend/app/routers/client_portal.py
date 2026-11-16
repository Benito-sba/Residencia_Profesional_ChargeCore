import io
from datetime import datetime, timedelta
from typing import Literal
from xml.sax.saxutils import escape

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.auth import require_client
from app.database import db, get_collection
from app.models.client_portal import (
    ClientActiveSessionEnvelope,
    ClientStartChargeRequest,
    ClientRewardResponse,
    ClientRewardSelectionRequest,
    ClientRewardsEnvelope,
    ClientProfileResponse,
    ClientProfileUpdate,
    ClientQRChargeRequest,
    ClientSessionResponse,
    ClientVehicleCreate,
    ClientVehicleResponse,
    ClientVehicleUpdate,
)
from app.services import ocpp_command_service
from app.services.reward_service import get_finalized_session_count

router = APIRouter(prefix="/api/client", tags=["Portal Cliente"])


def _is_indeterminate_command_error(error: HTTPException) -> bool:
    return (
        isinstance(error.detail, dict)
        and error.detail.get("outcome") == "indeterminate"
    )


def _parse_vehicle_id(vehicle_id: str) -> ObjectId:
    try:
        return ObjectId(vehicle_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=422, detail="El vehículo seleccionado no es válido")


def _parse_request_id(request_id: str) -> ObjectId:
    try:
        return ObjectId(request_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=422, detail="La solicitud NFC no es válida")


@router.get("/me", response_model=ClientProfileResponse)
async def get_client_profile(current_user=Depends(require_client)):
    profile = dict(current_user)
    profile["total_cargas"] = await get_finalized_session_count(
        current_user["_id"],
        get_collection("sesiones"),
    )
    return profile


@router.put("/me", response_model=ClientProfileResponse)
async def update_client_profile(
    profile: ClientProfileUpdate,
    current_user=Depends(require_client),
):
    update_data = profile.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=422, detail="No hay datos para actualizar")

    users = get_collection("usuarios")
    user_filter = {
        "_id": current_user["_id"],
        "empresa_id": current_user["empresa_id"],
        "rol": "cliente",
        "activo": {"$ne": False},
    }
    result = await users.update_one(user_filter, {"$set": update_data})
    if result.matched_count != 1:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    updated_user = await users.find_one(
        {
            "_id": current_user["_id"],
            "empresa_id": current_user["empresa_id"],
        },
        {"password": 0},
    )
    if updated_user is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    updated_user["total_cargas"] = await get_finalized_session_count(
        current_user["_id"],
        get_collection("sesiones"),
    )
    return updated_user


@router.get("/me/vehicles", response_model=list[ClientVehicleResponse])
async def list_client_vehicles(current_user=Depends(require_client)):
    cursor = get_collection("vehiculos").find({
        "usuario_id": current_user["_id"],
        "empresa_id": current_user["empresa_id"],
        "activo": True,
    })
    return [vehicle async for vehicle in cursor]


@router.get("/me/charge-requests")
async def list_client_charge_requests(current_user=Depends(require_client)):
    now = datetime.utcnow()
    cursor = get_collection("nfc_charge_requests").find({
        "usuario_id": current_user["_id"],
        "empresa_id": current_user["empresa_id"],
        "status": {"$in": ["awaiting_vehicle", "start_requested"]},
        "expires_at": {"$gt": now},
    }).sort("created_at", -1).limit(5)

    requests = []
    async for item in cursor:
        charger = await get_collection("cargadores").find_one(
            {
                "charge_point_id": item["charge_point_id"],
                "empresa_id": current_user["empresa_id"],
            },
            {"_id": 0, "nombre": 1, "conectores": 1},
        )
        connectors = []
        if charger:
            connectors = [
                {
                    "id": connector.get("id_conector"),
                    "tipo": connector.get("tipo"),
                    "estado": connector.get("estado", "Available"),
                }
                for connector in charger.get("conectores") or []
                if connector.get("id_conector") is not None
            ]
        requests.append({
            "id": str(item["_id"]),
            "charge_point_id": item["charge_point_id"],
            "cargador": charger.get("nombre") if charger else item["charge_point_id"],
            "conectores": connectors,
            "source": item.get("source", "nfc"),
            "status": item["status"],
            "expires_at": item["expires_at"],
        })
    return requests


@router.post("/me/qr-charge-requests", status_code=status.HTTP_201_CREATED)
async def create_client_qr_charge_request(
    payload: ClientQRChargeRequest,
    current_user=Depends(require_client),
):
    now = datetime.utcnow()
    user_id = current_user["_id"]
    company_id = current_user["empresa_id"]

    active_session = await get_collection("sesiones").find_one({
        "usuario_id": user_id,
        "empresa_id": company_id,
        "estado": {"$in": ["activa", "iniciada"]},
    })
    if active_session:
        raise HTTPException(status_code=409, detail="Ya tienes una sesión de carga activa")

    card = await get_collection("nfc_cards").find_one({
        "usuario_id": user_id,
        "empresa_id": company_id,
        "estado": "activa",
    })
    if card is None:
        raise HTTPException(
            status_code=409,
            detail="Tu cuenta necesita una tarjeta NFC activa asignada para iniciar mediante QR",
        )

    charger = await get_collection("cargadores").find_one({
        "charge_point_id": payload.charge_point_id,
        "empresa_id": company_id,
        "activo": True,
    })
    if charger is None:
        raise HTTPException(status_code=404, detail="El cargador del QR no existe o no está disponible")

    connectors = [
        {
            "id": connector.get("id_conector"),
            "tipo": connector.get("tipo"),
            "estado": connector.get("estado", "Available"),
        }
        for connector in charger.get("conectores") or []
        if connector.get("id_conector") is not None
    ]
    if not any(
        str(connector["estado"]).lower()
        in {"available", "disponible", "preparing", "preparando"}
        for connector in connectors
    ):
        raise HTTPException(
            status_code=409,
            detail="El cargador no reporta conectores disponibles",
        )

    charge_request = {
        "_id": ObjectId(),
        "charge_point_id": payload.charge_point_id,
        "empresa_id": company_id,
        "uid": card["uid"],
        "card_id": card["_id"],
        "usuario_id": user_id,
        "status": "awaiting_vehicle",
        "source": "qr",
        "created_at": now,
        "expires_at": now + timedelta(minutes=3),
    }
    await get_collection("nfc_charge_requests").insert_one(charge_request)
    return {
        "id": str(charge_request["_id"]),
        "charge_point_id": payload.charge_point_id,
        "cargador": charger.get("nombre") or payload.charge_point_id,
        "conectores": connectors,
        "status": charge_request["status"],
        "expires_at": charge_request["expires_at"],
    }


@router.post("/me/charge-requests/{request_id}/start")
async def start_client_charge(
    request_id: str,
    selection: ClientStartChargeRequest,
    current_user=Depends(require_client),
):
    request_object_id = _parse_request_id(request_id)
    now = datetime.utcnow()
    company_id = current_user["empresa_id"]
    request = await get_collection("nfc_charge_requests").find_one({
        "_id": request_object_id,
        "usuario_id": current_user["_id"],
        "empresa_id": company_id,
        "status": "awaiting_vehicle",
    })
    if request is None:
        raise HTTPException(
            status_code=404,
            detail="La solicitud NFC no existe, ya fue utilizada o expiró",
        )
    if request.get("expires_at") is None or request["expires_at"] <= now:
        raise HTTPException(status_code=410, detail="La solicitud NFC expiró; acerca de nuevo tu tarjeta")

    vehicle_id = _parse_vehicle_id(selection.vehicle_id)
    vehicle = await get_collection("vehiculos").find_one({
        "_id": vehicle_id,
        "usuario_id": current_user["_id"],
        "empresa_id": company_id,
        "activo": True,
    })
    if vehicle is None:
        raise HTTPException(status_code=404, detail="El vehículo no existe o no pertenece a tu cuenta")

    charger = await get_collection("cargadores").find_one({
        "charge_point_id": request["charge_point_id"],
        "empresa_id": company_id,
        "activo": True,
    })
    if charger is None:
        raise HTTPException(status_code=409, detail="El cargador ya no está disponible")
    connector = next(
        (
            item for item in charger.get("conectores", [])
            if item.get("id_conector") == selection.connector_id
        ),
        None,
    )
    if connector is None:
        raise HTTPException(status_code=422, detail="El conector seleccionado no pertenece al cargador")
    connector_status = str(connector.get("estado", "Available")).lower()
    if connector_status not in {"available", "disponible", "preparing", "preparando"}:
        raise HTTPException(status_code=409, detail="El conector seleccionado no está disponible")

    active_session = await get_collection("sesiones").find_one({
        "usuario_id": current_user["_id"],
        "empresa_id": company_id,
        "estado": {"$in": ["activa", "iniciada"]},
    })
    if active_session:
        raise HTTPException(status_code=409, detail="Ya tienes una sesión de carga activa")

    expires_at = now + timedelta(minutes=3)
    claim_update = {"$set": {
        "status": "start_requested",
        "vehicle_id": vehicle_id,
        "connector_id": selection.connector_id,
        "start_requested_at": now,
        "expires_at": expires_at,
    }}
    if request.get("source") == "qr":
        claim_update["$unset"] = {
            "last_result": "",
            "last_error": "",
        }
    claimed = await get_collection("nfc_charge_requests").update_one(
        {
            "_id": request_object_id,
            "usuario_id": current_user["_id"],
            "empresa_id": company_id,
            "status": "awaiting_vehicle",
            "expires_at": request["expires_at"],
        },
        claim_update,
    )
    if claimed.matched_count != 1:
        raise HTTPException(status_code=409, detail="La solicitud NFC ya fue procesada; actualiza la pantalla")

    try:
        result = await ocpp_command_service.remote_start(
            request["charge_point_id"],
            request["uid"],
            selection.connector_id,
            request_id=request["_id"],
        )
    except HTTPException as error:
        if not _is_indeterminate_command_error(error):
            recovery_fields = {"status": "awaiting_vehicle"}
            if request.get("source") == "qr":
                recovery_fields.update({
                    "last_result": "error",
                    "last_error": str(error.detail),
                    "last_attempt_at": datetime.utcnow(),
                })
            await get_collection("nfc_charge_requests").update_one(
                {
                    "_id": request_object_id,
                    "usuario_id": current_user["_id"],
                    "empresa_id": company_id,
                    "status": "start_requested",
                },
                {"$set": recovery_fields},
            )
        raise

    if result["status"] not in {"Accepted", "Scheduled"}:
        recovery_fields = {"status": "awaiting_vehicle"}
        if request.get("source") == "qr":
            recovery_fields.update({
                "last_result": "rejected",
                "last_error": str(result["status"]),
                "last_attempt_at": datetime.utcnow(),
            })
        await get_collection("nfc_charge_requests").update_one(
            {
                "_id": request_object_id,
                "usuario_id": current_user["_id"],
                "empresa_id": company_id,
                "status": "start_requested",
            },
            {"$set": recovery_fields},
        )
        raise HTTPException(
            status_code=409,
            detail=f"El cargador rechazó el inicio remoto: {result['status']}",
        )
    return {
        "mensaje": "Orden de inicio enviada al cargador",
        "status": result["status"],
    }


@router.post("/me/sessions/{session_id}/stop")
async def stop_client_charge(
    session_id: str,
    current_user=Depends(require_client),
):
    session_object_id = _parse_request_id(session_id)
    company_id = current_user["empresa_id"]
    sessions = get_collection("sesiones")
    session = await sessions.find_one({
        "_id": session_object_id,
        "usuario_id": current_user["_id"],
        "empresa_id": company_id,
        "estado": {"$in": ["activa", "iniciada"]},
    })
    if session is None:
        raise HTTPException(status_code=404, detail="No tienes esa sesión de carga activa")
    if session.get("stop_requested_at"):
        raise HTTPException(status_code=409, detail="Ya se solicitó detener esta carga")
    if session.get("transaction_id") is None:
        raise HTTPException(status_code=409, detail="La sesión no tiene una transacción OCPP válida")

    claim = await sessions.update_one(
        {
            "_id": session_object_id,
            "usuario_id": current_user["_id"],
            "empresa_id": company_id,
            "estado": {"$in": ["activa", "iniciada"]},
            "stop_requested_at": {"$exists": False},
        },
        {"$set": {"stop_requested_at": datetime.utcnow()}},
    )
    if claim.matched_count != 1:
        raise HTTPException(status_code=409, detail="Ya se solicitó detener esta carga")
    try:
        result = await ocpp_command_service.remote_stop(
            session["charge_point_id"],
            session["transaction_id"],
        )
    except HTTPException as error:
        if not _is_indeterminate_command_error(error):
            await sessions.update_one(
                {
                    "_id": session_object_id,
                    "usuario_id": current_user["_id"],
                    "empresa_id": company_id,
                    "estado": {"$in": ["activa", "iniciada"]},
                },
                {"$unset": {"stop_requested_at": ""}},
            )
        raise
    if result["status"] == "Rejected":
        await sessions.update_one(
            {
                "_id": session_object_id,
                "usuario_id": current_user["_id"],
                "empresa_id": company_id,
                "estado": {"$in": ["activa", "iniciada"]},
            },
            {"$unset": {"stop_requested_at": ""}},
        )
        raise HTTPException(
            status_code=409,
            detail="El cargador rechazó la orden de detención",
        )
    return {
        "mensaje": "Orden de detención enviada al cargador",
        "status": result["status"],
    }


@router.post("/me/sessions/{session_id}/reward")
async def select_client_reward(
    session_id: str,
    selection: ClientRewardSelectionRequest,
    current_user=Depends(require_client),
):
    session_object_id = _parse_request_id(session_id)
    sessions = get_collection("sesiones")
    reward_id = (
        _parse_request_id(selection.reward_id)
        if selection.reward_id is not None
        else None
    )
    rewards = get_collection("recompensas")
    company_id = current_user["empresa_id"]

    async def update_selection(mongo_session):
        session = await sessions.find_one(
            {
                "_id": session_object_id,
                "usuario_id": current_user["_id"],
                "empresa_id": company_id,
                "estado": {"$in": ["activa", "iniciada"]},
            },
            session=mongo_session,
        )
        if session is None:
            raise HTTPException(
                status_code=404, detail="No tienes esa sesión activa"
            )
        if session.get("stop_requested_at"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "La recompensa ya no se puede cambiar después de "
                    "solicitar el cierre"
                ),
            )

        previous_reward_id = session.get("selected_reward_id")
        if reward_id == previous_reward_id:
            return {
                "mensaje": "La selección de recompensa no cambió",
                "reward_id": str(reward_id) if reward_id else None,
            }

        if reward_id is not None:
            reward = await rewards.find_one(
                {
                    "_id": reward_id,
                    "usuario_id": current_user["_id"],
                    "usado": {"$ne": True},
                },
                session=mongo_session,
            )
            if reward is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "La recompensa no existe, no te pertenece o ya fue "
                        "utilizada"
                    ),
                )

            valid_until = reward.get("snapshot_fecha_finalizacion")
            valid_from = reward.get("snapshot_fecha_inicio")
            now = datetime.utcnow()
            if isinstance(valid_from, datetime) and valid_from > now:
                raise HTTPException(
                    status_code=409,
                    detail="La vigencia de esta recompensa aún no inicia",
                )
            if (
                isinstance(valid_until, datetime)
                and valid_until < now
            ):
                raise HTTPException(
                    status_code=409,
                    detail="La vigencia de esta recompensa terminó",
                )

            reserved = await rewards.update_one(
                {
                    "_id": reward_id,
                    "usuario_id": current_user["_id"],
                    "usado": {"$ne": True},
                    "$or": [
                        {"reservada_para_sesion": {"$exists": False}},
                        {"reservada_para_sesion": None},
                        {"reservada_para_sesion": session_object_id},
                    ],
                },
                {"$set": {"reservada_para_sesion": session_object_id}},
                session=mongo_session,
            )
            if reserved.matched_count != 1:
                raise HTTPException(
                    status_code=409,
                    detail="La recompensa ya está reservada para otra sesión",
                )

        session_filter = {
            "_id": session_object_id,
            "usuario_id": current_user["_id"],
            "empresa_id": company_id,
            "estado": {"$in": ["activa", "iniciada"]},
            "stop_requested_at": {"$exists": False},
        }
        if previous_reward_id is None:
            session_filter["$or"] = [
                {"selected_reward_id": {"$exists": False}},
                {"selected_reward_id": None},
            ]
        else:
            session_filter["selected_reward_id"] = previous_reward_id

        update = (
            {"$set": {
                "selected_reward_id": reward_id,
                "selected_reward_at": datetime.utcnow(),
            }}
            if reward_id is not None
            else {"$unset": {
                "selected_reward_id": "",
                "selected_reward_at": "",
            }}
        )
        updated = await sessions.update_one(
            session_filter,
            update,
            session=mongo_session,
        )
        if updated.matched_count != 1:
            raise HTTPException(
                status_code=409,
                detail="La sesión cambió; vuelve a cargarla e intenta de nuevo",
            )

        if previous_reward_id is not None:
            await rewards.update_one(
                {
                    "_id": previous_reward_id,
                    "reservada_para_sesion": session_object_id,
                },
                {"$unset": {"reservada_para_sesion": ""}},
                session=mongo_session,
            )

        return {
            "mensaje": (
                "Recompensa seleccionada para esta sesión"
                if reward_id is not None
                else "Selección de recompensa eliminada"
            ),
            "reward_id": str(reward_id) if reward_id else None,
        }

    async with await db.client.start_session() as mongo_session:
        return await mongo_session.with_transaction(update_selection)


@router.get("/me/sessions", response_model=list[ClientSessionResponse])
async def list_client_sessions(current_user=Depends(require_client)):
    owner_id = current_user["_id"]
    company_id = current_user["empresa_id"]
    session_projection = {
        "_id": 1,
        "charge_point_id": 1,
        "connector_id": 1,
        "vehiculo_id": 1,
        "fecha_inicio": 1,
        "fecha_fin": 1,
        "energia_consumida_kwh": 1,
        "potencia_promedio_kw": 1,
        "duracion_minutos": 1,
        "estado": 1,
        "motivo_fin": 1,
        "beneficio_aplicado": 1,
    }
    cursor = get_collection("sesiones").find(
        {"usuario_id": owner_id, "empresa_id": company_id},
        session_projection,
    ).sort("fecha_inicio", -1)
    sessions = [session async for session in cursor]
    session_ids = [session["_id"] for session in sessions]
    rewards_by_session = {}
    if session_ids:
        reward_cursor = get_collection("recompensas").find(
            {
                "usuario_id": owner_id,
                "sesion_id_uso": {"$in": session_ids},
            },
            {
                "sesion_id_uso": 1,
                "snapshot_nombre": 1,
                "snapshot_empresa": 1,
                "snapshot_descripcion": 1,
                "snapshot_condiciones": 1,
                "nombre": 1,
                "empresa": 1,
                "descripcion": 1,
                "condiciones": 1,
            },
        )
        async for reward in reward_cursor:
            session_id = reward.get("sesion_id_uso")
            if session_id:
                rewards_by_session[session_id] = {
                    "nombre": reward.get("snapshot_nombre") or reward.get("nombre"),
                    "empresa": reward.get("snapshot_empresa") or reward.get("empresa"),
                    "descripcion": (
                        reward.get("snapshot_descripcion")
                        or reward.get("descripcion")
                    ),
                    "condiciones": (
                        reward.get("snapshot_condiciones")
                        or reward.get("condiciones")
                    ),
                }

    vehicle_ids = {
        session.get("vehiculo_id")
        for session in sessions
        if session.get("vehiculo_id")
    }
    vehicles = {
        vehicle["_id"]: vehicle
        async for vehicle in get_collection("vehiculos").find(
            {
                "_id": {"$in": list(vehicle_ids)},
                "usuario_id": owner_id,
                "empresa_id": company_id,
            },
            {
                "_id": 1,
                "marca": 1,
                "modelo": 1,
                "placa": 1,
                "tipo_conector": 1,
            },
        )
    }

    charge_point_ids = {
        session.get("charge_point_id")
        for session in sessions
        if session.get("charge_point_id")
    }
    chargers = {
        charger["charge_point_id"]: charger.get("nombre")
        async for charger in get_collection("cargadores").find(
            {
                "charge_point_id": {"$in": list(charge_point_ids)},
                "empresa_id": company_id,
            },
            {"charge_point_id": 1, "nombre": 1},
        )
    }

    response = []
    for session in sessions:
        vehicle = vehicles.get(session.get("vehiculo_id"))
        response.append({
            "id": str(session["_id"]),
            "cargador": chargers.get(session.get("charge_point_id")),
            "charge_point_id": session.get("charge_point_id"),
            "connector_id": session.get("connector_id"),
            "vehiculo": {
                "marca": vehicle.get("marca"),
                "modelo": vehicle.get("modelo"),
                "placa": vehicle.get("placa"),
                "tipo_conector": vehicle.get("tipo_conector"),
            } if vehicle else None,
            "fecha_inicio": session.get("fecha_inicio"),
            "fecha_fin": session.get("fecha_fin"),
            "energia_consumida_kwh": session.get("energia_consumida_kwh"),
            "potencia_promedio_kw": session.get("potencia_promedio_kw"),
            "duracion_minutos": session.get("duracion_minutos"),
            "estado": session.get("estado"),
            "motivo_fin": session.get("motivo_fin"),
            "beneficio_aplicado": session.get("beneficio_aplicado"),
            "recompensa": rewards_by_session.get(session["_id"]),
        })
    return response


def _ticket_value(value):
    if value is None or value == "":
        return "No registrado"
    if isinstance(value, datetime):
        return (
            value.astimezone().strftime("%d/%m/%Y %H:%M")
            if value.tzinfo
            else value.strftime("%d/%m/%Y %H:%M")
        )
    return str(value)


@router.get("/me/sessions/{session_id}/ticket")
async def download_client_session_ticket(
    session_id: str,
    formato: Literal["recibo", "a6", "carta"] = "recibo",
    current_user=Depends(require_client),
):
    if formato not in {"recibo", "a6", "carta"}:
        raise HTTPException(status_code=422, detail="El formato del ticket no es válido")

    try:
        session_object_id = ObjectId(session_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=422, detail="La sesión seleccionada no es válida")

    session = await get_collection("sesiones").find_one({
        "_id": session_object_id,
        "usuario_id": current_user["_id"],
        "empresa_id": current_user["empresa_id"],
    })
    if session is None:
        raise HTTPException(status_code=404, detail="Sesión de carga no encontrada")
    if str(session.get("estado") or "").lower() not in {
        "finalizada",
        "finalizado",
        "completed",
    }:
        raise HTTPException(
            status_code=409,
            detail="El ticket estará disponible cuando la carga haya finalizado",
        )

    reward = await get_collection("recompensas").find_one({
        "usuario_id": current_user["_id"],
        "sesion_id_uso": session_object_id,
    }, {
        "snapshot_nombre": 1,
        "snapshot_empresa": 1,
        "snapshot_descripcion": 1,
        "snapshot_condiciones": 1,
        "nombre": 1,
        "empresa": 1,
        "descripcion": 1,
        "condiciones": 1,
    })
    vehicle = None
    if session.get("vehiculo_id"):
        vehicle = await get_collection("vehiculos").find_one({
            "_id": session["vehiculo_id"],
            "usuario_id": current_user["_id"],
            "empresa_id": current_user["empresa_id"],
        }, {
            "marca": 1,
            "modelo": 1,
            "placa": 1,
            "tipo_conector": 1,
        })
    charger = await get_collection("cargadores").find_one({
        "charge_point_id": session.get("charge_point_id"),
        "empresa_id": current_user["empresa_id"],
    })

    try:
        from reportlab.lib import colors
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from reportlab.graphics.shapes import Drawing, Group, Polygon, Rect
        from reportlab.lib.pagesizes import A6, letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfgen.canvas import Canvas
        from reportlab.platypus import (
            HRFlowable,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise RuntimeError("reportlab no está instalado") from error

    session_key = str(session["_id"])
    folio = f"CC-{session_key[-8:].upper()}"
    vehicle_name = " ".join(
        str(value)
        for value in (
            (vehicle or {}).get("marca"),
            (vehicle or {}).get("modelo"),
        )
        if value
    ) or "No registrado"
    if (vehicle or {}).get("placa"):
        vehicle_name = f"{vehicle_name} · {(vehicle or {}).get('placa')}"

    energy = session.get("energia_consumida_kwh")
    energy_number = (
        f"{energy:.2f}"
        if isinstance(energy, (int, float)) and not isinstance(energy, bool)
        else None
    )
    duration = session.get("duracion_minutos")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        hours, minutes = divmod(int(duration), 60)
        duration_text = (
            f"{hours} h {minutes} min" if hours else f"{minutes} min"
        )
    else:
        duration_text = "No registrado"
    average_power = session.get("potencia_promedio_kw")
    power_text = (
        f"{average_power:g} kW"
        if isinstance(average_power, (int, float))
        and not isinstance(average_power, bool)
        else "No registrado"
    )
    reward_name = (
        (reward or {}).get("snapshot_nombre")
        or (reward or {}).get("nombre")
    )
    reward_company = (
        (reward or {}).get("snapshot_empresa")
        or (reward or {}).get("empresa")
    )
    reward_description = (
        (reward or {}).get("snapshot_descripcion")
        or (reward or {}).get("descripcion")
    )
    reward_conditions = (
        (reward or {}).get("snapshot_condiciones")
        or (reward or {}).get("condiciones")
    )
    has_reward = bool(session.get("beneficio_aplicado") or reward)

    receipt_width = 80 * mm
    ticket_margin = 5 * mm
    content_width = receipt_width - 2 * ticket_margin
    palette = {
        "orange": colors.HexColor("#F15A24"),
        "dark": colors.HexColor("#1C1C1C"),
        "gray": colors.HexColor("#77716A"),
        "light": colors.HexColor("#FFF0E6"),
        "line": colors.HexColor("#E6D7C8"),
        "green": colors.HexColor("#DDF5E8"),
        "green_text": colors.HexColor("#16834A"),
    }
    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "TicketHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=14,
        textColor=colors.white,
    )
    header_subtitle_style = ParagraphStyle(
        "TicketHeaderSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        leading=9,
        textColor=colors.white,
    )
    folio_style = ParagraphStyle(
        "TicketFolio",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=21,
        alignment=1,
        textColor=palette["dark"],
    )
    centered_small_style = ParagraphStyle(
        "TicketCenteredSmall",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        leading=9,
        alignment=1,
        textColor=palette["gray"],
    )
    label_style = ParagraphStyle(
        "TicketLabel",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=palette["gray"],
    )
    value_style = ParagraphStyle(
        "TicketValue",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        alignment=2,
        textColor=palette["dark"],
    )
    energy_label_style = ParagraphStyle(
        "TicketEnergyLabel",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        alignment=1,
        textColor=colors.white,
    )
    energy_value_style = ParagraphStyle(
        "TicketEnergyValue",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=25,
        alignment=1,
        textColor=colors.white,
    )
    reward_title_style = ParagraphStyle(
        "TicketRewardTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        alignment=1,
        textColor=palette["orange"],
    )
    reward_detail_style = ParagraphStyle(
        "TicketRewardDetail",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        alignment=1,
        textColor=palette["dark"],
    )
    thanks_style = ParagraphStyle(
        "TicketThanks",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        alignment=1,
        textColor=palette["dark"],
    )

    logo = Drawing(32, 32)
    logo.add(Rect(
        0,
        0,
        32,
        32,
        rx=7,
        ry=7,
        fillColor=colors.white,
        strokeColor=colors.white,
    ))
    logo.add(Polygon(
        [19, 3, 9, 17, 17, 17, 14, 29, 25, 13, 18, 13],
        fillColor=palette["orange"],
        strokeColor=palette["orange"],
        strokeWidth=0,
    ))
    header = Table(
        [[
            logo,
            [
                Paragraph("ChargeCore", header_style),
                Paragraph("CRODE Mérida · Ticket de carga", header_subtitle_style),
            ],
        ]],
        colWidths=(40, content_width - 40),
        hAlign="LEFT",
    )
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), palette["orange"]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))

    ticket_date = _ticket_value(
        session.get("fecha_fin") or session.get("fecha_inicio")
    )
    story = [
        header,
        Spacer(1, 8),
        Paragraph("FOLIO", centered_small_style),
        Paragraph(escape(folio), folio_style),
        Paragraph(escape(ticket_date), centered_small_style),
        Spacer(1, 8),
        HRFlowable(
            width="100%",
            thickness=0.6,
            color=palette["line"],
            dash=(2, 2),
        ),
        Spacer(1, 8),
    ]

    details = [
        ("Cliente", current_user.get("nombre") or "Cliente"),
        ("Vehículo", vehicle_name),
        (
            "Cargador",
            (charger or {}).get("nombre")
            or session.get("charge_point_id")
            or "No registrado",
        ),
        (
            "Conector",
            f"{_ticket_value(session.get('connector_id'))} · "
            f"{_ticket_value((vehicle or {}).get('tipo_conector'))}",
        ),
        ("Inicio", _ticket_value(session.get("fecha_inicio"))),
        ("Fin", _ticket_value(session.get("fecha_fin"))),
        ("Duración", duration_text),
        ("Potencia promedio", power_text),
    ]
    if session.get("motivo_fin"):
        details.append(("Motivo de cierre", str(session["motivo_fin"])))
    details.append(("Estado", "Finalizada"))

    detail_rows = [
        [
            Paragraph(escape(label), label_style),
            Paragraph(escape(str(value)), value_style),
        ]
        for label, value in details
    ]
    detail_table = Table(
        detail_rows,
        colWidths=(content_width * 0.42, content_width * 0.58),
        hAlign="LEFT",
    )
    detail_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.extend([detail_table, Spacer(1, 9)])

    energy_value = (
        Paragraph(
            f'<font size="22"><b>{escape(energy_number)}</b></font> '
            f'<font color="#F15A24"><b>kWh</b></font>',
            energy_value_style,
        )
        if energy_number
        else Paragraph("No registrado", energy_value_style)
    )
    energy_block = Table(
        [
            [Paragraph("ENERGÍA ENTREGADA", energy_label_style)],
            [energy_value],
        ],
        colWidths=(content_width,),
        hAlign="LEFT",
    )
    energy_block.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), palette["dark"]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([energy_block, Spacer(1, 9)])

    if has_reward:
        reward_lines = [
            Paragraph("¡GANASTE UNA RECOMPENSA!", reward_title_style)
        ]
        reward_heading = " · ".join(
            value for value in (reward_name, reward_company) if value
        )
        reward_heading = reward_heading or session.get(
            "beneficio_aplicado"
        ) or "Beneficio aplicado a esta carga"
        reward_lines.append(Paragraph(escape(reward_heading), reward_detail_style))
        if reward_description:
            reward_lines.append(
                Paragraph(escape(reward_description), reward_detail_style)
            )
        elif session.get("beneficio_aplicado") and reward_heading != session.get(
            "beneficio_aplicado"
        ):
            reward_lines.append(
                Paragraph(
                    escape(session["beneficio_aplicado"]),
                    centered_small_style,
                )
            )
        if reward_conditions:
            reward_lines.append(
                Paragraph(escape(reward_conditions), centered_small_style)
            )
        reward_box = Table([[reward_lines]], colWidths=(content_width,))
        reward_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), palette["light"]),
            ("BOX", (0, 0), (-1, -1), 0.8, palette["orange"]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.extend([reward_box, Spacer(1, 9)])

    qr_widget = QrCodeWidget(f"ChargeCore ticket {folio}")
    qr_left, qr_bottom, qr_right, _ = qr_widget.getBounds()
    qr_size = 24 * mm
    qr_scale = qr_size / (qr_right - qr_left)
    qr_offset = (content_width - qr_size) / 2
    qr_group = Group()
    qr_group.add(qr_widget)
    qr_group.transform = [
        qr_scale,
        0,
        0,
        qr_scale,
        qr_offset - qr_left * qr_scale,
        -qr_bottom * qr_scale,
    ]
    qr_drawing = Drawing(content_width, qr_size)
    qr_drawing.add(qr_group)
    story.extend([
        qr_drawing,
        Spacer(1, 3),
        Paragraph("Código del ticket", centered_small_style),
        Spacer(1, 7),
        HRFlowable(
            width="100%",
            thickness=0.6,
            color=palette["line"],
            dash=(2, 2),
        ),
        Spacer(1, 7),
        Paragraph("Gracias por realizar su carga con ChargeCore.", thanks_style),
        Spacer(1, 5),
        Paragraph(
            "ChargeCore · Monitoreo de cargadores eléctricos",
            centered_small_style,
        ),
    ])

    def measure_story():
        height = 0
        for flowable in story:
            _, flowable_height = flowable.wrap(content_width, 100000)
            height += (
                flowable.getSpaceBefore()
                + flowable_height
                + flowable.getSpaceAfter()
            )
        return height

    story_height = measure_story()
    ticket_height = story_height + 2 * ticket_margin
    if formato == "recibo":
        page_width, page_height = receipt_width, ticket_height
        scale = 1
        offset_x = 0
        offset_y = 0
    else:
        page_width, page_height = A6 if formato == "a6" else letter
        page_margin = 8 * mm
        scale = min(
            1,
            (page_width - 2 * page_margin) / receipt_width,
            (page_height - 2 * page_margin) / ticket_height,
        )
        offset_x = (page_width - receipt_width * scale) / 2
        offset_y = (page_height - ticket_height * scale) / 2

    pdf_buffer = io.BytesIO()
    pdf = Canvas(
        pdf_buffer,
        pagesize=(page_width, page_height),
        pageCompression=0,
    )
    pdf.setFillColor(colors.white)
    pdf.rect(0, 0, page_width, page_height, fill=1, stroke=0)
    pdf.saveState()
    pdf.translate(offset_x, offset_y)
    pdf.scale(scale, scale)
    cursor_y = ticket_height - ticket_margin
    for flowable in story:
        cursor_y -= flowable.getSpaceBefore()
        _, flowable_height = flowable.wrap(content_width, 100000)
        cursor_y -= flowable_height
        flowable.drawOn(pdf, ticket_margin, cursor_y)
        cursor_y -= flowable.getSpaceAfter()
    pdf.restoreState()
    pdf.showPage()
    pdf.save()
    pdf_buffer.seek(0)
    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="chargecore-ticket-{session_key[-8:]}-'
                f'{formato}.pdf"'
            )
        },
    )


@router.get("/me/active-session", response_model=ClientActiveSessionEnvelope)
async def get_client_active_session(current_user=Depends(require_client)):
    owner_id = current_user["_id"]
    company_id = current_user["empresa_id"]
    cursor = get_collection("sesiones").find(
        {
            "usuario_id": owner_id,
            "empresa_id": company_id,
            "estado": {"$in": ["activa", "iniciada"]},
        },
        {
            "_id": 1,
            "charge_point_id": 1,
            "connector_id": 1,
            "vehiculo_id": 1,
            "transaction_id": 1,
            "fecha_inicio": 1,
            "energia_consumida_kwh": 1,
            "estado": 1,
            "stop_requested_at": 1,
            "selected_reward_id": 1,
        },
    ).sort("fecha_inicio", -1).limit(1)
    session = None
    async for item in cursor:
        session = item
        break
    if session is None:
        return {"active": False, "session": None}

    vehicle = None
    vehicle_id = session.get("vehiculo_id")
    if vehicle_id:
        vehicle = await get_collection("vehiculos").find_one(
            {
                "_id": vehicle_id,
                "usuario_id": owner_id,
                "empresa_id": company_id,
            },
            {"_id": 0, "marca": 1, "modelo": 1, "placa": 1},
        )

    charge_point_id = session.get("charge_point_id")
    charger = None
    if charge_point_id:
        charger = await get_collection("cargadores").find_one(
            {
                "charge_point_id": charge_point_id,
                "empresa_id": company_id,
            },
            {"_id": 0, "nombre": 1},
        )

    power_kw = None
    latest_update = None
    transaction_id = session.get("transaction_id")
    if transaction_id is not None:
        latest_meter = await get_collection("meter_values").find_one(
            {
                "transaction_id": transaction_id,
                "charge_point_id": charge_point_id,
            },
            {"_id": 0, "timestamp": 1, "sampled_values": 1},
            sort=[("timestamp", -1)],
        )
        if latest_meter:
            latest_update = latest_meter.get("timestamp")
            for meter in latest_meter.get("sampled_values") or []:
                for sampled in meter.get("sampled_value") or []:
                    if sampled.get("measurand") != "Power.Active.Import":
                        continue
                    try:
                        value = float(sampled.get("value"))
                    except (TypeError, ValueError):
                        continue
                    unit = str(sampled.get("unit") or "").lower()
                    if unit == "kw":
                        power_kw = value
                    elif unit == "w":
                        power_kw = value / 1000
                    break
                if power_kw is not None:
                    break

    return {
        "active": True,
        "session": {
            "id": str(session["_id"]),
            "cargador": charger.get("nombre") if charger else None,
            "charge_point_id": charge_point_id,
            "connector_id": session.get("connector_id"),
            "vehiculo": vehicle,
            "fecha_inicio": session.get("fecha_inicio"),
            "energia_consumida_kwh": session.get("energia_consumida_kwh"),
            "potencia_actual_kw": power_kw,
            "ultima_actualizacion": latest_update,
            "duracion_minutos": None,
            "estado": session.get("estado"),
            "stop_requested": bool(session.get("stop_requested_at")),
            "selected_reward_id": (
                str(session["selected_reward_id"])
                if session.get("selected_reward_id")
                else None
            ),
        },
    }


@router.get("/me/rewards", response_model=ClientRewardsEnvelope)
async def list_client_rewards(current_user=Depends(require_client)):
    cursor = get_collection("recompensas").find(
        {"usuario_id": current_user["_id"]}
    ).sort("fecha_otorgamiento", -1)

    rewards = []
    async for reward in cursor:
        used = bool(reward.get("usado", False))
        valid_from = reward.get("snapshot_fecha_inicio")
        valid_until = reward.get("snapshot_fecha_finalizacion")
        now = datetime.utcnow()
        not_started = isinstance(valid_from, datetime) and valid_from > now
        expired = isinstance(valid_until, datetime) and valid_until < now
        rewards.append({
            "id": str(reward["_id"]),
            "nombre": reward.get("snapshot_nombre"),
            "empresa": reward.get("snapshot_empresa"),
            "descripcion": reward.get("snapshot_descripcion") or reward.get("descripcion"),
            "condiciones": reward.get("snapshot_condiciones"),
            "fecha_otorgamiento": reward.get("fecha_otorgamiento"),
            "fecha_inicio": reward.get("snapshot_fecha_inicio"),
            "fecha_finalizacion": reward.get("snapshot_fecha_finalizacion"),
            "cargas_requeridas": reward.get("snapshot_cargas_requeridas") or reward.get("cargas_requeridas"),
            "cargas_acumuladas": reward.get("cargas_acumuladas"),
            "usada": used,
            "fecha_uso": reward.get("fecha_uso"),
            "estado": (
                "Usada"
                if used
                else "Pendiente"
                if not_started
                else "Vencida"
                if expired
                else "Disponible"
            ),
        })

    return {"recompensas": rewards, "total": len(rewards)}


@router.post(
    "/me/vehicles",
    response_model=ClientVehicleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_client_vehicle(
    vehicle: ClientVehicleCreate,
    current_user=Depends(require_client),
):
    vehicle_data = vehicle.model_dump()
    vehicle_data.update({
        "usuario_id": current_user["_id"],
        "empresa_id": current_user["empresa_id"],
        "activo": True,
        "created_at": datetime.utcnow(),
    })
    result = await get_collection("vehiculos").insert_one(vehicle_data)
    created_vehicle = await get_collection("vehiculos").find_one({
        "_id": result.inserted_id,
        "usuario_id": current_user["_id"],
        "empresa_id": current_user["empresa_id"],
    })
    return created_vehicle


@router.put("/me/vehicles/{vehicle_id}", response_model=ClientVehicleResponse)
async def update_client_vehicle(
    vehicle_id: str,
    vehicle: ClientVehicleUpdate,
    current_user=Depends(require_client),
):
    object_id = _parse_vehicle_id(vehicle_id)
    update_data = vehicle.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=422, detail="No hay datos para actualizar")

    result = await get_collection("vehiculos").update_one(
        {
            "_id": object_id,
            "usuario_id": current_user["_id"],
            "empresa_id": current_user["empresa_id"],
        },
        {"$set": update_data},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")

    updated_vehicle = await get_collection("vehiculos").find_one({
        "_id": object_id,
        "usuario_id": current_user["_id"],
        "empresa_id": current_user["empresa_id"],
    })
    return updated_vehicle


@router.delete("/me/vehicles/{vehicle_id}")
async def delete_client_vehicle(
    vehicle_id: str,
    current_user=Depends(require_client),
):
    object_id = _parse_vehicle_id(vehicle_id)
    result = await get_collection("vehiculos").update_one(
        {
            "_id": object_id,
            "usuario_id": current_user["_id"],
            "empresa_id": current_user["empresa_id"],
        },
        {"$set": {"activo": False}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    return {"mensaje": "Vehículo desactivado"}
