from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, status
from bson import ObjectId
from app.database import get_collection
from app.auth import require_admin, require_read_access
from app.models.reward import (
    RecompensaCatalogoCreate,
    RecompensaCatalogoUpdate,
    ReglaRecompensaCreate,
    ReglaRecompensaEstado,
    ReglaRecompensaUpdate,
)
from app.validation import parse_object_id

router = APIRouter(prefix="/api/recompensas", tags=["Recompensas"])


def _serialize_value(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    return value


def _serialize_document(document):
    return _serialize_value(document)


def _serialize_rule(rule):
    return _serialize_document(rule)


def _serialize_catalog(catalog):
    return _serialize_document(catalog)


def _validate_catalog_dates(data):
    start = data.get("fecha_inicio")
    end = data.get("fecha_finalizacion")
    if start and end and start > end:
        raise HTTPException(
            status_code=422,
            detail="La fecha de inicio no puede ser posterior a la fecha de finalización",
        )


async def _get_catalog(catalog_id):
    catalog = await get_collection("recompensa_catalogo").find_one({"_id": catalog_id})
    if catalog is None:
        raise HTTPException(status_code=404, detail="Recompensa de catálogo no encontrada")
    return catalog


def _catalog_allows_active_rule(catalog):
    now = datetime.utcnow()
    return (
        catalog.get("activa", False)
        and catalog.get("fecha_inicio") <= now <= catalog.get("fecha_finalizacion")
    )


async def _ensure_active_rule_allowed(catalog_id):
    catalog = await _get_catalog(catalog_id)
    if not _catalog_allows_active_rule(catalog):
        raise HTTPException(
            status_code=409,
            detail="La recompensa debe estar activa y dentro de su vigencia para crear una regla activa",
        )
    return catalog


async def _ensure_no_active_rule_duplicate(cargas_requeridas, catalog_id, exclude_id=None):
    query = {
        "cargas_requeridas": cargas_requeridas,
        "recompensa_catalogo_id": catalog_id,
        "activa": True,
    }
    if exclude_id:
        query["_id"] = {"$ne": exclude_id}
    if await get_collection("recompensa_reglas").find_one(query):
        raise HTTPException(
            status_code=409,
            detail="Ya existe una regla activa con esas cargas y esa recompensa",
        )


def _validate_catalog_update_dates(existing, update_data):
    dates = {
        "fecha_inicio": update_data.get("fecha_inicio", existing.get("fecha_inicio")),
        "fecha_finalizacion": update_data.get(
            "fecha_finalizacion", existing.get("fecha_finalizacion")
        ),
    }
    _validate_catalog_dates(dates)


@router.get("/catalogo")
async def list_reward_catalog(current_user=Depends(require_read_access)):
    cursor = get_collection("recompensa_catalogo").find().sort("nombre", 1)
    return [_serialize_catalog(catalog) async for catalog in cursor]


@router.post("/catalogo", status_code=status.HTTP_201_CREATED)
async def create_reward_catalog(
    catalog: RecompensaCatalogoCreate,
    current_user=Depends(require_admin),
):
    duplicate = await get_collection("recompensa_catalogo").find_one(
        {"nombre": catalog.nombre, "empresa": catalog.empresa}
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail="Ya existe una recompensa con el mismo nombre y empresa",
        )
    catalog_data = catalog.model_dump()
    now = datetime.utcnow()
    catalog_data["fecha_creacion"] = now
    catalog_data["fecha_actualizacion"] = now
    result = await get_collection("recompensa_catalogo").insert_one(catalog_data)
    created = await get_collection("recompensa_catalogo").find_one({"_id": result.inserted_id})
    return _serialize_catalog(created)


@router.put("/catalogo/{catalog_id}")
async def update_reward_catalog(
    catalog_id: str,
    catalog: RecompensaCatalogoUpdate,
    current_user=Depends(require_admin),
):
    object_id = parse_object_id(catalog_id, "recompensa")
    existing = await _get_catalog(object_id)
    update_data = catalog.model_dump(exclude_unset=True)
    _validate_catalog_update_dates(existing, update_data)
    name = update_data.get("nombre", existing.get("nombre"))
    company = update_data.get("empresa", existing.get("empresa"))
    duplicate = await get_collection("recompensa_catalogo").find_one(
        {"nombre": name, "empresa": company, "_id": {"$ne": object_id}}
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail="Ya existe otra recompensa con el mismo nombre y empresa",
        )
    update_data["fecha_actualizacion"] = datetime.utcnow()
    await get_collection("recompensa_catalogo").update_one(
        {"_id": object_id}, {"$set": update_data}
    )
    updated = await get_collection("recompensa_catalogo").find_one({"_id": object_id})
    return _serialize_catalog(updated)


@router.put("/catalogo/{catalog_id}/estado")
async def update_reward_catalog_status(
    catalog_id: str,
    state: ReglaRecompensaEstado,
    current_user=Depends(require_admin),
):
    object_id = parse_object_id(catalog_id, "recompensa")
    await _get_catalog(object_id)
    await get_collection("recompensa_catalogo").update_one(
        {"_id": object_id},
        {"$set": {"activa": state.activa, "fecha_actualizacion": datetime.utcnow()}},
    )
    updated = await get_collection("recompensa_catalogo").find_one({"_id": object_id})
    return _serialize_catalog(updated)


@router.get("/reglas")
async def list_reward_rules(current_user=Depends(require_read_access)):
    cursor = get_collection("recompensa_reglas").find().sort("cargas_requeridas", 1)
    rules = []
    async for rule in cursor:
        rule = _serialize_rule(rule)
        catalog = await get_collection("recompensa_catalogo").find_one(
            {"_id": ObjectId(rule["recompensa_catalogo_id"])}
        )
        rule["recompensa"] = _serialize_catalog(catalog) if catalog else None
        rules.append(rule)
    return rules


@router.post("/reglas", status_code=status.HTTP_201_CREATED)
async def create_reward_rule(rule: ReglaRecompensaCreate, current_user=Depends(require_admin)):
    if rule.activa:
        await _ensure_active_rule_allowed(rule.recompensa_catalogo_id)
    else:
        await _get_catalog(rule.recompensa_catalogo_id)
    await _ensure_no_active_rule_duplicate(rule.cargas_requeridas, rule.recompensa_catalogo_id)
    rule_data = rule.model_dump()
    now = datetime.utcnow()
    rule_data["fecha_creacion"] = now
    rule_data["fecha_actualizacion"] = now
    result = await get_collection("recompensa_reglas").insert_one(rule_data)
    created = await get_collection("recompensa_reglas").find_one({"_id": result.inserted_id})
    return _serialize_rule(created)


@router.put("/reglas/{rule_id}")
async def update_reward_rule(
    rule_id: str,
    rule: ReglaRecompensaUpdate,
    current_user=Depends(require_admin),
):
    object_id = parse_object_id(rule_id, "regla")
    existing = await get_collection("recompensa_reglas").find_one({"_id": object_id})
    if existing is None:
        raise HTTPException(status_code=404, detail="Regla de recompensa no encontrada")
    update_data = rule.model_dump(exclude_unset=True)
    next_catalog_id = update_data.get("recompensa_catalogo_id", existing["recompensa_catalogo_id"])
    next_active = update_data.get("activa", existing.get("activa", True))
    if next_active:
        await _ensure_active_rule_allowed(next_catalog_id)
    else:
        await _get_catalog(next_catalog_id)
    await _ensure_no_active_rule_duplicate(
        update_data.get("cargas_requeridas", existing["cargas_requeridas"]),
        next_catalog_id,
        exclude_id=object_id,
    )
    update_data["fecha_actualizacion"] = datetime.utcnow()
    await get_collection("recompensa_reglas").update_one(
        {"_id": object_id}, {"$set": update_data}
    )
    updated = await get_collection("recompensa_reglas").find_one({"_id": object_id})
    return _serialize_rule(updated)


@router.put("/reglas/{rule_id}/estado")
async def update_reward_rule_status(
    rule_id: str,
    state: ReglaRecompensaEstado,
    current_user=Depends(require_admin),
):
    object_id = parse_object_id(rule_id, "regla")
    existing = await get_collection("recompensa_reglas").find_one({"_id": object_id})
    if existing is None:
        raise HTTPException(status_code=404, detail="Regla de recompensa no encontrada")
    if state.activa:
        await _ensure_active_rule_allowed(existing["recompensa_catalogo_id"])
        await _ensure_no_active_rule_duplicate(
            existing["cargas_requeridas"], existing["recompensa_catalogo_id"], exclude_id=object_id
        )
    await get_collection("recompensa_reglas").update_one(
        {"_id": object_id},
        {"$set": {"activa": state.activa, "fecha_actualizacion": datetime.utcnow()}},
    )
    updated = await get_collection("recompensa_reglas").find_one({"_id": object_id})
    return _serialize_rule(updated)

@router.get("/")
async def list_rewards(usuario_id: str = None, current_user=Depends(require_read_access)):
    query = {}
    if usuario_id:
        query["usuario_id"] = parse_object_id(usuario_id, "usuario")

    cursor = get_collection("recompensas").find(query).sort("fecha_otorgamiento", -1)
    rewards = []
    async for r in cursor:
        rewards.append(_serialize_document(r))
    return rewards

@router.get("/usuario/{user_id}")
async def get_user_rewards(user_id: str, current_user=Depends(require_read_access)):
    cursor = get_collection("recompensas").find(
        {"usuario_id": parse_object_id(user_id, "usuario")}
    ).sort("fecha_otorgamiento", -1)

    disponibles = []
    usadas = []

    async for r in cursor:
        r = _serialize_document(r)

        if r["usado"]:
            usadas.append(r)
        else:
            disponibles.append(r)

    return {
        "disponibles": disponibles,
        "usadas": usadas,
        "total_disponibles": len(disponibles),
        "total_usadas": len(usadas)
    }