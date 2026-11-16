from bson import ObjectId
from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from datetime import datetime, timedelta
from app.database import get_collection
from app.auth import require_company_admin_access

router = APIRouter(prefix="/api/monitoreo", tags=["Monitoreo"])


async def _company_activity_query(current_user, query, *, charge_point_only=False):
    if current_user.get("rol") == "superadmin":
        return query
    if not charge_point_only:
        return {
            "$and": [
                query,
                {"empresa_id": current_user["empresa_id"]},
            ]
        }

    charge_point_ids = [
        charger["charge_point_id"]
        async for charger in get_collection("cargadores").find(
            {"empresa_id": current_user["empresa_id"], "activo": True},
            {"charge_point_id": 1},
        )
        if charger.get("charge_point_id")
    ]
    retired_ids = {
        charger["charge_point_id"]
        async for charger in get_collection("cargador_eliminaciones").find(
            {"charge_point_id": {"$in": charge_point_ids}},
            {"charge_point_id": 1},
        )
        if charger.get("charge_point_id")
    }
    charge_point_ids = [
        charge_point_id
        for charge_point_id in charge_point_ids
        if charge_point_id not in retired_ids
    ]
    scope = {
        "$and": [
            {"charge_point_id": {"$in": charge_point_ids}},
            {"empresa_id": current_user["empresa_id"]},
        ]
    }
    return {"$and": [query, scope]}


@router.get("/alertas")
async def list_alerts(
    nivel: str = Query(None, pattern="^(info|warning|error|debug)$"),
    modulo: str = Query(None),
    dias: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=500),
    current_user=Depends(require_company_admin_access)
):
    logs_col = get_collection("logs")
    query = {"timestamp": {"$gte": datetime.utcnow() - timedelta(days=dias)}}
    if nivel:
        query["nivel"] = nivel
    if modulo:
        query["modulo"] = {"$regex": modulo, "$options": "i"}

    cursor = logs_col.find(
        await _company_activity_query(current_user, query)
    ).sort("timestamp", -1).limit(limit)
    logs = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        logs.append(doc)
    return jsonable_encoder(logs, custom_encoder={ObjectId: str})

@router.get("/eventos-ocpp")
async def list_ocpp_events(
    charge_point_id: str = Query(None),
    tipo: str = Query(None),
    dias: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=500),
    current_user=Depends(require_company_admin_access)
):
    eventos_col = get_collection("eventos_ocpp")
    query = {"timestamp": {"$gte": datetime.utcnow() - timedelta(days=dias)}}
    if charge_point_id:
        query["charge_point_id"] = charge_point_id
    if tipo:
        query["tipo_mensaje"] = tipo

    cursor = eventos_col.find(
        await _company_activity_query(current_user, query)
    ).sort("timestamp", -1).limit(limit)
    events = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        events.append(doc)
    return jsonable_encoder(events, custom_encoder={ObjectId: str})

@router.get("/ocpp-messages/{charge_point_id}")
async def list_ocpp_messages(
    charge_point_id: str,
    action: str = Query(None),
    message_type_name: str = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    current_user=Depends(require_company_admin_access),
):
    query = {"charge_point_id": charge_point_id}
    if action:
        query["action"] = action
    if message_type_name:
        query["message_type_name"] = message_type_name
    cursor = get_collection("ocpp_messages").find(
        await _company_activity_query(
            current_user,
            query,
            charge_point_only=True,
        )
    ).sort("timestamp", -1).limit(limit)
    messages = []
    async for message in cursor:
        message["_id"] = str(message["_id"])
        messages.append(message)
    return jsonable_encoder(messages, custom_encoder={ObjectId: str})

@router.get("/ocpp-errors")
async def list_ocpp_errors(
    charge_point_id: str = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    current_user=Depends(require_company_admin_access),
):
    query = {"message_type_name": "CALLERROR"}
    if charge_point_id:
        query["charge_point_id"] = charge_point_id
    cursor = get_collection("ocpp_messages").find(
        await _company_activity_query(
            current_user,
            query,
            charge_point_only=True,
        )
    ).sort("timestamp", -1).limit(limit)
    errors = []
    async for error in cursor:
        error["_id"] = str(error["_id"])
        errors.append(error)
    return jsonable_encoder(errors, custom_encoder={ObjectId: str})