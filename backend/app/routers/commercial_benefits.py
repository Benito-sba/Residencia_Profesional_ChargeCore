from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.auth import require_company_owner, require_superadmin
from app.database import db, get_collection
from app.models.phase4_rewards import (
    CommercialBenefitAssignmentRequest,
    CommercialBenefitCreate,
    CommercialBenefitState,
    CommercialBenefitUpdate,
)
from app.validation import parse_object_id
from app.services.company_reward_points import _stable_id


router = APIRouter(prefix="/api/commercial-benefits", tags=["Beneficios ChargeCore"])


def _serialize(document):
    if isinstance(document, ObjectId):
        return str(document)
    if isinstance(document, dict):
        return {key: _serialize(value) for key, value in document.items()}
    if isinstance(document, list):
        return [_serialize(value) for value in document]
    return document


def _effective_assignment_state(assignment, now):
    state = assignment.get("estado", "disponible")
    if (
        state == "disponible"
        and assignment.get("expires_at")
        and assignment["expires_at"] < now
    ):
        return "vencido"
    return state


async def _company_eligibility_metrics(company_id, requirements, session):
    metrics = {
        "min_completed_sessions": 0,
        "min_active_clients": 0,
        "min_active_chargers": 0,
    }
    if requirements.get("min_completed_sessions", 0):
        metrics["min_completed_sessions"] = await get_collection(
            "sesiones"
        ).count_documents(
            {"empresa_id": company_id, "estado": "finalizada"},
            session=session,
        )
    if requirements.get("min_active_clients", 0):
        metrics["min_active_clients"] = await get_collection(
            "usuarios"
        ).count_documents(
            {
                "empresa_id": company_id,
                "rol": "cliente",
                "activo": {"$ne": False},
            },
            session=session,
        )
    if requirements.get("min_active_chargers", 0):
        metrics["min_active_chargers"] = await get_collection(
            "cargadores"
        ).count_documents(
            {"empresa_id": company_id, "activo": True},
            session=session,
        )
    return metrics


def _validate_company_requirements(requirements, metrics):
    unmet = [
        key
        for key, threshold in requirements.items()
        if metrics.get(key, 0) < threshold
    ]
    if unmet:
        raise HTTPException(
            status_code=409,
            detail=(
                "La empresa no cumple los requisitos del beneficio: "
                + ", ".join(unmet)
            ),
        )


async def _get_benefit(benefit_id):
    benefit = await get_collection("commercial_benefits").find_one(
        {"_id": benefit_id}
    )
    if benefit is None:
        raise HTTPException(status_code=404, detail="Beneficio no encontrado")
    return benefit


@router.get("/catalog")
async def list_benefits(current_user=Depends(require_superadmin)):
    cursor = get_collection("commercial_benefits").find().sort("created_at", -1)
    return [_serialize(item) async for item in cursor]


@router.get("/assignments")
async def list_all_benefit_assignments(current_user=Depends(require_superadmin)):
    assignments = get_collection("commercial_benefit_assignments")
    movements = get_collection("commercial_benefit_movements")
    cursor = assignments.find().sort("assigned_at", -1).limit(500)
    now = datetime.utcnow()
    results = []
    async for assignment in cursor:
        assignment["estado"] = _effective_assignment_state(assignment, now)
        assignment["movimientos"] = [
            _serialize(item)
            async for item in movements.find(
                {
                    "assignment_id": assignment["_id"],
                    "empresa_id": assignment["empresa_id"],
                }
            ).sort("created_at", -1)
        ]
        results.append(_serialize(assignment))
    return results


@router.post("/catalog", status_code=status.HTTP_201_CREATED)
async def create_benefit(
    payload: CommercialBenefitCreate,
    current_user=Depends(require_superadmin),
):
    benefit = payload.model_dump()
    benefit.update({
        "_id": ObjectId(),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "created_by": current_user["_id"],
    })
    await get_collection("commercial_benefits").insert_one(benefit)
    return _serialize(benefit)


