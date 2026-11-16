from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.encoders import jsonable_encoder
from app.database import get_collection, get_database
from app.auth import company_scoped_query, require_company_admin_access
from app.models.charger import CargadorCreate, CargadorUpdate, OCPPPasswordUpdate
from app.models.ocpp_commands import (
    ChangeAvailabilityRequest,
    RemoteStopRequest,
    ResetRequest,
)
from app.services import ocpp_command_service
from app.ocpp_auth import hash_ocpp_password
from app.validation import parse_object_id

router = APIRouter(prefix="/api/cargadores", tags=["Cargadores"])


def _public_charger(charger):
    if charger.get("empresa_id"):
        charger["empresa_id"] = str(charger["empresa_id"])
    charger["ocpp_credentials_configured"] = bool(
        charger.pop("ocpp_password_hash", None)
    )
    return charger


async def _has_unscoped_charger_activity(charge_point_id: str) -> bool:
    query = {
        "charge_point_id": charge_point_id,
        "$or": [
            {"empresa_id": {"$exists": False}},
            {"empresa_id": None},
        ],
    }
    for collection_name in ("logs", "eventos_ocpp", "ocpp_messages"):
        if await get_collection(collection_name).find_one(query):
            return True
    return False


@router.get("/")
async def list_chargers(current_user=Depends(require_company_admin_access)):
    cursor = get_collection("cargadores").find(
        company_scoped_query(current_user, {"activo": True})
    )
    chargers = []
    async for c in cursor:
        c["_id"] = str(c["_id"])
        chargers.append(_public_charger(c))
    return chargers

@router.get("/{charge_point_id}/status")
async def get_charger_status(
    charge_point_id: str,
    current_user=Depends(require_company_admin_access),
):
    charger = await get_collection("cargadores").find_one(
        company_scoped_query(current_user, {
            "charge_point_id": charge_point_id,
            "activo": True,
        })
    )
    if not charger:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    charger["_id"] = str(charger["_id"])
    return _public_charger(charger)

@router.get("/{charge_point_id}/connectors")
async def get_charger_connectors(
    charge_point_id: str,
    current_user=Depends(require_company_admin_access),
):
    charger = await get_collection("cargadores").find_one(
        company_scoped_query(current_user, {
            "charge_point_id": charge_point_id,
            "activo": True,
        }),
        {"_id": 0, "conectores": 1},
    )
    if charger is None:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    return charger.get("conectores", [])

@router.get("/{charge_point_id}/meter-values")
async def get_charger_meter_values(
    charge_point_id: str,
    limit: int = 100,
    current_user=Depends(require_company_admin_access),
):
    charger = await get_collection("cargadores").find_one(
        company_scoped_query(current_user, {
            "charge_point_id": charge_point_id,
            "activo": True,
        }),
        {"_id": 1},
    )
    if charger is None:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    cursor = get_collection("meter_values").find(
        company_scoped_query(current_user, {"charge_point_id": charge_point_id})
    ).sort("timestamp", -1).limit(limit)
    values = []
    async for value in cursor:
        value["_id"] = str(value["_id"])
        values.append(value)
    return jsonable_encoder(values, custom_encoder={ObjectId: str})


@router.get("/{charger_id}")
async def get_charger(
    charger_id: str,
    current_user=Depends(require_company_admin_access),
):
    charger = await get_collection("cargadores").find_one(
        company_scoped_query(
            current_user,
            {"_id": parse_object_id(charger_id, "cargador"), "activo": True},
        )
    )
    if not charger:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    charger["_id"] = str(charger["_id"])
    return _public_charger(charger)

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_charger(
    charger: CargadorCreate,
    current_user=Depends(require_company_admin_access),
):
    retired_charger = await get_collection("cargador_eliminaciones").find_one(
        {"charge_point_id": charger.charge_point_id}
    )
    if retired_charger is not None:
        raise HTTPException(
            status_code=409,
            detail="El identificador OCPP pertenece a un cargador retirado y no se puede reutilizar",
        )

    if current_user.get("rol") == "dueno_empresa":
        if charger.empresa_id is not None:
            raise HTTPException(
                status_code=403,
                detail="La empresa del cargador se determina desde la sesión",
            )
        empresa_id = current_user["empresa_id"]
    else:
        empresa_id = charger.empresa_id
        if empresa_id is not None:
            company = await get_collection("empresas").find_one({
                "_id": empresa_id,
                "activo": True,
            })
            if company is None:
                raise HTTPException(
                    status_code=403,
                    detail="La empresa asignada no está disponible",
                )

    charger_data = charger.model_dump(exclude={"ocpp_password", "empresa_id"})
    if empresa_id is not None:
        charger_data["empresa_id"] = empresa_id
    charger_data["ocpp_password_hash"] = hash_ocpp_password(charger.ocpp_password)
    result = await get_collection("cargadores").insert_one(charger_data)
    return {"id": str(result.inserted_id)}


