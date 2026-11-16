from datetime import datetime
import hashlib
from bson import ObjectId
from app.database import db, get_collection
from pymongo.errors import DuplicateKeyError


def _stable_reward_id(
    usuario_id: ObjectId,
    regla_id: ObjectId,
    cargas_requeridas: int,
) -> ObjectId:
    stable_key = f"{usuario_id}:{regla_id}:{cargas_requeridas}".encode()
    return ObjectId(hashlib.sha256(stable_key).digest()[:12])


async def get_finalized_session_count(usuario_id: ObjectId, sessions=None) -> int:
    sessions = sessions if sessions is not None else get_collection("sesiones")
    return await sessions.count_documents({
        "usuario_id": usuario_id,
        "estado": "finalizada",
    })


async def verificar_y_otorgar_recompensa(sesion: dict) -> int:
    """Cuenta cierres posteriores al corte; conserva premios y no reconstruye hitos previos."""
    if not isinstance(sesion, dict):
        return 0

    usuario_id = sesion.get("usuario_id")
    sesion_id = sesion.get("_id")
    cierre = sesion.get("stop_closure")
    if (
        sesion.get("estado") != "finalizada"
        or not isinstance(usuario_id, ObjectId)
        or not isinstance(sesion_id, ObjectId)
        or not isinstance(sesion.get("fecha_fin"), datetime)
        or not isinstance(cierre, dict)
        or cierre.get("rewards_granted") is True
    ):
        return 0

    usuarios_col = get_collection("usuarios")
    recompensas_col = get_collection("recompensas")
    progreso_col = get_collection("recompensa_progreso")
    reglas_col = get_collection("recompensa_reglas")
    catalogo_col = get_collection("recompensa_catalogo")

    ahora = datetime.utcnow()
    event_id = f"legacy-reward-session:{sesion_id}"

    async def process_session(mongo_session):
        usuario = await usuarios_col.find_one(
            {"_id": usuario_id}, session=mongo_session
        )
        if usuario is None:
            return 0

        processed = await progreso_col.find_one(
            {"_id": event_id}, session=mongo_session
        )
        if processed is not None:
            return processed["cargas_nuevas"]

        progreso = await progreso_col.find_one(
            {"_id": usuario_id}, session=mongo_session
        )
        cargas_nuevas = (progreso or {}).get("cargas_nuevas", 0) + 1
        await progreso_col.insert_one(
            {
                "_id": event_id,
                "tipo": "sesion_procesada",
                "usuario_id": usuario_id,
                "sesion_id": sesion_id,
                "cargas_nuevas": cargas_nuevas,
                "created_at": ahora,
            },
            session=mongo_session,
        )
        result = await progreso_col.update_one(
            {"_id": usuario_id},
            {
                "$inc": {"cargas_nuevas": 1},
                "$set": {"updated_at": ahora},
                "$setOnInsert": {"created_at": ahora},
            },
            upsert=True,
            session=mongo_session,
        )
        if result.matched_count != 1 and result.upserted_id != usuario_id:
            raise RuntimeError("No se pudo persistir el progreso de recompensas")

        reglas = reglas_col.find(
            {
                "activa": True,
                "cargas_requeridas": {"$lte": cargas_nuevas},
            },
            session=mongo_session,
        )
        async for regla in reglas:
            milestone = regla["cargas_requeridas"]
            catalogo = await catalogo_col.find_one(
                {
                    "_id": regla["recompensa_catalogo_id"],
                    "activa": True,
                    "fecha_inicio": {"$lte": ahora},
                    "fecha_finalizacion": {"$gte": ahora},
                },
                session=mongo_session,
            )
            if catalogo is None:
                continue

            existe = await recompensas_col.find_one(
                {
                    "usuario_id": usuario_id,
                    "recompensa_regla_id": regla["_id"],
                    "cargas_requeridas": milestone,
                },
                session=mongo_session,
            )
            if existe:
                continue

            recompensa_id = _stable_reward_id(
                usuario_id, regla["_id"], milestone
            )
            await recompensas_col.update_one(
                {"_id": recompensa_id},
                {"$setOnInsert": {
                    "_id": recompensa_id,
                    "usuario_id": usuario_id,
                    "tipo": "carga_gratuita",
                    "descripcion": catalogo["descripcion"],
                    "cargas_requeridas": milestone,
                    "cargas_acumuladas": cargas_nuevas,
                    "usado": False,
                    "fecha_otorgamiento": ahora,
                    "fecha_uso": None,
                    "sesion_id_uso": None,
                    "recompensa_catalogo_id": catalogo["_id"],
                    "recompensa_regla_id": regla["_id"],
                    "snapshot_nombre": catalogo["nombre"],
                    "snapshot_empresa": catalogo["empresa"],
                    "snapshot_descripcion": catalogo["descripcion"],
                    "snapshot_condiciones": catalogo.get("condiciones", ""),
                    "snapshot_cargas_requeridas": regla["cargas_requeridas"],
                    "snapshot_fecha_inicio": catalogo["fecha_inicio"],
                    "snapshot_fecha_finalizacion": catalogo["fecha_finalizacion"],
                }},
                upsert=True,
                session=mongo_session,
            )
        return cargas_nuevas

    try:
        async with await db.client.start_session() as mongo_session:
            return await mongo_session.with_transaction(process_session)
    except DuplicateKeyError:
        processed = await progreso_col.find_one({"_id": event_id})
        if processed is None:
            raise
        return processed["cargas_nuevas"]