@router.put("/catalog/{benefit_id}")
async def update_benefit(
    benefit_id: str,
    payload: CommercialBenefitUpdate,
    current_user=Depends(require_superadmin),
):
    object_id = parse_object_id(benefit_id, "beneficio")
    existing = await _get_benefit(object_id)
    update = payload.model_dump(exclude_unset=True, exclude_none=True)
    unset_fields = set()
    if "fecha_finalizacion" in payload.model_fields_set:
        if payload.fecha_finalizacion is None:
            unset_fields.add("fecha_finalizacion")
        else:
            update["fecha_finalizacion"] = payload.fecha_finalizacion
    if "max_assignments_per_company" in payload.model_fields_set:
        update["max_assignments_per_company"] = payload.max_assignments_per_company
    if not update and not unset_fields:
        raise HTTPException(status_code=422, detail="No hay cambios para guardar")
    proposed = {**existing, **update}
    if "fecha_finalizacion" in unset_fields:
        proposed["fecha_finalizacion"] = None
    fecha_finalizacion = proposed.get("fecha_finalizacion")
    if (
        fecha_finalizacion is not None
        and proposed["fecha_inicio"] > fecha_finalizacion
    ):
        raise HTTPException(status_code=422, detail="La vigencia del beneficio no es válida")
    if proposed["tipo"] == "reconocimiento":
        proposed["valor"] = None
        update["valor"] = None
    elif proposed.get("valor") is None:
        raise HTTPException(status_code=422, detail="Este beneficio requiere un valor")
    update["updated_at"] = datetime.utcnow()
    mongo_update = {"$set": update}
    if unset_fields:
        mongo_update["$unset"] = {field: "" for field in unset_fields}
    await get_collection("commercial_benefits").update_one(
        {"_id": object_id},
        mongo_update,
    )
    return _serialize(await _get_benefit(object_id))


@router.put("/catalog/{benefit_id}/state")
async def set_benefit_state(
    benefit_id: str,
    payload: CommercialBenefitState,
    current_user=Depends(require_superadmin),
):
    object_id = parse_object_id(benefit_id, "beneficio")
    await _get_benefit(object_id)
    await get_collection("commercial_benefits").update_one(
        {"_id": object_id},
        {"$set": {"activa": payload.activa, "updated_at": datetime.utcnow()}},
    )
    return _serialize(await _get_benefit(object_id))


