from datetime import datetime
import hashlib
from bson import ObjectId
from app.database import get_collection


def _stable_reward_id(
    usuario_id: ObjectId,
    regla_id: ObjectId,
    cargas_requeridas: int,
) -> ObjectId:
    stable_key = f"{usuario_id}:{regla_id}:{cargas_requeridas}".encode()
    return ObjectId(hashlib.sha256(stable_key).digest()[:12])


async def verificar_y_otorgar_recompensa(usuario_id: str):
    usuarios_col = get_collection("usuarios")
    recompensas_col = get_collection("recompensas")
    sesiones_col = get_collection("sesiones")
    reglas_col = get_collection("recompensa_reglas")
    catalogo_col = get_collection("recompensa_catalogo")

    usuario_object_id = ObjectId(usuario_id)
    usuario = await usuarios_col.find_one({"_id": usuario_object_id})
    if usuario is None:
        return

    total_cargas = await sesiones_col.count_documents({
        "usuario_id": usuario_object_id,
        "estado": "finalizada"
    })

    await usuarios_col.update_one(
        {"_id": usuario_object_id},
        {"$set": {"total_cargas": total_cargas}}
    )

    if total_cargas == 0:
        return

    ahora = datetime.utcnow()
    reglas = reglas_col.find({
        "activa": True,
        "cargas_requeridas": {"$lte": total_cargas},
    })
    async for regla in reglas:
        milestone = regla["cargas_requeridas"]
        catalogo = await catalogo_col.find_one({
            "_id": regla["recompensa_catalogo_id"],
            "activa": True,
            "fecha_inicio": {"$lte": ahora},
            "fecha_finalizacion": {"$gte": ahora},
        })
        if catalogo is None:
            continue

        existe = await recompensas_col.find_one({
            "usuario_id": usuario_object_id,
            "recompensa_regla_id": regla["_id"],
            "cargas_requeridas": milestone,
        })
        if existe:
            continue

        recompensa_id = _stable_reward_id(
            usuario_object_id, regla["_id"], milestone
        )
        await recompensas_col.update_one(
            {"_id": recompensa_id},
            {"$setOnInsert": {
                "_id": recompensa_id,
                "usuario_id": usuario_object_id,
                "tipo": "carga_gratuita",
                "descripcion": catalogo["descripcion"],
                "cargas_requeridas": milestone,
                "cargas_acumuladas": total_cargas,
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
        )


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