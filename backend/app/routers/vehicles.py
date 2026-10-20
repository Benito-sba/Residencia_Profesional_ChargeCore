from fastapi import APIRouter, HTTPException, status, Depends
from app.database import get_collection
from app.auth import require_admin, require_read_access
from app.models.vehicle import VehiculoCreate, VehiculoUpdate
from app.validation import parse_object_id

router = APIRouter(prefix="/api/vehiculos", tags=["Vehículos"])

@router.get("/")
async def list_vehicles(current_user=Depends(require_read_access)):
    cursor = get_collection("vehiculos").find({"activo": True})
    vehicles = []
    async for v in cursor:
        v["_id"] = str(v["_id"])
        if v.get("usuario_id"):
            v["usuario_id"] = str(v["usuario_id"])
        vehicles.append(v)
    return vehicles

@router.get("/usuario/{user_id}")
async def get_user_vehicles(user_id: str, current_user=Depends(require_read_access)):
    cursor = get_collection("vehiculos").find(
        {"usuario_id": parse_object_id(user_id, "usuario"), "activo": True}
    )
    vehicles = []
    async for v in cursor:
        v["_id"] = str(v["_id"])
        vehicles.append(v)
    return vehicles

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_vehicle(vehicle: VehiculoCreate, current_user=Depends(require_admin)):
    user = await get_collection("usuarios").find_one({"_id": vehicle.usuario_id})
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    result = await get_collection("vehiculos").insert_one(vehicle.model_dump())
    return {"id": str(result.inserted_id)}

@router.put("/{vehicle_id}")
async def update_vehicle(vehicle_id: str, vehicle: VehiculoUpdate, current_user=Depends(require_admin)):
    update_data = {k: v for k, v in vehicle.model_dump().items() if v is not None}
    result = await get_collection("vehiculos").update_one(
        {"_id": parse_object_id(vehicle_id, "vehiculo")},
        {"$set": update_data}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    return {"mensaje": "Vehículo actualizado"}

@router.delete("/{vehicle_id}")
async def delete_vehicle(vehicle_id: str, current_user=Depends(require_admin)):
    result = await get_collection("vehiculos").update_one(
        {"_id": parse_object_id(vehicle_id, "vehiculo")},
        {"$set": {"activo": False}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    return {"mensaje": "Vehículo desactivado"}