@router.post("/catalog/{benefit_id}/assign")
async def assign_benefit(
    benefit_id: str,
    payload: CommercialBenefitAssignmentRequest,
    current_user=Depends(require_superadmin),
):
    object_id = parse_object_id(benefit_id, "beneficio")
    companies = get_collection("empresas")
    benefits = get_collection("commercial_benefits")
    assignments = get_collection("commercial_benefit_assignments")
    now = datetime.utcnow()
    company_ids = [parse_object_id(value, "empresa") for value in payload.empresa_ids]
    if len(set(company_ids)) != len(company_ids):
        raise HTTPException(status_code=422, detail="No repitas empresas en la asignación")

    async def apply(session):
        serializable_benefit = await benefits.update_one(
            {"_id": object_id, "activa": True},
            {"$inc": {"assignment_sequence": 1}},
            session=session,
        )
        if serializable_benefit.matched_count != 1:
            raise HTTPException(status_code=409, detail="El beneficio no está activo")
        current_benefit = await benefits.find_one(
            {"_id": object_id},
            session=session,
        )
        if current_benefit is None:
            raise HTTPException(status_code=404, detail="Beneficio no encontrado")
        fecha_finalizacion = current_benefit.get("fecha_finalizacion")
        if (
            current_benefit["fecha_inicio"] > now
            or (
                fecha_finalizacion is not None
                and now > fecha_finalizacion
            )
        ):
            raise HTTPException(status_code=409, detail="El beneficio está fuera de vigencia")
        assignment_limit = current_benefit.get("max_assignments_per_company")
        if assignment_limit != 1 and payload.assignment_request_id is None:
            raise HTTPException(
                status_code=422,
                detail="Se requiere assignment_request_id para asignaciones repetibles",
            )
        assignment_request_id = str(
            payload.assignment_request_id or "single-assignment"
        )
        created = []
        for company_id in company_ids:
            company = await companies.find_one(
                {"_id": company_id, "activo": True},
                session=session,
            )
            if company is None:
                raise HTTPException(status_code=404, detail="Empresa no encontrada o inactiva")
            assignment_id = _stable_id(
                f"commercial-benefit:{object_id}:{company_id}:{assignment_request_id}"
            )
            duplicate = await assignments.find_one(
                {"_id": assignment_id},
                session=session,
            )
            if duplicate:
                created.append(duplicate)
                continue
            count = await assignments.count_documents(
                {
                    "benefit_id": object_id,
                    "empresa_id": company_id,
                },
                session=session,
            )
            if assignment_limit is not None and count >= assignment_limit:
                raise HTTPException(status_code=409, detail="La empresa alcanzó el límite de asignaciones")
            requirements = current_benefit.get("requisitos") or {}
            metrics = await _company_eligibility_metrics(
                company_id,
                requirements,
                session,
            )
            _validate_company_requirements(requirements, metrics)
            assignment = {
                "_id": assignment_id,
                "benefit_id": object_id,
                "empresa_id": company_id,
                "assignment_request_id": assignment_request_id,
                "estado": "disponible",
                "assigned_at": now,
                "expires_at": fecha_finalizacion,
                "requisitos_snapshot": requirements,
                "evidencia_requisitos": metrics,
                "condiciones_uso_snapshot": current_benefit.get(
                    "condiciones_uso", {}
                ),
                "snapshot_benefit": {
                    key: current_benefit.get(key)
                    for key in (
                        "nombre",
                        "categoria",
                        "tipo",
                        "descripcion",
                        "condiciones",
                        "requisitos",
                        "condiciones_uso",
                        "valor",
                        "fecha_inicio",
                        "fecha_finalizacion",
                        "max_assignments_per_company",
                    )
                },
                "created_by": current_user["_id"],
            }
            await assignments.insert_one(assignment, session=session)
            await get_collection("commercial_benefit_movements").insert_one(
                {
                    "_id": _stable_id(f"commercial-benefit-assigned:{assignment_id}"),
                    "assignment_id": assignment_id,
                    "benefit_id": object_id,
                    "empresa_id": company_id,
                    "tipo": "asignacion",
                    "assignment_request_id": assignment_request_id,
                    "snapshot_benefit": assignment["snapshot_benefit"],
                    "requisitos_snapshot": requirements,
                    "evidencia_requisitos": metrics,
                    "actor_id": current_user["_id"],
                    "created_at": now,
                },
                session=session,
            )
            created.append(assignment)
        return created

    try:
        async with await db.client.start_session() as session:
            created = await session.with_transaction(apply)
    except HTTPException:
        raise
    except PyMongoError as error:
        if error.has_error_label("TransientTransactionError") or error.has_error_label(
            "UnknownTransactionCommitResult"
        ):
            raise HTTPException(status_code=409, detail="Conflicto al asignar beneficios; vuelve a consultar") from error
        if isinstance(error, DuplicateKeyError):
            raise HTTPException(status_code=409, detail="El beneficio ya fue asignado") from error
        raise
    return [_serialize(item) for item in created]


@router.get("/mine")
async def list_my_benefits(current_user=Depends(require_company_owner)):
    assignments = get_collection("commercial_benefit_assignments")
    cursor = assignments.find({"empresa_id": current_user["empresa_id"]}).sort(
        "assigned_at", -1
    )
    results = []
    now = datetime.utcnow()
    async for assignment in cursor:
        assignment["estado"] = _effective_assignment_state(assignment, now)
        assignment["movimientos"] = [
            _serialize(item)
            async for item in get_collection("commercial_benefit_movements").find(
                {
                    "assignment_id": assignment["_id"],
                    "empresa_id": current_user["empresa_id"],
                }
            ).sort("created_at", -1)
        ]
        results.append(_serialize(assignment))
    return results


