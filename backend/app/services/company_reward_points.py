import hashlib
from datetime import datetime

from bson import ObjectId
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.database import db, get_collection


def _stable_id(value: str) -> ObjectId:
    return ObjectId(hashlib.sha256(value.encode("utf-8")).digest()[:12])


def _balance_id(company_id: ObjectId, user_id: ObjectId) -> ObjectId:
    return _stable_id(f"company-reward-balance:{company_id}:{user_id}")


async def accrue_points_for_completed_session(session_document: dict) -> int:
    company_id = session_document.get("empresa_id")
    user_id = session_document.get("usuario_id")
    session_id = session_document.get("_id")
    if (
        session_document.get("estado") != "finalizada"
        or not isinstance(company_id, ObjectId)
        or not isinstance(user_id, ObjectId)
        or not isinstance(session_id, ObjectId)
    ):
        return 0

    companies = get_collection("empresas")
    users = get_collection("usuarios")
    rules = get_collection("company_reward_rules")
    balances = get_collection("company_reward_balances")
    ledger = get_collection("company_reward_ledger")
    now = datetime.utcnow()

    async def apply(session):
        company = await companies.find_one(
            {"_id": company_id, "activo": True},
            session=session,
        )
        user = await users.find_one(
            {
                "_id": user_id,
                "empresa_id": company_id,
                "rol": "cliente",
                "activo": {"$ne": False},
            },
            session=session,
        )
        rule = await rules.find_one(
            {"_id": company_id, "empresa_id": company_id, "activa": True},
            session=session,
        )
        if company is None or user is None or rule is None:
            return 0
        energy = max(0.0, float(session_document.get("energia_consumida_kwh") or 0))
        if energy < rule.get("energia_minima_kwh", 0):
            return 0

        points = int(rule["puntos_por_carga"])
        ledger_id = _stable_id(
            f"company-reward-accrual:{company_id}:{session_id}:{rule['_id']}"
        )
        await ledger.insert_one(
            {
                "_id": ledger_id,
                "empresa_id": company_id,
                "usuario_id": user_id,
                "tipo": "acumulacion",
                "puntos": points,
                "saldo_delta": points,
                "session_id": session_id,
                "regla_id": rule["_id"],
                "snapshot_regla": {
                    "puntos_por_carga": rule["puntos_por_carga"],
                    "energia_minima_kwh": rule.get("energia_minima_kwh", 0),
                    "energia_sesion_kwh": energy,
                },
                "created_at": now,
            },
            session=session,
        )
        await balances.update_one(
            {"_id": _balance_id(company_id, user_id)},
            {
                "$setOnInsert": {
                    "empresa_id": company_id,
                    "usuario_id": user_id,
                    "puntos_disponibles": 0,
                    "puntos_reservados": 0,
                    "created_at": now,
                },
                "$inc": {"puntos_disponibles": points},
                "$set": {"updated_at": now},
            },
            upsert=True,
            session=session,
        )
        return points

    try:
        async with await db.client.start_session() as mongo_session:
            return await mongo_session.with_transaction(apply)
    except DuplicateKeyError:
        return 0
    except PyMongoError:
        raise