@router.put("/{charger_id}/ocpp-credentials")
async def update_ocpp_credentials(
    charger_id: str,
    credentials: OCPPPasswordUpdate,
    current_user=Depends(require_company_admin_access),
):
    result = await get_collection("cargadores").update_one(
        company_scoped_query(
            current_user,
            {"_id": parse_object_id(charger_id, "cargador"), "activo": True},
        ),
        {"$set": {
            "ocpp_password_hash": hash_ocpp_password(credentials.password),
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    return {"mensaje": "Credenciales OCPP actualizadas"}

@router.put("/{charger_id}")
async def update_charger(
    charger_id: str,
    charger: CargadorUpdate,
    current_user=Depends(require_company_admin_access),
):
    updates = charger.model_dump(exclude_unset=True)
    assigning_company = "empresa_id" in updates
    if assigning_company and current_user.get("rol") != "superadmin":
        raise HTTPException(
            status_code=403,
            detail="La asignación de empresa requiere permisos de superadministrador",
        )
    if assigning_company and updates["empresa_id"] is None:
        raise HTTPException(
            status_code=422,
            detail="No se puede desasignar la empresa de un cargador",
        )

    update_data = {
        key: value
        for key, value in updates.items()
        if value is not None
    }
    object_id = parse_object_id(charger_id, "cargador")
    chargers = get_collection("cargadores")
    target_query = company_scoped_query(
        current_user,
        {"_id": object_id, "activo": True},
    )

    if assigning_company:
        company = await get_collection("empresas").find_one({
            "_id": update_data["empresa_id"],
            "activo": True,
        })
        if company is None:
            raise HTTPException(
                status_code=403,
                detail="La empresa asignada no está disponible",
            )

        existing = await chargers.find_one(target_query)
        if existing is None:
            raise HTTPException(status_code=404, detail="Cargador no encontrado")
        existing_company_id = existing.get("empresa_id")
        if existing_company_id is not None:
            if existing_company_id != update_data["empresa_id"]:
                raise HTTPException(
                    status_code=409,
                    detail="No se puede reasignar un cargador que ya pertenece a una empresa",
                )
        else:
            if (
                existing.get("conectado") is True
                or existing.get("connection_token") is not None
            ):
                raise HTTPException(
                    status_code=409,
                    detail="No se puede asignar un cargador mientras mantenga una conexión OCPP",
                )

            charge_point_id = existing.get("charge_point_id")
            retired_history = await get_collection(
                "cargador_eliminaciones"
            ).find_one({"charge_point_id": charge_point_id})
            if retired_history is not None:
                raise HTTPException(
                    status_code=409,
                    detail="No se puede asignar un cargador con historial de eliminación ambiguo",
                )

            active_sessions = await get_collection("sesiones").count_documents({
                "charge_point_id": charge_point_id,
                "estado": {"$in": ["activa", "iniciada"]},
            })
            if active_sessions:
                raise HTTPException(
                    status_code=409,
                    detail="No se puede asignar un cargador con una sesión activa",
                )
            if await _has_unscoped_charger_activity(charge_point_id):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No se puede asignar un cargador con actividad histórica "
                        "sin empresa; requiere verificación explícita"
                    ),
                )
            target_query["$or"] = [
                {"empresa_id": {"$exists": False}},
                {"empresa_id": None},
            ]
            target_query["conectado"] = {"$ne": True}
            target_query["connection_token"] = {"$in": [None]}

    update = {}
    if update_data:
        update["$set"] = update_data
    if not update:
        return {"mensaje": "Sin cambios"}
    result = await chargers.update_one(target_query, update)
    if result.matched_count == 0:
        if assigning_company:
            raise HTTPException(
                status_code=409,
                detail="El cargador cambió mientras se asignaba su empresa",
            )
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    return {"mensaje": "Cargador actualizado"}


@router.delete("/{charger_id}")
async def delete_charger(
    charger_id: str,
    current_user=Depends(require_company_admin_access),
):
    object_id = parse_object_id(charger_id, "cargador")
    chargers = get_collection("cargadores")
    sessions = get_collection("sesiones")
    deletion_logs = get_collection("cargador_eliminaciones")
    database = get_database()

    async with await database.client.start_session() as session:
        async with session.start_transaction():
            charger = await chargers.find_one(
                company_scoped_query(
                    current_user,
                    {"_id": object_id, "activo": {"$ne": False}},
                ),
                session=session,
            )
            if charger is None:
                raise HTTPException(status_code=404, detail="Cargador no encontrado")

            active_sessions = await sessions.count_documents(
                {
                    "charge_point_id": charger.get("charge_point_id"),
                    "estado": {"$in": ["activa", "iniciada"]},
                },
                session=session,
            )
            if active_sessions:
                raise HTTPException(
                    status_code=409,
                    detail="No se puede eliminar un cargador con una sesión activa",
                )

            deleted_at = datetime.utcnow()
            deletion_record = {
                "charger_id": str(object_id),
                "charge_point_id": charger.get("charge_point_id"),
                "nombre": charger.get("nombre"),
                "ubicacion": charger.get("ubicacion"),
                "deleted_at": deleted_at,
                "deleted_by": (
                    current_user.get("nombre")
                    or current_user.get("email")
                    or str(current_user.get("_id") or "")
                ),
                "empresa_id": charger.get("empresa_id"),
                "charger_snapshot": {
                    key: value
                    for key, value in charger.items()
                    if key not in {"_id", "ocpp_password_hash"}
                },
            }
            await deletion_logs.insert_one(deletion_record, session=session)
            result = await chargers.update_one(
                company_scoped_query(
                    current_user,
                    {"_id": object_id, "activo": {"$ne": False}},
                ),
                {"$set": {
                    "activo": False,
                    "deleted_at": deleted_at,
                    "deleted_by": deletion_record["deleted_by"],
                }},
                session=session,
            )
            if result.matched_count == 0:
                raise HTTPException(status_code=404, detail="Cargador no encontrado")

    return {"mensaje": "Cargador desactivado y registrado en reportes"}


@router.get("/{charger_id}/historial")
async def get_charger_history(
    charger_id: str,
    current_user=Depends(require_company_admin_access),
):
    charger = await get_collection("cargadores").find_one(
        company_scoped_query(
            current_user,
            {"_id": parse_object_id(charger_id, "cargador"), "activo": True},
        )
    )
    if not charger:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")

    cp_id = charger["charge_point_id"]
    cursor = get_collection("sesiones").find(
        company_scoped_query(current_user, {"charge_point_id": cp_id})
    ).sort("fecha_inicio", -1).limit(50)
    sessions = []
    async for s in cursor:
        s["_id"] = str(s["_id"])
        sessions.append(s)
    return jsonable_encoder(sessions, custom_encoder={ObjectId: str})


@router.post("/{charge_point_id}/remote-start")
async def remote_start(
    charge_point_id: str,
    current_user=Depends(require_company_admin_access),
):
    charger = await get_collection("cargadores").find_one(
        company_scoped_query(current_user, {
            "charge_point_id": charge_point_id,
            "activo": True,
        }),
        {"_id": 1},
    )
    if charger is None:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="El inicio de carga debe solicitarlo el cliente desde su cuenta",
    )