@router.post("/mine/{assignment_id}/use")
async def use_my_benefit(assignment_id: str, current_user=Depends(require_company_owner)):
    object_id = parse_object_id(assignment_id, "asignación")
    assignments = get_collection("commercial_benefit_assignments")
    movements = get_collection("commercial_benefit_movements")
    now = datetime.utcnow()
    available_query = {
        "_id": object_id,
        "empresa_id": current_user["empresa_id"],
        "estado": "disponible",
        "$or": [
            {"expires_at": {"$gte": now}},
            {"expires_at": None},
        ],
    }

    async def apply(session):
        assignment = await assignments.find_one(
            available_query,
            session=session,
        )
        if assignment is None:
            raise HTTPException(status_code=409, detail="El beneficio no está disponible")
        usage_conditions = assignment.get("condiciones_uso_snapshot") or {}
        elapsed_days = (now - assignment.get("assigned_at", now)).days
        min_days = usage_conditions.get("min_days_after_assignment", 0)
        max_days = usage_conditions.get("max_days_after_assignment")
        if elapsed_days < min_days:
            raise HTTPException(
                status_code=409,
                detail="Aún no se cumple la condición temporal de uso",
            )
        if max_days is not None and elapsed_days > max_days:
            raise HTTPException(
                status_code=409,
                detail="La ventana de uso del beneficio venció",
            )
        claim = await assignments.update_one(
            available_query,
            {"$set": {
                "estado": "utilizado",
                "used_at": now,
                "used_by": current_user["_id"],
                "aplicacion": {
                    "tipo": assignment["snapshot_benefit"].get("tipo"),
                    "valor": assignment["snapshot_benefit"].get("valor"),
                    "snapshot_benefit": assignment["snapshot_benefit"],
                    "condiciones_uso_snapshot": usage_conditions,
                    "aplicado_at": now,
                    "aplicado_por": current_user["_id"],
                },
            }},
            session=session,
        )
        if claim.modified_count != 1:
            raise HTTPException(status_code=409, detail="El beneficio ya fue utilizado")
        await movements.insert_one(
            {
                "_id": _stable_id(f"commercial-benefit-used:{object_id}"),
                "assignment_id": object_id,
                "benefit_id": assignment["benefit_id"],
                "empresa_id": current_user["empresa_id"],
                "tipo": "utilizacion",
                "tipo_beneficio": assignment["snapshot_benefit"].get("tipo"),
                "valor_aplicado": assignment["snapshot_benefit"].get("valor"),
                "snapshot_benefit": assignment["snapshot_benefit"],
                "condiciones_uso_snapshot": usage_conditions,
                "actor_id": current_user["_id"],
                "created_at": now,
            },
            session=session,
        )
        assignment["estado"] = "utilizado"
        assignment["used_at"] = now
        assignment["used_by"] = current_user["_id"]
        assignment["aplicacion"] = {
            "tipo": assignment["snapshot_benefit"].get("tipo"),
            "valor": assignment["snapshot_benefit"].get("valor"),
            "snapshot_benefit": assignment["snapshot_benefit"],
            "condiciones_uso_snapshot": usage_conditions,
            "aplicado_at": now,
            "aplicado_por": current_user["_id"],
        }
        return assignment

    try:
        async with await db.client.start_session() as session:
            result = await session.with_transaction(apply)
    except HTTPException:
        raise
    except PyMongoError as error:
        if error.has_error_label("TransientTransactionError") or error.has_error_label(
            "UnknownTransactionCommitResult"
        ):
            raise HTTPException(status_code=409, detail="Conflicto al utilizar el beneficio; consulta su estado") from error
        raise
    return {
        "mensaje": "Uso del beneficio registrado",
        "beneficio": _serialize(result),
        "valor_aplicado": result["snapshot_benefit"].get("valor"),
    }
