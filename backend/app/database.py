from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from app.config import settings

class Database:
    client: AsyncIOMotorClient = None

db = Database()

async def connect_db():
    db.client = AsyncIOMotorClient(settings.MONGODB_URL)
    database = get_database()
    if not settings.DEBUG:
        topology = await database.command("hello")
        if not topology.get("setName") and topology.get("msg") != "isdbgrid":
            raise RuntimeError(
                "MongoDB debe ser un replica set o clúster compatible "
                "con transacciones en producción"
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
    await database.meter_values.create_index([("transaction_id", 1), ("timestamp", -1)])
    await database.telemetria.create_index([("charge_point_id", 1), ("timestamp", -1)])
    await database.eventos_ocpp.create_index([("charge_point_id", 1), ("timestamp", -1)])
    await database.ocpp_messages.create_index([("charge_point_id", 1), ("timestamp", -1)])
    await database.ocpp_messages.create_index([("charge_point_id", 1), ("unique_id", 1)])
    print(f"✅ Conectado a MongoDB: {settings.DATABASE_NAME}")

async def close_db():
    if db.client:
        db.client.close()
        print("🔌 Desconectado de MongoDB")

def get_database():
    return db.client[settings.DATABASE_NAME]

def get_collection(name: str):
    return get_database()[name]


async def next_transaction_id() -> int:
    result = await get_collection("counters").find_one_and_update(
        {"_id": "transaction_id"},
        [{"$set": {"value": {"$add": [{"$ifNull": ["$value", 999]}, 1]}}}],
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(result["value"])