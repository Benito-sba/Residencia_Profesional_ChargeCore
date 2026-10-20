from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timedelta
from app.database import get_collection
from app.auth import require_read_access
from app.validation import parse_object_id

router = APIRouter(prefix="/api/sesiones", tags=["Sesiones de Carga"])


def _parse_date(value: str, field_name: str, end_date: bool = False):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail=f"El valor de {field_name} no es una fecha válida",
        )
    if end_date and len(value) == 10:
        return parsed + timedelta(days=1), "$lt"
    return parsed, "$lte"


async def _enrich_sessions(sessions):
    user_ids = {session.get("usuario_id") for session in sessions if session.get("usuario_id")}
    vehicle_ids = {session.get("vehiculo_id") for session in sessions if session.get("vehiculo_id")}
    card_ids = {session.get("nfc_card_id") for session in sessions if session.get("nfc_card_id")}
    charge_point_ids = {session.get("charge_point_id") for session in sessions if session.get("charge_point_id")}

    users = {
        user["_id"]: user
        async for user in get_collection("usuarios").find({"_id": {"$in": list(user_ids)}})
    }
    vehicles = {
        vehicle["_id"]: vehicle
        async for vehicle in get_collection("vehiculos").find({"_id": {"$in": list(vehicle_ids)}})
    }
    cards = {
        card["_id"]: card
        async for card in get_collection("nfc_cards").find({"_id": {"$in": list(card_ids)}})
    }
    chargers = {
        charger["charge_point_id"]: charger
        async for charger in get_collection("cargadores").find(
            {"charge_point_id": {"$in": list(charge_point_ids)}},
            {"charge_point_id": 1, "nombre": 1},
        )
    }

    for session in sessions:
        user = users.get(session.get("usuario_id"))
        vehicle = vehicles.get(session.get("vehiculo_id"))
        card = cards.get(session.get("nfc_card_id"))
        charger = chargers.get(session.get("charge_point_id"))

        session["_id"] = str(session["_id"])
        for field in ("usuario_id", "vehiculo_id", "nfc_card_id"):
            if session.get(field):
                session[field] = str(session[field])
        session["usuario_nombre"] = user.get("nombre") if user else None
        session["usuario_email"] = user.get("email") if user else None
        session["vehiculo_placa"] = vehicle.get("placa") if vehicle else None
        session["vehiculo_marca"] = vehicle.get("marca") if vehicle else None
        session["vehiculo_modelo"] = vehicle.get("modelo") if vehicle else None
        session["nfc_uid"] = card.get("uid") if card else None
        session["cargador_nombre"] = charger.get("nombre") if charger else None
    return sessions

@router.get("/")
async def list_sessions(
    skip: int = 0,
    limit: int = 100,
    estado: str = None,
    usuario_id: str = None,
    charge_point_id: str = None,
    fecha_inicio: str = None,
    fecha_fin: str = None,
    current_user=Depends(require_read_access)
):
    query = {}
    if estado:
        query["estado"] = estado
    if usuario_id:
        query["usuario_id"] = parse_object_id(usuario_id, "usuario")
    if charge_point_id:
        query["charge_point_id"] = charge_point_id
    if fecha_inicio or fecha_fin:
        query["fecha_inicio"] = {}
        if fecha_inicio:
            start_date, _ = _parse_date(fecha_inicio, "fecha_inicio")
            query["fecha_inicio"]["$gte"] = start_date
        if fecha_fin:
            end_date, operator = _parse_date(fecha_fin, "fecha_fin", end_date=True)
            query["fecha_inicio"][operator] = end_date

    cursor = get_collection("sesiones").find(query).sort("fecha_inicio", -1).skip(skip).limit(limit)
    sessions = []
    async for s in cursor:
        sessions.append(s)
    return await _enrich_sessions(sessions)

@router.get("/transaction/{transaction_id}")
async def get_session_by_transaction(transaction_id: int, current_user=Depends(require_read_access)):
    session = await get_collection("sesiones").find_one({"transaction_id": transaction_id})
    if session is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    session["_id"] = str(session["_id"])
    for field in ("usuario_id", "vehiculo_id", "nfc_card_id"):
        if session.get(field):
            session[field] = str(session[field])
    return session

@router.get("/transaction/{transaction_id}/meter-values")
async def get_session_meter_values(transaction_id: int, current_user=Depends(require_read_access)):
    values = []
    cursor = get_collection("meter_values").find(
        {"transaction_id": transaction_id}
    ).sort("timestamp", -1)
    async for value in cursor:
        value["_id"] = str(value["_id"])
        values.append(value)
    return values

@router.get("/activas")
async def get_active_sessions(current_user=Depends(require_read_access)):
    cursor = get_collection("sesiones").find({"estado": {"$in": ["iniciada", "activa"]}})
    sessions = []
    async for s in cursor:
        sessions.append(s)
    return await _enrich_sessions(sessions)

@router.get("/usuario/{user_id}")
async def get_user_sessions(user_id: str, current_user=Depends(require_read_access)):
    cursor = get_collection("sesiones").find({
        "usuario_id": parse_object_id(user_id, "usuario"),
        "estado": "finalizada"
    }).sort("fecha_inicio", -1)

    sessions = []
    total_energia = 0

    async for s in cursor:
        s["_id"] = str(s["_id"])
        s["usuario_id"] = str(s["usuario_id"])
        sessions.append(s)
        total_energia += s.get("energia_consumida_kwh", 0)

    return {
        "sesiones": sessions,
        "resumen": {
            "total_sesiones": len(sessions),
            "energia_total_kwh": round(total_energia, 2)
        }
    }