@router.post("/{charge_point_id}/remote-stop")
async def remote_stop(
    charge_point_id: str,
    request: RemoteStopRequest,
    current_user=Depends(require_company_admin_access),
):
    session = await get_collection("sesiones").find_one(
        company_scoped_query(current_user, {
            "charge_point_id": charge_point_id,
            "transaction_id": request.transaction_id,
            "estado": {"$in": ["activa", "iniciada"]},
        })
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Sesión activa no encontrada")
    return await ocpp_command_service.remote_stop(charge_point_id, request.transaction_id)


@router.post("/{charge_point_id}/commands/reconcile")
async def reconcile_remote_commands(
    charge_point_id: str,
    current_user=Depends(require_company_admin_access),
):
    charger = await get_collection("cargadores").find_one(
        company_scoped_query(current_user, {
            "charge_point_id": charge_point_id,
        })
    )
    if charger is None:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    return await ocpp_command_service.reconcile_remote_commands(
        charger,
        current_user["_id"],
    )


@router.post("/{charge_point_id}/reset")
async def reset(
    charge_point_id: str,
    request: ResetRequest,
    current_user=Depends(require_company_admin_access),
):
    charger = await get_collection("cargadores").find_one(
        company_scoped_query(current_user, {
            "charge_point_id": charge_point_id,
            "activo": True,
        }),
        {"_id": 1},
    )
    if charger is None:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    return await ocpp_command_service.reset(charge_point_id, request.type)


@router.post("/{charge_point_id}/availability")
async def change_availability(
    charge_point_id: str,
    request: ChangeAvailabilityRequest,
    current_user=Depends(require_company_admin_access),
):
    charger = await get_collection("cargadores").find_one(
        company_scoped_query(current_user, {
            "charge_point_id": charge_point_id,
            "activo": True,
        }),
        {"_id": 1},
    )
    if charger is None:
        raise HTTPException(status_code=404, detail="Cargador no encontrado")
    return await ocpp_command_service.change_availability(
        charge_point_id,
        request.connector_id,
        request.type,
    )