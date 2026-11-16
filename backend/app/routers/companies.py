from datetime import datetime, timezone
from typing import Literal

from pymongo.errors import DuplicateKeyError, PyMongoError
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.auth import (
    require_company_admin_access,
    require_company_owner,
    require_superadmin,
)
from app.database import get_collection, get_database, serialize_company_change
from app.models.company import normalize_company_name
from app.validation import parse_object_id


router = APIRouter(prefix="/api/empresas", tags=["Empresas"])


class EmpresaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(..., min_length=1, max_length=150)
    contacto_nombre: str | None = Field(default=None, max_length=150)
    contacto_email: EmailStr | None = Field(default=None, max_length=254)
    contacto_telefono: str | None = Field(default=None, max_length=40)

    @field_validator("nombre", mode="before")
    @classmethod
    def normalize_display_name(cls, value):
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("contacto_nombre", "contacto_email", "contacto_telefono", mode="before")
    @classmethod
    def normalize_optional_contact(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class EmpresaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str | None = Field(default=None, min_length=1, max_length=150)
    contacto_nombre: str | None = Field(default=None, max_length=150)
    contacto_email: EmailStr | None = Field(default=None, max_length=254)
    contacto_telefono: str | None = Field(default=None, max_length=40)
    activo: bool | None = None
    estado: Literal["activa", "suspendida", "inactiva"] | None = None

    @field_validator("nombre", mode="before")
    @classmethod
    def normalize_display_name(cls, value):
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("contacto_nombre", "contacto_email", "contacto_telefono", mode="before")
    @classmethod
    def normalize_optional_contact(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_status_fields(self):
        if self.estado is not None and self.activo is not None:
            if self.activo != (self.estado == "activa"):
                raise ValueError("activo y estado deben representar el mismo estado")
        return self


def _serialize_company(company: dict) -> dict:
    active = company.get("activo", True)
    return {
        "_id": str(company["_id"]),
        "nombre": company["nombre"],
        "contacto_nombre": company.get("contacto_nombre"),
        "contacto_email": company.get("contacto_email"),
        "contacto_telefono": company.get("contacto_telefono"),
        "activo": active,
        "estado": company.get(
            "estado",
            "activa" if active else "inactiva",
        ),
        "created_at": company.get("created_at"),
        "updated_at": company.get("updated_at"),
    }


@router.get("/")
async def list_companies(current_user=Depends(require_superadmin)):
    cursor = get_collection("empresas").find().sort("nombre", 1)
    return [_serialize_company(company) async for company in cursor]


@router.get("/{company_id}/overview")
async def get_company_overview(
    company_id: str,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=20),
    current_user=Depends(require_superadmin),
):
    object_id = parse_object_id(company_id, "empresa")
    company = await get_collection("empresas").find_one({"_id": object_id})
    if company is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    chargers = get_collection("cargadores")
    users = get_collection("usuarios")
    sessions = get_collection("sesiones")
    user_query = {"empresa_id": object_id}
    session_query = {"empresa_id": object_id}
    charger_query = {"empresa_id": object_id}
    recent_sessions = [
        session
        async for session in sessions.find(session_query)
        .sort("fecha_inicio", -1)
        .skip(skip)
        .limit(limit)
    ]
    activity = []
    for session in recent_sessions:
        user = None
        if session.get("usuario_id") is not None:
            user = await users.find_one({
                "_id": session["usuario_id"],
                "empresa_id": object_id,
            })
        activity.append({
            "transaction_id": session.get("transaction_id"),
            "charge_point_id": session.get("charge_point_id"),
            "cliente": (
                user.get("nombre") or user.get("email")
                if user
                else "Cliente no disponible"
            ),
            "estado": session.get("estado"),
            "fecha_inicio": session.get("fecha_inicio"),
            "fecha_fin": session.get("fecha_fin"),
            "energia_consumida_kwh": session.get("energia_consumida_kwh", 0),
        })

    return {
        "empresa": _serialize_company(company),
        "metricas": {
            "cargadores": await chargers.count_documents(charger_query),
            "usuarios": await users.count_documents(user_query),
            "clientes": await users.count_documents({
                **user_query,
                "rol": "cliente",
            }),
            "duenos": await users.count_documents({
                **user_query,
                "rol": "dueno_empresa",
            }),
            "sesiones": await sessions.count_documents(session_query),
            "sesiones_activas": await sessions.count_documents({
                **session_query,
                "estado": {"$in": ["activa", "iniciada"]},
            }),
            "sesiones_finalizadas": await sessions.count_documents({
                **session_query,
                "estado": "finalizada",
            }),
        },
        "actividad": activity,
    }


@router.get("/me")
async def get_my_company(current_user=Depends(require_company_owner)):
    company = await get_collection("empresas").find_one({
        "_id": current_user["empresa_id"],
        "activo": True,
    })
    if company is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    return _serialize_company(company)


@router.get("/qr-operations")
async def list_qr_operations(
    limit: int = Query(default=100, ge=1, le=500),
    current_user=Depends(require_company_admin_access),
):
    query = {"source": "qr"}
    if current_user.get("rol") != "superadmin":
        query["empresa_id"] = current_user["empresa_id"]

    requests = get_collection("nfc_charge_requests")
    cursor = requests.find(query).sort("created_at", -1).limit(limit)
    operations = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async for item in cursor:
        company_id = item.get("empresa_id")
        charge_point_id = item.get("charge_point_id")
        charger_query = {"charge_point_id": item.get("charge_point_id")}
        user_query = {"_id": item.get("usuario_id")}
        if company_id is not None:
            charger_query["empresa_id"] = company_id
            user_query["empresa_id"] = company_id
        charger = await get_collection("cargadores").find_one(
            charger_query,
            {"nombre": 1},
        )
        user = await get_collection("usuarios").find_one(
            user_query,
            {"nombre": 1, "email": 1},
        )
        company = (
            await get_collection("empresas").find_one({"_id": company_id})
            if company_id is not None
            else None
        )
        vehicle = None
        if item.get("vehicle_id") is not None and company_id is not None:
            vehicle = await get_collection("vehiculos").find_one(
                {
                    "_id": item["vehicle_id"],
                    "empresa_id": company_id,
                    "usuario_id": item.get("usuario_id"),
                },
                {"marca": 1, "modelo": 1, "placa": 1},
            )
        session = None
        if company_id is not None and charge_point_id:
            session_query = {
                "empresa_id": company_id,
                "charge_point_id": charge_point_id,
                "qr_request_id": item["_id"],
            }
            if item.get("transaction_id") is not None:
                session_query["transaction_id"] = item["transaction_id"]
            if item.get("usuario_id") is not None:
                session_query["usuario_id"] = item["usuario_id"]
            session = await get_collection("sesiones").find_one(session_query)

        raw_status = item.get("status", "desconocido")
        if session and session.get("estado") == "finalizada":
            operation_status = "finished"
        elif session and session.get("estado") in {"activa", "iniciada"}:
            operation_status = "started"
        elif item.get("last_result") in {"rejected", "error"}:
            operation_status = item["last_result"]
        elif raw_status == "awaiting_vehicle":
            operation_status = (
                "expired"
                if item.get("expires_at") and item["expires_at"] <= now
                else "pending"
            )
        elif raw_status == "start_requested":
            operation_status = "processing"
        elif raw_status == "transaction_started":
            operation_status = "started"
        else:
            operation_status = raw_status

        vehicle_name = "Vehículo no seleccionado"
        if vehicle:
            vehicle_name = " ".join(
                str(vehicle.get(field) or "").strip()
                for field in ("marca", "modelo")
                if vehicle.get(field)
            ) or "Vehículo"
            if vehicle.get("placa"):
                vehicle_name = f"{vehicle_name} · {vehicle['placa']}"
        transaction_id = (
            session.get("transaction_id")
            if session
            else item.get("transaction_id")
        )
        if session and session.get("estado") == "finalizada":
            follow_up = "Sesión finalizada"
        elif session:
            follow_up = "Sesión iniciada"
        elif operation_status == "processing":
            follow_up = "Esperando confirmación del cargador"
        elif operation_status == "rejected":
            follow_up = item.get("last_error") or "El cargador rechazó la solicitud"
        elif operation_status == "error":
            follow_up = item.get("last_error") or "Ocurrió un error al iniciar"
        elif operation_status == "expired":
            follow_up = "La solicitud expiró sin iniciar una sesión"
        elif operation_status == "pending":
            follow_up = "Esperando selección de vehículo"
        else:
            follow_up = "Sin sesión vinculada"
        if transaction_id is not None and session:
            follow_up = f"{follow_up} · TX {transaction_id}"

        operations.append({
            "id": str(item["_id"]),
            "empresa_id": str(company_id) if company_id is not None else None,
            "empresa": company.get("nombre") if company else "Empresa no disponible",
            "charge_point_id": charge_point_id,
            "cargador": (
                charger.get("nombre")
                if charger
                else charge_point_id
            ),
            "cliente": (
                user.get("nombre") or user.get("email")
                if user
                else "Cliente no disponible"
            ),
            "vehiculo": vehicle_name,
            "status": raw_status,
            "estado_operacion": operation_status,
            "seguimiento": follow_up,
            "transaction_id": transaction_id,
            "created_at": item.get("created_at"),
            "start_requested_at": item.get("start_requested_at"),
            "started_at": (
                session.get("fecha_inicio")
                if session
                else item.get("started_at")
            ),
            "finished_at": (
                session.get("fecha_fin")
                if session
                else item.get("finished_at")
            ),
            "last_error": item.get("last_error"),
            "expires_at": item.get("expires_at"),
        })
    return operations


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_company(
    request: EmpresaCreate,
    current_user=Depends(require_superadmin),
):
    name = request.nombre
    normalized_name = normalize_company_name(name)
    companies = get_collection("empresas")
    if await companies.find_one({"nombre_normalizado": normalized_name}):
        raise HTTPException(
            status_code=409,
            detail="Ya existe una empresa con ese nombre",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    document = {
        "nombre": name,
        "nombre_normalizado": normalized_name,
        "contacto_nombre": request.contacto_nombre,
        "contacto_email": str(request.contacto_email) if request.contacto_email else None,
        "contacto_telefono": request.contacto_telefono,
        "activo": True,
        "estado": "activa",
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = await companies.insert_one(document)
    except DuplicateKeyError as error:
        raise HTTPException(
            status_code=409,
            detail="Ya existe una empresa con ese nombre",
        ) from error
    created = await companies.find_one({"_id": result.inserted_id})
    if created is None:
        raise HTTPException(
            status_code=500,
            detail="No se pudo recuperar la empresa creada",
        )
    return _serialize_company(created)


@router.put("/{company_id}")
async def update_company(
    company_id: str,
    request: EmpresaUpdate,
    current_user=Depends(require_superadmin),
):
    object_id = parse_object_id(company_id, "empresa")
    companies = get_collection("empresas")
    update_data = request.model_dump(exclude_unset=True)
    update_data = {
        key: value
        for key, value in update_data.items()
        if value is not None or key.startswith("contacto_")
    }
    if not update_data:
        raise HTTPException(status_code=422, detail="No hay cambios para guardar")
    if "estado" in update_data:
        update_data["activo"] = update_data["estado"] == "activa"
    elif "activo" in update_data:
        update_data["estado"] = (
            "activa" if update_data["activo"] else "inactiva"
        )
    if update_data.get("contacto_email") is not None:
        update_data["contacto_email"] = str(update_data["contacto_email"])

    update_data["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        async with await get_database().client.start_session() as session:
            async with session.start_transaction():
                company_lock = await serialize_company_change(
                    object_id,
                    session,
                    companies,
                )
                if company_lock.matched_count == 0:
                    raise HTTPException(
                        status_code=404,
                        detail="Empresa no encontrada",
                    )

                existing = await companies.find_one(
                    {"_id": object_id},
                    session=session,
                )
                if existing is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Empresa no encontrada",
                    )

                if "nombre" in update_data:
                    normalized_name = normalize_company_name(update_data["nombre"])
                    duplicate = await companies.find_one(
                        {
                            "nombre_normalizado": normalized_name,
                            "_id": {"$ne": object_id},
                        },
                        session=session,
                    )
                    if duplicate:
                        raise HTTPException(
                            status_code=409,
                            detail="Ya existe otra empresa con ese nombre",
                        )
                    update_data["nombre_normalizado"] = normalized_name

                if (
                    update_data.get("estado") == "inactiva"
                    and existing.get("activo", True) is not False
                ):
                    active_sessions = await get_collection(
                        "sesiones"
                    ).count_documents(
                        {
                            "empresa_id": object_id,
                            "estado": {"$in": ["activa", "iniciada"]},
                        },
                        session=session,
                    )
                    connected_charger = await get_collection(
                        "cargadores"
                    ).find_one(
                        {
                            "empresa_id": object_id,
                            "activo": True,
                            "conectado": True,
                        },
                        session=session,
                    )
                    if active_sessions or connected_charger:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "No se puede desactivar la empresa con sesiones "
                                "activas o cargadores conectados"
                            ),
                        )

                result = await companies.update_one(
                    {"_id": object_id},
                    {"$set": update_data},
                    session=session,
                )
                if result.matched_count == 0:
                    raise HTTPException(
                        status_code=404,
                        detail="Empresa no encontrada",
                    )

                updated = await companies.find_one(
                    {"_id": object_id},
                    session=session,
                )
                if updated is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Empresa no encontrada",
                    )
                response = _serialize_company(updated)
    except DuplicateKeyError as error:
        raise HTTPException(
            status_code=409,
            detail="Ya existe otra empresa con ese nombre",
        ) from error
    except PyMongoError as error:
        if error.has_error_label("UnknownTransactionCommitResult"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "No se pudo confirmar con certeza el cambio de empresa; "
                    "verifica el estado antes de repetir la operación"
                ),
            ) from error
        if error.has_error_label("TransientTransactionError"):
            raise HTTPException(
                status_code=409,
                detail="Hay otro cambio de empresa en curso; vuelve a intentarlo",
            ) from error
        raise
    return response