async def aplicar_recompensa_en_sesion(usuario_id: str, sesion_id: str):
    recompensas_col = get_collection("recompensas")
    sesiones_col = get_collection("sesiones")

    usuario_object_id = ObjectId(usuario_id)
    sesion_object_id = ObjectId(sesion_id)
    sesion = await sesiones_col.find_one({"_id": sesion_object_id})
    if sesion is None:
        raise ValueError(f"No existe la sesión {sesion_id} para aplicar recompensa")

    cierre = sesion.get("stop_closure")
    if not isinstance(cierre, dict):
        raise RuntimeError(
            f"La sesión {sesion_id} no tiene estado de selección de recompensa"
        )

    if not cierre.get("reward_selection_complete"):
        return {
            "tipo": "ninguna",
            "mensaje": "No se seleccionó una recompensa para esta sesión",
        }

    recompensa_id = cierre.get("reward_id")
    if recompensa_id is None:
        beneficio = sesion.get("beneficio_aplicado")
        if beneficio:
            return {
                "tipo": (
                    "carga_gratuita"
                    if sesion.get("es_carga_gratuita")
                    else "beneficio_especial"
                ),
                "mensaje": beneficio,
            }
        return {"tipo": "ninguna", "mensaje": "No hay recompensas disponibles"}

    recompensa = await recompensas_col.find_one({
        "_id": recompensa_id,
        "usuario_id": usuario_object_id,
    })
    if recompensa is None:
        raise RuntimeError(
            f"La recompensa seleccionada {recompensa_id} ya no existe"
        )

    if (
        not recompensa.get("usado")
        or recompensa.get("sesion_id_uso") != sesion_object_id
    ):
        claim = await recompensas_col.update_one(
            {
                "_id": recompensa_id,
                "usado": {"$ne": True},
                "$or": [
                    {"reservada_para_sesion": {"$exists": False}},
                    {"reservada_para_sesion": None},
                    {"reservada_para_sesion": sesion_object_id},
                ],
            },
            {"$set": {
                "usado": True,
                "fecha_uso": datetime.utcnow(),
                "sesion_id_uso": sesion_object_id,
            }, "$unset": {"reservada_para_sesion": ""}},
        )
        if claim.modified_count != 1:
            recompensa = await recompensas_col.find_one({"_id": recompensa_id})
            if (
                recompensa is None
                or not recompensa.get("usado")
                or recompensa.get("sesion_id_uso") != sesion_object_id
            ):
                await sesiones_col.update_one(
                    {
                        "_id": sesion_object_id,
                        "stop_closure.reward_selection_complete": True,
                        "stop_closure.reward_id": recompensa_id,
                    },
                    {"$set": {
                        "stop_closure.reward_selection_complete": False,
                        "stop_closure.reward_id": None,
                    }},
                )
                raise RuntimeError(
                    f"No se pudo reclamar la recompensa {recompensa_id} "
                    f"para la sesión {sesion_id}; se requiere reintento"
                )

    if recompensa["tipo"] == "carga_gratuita":
        session_fields = {
            "es_carga_gratuita": True,
            "beneficio_aplicado": "Carga gratuita por recompensa",
        }
        result = {
            "tipo": "carga_gratuita",
            "mensaje": "Carga gratuita aplicada",
        }
    else:
        session_fields = {
            "beneficio_aplicado": "Beneficio especial por recompensa",
        }
        result = {
            "tipo": "beneficio_especial",
            "mensaje": "Beneficio especial aplicado",
        }

    applied = await sesiones_col.update_one(
        {"_id": sesion_object_id},
        {"$set": session_fields},
    )
    if applied.matched_count != 1:
        raise RuntimeError(
            f"No se pudo guardar la recompensa en la sesión {sesion_id}"
        )

    return result