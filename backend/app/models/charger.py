from datetime import datetime
from typing import Annotated, Optional, List
from pydantic import BaseModel, Field, StringConstraints
from bson import ObjectId
from .user import PyObjectId

class Conector(BaseModel):
    id_conector: int
    tipo: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
    ]
    estado: str = Field(default="Available", pattern="^(Available|Preparing|Charging|SuspendedEV|SuspendedEVSE|Finishing|Reserved|Unavailable|Faulted)$")
    potencia_max_kw: float

class CargadorBase(BaseModel):
    charge_point_id: str = Field(..., min_length=1, max_length=50)
    nombre: str = Field(..., min_length=1, max_length=100)
    ubicacion: str
    conectores: List[Conector] = []
    estado_general: str = Field(default="disponible", pattern="^(disponible|ocupado|mantenimiento|fuera_de_servicio)$")
    firmware_version: Optional[str] = None
    modelo: Optional[str] = None
    fabricante: Optional[str] = None
    activo: bool = True
    ultimo_heartbeat: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class CargadorCreate(CargadorBase):
    ocpp_password: str = Field(..., min_length=12, max_length=128)


class OCPPPasswordUpdate(BaseModel):
    password: str = Field(..., min_length=12, max_length=128)

class CargadorUpdate(BaseModel):
    nombre: Optional[str] = None
    ubicacion: Optional[str] = None
    conectores: Optional[List[Conector]] = None
    estado_general: Optional[str] = None
    firmware_version: Optional[str] = None
    modelo: Optional[str] = None
    fabricante: Optional[str] = None
    activo: Optional[bool] = None

class CargadorResponse(CargadorBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    sesion_actual: Optional[dict] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
        arbitrary_types_allowed = True