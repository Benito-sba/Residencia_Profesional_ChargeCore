from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from bson import ObjectId
from .user import PyObjectId

class SesionCargaBase(BaseModel):
    charge_point_id: str
    connector_id: int
    usuario_id: Optional[PyObjectId] = None
    vehiculo_id: Optional[PyObjectId] = None
    nfc_card_id: Optional[PyObjectId] = None
    transaction_id: int
    estado: str = Field(default="iniciada", pattern="^(iniciada|activa|finalizada|error)$")
    fecha_inicio: datetime = Field(default_factory=datetime.utcnow)
    fecha_fin: Optional[datetime] = None
    energia_consumida_kwh: float = 0.0
    potencia_maxima_kw: float = 0.0
    potencia_promedio_kw: float = 0.0
    duracion_minutos: int = 0
    es_carga_gratuita: bool = False
    beneficio_aplicado: Optional[str] = None
    motivo_fin: Optional[str] = None
    meter_start: Optional[float] = None
    meter_stop: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class SesionCargaCreate(SesionCargaBase):
    pass

class SesionCargaUpdate(BaseModel):
    estado: Optional[str] = None
    fecha_fin: Optional[datetime] = None
    energia_consumida_kwh: Optional[float] = None
    potencia_maxima_kw: Optional[float] = None
    potencia_promedio_kw: Optional[float] = None
    duracion_minutos: Optional[int] = None
    es_carga_gratuita: Optional[bool] = None
    beneficio_aplicado: Optional[str] = None
    motivo_fin: Optional[str] = None
    meter_stop: Optional[float] = None

class SesionCargaResponse(SesionCargaBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
        arbitrary_types_allowed = True