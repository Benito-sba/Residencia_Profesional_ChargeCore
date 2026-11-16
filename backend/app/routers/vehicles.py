from fastapi import APIRouter, HTTPException, status, Depends
from app.database import get_collection
from app.auth import company_scoped_query, require_company_member
from app.models.vehicle import VehiculoCreate, VehiculoUpdate
from app.validation import parse_object_id

router = APIRouter(prefix="/api/vehiculos", tags=["Vehículos"])


async def _company_client(user_id, current_user):
    query = {
        "_id": user_id,
        "rol": "cliente",
        "activo": {"$ne": False},
    }
    if current_user.get("rol") != "superadmin":
        query["empresa_id"] = current_user["empresa_id"]
    user = await get_collection("usuarios").find_one(query)
    if user is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    company_id = user.get("empresa_id")
    if company_id is None:
        raise HTTPException(
            status_code=409,
            detail="No se pueden registrar vehículos para un cliente sin empresa",
        )
    company = await get_collection("empresas").find_one({
        "_id": company_id,
        "activo": True,
    })
    if company is None:
        raise HTTPException(status_code=409, detail="La empresa del cliente no está disponible")
    return user


@router.get("/")
async def list_vehicles(current_user=Depends(require_company_member)):
    query = company_scoped_query(current_user, {"activo": True})
    if current_user.get("rol") == "cliente":
        query["usuario_id"] = current_user["_id"]
    cursor = get_collection("vehiculos").find(query)
    vehicles = []
    async for v in cursor:
        v["_id"] = str(v["_id"])
        if v.get("usuario_id"):
            v["usuario_id"] = str(v["usuario_id"])
        if v.get("empresa_id"):
            v["empresa_id"] = str(v["empresa_id"])
        vehicles.append(v)
    return vehicles

@router.get("/usuario/{user_id}")
async def get_user_vehicles(user_id: str, current_user=Depends(require_company_member)):
    target_user_id = parse_object_id(user_id, "usuario")
    if (
        current_user.get("rol") == "cliente"
        and target_user_id != current_user["_id"]
    ):
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    await _company_client(target_user_id, current_user)
    cursor = get_collection("vehiculos").find(
        company_scoped_query(current_user, {
            "usuario_id": target_user_id,
            "activo": True,
        })
    )
    vehicles = []
    async for v in cursor:
        v["_id"] = str(v["_id"])
        if v.get("usuario_id"):
            v["usuario_id"] = str(v["usuario_id"])
        if v.get("empresa_id"):
            v["empresa_id"] = str(v["empresa_id"])
        vehicles.append(v)
    return vehicles

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_vehicle(
    vehicle: VehiculoCreate,
    current_user=Depends(require_company_member),
):
    if (
        current_user.get("rol") == "cliente"
        and vehicle.usuario_id != current_user["_id"]
    ):
        raise HTTPException(status_code=403, detail="No puedes registrar vehículos para otro cliente")
    user = await _company_client(vehicle.usuario_id, current_user)
    vehicle_data = vehicle.model_dump()
    vehicle_data["empresa_id"] = user["empresa_id"]
    result = await get_collection("vehiculos").insert_one(vehicle_data)
    return {"id": str(result.inserted_id)}

@router.put("/{vehicle_id}")
async def update_vehicle(
    vehicle_id: str,
    vehicle: VehiculoUpdate,
    current_user=Depends(require_company_member),
):
    update_data = {k: v for k, v in vehicle.model_dump().items() if v is not None}
    query = company_scoped_query(
        current_user,
        {"_id": parse_object_id(vehicle_id, "vehiculo")},
    )
    if current_user.get("rol") == "cliente":
        query["usuario_id"] = current_user["_id"]
    result = await get_collection("vehiculos").update_one(
        query,
        {"$set": update_data}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    return {"mensaje": "Vehículo actualizado"}

@router.delete("/{vehicle_id}")
async def delete_vehicle(vehicle_id: str, current_user=Depends(require_company_member)):
    query = company_scoped_query(
        current_user,
        {"_id": parse_object_id(vehicle_id, "vehiculo")},
    )
    if current_user.get("rol") == "cliente":
        query["usuario_id"] = current_user["_id"]
    result = await get_collection("vehiculos").update_one(
        query,
        {"$set": {"activo": False}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    return {"mensaje": "Vehículo desactivado"}