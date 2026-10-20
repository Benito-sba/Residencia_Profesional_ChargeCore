from fastapi import APIRouter, HTTPException, status, Depends
from app.database import get_collection
from app.auth import require_admin, require_operator, require_read_access
from app.models.charger import CargadorCreate, CargadorUpdate, OCPPPasswordUpdate
from app.models.ocpp_commands import (
    ChangeAvailabilityRequest,
    RemoteStartRequest,
    RemoteStopRequest,
    ResetRequest,
)
from app.services import ocpp_command_service
from app.ocpp_auth import hash_ocpp_password
from app.validation import parse_object_id

router = APIRouter(prefix="/api/cargadores", tags=["Cargadores"])


def _public_charger(charger):
    charger["ocpp_credentials_configured"] = bool(
        charger.pop("ocpp_password_hash", None)
    )
    return charger


@router.get("/")
async def list_chargers(current_user=Depends(require_read_access)):
    cursor = get_collection("cargadores").find({"activo": True})
    chargers = []
    async for c in cursor:
        c["_id"] = str(c["_id"])
        chargers.append(_public_charger(c))
    return chargers

@router.get("/{charge_point_id}/status")
async def get_charger_status(charge_point_id: str, current_user=Depends(require_read_access)):
    charger = await get_collection("cargadores").find_one({"charge_point_id": charge_point_id, "activo": True})
    if not charger:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    charger["_id"] = str(charger["_id"])
    return _public_charger(charger)

@router.get("/{charge_point_id}/connectors")
async def get_charger_connectors(charge_point_id: str, current_user=Depends(require_read_access)):
    charger = await get_collection("cargadores").find_one(
        {"charge_point_id": charge_point_id, "activo": True},
        {"_id": 0, "conectores": 1},
    )
    if charger is None:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    return charger.get("conectores", [])

@router.get("/{charge_point_id}/meter-values")
async def get_charger_meter_values(
    charge_point_id: str,
    limit: int = 100,
    current_user=Depends(require_read_access),
):
    cursor = get_collection("meter_values").find(
        {"charge_point_id": charge_point_id}
    ).sort("timestamp", -1).limit(limit)
    values = []
    async for value in cursor:
        value["_id"] = str(value["_id"])
        values.append(value)
    return values


@router.get("/{charger_id}")
async def get_charger(charger_id: str, current_user=Depends(require_read_access)):
    charger = await get_collection("cargadores").find_one(
        {"_id": parse_object_id(charger_id, "cargador")}
    )
    if not charger:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    charger["_id"] = str(charger["_id"])
    return _public_charger(charger)

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_charger(charger: CargadorCreate, current_user=Depends(require_admin)):
    charger_data = charger.model_dump(exclude={"ocpp_password"})
    charger_data["ocpp_password_hash"] = hash_ocpp_password(charger.ocpp_password)
    result = await get_collection("cargadores").insert_one(charger_data)
    return {"id": str(result.inserted_id)}


@router.put("/{charger_id}/ocpp-credentials")
async def update_ocpp_credentials(
    charger_id: str,
    credentials: OCPPPasswordUpdate,
    current_user=Depends(require_admin),
):
    result = await get_collection("cargadores").update_one(
        {"_id": parse_object_id(charger_id, "cargador")},
        {"$set": {
            "ocpp_password_hash": hash_ocpp_password(credentials.password),
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    return {"mensaje": "Credenciales OCPP actualizadas"}

@router.put("/{charger_id}")
async def update_charger(charger_id: str, charger: CargadorUpdate, current_user=Depends(require_admin)):
    updates = charger.model_dump(exclude_unset=True)
    update_data = {
        key: value
        for key, value in updates.items()
        if value is not None
    }
    update = {}
    if update_data:
        update["$set"] = update_data
    if not update:
        return {"mensaje": "Sin cambios"}
    result = await get_collection("cargadores").update_one(
        {"_id": parse_object_id(charger_id, "cargador")},
        update
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    return {"mensaje": "Cargador actualizado"}

@router.get("/{charger_id}/historial")
async def get_charger_history(charger_id: str, current_user=Depends(require_read_access)):
    charger = await get_collection("cargadores").find_one(
        {"_id": parse_object_id(charger_id, "cargador")}
    )
    if not charger:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")

    cp_id = charger["charge_point_id"]
    cursor = get_collection("sesiones").find({"charge_point_id": cp_id}).sort("fecha_inicio", -1).limit(50)
    sessions = []
    async for s in cursor:
        s["_id"] = str(s["_id"])
        sessions.append(s)
    return sessions


@router.post("/{charge_point_id}/remote-start")
async def remote_start(charge_point_id: str, request: RemoteStartRequest, current_user=Depends(require_operator)):
    return await ocpp_command_service.remote_start(
        charge_point_id,
        request.id_tag,
        request.connector_id,
    )


@router.post("/{charge_point_id}/remote-stop")
async def remote_stop(charge_point_id: str, request: RemoteStopRequest, current_user=Depends(require_operator)):
    return await ocpp_command_service.remote_stop(charge_point_id, request.transaction_id)


@router.post("/{charge_point_id}/reset")
async def reset(charge_point_id: str, request: ResetRequest, current_user=Depends(require_operator)):
    return await ocpp_command_service.reset(charge_point_id, request.type)


@router.post("/{charge_point_id}/availability")
async def change_availability(charge_point_id: str, request: ChangeAvailabilityRequest, current_user=Depends(require_operator)):
    return await ocpp_command_service.change_availability(
        charge_point_id,
        request.connector_id,
        request.type,
    )