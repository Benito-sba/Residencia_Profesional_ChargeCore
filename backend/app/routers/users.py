from datetime import datetime

from fastapi import APIRouter, HTTPException, status, Depends
from app.database import get_collection
from app.auth import get_password_hash, require_admin
from app.models.user import UsuarioCreate, UsuarioUpdate
from app.validation import parse_object_id

router = APIRouter(prefix="/api/usuarios", tags=["Usuarios"])

@router.get("/")
async def list_users(skip: int = 0, limit: int = 100, current_user=Depends(require_admin)):
    cursor = get_collection("usuarios").find().skip(skip).limit(limit)
    users = []
    async for user in cursor:
        user["_id"] = str(user["_id"])
        user.pop("password", None)
        users.append(user)
    return users

@router.get("/{user_id}")
async def get_user(user_id: str, current_user=Depends(require_admin)):
    user = await get_collection("usuarios").find_one(
        {"_id": parse_object_id(user_id, "usuario")}
    )
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    user["_id"] = str(user["_id"])
    user.pop("password", None)
    return user

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_user(user: UsuarioCreate, current_user=Depends(require_admin)):
    existing = await get_collection("usuarios").find_one({"email": user.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email ya registrado")

    user_dict = user.model_dump()
    user_dict["password"] = get_password_hash(user_dict["password"])
    result = await get_collection("usuarios").insert_one(user_dict)
    return {"id": str(result.inserted_id)}

@router.put("/{user_id}")
async def update_user(user_id: str, user: UsuarioUpdate, current_user=Depends(require_admin)):
    update_data = {k: v for k, v in user.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No hay datos para actualizar")

    users = get_collection("usuarios")
    object_id = parse_object_id(user_id, "usuario")
    existing = await users.find_one({"_id": object_id})
    if existing is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if "email" in update_data:
        duplicate = await users.find_one({
            "email": update_data["email"],
            "_id": {"$ne": object_id},
        })
        if duplicate:
            raise HTTPException(status_code=409, detail="Email ya registrado")

    is_active_admin = (
        existing.get("rol") == "admin" and existing.get("activo", True)
    )
    removes_active_admin = (
        is_active_admin
        and (
            update_data.get("rol", existing.get("rol")) != "admin"
            or not update_data.get("activo", existing.get("activo", True))
        )
    )
    if removes_active_admin and await users.count_documents({
        "rol": "admin",
        "activo": {"$ne": False},
    }) <= 1:
        raise HTTPException(
            status_code=409,
            detail="No se puede desactivar o degradar al último administrador activo",
        )

    if "password" in update_data:
        update_data["password"] = get_password_hash(update_data["password"])
    if update_data.get("activo") is False:
        update_data.setdefault("fecha_baja", datetime.utcnow())

    result = await users.update_one(
        {"_id": object_id},
        {"$set": update_data},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return {"mensaje": "Usuario actualizado"}

@router.delete("/{user_id}")
async def delete_user(user_id: str, current_user=Depends(require_admin)):
    users = get_collection("usuarios")
    object_id = parse_object_id(user_id, "usuario", status_code=400)
    existing = await users.find_one({"_id": object_id})
    if existing is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if (
        existing.get("rol") == "admin"
        and existing.get("activo", True)
        and await users.count_documents({
            "rol": "admin",
            "activo": {"$ne": False},
        }) <= 1
    ):
        raise HTTPException(
            status_code=409,
            detail="No se puede eliminar al último administrador activo",
        )

    result = await users.update_one(
        {"_id": object_id, "activo": {"$ne": False}},
        {"$set": {"activo": False, "fecha_baja": datetime.utcnow()}},
    )
    if result.matched_count == 0:
        return {"mensaje": "El usuario ya estaba desactivado"}
    return {"mensaje": "Usuario desactivado; historial conservado"}