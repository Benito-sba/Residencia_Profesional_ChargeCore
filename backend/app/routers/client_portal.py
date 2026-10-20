from datetime import datetime, timedelta

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_client
from app.database import db, get_collection
from app.models.client_portal import (
    ClientActiveSessionEnvelope,
    ClientStartChargeRequest,
    ClientRewardResponse,
    ClientRewardSelectionRequest,
    ClientRewardsEnvelope,
    ClientProfileResponse,
    ClientSessionResponse,
    ClientVehicleCreate,
    ClientVehicleResponse,
    ClientVehicleUpdate,
)
from app.services import ocpp_command_service

router = APIRouter(prefix="/api/client", tags=["Portal Cliente"])


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
    return current_user


@router.get("/me/vehicles", response_model=list[ClientVehicleResponse])
async def list_client_vehicles(current_user=Depends(require_client)):
    cursor = get_collection("vehiculos").find({
        "usuario_id": current_user["_id"],
        "activo": True,
    })
    return [vehicle async for vehicle in cursor]


@router.get("/me/charge-requests")
async def list_client_charge_requests(current_user=Depends(require_client)):
    now = datetime.utcnow()
    cursor = get_collection("nfc_charge_requests").find({
        "usuario_id": current_user["_id"],
        "status": {"$in": ["awaiting_vehicle", "start_requested"]},
        "expires_at": {"$gt": now},
    }).sort("created_at", -1).limit(5)

    requests = []
    async for item in cursor:
        charger = await get_collection("cargadores").find_one(
            {"charge_point_id": item["charge_point_id"]},
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
            "status": item["status"],
            "expires_at": item["expires_at"],
        })
    return requests


@router.post("/me/charge-requests/{request_id}/start")
async def start_client_charge(
    request_id: str,
    selection: ClientStartChargeRequest,
    current_user=Depends(require_client),
):
    request_object_id = _parse_request_id(request_id)
    now = datetime.utcnow()
    request = await get_collection("nfc_charge_requests").find_one({
        "_id": request_object_id,
        "usuario_id": current_user["_id"],
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
        "activo": True,
    })
    if vehicle is None:
        raise HTTPException(status_code=404, detail="El vehículo no existe o no pertenece a tu cuenta")

    charger = await get_collection("cargadores").find_one({
        "charge_point_id": request["charge_point_id"],
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
        "estado": {"$in": ["activa", "iniciada"]},
    })
    if active_session:
        raise HTTPException(status_code=409, detail="Ya tienes una sesión de carga activa")

    expires_at = now + timedelta(minutes=3)
    claimed = await get_collection("nfc_charge_requests").update_one(
        {
            "_id": request_object_id,
            "usuario_id": current_user["_id"],
            "status": "awaiting_vehicle",
            "expires_at": request["expires_at"],
        },
        {"$set": {
            "status": "start_requested",
            "vehicle_id": vehicle_id,
            "connector_id": selection.connector_id,
            "start_requested_at": now,
            "expires_at": expires_at,
        }},
    )
    if claimed.matched_count != 1:
        raise HTTPException(status_code=409, detail="La solicitud NFC ya fue procesada; actualiza la pantalla")

    try:
        result = await ocpp_command_service.remote_start(
            request["charge_point_id"],
            request["uid"],
            selection.connector_id,
        )
    except HTTPException:
        await get_collection("nfc_charge_requests").update_one(
            {
                "_id": request_object_id,
                "usuario_id": current_user["_id"],
                "status": "start_requested",
            },
            {"$set": {"status": "awaiting_vehicle"}},
        )
        raise

    if result["status"] not in {"Accepted", "Scheduled"}:
        await get_collection("nfc_charge_requests").update_one(
            {
                "_id": request_object_id,
                "usuario_id": current_user["_id"],
                "status": "start_requested",
            },
            {"$set": {"status": "awaiting_vehicle"}},
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
    sessions = get_collection("sesiones")
    session = await sessions.find_one({
        "_id": session_object_id,
        "usuario_id": current_user["_id"],
        "estado": "activa",
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
            "estado": "activa",
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
    except HTTPException:
        await sessions.update_one(
            {
                "_id": session_object_id,
                "usuario_id": current_user["_id"],
                "estado": "activa",
            },
            {"$unset": {"stop_requested_at": ""}},
        )
        raise
    if result["status"] == "Rejected":
        await sessions.update_one(
            {
                "_id": session_object_id,
                "usuario_id": current_user["_id"],
                "estado": "activa",
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

    async def update_selection(mongo_session):
        session = await sessions.find_one(
            {
                "_id": session_object_id,
                "usuario_id": current_user["_id"],
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
    session_projection = {
        "_id": 1,
        "charge_point_id": 1,
        "connector_id": 1,
        "vehiculo_id": 1,
        "fecha_inicio": 1,
        "fecha_fin": 1,
        "energia_consumida_kwh": 1,
        "duracion_minutos": 1,
        "estado": 1,
        "beneficio_aplicado": 1,
    }
    cursor = get_collection("sesiones").find(
        {"usuario_id": owner_id},
        session_projection,
    ).sort("fecha_inicio", -1)
    sessions = [session async for session in cursor]

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
            },
            {"_id": 1, "marca": 1, "modelo": 1, "placa": 1},
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
            {"charge_point_id": {"$in": list(charge_point_ids)}},
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
            } if vehicle else None,
            "fecha_inicio": session.get("fecha_inicio"),
            "fecha_fin": session.get("fecha_fin"),
            "energia_consumida_kwh": session.get("energia_consumida_kwh"),
            "duracion_minutos": session.get("duracion_minutos"),
            "estado": session.get("estado"),
            "beneficio_aplicado": session.get("beneficio_aplicado"),
        })
    return response


@router.get("/me/active-session", response_model=ClientActiveSessionEnvelope)
async def get_client_active_session(current_user=Depends(require_client)):
    owner_id = current_user["_id"]
    cursor = get_collection("sesiones").find(
        {
            "usuario_id": owner_id,
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
            },
            {"_id": 0, "marca": 1, "modelo": 1, "placa": 1},
        )

    charge_point_id = session.get("charge_point_id")
    charger = None
    if charge_point_id:
        charger = await get_collection("cargadores").find_one(
            {"charge_point_id": charge_point_id},
            {"_id": 0, "nombre": 1},
        )

    power_kw = None
    latest_update = None
    transaction_id = session.get("transaction_id")
    if transaction_id is not None:
        latest_meter = await get_collection("meter_values").find_one(
            {"transaction_id": transaction_id},
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
        "activo": True,
        "created_at": datetime.utcnow(),
    })
    result = await get_collection("vehiculos").insert_one(vehicle_data)
    created_vehicle = await get_collection("vehiculos").find_one({"_id": result.inserted_id})
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
        },
        {"$set": update_data},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")

    updated_vehicle = await get_collection("vehiculos").find_one({"_id": object_id})
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
        },
        {"$set": {"activo": False}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    return {"mensaje": "Vehículo desactivado"}
