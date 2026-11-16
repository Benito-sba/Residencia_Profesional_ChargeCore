from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from app.config import settings

class Database:
    client: AsyncIOMotorClient = None

db = Database()

async def connect_db():
    db.client = AsyncIOMotorClient(settings.MONGODB_URL)
    database = get_database()
    topology = await database.command("hello")
    if not topology.get("setName") and topology.get("msg") != "isdbgrid":
        raise RuntimeError(
            "MongoDB debe ser un replica set o clúster compatible "
            "con transacciones para aceptar conexiones OCPP"
        )
    await database.usuarios.create_index("email", unique=True)
    await database.nfc_cards.create_index("uid", unique=True)
    await database.nfc_charge_requests.create_index(
        "expires_at", expireAfterSeconds=0
    )
    await database.nfc_charge_requests.create_index(
        [("usuario_id", 1), ("status", 1), ("created_at", -1)]
    )
    await database.cargadores.create_index("charge_point_id", unique=True)
    await database.empresas.create_index(
        "nombre_normalizado",
        unique=True,
        partialFilterExpression={"nombre_normalizado": {"$type": "string"}},
        name="unique_company_normalized_name",
    )
    await database.cargador_eliminaciones.create_index("deleted_at")
    await database.sesiones.create_index("transaction_id", unique=True)
    await database.sesiones.create_index([("charge_point_id", 1), ("estado", 1)])
    await database.sesiones.create_index(
        [("usuario_id", 1)],
        unique=True,
        partialFilterExpression={
            "estado": {"$in": ["activa", "iniciada"]},
            "usuario_id": {"$type": "objectId"},
        },
        name="one_active_session_per_client",
    )
    await database.recompensas.create_index(
        [
            ("usuario_id", 1),
            ("recompensa_regla_id", 1),
            ("cargas_requeridas", 1),
        ],
        unique=True,
        partialFilterExpression={
            "usuario_id": {"$type": "objectId"},
            "recompensa_regla_id": {"$type": "objectId"},
            "cargas_requeridas": {"$type": "number"},
        },
        name="one_reward_per_user_rule_milestone",
    )
    await database.commercial_benefits.create_index(
        [("activa", 1), ("created_at", -1)],
        name="commercial_benefits_active_created",
    )
    await database.commercial_benefit_assignments.create_index(
        [("benefit_id", 1), ("empresa_id", 1), ("assignment_request_id", 1)],
        unique=True,
        partialFilterExpression={
            "benefit_id": {"$type": "objectId"},
            "empresa_id": {"$type": "objectId"},
            "assignment_request_id": {"$type": "string"},
        },
        name="commercial_assignment_request_unique",
    )
    await database.commercial_benefit_assignments.create_index(
        [("empresa_id", 1), ("assigned_at", -1)],
        name="commercial_assignments_company_date",
    )
    await database.commercial_benefit_assignments.create_index(
        [("benefit_id", 1), ("assigned_at", -1)],
        name="commercial_assignments_benefit_date",
    )
    await database.commercial_benefit_movements.create_index(
        [("empresa_id", 1), ("assignment_id", 1), ("created_at", -1)],
        name="commercial_movements_company_assignment_date",
    )
    await database.company_reward_catalog.create_index(
        [("empresa_id", 1), ("activa", 1), ("created_at", -1)],
        name="company_reward_catalog_company_active_date",
    )
    await database.company_reward_catalog.create_index(
        [("empresa_id", 1), ("fecha_finalizacion", 1)],
        name="company_reward_catalog_company_expiration",
    )
    await database.company_reward_balances.create_index(
        [("empresa_id", 1), ("usuario_id", 1)],
        unique=True,
        partialFilterExpression={
            "empresa_id": {"$type": "objectId"},
            "usuario_id": {"$type": "objectId"},
        },
        name="company_reward_balance_owner_unique",
    )
    await database.company_reward_balances.create_index(
        [("empresa_id", 1), ("updated_at", -1)],
        name="company_reward_balances_company_updated",
    )
    await database.company_reward_ledger.create_index(
        [("empresa_id", 1), ("session_id", 1)],
        unique=True,
        partialFilterExpression={
            "empresa_id": {"$type": "objectId"},
            "session_id": {"$type": "objectId"},
            "tipo": "acumulacion",
        },
        name="company_reward_accrual_session_unique",
    )
    await database.company_reward_ledger.create_index(
        [("empresa_id", 1), ("usuario_id", 1), ("created_at", -1)],
        name="company_reward_ledger_company_user_date",
    )
    await database.company_reward_redemptions.create_index(
        [("empresa_id", 1), ("pending_key", 1)],
        unique=True,
        partialFilterExpression={"pending_key": {"$type": "string"}},
        name="company_reward_pending_request_unique",
    )
    await database.company_reward_redemptions.create_index(
        [("empresa_id", 1), ("usuario_id", 1), ("created_at", -1)],
        name="company_reward_redemptions_company_user_date",
    )
    await database.company_reward_redemptions.create_index(
        [("empresa_id", 1), ("estado", 1), ("created_at", -1)],
        name="company_reward_redemptions_company_state_date",
    )
    await database.meter_values.create_index([("transaction_id", 1), ("timestamp", -1)])
    await database.telemetria.create_index([("charge_point_id", 1), ("timestamp", -1)])
    await database.eventos_ocpp.create_index([("charge_point_id", 1), ("timestamp", -1)])
    await database.ocpp_messages.create_index([("charge_point_id", 1), ("timestamp", -1)])
    await database.ocpp_messages.create_index([("charge_point_id", 1), ("unique_id", 1)])
    print(f"✅ Conectado a MongoDB Atlas: {settings.DATABASE_NAME}")

async def close_db():
    if db.client:
        db.client.close()
        print("🔌 Desconectado de MongoDB")

def get_database():
    return db.client[settings.DATABASE_NAME]

def get_collection(name: str):
    return get_database()[name]


async def serialize_company_change(
    company_id,
    session,
    companies=None,
    *,
    require_active: bool = False,
):
    if companies is None:
        companies = get_collection("empresas")
    query = {"_id": company_id}
    if require_active:
        query["activo"] = True
        query["estado"] = {"$ne": "suspendida"}
    return await companies.update_one(
        query,
        {"$inc": {"owner_change_version": 1}},
        session=session,
    )


async def next_transaction_id() -> int:
    result = await get_collection("counters").find_one_and_update(
        {"_id": "transaction_id"},
        [{"$set": {"value": {"$add": [{"$ifNull": ["$value", 999]}, 1]}}}],
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(result["value"])