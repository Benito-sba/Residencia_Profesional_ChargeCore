from datetime import datetime

from fastapi import APIRouter, HTTPException, status, Depends
from app.database import get_collection, get_database
from pymongo.errors import PyMongoError
from app.auth import (
    get_password_hash,
    require_user_management_access,
    validate_legacy_user_assignment,
)
from app.models.user import UsuarioCreate, UsuarioUpdate
from app.services.reward_service import get_finalized_session_count
from app.services.safe_deletion import has_user_history
from app.validation import parse_object_id

router = APIRouter(prefix="/api/usuarios", tags=["Usuarios"])


def _user_scope(current_user: dict) -> dict:
    if current_user.get("rol") == "dueno_empresa":
        return {
            "empresa_id": current_user["empresa_id"],
            "rol": "cliente",
        }
    return {}


async def _update_company_owner_atomically(
    users,
    target_query: dict,
    company_id,
    update_data: dict,
):
    try:
        async with await get_database().client.start_session() as session:
            async with session.start_transaction():
                company_result = await get_collection("empresas").update_one(
                    {"_id": company_id},
                    {"$inc": {"owner_change_version": 1}},
                    session=session,
                )
                if company_result.matched_count == 0:
                    raise HTTPException(
                        status_code=409,
                        detail="No se puede cambiar al dueño sin una empresa válida",
                    )

                active_owners = await users.count_documents(
                    {
                        "rol": "dueno_empresa",
                        "empresa_id": company_id,
                        "activo": {"$ne": False},
                    },
                    session=session,
                )
                if active_owners <= 1:
                    raise HTTPException(
                        status_code=409,
                        detail="No se puede desactivar o degradar al último dueño activo de la empresa",
                    )

                result = await users.update_one(
                    {
                        **target_query,
                        "rol": "dueno_empresa",
                        "empresa_id": company_id,
                        "activo": {"$ne": False},
                    },
                    {"$set": update_data},
                    session=session,
                )
                if result.matched_count == 0:
                    raise HTTPException(
                        status_code=409,
                        detail="La cuenta del dueño cambió durante la operación",
                    )
                return result
    except PyMongoError as exc:
        if (
            exc.has_error_label("TransientTransactionError")
            or exc.has_error_label("UnknownTransactionCommitResult")
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "No se pudo confirmar con certeza el cambio de dueño; "
                    "verifica el estado antes de repetir la operación"
                    if exc.has_error_label("UnknownTransactionCommitResult")
                    else "Hay otro cambio de dueños en curso; vuelve a intentarlo"
                ),
            ) from exc
        raise


@router.get("/")
async def list_users(
    skip: int = 0,
    limit: int = 100,
    current_user=Depends(require_user_management_access),
):
    cursor = (
        get_collection("usuarios")
        .find({
            **_user_scope(current_user),
            "archivado": {"$ne": True},
        })
        .skip(skip)
        .limit(limit)
    )
    users = []
    async for user in cursor:
        user["total_cargas"] = await get_finalized_session_count(
            user["_id"],
            get_collection("sesiones"),
        )
        user["_id"] = str(user["_id"])
        if user.get("empresa_id") is not None:
            user["empresa_id"] = str(user["empresa_id"])
        user.pop("password", None)
        users.append(user)
    return users

