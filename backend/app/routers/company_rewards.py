from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.auth import require_client, require_company_owner
from app.database import db, get_collection
from app.models.phase4_rewards import (
    CompanyRedemptionDecision,
    CompanyRedemptionRequest,
    CompanyRewardCreate,
    CompanyRewardRule,
    CompanyRewardState,
    CompanyRewardUpdate,
)
from app.services.company_reward_points import _balance_id, _stable_id
from app.validation import parse_object_id


router = APIRouter(prefix="/api/company-rewards", tags=["Recompensas por empresa"])
client_router = APIRouter(prefix="/api/client/me/company-rewards", tags=["Recompensas por empresa"])


def _serialize(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def _owner_filter(current_user, extra=None):
    return {"empresa_id": current_user["empresa_id"], **(extra or {})}


@router.get("/catalog")
async def list_company_reward_catalog(current_user=Depends(require_company_owner)):
    cursor = get_collection("company_reward_catalog").find(
        _owner_filter(current_user)
    ).sort("created_at", -1)
    return [_serialize(item) async for item in cursor]


@router.post("/catalog", status_code=status.HTTP_201_CREATED)
async def create_company_reward(
    payload: CompanyRewardCreate,
    current_user=Depends(require_company_owner),
):
    now = datetime.utcnow()
    reward = {
        "_id": ObjectId(),
        **payload.model_dump(),
        "empresa_id": current_user["empresa_id"],
        "created_by": current_user["_id"],
        "created_at": now,
        "updated_at": now,
    }
    await get_collection("company_reward_catalog").insert_one(reward)
    return _serialize(reward)


@router.put("/catalog/{reward_id}")
async def update_company_reward(
    reward_id: str,
    payload: CompanyRewardUpdate,
    current_user=Depends(require_company_owner),
):
    object_id = parse_object_id(reward_id, "recompensa")
    catalog = get_collection("company_reward_catalog")
    existing = await catalog.find_one(_owner_filter(current_user, {"_id": object_id}))
    if existing is None:
        raise HTTPException(status_code=404, detail="Recompensa no encontrada")
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "fecha_finalizacion" in payload.model_fields_set:
        changes["fecha_finalizacion"] = payload.fecha_finalizacion
    if not changes:
        raise HTTPException(status_code=422, detail="No hay cambios para guardar")
    changes["updated_at"] = datetime.utcnow()
    await catalog.update_one(
        _owner_filter(current_user, {"_id": object_id}),
        {"$set": changes},
    )
    return _serialize(await catalog.find_one({"_id": object_id}))


@router.put("/catalog/{reward_id}/state")
async def set_company_reward_state(
    reward_id: str,
    payload: CompanyRewardState,
    current_user=Depends(require_company_owner),
):
    object_id = parse_object_id(reward_id, "recompensa")
    catalog = get_collection("company_reward_catalog")
    result = await catalog.update_one(
        _owner_filter(current_user, {"_id": object_id}),
        {"$set": {"activa": payload.activa, "updated_at": datetime.utcnow()}},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=404, detail="Recompensa no encontrada")
    return _serialize(await catalog.find_one({"_id": object_id}))


@router.get("/rules")
async def get_company_reward_rule(current_user=Depends(require_company_owner)):
    rule = await get_collection("company_reward_rules").find_one(
        {"_id": current_user["empresa_id"], "empresa_id": current_user["empresa_id"]}
    )
    return _serialize(rule) if rule else None


@router.put("/rules")
async def set_company_reward_rule(
    payload: CompanyRewardRule,
    current_user=Depends(require_company_owner),
):
    now = datetime.utcnow()
    rule = {
        "_id": current_user["empresa_id"],
        **payload.model_dump(),
        "empresa_id": current_user["empresa_id"],
        "updated_by": current_user["_id"],
        "updated_at": now,
    }
    await get_collection("company_reward_rules").update_one(
        {"_id": current_user["empresa_id"]},
        {"$set": rule, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return _serialize(rule)


@router.get("/balances")
async def list_company_reward_balances(current_user=Depends(require_company_owner)):
    cursor = get_collection("company_reward_balances").find(
        {"empresa_id": current_user["empresa_id"]}
    ).sort("updated_at", -1)
    return [_serialize(item) async for item in cursor]


@router.get("/ledger")
async def list_company_reward_ledger(current_user=Depends(require_company_owner)):
    cursor = get_collection("company_reward_ledger").find(
        {"empresa_id": current_user["empresa_id"]}
    ).sort("created_at", -1).limit(500)
    return [_serialize(item) async for item in cursor]


@router.get("/redemptions")
async def list_company_redemptions(current_user=Depends(require_company_owner)):
    cursor = get_collection("company_reward_redemptions").find(
        {"empresa_id": current_user["empresa_id"]}
    ).sort("created_at", -1).limit(500)
    return [_serialize(item) async for item in cursor]


@router.post("/redemptions/{redemption_id}/decision")
async def decide_company_redemption(
    redemption_id: str,
    payload: CompanyRedemptionDecision,
    current_user=Depends(require_company_owner),
):
    object_id = parse_object_id(redemption_id, "solicitud")
    redemptions = get_collection("company_reward_redemptions")
    balances = get_collection("company_reward_balances")
    ledger = get_collection("company_reward_ledger")
    now = datetime.utcnow()

    async def decide(session):
        redemption = await redemptions.find_one(
            _owner_filter(current_user, {"_id": object_id, "estado": "pendiente"}),
            session=session,
        )
        if redemption is None:
            raise HTTPException(status_code=409, detail="La solicitud ya fue atendida o no existe")
        balance_query = {
            "_id": _balance_id(current_user["empresa_id"], redemption["usuario_id"]),
            "empresa_id": current_user["empresa_id"],
            "usuario_id": redemption["usuario_id"],
            "puntos_reservados": {"$gte": redemption["puntos_requeridos"]},
        }
        change = (
            {"$inc": {"puntos_reservados": -redemption["puntos_requeridos"]}}
            if payload.decision == "aprobar"
            else {
                "$inc": {
                    "puntos_reservados": -redemption["puntos_requeridos"],
                    "puntos_disponibles": redemption["puntos_requeridos"],
                }
            }
        )
        balance_update = await balances.update_one(
            balance_query,
            {**change, "$set": {"updated_at": now}},
            session=session,
        )
        if balance_update.matched_count != 1:
            raise HTTPException(status_code=409, detail="El saldo reservado no coincide")
        result = await redemptions.update_one(
            _owner_filter(current_user, {"_id": object_id, "estado": "pendiente"}),
            {"$set": {
                "estado": "aprobada" if payload.decision == "aprobar" else "rechazada",
                "decision": payload.decision,
                "nota_decision": payload.nota,
                "decided_by": current_user["_id"],
                "decided_at": now,
            }, "$unset": {"pending_key": ""}},
            session=session,
        )
        if result.modified_count != 1:
            raise HTTPException(status_code=409, detail="La solicitud cambió durante la operación")
        await ledger.insert_one(
            {
                "_id": _stable_id(f"company-redemption:{object_id}:{payload.decision}"),
                "empresa_id": current_user["empresa_id"],
                "usuario_id": redemption["usuario_id"],
                "tipo": "canje_aprobado" if payload.decision == "aprobar" else "canje_rechazado",
                "puntos": redemption["puntos_requeridos"],
                "saldo_delta": 0 if payload.decision == "aprobar" else redemption["puntos_requeridos"],
                "redemption_id": object_id,
                "snapshot_recompensa": redemption["snapshot_recompensa"],
                "actor_id": current_user["_id"],
                "created_at": now,
            },
            session=session,
        )
        redemption["estado"] = "aprobada" if payload.decision == "aprobar" else "rechazada"
        redemption["decision"] = payload.decision
        redemption["nota_decision"] = payload.nota
        redemption["decided_at"] = now
        return redemption

    try:
        async with await db.client.start_session() as session:
            result = await session.with_transaction(decide)
    except HTTPException:
        raise
    except PyMongoError as error:
        if error.has_error_label("TransientTransactionError") or error.has_error_label(
            "UnknownTransactionCommitResult"
        ):
            raise HTTPException(status_code=409, detail="Conflicto al atender el canje; consulta su estado") from error
        raise
    return _serialize(result)


async def _client_rewards_payload(current_user, session=None):
    company_id = current_user["empresa_id"]
    user_id = current_user["_id"]
    rule = await get_collection("company_reward_rules").find_one(
        {
            "_id": company_id,
            "empresa_id": company_id,
            "activa": True,
        },
        session=session,
    )
    balance = await get_collection("company_reward_balances").find_one(
        {"_id": _balance_id(company_id, user_id), "empresa_id": company_id, "usuario_id": user_id},
        session=session,
    )
    now = datetime.utcnow()
    catalog_cursor = get_collection("company_reward_catalog").find(
        {
            "empresa_id": company_id,
            "activa": True,
            "$or": [
                {"fecha_finalizacion": None},
                {"fecha_finalizacion": {"$gte": now}},
            ],
        },
        session=session,
    ).sort("created_at", -1)
    redemptions_cursor = get_collection("company_reward_redemptions").find(
        {"empresa_id": company_id, "usuario_id": user_id},
        session=session,
    ).sort("created_at", -1).limit(200)
    ledger_cursor = get_collection("company_reward_ledger").find(
        {"empresa_id": company_id, "usuario_id": user_id},
        session=session,
    ).sort("created_at", -1).limit(200)
    return {
        "puntos_disponibles": (balance or {}).get("puntos_disponibles", 0),
        "puntos_reservados": (balance or {}).get("puntos_reservados", 0),
        "puntos_por_carga": (rule or {}).get("puntos_por_carga"),
        "catalogo": [_serialize(item) async for item in catalog_cursor],
        "solicitudes": [_serialize(item) async for item in redemptions_cursor],
        "movimientos": [_serialize(item) async for item in ledger_cursor],
    }


@client_router.get("")
async def get_my_company_rewards(current_user=Depends(require_client)):
    return await _client_rewards_payload(current_user)


@client_router.get("/redemptions")
async def list_my_company_redemptions(current_user=Depends(require_client)):
    cursor = get_collection("company_reward_redemptions").find(
        {
            "empresa_id": current_user["empresa_id"],
            "usuario_id": current_user["_id"],
        }
    ).sort("created_at", -1).limit(200)
    return [_serialize(item) async for item in cursor]


@client_router.post("/redemptions", status_code=status.HTTP_201_CREATED)
async def request_company_redemption(
    payload: CompanyRedemptionRequest,
    current_user=Depends(require_client),
):
    company_id = current_user["empresa_id"]
    user_id = current_user["_id"]
    reward_id = parse_object_id(payload.recompensa_id, "recompensa")
    catalog = get_collection("company_reward_catalog")
    balances = get_collection("company_reward_balances")
    redemptions = get_collection("company_reward_redemptions")
    ledger = get_collection("company_reward_ledger")
    now = datetime.utcnow()
    request_id = ObjectId()

    async def request(session):
        reward = await catalog.find_one(
            {
                "_id": reward_id,
                "empresa_id": company_id,
                "activa": True,
                "$or": [
                    {"fecha_finalizacion": None},
                    {"fecha_finalizacion": {"$gte": now}},
                ],
            },
            session=session,
        )
        if reward is None:
            raise HTTPException(status_code=404, detail="Recompensa no disponible")
        pending = await redemptions.find_one(
            {
                "empresa_id": company_id,
                "usuario_id": user_id,
                "recompensa_id": reward_id,
                "estado": "pendiente",
            },
            session=session,
        )
        if pending:
            raise HTTPException(status_code=409, detail="Ya tienes una solicitud pendiente para esta recompensa")
        points = reward["puntos_requeridos"]
        debit = await balances.update_one(
            {
                "_id": _balance_id(company_id, user_id),
                "empresa_id": company_id,
                "usuario_id": user_id,
                "puntos_disponibles": {"$gte": points},
            },
            {
                "$inc": {
                    "puntos_disponibles": -points,
                    "puntos_reservados": points,
                },
                "$set": {"updated_at": now},
            },
            session=session,
        )
        if debit.matched_count != 1:
            raise HTTPException(status_code=409, detail="No tienes puntos suficientes")
        redemption = {
            "_id": request_id,
            "empresa_id": company_id,
            "usuario_id": user_id,
            "recompensa_id": reward_id,
            "estado": "pendiente",
            "pending_key": f"{user_id}:{reward_id}",
            "puntos_requeridos": points,
            "snapshot_recompensa": {
                key: reward.get(key)
                for key in ("nombre", "descripcion", "condiciones", "puntos_requeridos")
            },
            "created_at": now,
        }
        await redemptions.insert_one(redemption, session=session)
        await ledger.insert_one(
            {
                "_id": _stable_id(f"company-redemption:{request_id}:requested"),
                "empresa_id": company_id,
                "usuario_id": user_id,
                "tipo": "canje_solicitado",
                "puntos": points,
                "saldo_delta": -points,
                "reservado_delta": points,
                "redemption_id": request_id,
                "snapshot_recompensa": redemption["snapshot_recompensa"],
                "actor_id": user_id,
                "created_at": now,
            },
            session=session,
        )
        return redemption

    try:
        async with await db.client.start_session() as session:
            result = await session.with_transaction(request)
    except HTTPException:
        raise
    except DuplicateKeyError as error:
        raise HTTPException(status_code=409, detail="Solicitud de canje duplicada") from error
    except PyMongoError as error:
        if error.has_error_label("TransientTransactionError") or error.has_error_label(
            "UnknownTransactionCommitResult"
        ):
            raise HTTPException(status_code=409, detail="Conflicto al solicitar el canje; revisa tu saldo") from error
        raise
    return _serialize(result)
