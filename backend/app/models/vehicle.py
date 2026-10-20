from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from bson import ObjectId
from .user import PyObjectId

class VehiculoBase(BaseModel):
    usuario_id: PyObjectId
    marca: str = Field(..., min_length=1, max_length=50)
    modelo: str = Field(..., min_length=1, max_length=50)
    anio: int = Field(..., ge=2000, le=2030)
    placa: Optional[str] = None
    tipo_conector: str = Field(default="Type2", pattern="^(Type1|Type2|CCS|CHAdeMO|Tesla)$")
    capacidad_bateria_kwh: Optional[float] = None
    activo: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

class VehiculoCreate(VehiculoBase):
    pass

class VehiculoUpdate(BaseModel):
    marca: Optional[str] = None
    modelo: Optional[str] = None
    anio: Optional[int] = None
    placa: Optional[str] = None
    tipo_conector: Optional[str] = None
    capacidad_bateria_kwh: Optional[float] = None
    activo: Optional[bool] = None

class VehiculoResponse(VehiculoBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
        arbitrary_types_allowed = True