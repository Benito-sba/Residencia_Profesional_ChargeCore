from fastapi import APIRouter, Depends, Query
from datetime import datetime, timedelta
from app.database import get_collection
from app.auth import require_read_access

router = APIRouter(prefix="/api/monitoreo", tags=["Monitoreo"])

@router.get("/alertas")
async def list_alerts(
    nivel: str = Query(None, pattern="^(info|warning|error|debug)$"),
    modulo: str = Query(None),
    dias: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=500),
    current_user=Depends(require_read_access)
):
    logs_col = get_collection("logs")
    query = {"timestamp": {"$gte": datetime.utcnow() - timedelta(days=dias)}}
    if nivel:
        query["nivel"] = nivel
    if modulo:
        query["modulo"] = {"$regex": modulo, "$options": "i"}

    cursor = logs_col.find(query).sort("timestamp", -1).limit(limit)
    logs = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        logs.append(doc)
    return logs

@router.get("/eventos-ocpp")
async def list_ocpp_events(
    charge_point_id: str = Query(None),
    tipo: str = Query(None),
    dias: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=500),
    current_user=Depends(require_read_access)
):
    eventos_col = get_collection("eventos_ocpp")
    query = {"timestamp": {"$gte": datetime.utcnow() - timedelta(days=dias)}}
    if charge_point_id:
        query["charge_point_id"] = charge_point_id
    if tipo:
        query["tipo_mensaje"] = tipo

    cursor = eventos_col.find(query).sort("timestamp", -1).limit(limit)
    events = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        events.append(doc)
    return events

@router.get("/ocpp-messages/{charge_point_id}")
async def list_ocpp_messages(
    charge_point_id: str,
    action: str = Query(None),
    message_type_name: str = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    current_user=Depends(require_read_access),
):
    query = {"charge_point_id": charge_point_id}
    if action:
        query["action"] = action
    if message_type_name:
        query["message_type_name"] = message_type_name
    cursor = get_collection("ocpp_messages").find(query).sort("timestamp", -1).limit(limit)
    messages = []
    async for message in cursor:
        message["_id"] = str(message["_id"])
        messages.append(message)
    return messages

@router.get("/ocpp-errors")
async def list_ocpp_errors(
    charge_point_id: str = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    current_user=Depends(require_read_access),
):
    query = {"message_type_name": "CALLERROR"}
    if charge_point_id:
        query["charge_point_id"] = charge_point_id
    cursor = get_collection("ocpp_messages").find(query).sort("timestamp", -1).limit(limit)
    errors = []
    async for error in cursor:
        error["_id"] = str(error["_id"])
        errors.append(error)
    return errors