@router.get("/{user_id}")
async def get_user(
    user_id: str,
    current_user=Depends(require_user_management_access),
):
    user = await get_collection("usuarios").find_one(
        {
            "_id": parse_object_id(user_id, "usuario"),
            **_user_scope(current_user),
        }
    )
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    user["total_cargas"] = await get_finalized_session_count(
        user["_id"],
        get_collection("sesiones"),
    )
    user["_id"] = str(user["_id"])
    if user.get("empresa_id") is not None:
        user["empresa_id"] = str(user["empresa_id"])
    user.pop("password", None)
    return user

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_user(
    user: UsuarioCreate,
    current_user=Depends(require_user_management_access),
):
    role = current_user.get("rol")
    is_company_owner = role == "dueno_empresa"
    is_superadmin = role == "superadmin"
    if is_company_owner:
        if user.rol != "cliente":
            raise HTTPException(
                status_code=403,
                detail="El dueño de empresa solo puede crear clientes",
            )
        if user.empresa_id is not None:
            raise HTTPException(
                status_code=403,
                detail="La empresa del cliente se determina desde la sesión",
            )
    elif is_superadmin:
        if user.rol not in {"superadmin", "dueno_empresa", "cliente"}:
            raise HTTPException(
                status_code=403,
                detail="Solo se pueden crear cuentas con roles empresariales vigentes",
            )
        if user.rol == "superadmin":
            if user.empresa_id is not None:
                raise HTTPException(
                    status_code=403,
                    detail="El superadministrador debe permanecer sin empresa",
                )
        else:
            if user.empresa_id is None:
                raise HTTPException(
                    status_code=403,
                    detail="El usuario debe pertenecer a una empresa activa",
                )
            company = await get_collection("empresas").find_one({
                "_id": user.empresa_id,
                "activo": True,
            })
            if company is None:
                raise HTTPException(
                    status_code=403,
                    detail="La empresa asignada no está disponible",
                )
    else:
        raise HTTPException(status_code=403, detail="Permisos insuficientes")

    existing = await get_collection("usuarios").find_one({"email": user.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email ya registrado")

    user_dict = user.model_dump()
    if is_company_owner:
        user_dict["empresa_id"] = current_user["empresa_id"]
    user_dict["password"] = get_password_hash(user_dict["password"])
    result = await get_collection("usuarios").insert_one(user_dict)
    return {"id": str(result.inserted_id)}

@router.put("/{user_id}")
async def update_user(
    user_id: str,
    user: UsuarioUpdate,
    current_user=Depends(require_user_management_access),
):
    is_superadmin = current_user.get("rol") == "superadmin"
    if "empresa_id" in user.model_fields_set and not is_superadmin:
        raise HTTPException(
            status_code=403,
            detail="La asignación de empresa requiere el flujo autorizado",
        )
    update_data = {k: v for k, v in user.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No hay datos para actualizar")
    if current_user.get("rol") == "dueno_empresa" and "rol" in update_data:
        raise HTTPException(
            status_code=403,
            detail="El dueño de empresa no puede cambiar el rol de un cliente",
        )

    users = get_collection("usuarios")
    object_id = parse_object_id(user_id, "usuario")
    target_query = {"_id": object_id, **_user_scope(current_user)}
    existing = await users.find_one(target_query)
    if existing is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if existing.get("archivado"):
        raise HTTPException(
            status_code=409,
            detail="No se puede modificar un usuario archivado",
        )

    next_role = update_data.get("rol", existing.get("rol"))
    if is_superadmin:
        company_id_was_set = "empresa_id" in user.model_fields_set
        requested_company_id = user.empresa_id
        effective_company_id = (
            requested_company_id
            if company_id_was_set
            else existing.get("empresa_id")
        )

        if next_role == "superadmin":
            if (
                requested_company_id is not None
                or (
                    existing.get("empresa_id") is not None
                    and not company_id_was_set
                )
            ):
                raise HTTPException(
                    status_code=403,
                    detail="El superadministrador debe permanecer sin empresa",
                )
            update_data["empresa_id"] = None
        elif next_role == "dueno_empresa" and effective_company_id is None:
            raise HTTPException(
                status_code=403,
                detail="El dueño de empresa debe tener una empresa asignada",
            )
        elif next_role not in {"dueno_empresa", "cliente"} and effective_company_id is not None:
            raise HTTPException(
                status_code=403,
                detail="Este rol no puede pertenecer a una empresa",
            )

        if company_id_was_set:
            if (
                requested_company_id is not None
                and next_role not in {"dueno_empresa", "cliente"}
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Este rol no puede pertenecer a una empresa",
                )
            if requested_company_id is not None:
                company = await get_collection("empresas").find_one({
                    "_id": requested_company_id,
                    "activo": True,
                })
                if company is None:
                    raise HTTPException(
                        status_code=403,
                        detail="La empresa asignada no está disponible",
                    )
            update_data["empresa_id"] = requested_company_id
    else:
        validate_legacy_user_assignment(next_role)
        if (
            "rol" in update_data
            and update_data["rol"] != existing.get("rol")
            and existing.get("empresa_id") is not None
        ):
            raise HTTPException(
                status_code=403,
                detail="No se puede cambiar el rol de un usuario asociado a una empresa",
            )

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

    is_active_superadmin = (
        existing.get("rol") == "superadmin" and existing.get("activo", True)
    )
    removes_active_superadmin = (
        is_active_superadmin
        and (
            next_role != "superadmin"
            or not update_data.get("activo", existing.get("activo", True))
        )
    )
    if removes_active_superadmin and await users.count_documents({
        "rol": "superadmin",
        "activo": {"$ne": False},
    }) <= 1:
        raise HTTPException(
            status_code=409,
            detail="No se puede desactivar o degradar al último superadministrador activo",
        )

    is_active_company_owner = (
        existing.get("rol") == "dueno_empresa"
        and existing.get("activo", True)
    )
    next_company_id = update_data.get("empresa_id", existing.get("empresa_id"))
    removes_active_company_owner = (
        is_active_company_owner
        and (
            next_role != "dueno_empresa"
            or not update_data.get("activo", existing.get("activo", True))
            or next_company_id != existing.get("empresa_id")
        )
    )
    if "password" in update_data:
        update_data["password"] = get_password_hash(update_data["password"])
    if update_data.get("activo") is False:
        update_data.setdefault("fecha_baja", datetime.utcnow())

    if removes_active_company_owner:
        result = await _update_company_owner_atomically(
            users,
            target_query,
            existing.get("empresa_id"),
            update_data,
        )
    else:
        result = await users.update_one(
            target_query,
            {"$set": update_data},
        )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return {"mensaje": "Usuario actualizado"}

@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    current_user=Depends(require_user_management_access),
):
    users = get_collection("usuarios")
    object_id = parse_object_id(user_id, "usuario", status_code=400)
    target_query = {"_id": object_id, **_user_scope(current_user)}
    existing = await users.find_one(target_query)
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
    if (
        existing.get("rol") == "superadmin"
        and existing.get("activo", True)
        and await users.count_documents({
            "rol": "superadmin",
            "activo": {"$ne": False},
        }) <= 1
    ):
        raise HTTPException(
            status_code=409,
            detail="No se puede desactivar al último superadministrador activo",
        )
    try:
        async with await get_database().client.start_session() as session:
            async with session.start_transaction():
                existing = await users.find_one(target_query, session=session)
                if existing is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Usuario no encontrado",
                    )
                if existing.get("archivado"):
                    return {"mensaje": "El usuario ya estaba archivado"}

                if (
                    existing.get("rol") == "admin"
                    and existing.get("activo", True)
                    and await users.count_documents(
                        {
                            "rol": "admin",
                            "activo": {"$ne": False},
                        },
                        session=session,
                    ) <= 1
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="No se puede eliminar al último administrador activo",
                    )
                if (
                    existing.get("rol") == "superadmin"
                    and existing.get("activo", True)
                    and await users.count_documents(
                        {
                            "rol": "superadmin",
                            "activo": {"$ne": False},
                        },
                        session=session,
                    ) <= 1
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="No se puede desactivar al último superadministrador activo",
                    )

                user_has_history = (
                    False
                    if existing.get("rol") == "dueno_empresa"
                    else await has_user_history(
                        existing,
                        get_collection,
                        session=session,
                    )
                )
                is_active_company_owner = (
                    existing.get("rol") == "dueno_empresa"
                    and existing.get("activo", True)
                )
                if user_has_history or existing.get("rol") == "dueno_empresa":
                    update_data = {
                        "activo": False,
                        "archivado": True,
                        "fecha_baja": datetime.utcnow(),
                    }
                    if is_active_company_owner:
                        company_id = existing.get("empresa_id")
                        if company_id is None:
                            raise HTTPException(
                                status_code=409,
                                detail="No se puede archivar al dueño sin una empresa válida",
                            )
                        company_result = await get_collection("empresas").update_one(
                            {"_id": company_id},
                            {"$inc": {"owner_change_version": 1}},
                            session=session,
                        )
                        if company_result.matched_count == 0:
                            raise HTTPException(
                                status_code=409,
                                detail="No se puede archivar al dueño sin una empresa válida",
                            )
                        active_owners = await users.count_documents(
                            {
                                "rol": "dueno_empresa",
                                "empresa_id": company_id,
                                "activo": {"$ne": False},
                            },
                            session=session,
                        )
                        if active_owners <= 1:
                            raise HTTPException(
                                status_code=409,
                                detail="No se puede desactivar al último dueño activo de la empresa",
                            )

                    result = await users.update_one(
                        target_query,
                        {"$set": update_data},
                        session=session,
                    )
                    if result.matched_count == 0:
                        raise HTTPException(
                            status_code=409,
                            detail="La cuenta cambió durante la operación",
                        )
                    await get_collection("vehiculos").update_many(
                        {"usuario_id": object_id},
                        {"$set": {
                            "activo": False,
                            "archivado": True,
                            "fecha_baja": update_data["fecha_baja"],
                        }},
                        session=session,
                    )
                    await get_collection("nfc_cards").update_many(
                        {"usuario_id": object_id},
                        {"$set": {
                            "estado": "bloqueada",
                            "archivado": True,
                            "fecha_baja": update_data["fecha_baja"],
                        }},
                        session=session,
                    )
                    return {
                        "mensaje": (
                            "Usuario archivado y desactivado; "
                            "historial conservado para reportes"
                            if user_has_history
                            else (
                                "Usuario archivado y desactivado; "
                                "relación empresarial conservada"
                            )
                        )
                    }

                await get_collection("vehiculos").delete_many(
                    {"usuario_id": object_id},
                    session=session,
                )
                await get_collection("nfc_cards").delete_many(
                    {"usuario_id": object_id},
                    session=session,
                )
                result = await users.delete_one(target_query, session=session)
                if result.deleted_count != 1:
                    raise HTTPException(
                        status_code=404,
                        detail="Usuario no encontrado",
                    )
                return {
                    "mensaje": (
                        "Usuario eliminado permanentemente junto con sus "
                        "vehículos y tarjetas sin historial"
                    )
                }
    except PyMongoError as exc:
        if exc.has_error_label("TransientTransactionError"):
            raise HTTPException(
                status_code=409,
                detail="Hay otra eliminación en curso; vuelve a intentarlo",
            ) from exc
        if exc.has_error_label("UnknownTransactionCommitResult"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "No se pudo confirmar con certeza la eliminación; "
                    "verifica el registro antes de repetirla"
                ),
            ) from exc
        raise